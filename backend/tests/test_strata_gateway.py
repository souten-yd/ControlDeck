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
    assert "vram-reserve-mib=3072" in commands[0]
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
    # Same weights, only the context changes: stays prepared (auto and on-demand start keep it).
    items = strata_gateway._read()
    items["qwen"]["activated"] = True
    strata_gateway._write(items)
    widened = strata_gateway.save_instance("qwen", family="qwen", model="iq3_s", port=8095,
        context="128k", kv="q4_0", vision="off", parallel=2, drafting="mtp", replace=True)
    assert widened["activated"] is True and widened["ctx_size"] == 131072


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


def test_engine_settings_reach_launch_as_validated_set_flags(monkeypatch):
    monkeypatch.setattr(strata_gateway, "DEFAULT_ENGINE_SETTINGS", {"STRATA_ROUTE_TAIL_SKIP": "0"})
    cfg = {"engine_settings": {"STRATA_HC_Q8": "0", "draft-conf": "0.35",
                               "STRATA_X; rm -rf /": "1", "STRATA_OK": "a b"}}
    # The default applies, overrides add to it, and anything shaped like an injection is dropped.
    assert strata_gateway.engine_settings(cfg) == {
        "STRATA_ROUTE_TAIL_SKIP": "0", "STRATA_HC_Q8": "0", "draft-conf": "0.35", "ple-io": "mmap"}
    assert strata_gateway.engine_settings({"engine_settings": {"STRATA_ROUTE_TAIL_SKIP": "off"}}) == {
        "STRATA_ROUTE_TAIL_SKIP": "off", "ple-io": "mmap"}
    # n-gram 表は mmap で読む（direct はこの NVMe で遅く、長い質問の読み込みが 1.9 倍違った。2026-11-20 実測）。
    # 登録で direct を指定すればそれを使い、DeepSeek の家族（別のエンジン）には付けない
    assert strata_gateway.engine_settings({"engine_settings": {"ple-io": "direct"}})["ple-io"] == "direct"
    assert "ple-io" not in strata_gateway.engine_settings({"family": "deepseek41"})


def test_free_form_chat_leaves_sampling_to_the_model():
    """The main chat sends no temperature, so each model's own (card / per-model) sampling applies."""
    from app.models_mgmt.runtime_provider import OpenAICompatibleRuntimeProvider, RuntimeChatRequest

    provider = OpenAICompatibleRuntimeProvider()
    free = provider._payload(RuntimeChatRequest(base_url="http://x/v1", model="m", messages=[], temperature=None), stream=False)
    assert "temperature" not in free
    fixed = provider._payload(RuntimeChatRequest(base_url="http://x/v1", model="m", messages=[], temperature=0.2), stream=False)
    assert fixed["temperature"] == 0.2


def test_thinking_settings_are_strata_shared_defaults(monkeypatch, tmp_path):
    from app.models_mgmt import local_llm
    _managed(monkeypatch, tmp_path)
    monkeypatch.setattr(local_llm, "find", lambda alias: None)
    monkeypatch.setattr(local_llm, "port_taken_by_other_runtime", lambda *a: False)
    row = strata_gateway.save_instance("qwen", family="qwen", model="iq2_xs", port=8095,
                                       reasoning_effort="xhigh", thinking_budget=4096)
    assert row["reasoning_effort"] == "xhigh" and row["thinking_budget"] == 4096
    # /settings names the deepest level "high"; nothing set clears the defaults.
    assert strata_gateway.thinking_defaults(strata_gateway._read()["qwen"]) == {
        "reasoning_effort": "high", "reasoning_budget_tokens": 4096}
    assert strata_gateway.thinking_defaults({"reasoning_effort": "", "thinking_budget": 0}) is None
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("qwen2", family="qwen", model="iq2_xs", port=8094, reasoning_effort="max")
    # Launch never passes them as engine arguments (the engine refuses the budget there).
    seen = []
    monkeypatch.setattr(strata_gateway, "_run", lambda args, timeout=0: (seen.append(args),
                        __import__("subprocess").CompletedProcess(args, 1, "", "stop here"))[1])
    strata_gateway._start_locked("qwen", explicit=True)
    joined = " ".join(seen[0])
    assert "--set reasoning-effort=off" in joined and "--set reasoning-budget-tokens=off" in joined
    # While running, a thinking change is applied live instead of refused.
    sent = []
    monkeypatch.setattr(strata_gateway, "_running", lambda: [{"profile": "launch-cd-qwen", "port": 8095, "state": "ready"}])
    monkeypatch.setattr(strata_gateway, "apply_thinking", lambda alias: sent.append(alias) or True)
    strata_gateway.save_instance("qwen", family="qwen", model="iq2_xs", port=8095,
                                 reasoning_effort="low", thinking_budget=0, replace=True)
    assert sent == ["qwen"]


