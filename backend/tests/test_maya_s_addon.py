"""Maya-S is strictly opt-in: code install, 96.5 GB download, and serving are separate.

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
from app.models_mgmt import maya_s


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setattr(maya_s, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(maya_s, "available", lambda: True)
    monkeypatch.setattr(maya_s.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(maya_s, "_running", lambda: False)
    monkeypatch.setattr(maya_s, "_ensure_alias_port_available", lambda: None)
    monkeypatch.setattr(maya_s, "_port_busy", lambda: False)
    from app.models_mgmt import large_model_lifecycle, runtime_policy
    # A start selects Maya-S and records its use: keep both per test.
    monkeypatch.setattr(runtime_policy, "_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(large_model_lifecycle, "_usage_path", lambda: tmp_path / "usage.json")
    return tmp_path


def _installed(tmp_path):
    source = tmp_path / "runtimes" / "maya-s" / "engine"
    source.mkdir(parents=True, exist_ok=True)
    (source / "maya.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (source.parent / "managed.json").write_text(
        json.dumps({"source": maya_s._REPO, "tag": "v1.1.60"}), encoding="utf-8")
    return source


def test_feature_registered_independently_from_strata():
    assert registry.FEATURES["maya-s"]["repo"] == "mw00/project-maya"
    assert registry.FEATURES["maya-s"]["kind"] == "gpu-runtime"
    assert "maya-s" in gpu_runtime.FEATURE_IDS
    assert registry.FEATURES["maya-s"]["route_gated"] is False


def test_runtime_uninstalled_has_no_files_or_automatic_models(managed):
    status = maya_s.runtime_status()
    assert not status["installed"]
    assert not status["configured"]
    assert not status["running"]
    assert not maya_s.models_dir().exists()
    assert "96.5GB" in status["warning"]


def test_install_only_clones_official_release_and_never_runs_maya(managed, monkeypatch):
    monkeypatch.setattr(maya_s, "_latest_release", lambda: "v1.1.60")
    commands = []

    def fake_exec(argv, *, cwd=None, timeout=120, check=True):
        commands.append(argv)
        assert argv[0] == "git"
        assert "--download-model" not in argv
        dest = Path(argv[-1])
        dest.mkdir(parents=True)
        (dest / "maya.sh").write_text("#!/bin/sh\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(maya_s, "_exec", fake_exec)
    result = maya_s._install()
    assert result["installed"]
    assert not result["configured"]
    assert not maya_s.models_dir().exists()
    assert maya_s._REPO in commands[0]
    assert "v1.1.60" in commands[0]


def test_model_download_needs_an_explicit_prepare_and_selects_only_maya_s(
    managed, monkeypatch,
):
    _installed(managed)
    args_seen = []
    config = maya_s.source() / "maya-glm-maya-s-hip.json"

    def fake_exec(argv, **kwargs):
        args_seen.append(argv)
        config.write_text('{"host":"127.0.0.1"}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(maya_s.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=150*1000**3))
    monkeypatch.setattr(maya_s, "_exec", fake_exec)
    state = maya_s._prepare_model(gpu_index=1)
    assert state["configured"]
    assert len(args_seen) == 1
    argv = args_seen[0]
    assert argv[0] == "bash"
    assert argv[argv.index("--model") + 1] == "glm-maya-s"
    assert argv[argv.index("--gpu") + 1] == "1"
    assert "--download-model" in argv and "--no-start" in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"


def test_prepare_checks_space_and_refuses_invalid_gpu(managed, monkeypatch):
    _installed(managed)
    called = []
    monkeypatch.setattr(maya_s, "_exec", lambda *a, **k: called.append(a))
    with pytest.raises(maya_s.MayaError, match="GPU番号"):
        maya_s._prepare_model(gpu_index=25)
    monkeypatch.setattr(maya_s.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=99*1000**3))
    with pytest.raises(maya_s.MayaError, match="110GB"):
        maya_s._prepare_model()
    assert not called


def test_start_requires_preparation_and_uses_user_unit(managed, monkeypatch):
    _installed(managed)
    assert maya_s.start_instance() == (False, "明示的なMaya-Sモデル準備が必要です")
    (maya_s.source() / "maya-glm-maya-s-hip.json").write_text("{}")
    maya_s._prepared_marker().write_text('{"model":"glm-maya-s"}')
    calls = []
    monkeypatch.setattr(maya_s, "_exec", lambda argv, **kw:
                        (calls.append(argv) or SimpleNamespace(returncode=0, stdout="")))
    assert maya_s.start_instance()[0]
    command = calls[0]
    assert "systemd-run" == command[0]
    assert "--collect" in command
    assert "127.0.0.1" in command
    assert "--download-model" not in command


def test_uninstall_rejects_running_service_and_preserves_weights(managed, monkeypatch):
    _installed(managed)
    weights = maya_s.models_dir() / "Maya-S" / "model.gguf"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"saved")
    monkeypatch.setattr(maya_s, "_running", lambda: True)
    with pytest.raises(maya_s.MayaError, match="稼働中"):
        maya_s.uninstall()
    assert maya_s.installed() and weights.exists()
    monkeypatch.setattr(maya_s, "_running", lambda: False)
    result = maya_s.uninstall()
    assert not result["installed"]
    assert weights.read_bytes() == b"saved"


def test_uninstall_refuses_symlink_directory(managed, monkeypatch):
    _installed(managed)
    external = managed / "unrelated"
    external.mkdir()
    root = maya_s.root()
    # Do not delete symlinks or their target even if files look managed.
    (root / "engine" / "maya.sh").unlink()
    (root / "engine").rename(root / "engine-old")
    (root / "engine").symlink_to(root / "engine-old", target_is_directory=True)
    with pytest.raises(maya_s.MayaError):
        maya_s.uninstall()
    assert (root / "engine-old").exists()


def test_gateway_ready_never_starts_or_downloads_model(managed, monkeypatch):
    _installed(managed)
    monkeypatch.setattr(maya_s, "health", lambda alias="maya-s":
                        asyncio.sleep(0, result={"ok": False, "status_code": None}))
    assert asyncio.run(maya_s.ensure_ready("maya-s")) is False


def test_installer_api_does_not_start_model(admin_client):
    response = admin_client.get("/api/v1/features")
    assert response.status_code == 200
    addons = {item["id"]: item for item in response.json()}
    assert addons["maya-s"]["kind"] == "gpu-runtime"


def test_prepare_endpoint_requires_installed_runtime(admin_client, monkeypatch):
    monkeypatch.setattr(maya_s, "installed", lambda: False)
    response = admin_client.post("/api/v1/features/maya-s/model/prepare-jobs",
                                 json={"gpu_index": 0},
                                 headers={"X-Requested-With": "ControlDeck"})
    assert response.status_code == 422


def test_prepare_endpoint_rejects_untrusted_extra_fields(admin_client):
    response = admin_client.post("/api/v1/features/maya-s/model/prepare-jobs",
                                 json={"gpu_index": 0, "source_url": "https://malicious.invalid"},
                                 headers={"X-Requested-With": "ControlDeck"})
    assert response.status_code == 422


def test_partial_download_config_is_not_marked_ready(managed):
    source = _installed(managed)
    (source / "maya-glm-maya-s-hip.json").write_text("{}")
    assert not maya_s.runtime_status()["configured"]
    assert maya_s._config() is None
    assert maya_s.start_instance()[0] is False



def test_old_s24_config_or_marker_never_claims_maya_s_ready(managed):
    source = _installed(managed)
    old_config = source / "maya-glm-maya-s24-hip.json"
    old_config.write_text("{}", encoding="utf-8")
    maya_s._prepared_marker().write_text('{"model":"glm-maya-s"}', encoding="utf-8")
    assert maya_s._config() is None  # S24 filename cannot match the S quant
    new_config = source / "maya-glm-maya-s-hip.json"
    new_config.write_text("{}", encoding="utf-8")
    maya_s._prepared_marker().write_text('{"model":"glm-maya-s24"}', encoding="utf-8")
    assert maya_s._config() is None  # S24 marker cannot assert S preparation


def test_lock_prevents_parallel_model_operations(managed):
    _installed(managed)
    with maya_s._operation_lock():
        with pytest.raises(maya_s.MayaError, match="別の操作"):
            maya_s.uninstall()


def test_update_refuses_active_model_before_git_fetch(managed, monkeypatch):
    _installed(managed)
    monkeypatch.setattr(maya_s, "_running", lambda: True)
    monkeypatch.setattr(maya_s, "_latest_release", lambda: "v1.1.61")
    monkeypatch.setattr(maya_s, "_exec",
                        lambda *args, **kw: pytest.fail("unexpected git change"))
    with pytest.raises(maya_s.MayaError, match="停止"):
        maya_s._update()


def test_default_gateway_does_not_autoselect_dormant_maya(managed, monkeypatch):
    from app.models_mgmt import gateway, local_llm
    monkeypatch.setattr(local_llm, "list_instances", lambda: [
        {"alias": "maya-s", "runtime": "maya-s", "role": "llm",
         "port": 8099, "loaded": False},
        {"alias": "qwen", "runtime": "llama.cpp", "role": "llm",
         "port": 8091, "loaded": False},
    ])
    assert gateway.resolve_instance(gateway.AUTO_MODEL)["alias"] == "qwen"
    assert gateway.resolve_instance("maya-s")["alias"] == "maya-s"


def test_maya_provider_never_auto_starts_on_discovery(managed, monkeypatch):
    from app.models_mgmt import providers, local_llm
    assert "maya-s" in local_llm.RUNTIMES
    caps = providers.capabilities("maya-s", managed=True)
    assert "list" in caps and "chat" in caps
    assert "load" not in caps and "delete" not in caps and "pull" not in caps


def test_health_requires_real_loaded_maya_s(monkeypatch, managed):
    import httpx
    from app.models_mgmt import maya_s
    _installed(managed)
    (maya_s.source() / "maya-glm-maya-s-hip.json").write_text("{}")
    maya_s._prepared_marker().write_text('{"model":"glm-maya-s"}')
    monkeypatch.setattr(maya_s, "_running", lambda: True)

    class Client:
        label = "GLM-5.3-Flash · Maya-S"
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get(self, url):
            req = httpx.Request("GET", url)
            if url.endswith("/health"):
                return httpx.Response(200, json={
                    "status":"ok", "model_label":self.label,
                    "unloaded": False}, request=req)
            return httpx.Response(200, json={"data":[
                {"id":"GLM-5.3-Flash", "status":{"value":"loaded"}}]}, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert asyncio.run(maya_s.health())["ok"] is True
    Client.label = "GLM-5.3-Flash · Maya-S24"
    assert asyncio.run(maya_s.health())["ok"] is False


def test_maya_port_conflict_prevents_start(managed, monkeypatch):
    _installed(managed)
    (maya_s.source() / "maya-glm-maya-s-hip.json").write_text("{}")
    maya_s._prepared_marker().write_text('{"model":"glm-maya-s"}')
    monkeypatch.setattr(maya_s, "_port_busy", lambda: True)
    monkeypatch.setattr(maya_s, "_exec",
                        lambda *a, **kw: pytest.fail("should not launch Maya"))
    ok, msg = maya_s.start_instance()
    assert ok is False and "8099" in msg


def test_privileged_process_never_mutates_managed_maya(managed, monkeypatch):
    _installed(managed)
    monkeypatch.setattr(maya_s.os, "geteuid", lambda: 0)
    with pytest.raises(maya_s.MayaError, match="root"):
        maya_s.uninstall()
    with pytest.raises(maya_s.MayaError, match="root"):
        maya_s.start_instance()
    assert maya_s.installed()


def test_settings_save_does_not_start_or_download(managed, monkeypatch):
    _installed(managed)
    monkeypatch.setattr(maya_s, '_exec', lambda *a, **k: pytest.fail('settings must not execute commands'))
    values = maya_s.get_settings() | {'context': 16384, 'parallel': 2, 'drafting': 'off'}
    result = maya_s.save_settings(values)
    assert result['settings'] == values
    assert not result['configured'] and not result['running']
    assert not maya_s.models_dir().exists()


def test_settings_reject_outside_allowed_roots(managed):
    _installed(managed)
    from fastapi import HTTPException
    with pytest.raises((HTTPException, ValueError, PermissionError)):
        maya_s.save_settings(maya_s.get_settings() | {'model_path': '/etc/passwd'})


def test_settings_while_running_rejected(managed, monkeypatch):
    _installed(managed)
    monkeypatch.setattr(maya_s, '_running', lambda: True)
    with pytest.raises(maya_s.MayaError, match='停止'):
        maya_s.save_settings({**maya_s.get_settings(), 'context': 32768})
    # Lifecycle flags are ControlDeck-side only and may change while running.
    maya_s.save_settings({**maya_s.get_settings(), 'idle_exclude': True})
    assert maya_s.get_settings()['idle_exclude'] is True


def test_build_only_never_invokes_installer_or_engine(managed, monkeypatch):
    _installed(managed)
    commands = []
    original = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda p: True if str(p).endswith('/llvm/bin/clang++') else original(p))
    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(maya_s.subprocess, 'run', run)
    state = maya_s.build_engine()
    assert len(commands) == 2
    assert all(c[0] == 'cmake' for c in commands)
    assert commands[1][-2:] == ['-j', '1']
    assert not state['configured']
    assert not maya_s.models_dir().exists()


def test_maya_policy_selection_never_starts_model(managed, monkeypatch):
    _installed(managed)
    from app.models_mgmt import runtime_policy
    monkeypatch.setattr(maya_s, 'start_instance', lambda *a: pytest.fail('must not start'))
    policy = runtime_policy.RuntimePolicy(selected_runtime='maya-s', coexistence='coexist')
    asyncio.run(runtime_policy.apply_selection(policy))
    assert policy.selected_runtime == 'maya-s'


def test_addon_maya_rejects_unready_and_vision(managed, monkeypatch):
    from app.models_mgmt import ai_gateway, runtime_policy
    monkeypatch.setattr(runtime_policy, 'get_policy', lambda: SimpleNamespace(selected_runtime='maya-s'))
    async def not_ready(*args):
        return {'ok': False}
    monkeypatch.setattr(maya_s, 'health', not_ready)
    for capability in ('text.generate', 'vision.analyze'):
        with pytest.raises(ai_gateway.AITargetUnavailable):
            asyncio.run(ai_gateway.resolve_ai_target(capability))


def test_models_page_status_endpoint_reports_without_side_effects(admin_client, managed, monkeypatch):
    """Models画面は未準備かどうかで「ダウンロード／既存モデル」を出し分ける。
    状態の問い合わせでは取得・ビルド・起動を一切しない。"""
    _installed(managed)
    monkeypatch.setattr(maya_s, "_exec", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no exec")))
    response = admin_client.get("/api/v1/features/maya-s/model")
    assert response.status_code == 200
    body = response.json()
    assert body["installed"] is True
    assert body["configured"] is False
    assert body["running"] is False
    assert body["settings"]["context"] == 8192


def test_model_preparation_jobs_are_listed_as_model_jobs():
    """Modelsの進行表示は kind=model. のジョブを拾う。Maya-Sの準備・起動もそこへ出す。"""
    from pathlib import Path
    router = (Path(__file__).parents[1] / "app" / "features" / "router.py").read_text(encoding="utf-8")
    for kind in ("prepare", "start", "existing"):
        assert f'jobs.create("model.maya-s.{kind}"' in router


def test_built_reflects_upstream_hip_build_dir(managed):
    _installed(managed)
    assert maya_s.runtime_status()['built'] is False
    build = maya_s.source() / 'build-hip'
    build.mkdir(parents=True)
    (build / 'maya').write_text('bin')
    assert maya_s.runtime_status()['built'] is False
    (build / 'MAYA-BUILD.json').write_text('{}')
    assert maya_s.runtime_status()['built'] is True


def _maya_request(max_tokens):
    from app.models_mgmt.runtime_provider import RuntimeChatRequest
    return RuntimeChatRequest(base_url='http://127.0.0.1:8099/v1', model='maya-s',
                              messages=[{'role': 'user', 'content': 'hi'}], max_tokens=max_tokens)


def _count_tokens(monkeypatch, prompt, context):
    from app.models_mgmt import runtime_provider
    async def room(_messages):
        return prompt, context
    monkeypatch.setattr(maya_s, 'prompt_room', room)
    async def ready(*_a, **_k):
        return True
    monkeypatch.setattr(maya_s, 'ensure_ready', ready)
    async def nothing(*_a, **_k):
        return None
    monkeypatch.setattr(runtime_provider.OpenAICompatibleRuntimeProvider, '_prepare', nothing)


def test_maya_provider_shrinks_output_budget_to_fit_context(monkeypatch):
    from app.models_mgmt.runtime_provider import MayaRuntimeProvider
    _count_tokens(monkeypatch, prompt=3000, context=8192)
    request = _maya_request(16384)
    asyncio.run(MayaRuntimeProvider()._prepare(request))
    assert request.max_tokens == 5192
    small = _maya_request(1024)
    asyncio.run(MayaRuntimeProvider()._prepare(small))
    assert small.max_tokens == 1024


def test_maya_provider_reports_prompt_longer_than_context(monkeypatch):
    from app.models_mgmt.runtime_provider import MayaRuntimeProvider, RuntimeProviderError
    _count_tokens(monkeypatch, prompt=9000, context=8192)
    with pytest.raises(RuntimeProviderError, match='入力が長すぎます'):
        asyncio.run(MayaRuntimeProvider()._prepare(_maya_request(2048)))


def test_maya_endpoint_uses_maya_provider(managed, monkeypatch):
    from app.models_mgmt import runtime_provider
    monkeypatch.setattr(maya_s, 'list_instances', lambda: [{'port': 8099}])
    assert runtime_provider.provider_for_base_url('http://127.0.0.1:8099/v1').kind == 'maya-s'


def test_gateway_fits_maya_output_budget(monkeypatch):
    from app.models_mgmt import gateway
    _count_tokens(monkeypatch, prompt=3000, context=8192)
    payload = {'messages': [], 'max_tokens': 32000, 'max_completion_tokens': 100}
    asyncio.run(gateway._fit_maya_output(payload))
    assert payload == {'messages': [], 'max_tokens': 5192, 'max_completion_tokens': 100}
    _count_tokens(monkeypatch, prompt=9000, context=8192)
    over = {'messages': [], 'max_tokens': 32000}
    asyncio.run(gateway._fit_maya_output(over))
    assert over['max_tokens'] == 32000  # Maya's own 400 explains it


def test_prompt_room_reads_maya_token_count(monkeypatch):
    import httpx
    real = httpx.AsyncClient
    def handler(req):
        assert req.url.path == '/api/tokens'
        return httpx.Response(200, json={'tokens': 18, 'max_context': 8192})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler)))
    # Maya's 8-token CTX_SLACK is part of the usable context.
    assert asyncio.run(maya_s.prompt_room([{'role': 'user', 'content': 'x'}])) == (18, 8184)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    assert asyncio.run(maya_s.prompt_room([])) is None


def test_save_settings_enables_maya_output_clamp(managed, monkeypatch):
    _installed(managed)
    config = maya_s.source() / 'maya-maya-s-v2-iq2_xxs-hip.json'
    config.write_text(json.dumps({'args': ['--parallel', '1'], 'env': {}}))
    maya_s._prepared_marker().write_text(json.dumps({'model': 'glm-maya-s'}))
    monkeypatch.setattr(maya_s, '_running', lambda: False)
    maya_s.save_settings(maya_s.get_settings())
    assert json.loads(config.read_text())['fit_max_tokens'] is True


def test_reasoning_effort_default_is_high_for_coding(monkeypatch, tmp_path):
    from app.models_mgmt import maya_s

    monkeypatch.setattr(maya_s, "root", lambda: tmp_path)
    assert maya_s.get_settings()["reasoning_effort"] == "high"
    from app.features.router import MayaSettingsBody
    assert MayaSettingsBody.model_fields["reasoning_effort"].default == "high"
