import asyncio

import pytest

from app.models_mgmt import runtime_lifecycle
from app.workflows import nodes


def test_managed_ollama_model_is_loaded_before_generation(monkeypatch):
    calls: list[tuple] = []

    async def llama_ready(base_url: str, *, timeout_seconds: int = 240) -> bool:
        calls.append(("llama-check", base_url, timeout_seconds))
        return True

    async def provider_load(provider: str, model: str, keep_alive=None):
        calls.append(("provider-load", provider, model, keep_alive))
        return {"loaded": True}

    monkeypatch.setattr(runtime_lifecycle.llama, "ensure_ready_by_base_url", llama_ready)
    monkeypatch.setattr(runtime_lifecycle.ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(runtime_lifecycle.provider_adapters, "load_model", provider_load)

    result = asyncio.run(runtime_lifecycle.ensure_chat_model_ready(
        "http://localhost:11434/v1", "local-model", keep_alive="45m", timeout_seconds=90,
    ))

    assert result == {"managed": True, "runtime": "ollama", "ready": True}
    assert ("provider-load", "ollama", "local-model", "45m") in calls


def test_external_endpoint_is_not_started_or_loaded(monkeypatch):
    loaded = False

    async def llama_ready(base_url: str, *, timeout_seconds: int = 240) -> bool:
        return True

    async def provider_load(provider: str, model: str, keep_alive=None):
        nonlocal loaded
        loaded = True

    monkeypatch.setattr(runtime_lifecycle.llama, "ensure_ready_by_base_url", llama_ready)
    monkeypatch.setattr(runtime_lifecycle.ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(runtime_lifecycle.provider_adapters, "load_model", provider_load)

    result = asyncio.run(runtime_lifecycle.ensure_chat_model_ready("https://llm.example/v1", "remote"))
    assert result["managed"] is False
    assert loaded is False


def test_llm_node_reports_managed_runtime_start_failure(monkeypatch):
    async def fail(*args, **kwargs):
        raise runtime_lifecycle.RuntimeStartupError("モデルロード失敗")

    monkeypatch.setattr(runtime_lifecycle, "ensure_chat_model_ready", fail)
    monkeypatch.setattr("app.models_mgmt.runtime_policy.ensure_gpu_profile", lambda **kwargs: None)

    with pytest.raises(nodes.NodeError, match="LLM準備失敗: モデルロード失敗"):
        asyncio.run(nodes.node_llm({"model": "local", "prompt": "hello"}, {}))
