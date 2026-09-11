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


def test_the_tool_listing_carries_what_the_addon_wrote_in_its_contract(monkeypatch):
    """一覧に出す説明へ、Add-on が契約に書いた使い方を載せる。

    label だけを出していた。label は画面に出す名前で 80 文字までと決まっており、
    そこに使い方は書けない。一覧を見て道具を選ぶ側には「何をするものか」しか
    伝わらず、順番や前提が伝わらなかった（SonicForge では、先に声を作らないと
    台詞ごとに別人の声になることが一覧から見えなかった）。
    """
    from app.addons import execution

    monkeypatch.setattr(
        execution,
        "discover",
        lambda kind, permissions: [{
            "addon_id": "sonic-forge",
            "id": "sonic.voice.create",
            "label": "Create a character voice",
            "schema_path": "/schemas/voice-create-request.json",
        }],
    )

    async def schema(addon_id, contribution_id, *, permissions):
        return {
            "type": "object",
            "additionalProperties": False,
            "description": "先に声を作る。作らないと台詞ごとに別人の声になる。",
        }

    monkeypatch.setattr(execution, "agent_schema", schema)
    tools = asyncio.run(execution.agent_mcp_tools({"workflows.run"}))
    description = tools[0]["description"]
    assert description.startswith("sonic-forge Add-on: Create a character voice")
    assert "作らないと台詞ごとに別人の声になる" in description

    # 契約に説明が無い Add-on は、これまでどおり label だけになる。
    async def bare(addon_id, contribution_id, *, permissions):
        return {"type": "object", "additionalProperties": False}

    monkeypatch.setattr(execution, "agent_schema", bare)
    tools = asyncio.run(execution.agent_mcp_tools({"workflows.run"}))
    assert tools[0]["description"] == "sonic-forge Add-on: Create a character voice"


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
    # 正常な処理時間で切らない。終わりを決めるのは host 側で、そちらは進捗が
    # 止まったときにだけ打ち切る（wait_agent_tool_job）。bridge が先に切ると、
    # job だけが走り続けて「失敗したのに物はできている」状態になる。
    assert captured["timeout"] == bridge.CALL_TIMEOUT_SECONDS
    assert bridge.CALL_TIMEOUT_SECONDS >= 600


def test_runtime_config_lets_opencode_drop_old_tool_output(monkeypatch, tmp_path):
    """古い道具の出力を捨てられるようにする。

    OpenCode の剪定は既定 false である。切ったままだと、忘れる仕組みが一つも
    動かない——自動圧縮のほうも model.limit.context が無いと発火しないので、
    実測ではモデルが断ってから畳む後追いだけが起きていた（793 session 中 19 件
    の圧縮のうち 17 件が後追い、先回りは 0 件）。

    剪定は会話も判断も壊さない。完了した tool の出力だけが対象で、直近
    40,000 tokens 分は残る。頂点 100k を超えた 13 session で見積もると平均
    51.7% が空く。
    """
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path)
    config = provider._runtime_config("prune", "http://127.0.0.1:8090/v1", "local")
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["compaction"]["prune"] is True


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
    # client 側で先に切らない。終わりを決めるのは host（進捗が止まったときだけ
    # 打ち切る）で、client はその答えを待つ側である。135 秒だった頃は、45 秒の
    # 曲の生成（実測 98〜127 秒）がエージェントには timeout として届きながら、
    # SonicForge 側では出来上がっていた。
    from app.integrations.opencode import addon_mcp_bridge as bridge_module

    assert server["timeout"] == agent_mcp.MCP_CLIENT_TIMEOUT_MS
    assert agent_mcp.MCP_CLIENT_TIMEOUT_MS >= bridge_module.CALL_TIMEOUT_SECONDS * 1000
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

    async def no_addon_tools(_permissions, **_options):
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


