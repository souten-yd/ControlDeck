"""systemd watchdog は「プロセスが生きているか」を見る。

健全性の確認を既定の executor で回していたため、`asyncio.to_thread` を使う他の
処理（モデルの起動・停止、重みのハッシュ計算）で埋まると確認が順番待ちになり、
その間 ping が出せなかった。プロセスは生きているのに systemd から見ればハングで、
SIGABRT で落とされる。実測で 3 日に 68 回。落ちれば繋いでいる画面は切れ、走って
いた生成も道連れになる。
"""

from __future__ import annotations

import asyncio
import inspect

from app.maintenance import watchdog


def test_the_health_check_does_not_share_the_default_executor():
    """共有プールが埋まっても確認は動く。"""
    source = inspect.getsource(watchdog.watchdog_loop)
    assert "ThreadPoolExecutor(max_workers=1" in source
    assert "asyncio.to_thread(is_healthy)" not in source


def test_a_slow_check_does_not_kill_the_process(monkeypatch):
    """確認が返らないだけで落とさない。この loop が動けている＝生きている。"""
    sent: list[str] = []
    monkeypatch.setenv("WATCHDOG_USEC", str(4 * 1_000_000))
    monkeypatch.setattr(watchdog, "sd_notify", lambda message: sent.append(message) or True)

    def never_returns() -> bool:
        import time

        time.sleep(30)
        return True

    monkeypatch.setattr(watchdog, "is_healthy", never_returns)

    async def scenario():
        task = asyncio.create_task(watchdog.watchdog_loop())
        await asyncio.sleep(3.0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert "WATCHDOG=1" in sent


def test_an_unhealthy_answer_still_stops_the_ping(monkeypatch):
    """本当に駄目なときは従来どおり黙り、systemd の再起動へ委ねる。"""
    sent: list[str] = []
    monkeypatch.setenv("WATCHDOG_USEC", str(4 * 1_000_000))
    monkeypatch.setattr(watchdog, "sd_notify", lambda message: sent.append(message) or True)
    monkeypatch.setattr(watchdog, "is_healthy", lambda: False)
    monkeypatch.setattr(watchdog, "health_checks", lambda: {"database": {"ok": False, "detail": "x"}})

    async def scenario():
        task = asyncio.create_task(watchdog.watchdog_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert sent == []
