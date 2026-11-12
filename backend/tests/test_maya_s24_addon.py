"""Maya-S24 is strictly opt-in: code install, 95 GB download, and serving are separate.

No network, GPU, systemd or real Maya payload is required for this test suite.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.features import gpu_runtime, registry
from app.models_mgmt import maya_s24


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setattr(maya_s24, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(maya_s24, "available", lambda: True)
    monkeypatch.setattr(maya_s24.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(maya_s24, "_running", lambda: False)
    monkeypatch.setattr(maya_s24, "_ensure_alias_port_available", lambda: None)
    monkeypatch.setattr(maya_s24, "_port_busy", lambda: False)
    return tmp_path


def _installed(tmp_path):
    source = tmp_path / "runtimes" / "maya-s24" / "engine"
    source.mkdir(parents=True, exist_ok=True)
    (source / "maya.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (source.parent / "managed.json").write_text(
        json.dumps({"source": maya_s24._REPO, "tag": "v1.1.60"}), encoding="utf-8")
    return source


def test_feature_registered_independently_from_strata():
    assert registry.FEATURES["maya-s24"]["repo"] == "mw00/project-maya"
    assert registry.FEATURES["maya-s24"]["kind"] == "gpu-runtime"
    assert "maya-s24" in gpu_runtime.FEATURE_IDS
    assert registry.FEATURES["maya-s24"]["route_gated"] is False


def test_runtime_uninstalled_has_no_files_or_automatic_models(managed):
    status = maya_s24.runtime_status()
    assert not status["installed"]
    assert not status["configured"]
    assert not status["running"]
    assert not maya_s24.models_dir().exists()
    assert "94.7GB" in status["warning"]


def test_install_only_clones_official_release_and_never_runs_maya(managed, monkeypatch):
    monkeypatch.setattr(maya_s24, "_latest_release", lambda: "v1.1.60")
    commands = []

    def fake_exec(argv, *, cwd=None, timeout=120, check=True):
        commands.append(argv)
        assert argv[0] == "git"
        assert "--download-model" not in argv
        dest = Path(argv[-1])
        dest.mkdir(parents=True)
        (dest / "maya.sh").write_text("#!/bin/sh\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(maya_s24, "_exec", fake_exec)
    result = maya_s24._install()
    assert result["installed"]
    assert not result["configured"]
    assert not maya_s24.models_dir().exists()
    assert maya_s24._REPO in commands[0]
    assert "v1.1.60" in commands[0]


def test_model_download_needs_an_explicit_prepare_and_selects_only_maya_s24(
    managed, monkeypatch,
):
    _installed(managed)
    args_seen = []
    config = maya_s24.source() / "maya-glm-maya-s24-hip.json"

    def fake_exec(argv, **kwargs):
        args_seen.append(argv)
        config.write_text('{"host":"127.0.0.1"}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(maya_s24.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=150*1000**3))
    monkeypatch.setattr(maya_s24, "_exec", fake_exec)
    state = maya_s24._prepare_model(gpu_index=1)
    assert state["configured"]
    assert len(args_seen) == 1
    argv = args_seen[0]
    assert argv[0] == "bash"
    assert argv[argv.index("--model") + 1] == "glm-maya-s24"
    assert argv[argv.index("--gpu") + 1] == "1"
    assert "--download-model" in argv and "--no-start" in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"


def test_prepare_checks_space_and_refuses_invalid_gpu(managed, monkeypatch):
    _installed(managed)
    called = []
    monkeypatch.setattr(maya_s24, "_exec", lambda *a, **k: called.append(a))
    with pytest.raises(maya_s24.MayaError, match="GPU番号"):
        maya_s24._prepare_model(gpu_index=25)
    monkeypatch.setattr(maya_s24.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=99*1000**3))
    with pytest.raises(maya_s24.MayaError, match="110GB"):
        maya_s24._prepare_model()
    assert not called


def test_start_requires_preparation_and_uses_user_unit(managed, monkeypatch):
    _installed(managed)
    assert maya_s24.start_instance() == (False, "明示的なMaya-S24モデル準備が必要です")
    (maya_s24.source() / "maya-glm-maya-s24-hip.json").write_text("{}")
    maya_s24._prepared_marker().write_text('{"model":"glm-maya-s24"}')
    calls = []
    monkeypatch.setattr(maya_s24, "_exec", lambda argv, **kw:
                        (calls.append(argv) or SimpleNamespace(returncode=0, stdout="")))
    assert maya_s24.start_instance()[0]
    command = calls[0]
    assert "systemd-run" == command[0]
    assert "--collect" in command
    assert "127.0.0.1" in command
    assert "--download-model" not in command


def test_uninstall_rejects_running_service_and_preserves_weights(managed, monkeypatch):
    _installed(managed)
    weights = maya_s24.models_dir() / "Maya-S24" / "model.gguf"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"saved")
    monkeypatch.setattr(maya_s24, "_running", lambda: True)
    with pytest.raises(maya_s24.MayaError, match="稼働中"):
        maya_s24.uninstall()
    assert maya_s24.installed() and weights.exists()
    monkeypatch.setattr(maya_s24, "_running", lambda: False)
    result = maya_s24.uninstall()
    assert not result["installed"]
    assert weights.read_bytes() == b"saved"


def test_uninstall_refuses_symlink_directory(managed, monkeypatch):
    _installed(managed)
    external = managed / "unrelated"
    external.mkdir()
    root = maya_s24.root()
    # Do not delete symlinks or their target even if files look managed.
    (root / "engine" / "maya.sh").unlink()
    (root / "engine").rename(root / "engine-old")
    (root / "engine").symlink_to(root / "engine-old", target_is_directory=True)
    with pytest.raises(maya_s24.MayaError):
        maya_s24.uninstall()
    assert (root / "engine-old").exists()


def test_gateway_ready_never_starts_or_downloads_model(managed, monkeypatch):
    _installed(managed)
    monkeypatch.setattr(maya_s24, "health", lambda alias="maya-s24":
                        asyncio.sleep(0, result={"ok": False, "status_code": None}))
    assert asyncio.run(maya_s24.ensure_ready("maya-s24")) is False


def test_installer_api_does_not_start_model(admin_client):
    response = admin_client.get("/api/v1/features")
    assert response.status_code == 200
    addons = {item["id"]: item for item in response.json()}
    assert addons["maya-s24"]["kind"] == "gpu-runtime"


def test_prepare_endpoint_requires_installed_runtime(admin_client, monkeypatch):
    monkeypatch.setattr(maya_s24, "installed", lambda: False)
    response = admin_client.post("/api/v1/features/maya-s24/model/prepare-jobs",
                                 json={"gpu_index": 0},
                                 headers={"X-Requested-With": "ControlDeck"})
    assert response.status_code == 422


def test_prepare_endpoint_rejects_untrusted_extra_fields(admin_client):
    response = admin_client.post("/api/v1/features/maya-s24/model/prepare-jobs",
                                 json={"gpu_index": 0, "source_url": "https://malicious.invalid"},
                                 headers={"X-Requested-With": "ControlDeck"})
    assert response.status_code == 422


def test_partial_download_config_is_not_marked_ready(managed):
    source = _installed(managed)
    (source / "maya-glm-maya-s24-hip.json").write_text("{}")
    assert not maya_s24.runtime_status()["configured"]
    assert maya_s24._config() is None
    assert maya_s24.start_instance()[0] is False


def test_lock_prevents_parallel_model_operations(managed):
    _installed(managed)
    with maya_s24._operation_lock():
        with pytest.raises(maya_s24.MayaError, match="別の操作"):
            maya_s24.uninstall()


def test_update_refuses_active_model_before_git_fetch(managed, monkeypatch):
    _installed(managed)
    monkeypatch.setattr(maya_s24, "_running", lambda: True)
    monkeypatch.setattr(maya_s24, "_latest_release", lambda: "v1.1.61")
    monkeypatch.setattr(maya_s24, "_exec",
                        lambda *args, **kw: pytest.fail("unexpected git change"))
    with pytest.raises(maya_s24.MayaError, match="停止"):
        maya_s24._update()


def test_default_gateway_does_not_autoselect_dormant_maya(managed, monkeypatch):
    from app.models_mgmt import gateway, local_llm
    monkeypatch.setattr(local_llm, "list_instances", lambda: [
        {"alias": "maya-s24", "runtime": "maya-s24", "role": "llm",
         "port": 8099, "loaded": False},
        {"alias": "qwen", "runtime": "llama.cpp", "role": "llm",
         "port": 8091, "loaded": False},
    ])
    assert gateway.resolve_instance(gateway.AUTO_MODEL)["alias"] == "qwen"
    assert gateway.resolve_instance("maya-s24")["alias"] == "maya-s24"


def test_maya_provider_never_auto_starts_on_discovery(managed, monkeypatch):
    from app.models_mgmt import providers, local_llm
    assert "maya-s24" in local_llm.RUNTIMES
    caps = providers.capabilities("maya-s24", managed=True)
    assert "list" in caps and "chat" in caps
    assert "load" not in caps and "delete" not in caps and "pull" not in caps


def test_health_requires_real_loaded_s24(monkeypatch, managed):
    import httpx
    from app.models_mgmt import maya_s24
    _installed(managed)
    (maya_s24.source() / "maya-glm-maya-s24-hip.json").write_text("{}")
    maya_s24._prepared_marker().write_text("{}")
    monkeypatch.setattr(maya_s24, "_running", lambda: True)

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get(self, url):
            req = httpx.Request("GET", url)
            if url.endswith("/health"):
                return httpx.Response(200, json={
                    "status":"ok", "model_label":"GLM-5.3-Flash · Maya-S24",
                    "unloaded": False}, request=req)
            return httpx.Response(200, json={"data":[
                {"id":"GLM-5.3-Flash", "status":{"value":"loaded"}}]}, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert asyncio.run(maya_s24.health())["ok"] is True


def test_maya_port_conflict_prevents_start(managed, monkeypatch):
    _installed(managed)
    (maya_s24.source() / "maya-glm-maya-s24-hip.json").write_text("{}")
    maya_s24._prepared_marker().write_text("{}")
    monkeypatch.setattr(maya_s24, "_port_busy", lambda: True)
    monkeypatch.setattr(maya_s24, "_exec",
                        lambda *a, **kw: pytest.fail("should not launch Maya"))
    ok, msg = maya_s24.start_instance()
    assert ok is False and "8099" in msg
