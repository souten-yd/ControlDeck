"""Strata must not download a model or alter the active runtime by default."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from app.models_mgmt import strata, strata_gateway


def _managed(monkeypatch, tmp_path):
    from app.models_mgmt import large_model_lifecycle, runtime_policy
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(strata_gateway, "_running", lambda: [])
    # A start makes Strata the selected runtime and records its use: keep both
    # out of the session-wide data dir so later tests see the default policy.
    monkeypatch.setattr(runtime_policy, "_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(large_model_lifecycle, "_usage_path", lambda: tmp_path / "usage.json")


def test_status_parses_upstream_json_even_when_human_lines_follow(monkeypatch):
    monkeypatch.setattr(strata, "installed", lambda: True)
    def fake_run(args, *, timeout=30):
        assert args == ["launch", "--status", "--output", "json"]
        return subprocess.CompletedProcess(args, 0,
            stdout='{"schema_version":1,"ok":true,"running":[{"profile":"launch-cd-qwen","port":8095,"state":"ready"}]}\ncd-qwen: ready\n')
    monkeypatch.setattr(strata_gateway, "_run", fake_run)
    rows = strata_gateway._running()
    assert rows[0]["profile"] == "launch-cd-qwen"


def test_auto_load_refuses_first_download(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    monkeypatch.setattr(strata_gateway, "_read", lambda: {
        "qwen": {"family": "qwen", "model": "iq2_xs", "port": 8095,
                 "context": "32k", "activated": False},
    })
    called = []
    monkeypatch.setattr(strata_gateway, "_run", lambda *a, **kw: called.append(a))
    assert strata_gateway._start("qwen", explicit=False)[0] is False
    assert called == []


def test_explicit_start_uses_fixed_cli_args_and_records_activation(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    models = tmp_path / "models.json"
    models.write_text(json.dumps({
        "qwen": {"family": "qwen", "model": "iq2_xs", "port": 8095,
                 "context": "32k", "activated": False},
    }))
    commands = []
    def fake_run(args, *, timeout=30):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"ok":true}')
    monkeypatch.setattr(strata_gateway, "_run", fake_run)
    states = iter([False, True])
    monkeypatch.setattr(strata_gateway, "get_instance", lambda _a: {"loaded": next(states)})
    monkeypatch.setattr(strata_gateway, "_api_ready", lambda _p: True)
    ok, error = strata_gateway.start_instance("qwen")
    assert ok and not error
    assert commands[0][:5] == ["launch", "--family", "qwen", "--model", "iq2_xs"]
    assert "--no-tui" in commands[0] and "--yes" in commands[0]
    assert "--access" in commands[0] and "local" in commands[0]
    assert "--restart" not in commands[0]
    assert json.loads(models.read_text())["qwen"]["activated"] is True


def test_cannot_register_untrusted_family_or_injected_alias(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("bad;rm", family="qwen", model="iq2_xs", port=8095)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("qwen", family="evil", model="iq2_xs", port=8095)


def test_unsloth_flash_next_sizes_are_registrable(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    saved = strata_gateway.save_instance("qwen-iq4", family="unsloth", model="ud-iq4_xs", port=8097)
    assert saved["alias"] == "qwen-iq4"
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("qwen-q8", family="unsloth", model="q8_0", port=8098)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("qwen-mix", family="qwen", model="ud-iq4_xs", port=8098)


def test_uninstall_never_deletes_models(monkeypatch, tmp_path):
    # Stop-check uses upstream 'running' array, not the unrelated launch 'serving' key.
    binary = tmp_path / "home" / ".local" / "bin" / "strata"
    binary.parent.mkdir(parents=True)
    binary.write_text("stub")
    marker = tmp_path / "managed.json"
    marker.write_text("{}")
    monkeypatch.setattr(strata, "_binary", lambda: binary)
    monkeypatch.setattr(strata, "_marker", lambda: marker)
    monkeypatch.setattr(strata, "_home", lambda: tmp_path / "home")
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata, "_run", lambda args, timeout=30: subprocess.CompletedProcess(
        args, 0, stdout='{"schema_version":1,"ok":true,"running":[]}'))
    monkeypatch.setattr(strata, "runtime_status", lambda: {"installed": False})
    models = tmp_path / "models"
    models.mkdir()
    (models / "important.gguf").write_bytes(b"original")
    assert strata.uninstall()["installed"] is False
    assert (models / "important.gguf").read_bytes() == b"original"


def test_download_only_exit_zero_does_not_activate(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    strata_gateway._write({"qwen": {"family": "qwen", "model": "iq2_xs",
        "port": 8095, "context": "32k", "activated": False}})
    monkeypatch.setattr(strata_gateway, "_run", lambda args, **kw:
        subprocess.CompletedProcess(args, 0, stdout='{"ok":true,"serving":false}'))
    ok, error = strata_gateway.start_instance("qwen")
    assert not ok and error
    assert strata_gateway._read()["qwen"]["activated"] is False


@pytest.mark.parametrize("loaded,models,expected", [
    (True, [{"id": "strata"}], True), (False, [{"id": "strata"}], False),
    (None, [{"id": "strata"}], False), (True, [], False),
])
def test_readiness_requires_loaded_health_and_models(monkeypatch, loaded, models, expected):
    import httpx
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=(
        {"loaded": loaded} if request.url.path == "/health" else {"data": models})))
    monkeypatch.setattr(strata_gateway.httpx, "Client", lambda **kw:
        original_client(transport=transport, **kw))
    assert strata_gateway._api_ready(8095) is expected


def test_local_model_requires_all_shards_and_allowed_realpath(monkeypatch, tmp_path):
    from app.files import service as files
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setattr(files, "allowed_roots", lambda: [root])
    first = root / "model-00001-of-00002.gguf"
    first.write_bytes(b"GGUFmodel")
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway._validate_model_path(str(first))
    second = root / "model-00002-of-00002.gguf"
    second.write_bytes(b"GGUFmodel")
    assert strata_gateway._validate_model_path(str(first)) == str(first)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway._validate_model_path(str(second))
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"GGUFmodel")
    first.unlink()
    first.symlink_to(outside)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway._validate_model_path(str(first))


def test_settings_update_requires_stopped_model_and_resets_activation(monkeypatch, tmp_path):
    from app.models_mgmt import local_llm
    _managed(monkeypatch, tmp_path)
    monkeypatch.setattr(local_llm, "find", lambda alias: None)
    monkeypatch.setattr(local_llm, "port_taken_by_other_runtime", lambda *a: False)
    strata_gateway.save_instance("qwen", family="qwen", model="iq2_xs", port=8095)
    items = strata_gateway._read()
    items["qwen"]["activated"] = True
    strata_gateway._write(items)
    monkeypatch.setattr(strata_gateway, "_running", lambda: [{
        "profile": "launch-cd-qwen", "port": 8095, "state": "starting"}])
    with pytest.raises(strata_gateway.StrataGatewayError, match="停止"):
        strata_gateway.save_instance("qwen", family="qwen", model="iq3_s", port=8095, replace=True)
    monkeypatch.setattr(strata_gateway, "_running", lambda: [])
    changed = strata_gateway.save_instance("qwen", family="qwen", model="iq3_s", port=8095,
        context="64k", kv="q4_0", vision="off", parallel=2, drafting="mtp", replace=True)
    assert changed["ctx_size"] == 65536
    assert changed["n_parallel"] == 2
    assert changed["activated"] is False
    assert changed["model"] == "iq3_s"


def test_addon_target_can_use_activated_local_strata(monkeypatch, tmp_path):
    from app.models_mgmt import ai_gateway, local_llm, runtime_policy
    _managed(monkeypatch, tmp_path)
    model = tmp_path / "local.gguf"
    model.write_bytes(b"GGUF")
    strata_gateway._write({"qwen": {"family": "qwen", "model": "iq2_xs",
        "port": 8095, "context": "32k", "activated": True, "model_path": str(model), "vision": "off"}})
    monkeypatch.setattr(runtime_policy, "get_policy", lambda: runtime_policy.RuntimePolicy(selected_runtime="strata"))
    monkeypatch.setattr(local_llm, "_llama_instances", lambda: [])
    target = asyncio.run(ai_gateway.resolve_ai_target("text.generate"))
    assert target.model == "qwen" and target.gateway_managed
    ai_gateway.require_local_target(target)
    with pytest.raises(ai_gateway.AITargetUnavailable):
        asyncio.run(ai_gateway.resolve_ai_target("vision.analyze"))
    items = strata_gateway._read()
    items["qwen"]["activated"] = False
    strata_gateway._write(items)
    with pytest.raises(ai_gateway.AITargetUnavailable):
        asyncio.run(ai_gateway.resolve_ai_target("text.generate"))


def test_lifecycle_flags_change_while_running_and_keep_activation(monkeypatch, tmp_path):
    from app.models_mgmt import local_llm
    _managed(monkeypatch, tmp_path)
    monkeypatch.setattr(local_llm, "find", lambda alias: None)
    monkeypatch.setattr(local_llm, "port_taken_by_other_runtime", lambda *a: False)
    strata_gateway.save_instance("qwen", family="qwen", model="iq2_xs", port=8095, context="256k")
    items = strata_gateway._read()
    items["qwen"]["activated"] = True
    strata_gateway._write(items)
    monkeypatch.setattr(strata_gateway, "_running", lambda: [{
        "profile": "launch-cd-qwen", "port": 8095, "state": "ready"}])
    changed = strata_gateway.save_instance("qwen", family="qwen", model="iq2_xs", port=8095,
        context="256k", idle_exclude=True, auto_start=True, replace=True)
    assert changed["idle_exclude"] is True and changed["auto_start"] is True
    assert changed["activated"] is True and changed["ctx_size"] == 262144
    with pytest.raises(strata_gateway.StrataGatewayError, match="停止"):
        strata_gateway.save_instance("qwen", family="qwen", model="iq2_xs", port=8095,
            context="128k", idle_exclude=True, auto_start=True, replace=True)
