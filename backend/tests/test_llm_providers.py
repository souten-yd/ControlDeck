import asyncio
import pytest
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _ModelsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/models":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


def test_provider_catalog_normalizes_available_and_managed_unavailable(monkeypatch):
    import asyncio
    from app.models_mgmt import providers

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def candidates():
        return [
            {"id": "test", "provider": "openai-compatible", "name": "Test",
             "base_url": f"http://127.0.0.1:{server.server_port}/v1", "managed": False,
             "installed": None, "experimental": False},
            {"id": "ollama", "provider": "ollama", "name": "Ollama",
             "base_url": "http://127.0.0.1:1/v1", "managed": True,
             "installed": None, "experimental": False},
        ]

    monkeypatch.setattr(providers, "_candidates", candidates)
    try:
        result = asyncio.run(providers.list_providers())
    finally:
        server.shutdown()
    assert result[0]["id"] == "ollama" and result[0]["available"] is False
    test = next(item for item in result if item["id"] == "test")
    assert test["available"] is True and test["models"] == ["model-a", "model-b"]


def test_detected_provider_ids_include_endpoint_but_managed_ids_are_stable():
    from app.models_mgmt.providers import _provider_id

    assert _provider_id("ollama", "http://host-a:11434/v1", managed=True) == "ollama"
    assert _provider_id("ollama", "http://127.0.0.1:11434/v1", managed=False) == "ollama-127.0.0.1-11434"


def test_provider_api_and_workflow_compatibility(admin_client, monkeypatch):
    from app.models_mgmt import providers

    expected = [{
        "id": "llama.cpp", "provider": "llama.cpp", "name": "llama.cpp",
        "base_url": "http://127.0.0.1:8080/v1", "managed": True,
        "installed": True, "experimental": True, "available": True, "models": ["local"],
        "capabilities": ["list", "load", "unload", "configure"],
    }]

    async def fake_list(**kwargs):
        return expected

    monkeypatch.setattr(providers, "list_providers", fake_list)
    assert admin_client.get("/api/v1/models/providers").json() == expected
    workflow = admin_client.get("/api/v1/workflows/llm-endpoints")
    assert workflow.status_code == 200
    assert workflow.json()[0]["base_url"] == "http://127.0.0.1:8080/v1"
    assert workflow.json()[0]["models"] == ["local"]


def test_ollama_adapter_normalizes_models_and_lifecycle(monkeypatch):
    import asyncio
    from app.models_mgmt import provider_adapters

    provider = {
        "id": "ollama", "provider": "ollama", "name": "Ollama", "managed": True,
        "available": True, "models": ["qwen"],
        "capabilities": ["list", "load", "unload", "delete", "pull", "configure"],
    }

    async def catalog(**kwargs):
        return [provider]

    async def models():
        return [{"name": "qwen", "size": 123, "modified_at": "now", "loaded": True,
                 "family": "qwen", "parameter_size": "7B", "quantization": "Q4", "vram": 45}]

    async def no_running_models():
        return []

    calls = []
    monkeypatch.setattr(provider_adapters.providers, "list_providers", catalog)
    monkeypatch.setattr(provider_adapters.ollama, "list_models", models)
    monkeypatch.setattr(provider_adapters.ollama, "running_models", no_running_models)
    monkeypatch.setattr(provider_adapters.ollama, "load", lambda *args: _async_result(calls, ("load", args), {"loaded": True}))
    monkeypatch.setattr(provider_adapters.ollama, "unload", lambda *args: _async_result(calls, ("unload", args), {"loaded": False}))
    monkeypatch.setattr(provider_adapters.ollama, "delete", lambda *args: _async_result(calls, ("delete", args), None))
    monkeypatch.setattr(provider_adapters.ollama, "get_model_config", lambda model: {"num_ctx": 8192})
    monkeypatch.setattr(provider_adapters.ollama, "set_model_config", lambda model, patch: {**patch})

    listed = asyncio.run(provider_adapters.list_models("ollama"))
    assert listed[0]["id"] == "qwen" and listed[0]["size_bytes"] == 123 and listed[0]["loaded"] is True
    assert asyncio.run(provider_adapters.load_model("ollama", "qwen", "1h"))["loaded"] is True
    assert asyncio.run(provider_adapters.unload_model("ollama", "qwen"))["loaded"] is False
    asyncio.run(provider_adapters.delete_model("ollama", "qwen"))
    assert asyncio.run(provider_adapters.get_model_config("ollama", "qwen")) == {"num_ctx": 8192}
    assert asyncio.run(provider_adapters.configure_model("ollama", "qwen", {"temperature": 0.2})) == {
        "temperature": 0.2,
    }
    import pytest
    with pytest.raises(provider_adapters.InvalidConfiguration):
        asyncio.run(provider_adapters.configure_model("ollama", "qwen", {"num_ctx": "large"}))
    with pytest.raises(provider_adapters.ProviderNotFound):
        asyncio.run(provider_adapters.get_model_config("ollama", "missing"))
    assert [call[0] for call in calls] == ["load", "unload", "delete"]


