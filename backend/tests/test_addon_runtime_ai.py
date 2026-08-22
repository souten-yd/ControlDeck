from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.models_mgmt.ai_gateway import AITarget
from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def runtime_ai_api(admin_client, monkeypatch, tmp_path):
    from app.addon_runtime import ai as runtime_ai
    from app.addons import health, registry, router as addon_router, tokens
    from app.addons.schema import AddonHealthReport
    from app.database import SessionLocal
    from app.models import User

    data = tmp_path / "runtime-ai-data"
    monkeypatch.setattr(registry, "data_dir", lambda: data)
    monkeypatch.setattr(tokens, "data_dir", lambda: data)
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()

    async def healthy(addon_id: str, client=None):
        return registry.update_health(addon_id, AddonHealthReport.model_validate({
            "status": "healthy", "contract_version": "2.0",
        }))

    async def no_grace():
        return None

    async def available(_capability: str) -> bool:
        return True

    class FakeProvider:
        def __init__(self):
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            return '{"ok":true}'

    provider = FakeProvider()
    monkeypatch.setattr(health, "recheck", healthy)
    monkeypatch.setattr(addon_router, "_wait_for_disable_grace", no_grace)
    monkeypatch.setattr(runtime_ai, "capability_available", available)
    monkeypatch.setattr(
        runtime_ai,
        "resolve_ai_target",
        lambda capability: asyncio.sleep(0, result=AITarget("http://127.0.0.1:18080/v1", "host-selected")),
    )
    monkeypatch.setattr(runtime_ai, "provider_for_base_url", lambda _base: provider)

    manifest = deepcopy(addon_manifest())
    manifest["host_capabilities"].append("ai.inference")
    assert admin_client.post("/api/v1/addons", json=manifest, headers=CSRF_HEADERS).status_code == 201
    assert admin_client.post(
        "/api/v1/addons/fake-addon/enable",
        json={"granted_capabilities": ["ai.inference"]},
        headers=CSRF_HEADERS,
    ).status_code == 200
    with SessionLocal() as db:
        user_id = db.query(User).filter(User.username == "admin").one().id
    token = tokens.issue("fake-addon", subject=str(user_id), kind="service")
    headers = {"Authorization": f"Bearer {token}", "X-Control-Deck-Addon-ID": "fake-addon"}
    return admin_client, headers, provider


def test_addon_ai_capability_is_manifest_valid():
    from app.addons.schema import parse_manifest

    manifest = addon_manifest()
    manifest["host_capabilities"].append("ai.inference")
    parsed = parse_manifest(manifest)
    assert "ai.inference" in parsed.manifest.host_capabilities


def test_addon_ai_complete_uses_host_selected_target_without_leaking_identity(runtime_ai_api):
    client, headers, provider = runtime_ai_api
    response = client.post(
        "/api/v1/addon-runtime/fake-addon/ai/complete",
        headers=headers,
        json={
            "capability": "vision.analyze",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,eA=="}},
                ],
            }],
            "response_format": {"type": "json_object"},
            "max_tokens": 128,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"content": '{"ok":true}', "capability": "vision.analyze"}
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.model == "host-selected"
    assert request.base_url == "http://127.0.0.1:18080/v1"
    assert request.disable_thinking is True


def test_addon_ai_capabilities_do_not_expose_provider_or_model(runtime_ai_api):
    client, headers, _provider = runtime_ai_api
    response = client.get("/api/v1/addon-runtime/fake-addon/ai/capabilities", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "text.generate": {"available": True},
        "vision.analyze": {"available": True},
    }


@pytest.mark.parametrize("payload", [
    {
        "capability": "vision.analyze",
        "messages": [{"role": "user", "content": "no image"}],
    },
    {
        "capability": "vision.analyze",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
        ]}],
    },
])
def test_addon_ai_rejects_unbounded_or_missing_vision_input(runtime_ai_api, payload):
    client, headers, _provider = runtime_ai_api
    response = client.post(
        "/api/v1/addon-runtime/fake-addon/ai/complete",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 422


def test_ai_gateway_llama_vision_requires_mmproj_and_prefers_loaded(monkeypatch):
    from app.models_mgmt import ai_gateway, llama, runtime_policy

    monkeypatch.setattr(runtime_policy, "get_policy", lambda: SimpleNamespace(selected_runtime="llama.cpp"))
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {
            "alias": "text-only", "role": "llm", "mmproj_path": "",
            "base_url": "http://127.0.0.1:8080/v1", "loaded": True, "order": 1,
        },
        {
            "alias": "vision-cold", "role": "llm", "mmproj_path": "/models/mmproj.gguf",
            "base_url": "http://127.0.0.1:8081/v1", "loaded": False, "order": 1,
        },
        {
            "alias": "vision-warm", "role": "llm", "mmproj_path": "/models/mmproj2.gguf",
            "base_url": "http://127.0.0.1:8082/v1", "loaded": True, "order": 5,
        },
    ])

    target = asyncio.run(ai_gateway.resolve_ai_target("vision.analyze"))
    assert target.model == "vision-warm"
    assert target.base_url == "http://127.0.0.1:8082/v1"
