import asyncio


def test_runtime_policy_roundtrip(tmp_path, monkeypatch):
    from app.models_mgmt import runtime_policy

    monkeypatch.setattr(runtime_policy, "_path", lambda: tmp_path / "policy.json")
    policy = runtime_policy.RuntimePolicy(
        selected_runtime="llama.cpp",
        selected_backend="vulkan",
        assistant_name="Local AI",
        chat={"reasoning": "off", "timeout_seconds": 60},
    )
    runtime_policy.save_policy(policy)

    loaded = runtime_policy.get_policy()
    assert loaded == policy
    assert loaded.chat.timeout_seconds == 60


def test_apply_exclusive_llama_unloads_ollama(monkeypatch):
    from app.models_mgmt import llama, ollama, runtime_policy

    monkeypatch.setattr(llama, "installed_backends", lambda: ["vulkan"])
    monkeypatch.setattr(llama, "get_config", lambda: {"backend": "vulkan"})

    unloaded = []

    async def running_models():
        return [{"name": "qwen:latest"}, {"model": "gemma:latest"}]

    async def unload(name):
        unloaded.append(name)

    monkeypatch.setattr(ollama, "running_models", running_models)
    monkeypatch.setattr(ollama, "unload", unload)
    policy = runtime_policy.RuntimePolicy(
        selected_runtime="llama.cpp", selected_backend="vulkan", coexistence="exclusive"
    )

    asyncio.run(runtime_policy.apply_selection(policy))
    assert unloaded == ["qwen:latest", "gemma:latest"]


def test_apply_exclusive_ollama_stops_llama(monkeypatch):
    from app.models_mgmt import llama, runtime_policy

    async def healthy():
        return {"ok": True}

    stopped = []
    monkeypatch.setattr(llama, "health", healthy)
    monkeypatch.setattr(llama, "stop_instance", lambda: stopped.append(True))

    asyncio.run(runtime_policy.apply_selection(runtime_policy.RuntimePolicy(selected_runtime="ollama")))
    assert stopped == [True]


def test_runtime_policy_rejects_invalid_limits():
    from pydantic import ValidationError
    from app.models_mgmt.runtime_policy import RuntimePolicy

    try:
        RuntimePolicy(deep_research={"max_report_tokens": 262145})
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid max_report_tokens was accepted")


def test_model_output_tokens_uses_ollama_model_config(monkeypatch):
    from app.models_mgmt import llama, ollama, runtime_policy

    monkeypatch.setattr(ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(ollama, "get_model_config", lambda model: {"num_predict": 131072})
    monkeypatch.setattr(llama, "list_instances", lambda: [])
    assert runtime_policy.model_output_tokens("http://127.0.0.1:11434/v1", "qwen") == 131072


def test_model_output_tokens_uses_llama_instance_and_caps_unlimited(monkeypatch):
    from app.models_mgmt import llama, ollama, runtime_policy

    monkeypatch.setattr(ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(llama, "list_instances", lambda: [{"port": 8080, "n_predict": -1}])
    assert runtime_policy.model_output_tokens("http://127.0.0.1:8080/v1", "m") == 262144
    assert runtime_policy.model_output_tokens("https://external.example/v1", "m") == 8192
