"""開発の作業場の「開発対象」と機能（capabilities）。

目的: 作業場を「ControlDeck の改修か」ではなく機能（llm・tools・mirror・gpu）で分け、機能のない作業場では
その機能が本当に使えないことを保つ（画面で隠すだけにしない）。
  - 開発対象 app / llm_app / controldeck の既定の機能と取り込み先、元との組み合わせの検査
  - 権限: controldeck と gpu の作業場の作成は settings.manage、それ以外は terminal.use。監査に target と capabilities
  - 強制: 中継（llm 無しで models・chat・load が 403、tools 無しで MCP が 403）・箱の環境の OPENAI_*・
    モデルの順番からローカルが消える・mirror 無しで mirror-start を断る・adopt の branch / pr の振り分け
  - 古い作業場（capabilities 無し）は今までどおり（本体なら controldeck、それ以外は llm_app 相当）
根拠: 2026-11-22 利用者「汎用的にするには、ControlDeck の改修かどうかではなく、機能的な改修対象によって権限を分ける方がよい」
（権限と隔離に関わる L2）。中継・podman・systemd は呼ばない（モック）、git は本物。
"""
import asyncio
import json
import subprocess

import httpx
import pytest
from sqlalchemy import select

from app.maintenance import sandbox, workspaces
from app.maintenance import sandbox_gateway as gw
from tests.conftest import CSRF_HEADERS
from tests.test_sandbox_gateway import _resp
from tests.test_dev_workspace_adopt import external_factory, git, run_check  # noqa: F401
from tests.test_dev_workspaces import api, repo  # noqa: F401  (fixture)

API = gw.API


def _make(source="controldeck", name="t1", **kw):
    return sandbox.load(workspaces.create("admin", source, name, **kw)["name"])


# ---- 開発対象と機能 ----

def test_presets_give_capabilities_and_adopt_and_default_target_follows_source(repo):
    app = workspaces.create("admin", "controldeck", "a1", target="app")
    assert app["capabilities"] == ["tools"] and app["adopt"] == "branch" and app["target"] == "app"
    llm = workspaces.create("admin", "controldeck", "a2", target="llm_app")
    assert llm["capabilities"] == ["llm", "tools"] and llm["adopt"] == "branch"
    cd = workspaces.create("admin", "controldeck", "a3", target="controldeck")
    assert cd["capabilities"] == ["llm", "tools", "mirror"] and cd["adopt"] == "pr" and cd["target_label"] == "本体"
    assert workspaces.create("admin", "controldeck", "a4")["target"] == "controldeck", "省くと今までどおり"


def test_combination_checks_controldeck_and_mirror_only_for_controldeck_source(repo, tmp_path, monkeypatch):
    from app.integrations.opencode import provider

    root = tmp_path / "CodeDEV"
    (root / "proj").mkdir(parents=True)
    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    git(root / "proj", "init", "-q", "-b", "main")
    git(root / "proj", "commit", "-q", "--allow-empty", "-m", "init")
    with pytest.raises(sandbox.SandboxError, match="ControlDeck 本体"):
        workspaces.create("admin", "codedev:proj", "c1", target="controldeck")
    with pytest.raises(sandbox.SandboxError, match="mirror"):
        workspaces.create("admin", "codedev:proj", "c2", target="llm_app", capabilities=["llm", "mirror"])
    with pytest.raises(sandbox.SandboxError, match="開発対象"):
        workspaces.create("admin", "controldeck", "c3", target="nothing")
    with pytest.raises(sandbox.SandboxError, match="知らない機能"):
        workspaces.create("admin", "controldeck", "c4", capabilities=["root"])
    assert not (sandbox.root() / "c1").exists(), "断ったら箱を作らない"
    plain = workspaces.create("admin", "codedev:proj", "c5")
    assert plain["target"] == "llm_app" and plain["capabilities"] == ["llm", "tools"]
    over = workspaces.create("admin", "codedev:proj", "c6", target="app", capabilities=["llm"])
    assert over["capabilities"] == ["llm"] and over["adopt"] == "branch"


def test_required_permission_is_manage_for_controldeck_and_gpu_only():
    assert workspaces.required_permission("controldeck", ["tools"]) == "settings.manage"
    assert workspaces.required_permission("app", ["tools", "gpu"]) == "settings.manage"
    assert workspaces.required_permission("llm_app", ["llm", "tools"]) == "terminal.use"
    assert workspaces.required_permission("app", []) == "terminal.use"