def test_memory_settings_preserve_omitted_clear_auto_and_forward(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    args = dict(family="unsloth", model="ud-iq4_xs", context="256k", port=8096, parallel=6)
    result = strata_gateway.save_instance("memory-test", **args, kv_resident=32768, ram_keep_gib=8)
    assert result["kv_resident"] == 32768
    strata_gateway.save_instance("memory-test", **args, replace=True)
    cfg = strata_gateway._read()["memory-test"]
    assert cfg["ram_keep_gib"] == 8
    settings = strata_gateway.engine_settings(cfg)
    assert settings["kv-resident"] == "32768"
    assert settings["STRATA_RAM_KEEP_GIB"] == "8"
    strata_gateway.save_instance("memory-test", **args, replace=True, kv_resident=None, ram_keep_gib=None)
    settings = strata_gateway.engine_settings(strata_gateway._read()["memory-test"])
    assert "kv-resident" not in settings
    assert "STRATA_RAM_KEEP_GIB" not in settings


@pytest.mark.parametrize("change", [{"kv_resident": 4097}, {"kv_resident": 1024}, {"ram_keep_gib": -1}, {"ram_keep_gib": float("nan")}, {"ram_keep_gib": 257}])
def test_invalid_memory_settings_are_rejected(monkeypatch, tmp_path, change):
    _managed(monkeypatch, tmp_path)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("memory-test", family="unsloth", model="ud-iq4_xs", port=8096, **change)


def test_legacy_memory_values_are_not_launch_changes_when_equal(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    args = dict(family="unsloth", model="ud-iq4_xs", context="256k", port=8096, parallel=6)
    strata_gateway.save_instance("legacy-memory", **args)
    items = strata_gateway._read()
    items["legacy-memory"]["engine_settings"] = {"kv-resident": "32768", "STRATA_RAM_KEEP_GIB": "8"}
    strata_gateway._write(items)
    monkeypatch.setattr(strata_gateway, "_running", lambda: [{"profile": "launch-cd-legacy-memory", "port": 8096, "state": "ready"}])
    # UI hydration of a legacy override must not block live lifecycle-only edits.
    result = strata_gateway.save_instance("legacy-memory", **args, replace=True, idle_exclude=True, kv_resident=32768, ram_keep_gib=8)
    assert result["idle_exclude"]
    with pytest.raises(strata_gateway.StrataGatewayError, match="停止"):
        strata_gateway.save_instance("legacy-memory", **args, replace=True, kv_resident=None, ram_keep_gib=None)
    monkeypatch.setattr(strata_gateway, "_running", lambda: [])
    strata_gateway.save_instance("legacy-memory", **args, replace=True, kv_resident=None, ram_keep_gib=None)
    assert "kv-resident" not in strata_gateway.engine_settings(strata_gateway._read()["legacy-memory"])


def _gguf(path, file_type: int, quantized_by: str | None):
    """最小の GGUF（メタデータだけ）。"""
    import struct

    def text(s: str) -> bytes:
        b = s.encode()
        return struct.pack("<Q", len(b)) + b

    kv = [(b"general.architecture", 8, text("qwen4exp")), (b"general.file_type", 4, struct.pack("<I", file_type))]
    if quantized_by is not None:
        kv.append((b"general.quantized_by", 8, text(quantized_by)))
    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kv))
    for key, kind, value in kv:
        body += struct.pack("<Q", len(key)) + key + struct.pack("<I", kind) + value
    path.write_bytes(body)
    return path


