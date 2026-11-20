"""Radiance runtime: registration, launch command, lifecycle and routing."""
from __future__ import annotations

import asyncio

import pytest

from app.models_mgmt import radiance


@pytest.fixture
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(radiance, "root", lambda: tmp_path / "radiance")
    monkeypatch.setattr(radiance, "installed", lambda: True)
    monkeypatch.setattr(radiance, "_running", lambda alias: False)
    model = tmp_path / "qwen3.8-27b-iq5r-v4.rad"
    model.write_bytes(b"rad")
    from app.files import service as files
    monkeypatch.setattr(files, "resolve", lambda value: __import__("pathlib").Path(value))
    from app.models_mgmt import local_llm
    monkeypatch.setattr(local_llm, "find", lambda alias: None)
    monkeypatch.setattr(local_llm, "port_taken_by_other_runtime", lambda *a: "")
    return model


def test_registration_validates_and_lists(managed):
    row = radiance.save_instance("qwen3.8-27b-radiance", model_path=str(managed), port=8100)
    assert row["runtime"] == "radiance" and row["base_url"] == "http://127.0.0.1:8100/v1"
    assert row["kv_cache"] == "bf16" and row["ctx_size"] == 131072 and row["on_demand_start"] is True
    with pytest.raises(radiance.RadianceError):
        radiance.save_instance("other", model_path=str(managed.with_suffix(".gguf")), port=8101)
    with pytest.raises(radiance.RadianceError):
        radiance.save_instance("dup", model_path=str(managed), port=8100)  # port taken inside Radiance
    with pytest.raises(radiance.RadianceError):
        radiance.save_instance("bad", model_path=str(managed), port=8102, kv_cache="int4")
    assert [item["alias"] for item in radiance.list_instances()] == ["qwen3.8-27b-radiance"]


def test_lifecycle_flags_change_while_running_but_launch_settings_do_not(managed, monkeypatch):
    radiance.save_instance("q", model_path=str(managed), port=8100)
    monkeypatch.setattr(radiance, "_running", lambda alias: True)
    changed = radiance.save_instance("q", model_path=str(managed), port=8100, idle_exclude=True, replace=True)
    assert changed["idle_exclude"] is True
    with pytest.raises(radiance.RadianceError, match="停止"):
        radiance.save_instance("q", model_path=str(managed), port=8100, context=65536, replace=True)


def test_launch_args_are_fixed_and_loopback_only(managed):
    radiance.save_instance("q", model_path=str(managed), port=8100, kv_cache="fp8",
                           max_num_seqs=2, speculative_tokens=7)
    cfg = radiance._read()["q"]
    args = radiance.launch_args("q", cfg)
    assert args[1:3] == ["run", "--rm"]
    joined = " ".join(args)
    # Rootless podman, host network, the model folder and the settings file read-only.
    assert f"{radiance.IMAGE}:{radiance.PINNED_TAG}" in args and "--network host" in joined
    assert f"-v {managed.parent}:/models:ro" in joined and args[-2:] == ["--config", "/config/radiance.yaml"]
    text = radiance.config_yaml("q", cfg)
    assert "  host: 127.0.0.1\n  port: 8100\n" in text and f"    model: /models/{managed.name}\n" in text
    assert "    kv-cache-dtype: fp8\n" in text and "    max-num-seqs: 2\n" in text and "    tp: 1\n" in text
    assert "    num-speculative-tokens: 7\n" in text
    # 会話の KV を VRAM から追い出すときホストの RAM へ残す（既定は無しで、外れた会話を最初から読み直していた。2026-11-20）
    assert f"    prefix-cache-mib: {radiance.PREFIX_CACHE_MIB}\n" in text
    radiance.save_instance("auto-depth", model_path=str(managed), port=8101)
    assert "num-speculative-tokens" not in radiance.config_yaml("auto-depth", radiance._read()["auto-depth"])


def test_starting_radiance_frees_the_other_large_models(monkeypatch):
    from app.models_mgmt import large_model_lifecycle, llama, maya_s, strata, strata_gateway

    stopped = []
    monkeypatch.setattr(maya_s, "installed", lambda: True)
    monkeypatch.setattr(maya_s, "is_running", lambda: True)
    monkeypatch.setattr(maya_s, "get_settings", lambda: {"idle_exclude": False})
    monkeypatch.setattr(maya_s, "stop_instance", lambda: (stopped.append("maya-s"), (True, ""))[1])
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [{"alias": "flash", "loaded": True}])
    monkeypatch.setattr(strata_gateway, "stop_instance", lambda a: (stopped.append(a), (True, ""))[1])
    monkeypatch.setattr(radiance, "installed", lambda: True)
    monkeypatch.setattr(radiance, "list_instances", lambda: [])

    async def release_llms():
        stopped.append("llama")
    monkeypatch.setattr(llama, "release_loaded_llms", release_llms)
    asyncio.run(large_model_lifecycle.release_for(large_model_lifecycle.RADIANCE))
    assert stopped == ["maya-s", "flash", "llama"]

    # ...and Radiance is freed when Maya-S starts.
    stopped.clear()
    monkeypatch.setattr(maya_s, "is_running", lambda: False)
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [])
    monkeypatch.setattr(radiance, "list_instances", lambda: [{"alias": "q", "loaded": True}])
    monkeypatch.setattr(radiance, "stop_instance", lambda a: (stopped.append(a), (True, ""))[1])
    asyncio.run(large_model_lifecycle.release_for(large_model_lifecycle.MAYA))
    assert stopped == ["q", "llama"]