def test_llama_adapter_lists_and_controls_each_catalog_instance(monkeypatch, tmp_path):
    import asyncio
    from app.models_mgmt import provider_adapters

    provider = {
        "id": "llama.cpp", "provider": "llama.cpp", "name": "llama.cpp", "managed": True,
        "available": True, "models": ["a"],
        "capabilities": ["list", "load", "unload", "delete", "configure", "health", "start", "stop"],
    }

    async def catalog(**kwargs):
        return [provider]

    model_a = tmp_path / "a.gguf"
    model_b = tmp_path / "b.gguf"
    model_a.write_bytes(b"a")
    model_b.write_bytes(b"bb")
    instances = [
        {"alias": "a", "model_path": str(model_a), "mmproj_path": str(tmp_path / "mmproj.gguf"), "port": 8100, "base_url": "http://127.0.0.1:8100/v1", "unit": "a.service", "runtime_status": "RUNNING"},
        {"alias": "b", "model_path": str(model_b), "port": 8101, "base_url": "http://127.0.0.1:8101/v1", "unit": "b.service", "runtime_status": "STOPPED"},
    ]
    calls = []

    async def health(alias=None):
        return {"ok": alias == "a"}

    async def no_running_models():
        return []

    monkeypatch.setattr(provider_adapters.providers, "list_providers", catalog)
    monkeypatch.setattr(provider_adapters.ollama, "running_models", no_running_models)
    monkeypatch.setattr(provider_adapters.llama, "get_config", lambda: {"backend": "rocm"})
    monkeypatch.setattr(provider_adapters.llama, "list_instances", lambda: instances)
    monkeypatch.setattr(provider_adapters.llama, "get_instance", lambda alias: next(item for item in instances if item["alias"] == alias))
    monkeypatch.setattr(provider_adapters.llama, "health", health)
    monkeypatch.setattr(provider_adapters.llama, "start_instance", lambda alias: (calls.append(("start", alias)) or (True, "")))
    monkeypatch.setattr(provider_adapters.llama, "stop_instance", lambda alias: (calls.append(("stop", alias)) or (True, "")))
    monkeypatch.setattr(provider_adapters.llama, "delete_instance", lambda alias: calls.append(("delete", alias)))
    monkeypatch.setattr("app.models_mgmt.runtime_policy.ensure_gpu_profile", lambda **kwargs: {})

    listed = asyncio.run(provider_adapters.list_models("llama.cpp"))
    assert [item["id"] for item in listed] == ["a", "b"]
    assert listed[0]["loaded"] is True and listed[0]["details"]["vision_enabled"] is True
    assert listed[1]["details"]["port"] == 8101 and listed[1]["details"]["vision_enabled"] is False
    asyncio.run(provider_adapters.load_model("llama.cpp", "b"))
    asyncio.run(provider_adapters.unload_model("llama.cpp", "a"))
    asyncio.run(provider_adapters.delete_model("llama.cpp", "b"))
    assert calls == [("start", "b"), ("stop", "a"), ("delete", "b")]


async def _async_result(calls, call, result):
    calls.append(call)
    return result


def test_external_provider_rejects_mutation(monkeypatch):
    import asyncio
    import pytest
    from app.models_mgmt import provider_adapters

    async def catalog(**kwargs):
        return [{
            "id": "external", "provider": "openai-compatible", "managed": False,
            "available": True, "models": ["remote"], "capabilities": ["list"],
        }]

    monkeypatch.setattr(provider_adapters.providers, "list_providers", catalog)
    listed = asyncio.run(provider_adapters.list_models("external"))
    assert listed[0]["id"] == "remote"
    with pytest.raises(provider_adapters.UnsupportedOperation):
        asyncio.run(provider_adapters.load_model("external", "remote"))
    with pytest.raises(provider_adapters.UnsupportedOperation):
        asyncio.run(provider_adapters.get_model_config("external", "remote"))


