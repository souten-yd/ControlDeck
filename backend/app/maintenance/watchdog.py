"""systemd ウォッチドッグ連携と内部ヘルスチェック。

Type=notify + WatchdogSec で運用し、内部が健全な間のみ WATCHDOG=1 を送る。
ハング（イベントループ停止）や内部異常（DB 不通・収集停止）時は ping が止まり、
systemd がサービスを自動再起動する。
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("control_deck.watchdog")

# 各バックグラウンドループが更新する心拍（monotonic 秒）
_heartbeats: dict[str, float] = {}


def beat(name: str) -> None:
    _heartbeats[name] = time.monotonic()


def heartbeat_age(name: str) -> float | None:
    ts = _heartbeats.get(name)
    return None if ts is None else time.monotonic() - ts


def sd_notify(message: str) -> bool:
    """systemd へ通知を送る。NOTIFY_SOCKET がなければ何もしない。"""
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return False
    addr = "\0" + sock_path[1:] if sock_path.startswith("@") else sock_path
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.send(message.encode())
        return True
    except OSError as e:
        logger.debug("sd_notify failed: %s", e)
        return False


def notify_ready() -> None:
    if sd_notify("READY=1"):
        logger.info("systemd へ READY=1 を通知しました")


def _check_db() -> tuple[bool, str]:
    try:
        from sqlalchemy import text

        from app.database import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as e:
        return False, f"DB 接続失敗: {type(e).__name__}"


def _check_collector() -> tuple[bool, str]:
    from app.config import get_config

    age = heartbeat_age("collector")
    if age is None:
        return True, "起動待ち"  # 起動直後は許容
    limit = max(30.0, get_config().monitoring.interval_seconds * 10)
    if age > limit:
        return False, f"メトリクス収集が {age:.0f} 秒停止"
    return True, "ok"


def _check_scheduler() -> tuple[bool, str]:
    age = heartbeat_age("scheduler")
    if age is None:
        return True, "起動待ち"
    if age > 300:
        return False, f"スケジューラーが {age:.0f} 秒停止"
    return True, "ok"


def _check_alerts() -> tuple[bool, str]:
    age = heartbeat_age("alerts")
    if age is None:
        return True, "起動待ち"
    if age > 120:
        return False, f"アラート評価が {age:.0f} 秒停止"
    return True, "ok"


def health_checks() -> dict[str, dict]:
    """内部ヘルスチェック一式。すべて ok なら健全。"""
    results = {}
    for name, fn in (
        ("database", _check_db),
        ("metrics_collector", _check_collector),
        ("workflow_scheduler", _check_scheduler),
        ("alert_engine", _check_alerts),
    ):
        ok, detail = fn()
        results[name] = {"ok": ok, "detail": detail}
    return results


def is_healthy() -> bool:
    return all(c["ok"] for c in health_checks().values())


def watchdog_enabled() -> bool:
    return bool(os.environ.get("WATCHDOG_USEC"))


# event loop が動いていることを示す印。停止の検知だけに使う。
_loop_tick = time.monotonic()

# これだけ止まったら「同期処理が loop を塞いでいる」と見なして stack を取る。
# systemd の WatchdogSec より短くしないと、落とされた後になって記録が残らない。
_STALL_SECONDS = 12.0


async def _loop_ticker() -> None:
    """event loop が回っている限り、印を更新し続ける。"""
    global _loop_tick
    while True:
        _loop_tick = time.monotonic()
        await asyncio.sleep(1.0)


def _stall_watcher() -> None:
    """loop が止まったら、全 thread の stack を残す。

    watchdog に落とされる事象を追うのに、外から見た「無応答」以外の手掛かりが
    無かった。落ちた後では何が塞いでいたか分からないので、落ちる前に記録する。
    loop の外（daemon thread）から見張るのが要点で、loop の中に置くと、塞がれた
    ときに見張り自身も動けない。
    """
    reported = False
    while True:
        time.sleep(1.0)
        age = time.monotonic() - _loop_tick
        if age < _STALL_SECONDS:
            reported = False
            continue
        if reported:
            continue
        reported = True
        logger.error("event loop が %.1f 秒応答していない。全 thread の stack を記録する", age)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)


def start_stall_watcher() -> None:
    """停止検知を始める。loop の中と外に 1 つずつ置く。"""
    threading.Thread(target=_stall_watcher, name="loop-stall-watch", daemon=True).start()


async def watchdog_loop() -> None:
    """WatchdogSec の半分の間隔で、健全なときのみ ping を送る。

    健全性の確認は専用の thread で行う。既定の executor は `asyncio.to_thread` を
    使うあらゆる処理と共有していて、モデルの起動・停止や重みのハッシュ計算のように
    長く塞ぐものが混ざる。埋まると確認が順番待ちになり、その間 ping が出せない。
    プロセスは生きているのに systemd から見ればハングで、SIGABRT で落とされる
    ——実測で 3 日に 68 回起きていた。落ちれば当然、繋いでいる画面は切れ、走って
    いた生成も道連れになる。監視のための仕組みが、監視対象を壊していた。

    確認が時間内に終わらないときは、落とさずに ping する。この loop が動けている
    こと自体が「event loop は生きている」証拠であり、systemd の watchdog が拾う
    べきなのはそこである。DB が一時的に遅いことは、プロセスを落とす理由にならない
    （不調は alert として出す）。ping を止めるのは、確認が「駄目だ」と答えたときだけ。
    """
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        logger.info("systemd ウォッチドッグは無効です（WATCHDOG_USEC なし）")
        return
    interval = max(2.0, int(usec) / 1_000_000 / 2)
    logger.info("systemd ウォッチドッグ有効（ping 間隔 %.0f 秒）", interval)
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="watchdog") as pool:
        while True:
            try:
                healthy = await asyncio.wait_for(
                    loop.run_in_executor(pool, is_healthy), timeout=interval
                )
            except (TimeoutError, asyncio.TimeoutError):
                # 確認が返らないだけで、この loop は動けている。落とさない。
                logger.warning("内部ヘルスチェックが %.0f 秒で終わらなかった", interval)
                sd_notify("WATCHDOG=1")
            except Exception:
                logger.exception("watchdog loop error")
                sd_notify("WATCHDOG=1")
            else:
                if healthy:
                    sd_notify("WATCHDOG=1")
                else:
                    # ping を止め、systemd による再起動へ委ねる
                    logger.error("内部ヘルスチェック失敗: %s", health_checks())
            await asyncio.sleep(interval)
