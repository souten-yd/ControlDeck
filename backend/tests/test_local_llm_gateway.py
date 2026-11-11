"""Regression coverage for the remaining managed llama.cpp runtime."""
import asyncio

from app.models_mgmt import gateway, llama, local_llm


def test_local_llm_lists_only_managed_llama(monkeypatch):
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen-llama", "role": "llm", "port": 8080, "loaded": True,
         "model_path": "/m/a.gguf", "order": 1},
        {"alias": "embedding", "role": "embedding", "port": 8081, "loaded": False},
    ])
    assert local_llm.RUNTIMES == ("llama.cpp",)
    listed = local_llm.llm_instances()
    assert [item["alias"] for item in listed] == ["qwen-llama"]
    assert local_llm.runtime_of("qwen-llama") == "llama.cpp"
    assert local_llm.endpoint_ports() == {8080, 8081}
    assert gateway.resolve_endpoint("qwen-llama") == ("qwen-llama", 8080)


def test_local_llm_ready_dispatches_to_llama(monkeypatch):
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen", "role": "llm", "port": 8080, "loaded": False},
    ])
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
    assert runtime_provider.provider_for_base_url("http://127.0.0.1:8080/v1").kind == "llama.cpp"
    assert runtime_provider.provider_for_base_url("http://example.com/v1").kind == "openai-compatible"


def test_gateway_never_forces_greedy_sampling_for_llama(monkeypatch):
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen", "role": "llm", "port": 8080, "loaded": True},
    ])
    assert gateway._greedy_sampling_for({"alias": "qwen", "runtime": "llama.cpp"}, None) is False
