"""重い同期処理を event loop の上で走らせていないことの試験。

診断で、loop のスレッドが manifest の読み込み・DB・subprocess・SSL コンテキストの生成で
数秒止まっていた。重い関数をわざと遅くし、その間に loop の別のタスクが進むことを確かめる。
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from tests.test_addon_contract import addon_manifest


async def _ticks_while(coro, *, seconds: float) -> tuple[object, int]:
    """coro が走っている間に、loop が何回 tick を刻めたかを返す。"""
    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    try:
        result = await coro
    finally:
        stop.set()
        await task
    return result, ticks


def _slow(seconds: float, value=None):
    main = threading.get_ident()

    def run(*_a, **_k):
        assert threading.get_ident() != main, "event loop のスレッドで呼ばれた"
        time.sleep(seconds)
        return value

    return run


def test_manifest_load_is_cached_until_file_changes(monkeypatch, tmp_path):
    from app.addons import registry
    from app.addons.schema import parse_manifest

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    registry.reset_runtime_state_for_tests()
    registry.install(parse_manifest(addon_manifest()))
    path = registry._addon_dir("fake-addon") / registry.MANIFEST_NAME
    calls = []
    original = registry.parse_manifest
    monkeypatch.setattr(registry, "parse_manifest", lambda v: calls.append(1) or original(v))
    registry.reset_runtime_state_for_tests()
    first = registry._load_path(path)
    assert registry._load_path(path) is first
    assert len(calls) == 1
    # 書き換えたら読み直す
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "\n", encoding="utf-8")
    registry._load_path(path)
    assert len(calls) == 2


def test_addon_health_poll_does_not_block_loop(monkeypatch):
    from app.addons import health, registry

    monkeypatch.setattr(registry, "list_addons", _slow(0.3, []))
    _, ticks = asyncio.run(_ticks_while(health.poll_once(), seconds=0.3))
    assert ticks >= 10


def test_pause_recovery_does_not_block_loop(monkeypatch):
    from app.workflows import engine

    monkeypatch.setattr(engine, "_expire_and_collect_resumable", _slow(0.3, []))
    launched, ticks = asyncio.run(_ticks_while(engine.recover_paused_workflows_once(), seconds=0.3))
    assert launched == 0
    assert ticks >= 10


def test_runtime_environment_probes_run_off_loop(monkeypatch):
    from app.models_mgmt import runtime_policy

    main = threading.get_ident()
    seen = []

    def probe():
        seen.append(threading.get_ident())
        time.sleep(0.3)
        return {"rocm": False}, [], {"installed": False, "instances": []}, False

    monkeypatch.setattr(runtime_policy, "_probe_local_runtimes", probe)

    async def go():
        from app.models_mgmt import ollama
        monkeypatch.setattr(ollama, "status", _async_value({"available": False}))
        return await runtime_policy.environment()

    _, ticks = asyncio.run(_ticks_while(go(), seconds=0.3))
    assert seen and seen[0] != main
    assert ticks >= 10


def _async_value(value):
    async def run(*_a, **_k):
        return value

    return run


def test_shared_ssl_context_is_reused():
    from app.http_tls import shared_ssl_context

    assert shared_ssl_context() is shared_ssl_context()
