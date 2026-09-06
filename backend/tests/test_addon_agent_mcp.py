import asyncio
import json
from contextlib import nullcontext
from pathlib import Path


def test_agent_mcp_token_is_user_bound_and_long_ttl_is_explicit(admin_client):
    import pytest

    from app.addons import tokens
    from app.addons.agent_mcp import MCP_TOKEN_TTL_SECONDS, issue_opencode_token
    from app.database import SessionLocal
    from app.models import User
    from sqlalchemy import select

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
    token = issue_opencode_token(user.id, "job-safe_1", project_id="sample-project")
    claims = tokens.verify(
        token,
        addon_id="control-deck",
        kind="agent-mcp",
        max_ttl_seconds=MCP_TOKEN_TTL_SECONDS,
    )
    assert claims["actor_user_id"] == user.id
    assert claims["sub"] == "opencode:job-safe_1"
    assert claims["project_id"] == "sample-project"
    with pytest.raises(tokens.AddonTokenError):
        tokens.verify(token, addon_id="control-deck", kind="agent-mcp")
    with pytest.raises(tokens.AddonTokenError):
        issue_opencode_token(user.id, "job-safe_1", project_id="../outside")


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
    def failed_host_request(*_args, **_kwargs):
        raise bridge.BridgeError("failed")

    monkeypatch.setattr(bridge, "_host_request", failed_host_request)
    failed = bridge.handle_message({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "media.capabilities", "arguments": {}},
    })
    assert failed["result"] == {
        "content": [{"type": "text", "text": "failed"}], "isError": True,
    }
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
    issued = []

    def issue_token(user_id, correlation, *, project_id=None):
        issued.append((user_id, correlation, project_id))
        return "signed-user-token"

    monkeypatch.setattr(agent_mcp, "issue_opencode_token", issue_token)
    without_user = provider._runtime_config("without-user", "http://127.0.0.1:8090/v1", "local")
    assert "mcp" not in json.loads(without_user.read_text(encoding="utf-8"))
    with_user = provider._runtime_config(
        "with-user", "http://127.0.0.1:8090/v1", "local",
        owner_user_id=7, project_id="sample-project",
    )
    payload = json.loads(with_user.read_text(encoding="utf-8"))
    server = payload["mcp"]["controldeck_addons"]
    assert server["type"] == "local" and server["enabled"] is True
    assert server["timeout"] == agent_mcp.MCP_CLIENT_TIMEOUT_MS == 135_000
    assert isinstance(server["command"], list) and server["command"][1].endswith("addon_mcp_bridge.py")
    assert server["environment"]["CONTROL_DECK_ADDON_MCP_TOKEN"] == "signed-user-token"
    assert server["environment"]["CONTROL_DECK_ADDON_MCP_URL"].startswith("http://127.0.0.1:")
    assert issued == [(7, "with-user", "sample-project")]


def test_managed_project_id_accepts_only_direct_codedev_child(monkeypatch, tmp_path):
    from app.integrations.opencode import provider

    root = tmp_path / "CodeDEV"
    project = root / "game"
    nested = project / "packages" / "client"
    outside = tmp_path / "outside"
    nested.mkdir(parents=True)
    outside.mkdir()
    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    assert provider._managed_project_id(project) == "game"
    assert provider._managed_project_id(nested) is None
    assert provider._managed_project_id(outside) is None


