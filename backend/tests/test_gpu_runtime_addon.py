"""Opt-in GPU runtimes: llama.cpp remains; Strata replaces retired Lucebox."""
import asyncio

import pytest

from app.features import gpu_runtime, registry


def test_catalog_exposes_strata_not_lucebox():
    assert {"llama-cpp", "strata"} <= registry.KNOWN_FEATURES
    assert "lucebox" not in registry.KNOWN_FEATURES
    assert registry.FEATURES["strata"]["repo"] == "Niko1221/Strata"
    assert registry.FEATURES["strata"]["kind"] == "gpu-runtime"
    assert registry.FEATURES["strata"]["route_gated"] is False


def test_status_uses_strata_authoritative_status(monkeypatch):
    from app.features import strata_runtime

    monkeypatch.setattr(strata_runtime, "runtime_status", lambda: {
        "installed": True, "managed": True, "version": "v0.2.13",
        "healthy": True, "error": "", "available": True,
        "detail": {"runtime": "strata", "launch_command": "/tmp/strata-managed"},
    })
    state = gpu_runtime.status("strata")
    assert state["installed"] and state["version"] == "v0.2.13"
    assert state["detail"]["runtime"] == "strata"


def test_strata_install_calls_opt_in_manager_only(monkeypatch):
    from app.features import strata_runtime

    calls = []

    async def fake_install(job=None, *, options=None):
        calls.append(options)
        return {"runtime": "strata", "version": "v0.2.13"}

    monkeypatch.setattr(strata_runtime, "install", fake_install)
    result = asyncio.run(gpu_runtime.install("strata", None))
    assert calls == [{}]
    assert result["runtime"] == "strata"


def test_strata_update_and_delete_are_separate_from_llama(monkeypatch):
    from app.features import strata_runtime

    calls = []

    async def fake_update(job=None):
        calls.append("update")
        return {"version": "v0.2.13"}

    monkeypatch.setattr(strata_runtime, "update", fake_update)
    monkeypatch.setattr(strata_runtime, "uninstall", lambda: calls.append("delete") or {"removed": True})
    asyncio.run(gpu_runtime.update("strata"))
    assert gpu_runtime.uninstall("strata")["removed"] is True
    assert calls == ["update", "delete"]


def test_unknown_lucebox_routes_are_rejected(admin_client):
    assert admin_client.get("/api/v1/features/lucebox/release-status").status_code == 404
    assert admin_client.get("/api/v1/models/lucebox/status").status_code in (404, 405)  # generic route can match method


def test_strata_not_a_default_llm_runtime():
    from app.models_mgmt.runtime_policy import RuntimePolicy

    assert RuntimePolicy().selected_runtime == "ollama"
    with pytest.raises(ValueError):
        RuntimePolicy(selected_runtime="strata")