def test_old_workspace_without_capabilities_behaves_as_before(repo):
    made = workspaces.create("admin", "controldeck", "old1")
    meta = json.loads(sandbox._meta_path("old1").read_text())
    for key in ("capabilities", "target", "adopt"):
        meta.pop(key)
    sandbox._meta_path("old1").write_text(json.dumps(meta))
    box = sandbox.load(made["name"])
    assert box.target == "controldeck" and box.adopt_mode == "pr" and box.capabilities == {"llm", "tools", "mirror"}
    meta["workspace_source"] = "codedev:x"
    sandbox._meta_path("old1").write_text(json.dumps(meta))
    box = sandbox.load("old1")
    assert box.target == "llm_app" and box.adopt_mode == "branch" and box.capabilities == {"llm", "tools"}


def test_tampered_capabilities_in_meta_are_refused(repo):
    _make(name="tp1")
    meta = json.loads(sandbox._meta_path("tp1").read_text())
    for key, value in (("capabilities", ["llm", "root"]), ("capabilities", "llm"), ("target", "x"), ("adopt", "push")):
        sandbox._meta_path("tp1").write_text(json.dumps({**meta, key: value}))
        with pytest.raises(sandbox.SandboxError, match="改ざん"):
            sandbox.load("tp1")


# ---- API の権限・監査 ----

def test_api_permissions_audit_and_listing(repo, api, monkeypatch):
    from app.database import SessionLocal
    from app.models import AuditLog
    from app.security import deps

    url = "/api/v1/opencode/workspaces"
    monkeypatch.setattr(deps, "user_permissions", lambda user: {"terminal.use"})
    for body in ({"source": "controldeck"}, {"source": "controldeck", "target": "controldeck"},
                 {"source": "controldeck", "target": "app", "capabilities": ["tools", "gpu"]}):
        r = api.post(url, json=body, headers=CSRF_HEADERS)
        assert r.status_code == 403 and "settings.manage" in r.json()["detail"], body
    assert api.get(url).json()["workspaces"] == [], "権限が無ければ箱を作らない"
    r = api.post(url, json={"source": "controldeck", "target": "llm_app", "name": "lm1"}, headers=CSRF_HEADERS)
    assert r.status_code == 201 and r.json()["target"] == "llm_app"
    assert api.post(url, json={"source": "controldeck", "target": "app"}, headers=CSRF_HEADERS).status_code == 201
    assert api.post(url, json={"source": "controldeck", "target": "zzz"}, headers=CSRF_HEADERS).status_code == 422
    monkeypatch.setattr(deps, "user_permissions", lambda user: {"terminal.use", "settings.manage"})
    r = api.post(url, json={"source": "controldeck", "target": "controldeck", "name": "cd1"}, headers=CSRF_HEADERS)
    assert r.status_code == 201 and r.json()["capabilities"] == ["llm", "tools", "mirror"]
    r = api.post(url, json={"source": "controldeck", "target": "app", "capabilities": ["tools", "gpu"], "name": "gp1"},
                 headers=CSRF_HEADERS)
    assert r.status_code == 201 and "gpu" in r.json()["capabilities"], "gpu は受け付ける（P4 は未実装で何も開かない）"
    listed = {w["name"]: w for w in api.get(url).json()["workspaces"]}
    assert listed["cd1"]["target"] == "controldeck" and listed["lm1"]["capabilities"] == ["llm", "tools"]
    with SessionLocal() as db:
        rows = list(db.execute(select(AuditLog).where(AuditLog.action == "opencode.workspace.create")).scalars())
    metas = {row.resource_id: json.loads(row.metadata_json) if isinstance(row.metadata_json, str) else row.metadata_json
             for row in rows}
    assert metas["cd1"]["target"] == "controldeck" and metas["cd1"]["capabilities"] == ["llm", "tools", "mirror"]
    assert metas["lm1"]["target"] == "llm_app"


# ---- 中継の強制 ----

def _client(**kwargs):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return _resp(chunks=[json.dumps({"tools": [{"name": "control_deck.lab_projects"}], "ok": True}).encode()])

    app = gw.create_app(name="b", upstream="http://prod.invalid", transport=httpx.MockTransport(handler),
                        mcp_token="tok", **kwargs)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay"), seen


def test_relay_without_llm_denies_models_chat_and_load_but_keeps_tools():
    async def go():
        client, seen = _client(allow_llm=False, allow_tools=True, allow_load=True)
        async with client as c:
            assert (await c.get(f"{API}/models")).status_code == 403
            assert (await c.post(f"{API}/chat/completions", json={"model": "auto"})).status_code == 403
            assert (await c.post(f"{API}/models/m1/load")).status_code == 403, "allow_load でも llm が無ければ通さない"
            assert (await c.get("/api/v1/addons/agent-mcp/tools")).status_code == 200
            assert (await c.post("/api/v1/addons/agent-mcp/call", json={"name": "control_deck.lab_projects"})).status_code == 200
            health = (await c.get("/healthz")).json()
            assert health["llm"] is False and health["tools"] is True
        assert not any(p.startswith(API) for p in seen), "上流へ出ていない"
    asyncio.run(go())


