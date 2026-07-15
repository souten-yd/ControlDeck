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
    }]

    async def fake_list(**kwargs):
        return expected

    monkeypatch.setattr(providers, "list_providers", fake_list)
    assert admin_client.get("/api/v1/models/providers").json() == expected
    workflow = admin_client.get("/api/v1/workflows/llm-endpoints")
    assert workflow.status_code == 200
    assert workflow.json()[0]["base_url"] == "http://127.0.0.1:8080/v1"
    assert workflow.json()[0]["models"] == ["local"]
