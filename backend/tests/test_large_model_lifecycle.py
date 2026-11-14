"""Idle unload, on-demand start and auto-start for Maya-S and Strata."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.models_mgmt import maya_s, large_model_lifecycle, strata_gateway


@pytest.fixture
def usage(tmp_path, monkeypatch):
    monkeypatch.setattr(large_model_lifecycle, "_usage_path", lambda: tmp_path / "usage.json")
    return tmp_path


def _policy(monkeypatch, *, enabled=True, minutes=30):
    from app.models_mgmt import runtime_policy
    monkeypatch.setattr(runtime_policy, "get_policy",
                        lambda: SimpleNamespace(idle_unload_enabled=enabled, idle_unload_minutes=minutes))


def _loaded(monkeypatch, rows):
    monkeypatch.setattr(large_model_lifecycle, "_loaded_models", lambda: rows)
    monkeypatch.setattr(large_model_lifecycle, "_client_connected", lambda port: False)


def _stops(monkeypatch):
    stopped = []
    monkeypatch.setattr(maya_s, "stop_instance", lambda alias="maya-s": stopped.append(("maya", alias)) or (True, ""))
    monkeypatch.setattr(strata_gateway, "stop_instance", lambda alias: stopped.append(("strata", alias)) or (True, ""))
    return stopped


def test_idle_model_is_unloaded_after_the_shared_idle_time(usage, monkeypatch):
    _policy(monkeypatch, minutes=30)
    _loaded(monkeypatch, [("maya-s", {"alias": "maya-s", "port": 8099}),
                          ("strata", {"alias": "qwen", "port": 8096})])
    stopped = _stops(monkeypatch)
    now = 10_000.0
    large_model_lifecycle.mark_used("maya-s", "maya-s", at=now - 31 * 60)
    large_model_lifecycle.mark_used("strata", "qwen", at=now - 5 * 60)
    assert asyncio.run(large_model_lifecycle.idle_unload_once(now)) == ["maya-s:maya-s"]
    assert stopped == [("maya", "maya-s")]


def test_idle_unload_respects_exclude_disabled_policy_and_unknown_use(usage, monkeypatch):
    now = 10_000.0
    stopped = _stops(monkeypatch)
    _policy(monkeypatch, enabled=False)
    _loaded(monkeypatch, [("strata", {"alias": "qwen", "port": 8096})])
    large_model_lifecycle.mark_used("strata", "qwen", at=0)
    assert asyncio.run(large_model_lifecycle.idle_unload_once(now)) == []
    _policy(monkeypatch)
    _loaded(monkeypatch, [("strata", {"alias": "qwen", "port": 8096, "idle_exclude": True})])
    assert asyncio.run(large_model_lifecycle.idle_unload_once(now)) == []
    # Never used through ControlDeck: the clock starts now, nothing is stopped.
    _loaded(monkeypatch, [("strata", {"alias": "new", "port": 8097})])
    assert asyncio.run(large_model_lifecycle.idle_unload_once(now)) == []
    assert large_model_lifecycle.last_used("strata", "new") == now
    assert stopped == []


def test_external_client_keeps_model_loaded(usage, monkeypatch):
    _policy(monkeypatch)
    _loaded(monkeypatch, [("strata", {"alias": "qwen", "port": 8096})])
    monkeypatch.setattr(large_model_lifecycle, "_client_connected", lambda port: port == 8096)
    stopped = _stops(monkeypatch)
    large_model_lifecycle.mark_used("strata", "qwen", at=0)
    assert asyncio.run(large_model_lifecycle.idle_unload_once(10_000.0)) == []
    assert stopped == [] and large_model_lifecycle.last_used("strata", "qwen") == 10_000.0


def _maya_prepared(monkeypatch, *, on_demand=True):
    healthy = {"ok": False}
    async def health(alias="maya-s"):
        return {"ok": healthy["ok"], "status_code": None}
    monkeypatch.setattr(maya_s, "health", health)
    monkeypatch.setattr(maya_s, "_config", lambda: object())
    monkeypatch.setattr(maya_s, "_running", lambda: False)
    monkeypatch.setattr(maya_s, "get_settings", lambda: {"on_demand_start": on_demand})
    async def released(runtime):
        released.calls.append(runtime)
    released.calls = []
    monkeypatch.setattr(large_model_lifecycle, "release_for", released)
    started = []
    def start(alias="maya-s"):
        started.append(alias)
        healthy["ok"] = True
        return True, ""
    monkeypatch.setattr(maya_s, "start_instance", start)
    async def wait_ready(*, timeout_seconds=900):
        return healthy["ok"]
    monkeypatch.setattr(maya_s, "wait_ready", wait_ready)
    return started, released.calls


def test_maya_starts_on_request_and_frees_memory_first(usage, monkeypatch):
    started, released = _maya_prepared(monkeypatch)
    assert asyncio.run(maya_s.ensure_ready("maya-s")) is True
    assert started == ["maya-s"] and released == ["maya-s"]
    assert large_model_lifecycle.last_used("maya-s", "maya-s") is not None


def test_maya_on_demand_start_can_be_turned_off(usage, monkeypatch):
    started, _ = _maya_prepared(monkeypatch, on_demand=False)
    assert asyncio.run(maya_s.ensure_ready("maya-s")) is False
    assert started == []
    # Startup auto-start passes start=True explicitly.
    assert asyncio.run(maya_s.ensure_ready("maya-s", start=True)) is True


def test_release_for_maya_stops_strata_and_idle_llama(monkeypatch):
    from app.models_mgmt import llama, strata
    stopped = _stops(monkeypatch)
    monkeypatch.setattr(maya_s, "installed", lambda: True)
    monkeypatch.setattr(maya_s, "is_running", lambda: False)
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "list_instances",
                        lambda: [{"alias": "qwen", "loaded": True}, {"alias": "off", "loaded": False}])
    calls = []
    async def release_llms(**kwargs):
        calls.append(kwargs)
        return True, "", 0
    monkeypatch.setattr(llama, "release_loaded_llms", release_llms)
    asyncio.run(large_model_lifecycle.release_for("maya-s"))
    assert stopped == [("strata", "qwen")] and calls == [{}]


def test_release_for_strata_stops_running_maya_only(monkeypatch):
    from app.models_mgmt import strata
    stopped = _stops(monkeypatch)
    monkeypatch.setattr(maya_s, "installed", lambda: True)
    monkeypatch.setattr(maya_s, "is_running", lambda: True)
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [{"alias": "qwen", "loaded": True}])
    asyncio.run(large_model_lifecycle.release_for("strata"))
    assert stopped == [("maya", "maya-s")]


@pytest.mark.parametrize("value,ok", [("8k", True), ("256k", True), ("1024k", True),
                                      ("2k", False), ("2048k", False), ("32K", False), ("32", False)])
def test_strata_context_accepts_any_k_size_in_range(value, ok):
    assert strata_gateway.valid_context(value) is ok


def test_release_for_keeps_a_resident_model(monkeypatch):
    from app.models_mgmt import strata
    stopped = _stops(monkeypatch)
    monkeypatch.setattr(maya_s, "installed", lambda: True)
    monkeypatch.setattr(maya_s, "is_running", lambda: True)
    monkeypatch.setattr(maya_s, "get_settings", lambda: {"idle_exclude": True})
    monkeypatch.setattr(strata, "installed", lambda: False)
    with pytest.raises(RuntimeError, match="常駐"):
        asyncio.run(large_model_lifecycle.release_for("strata"))
    assert stopped == []


def test_gateway_switch_frees_the_other_large_model_only_when_target_is_stopped(monkeypatch):
    from app.models_mgmt import local_llm
    calls = []
    async def release(runtime):
        calls.append(runtime)
    async def idle(seconds=60.0):
        return True
    monkeypatch.setattr(large_model_lifecycle, "release_for", release)
    monkeypatch.setattr(large_model_lifecycle, "_wait_idle_requests", idle)
    rows = {"qwen": {"runtime": "strata", "loaded": False},
            "maya-s": {"runtime": "maya-s", "loaded": True},
            "Qwen3.8-27B": {"runtime": "llama.cpp", "loaded": False}}
    monkeypatch.setattr(local_llm, "find", lambda alias: rows.get(alias))
    for alias in ("qwen", "maya-s", "Qwen3.8-27B", "missing"):
        asyncio.run(large_model_lifecycle.prepare_switch(alias))
    assert calls == ["strata"]


def test_gateway_switch_never_cuts_a_running_request(monkeypatch):
    from app.models_mgmt import local_llm
    calls = []
    async def release(runtime):
        calls.append(runtime)
    async def busy(seconds=60.0):
        return False
    monkeypatch.setattr(large_model_lifecycle, "release_for", release)
    monkeypatch.setattr(large_model_lifecycle, "_wait_idle_requests", busy)
    monkeypatch.setattr(local_llm, "find", lambda alias: {"runtime": "strata", "loaded": False})
    asyncio.run(large_model_lifecycle.prepare_switch("qwen"))
    assert calls == []


def test_registered_strata_port_wins_over_an_empty_llama_endpoint(monkeypatch):
    from app.models_mgmt import llama, runtime_provider, strata
    monkeypatch.setattr(llama, "endpoint_ports", lambda: {8096, 8097})
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [{"alias": "qwen", "port": 8096}])
    monkeypatch.setattr(maya_s, "list_instances", lambda: [])
    assert runtime_provider.provider_for_base_url("http://127.0.0.1:8096/v1").kind == "strata"
    assert runtime_provider.provider_for_base_url("http://127.0.0.1:8097/v1").kind == "llama.cpp"
