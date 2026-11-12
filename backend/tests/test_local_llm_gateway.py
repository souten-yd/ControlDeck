"""Regression coverage for the remaining managed llama.cpp runtime."""
import asyncio

from app.models_mgmt import gateway, llama, local_llm, strata_gateway


def test_local_llm_lists_only_managed_llama(monkeypatch):
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen-llama", "role": "llm", "port": 8080, "loaded": True,
         "model_path": "/m/a.gguf", "order": 1},
        {"alias": "embedding", "role": "embedding", "port": 8081, "loaded": False},
    ])
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [])
    assert local_llm.RUNTIMES == ("llama.cpp", "strata", "maya-s")
    listed = local_llm.llm_instances()
    assert [item["alias"] for item in listed] == ["qwen-llama"]
    assert local_llm.runtime_of("qwen-llama") == "llama.cpp"
    assert local_llm.endpoint_ports() == {8080, 8081}
    assert gateway.resolve_endpoint("qwen-llama") == ("qwen-llama", 8080)


def test_local_llm_ready_dispatches_to_llama(monkeypatch):
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen", "role": "llm", "port": 8080, "loaded": False},
    ])
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [])
    seen = []

    async def ready(alias, *, timeout_seconds=240):
        seen.append(alias)
        return True

    monkeypatch.setattr(llama, "ensure_ready", ready)
    assert asyncio.run(local_llm.ensure_ready("qwen")) is True
    assert seen == ["qwen"]
    assert asyncio.run(local_llm.ensure_ready("missing")) is False


def test_provider_selection_keeps_llama_and_generic_endpoints(monkeypatch):
    from app.models_mgmt import runtime_provider

    monkeypatch.setattr(llama, "endpoint_ports", lambda: {8080})
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [])
    assert runtime_provider.provider_for_base_url("http://127.0.0.1:8080/v1").kind == "llama.cpp"
    assert runtime_provider.provider_for_base_url("http://example.com/v1").kind == "openai-compatible"


def test_gateway_never_forces_greedy_sampling_for_llama(monkeypatch):
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen", "role": "llm", "port": 8080, "loaded": True},
    ])
    assert gateway._greedy_sampling_for({"alias": "qwen", "runtime": "llama.cpp"}, None) is False


def test_gateway_dispatches_strata_alias_without_rewriting_other_models(monkeypatch):
    from app.models_mgmt import strata
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "llama-model", "port": 8080, "role": "llm"},
    ])
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [
        {"alias": "strata-qwen", "port": 8095, "role": "llm",
         "runtime": "strata", "loaded": True},
    ])
    assert gateway.resolve_internal_target(gateway.base_url(), "strata-qwen") == (
        "http://127.0.0.1:8095/v1", "strata",
    )
    assert gateway.resolve_internal_target(gateway.base_url(), "llama-model") == (
        "http://127.0.0.1:8080/v1", "llama-model",
    )
    from app.models_mgmt import runtime_provider
    assert runtime_provider.provider_for_base_url("http://127.0.0.1:8095/v1").kind == "strata"


def test_strata_registration_remains_explicit_and_preserves_models(monkeypatch, tmp_path):
    from app.models_mgmt import strata

    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "_path", lambda: tmp_path / "instances.json")
    monkeypatch.setattr(strata_gateway, "_running", lambda: [])
    monkeypatch.setattr(llama, "list_instances", lambda: [])
    instance = strata_gateway.save_instance(
        "strata-test", family="qwen", model="iq2_xs", context="32k", port=8095
    )
    assert instance["loaded"] is False
    assert instance["activated"] is False
    assert len(strata_gateway.list_instances()) == 1
    # No launch, download, or destructive file changes.
    assert (tmp_path / "instances.json").is_file()
    assert strata_gateway.delete_instance("strata-test")["model_files_deleted"] is False
    assert strata_gateway.list_instances() == []


def test_auto_does_not_choose_unactivated_strata(monkeypatch):
    from app.models_mgmt import strata
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(llama, "list_instances", lambda: [])
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: [
        {"alias": "unactivated", "port": 8095, "role": "llm", "runtime": "strata",
         "activated": False, "loaded": False},
    ])
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as exc:
        gateway.resolve_instance(gateway.AUTO_MODEL)
    assert exc.value.status_code == 404
    assert gateway.resolve_endpoint("unactivated") == ("unactivated", 8095)