def test_llama_common_config_rejects_identity_changes(monkeypatch):
    import asyncio
    import pytest
    from app.models_mgmt import provider_adapters

    async def catalog(**kwargs):
        return [{
            "id": "llama.cpp", "provider": "llama.cpp", "managed": True,
            "available": True, "models": ["local"], "capabilities": ["list", "configure"],
        }]

    instance = {"alias": "local", "model_path": "/models/local.gguf", "port": 8080, "ctx_size": 4096}
    monkeypatch.setattr(provider_adapters.providers, "list_providers", catalog)
    monkeypatch.setattr(provider_adapters.llama, "get_instance", lambda alias: dict(instance))
    monkeypatch.setattr(provider_adapters.llama, "save_instance", lambda alias, patch: {"selected_alias": alias})

    assert asyncio.run(provider_adapters.get_model_config("llama.cpp", "local"))["ctx_size"] == 4096
    configured = asyncio.run(provider_adapters.configure_model("llama.cpp", "local", {"ctx_size": 8192}))
    assert configured["model"] == "local"
    with pytest.raises(provider_adapters.InvalidConfiguration):
        asyncio.run(provider_adapters.configure_model("llama.cpp", "local", {"model_path": "/tmp/other.gguf"}))


def test_common_provider_api_routes(admin_client, monkeypatch):
    from app.models_mgmt import provider_adapters

    async def listed(provider_id):
        return [{"id": "m", "name": "m", "size_bytes": 1, "modified_at": "", "loaded": False, "details": {}}]

    async def loaded(provider_id, model_id, keep_alive=None):
        return {"model": model_id, "loaded": True}

    async def configured(provider_id, model_id, patch):
        return dict(patch)

    async def model_config(provider_id, model_id):
        return {"num_ctx": 4096}

    async def ensure_operation(provider_id, operation):
        return {"provider": provider_id, "managed": True, "capabilities": [operation]}

    async def pulled(provider_id, model_id):
        yield {"status": "success", "completed": 1, "total": 1}

    monkeypatch.setattr(provider_adapters, "list_models", listed)
    monkeypatch.setattr(provider_adapters, "load_model", loaded)
    monkeypatch.setattr(provider_adapters, "get_model_config", model_config)
    monkeypatch.setattr(provider_adapters, "configure_model", configured)
    monkeypatch.setattr(provider_adapters, "ensure_operation", ensure_operation)
    monkeypatch.setattr(provider_adapters, "pull_model", pulled)
    assert admin_client.get("/api/v1/models/providers/ollama/models").json()[0]["id"] == "m"
    response = admin_client.post(
        "/api/v1/models/providers/ollama/models/m/load",
        json={"keep_alive": "1h"}, headers={"X-Requested-With": "ControlDeck"},
    )
    assert response.status_code == 200 and response.json()["loaded"] is True
    assert admin_client.get("/api/v1/models/providers/ollama/models/m/config").json() == {"num_ctx": 4096}
    configured_response = admin_client.put(
        "/api/v1/models/providers/ollama/models/m/config",
        json={"num_ctx": 8192}, headers={"X-Requested-With": "ControlDeck"},
    )
    assert configured_response.status_code == 200 and configured_response.json() == {"num_ctx": 8192}
    pull_response = admin_client.post(
        "/api/v1/models/providers/ollama/pull-jobs",
        json={"model": "m"}, headers={"X-Requested-With": "ControlDeck"},
    )
    assert pull_response.status_code == 201 and pull_response.json()["job_id"]


def test_managed_provider_is_marked_so_ui_can_hide_endpoint_url(monkeypatch):
    """管理下のモデルは接続先を意識させない。

    どのポートで動くかは ControlDeck（エンドポイント）が決めるので、
    UI では URL を隠してモデル名だけ出す。外部endpointだけ区別のためURLを添える。
    そのための managed フラグが provider catalog に載っていること。
    """
    import asyncio

    from app.models_mgmt import providers

    async def _run():
        return await providers.list_providers(include_unavailable=True)

    catalog = asyncio.run(_run())
    managed = [p for p in catalog if p.get("managed")]
    assert managed, "管理下providerが1件も無い"
    for provider in catalog:
        # UI が分岐に使うので、必ず真偽が決まっていること
        assert isinstance(provider.get("managed"), bool)


def test_gateway_is_the_default_endpoint_for_llama_runtime(monkeypatch):
    """llama.cpp運用時の既定接続先はゲートウェイ。

    個別ポートを直接指すと、クライアントごとに違うモデルを起こしてしまう。
    接続先を1アドレスへ揃えると、モデル解決・起動・受け入れ制御が一元化される。
    """
    import asyncio

    from app.models_mgmt import llama, providers

    async def candidates():
        return [
            {"id": "llama.cpp", "provider": "llama.cpp", "name": "llama.cpp",
             "base_url": "http://127.0.0.1:8090/v1", "managed": True,
             "installed": True, "experimental": True},
        ]

    monkeypatch.setattr(providers, "_candidates", candidates)
    monkeypatch.setattr(providers, "_selected_runtime", lambda: "llama.cpp")
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "chat", "role": "llm", "port": 8090},
        {"alias": "embed", "role": "embedding", "port": 8094},
    ])
    result = asyncio.run(providers.list_providers(include_gateway=True))
    assert result[0]["id"] == "control-deck-gateway"
    assert result[0]["selected"] is True
    assert result[0]["base_url"].endswith("/api/v1/llm/v1")
    # チャット先だけを束ねる（embeddingは出さない）。先頭のautoは転送先をControlDeckに任せる。
    assert result[0]["models"] == ["auto", "chat"]
    # 個別ポートはゲートウェイへ集約され、接続先の選択肢には残さない
    assert [item["id"] for item in result] == ["control-deck-gateway"]


