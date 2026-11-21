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


def test_default_model_ref_is_dropped_from_policy(tmp_path, monkeypatch):
    """RuntimePolicy から未使用の default_model_ref を消し、古い設定ファイルも読める。

    目的: 既定モデルの決め方は selected_runtime + preferred_models +
    gateway.resolve_instance のフォールバックに置き換わったので、
    default_model_ref は書けても効かない状態にする。
    根拠: HANDOVER-model-management-v2.md「既知のバグ」の 1 件
    （default_model_ref は定義だけで参照ゼロ）。2026-11-22 に backend 全体を
    grep して参照が無いことを確認し削除した。
    """
    import json

    from app.models_mgmt import runtime_policy

    monkeypatch.setattr(runtime_policy, "_path", lambda: tmp_path / "policy.json")
    policy = runtime_policy.RuntimePolicy(selected_runtime="llama.cpp")
    assert "default_model_ref" not in policy.model_dump()
    # 旧 model-runtime-policy.json にキーが残っていても読み替え（pydantic が無視）できる
    (tmp_path / "policy.json").write_text(json.dumps(
        {**policy.model_dump(), "default_model_ref": "llama.cpp:qwen3.6-27b"}), encoding="utf-8")
    loaded = runtime_policy.get_policy()
    assert loaded == policy


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


def test_preferred_models_are_per_runtime_and_survive_restart(tmp_path, monkeypatch):
    from app.models_mgmt import runtime_policy as rp
    monkeypatch.setattr(rp, '_path', lambda: tmp_path / 'policy.json')
    rp.save_policy(rp.RuntimePolicy(preferred_models={'strata': 'b', 'llama.cpp': 'a'}))
    items = [{'alias': 'a'}, {'alias': 'b'}]
    assert rp.prefer_models(items, 'strata')[0]['alias'] == 'b'
    assert rp.prefer_models(items, 'llama.cpp')[0]['alias'] == 'a'
    assert rp.prefer_models([{'alias': 'c'}], 'strata') == [{'alias': 'c'}]


def test_preference_save_does_not_start_stop_or_change_gpu(admin_client, tmp_path, monkeypatch):
    from app.models_mgmt import runtime_policy as rp, amd_gpu
    monkeypatch.setattr(rp, '_path', lambda: tmp_path / 'policy.json')
    rp.save_policy(rp.RuntimePolicy(preferred_models={'radiance': 'old'}))
    async def choices():
        return [{'runtime': 'strata', 'alias': 'second', 'ready': True},
                {'runtime': 'strata', 'alias': 'unprepared', 'ready': False}]
    async def env():
        return {'policy': rp.get_policy().model_dump()}
    def forbidden(*a, **kw):
        raise AssertionError('Preference save must not change live models/GPU')
    monkeypatch.setattr(rp, 'startup_models', choices)
    monkeypatch.setattr(rp, 'environment', env)
    monkeypatch.setattr(rp, 'apply_selection', forbidden)
    monkeypatch.setattr(amd_gpu, 'apply_profile', forbidden)
    headers = {'X-Requested-With': 'ControlDeck'}
    result = admin_client.put('/api/v1/models/runtime-policy',
        json={'preferred_models': {'strata': 'second'}}, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()['policy']['preferred_models'] == {'strata': 'second', 'radiance': 'old'}
    for runtime, alias in [('llama.cpp', 'second'), ('strata', 'missing'), ('strata', 'unprepared')]:
        invalid = admin_client.put('/api/v1/models/runtime-policy',
            json={'preferred_models': {runtime: alias}}, headers=headers)
        assert invalid.status_code == 422
    assert rp.get_policy().preferred_models['strata'] == 'second'
    assert admin_client.put('/api/v1/models/runtime-policy',
        json={'preferred_models': {'strata': ''}}, headers=headers).status_code == 200