def test_project_input_grant_tool_reads_only_files_inside_the_project(admin_client, monkeypatch, tmp_path):
    """project へ置いた絵を Add-on に読ませる券。

    生成した物しか参照にできないと、下描きや既存のキャラ表を渡す道が無い。
    取り込み口は Host の画面からしか届かないので、agent 側にこの入口を置く。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.addon_runtime import grants
    from app.addons import agent_mcp
    from app.database import SessionLocal, get_db
    from app.models import User
    from app.project_lab import service as project_lab
    from sqlalchemy import select

    root = tmp_path / "CodeDEV"
    assets = root / "game" / "assets"
    assets.mkdir(parents=True)
    (assets / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 32)
    (root / "game" / "linked.png").symlink_to(outside / "secret.png")
    grant_data = tmp_path / "grant-data"
    monkeypatch.setattr(project_lab, "project_root", lambda: root)
    monkeypatch.setattr(grants, "data_dir", lambda: grant_data)
    monkeypatch.setattr(grants.files, "resolve", lambda value: Path(value).resolve(strict=True))
    monkeypatch.setattr(agent_mcp, "_eligible_input_addons", lambda _permissions: ["fake-addon"])
    monkeypatch.setattr(agent_mcp, "_eligible_output_addons", lambda _permissions: [])

    async def no_addon_tools(_permissions, **_options):
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
    scoped = agent_mcp.issue_opencode_token(user.id, "project-input", project_id="game")
    headers = {"Authorization": f"Bearer {scoped}", "X-Requested-With": "ControlDeck"}
    listed = local_client.get("/api/v1/addons/agent-mcp/tools", headers=headers)
    assert [tool["name"] for tool in listed.json()["tools"]] == [agent_mcp.PROJECT_INPUT_GRANT_TOOL]

    def call(path):
        return local_client.post(
            "/api/v1/addons/agent-mcp/call",
            headers=headers,
            json={"name": agent_mcp.PROJECT_INPUT_GRANT_TOOL, "arguments": {
                "addon_id": "fake-addon", "relative_path": path,
            }},
        )

    created = call("assets/hero.png")
    assert created.status_code == 200, created.text
    assert created.json()["kind"] == "read"
    # 券だけを返す。host の path は外へ出さない。
    assert created.json()["grant_id"].startswith("grant:") and str(root) not in created.text
    # symlink で project の外を指しても通さない。
    assert call("linked.png").status_code == 422
    assert call("../outside/secret.png").status_code == 422
    assert call("/etc/hostname").status_code == 422
    # directory は読み取りの券にならない。
    assert call("assets").status_code == 422

    unscoped = agent_mcp.issue_opencode_token(user.id, "no-project")
    unscoped_headers = {"Authorization": f"Bearer {unscoped}", "X-Requested-With": "ControlDeck"}
    assert local_client.get("/api/v1/addons/agent-mcp/tools", headers=unscoped_headers).json()["tools"] == []
    denied = local_client.post(
        "/api/v1/addons/agent-mcp/call",
        headers=unscoped_headers,
        json={"name": agent_mcp.PROJECT_INPUT_GRANT_TOOL, "arguments": {
            "addon_id": "fake-addon", "relative_path": "assets/hero.png",
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


def _fat_schema(size: int) -> dict:
    """境目を跨ぐ大きさの契約を作る。中身は問わない。"""
    return {
        "type": "object",
        "additionalProperties": False,
        "description": "3D の場面を作る。",
        "properties": {"operations": {"type": "array", "items": {"enum": ["a" * size]}}},
    }


def test_a_big_contract_is_left_out_of_the_listing_and_fetched_by_name(monkeypatch):
    """道具定義の費用の 94% は説明ではなく schema である。

    実測: 道具 22 個の定義で 24,999 トークン。大きいものに偏っていて、3D の
    scene.create と scene.edit の二つだけで schema 全体の 54% を占めていた。
    3D を触らない会話でもこれを毎ターン払っていた。

    大きいものだけを一覧から外し、使うと決めた道具の契約だけを取りに来させる。
    小さいものまで外すと往復が増えるだけなので触らない。
    """
    from app.addons import execution

    contributions = [
        {"addon_id": "media-forge", "id": "media.scene.create", "label": "Create a 3D scene",
         "schema_path": "/schemas/scene-create.json"},
        {"addon_id": "media-forge", "id": "media.capabilities", "label": "Capabilities",
         "schema_path": "/schemas/capabilities.json"},
    ]
    monkeypatch.setattr(execution, "discover", lambda kind, permissions: contributions)

    async def schema(addon_id, contribution_id, *, permissions):
        if contribution_id == "media.scene.create":
            return _fat_schema(4000)
        return {"type": "object", "additionalProperties": False, "description": "何が使えるかを返す。"}

    monkeypatch.setattr(execution, "agent_schema", schema)

    full = asyncio.run(execution.agent_mcp_tools({"workflows.run"}))
    assert "operations" in json.dumps(full, ensure_ascii=False)

    lazy = asyncio.run(execution.agent_mcp_tools({"workflows.run"}, contract_threshold=4000))
    by_name = {tool["name"]: tool for tool in lazy}

    big = by_name["media.scene.create"]
    assert big["inputSchema"] == {"type": "object", "additionalProperties": True}
    # どこへ取りに行けばよいかを、外した道具の説明そのものに書く。
    assert "control_deck.tool_contract" in big["description"]
    assert '"name": "media.scene.create"' in big["description"]
    # 一覧から選ぶ手がかりは残す。名前だけになると選べない。
    assert "3D の場面を作る" in big["description"]

    small = by_name["media.capabilities"]
    assert small["inputSchema"]["additionalProperties"] is False
    assert "control_deck.tool_contract" not in small["description"]

    contract = asyncio.run(execution.agent_contract("media.scene.create", {"workflows.run"}))
    assert contract["properties"]["operations"]["items"]["enum"] == ["a" * 4000]
    assert asyncio.run(execution.agent_contract("media.nope", {"workflows.run"})) is None

    # client は道具の名前を書き換える。OpenCode はこの MCP server の名前を前に
    # 付け、記号を _ にする（実測: controldeck_addons_media_scene_create）。
    # モデルが契約を取りに来るとき渡すのは、その見えている名前である。
    mangled = asyncio.run(execution.agent_contract(
        "controldeck_addons_media_scene_create", {"workflows.run"},
    ))
    assert mangled == contract
    # 後ろに重なる別の道具を拾わない。長い方を採る。
    assert execution._match_tool_name(
        "controldeck_addons_media_scene_create",
        ["scene.create", "media.scene.create"],
    ) == "media.scene.create"
    assert execution._match_tool_name("something_else", ["media.scene.create"]) is None


def test_the_contract_tool_appears_only_when_contracts_are_withheld(admin_client, monkeypatch):
    """境目が 0 なら従来どおり全部載せる。取りに来る道具も出さない。

    出しっぱなしにすると「契約を取ってから呼べ」と書かれていない道具にも
    使えてしまい、一覧に既に載っているものを二度払うことになる。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.addons import agent_mcp
    from app.config import get_config
    from app.database import SessionLocal, get_db
    from app.models import User
    from sqlalchemy import select

    fat = _fat_schema(4000)
    monkeypatch.setattr(
        agent_mcp.execution, "discover",
        lambda kind, permissions: [{
            "addon_id": "media-forge", "id": "media.scene.create",
            "label": "Create a 3D scene", "schema_path": "/schemas/scene-create.json",
        }],
    )

    async def schema(addon_id, contribution_id, *, permissions):
        return fat

    monkeypatch.setattr(agent_mcp.execution, "agent_schema", schema)

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
    app = FastAPI()
    app.include_router(agent_mcp.router, prefix="/api/v1")

    def database():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = database
    local_client = TestClient(app)
    token = agent_mcp.issue_opencode_token(user.id, "contract-tool")
    headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "ControlDeck"}

    def use(threshold: int):
        # 生きている設定そのものを触る。差し替えると token を検証する側の設定まで
        # 変わってしまい、見たいものと関係の無いところで 401 になる。
        monkeypatch.setattr(
            get_config().addons, "agent_tool_contract_threshold", threshold,
        )

    use(0)
    eager = local_client.get("/api/v1/addons/agent-mcp/tools", headers=headers).json()["tools"]
    assert agent_mcp.CONTRACT_TOOL not in [tool["name"] for tool in eager]
    assert "operations" in json.dumps(eager, ensure_ascii=False)
    # 出していないうちは呼べない。
    refused = local_client.post(
        "/api/v1/addons/agent-mcp/call", headers=headers,
        json={"name": agent_mcp.CONTRACT_TOOL, "arguments": {"name": "media.scene.create"}},
    )
    assert refused.status_code == 404

    use(4000)
    lazy = local_client.get("/api/v1/addons/agent-mcp/tools", headers=headers).json()["tools"]
    assert agent_mcp.CONTRACT_TOOL in [tool["name"] for tool in lazy]
    assert "operations" not in json.dumps(lazy, ensure_ascii=False)

    fetched = local_client.post(
        "/api/v1/addons/agent-mcp/call", headers=headers,
        json={"name": agent_mcp.CONTRACT_TOOL, "arguments": {"name": "media.scene.create"}},
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["inputSchema"]["properties"]["operations"]["items"]["enum"] == ["a" * 4000]

    # 呼べない道具の契約は取れない。一覧に出る範囲と同じ範囲しか見せない。
    missing = local_client.post(
        "/api/v1/addons/agent-mcp/call", headers=headers,
        json={"name": agent_mcp.CONTRACT_TOOL, "arguments": {"name": "media.nope"}},
    )
    assert missing.status_code == 404


def test_a_long_tool_result_is_trimmed_and_the_whole_thing_is_left_in_a_file(tmp_path, monkeypatch):
    """道具の結果は、そのまま載せると文脈を食い尽くしうる。

    実測: 262,142 トークンで打ち止めた会話の内訳は道具の結果が 48.1% で最大。
    bridge の上限は 1 MB しか無く、1 件で 30 万トークン——文脈の丸ごとぶんを
    一度に食える形だった。50 枚の batch や長い書き起こしが現にそこへ近づく。

    削るときに頭で切ると JSON が壊れて読めなくなる。形は保ったまま、並びと
    長い文字列だけを刈り、全文は file へ落として grep で読ませる。
    """
    import importlib

    bridge = importlib.import_module("app.integrations.opencode.addon_mcp_bridge")
    monkeypatch.setenv(bridge.RESULT_DIR_ENV, str(tmp_path / "results"))

    short = {"job_id": "abc", "asset_id": "asset_1"}
    inline = bridge._tool_result("media.generate", short)
    assert json.loads(inline["content"][0]["text"]) == short
    assert inline["structuredContent"] == short
    assert not list((tmp_path / "results").glob("*.json")) if (tmp_path / "results").exists() else True

    value = {
        "job_id": "job_long",
        "output": {
            "items": [{"index": i, "status": "succeeded", "asset_id": f"asset_{i}"} for i in range(50)],
            "log": "x" * 20000,
        },
    }
    trimmed = bridge._tool_result("media.generate.batch", value)
    text = trimmed["content"][0]["text"]
    assert len(text) < bridge.RESULT_INLINE_LIMIT + 500
    # 形は残る。job_id と最初の数件の成否は読める。
    assert trimmed["structuredContent"]["job_id"] == "job_long"
    items = trimmed["structuredContent"]["output"]["items"]
    assert items[0] == {"index": 0, "status": "succeeded", "asset_id": "asset_0"}
    assert "残り 45 件" in items[-1]
    assert "20000 文字" in trimmed["structuredContent"]["output"]["log"]

    # 全文は file に残り、どこに在るかを結果そのものが名指しする。
    files = list((tmp_path / "results").glob("*.json"))
    assert len(files) == 1
    assert str(files[0]) in text
    assert json.loads(files[0].read_text(encoding="utf-8")) == value

    # 契約を取りに来る道具だけは削らない。削ると、契約を一覧から外した意味が
    # 無くなる——読ませるために取りに来させているのに、読める形で渡らない。
    # 実測で media.scene.create の契約は 18,838 文字あり、上限に掛かっていた。
    verbatim = bridge._tool_result("control_deck.tool_contract", value)
    assert json.loads(verbatim["content"][0]["text"]) == value
    assert verbatim["structuredContent"] == value

    # 置き場が無い環境でも失敗にしない。載る分は削った側で成立している。
    monkeypatch.delenv(bridge.RESULT_DIR_ENV)
    without = bridge._tool_result("media.generate.batch", value)
    assert "全文は残っていない" in without["content"][0]["text"]


def test_the_result_directory_is_readable_without_asking(monkeypatch, tmp_path):
    """落とした全文を読むのに確認が入ると、削った意味が無くなる。"""
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path)
    allowed = provider._allowed_directories()
    root = str((tmp_path / "integrations" / "opencode" / "tool-results").resolve())
    assert allowed[f"{root}/*"] == "allow"
    assert allowed[f"{root}/**"] == "allow"


