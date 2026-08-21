import asyncio
import json
from contextlib import nullcontext


def test_agent_mcp_token_is_user_bound_and_long_ttl_is_explicit(admin_client):
    import pytest

    from app.addons import tokens
    from app.addons.agent_mcp import MCP_TOKEN_TTL_SECONDS, issue_opencode_token
    from app.database import SessionLocal
    from app.models import User
    from sqlalchemy import select

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
    token = issue_opencode_token(user.id, "job-safe_1")
    claims = tokens.verify(
        token,
        addon_id="control-deck",
        kind="agent-mcp",
        max_ttl_seconds=MCP_TOKEN_TTL_SECONDS,
    )
    assert claims["actor_user_id"] == user.id
    assert claims["sub"] == "opencode:job-safe_1"
    with pytest.raises(tokens.AddonTokenError):
        tokens.verify(token, addon_id="control-deck", kind="agent-mcp")


def test_agent_mcp_catalog_uses_public_ids_and_namespaces_duplicates(monkeypatch):
    from app.addons import execution

    contributions = [
        {"addon_id": "media-forge", "id": "media.capabilities", "label": "Capabilities"},
        {"addon_id": "one", "id": "shared.inspect", "label": "One"},
        {"addon_id": "two", "id": "shared.inspect", "label": "Two"},
    ]
    monkeypatch.setattr(execution, "discover", lambda kind, permissions: contributions)

    async def schema(addon_id, contribution_id, permissions=None):
        return {"type": "object", "additionalProperties": False}

    monkeypatch.setattr(execution, "agent_schema", schema)
    tools = asyncio.run(execution.agent_mcp_tools({"workflows.run"}))
    assert [item["name"] for item in tools] == [
        "media.capabilities",
        "one.shared.inspect",
        "two.shared.inspect",
    ]
    assert asyncio.run(execution.agent_mcp_target("media.capabilities", {"workflows.run"})) == (
        "media-forge",
        "media.capabilities",
    )


def test_stdio_bridge_protocol_and_tool_result(monkeypatch):
    from app.integrations.opencode import addon_mcp_bridge as bridge

    requests = []

    def host_request(path, payload=None):
        requests.append((path, payload))
        if path == "/tools":
            return {"tools": [{"name": "media.capabilities", "inputSchema": {"type": "object"}}]}
        return {"job_id": "job-1", "asset_id": "job-result:job-1", "output": {"available": True}}

    monkeypatch.setattr(bridge, "_host_request", host_request)
    initialized = bridge.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert initialized["result"]["protocolVersion"] == "2025-03-26"
    fallback = bridge.handle_message({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "initialize",
        "params": {"protocolVersion": "2099-01-01"},
    })
    assert fallback["result"]["protocolVersion"] == bridge.LATEST_PROTOCOL_VERSION
    malformed_params = bridge.handle_message({
        "jsonrpc": "2.0", "id": 5, "method": "initialize", "params": [],
    })
    assert malformed_params["result"]["protocolVersion"] == bridge.LATEST_PROTOCOL_VERSION
    listed = bridge.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed["result"]["tools"][0]["name"] == "media.capabilities"
    called = bridge.handle_message({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "media.capabilities", "arguments": {}},
    })
    assert called["result"]["structuredContent"]["job_id"] == "job-1"
    assert requests[-1] == ("/call", {"name": "media.capabilities", "arguments": {}})
    assert bridge.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_stdio_bridge_marks_host_post_as_control_deck_request(monkeypatch):
    from app.integrations.opencode import addon_mcp_bridge as bridge

    captured = {}

    class Response:
        def read(self, _limit):
            return b'{"job_id":"job-1"}'

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return nullcontext(Response())

    monkeypatch.setenv("CONTROL_DECK_ADDON_MCP_URL", "http://127.0.0.1:8765/api/v1/addons/agent-mcp")
    monkeypatch.setenv("CONTROL_DECK_ADDON_MCP_TOKEN", "signed-token")
    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen)
    assert bridge._host_request("/call", payload={"name": "media.capabilities"}) == {"job_id": "job-1"}
    assert captured["request"].get_header("X-requested-with") == "ControlDeck"
    assert captured["request"].get_header("Authorization") == "Bearer signed-token"
    assert captured["timeout"] == 130


def test_runtime_config_projects_mcp_only_with_user_authority(monkeypatch, tmp_path):
    from app.addons import agent_mcp
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_mcp, "issue_opencode_token", lambda user_id, correlation: "signed-user-token")
    without_user = provider._runtime_config("without-user", "http://127.0.0.1:8090/v1", "local")
    assert "mcp" not in json.loads(without_user.read_text(encoding="utf-8"))
    with_user = provider._runtime_config(
        "with-user", "http://127.0.0.1:8090/v1", "local", owner_user_id=7,
    )
    payload = json.loads(with_user.read_text(encoding="utf-8"))
    server = payload["mcp"]["controldeck_addons"]
    assert server["type"] == "local" and server["enabled"] is True
    assert isinstance(server["command"], list) and server["command"][1].endswith("addon_mcp_bridge.py")
    assert server["environment"]["CONTROL_DECK_ADDON_MCP_TOKEN"] == "signed-user-token"
    assert server["environment"]["CONTROL_DECK_ADDON_MCP_URL"].startswith("http://127.0.0.1:")
