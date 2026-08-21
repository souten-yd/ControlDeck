from __future__ import annotations

import asyncio
import json
from pathlib import Path

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


def test_workflow_catalog_dry_run_execution_and_saved_unavailable_node(addon_api, monkeypatch):
    client, registry = addon_api
    from app.addons import execution, tokens
    from app.workflows import engine
    from app.workflows.dry_run import simulate_node

    execution.reset_for_tests()
    assert client.post("/api/v1/addons", json=addon_manifest(), headers=CSRF_HEADERS).status_code == 201
    assert client.post("/api/v1/addons/fake-addon/enable", headers=CSRF_HEADERS).status_code == 200
    calls: list[dict] = []
    service_claims: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/schemas/workflow-input":
            return httpx.Response(200, json={
                "type": "object", "required": ["prompt"],
                "properties": {"prompt": {"type": "string", "description": "Prompt"}},
                "additionalProperties": False,
            })
        if request.url.path == "/schemas/workflow-output":
            return httpx.Response(200, json={
                "type": "object", "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
            })
        calls.append(json.loads(request.content))
        service_claims.append(tokens.verify(
            request.headers["Authorization"].removeprefix("Bearer "),
            addon_id="fake-addon",
            kind="service",
        ))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(execution, "_client", _transport(handler))
    node_type = "addon.workflow:fake-addon:fake.generate"
    catalog = client.get("/api/v1/workflows/node-catalog")
    assert catalog.status_code == 200
    remote = next(item for item in catalog.json() if item["type"] == node_type)
    assert remote["addon"] == {"id": "fake-addon", "contribution_id": "fake.generate", "label": "Generate"}
    assert remote["config_schema"]["prompt"]["required"] is True

    definition = json.dumps({
        "nodes": [
            {"id": "start", "type": "trigger", "config": {}},
            {"id": "remote", "type": node_type, "config": {"prompt": "hello"}},
        ],
        "edges": [{"source": "start", "target": "remote"}],
    })
    engine.validate_definition(definition)
    preview = simulate_node(node_type, {"prompt": "hello"})
    assert preview["ok"] is True and preview["dry_run"] is True
    assert calls == []

    executor = execution.workflow_executor(node_type)
    assert executor is not None
    monkeypatch.setattr(execution, "execution_owner", lambda execution_id: 7 if execution_id == 17 else None)
    output = asyncio.run(executor(
        {"prompt": "{{start.message}}", "__execution_id": 17, "__node_id": "remote"},
        {"start": {"output": {"message": "rendered"}}},
    ))
    assert output == {"ok": True}
    assert calls == [{
        "input": {"prompt": "rendered"},
        "correlation": {"execution_id": "17", "node_id": "remote"},
    }]
    assert service_claims[0]["actor_user_id"] == 7
    assert service_claims[0]["sub"] == "workflow:17"

    registry.set_enabled("fake-addon", False)
    # Definition history remains structurally valid, while execution fails closed.
    engine.validate_definition(definition)
    with pytest.raises(Exception, match="無効"):
        asyncio.run(executor({"prompt": "hello"}, {}))