def test_project_output_grant_tool_is_project_scoped_and_opaque(admin_client, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.addon_runtime import grants
    from app.addons import agent_mcp
    from app.database import SessionLocal, get_db
    from app.models import User
    from app.project_lab import service as project_lab
    from sqlalchemy import select

    root = tmp_path / "CodeDEV"
    destination = root / "game" / "assets" / "generated"
    destination.mkdir(parents=True)
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (root / "game" / "linked").symlink_to(escaped, target_is_directory=True)
    grant_data = tmp_path / "grant-data"
    monkeypatch.setattr(project_lab, "project_root", lambda: root)
    monkeypatch.setattr(grants, "data_dir", lambda: grant_data)
    monkeypatch.setattr(grants.files, "resolve", lambda value: Path(value).resolve(strict=True))
    monkeypatch.setattr(agent_mcp, "_eligible_output_addons", lambda _permissions: ["fake-addon"])

    async def no_addon_tools(_permissions):
        return []

    monkeypatch.setattr(agent_mcp.execution, "agent_mcp_tools", no_addon_tools)
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
    app = FastAPI()
    app.include_router(agent_mcp.router, prefix="/api/v1")

    def database():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = database
    local_client = TestClient(app)
    scoped = agent_mcp.issue_opencode_token(user.id, "project-tool", project_id="game")
    headers = {"Authorization": f"Bearer {scoped}", "X-Requested-With": "ControlDeck"}
    listed = local_client.get("/api/v1/addons/agent-mcp/tools", headers=headers)
    assert listed.status_code == 200
    assert [tool["name"] for tool in listed.json()["tools"]] == [agent_mcp.PROJECT_OUTPUT_GRANT_TOOL]
    created = local_client.post(
        "/api/v1/addons/agent-mcp/call",
        headers=headers,
        json={"name": agent_mcp.PROJECT_OUTPUT_GRANT_TOOL, "arguments": {
            "addon_id": "fake-addon", "relative_directory": "assets/generated",
        }},
    )
    assert created.status_code == 200, created.text
    assert set(created.json()) == {"grant_id", "kind", "name", "size", "expires_at"}
    assert created.json()["grant_id"].startswith("grant:") and str(root) not in created.text
    escaped_response = local_client.post(
        "/api/v1/addons/agent-mcp/call",
        headers=headers,
        json={"name": agent_mcp.PROJECT_OUTPUT_GRANT_TOOL, "arguments": {
            "addon_id": "fake-addon", "relative_directory": "linked",
        }},
    )
    assert escaped_response.status_code == 422
    root_response = local_client.post(
        "/api/v1/addons/agent-mcp/call",
        headers=headers,
        json={"name": agent_mcp.PROJECT_OUTPUT_GRANT_TOOL, "arguments": {
            "addon_id": "fake-addon", "relative_directory": ".",
        }},
    )
    assert root_response.status_code == 422

    unscoped = agent_mcp.issue_opencode_token(user.id, "no-project")
    unscoped_headers = {"Authorization": f"Bearer {unscoped}", "X-Requested-With": "ControlDeck"}
    assert local_client.get("/api/v1/addons/agent-mcp/tools", headers=unscoped_headers).json()["tools"] == []
    denied = local_client.post(
        "/api/v1/addons/agent-mcp/call",
        headers=unscoped_headers,
        json={"name": agent_mcp.PROJECT_OUTPUT_GRANT_TOOL, "arguments": {
            "addon_id": "fake-addon", "relative_directory": "assets/generated",
        }},
    )
    assert denied.status_code == 404


def test_published_tool_schema_drops_length_bounds_but_validation_keeps_them(monkeypatch):
    """モデルへ出すスキーマから長さ制約を落とす（検証側の制約は残す）。

    制約付きデコード（llama.cpp の JSON schema → GBNF 変換）は maxLength を
    「文字ルールの繰り返し回数」へ展開する。大きな値が一つあるだけで文法生成に
    失敗し、その tool を含む全リクエストが 400 になるため、Add-on tool を有効に
    しただけで OpenCode がローカルモデルを使えなくなる。
    """
    from app.addons import execution

    raw = {
        "type": "object",
        "properties": {
            "text": {"type": ["string", "null"], "maxLength": 100_000},
            "stages": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64}},
            }},
        },
        "required": ["text"],
    }
    contributions = [{"addon_id": "sonic-forge", "id": "sonic.pipeline", "label": "Pipeline"}]
    monkeypatch.setattr(execution, "discover", lambda kind, permissions: contributions)

    async def schema(addon_id, contribution_id, permissions=None):
        return raw

    monkeypatch.setattr(execution, "agent_schema", schema)

    published = json.dumps(asyncio.run(execution.agent_mcp_tools({"workflows.run"})))
    assert "maxLength" not in published and "minLength" not in published
    # 構造・型・required は落とさない。落とすのは展開できない長さ制約だけ。
    assert '"required": ["text"]' in published.replace("'", '"')
    assert '"type": "array"' in published

    definitions = json.dumps(asyncio.run(execution.agent_tool_definitions({"workflows.run"})))
    assert "maxLength" not in definitions and "minLength" not in definitions

    # 元のスキーマは書き換えない。実際の上限は create_agent_tool_job の validate() が使う。
    assert raw["properties"]["text"]["maxLength"] == 100_000


