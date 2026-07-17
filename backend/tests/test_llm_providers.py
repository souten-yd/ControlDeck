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

    listed = asyncio.run(provider_adapters.list_models("ollama"))
    assert listed[0]["id"] == "qwen" and listed[0]["size_bytes"] == 123 and listed[0]["loaded"] is True
    assert asyncio.run(provider_adapters.load_model("ollama", "qwen", "1h"))["loaded"] is True
    assert asyncio.run(provider_adapters.unload_model("ollama", "qwen"))["loaded"] is False
    asyncio.run(provider_adapters.delete_model("ollama", "qwen"))
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
        {"alias": "a", "model_path": str(model_a), "port": 8100, "base_url": "http://127.0.0.1:8100/v1", "unit": "a.service", "runtime_status": "RUNNING"},
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
    assert listed[0]["loaded"] is True and listed[1]["details"]["port"] == 8101
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


def test_common_provider_api_routes(admin_client, monkeypatch):
    from app.models_mgmt import provider_adapters

    async def listed(provider_id):
        return [{"id": "m", "name": "m", "size_bytes": 1, "modified_at": "", "loaded": False, "details": {}}]

    async def loaded(provider_id, model_id, keep_alive=None):
        return {"model": model_id, "loaded": True}

    monkeypatch.setattr(provider_adapters, "list_models", listed)
    monkeypatch.setattr(provider_adapters, "load_model", loaded)
    assert admin_client.get("/api/v1/models/providers/ollama/models").json()[0]["id"] == "m"
    response = admin_client.post(
        "/api/v1/models/providers/ollama/models/m/load",
        json={"keep_alive": "1h"}, headers={"X-Requested-With": "ControlDeck"},
    )
    assert response.status_code == 200 and response.json()["loaded"] is True