def test_agent_tool_runs_as_owned_job_and_returns_asset_reference(addon_api, monkeypatch):
    client, _registry = addon_api
    from app.addons import execution

    execution.reset_for_tests()
    assert client.post("/api/v1/addons", json=addon_manifest(), headers=CSRF_HEADERS).status_code == 201
    assert client.post("/api/v1/addons/fake-addon/enable", headers=CSRF_HEADERS).status_code == 200
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/schemas/agent-tool":
            return httpx.Response(200, json={
                "type": "object", "required": ["prompt"],
                "properties": {"prompt": {"type": "string"}}, "additionalProperties": False,
            })
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "generated"}]})

    monkeypatch.setattr(execution, "_client", _transport(handler))
    invalid = client.post(
        "/api/v1/addons/fake-addon/agent-tools/fake.generate/invoke",
        json={"arguments": {"prompt": 3}}, headers=CSRF_HEADERS,
    )
    assert invalid.status_code == 422
    raw_path = client.post(
        "/api/v1/addons/fake-addon/agent-tools/fake.generate/invoke",
        json={"arguments": {"prompt": "/etc/passwd"}}, headers=CSRF_HEADERS,
    )
    assert raw_path.status_code == 422
    assert raw_path.json()["detail"]["code"] == "unscoped_host_path"
    assert requests == []

    response = client.post(
        "/api/v1/addons/fake-addon/agent-tools/fake.generate/invoke",
        json={"arguments": {"prompt": "hello"}}, headers=CSRF_HEADERS,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["job_id"]
    assert result["asset_id"] == f"job-result:{result['job_id']}"
    assert result["output"]["content"][0]["text"] == "generated"
    assert requests == [{
        "input": {"prompt": "hello"},
        "correlation": {"job_id": result["job_id"]},
    }]
    job = client.get(f"/api/v1/jobs/{result['job_id']}")
    assert job.status_code == 200
    assert job.json()["owner_user_id"] is not None and job.json()["status"] == "succeeded"


def test_context_action_uses_opaque_scoped_grant_and_rejects_paths(addon_api, monkeypatch):
    client, _registry = addon_api
    from app.addons import execution, tokens

    execution.reset_for_tests()
    assert client.post("/api/v1/addons", json=addon_manifest(), headers=CSRF_HEADERS).status_code == 201
    assert client.post("/api/v1/addons/fake-addon/enable", headers=CSRF_HEADERS).status_code == 200
    requests: list[dict] = []
    service_claims: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        authorization = request.headers["Authorization"]
        service_claims.append(tokens.verify(
            authorization.removeprefix("Bearer "),
            addon_id="fake-addon",
            kind="service",
        ))
        return httpx.Response(200, json={"summary": "safe"})

    monkeypatch.setattr(execution, "_client", _transport(handler))
    rejected = client.post(
        "/api/v1/addons/fake-addon/context-actions/fake.inspect/invoke",
        json={"context_type": "file", "resource_id": "/etc/passwd", "input": {}},
        headers=CSRF_HEADERS,
    )
    assert rejected.status_code == 404
    assert requests == []

    response = client.post(
        "/api/v1/addons/fake-addon/context-actions/fake.inspect/invoke",
        json={"context_type": "file", "resource_id": "asset:report-7", "input": {"question": "summary"}},
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 200 and response.json() == {"summary": "safe"}
    payload = requests[0]
    assert payload["input"] == {"question": "summary"}
    assert payload["context"]["type"] == "file"
    assert payload["context"]["resource_id"] == "asset:report-7"
    assert payload["context"]["grant_id"] is None
    assert "/etc/passwd" not in json.dumps(payload)

    created_grants: list[tuple[str, int, str, str]] = []

    def create_grant(addon_id: str, owner_user_id: int, path: str, kind: str):
        created_grants.append((addon_id, owner_user_id, path, kind))
        return {"grant_id": "grant:context-file"}

    monkeypatch.setattr(execution.runtime_grants, "create", create_grant)
    raw_path = client.post(
        "/api/v1/addons/fake-addon/context-actions/fake.inspect/invoke",
        json={"context_type": "file", "resource_id": "/allowed/report.txt", "input": {}},
        headers=CSRF_HEADERS,
    )
    assert raw_path.status_code == 200
    assert requests[-1]["context"]["resource_id"] == "grant:context-file"
    assert requests[-1]["context"]["grant_id"] == "grant:context-file"
    assert created_grants and created_grants[0][0] == "fake-addon"
    assert "/allowed/report.txt" not in json.dumps(requests[-1])
    assert service_claims[-1]["actor_user_id"] == created_grants[0][1]
    assert service_claims[-1]["grant_ids"] == ["grant:context-file"]


@pytest.mark.parametrize(
    "route",
    [
        "/settings",
        "/x/other-addon/workspace",
        "https://example.com/x/fake-addon/workspace",
        "//example.com/x/fake-addon/workspace",
        "/x/fake-addon/workspace#outside",
        "/x/fake-addon/%2e%2e/settings",
        "/x/%6fther-addon/workspace",
        "/x/fake-addon/workspace%0aoutside",
        "/x/fake-addon/workspace\\outside",
    ],
)
def test_context_action_open_route_is_confined_to_its_own_addon(route):
    from app.addons import execution

    with pytest.raises(execution.AddonExecutionError) as invalid:
        execution.validate_context_result("fake-addon", {"action": "open_route", "route": route})
    assert invalid.value.code == "invalid_context_response"


def test_context_action_accepts_own_addon_route_and_query():
    from app.addons import execution

    value = {"action": "open_route", "route": "/x/fake-addon/workspace/edit?asset=opaque"}
    assert execution.validate_context_result("fake-addon", value) == value
    legacy = {"status": "accepted", "detail": {"id": "opaque"}}
    assert execution.validate_context_result("fake-addon", legacy) == legacy


def test_llm_agent_discovers_and_dispatches_remote_tool(monkeypatch):
    from app.addons import execution
    from app.workflows import nodes

    tool_name = "addon_fake_addon_fake_generate_deadbeef"
    posts: list[dict] = []

    async def definitions(_permissions):
        return [{
            "type": "function",
            "function": {"name": tool_name, "description": "Fake", "parameters": {"type": "object"}},
        }]

    async def invoke(name, arguments, context):
        assert name == tool_name
        assert arguments == {"prompt": "hello"}
        assert context["__execution_id"] == 9
        return {"job_id": "job-9", "asset_id": "job-result:job-9"}

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json, headers):
            posts.append(json)
            if len(posts) == 1:
                assert any(tool["function"]["name"] == tool_name for tool in json["tools"])
                return FakeResponse({"choices": [{"message": {
                    "role": "assistant", "content": "", "tool_calls": [{
                        "id": "call-1", "function": {"name": tool_name, "arguments": '{"prompt":"hello"}'},
                    }],
                }}]})
            return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    monkeypatch.setattr(execution, "agent_tool_definitions", definitions)
    monkeypatch.setattr(execution, "execution_permissions", lambda _value: {"workflows.run"})
    monkeypatch.setattr(execution, "invoke_agent_tool_name", invoke)
    monkeypatch.setattr(nodes.httpx, "AsyncClient", lambda timeout: FakeClient())

    result = asyncio.run(nodes._agent_llm(
        "http://127.0.0.1:9999/v1", "fake", "key", "", "run", {"__execution_id": 9}, {},
    ))
    assert result["content"] == "done" and result["rounds"] == 2
    tool_message = posts[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "job-result:job-9" in tool_message["content"]