def test_mcp_token_renews_itself_while_the_session_is_in_use(monkeypatch):
    """OpenCode の session は何日も開く。使われている限り期限で切らさない。"""
    import time as _time

    from app.addons import agent_mcp

    claims = {
        "sub": "opencode:tui-1",
        "actor_user_id": 1,
        "project_id": "sample",
        "exp": int(_time.time()) + agent_mcp.MCP_TOKEN_TTL_SECONDS,
    }
    # 発行直後は更新しない。毎回作り直すと token が無駄に増える。
    assert agent_mcp._renewed_token(claims) is None

    # 残りが半分を切ったら新しいものを配る
    claims["exp"] = int(_time.time()) + agent_mcp.MCP_TOKEN_TTL_SECONDS // 4
    renewed = agent_mcp._renewed_token(claims)
    assert isinstance(renewed, str) and renewed

    from app.addons import tokens

    fresh = tokens.verify(
        renewed, addon_id="control-deck", kind="agent-mcp",
        max_ttl_seconds=agent_mcp.MCP_TOKEN_TTL_SECONDS,
    )
    # 範囲は据え置き。更新で権限が広がってはいけない。
    assert fresh["sub"] == claims["sub"]
    assert fresh["actor_user_id"] == claims["actor_user_id"]
    assert fresh["project_id"] == claims["project_id"]
    assert fresh["exp"] > claims["exp"]


def test_mcp_token_renewal_refuses_claims_it_cannot_trust():
    from app.addons import agent_mcp

    near = int(__import__("time").time()) + 60
    assert agent_mcp._renewed_token({"sub": "opencode:x", "actor_user_id": 1}) is None
    assert agent_mcp._renewed_token({"sub": "", "actor_user_id": 1, "exp": near}) is None
    assert agent_mcp._renewed_token({"sub": "opencode:x", "actor_user_id": None, "exp": near}) is None
    assert agent_mcp._renewed_token({"sub": "opencode:", "actor_user_id": 1, "exp": near}) is None


def test_output_grant_is_issued_automatically_for_the_current_project(admin_client, monkeypatch, tmp_path):
    """置き先の grant を agent に作らせない。

    生成物を project へ置くツールは project_output_grant を必須にしている。agent は
    それを自分で作ってから呼ぶ必要があり、手順が1つ増えるぶん「生成はできたのに
    置けない」で止まりやすい。

    自動で作っても境界は変わらない。作るのは呼ばれている add-on のぶんだけで、
    置き先は token が指している今の project の中に限られる。agent が自分で
    control_deck.project_output_grant を呼べば得られるものと同じである。
    """
    from app.addon_runtime import grants
    from app.addons import agent_mcp
    from app.database import SessionLocal
    from app.models import User
    from app.project_lab import service as project_lab
    from sqlalchemy import select

    root = tmp_path / "CodeDEV"
    (root / "game").mkdir(parents=True)
    grant_data = tmp_path / "grant-data"
    monkeypatch.setattr(project_lab, "project_root", lambda: root)
    monkeypatch.setattr(grants, "data_dir", lambda: grant_data)
    monkeypatch.setattr(grants.files, "resolve", lambda value: Path(value).resolve(strict=True))
    monkeypatch.setattr(agent_mcp, "_eligible_output_addons", lambda _permissions: ["sonic-forge"])
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()

    issued = agent_mcp._auto_output_grant("sonic-forge", "game", {"any"}, user)

    assert isinstance(issued, str) and issued.startswith("grant:")
    # 置き先は project の中。掘るのは1段だけで、無ければ作る。
    created = root / "game" / agent_mcp.AUTO_OUTPUT_DIRECTORY
    assert created.is_dir()


def test_auto_grant_refuses_addons_and_projects_outside_the_session(admin_client, monkeypatch, tmp_path):
    """自動発行でも、明示発行と同じ範囲しか許さない。"""
    from app.addon_runtime import grants
    from app.addons import agent_mcp
    from app.database import SessionLocal
    from app.models import User
    from app.project_lab import service as project_lab
    from sqlalchemy import select

    root = tmp_path / "CodeDEV"
    (root / "game").mkdir(parents=True)
    monkeypatch.setattr(project_lab, "project_root", lambda: root)
    monkeypatch.setattr(grants, "data_dir", lambda: tmp_path / "grant-data")
    monkeypatch.setattr(agent_mcp, "_eligible_output_addons", lambda _permissions: ["sonic-forge"])
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()

    # 資格の無い add-on には出さない
    assert agent_mcp._auto_output_grant("other-addon", "game", {"any"}, user) is None
    # session が project を指していなければ出さない
    assert agent_mcp._auto_output_grant("sonic-forge", None, {"any"}, user) is None
    # project の外は指させない
    assert agent_mcp._auto_output_grant("sonic-forge", "../outside", {"any"}, user) is None
    # 作れなかったことを例外にしない（add-on 側の入力検証に理由を言わせる）
    assert agent_mcp._auto_output_grant("sonic-forge", "missing-project", {"any"}, user) is None