def test_the_session_is_told_how_to_spend_its_context(monkeypatch, tmp_path):
    """文脈の使い方を system prompt へ足す。

    実測（262,142 トークンで打ち止めた会話）の内訳は道具の結果が 48.1% で、
    その中身は「探しものが在るかを見るための全文読み」と、絞らずに受け取った
    コマンドの出力だった。読む前に当たりを付けるだけで桁が変わる。

    OpenCode の instructions は system prompt へ足される（実測: 目印を書いた
    file を渡し、ローカルモデルがその語を答えた）。プロジェクト側の AGENTS.md は
    そのまま残る。
    """
    from app.integrations.opencode import provider

    notes = Path(provider.__file__).with_name("agent-notes.md")
    assert notes.exists(), "文脈の使い方を書いたものが無い"
    body = notes.read_text(encoding="utf-8")
    assert "grep" in body and "batch" in body and "control_deck.tool_contract" in body

    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "_skill_paths", lambda: [])
    monkeypatch.setattr(provider, "_allowed_directories", lambda: {})
    config = provider._runtime_config("job-notes", "http://127.0.0.1:1/v1", "model")
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["instructions"] == [str(notes)]


def test_the_contract_description_is_published_once_not_twice(monkeypatch):
    """契約の説明を、道具の説明と schema の両方に載せない。

    _agent_tool_description は契約の top-level description を道具の説明へ
    持ち上げる。schema にも残したままだったので、同じ本文が一つの道具定義に
    二度載っていた（実測: 道具 24 個で 2,608 トークンぶん）。

    落とすのは top-level だけである。引数ごとの説明は持ち上げていないので残す。
    """
    from app.addons import execution

    monkeypatch.setattr(
        execution, "discover",
        lambda kind, permissions: [{
            "addon_id": "media-forge", "id": "media.inspect",
            "label": "Read back what a finished media asset is",
            "schema_path": "/schemas/asset-reference.json",
        }],
    )

    async def schema(addon_id, contribution_id, *, permissions):
        return {
            "type": "object",
            "additionalProperties": False,
            "description": "Read back what a finished asset is. It does not change anything.",
            "properties": {"asset_id": {"type": "string", "description": "The asset to read."}},
        }

    monkeypatch.setattr(execution, "agent_schema", schema)
    tool = asyncio.run(execution.agent_mcp_tools({"workflows.run"}))[0]

    assert "It does not change anything" in tool["description"]
    assert "description" not in tool["inputSchema"]
    # 引数の説明は残る。こちらは持ち上げていない。
    assert tool["inputSchema"]["properties"]["asset_id"]["description"] == "The asset to read."
    assert json.dumps(tool, ensure_ascii=False).count("It does not change anything") == 1


