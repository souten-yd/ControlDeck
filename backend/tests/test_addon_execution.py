from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.conftest import CSRF_HEADERS
from tests.test_addon_api import addon_api
from tests.test_addon_contract import addon_manifest
from tests.test_addon_registry import _health, _install, isolated_registry


def _transport(handler):
    return lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout, follow_redirects=False)


def test_remote_execution_validates_schemas_tokens_sizes_and_disable_race(isolated_registry, monkeypatch):
    from app.addons import execution

    execution.reset_for_tests()
    registry = isolated_registry
    _install(registry)
    registry.set_enabled("fake-addon", True)
    registry.update_health("fake-addon", _health())
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/schemas/workflow-input":
            return httpx.Response(200, json={
                "type": "object", "required": ["prompt"],
                "properties": {"prompt": {"type": "string"}}, "additionalProperties": False,
            })
        if request.url.path == "/schemas/workflow-output":
            return httpx.Response(200, json={
                "type": "object", "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
            })
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(execution, "_client", _transport(handler))

    async def scenario():
        input_schema, output_schema = await execution.workflow_schemas("fake-addon", "fake.generate")
        execution.validate(input_schema, {"prompt": "hello"}, label="input")
        with pytest.raises(execution.AddonExecutionError) as invalid:
            execution.validate(input_schema, {"prompt": 3}, label="input")
        assert invalid.value.code == "schema_validation_failed"
        output = await execution.invoke(
            "workflow_executors", "fake-addon", "fake.generate", {"prompt": "hello"}, subject="workflow:7",
        )
        execution.validate(output_schema, output, label="output")

    asyncio.run(scenario())
    call = seen[-1]
    assert call.url.path == "/workflow/execute"
    assert call.headers["authorization"].startswith("Bearer ")
    assert call.headers["x-control-deck-addon-id"] == "fake-addon"
    assert "cookie" not in call.headers

    registry.set_enabled("fake-addon", False)

    async def disabled_scenario():
        with pytest.raises(execution.AddonExecutionError) as disabled:
            await execution.invoke(
                "workflow_executors", "fake-addon", "fake.generate", {"prompt": "hello"}, subject="workflow:7",
            )
        return disabled.value.code

    assert asyncio.run(disabled_scenario()) == "addon_disabled"


def test_remote_execution_rejects_redirect_invalid_schema_and_oversize(isolated_registry, monkeypatch):
    from app.addons import execution

    execution.reset_for_tests()
    registry = isolated_registry
    _install(registry)
    registry.set_enabled("fake-addon", True)
    registry.update_health("fake-addon", _health())

    def invalid_schema(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "array"})

    monkeypatch.setattr(execution, "_client", _transport(invalid_schema))

    async def invalid_scenario():
        with pytest.raises(execution.AddonExecutionError) as invalid:
            await execution.schema("fake-addon", "/schemas/workflow-input")
        return invalid.value.code

    assert asyncio.run(invalid_scenario()) == "invalid_schema"

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:9131/elsewhere"})

    monkeypatch.setattr(execution, "_client", _transport(redirect))

    async def rejection_scenario():
        with pytest.raises(execution.AddonExecutionError) as redirected:
            await execution.invoke("workflow_executors", "fake-addon", "fake.generate", {}, subject="workflow:7")
        with pytest.raises(execution.AddonExecutionError) as oversized:
            await execution.invoke(
                "workflow_executors", "fake-addon", "fake.generate",
                {"value": "x" * execution.REQUEST_LIMIT_BYTES}, subject="workflow:7",
            )
        return redirected.value.code, oversized.value.code

    assert asyncio.run(rejection_scenario()) == ("redirect_rejected", "request_too_large")


def test_execution_discovery_filters_permission_availability_and_invalid_schema(addon_api, monkeypatch):
    client, registry = addon_api
    from app.addons import execution
    from app.addons.schema import AddonHealthReport

    execution.reset_for_tests()
    value = addon_manifest()
    value["contributions"]["agent_tools"][0]["permission"] = "settings.manage"
    assert client.post("/api/v1/addons", json=value, headers=CSRF_HEADERS).status_code == 201
    assert client.post("/api/v1/addons/fake-addon/enable", headers=CSRF_HEADERS).status_code == 200
    registry.update_health("fake-addon", AddonHealthReport.model_validate({
        "status": "degraded", "contract_version": "2.0",
        "reason_code": "service_unreachable", "message": "partial", "action": {"kind": "retry"},
        "contributions": {
            "workflow_executor:fake.generate": "available",
            "agent_tool:fake.generate": "available",
            "context_action:fake.inspect": "unavailable",
        },
    }))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("workflow-input"):
            return httpx.Response(200, json={"type": "array"})
        return httpx.Response(200, json={"type": "object"})

    monkeypatch.setattr(execution, "_client", _transport(handler))
    response = client.get("/api/v1/addons/execution-contributions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["contributions"]["workflow_executors"] == []
    assert payload["schema_errors"] == {"fake-addon:fake.generate": "invalid_schema"}
    assert payload["contributions"]["agent_tools"][0]["id"] == "fake.generate"
    assert payload["contributions"]["context_actions"] == []
