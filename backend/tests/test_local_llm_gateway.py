"""The managed local gateway remains llama.cpp-only after retiring Lucebox."""
import asyncio

import pytest

from app.models_mgmt import gateway, llama, local_llm
from app.models_mgmt import providers


@pytest.fixture()
def registered_llama(monkeypatch):
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen-local", "role": "llm", "port": 8080,
         "loaded": False, "model_path": "/m/qwen.gguf", "order": 1},
    ])


def test_local_registry_only_has_llama(registered_llama):
    assert local_llm.RUNTIMES == ("llama.cpp",)
    assert local_llm.runtime_of("qwen-local") == "llama.cpp"
    assert local_llm.endpoint_ports() == {8080}
    assert local_llm.find("old-lucebox-alias") is None


def test_gateway_routes_managed_llama(registered_llama):
    assert gateway.resolve_endpoint("qwen-local") == ("qwen-local", 8080)
    assert gateway.resolve_endpoint("auto") == ("qwen-local", 8080)


def test_provider_does_not_advertise_retired_runtime():
    assert "lucebox" not in providers.GATEWAY_RUNTIMES
    assert "llama.cpp" in providers.GATEWAY_RUNTIMES


def test_legacy_policy_is_not_selected(tmp_path, monkeypatch):
    from app.models_mgmt import runtime_policy

    path = tmp_path / "model-runtime-policy.json"
    path.write_text(
        '{"selected_runtime":"lucebox","selected_backend":"rocm10",'
        '"idle_unload_minutes":55,"assistant_name":"既存アシスタント"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_policy, "_path", lambda: path)
    policy = runtime_policy.get_policy()
    assert policy.selected_runtime == "ollama"
    assert policy.selected_backend == ""
    assert policy.idle_unload_minutes == 55
    assert policy.assistant_name == "既存アシスタント"
    # Never rewrite/destroy historic user settings merely by reading them.
    assert '"lucebox"' in path.read_text(encoding="utf-8")


def test_legacy_pins_do_not_force_sampling():
    from app.models_mgmt.gateway import _greedy_sampling_for

    assert _greedy_sampling_for({"alias": "qwen-local"}, None) is False