def test_the_session_declares_the_window_so_compaction_can_fire(monkeypatch, tmp_path):
    """窓の大きさを宣言しないと、自動圧縮は一度も動かない。

    OpenCode は limit.context が 0 の間は発火しないと決めている。provider が
    モデルを宣言するとき limit を書いていなかったので、既定が auto: true でも
    先回りの圧縮は起きていなかった（実測: この環境の 793 session のうち先回りは
    0 件、頂点は 262,142 tokens で打ち止め）。

    input も併せて宣言する。OpenCode は input がある場合だけ compaction.reserved
    を見る作りで、context だけだと予備は返答の上限（既定 32,000）に固定される。
    """
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "_skill_paths", lambda: [])
    monkeypatch.setattr(provider, "_allowed_directories", lambda: {})
    monkeypatch.setattr(
        provider, "_model_limits",
        lambda model: {"context": 65536, "output": 8192, "input": 57344},
    )
    config = provider._runtime_config("job-window", "http://127.0.0.1:1/v1", "Qwen3.8-27B")
    payload = json.loads(config.read_text(encoding="utf-8"))
    model = payload["provider"]["controldeck"]["models"]["Qwen3.8-27B"]

    assert model["limit"] == {"context": 65536, "output": 8192, "input": 57344}
    compaction = payload["compaction"]
    assert compaction["prune"] is True
    assert compaction["reserved"] == provider.OPENCODE_COMPACTION_RESERVED
    # 発火点 = limit.input - reserved。畳んだ直後の余裕を残すため、直近に残す量は
    # OpenCode の既定（発火点の 25% = 13,312）より小さくしてある。
    threshold = model["limit"]["input"] - compaction["reserved"]
    assert compaction["preserve_recent_tokens"] < threshold // 4