def test_gateway_is_hidden_from_runtime_management_listing(monkeypatch):
    """モデル管理の一覧には出さない。モデルを保有するのはllama.cpp側のため。"""
    import asyncio

    from app.models_mgmt import llama, providers

    async def candidates():
        return [
            {"id": "llama.cpp", "provider": "llama.cpp", "name": "llama.cpp",
             "base_url": "http://127.0.0.1:8090/v1", "managed": True,
             "installed": True, "experimental": True},
        ]

    monkeypatch.setattr(providers, "_candidates", candidates)
    monkeypatch.setattr(providers, "_selected_runtime", lambda: "llama.cpp")
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "chat", "role": "llm", "port": 8090},
    ])
    result = asyncio.run(providers.list_providers())
    assert [item["id"] for item in result] == ["llama.cpp"]
    # 既定の選択はランタイム側へ戻る
    assert result[0]["selected"] is True


# ── 同時ロード上限の競合 ────────────────────────────────────────────────
#
# 実機で「2 番目のモデルを読み込み、チャットで LLM を呼ぶとメモリの読み込みが
# おかしくなる」ことがあった。上限判定が check-then-act で、ensure_chat_model_ready
# の lock が base_url::model 単位のため、別モデルの要求同士が直列化されない。
# 実測で上限 1 に対し同時要求 2 件が両方通っていた。

@pytest.fixture
def _one_model_at_a_time(monkeypatch):
    from app.models_mgmt import provider_adapters as adapters

    loaded: set[str] = set()

    class Policy:
        max_loaded_models = 1

    async def running_models():
        await asyncio.sleep(0)  # 判定と反映の間に他が走る余地を作る
        return [{"name": name} for name in loaded]

    async def load(model_id, keep_alive=None):
        await asyncio.sleep(0.01)
        loaded.add(model_id)
        return {"model": model_id, "loaded": True}

    async def provider(_provider_id):
        return {"provider": "ollama", "managed": True, "capabilities": ["load"]}

    monkeypatch.setattr("app.models_mgmt.runtime_policy.get_policy", lambda: Policy())
    monkeypatch.setattr("app.models_mgmt.runtime_policy.ensure_gpu_profile", lambda **_: {})
    monkeypatch.setattr(adapters, "_provider", provider)
    monkeypatch.setattr(adapters.ollama, "running_models", running_models)
    monkeypatch.setattr(adapters.ollama, "load", load)
    monkeypatch.setattr(adapters.llama, "list_instances", lambda: [])
    adapters._inflight.clear()
    return adapters, loaded


def test_concurrent_loads_cannot_exceed_the_limit(_one_model_at_a_time):
    """判定と、判定の前提になる状態の変更が別々だと、両方が「空きがある」を見る。"""
    adapters, loaded = _one_model_at_a_time

    async def attempt(name):
        try:
            await adapters.load_model("ollama", name)
            return True
        except adapters.ProviderError:
            return False

    async def run():
        return await asyncio.gather(attempt("a"), attempt("b"))

    outcomes = asyncio.run(run())
    assert sorted(outcomes) == [False, True], "同時要求で上限を超えた"
    assert len(loaded) == 1
    assert not adapters._inflight, "予約が残っている"


def test_a_failed_load_gives_its_slot_back(_one_model_at_a_time):
    """返さないと、以後その分だけ上限が目減りする。"""
    adapters, loaded = _one_model_at_a_time

    async def run():
        async def boom(model_id, keep_alive=None):
            raise adapters.ollama.OllamaError("起動に失敗")

        original = adapters.ollama.load
        adapters.ollama.load = boom
        try:
            with pytest.raises(adapters.ProviderError):
                await adapters.load_model("ollama", "x")
        finally:
            adapters.ollama.load = original
        assert not adapters._inflight
        await adapters.load_model("ollama", "y")

    asyncio.run(run())
    assert loaded == {"y"}


def test_reloading_a_model_that_is_already_loaded_is_allowed(_one_model_at_a_time):
    adapters, loaded = _one_model_at_a_time

    async def run():
        await adapters.load_model("ollama", "a")
        await adapters.load_model("ollama", "a")  # 同じものの再要求は枠を使わない

    asyncio.run(run())
    assert loaded == {"a"}
