import asyncio


def test_runtime_policy_roundtrip(tmp_path, monkeypatch):
    from app.models_mgmt import runtime_policy

    monkeypatch.setattr(runtime_policy, "_path", lambda: tmp_path / "policy.json")
    policy = runtime_policy.RuntimePolicy(
        selected_runtime="llama.cpp",
        selected_backend="vulkan",
        assistant_name="Local AI",
        chat={"max_output_tokens": 512, "reasoning": "off", "timeout_seconds": 60},
    )
    runtime_policy.save_policy(policy)

    loaded = runtime_policy.get_policy()
    assert loaded == policy
    assert loaded.chat.max_output_tokens == 512


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
        RuntimePolicy(chat={"max_output_tokens": 0})
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid max_output_tokens was accepted")
