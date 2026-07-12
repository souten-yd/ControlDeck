"""systemd ウォッチドッグ連携と内部ヘルスチェック。

Type=notify + WatchdogSec で運用し、内部が健全な間のみ WATCHDOG=1 を送る。
ハング（イベントループ停止）や内部異常（DB 不通・収集停止）時は ping が止まり、
systemd がサービスを自動再起動する。
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time

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


async def watchdog_loop() -> None:
    """WatchdogSec の半分の間隔で、健全なときのみ ping を送る。"""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        logger.info("systemd ウォッチドッグは無効です（WATCHDOG_USEC なし）")
        return
    interval = max(2.0, int(usec) / 1_000_000 / 2)
    logger.info("systemd ウォッチドッグ有効（ping 間隔 %.0f 秒）", interval)
    while True:
        try:
            healthy = await asyncio.to_thread(is_healthy)
            if healthy:
                sd_notify("WATCHDOG=1")
            else:
                # ping を止め、systemd による再起動へ委ねる
                logger.error("内部ヘルスチェック失敗: %s", health_checks())
        except Exception:
            logger.exception("watchdog loop error")
        await asyncio.sleep(interval)