def test_strata_refuses_quantizations_it_cannot_run_at_registration(tmp_path):
    """Strata は Unsloth の UD-*・GSQ-RCO・Q8_0 だけを動かす。ほかの K 量子化（Q4_K_S など）は登録の時点で断り、llama.cpp を案内する。
    根拠: 2026-11-21 本番で Uncensored の Q4_K_S を Strata に登録し、ロードが「a GGUF Strata cannot run」で 4 回失敗した。"""
    import pytest

    from app.models_mgmt import strata_gateway

    q4ks = _gguf(tmp_path / "Uncensored-Q4_K_S.gguf", 14, None)
    with pytest.raises(strata_gateway.StrataGatewayError, match="Q4_K_S.*llama.cpp"):
        strata_gateway.check_runnable("unsloth", q4ks)
    strata_gateway.check_runnable("unsloth", _gguf(tmp_path / "ud.gguf", 30, "Unsloth"))        # UD-IQ4_XS
    strata_gateway.check_runnable("unsloth", _gguf(tmp_path / "q8.gguf", 7, None))              # Q8_0
    strata_gateway.check_runnable("qwen", q4ks)                                                  # 他の系列は Strata に任せる
    (tmp_path / "broken.gguf").write_bytes(b"GGUF")
    strata_gateway.check_runnable("unsloth", tmp_path / "broken.gguf")                           # 読めないものも任せる
    assert strata_gateway.gguf_header(q4ks)["general.file_type"] == 14


def test_inkling_2_mini_is_registrable_and_launched_without_qwen_only_arguments(monkeypatch, tmp_path):
    """Inkling 2 Mini（Strata 0.2.19 の strata-inkling）を登録・起動できる。Qwen 用の引数と思考の設定は渡さない。
    根拠: 2026-11-23 利用者「Strata や Radiance で inkling がサポートされていれば候補として追加できるように」。
    strata-inkling は --set の値をそのままエンジンの引数へ足す（Strata の terminal/serve/inkling_kind.py）ので、
    知らない引数（--ple-io・--kv-resident・思考）は起動を壊しうる。思考は持たない（inkling_family.HAS_THINKING=False）。
    系列名と量子化は 0.2.20 の `strata launch --family inkling-2-mini --model sq-20 --plan` で確かめた。"""
    from app.models_mgmt import local_llm
    _managed(monkeypatch, tmp_path)
    monkeypatch.setattr(local_llm, "find", lambda alias: None)
    monkeypatch.setattr(local_llm, "port_taken_by_other_runtime", lambda *a: False)
    for port, size in enumerate(("sq-20", "sq-15", "sq-10", "ud-q4_k_xl"), start=8110):
        assert strata_gateway.save_instance(f"inkling-{size.replace('_', '-')}", family="inkling-2-mini",
                                            model=size, port=port)["model"] == size
    with pytest.raises(strata_gateway.StrataGatewayError):          # Qwen の量子化名は通さない
        strata_gateway.save_instance("ink-bad", family="inkling-2-mini", model="iq3_s", port=8119)
    with pytest.raises(strata_gateway.StrataGatewayError, match="思考"):
        strata_gateway.save_instance("ink-think", family="inkling-2-mini", model="sq-20", port=8119, reasoning_effort="low")
    with pytest.raises(strata_gateway.StrataGatewayError, match="Qwen系"):
        strata_gateway.save_instance("ink-kv", family="inkling-2-mini", model="sq-20", port=8119, kv_resident=32768)
    with pytest.raises(strata_gateway.StrataGatewayError, match="DSpark"):
        strata_gateway.save_instance("ink-mtp", family="inkling-2-mini", model="sq-20", port=8119, drafting="mtp")

    settings = strata_gateway.launch_engine_settings({"family": "inkling-2-mini", "kv_resident": 32768})
    assert "ple-io" not in settings and "kv-resident" not in settings
    assert "vram-reserve-mib" in settings                     # strata-inkling が受け付ける（ep_dual_gpu.h）

    seen = []
    monkeypatch.setattr(strata_gateway, "_run", lambda args, timeout=0: (seen.append(args),
                        subprocess.CompletedProcess(args, 1, "", "stop here"))[1])
    strata_gateway._start_locked("inkling-sq-20", explicit=True)
    joined = " ".join(seen[0])
    assert seen[0][:5] == ["launch", "--family", "inkling-2-mini", "--model", "sq-20"]
    assert "reasoning-effort" not in joined and "reasoning-budget-tokens" not in joined and "ple-io" not in joined
    assert strata_gateway.apply_thinking("inkling-sq-20") is False   # 思考の既定を送らない