def test_the_window_comes_from_the_instance_that_serves_the_model(monkeypatch):
    """窓の大きさは llama の instance が持っている。決め打ちにしない。"""
    from app.integrations.opencode import provider
    from app.models_mgmt import llama

    monkeypatch.setattr(
        llama, "list_instances",
        lambda: [{"alias": "other", "ctx_size": 8192},
                 {"alias": "Qwen3.8-27B", "ctx_size": 65536}],
    )
    limits = provider._model_limits("Qwen3.8-27B")
    assert limits == {"context": 65536, "output": 8192, "input": 65536 - 8192}
    # 小さい窓では、返答の上限も窓に合わせて縮む。8,192 のまま渡すと入力が
    # 窓の半分しか使えなくなる。
    assert provider._model_limits("other") == {"context": 8192, "output": 2048, "input": 6144}
    # 知らないモデルは宣言しない。誤った窓を渡すより、従来どおり畳まないほうがよい。
    assert provider._model_limits("nope") is None


def test_heavy_addon_tools_move_to_specialist_subagents(monkeypatch, tmp_path):
    """重い道具は主の会話から外し、専門の子に持たせる。

    実測（道具 24 個で 26,692 トークン）: 3D が 15,583（58%）、音が 5,651（21%）、
    絵が 5,236（20%）。3D はコードを書く会話で一度も使われないのに、毎ターン
    載っていた。

    絵は主に残す。よく使い、一発で終わることが多いので、子を起こす往復のほうが
    載せておく 5,236 トークンより高くつく。

    効きめは道具定義だけではない。子の往復は主の文脈に入らず（実測で会話の
    48.1% が道具の結果だった）、子は文脈が短いぶん、MCP が GPU を要求して
    言語モデルが降ろされた後の読み直しも安い。
    """
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "_skill_paths", lambda: [])
    monkeypatch.setattr(provider, "_allowed_directories", lambda: {})
    monkeypatch.setattr(provider, "_model_limits", lambda model: None)
    config = provider._runtime_config("job-agents", "http://127.0.0.1:1/v1", "model")
    agents = json.loads(config.read_text(encoding="utf-8"))["agent"]

    assert agents["sculptor"]["mode"] == "subagent"
    assert agents["sculptor"]["tools"]["controldeck_addons_media_scene_*"] is True
    assert agents["sound"]["tools"]["controldeck_addons_sonic_*"] is True

    build = agents["build"]["tools"]
    # 主は 3D と音を持たない。
    assert build["controldeck_addons_media_scene_*"] is False
    assert build["controldeck_addons_media_job_*"] is False
    assert build["controldeck_addons_sonic_*"] is False
    # 絵は主に残す。外した覚えのないものまで消えていないことを見る。
    assert not any(key.startswith("controldeck_addons_media_generate") for key in build)
    assert not any(key.startswith("controldeck_addons_media_pack") for key in build)

    # 子には何をする係かを書く。書かないと、主はいつ呼べばよいか分からない。
    for name in ("sculptor", "sound"):
        assert len(agents[name]["description"]) > 20
