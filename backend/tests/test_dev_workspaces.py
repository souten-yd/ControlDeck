"""開発の作業場 2 段目（AI アシスタントの OpenCode の会話から、開発用の箱を選んで・作って、その中で開発させる）。

目的: 作業場（箱）の中で会話サーバー（opencode serve）を、箱の隔離（pod・中継だけに届く・鍵を置かない）を保ったまま
動かす。持ち主だけが入れ、作る・消す・一覧が API から安全にできる（未保存の変更は確認なしで消さない）。
根拠: 2026-11-22 利用者「Claude なしで、AI アシスタントに要望を出せば OpenCode が開発を進められる状態に」。
設計は 2026-11-21 の指示書（段 1 は #532）。podman・systemd は呼ばず（モック）、git は本物を使う。
"""
import json
import subprocess

import pytest
from sqlalchemy import select

from app.maintenance import sandbox, workspaces
from tests.conftest import CSRF_HEADERS

REAL_ENSURE_POD = sandbox.ensure_pod
FAKE_KEY = "production-" + "gateway-key-" + "value"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(sandbox, "_data_dir", lambda: data)
    ports = iter(range(18800, 18900))
    monkeypatch.setattr(sandbox, "_free_port", lambda: next(ports))
    gateways = iter(range(18900, 19000))
    monkeypatch.setattr(sandbox, "_free_gateway_port", lambda: next(gateways))
    serves = iter(range(19000, 19100))
    monkeypatch.setattr(sandbox, "_free_serve_port", lambda: next(serves))
    monkeypatch.setattr(sandbox, "ensure_pod", lambda box, **kw: sandbox.pod_name(box))
    monkeypatch.setattr(sandbox, "ensure_gateway", lambda box: None)
    monkeypatch.setattr(sandbox, "mirror_stop", lambda box: None)
    monkeypatch.setattr(sandbox, "gateway_stop", lambda box: None)
    monkeypatch.setattr(sandbox, "pod_remove", lambda box: None)
    monkeypatch.setattr(sandbox, "_users_with_permission",
                        lambda permission, name="": [name or "admin"])
    monkeypatch.setattr(sandbox, "_owner_user_id", lambda username: 7 if username == "admin" else 8)
    source = tmp_path / "src"
    source.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=source, check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run("-c", "user.email=a@b.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    run("remote", "add", "origin", "https://github.com/example/repo.git")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.setattr(workspaces, "controldeck_repo", lambda: source)
    return source


# ---- serve_port と pod の公開 ----

def test_new_box_gets_a_serve_port_and_old_box_gets_one_on_load(repo):
    box = workspaces_create_box()
    assert box.serve_port == 19000
    meta = json.loads((box.path / "sandbox.json").read_text())
    meta.pop("serve_port")
    (box.path / "sandbox.json").write_text(json.dumps(meta))
    loaded = sandbox.load(box.name)
    assert loaded.serve_port == 19001
    assert json.loads((box.path / "sandbox.json").read_text())["serve_port"] == 19001, "保存して以後は同じポート"


def workspaces_create_box(name="ws1"):
    return sandbox.load(workspaces.create("admin", "controldeck", name)["name"])


def _fake_podman(monkeypatch, label, running):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["pod", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if label[0] is not None else 1, label[0] or "", "")
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, "abc123\n" if running[0] else "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    return calls


def test_pod_publishes_serve_port_and_spec_changes_with_it(repo, monkeypatch):
    box = workspaces_create_box()
    before = sandbox._pod_spec(box)
    calls = _fake_podman(monkeypatch, [None], [False])
    REAL_ENSURE_POD(box)
    create = next(c for c in calls if c[1:3] == ["pod", "create"])
    assert "127.0.0.1:19000:19000" in create
    box.meta["serve_port"] = 19050
    assert sandbox._pod_spec(box) != before, "ポートが変われば pod は作り直し対象"


def test_stale_pod_is_not_recreated_while_containers_run(repo, monkeypatch):
    """目的: 古い箱の pod に動いているコンテナ（ミラー・作業中の OpenCode）があるとき、作り直して落とさない。
    会話サーバーだけが「使えない」エラーになる（安全側）。"""
    box = workspaces_create_box()
    calls = _fake_podman(monkeypatch, ["stale"], [True])
    assert REAL_ENSURE_POD(box) == sandbox.pod_name(box), "今の pod を使う"
    assert not any(c[1:3] in (["pod", "rm"], ["pod", "create"]) for c in calls)
    with pytest.raises(sandbox.SandboxError, match="古い形の pod"):
        REAL_ENSURE_POD(box, need_serve_port=True)
    assert not any(c[1:3] in (["pod", "rm"], ["pod", "create"]) for c in calls)


def test_stale_pod_is_recreated_when_nothing_runs(repo, monkeypatch):
    box = workspaces_create_box()
    calls = _fake_podman(monkeypatch, ["stale"], [False])
    REAL_ENSURE_POD(box, need_serve_port=True)
    kinds = [c[1:3] for c in calls]
    assert kinds.index(["pod", "rm"]) < kinds.index(["pod", "create"])


# ---- 作業場の判定と持ち主 ----

def test_box_for_project_only_matches_a_box_work_tree(repo, tmp_path):
    box = workspaces_create_box()
    assert workspaces.box_for_project(box.repo).name == box.name
    assert workspaces.box_for_project(box.repo / "backend") is None
    assert workspaces.box_for_project(box.path) is None
    assert workspaces.box_for_project(tmp_path / "elsewhere") is None
    assert workspaces.box_for_project(sandbox.root() / "nobox" / "work" / "repo") is None


def test_workspace_chat_is_for_the_owner_only(repo):
    from app.integrations.opencode import chat_server

    box = workspaces_create_box()
    assert chat_server.workspace_box(box.repo, 7).name == box.name
    for other in (8, None):
        with pytest.raises(chat_server.ChatServerError, match="作った人だけ"):
            chat_server.workspace_box(box.repo, other)
    assert chat_server.workspace_box(repo, 8) is None, "作業場でなければ今までどおり"


def test_resolve_project_returns_a_workspace_path_without_importing(repo, monkeypatch):
    import asyncio

    from app.integrations.opencode import chat_server, provider

    box = workspaces_create_box()
    monkeypatch.setattr(provider, "import_project", lambda p: pytest.fail("作業場は取り込まない"))
    assert asyncio.run(chat_server.resolve_project("", str(box.repo))) == box.repo.resolve()


# ---- 会話サーバーの起動 ----

def test_workspace_server_starts_in_the_pod_with_env_file_and_relay_only_config(repo, monkeypatch, tmp_path):
    """目的: 会話サーバーは箱の pod の中（podman --pod）で起き、パスワードは env-file で渡し argv に出さない。
    設定は箱のホームに書かれ、中継を向き、本番のゲートウェイ鍵・MCP トークンを含まない。権限プラグインは箱の中へ写す。"""
    from app.integrations.opencode import chat_server, provider, zen
    from app.models_mgmt import gateway

    monkeypatch.setattr(gateway, "get_api_key", lambda create=False: FAKE_KEY)
    monkeypatch.setattr(zen, "get", lambda: "")
    monkeypatch.setattr(chat_server, "_state_dir", lambda: tmp_path / "state")
    (tmp_path / "state").mkdir()
    binary = tmp_path / "opencode"
    binary.write_text("")
    monkeypatch.setattr(provider, "active_binary", lambda preferred="": ("v2", binary))
    monkeypatch.setattr(chat_server.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    monkeypatch.setattr(chat_server.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    box = workspaces_create_box()
    server = chat_server._start_workspace(box, 7, "k1")
    launch = next(c for c in calls if c[0].endswith("systemd-run"))
    assert "--pod" in launch and launch[launch.index("--pod") + 1] == sandbox.pod_name(box)
    assert "host" not in launch, "host network ではない"
    assert launch[-5:-1] == ["serve", "--hostname", "127.0.0.1", "--port"]
    assert launch[-1] == "19000" and server.port == 19000 == box.serve_port
    env_file = chat_server._workspace_env_file("k1")
    assert launch[launch.index("--env-file") + 1] == str(env_file)
    assert (env_file.stat().st_mode & 0o777) == 0o600
    assert server.password in env_file.read_text()
    assert not any(server.password in part for part in launch), "パスワードは argv に出さない"
    assert server.workspace == box.name and server.project == str(box.repo)
    config_path = box.home / ".config" / "opencode" / "opencode.json"
    text = config_path.read_text()
    assert FAKE_KEY not in text
    assert "http://127.0.0.1:18900/api/v1/llm/v1" in text
    config = json.loads(text)
    copied = box.home / ".controldeck" / "plugins" / "controldeck-permission"
    assert (copied / "index.mjs").is_file()
    assert copied.as_uri() in config["plugins"], "権限プラグインは箱から見える写しを指す"
    assert provider.PERMISSION_PLUGIN.as_uri() not in config["plugins"]
    assert any(p["action"] == "question" and p["effect"] == "deny" for p in config["permissions"])
    assert any(p["effect"] == "ask" and p["action"] == "shell" for p in config["permissions"])
    assert config["model"] == "controldeck/auto"
    assert f"CONTROLDECK_PERMISSION_FILE={server.permission_file}" in launch
    assert server.permission_file.startswith(str(box.home))
    assert chat_server.load("k1").workspace == box.name
    # 止めると env-file と権限のファイルだけ消え、箱のホーム（設定）は残る
    monkeypatch.setattr(chat_server, "_stop_unit", lambda unit: None)
    chat_server.stop("k1")
    assert not env_file.exists() and config_path.is_file() and chat_server.load("k1") is None


def test_workspace_server_refuses_unknown_network_and_reports_old_pod(repo, monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server, provider

    monkeypatch.setattr(chat_server, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "active_binary", lambda preferred="": ("v2", tmp_path / "opencode"))
    monkeypatch.setattr(chat_server.shutil, "which", lambda name: f"/usr/bin/{name}")
    box = workspaces_create_box()
    with pytest.raises(chat_server.ChatServerError, match="network"):
        chat_server._start_workspace(box, 7, "k2", network="none")

    def old_pod(box, **kw):
        raise sandbox.SandboxError("古い形の pod です")

    monkeypatch.setattr(sandbox, "ensure_pod", old_pod)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(chat_server.ChatServerError, match="古い形"):
        chat_server._start_workspace(box, 7, "k3")
    assert not chat_server._workspace_env_file("k3").exists(), "失敗したら env-file を残さない"


# ---- 作業場の作成・一覧・削除 ----

def test_create_uses_origin_main_and_auto_names_and_lists_only_own(repo):
    first = workspaces.create("admin", "controldeck")
    second = workspaces.create("admin", "controldeck")
    import re

    assert re.fullmatch(r"ws-\d{8}-1", first["name"]) and second["name"].endswith("-2")
    assert sandbox.NAME.fullmatch(first["name"])
    assert first["source"] == "controldeck" and first["source_label"] == "ControlDeck 本体"
    assert first["dirty"] is False and first["unpushed"] == 0 and first["branch"] == f"sandbox/{first['name']}"
    assert {w["name"] for w in workspaces.list_for("admin")} == {first["name"], second["name"]}
    assert workspaces.list_for("someone-else") == []
    with pytest.raises(sandbox.SandboxError, match="ありません"):
        workspaces.get(first["name"], "someone-else")


def test_create_from_codedev_requires_a_git_project(repo, tmp_path, monkeypatch):
    from app.integrations.opencode import provider

    root = tmp_path / "CodeDEV"
    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    (root / "plain").mkdir(parents=True)
    git_project = root / "gitproj"
    git_project.mkdir()
    for args in (["init", "-q"], ["-c", "user.email=a@b.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *args], cwd=git_project, check=True, capture_output=True)
    with pytest.raises(sandbox.SandboxError, match="git のリポジトリではない"):
        workspaces.create("admin", "codedev:plain")
    with pytest.raises(sandbox.SandboxError, match="ありません"):
        workspaces.create("admin", "codedev:../x")
    made = workspaces.create("admin", "codedev:gitproj", "gp1")
    assert made["source"] == "codedev:gitproj" and made["source_label"] == "gitproj" and made["name"] == "gp1"
    with pytest.raises(sandbox.SandboxError):
        workspaces.create("admin", "other")


def test_delete_refuses_unsaved_changes_without_confirm(repo):
    name = workspaces.create("admin", "controldeck")["name"]
    box = sandbox.load(name)
    (box.repo / "new.txt").write_text("x")
    with pytest.raises(workspaces.UnsavedChanges) as info:
        workspaces.delete(name, "admin")
    assert info.value.dirty is True and box.path.exists()
    subprocess.run(["git", "add", "-A"], cwd=box.repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "w"], cwd=box.repo, check=True, capture_output=True)
    with pytest.raises(workspaces.UnsavedChanges) as info:
        workspaces.delete(name, "admin")
    assert info.value.dirty is False and info.value.unpushed == 1, "コミット済みでも push 前なら断る"
    workspaces.delete(name, "admin", confirm=True)
    assert not box.path.exists()
    clean = workspaces.create("admin", "controldeck")["name"]
    workspaces.delete(clean, "admin")
    assert not (sandbox.root() / clean).exists(), "変更が無ければ確認なしで消せる"


def test_delete_is_owner_only(repo):
    name = workspaces.create("admin", "controldeck")["name"]
    with pytest.raises(sandbox.SandboxError, match="ありません"):
        workspaces.delete(name, "intruder", confirm=True)
    assert (sandbox.root() / name).exists()


# ---- API ----

@pytest.fixture
def api(admin_client):
    """opencode のルーターは機能が有効なときだけ main に付くので、同じ DB・同じログインで単体のアプリへ付けて使う。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.integrations.opencode.router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app, cookies=admin_client.cookies)
    yield client
    client.close()


def test_workspace_api_list_create_delete_confirm_and_audit(repo, api, monkeypatch):
    from app.database import SessionLocal
    from app.integrations.opencode import chat_server
    from app.models import AuditLog

    stopped = []
    monkeypatch.setattr(chat_server, "stop", lambda key: stopped.append(key))
    r = api.get("/api/v1/opencode/workspaces")
    assert r.status_code == 200 and r.json() == {"workspaces": []}
    r = api.post("/api/v1/opencode/workspaces", json={"source": "controldeck"}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["repo_path"].endswith(f"{made['name']}/work/repo")
    assert api.post("/api/v1/opencode/workspaces", json={"source": "codedev:nothing"},
                             headers=CSRF_HEADERS).status_code == 422
    assert [w["name"] for w in api.get("/api/v1/opencode/workspaces").json()["workspaces"]] == [made["name"]]
    (sandbox.root() / made["name"] / "work" / "repo" / "a.txt").write_text("x")
    r = api.delete(f"/api/v1/opencode/workspaces/{made['name']}", headers=CSRF_HEADERS)
    assert r.status_code == 409 and r.json()["detail"]["dirty"] is True
    assert (sandbox.root() / made["name"]).exists() and not stopped
    r = api.delete(f"/api/v1/opencode/workspaces/{made['name']}?confirm=true", headers=CSRF_HEADERS)
    assert r.status_code == 200 and not (sandbox.root() / made["name"]).exists()
    assert len(stopped) == 1, "会話サーバーも止める"
    assert api.delete("/api/v1/opencode/workspaces/nothing", headers=CSRF_HEADERS).status_code == 422
    with SessionLocal() as db:
        actions = [a for a in db.execute(select(AuditLog.action).where(AuditLog.action.like("opencode.workspace.%"))).scalars()]
    assert "opencode.workspace.create" in actions and "opencode.workspace.delete" in actions


def test_workspace_api_requires_login_and_terminal_permission(repo, api, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.integrations.opencode.router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/opencode/workspaces").status_code in (401, 403)
        assert anonymous.post("/api/v1/opencode/workspaces", json={"source": "controldeck"},
                              headers=CSRF_HEADERS).status_code in (401, 403)
    routes = {(route.path, tuple(sorted(route.methods))): route for route in router.routes if "workspaces" in route.path}
    assert len(routes) == 3
    for route in routes.values():
        assert "terminal.use" in repr(route.dependant.dependencies[0].call.__closure__[0].cell_contents), route.path