def test_gateway_auto_uses_radiance_when_selected(monkeypatch):
    from app.models_mgmt import gateway, local_llm

    rows = [
        {"alias": "Qwen3.8-27B", "role": "llm", "runtime": "llama.cpp", "port": 8097, "loaded": False},
        {"alias": "qwen3.8-27b-radiance", "role": "llm", "runtime": "radiance", "port": 8100, "loaded": False},
    ]
    monkeypatch.setattr(local_llm, "list_instances", lambda **kw: [dict(r) for r in rows])
    monkeypatch.setattr(gateway, "_selected_runtime", lambda: "radiance")
    assert gateway.resolve_endpoint(gateway.AUTO_MODEL) == ("qwen3.8-27b-radiance", 8100)


def test_requests_to_a_radiance_port_use_the_radiance_provider(monkeypatch):
    from app.models_mgmt import runtime_provider, strata, maya_s

    monkeypatch.setattr(strata, "installed", lambda: False)
    monkeypatch.setattr(maya_s, "list_instances", lambda: [])
    monkeypatch.setattr(radiance, "installed", lambda: True)
    monkeypatch.setattr(radiance, "list_instances", lambda: [{"alias": "q", "port": 8100}])
    assert runtime_provider.provider_for_base_url("http://127.0.0.1:8100/v1").kind == "radiance"


def test_runtime_policy_and_feature_know_radiance():
    from app.features import gpu_runtime, registry
    from app.models_mgmt.runtime_policy import RuntimePolicy

    assert RuntimePolicy(selected_runtime="radiance").selected_runtime == "radiance"
    assert registry.FEATURES["radiance"]["kind"] == "gpu-runtime"
    assert gpu_runtime.is_gpu_runtime("radiance")
    assert radiance._TAG.fullmatch(radiance.PINNED_TAG)


def test_thinking_defaults_reach_the_server(managed):
    radiance.save_instance("q", model_path=str(managed), port=8100)
    text = radiance.config_yaml("q", radiance._read()["q"])
    # The template's own depth, no cap; high/max (OpenCode, GLM-style) mapped to the template's xhigh.
    assert "reasoning-effort:" not in text.replace("reasoning-effort-map", "") and "thinking-token-budget" not in text
    assert '    reasoning-effort-map: "high=xhigh,max=xhigh"\n' in text
    radiance.save_instance("r", model_path=str(managed), port=8101, reasoning_effort="medium", thinking_budget=8192)
    text = radiance.config_yaml("r", radiance._read()["r"])
    assert "    reasoning-effort: medium\n" in text and "    thinking-token-budget: 8192\n" in text
    with pytest.raises(radiance.RadianceError):
        radiance.save_instance("s", model_path=str(managed), port=8102, reasoning_effort="high")


def test_starting_strata_frees_a_resident_llama_chat_model(monkeypatch):
    """Strata does not fit beside Qwen3.8-27B on the R9700: its start must free it too."""
    from app.models_mgmt import large_model_lifecycle, llama, maya_s, strata, strata_gateway

    stopped = []
    monkeypatch.setattr(maya_s, "installed", lambda: False)
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [])
    monkeypatch.setattr(radiance, "installed", lambda: False)

    async def release_llms():
        stopped.append("llama")
    monkeypatch.setattr(llama, "release_loaded_llms", release_llms)
    asyncio.run(large_model_lifecycle.release_for(large_model_lifecycle.STRATA))
    assert stopped == ["llama"]


def test_long_context_uses_yarn_and_memory_options(managed):
    """Past the training context YaRN is set; i8 / memory saver / RAM offload reach the file."""
    radiance.save_instance("long", model_path=str(managed), port=8100, context=327680,
                           kv_cache="i8", max_num_seqs=6, memory_saver=True)
    text = radiance.config_yaml("long", radiance._read()["long"])
    assert '    rope-scaling: "yarn:1.25:262144"\n' in text and "    kv-cache-dtype: i8\n" in text
    assert "    state-cache-dtype: f16\n" in text and "    vision-tower-placement: ram\n" in text
    assert "placement: layer_offload" not in text and "    max-num-seqs: 6\n" in text
    radiance.save_instance("far", model_path=str(managed), port=8101, context=524288,
                           kv_cache="i8", ram_offload=True)
    text = radiance.config_yaml("far", radiance._read()["far"])
    assert '"yarn:2:262144"' in text and "    placement: layer_offload\n" in text
    assert "    flex: {kv: {min-tokens: 524288}}\n" in text
    # Within the training context nothing is scaled, and 1.x's cache formats are gone.
    radiance.save_instance("short", model_path=str(managed), port=8102)
    assert "rope-scaling" not in radiance.config_yaml("short", radiance._read()["short"])
    with pytest.raises(radiance.RadianceError):
        radiance.save_instance("old", model_path=str(managed), port=8103, kv_cache="k6_v4")