def test_relay_without_tools_denies_mcp_but_keeps_llm():
    async def go():
        client, seen = _client(allow_llm=True, allow_tools=False)
        async with client as c:
            assert (await c.get(f"{API}/models")).status_code == 200
            assert (await c.get("/api/v1/addons/agent-mcp/tools")).status_code == 403
            assert (await c.post("/api/v1/addons/agent-mcp/call", json={"name": "control_deck.lab_projects"})).status_code == 403
            health = (await c.get("/healthz")).json()
            assert health["tools"] is False and health["mcp"] is False
        assert not any("agent-mcp" in p for p in seen)
    asyncio.run(go())


def test_relay_cli_flags_decide_the_capabilities(monkeypatch):
    captured = {}
    monkeypatch.setattr(gw, "create_app", lambda **kw: captured.update(kw) or object())
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    gw.main(["--name", "n", "--port", "18999", "--upstream", "http://x"])
    assert captured["allow_llm"] is False and captured["allow_tools"] is False, "引数が無ければ閉じる"
    gw.main(["--name", "n", "--port", "18999", "--upstream", "http://x", "--allow-llm", "--allow-tools"])
    assert captured["allow_llm"] is True and captured["allow_tools"] is True


def test_ensure_gateway_passes_capabilities_and_restarts_a_mismatched_relay(repo, monkeypatch):
    from app.addons import agent_mcp

    monkeypatch.setattr(sandbox, "_owner_user_id", lambda username: 7)
    monkeypatch.setattr(agent_mcp, "issue_opencode_token", lambda uid, cid, **kw: "tok")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    from tests.test_dev_sandbox import REAL_ENSURE_GATEWAY

    cases = [("app", ["--allow-tools"], ["--allow-llm"]),
             ("llm_app", ["--allow-llm", "--allow-tools"], []),
             ("controldeck", ["--allow-llm", "--allow-tools"], [])]
    boxes = {target: _make(name=f"gw-{target.replace('_', '')}", target=target) for target, _, _ in cases}
    for target, expect, absent in cases:
        box = boxes[target]
        calls = []
        monkeypatch.setattr(sandbox, "_gateway_healthy",
                            lambda port, calls=calls, **kw: any(c[0].endswith("systemd-run") for c in calls))
        monkeypatch.setattr(sandbox.subprocess, "run",
                            lambda argv, calls=calls, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
        REAL_ENSURE_GATEWAY(box)
        start = next(c for c in calls if c[0].endswith("systemd-run"))
        assert all(flag in start for flag in expect) and not any(flag in start for flag in absent), target


def test_gateway_health_must_match_the_boxes_capabilities(monkeypatch):
    import io

    import urllib.request

    def serve(payload):
        class R(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return lambda url, timeout=0: R(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", serve({"mcp": True, "llm": True, "tools": True}))
    assert sandbox._gateway_healthy(1, llm=True, tools=True)
    assert not sandbox._gateway_healthy(1, llm=False, tools=True), "機能が違う中継は作り直す"
    monkeypatch.setattr(urllib.request, "urlopen", serve({"mcp": True}))   # 印の無い古い中継は全部許す
    assert sandbox._gateway_healthy(1, llm=True, tools=True)
    assert not sandbox._gateway_healthy(1, llm=False, tools=True)
    assert not sandbox._gateway_healthy(1, llm=True, tools=False)
    monkeypatch.setattr(urllib.request, "urlopen", serve({"mcp": False, "llm": True, "tools": False}))
    assert sandbox._gateway_healthy(1, llm=True, tools=False), "道具の無い箱は MCP のトークンが無くてよい"


# ---- 箱の環境・モデルの順番・mirror ----

def test_llm_box_gets_standard_openai_env_and_others_do_not(repo):
    llm = _make(name="e1", target="llm_app")
    argv = sandbox.podman_argv(llm, ["true"])
    assert f"OPENAI_BASE_URL=http://127.0.0.1:{llm.gateway_port}/api/v1/llm/v1" in argv
    assert "OPENAI_API_KEY=sandbox" in argv
    plain = _make(name="e2", target="app")
    argv = sandbox.podman_argv(plain, ["true"])
    assert not any(part.startswith("OPENAI_") for part in argv)
    assert not any("production" in part for part in argv)


def test_box_without_llm_drops_local_models_and_conversation_is_refused_without_external(repo, monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server, provider, zen

    monkeypatch.setattr(zen, "get", lambda: "")
    plain = _make(name="m1", target="app")
    assert sandbox.model_chain(plain) == [], "外部のキーが無ければ空"
    monkeypatch.setattr(zen, "get", lambda: "zen-key-for-test")
    assert sandbox.model_chain(plain) == ["opencode-go/glm-5.3", "opencode/hy4-flash-free", "opencode/deepseek-v4.1-flash-free"]
    plain.meta["model_chain"] = ["controldeck/auto", "local"]
    assert sandbox.model_chain(plain) == [], "指定していてもローカルは外す"
    assert sandbox.reviewer_model_for(plain, "opencode/hy4-flash-free") is None
    assert sandbox.model_chain(_make(name="m2", target="llm_app"))[-1] == "controldeck/auto"
    # 会話サーバーは「使えるモデルがありません」で断る
    monkeypatch.setattr(zen, "get", lambda: "")
    monkeypatch.setattr(chat_server, "_state_dir", lambda: tmp_path / "state")
    binary = tmp_path / "opencode"
    binary.write_text("")
    monkeypatch.setattr(provider, "active_binary", lambda preferred="": ("v2", binary))
    monkeypatch.setattr(chat_server.shutil, "which", lambda name: f"/usr/bin/{name}")
    plain.meta.pop("model_chain")
    with pytest.raises(chat_server.ChatServerError, match="使えるモデルがありません"):
        chat_server._start_workspace(sandbox.load(plain.name), 7, "k9")


def test_opencode_config_has_no_controldeck_tools_without_the_tools_capability(repo):
    llm_only = _make(name="o1", target="app", capabilities=["llm"])
    config = json.loads(sandbox.opencode_config(llm_only).read_text())
    assert "controldeck_addons" not in json.dumps(config.get("mcp") or {})
    with_tools = _make(name="o2", target="app")
    assert "controldeck_addons" in json.dumps(json.loads(sandbox.opencode_config(with_tools).read_text()))


def test_mirror_start_is_refused_without_the_mirror_capability(repo, monkeypatch):
    boxes = [_make(name=f"mi-{target.replace('_', '')}", target=target) for target in ("app", "llm_app")]
    cli = _make(name="mi3", target="controldeck", capabilities=["tools"])
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: pytest.fail("mirror を起こそうとした"))
    for box in boxes:
        with pytest.raises(sandbox.SandboxError, match="複製"):
            sandbox.mirror_start(box)
    with pytest.raises(sandbox.SandboxError, match="複製"):
        sandbox.mirror_start(cli)


# ---- 取り込み先 ----

def test_adopt_branch_for_non_controldeck_target_never_opens_a_pr(repo, monkeypatch, external_factory):  # noqa: F811
    box = _make(name="ab1", target="llm_app")
    external = external_factory(box)
    (box.repo / "x.txt").write_text("x")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "x")
    run_check(box.name)
    result = workspaces.adopt(box.name, "admin")
    assert result["kind"] == "branch" and result["pr_url"] == "" and result["branch"] == "ws/ab1"
    assert git(repo, "rev-parse", "ws/ab1") == git(box.repo, "rev-parse", "HEAD"), "元のリポジトリへのブランチ"
    assert not external["push"] and not external["gh"]


def test_adopt_pr_only_for_controldeck_source_and_branch_never_pushes(repo, monkeypatch, external_factory):  # noqa: F811
    box = _make(name="ab2", target="controldeck")
    external = external_factory(box)
    (box.repo / "y.txt").write_text("y")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "y")
    run_check(box.name)
    assert workspaces.adopt(box.name, "admin")["kind"] == "pr" and external["gh"]
    # meta を書き換えて、元が本体でないのに pr にしても断る（取り込み先の強制）
    other = _make(name="ab3", target="app")
    meta = json.loads(sandbox._meta_path("ab3").read_text())
    meta.update(adopt="pr", workspace_source="codedev:zz")
    sandbox._meta_path("ab3").write_text(json.dumps(meta))
    with pytest.raises(workspaces.AdoptRefused, match="ControlDeck 本体"):
        workspaces.adopt(other.name, "admin")


def test_api_adopt_permission_follows_the_adopt_mode(repo, api, monkeypatch, external_factory):  # noqa: F811
    from app.security import deps

    box = _make(name="ab4", target="llm_app")
    external_factory(box)
    (box.repo / "z.txt").write_text("z")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "z")
    run_check(box.name)
    monkeypatch.setattr(deps, "user_permissions", lambda user: {"terminal.use"})
    r = api.post(f"/api/v1/opencode/workspaces/{box.name}/adopt", json={"confirm": True}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["kind"] == "branch", "branch の取り込みは terminal.use で足りる"
