"""ControlDeck の MCP の Project Lab の道具（control_deck.lab_*）。

目的（何を守るか）:
- 一覧・開始・状態・ログ・停止が、project_lab の既存の関数（runs・service）を呼ぶだけで動く。
- 権限は router.py と同じ（見る = project_lab.view、始める・止める = project_lab.run）。権限の無い道具は一覧にも出さず、呼んでも断る。
- 他人の run は見えない・止められない（管理者 = settings.manage は全員分）。無い run と区別しない。
- 開始・停止は監査に残る。ログは上限（最大 256 KB）で切る。
- web の run は preview の URL（鍵付きの相対パスと、ControlDeck の 127.0.0.1 の絶対 URL）を返す。

根拠: 2026-11-21 利用者の決定（HANDOFF-opencode-dev-environment.md の P2a）。OpenCode が Web・ソフトの動作確認に
Project Lab を使えるようにする。project_lab の関数はモック（本物の systemd は呼ばない）。
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.addons import agent_mcp
from app.database import SessionLocal, get_db
from app.models import AuditLog, ProjectRun, Role, User

BASE = "/api/v1/addons/agent-mcp"


@pytest.fixture
def lab(admin_client, monkeypatch):
    from app.project_lab import runs

    users = {}
    with SessionLocal() as db:
        for username, perms in {
            "lab-op1": ["workflows.run", "project_lab.view", "project_lab.run"],
            "lab-op2": ["workflows.run", "project_lab.view", "project_lab.run"],
            "lab-view": ["workflows.run", "project_lab.view"],
            "lab-none": ["workflows.run"],
        }.items():
            role = db.execute(select(Role).where(Role.name == f"role-{username}")).scalar_one_or_none()
            if role is None:
                role = Role(name=f"role-{username}", permissions_json=json.dumps(perms))
                db.add(role)
                db.commit()
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                user = User(username=username, password_hash="x", role_id=role.id)
                db.add(user)
                db.commit()
            users[username] = user.id
        users["admin"] = db.execute(select(User).where(User.username == "admin")).scalar_one().id
        db.query(ProjectRun).delete()
        db.commit()

    calls = {"start": [], "cancel": []}

    def fake_start(db, *, project_id, profile_id, timeout_seconds, created_by):
        calls["start"].append((project_id, profile_id, timeout_seconds, created_by))
        if profile_id == "busy":
            raise runs.ProjectRunError("同じprojectの実行が既に進行中です")
        row = ProjectRun(project_id=project_id, project_name="Demo", profile_id=profile_id,
                         profile_type="web" if profile_id == "dev" else "cli", status="RUNNING",
                         command_json=json.dumps(["npm", "run", "dev"]), created_by=created_by)
        db.add(row)
        db.commit()
        row.unit_name = f"control-deck-project-run-{row.id}"
        db.commit()
        return row

    def fake_cancel(db, row):
        calls["cancel"].append(row.id)
        row.status = "CANCELED"
        db.commit()
        return row

    monkeypatch.setattr(runs, "start_run", fake_start)
    monkeypatch.setattr(runs, "cancel_run", fake_cancel)
    monkeypatch.setattr(runs, "refresh_run", lambda db, row: row)
    monkeypatch.setattr(runs, "web_preview_ready", lambda row: True)
    monkeypatch.setattr(runs, "run_logs", lambda row: "line\n" * 100_000 + "END secret=***")

    class Manifest:
        profiles = [type("P", (), {"id": "dev", "label": "開発サーバー", "type": "web"})(),
                    type("P", (), {"id": "test", "label": "テスト", "type": "test"})()]

    monkeypatch.setattr(agent_mcp.project_lab, "list_projects",
                        lambda: [{"id": "demo", "name": "Demo", "description": "d"},
                                 {"id": "bare", "name": "Bare", "description": ""}])
    monkeypatch.setattr(agent_mcp.project_lab, "resolve_project", lambda pid: pid)
    monkeypatch.setattr(agent_mcp.project_lab, "_read_manifest",
                        lambda path: (Manifest() if path == "demo" else None, []))

    app = FastAPI()
    app.include_router(agent_mcp.router, prefix="/api/v1")

    def database():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = database
    http = TestClient(app)

    def call(username, name, arguments=None):
        token = agent_mcp.issue_opencode_token(users[username], "lab-test")
        headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "ControlDeck"}
        return http.post(f"{BASE}/call", headers=headers, json={"name": name, "arguments": arguments or {}})

    def tools(username):
        token = agent_mcp.issue_opencode_token(users[username], "lab-test")
        headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "ControlDeck"}
        return [t["name"] for t in http.get(f"{BASE}/tools", headers=headers).json()["tools"]]

    return type("Lab", (), {"call": staticmethod(call), "tools": staticmethod(tools), "calls": calls})


def _audit(action):
    with SessionLocal() as db:
        return db.execute(select(AuditLog).where(AuditLog.action == action)).scalars().all()


def test_listing_shows_only_the_lab_tools_the_user_may_use(lab):
    assert set(agent_mcp.LAB_TOOLS) <= set(lab.tools("lab-op1"))
    view_only = lab.tools("lab-view")
    assert agent_mcp.LAB_RUN_STATUS_TOOL in view_only and agent_mcp.LAB_PROJECTS_TOOL in view_only
    assert agent_mcp.LAB_RUN_START_TOOL not in view_only and agent_mcp.LAB_RUN_STOP_TOOL not in view_only
    assert not set(agent_mcp.LAB_TOOLS) & set(lab.tools("lab-none"))


def test_projects_lists_profiles_with_id_label_type(lab):
    body = lab.call("lab-view", agent_mcp.LAB_PROJECTS_TOOL).json()
    demo = next(p for p in body["projects"] if p["id"] == "demo")
    assert demo["profiles"] == [{"id": "dev", "label": "開発サーバー", "type": "web"},
                                {"id": "test", "label": "テスト", "type": "test"}]
    assert next(p for p in body["projects"] if p["id"] == "bare")["profiles"] == []
    assert lab.call("lab-none", agent_mcp.LAB_PROJECTS_TOOL).status_code == 403


def test_start_returns_preview_urls_and_leaves_an_audit(lab):
    r = lab.call("lab-op1", agent_mcp.LAB_RUN_START_TOOL,
                 {"project_id": "demo", "profile_id": "dev", "timeout_seconds": 120})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "RUNNING" and out["previewReady"] is True
    assert out["previewPath"].startswith(f"/project-view/r/{out['id']}/") and out["previewPath"].endswith("/")
    assert out["previewUrlAbsolute"].startswith("http://127.0.0.1:") and out["previewUrlAbsolute"].endswith(out["previewPath"])
    assert lab.calls["start"][-1][2] == 120
    assert any(a.resource_id == str(out["id"]) and "agent_mcp" in a.metadata_json for a in _audit("project_lab.run.start"))
    cli = lab.call("lab-op2", agent_mcp.LAB_RUN_START_TOOL, {"project_id": "demo", "profile_id": "test"}).json()
    assert "previewUrlAbsolute" not in cli and lab.calls["start"][-1][2] == 600


def test_start_refusals_without_permission_bad_args_and_run_error(lab):
    assert lab.call("lab-view", agent_mcp.LAB_RUN_START_TOOL, {"project_id": "demo", "profile_id": "dev"}).status_code == 403
    assert not lab.calls["start"]
    for bad in ({"project_id": "demo"}, {"project_id": "demo", "profile_id": "dev", "timeout_seconds": 99999},
                {"project_id": "demo", "profile_id": "dev", "extra": 1}):
        assert lab.call("lab-op1", agent_mcp.LAB_RUN_START_TOOL, bad).status_code == 422, bad
    before = len(_audit("project_lab.run.start"))
    r = lab.call("lab-op1", agent_mcp.LAB_RUN_START_TOOL, {"project_id": "demo", "profile_id": "busy"})
    assert r.status_code == 409
    failures = _audit("project_lab.run.start")[before:]
    assert failures and failures[-1].result == "failure"


def test_status_logs_and_stop_work_for_the_owner(lab):
    run_id = lab.call("lab-op1", agent_mcp.LAB_RUN_START_TOOL, {"project_id": "demo", "profile_id": "dev"}).json()["id"]
    assert lab.call("lab-op1", agent_mcp.LAB_RUN_STATUS_TOOL, {"run_id": run_id}).json()["status"] == "RUNNING"
    stopped = lab.call("lab-op1", agent_mcp.LAB_RUN_STOP_TOOL, {"run_id": run_id})
    assert stopped.status_code == 200 and stopped.json()["status"] == "CANCELED"
    assert lab.calls["cancel"] == [run_id]
    assert any(a.resource_id == str(run_id) for a in _audit("project_lab.run.cancel"))


def test_logs_are_cut_to_the_tail_limit(lab):
    run_id = lab.call("lab-op1", agent_mcp.LAB_RUN_START_TOOL, {"project_id": "demo", "profile_id": "test"}).json()["id"]
    default = lab.call("lab-op1", agent_mcp.LAB_RUN_LOGS_TOOL, {"run_id": run_id}).json()
    assert len(default["logs"]) == 32 * 1024 and default["truncated"] and default["logs"].endswith("END secret=***")
    small = lab.call("lab-op1", agent_mcp.LAB_RUN_LOGS_TOOL, {"run_id": run_id, "tail_kb": 1}).json()
    assert len(small["logs"]) == 1024
    biggest = lab.call("lab-op1", agent_mcp.LAB_RUN_LOGS_TOOL, {"run_id": run_id, "tail_kb": 256}).json()
    assert len(biggest["logs"]) == 256 * 1024
    assert lab.call("lab-op1", agent_mcp.LAB_RUN_LOGS_TOOL, {"run_id": run_id, "tail_kb": 257}).status_code == 422


def test_other_users_runs_are_invisible_but_admin_sees_all(lab):
    run_id = lab.call("lab-op1", agent_mcp.LAB_RUN_START_TOOL, {"project_id": "demo", "profile_id": "dev"}).json()["id"]
    for tool in (agent_mcp.LAB_RUN_STATUS_TOOL, agent_mcp.LAB_RUN_LOGS_TOOL, agent_mcp.LAB_RUN_STOP_TOOL):
        assert lab.call("lab-op2", tool, {"run_id": run_id}).status_code == 404, tool
    assert lab.call("lab-op2", agent_mcp.LAB_RUN_STATUS_TOOL, {"run_id": 999999}).status_code == 404
    assert not lab.calls["cancel"]
    assert lab.call("admin", agent_mcp.LAB_RUN_STATUS_TOOL, {"run_id": run_id}).status_code == 200
    assert lab.call("admin", agent_mcp.LAB_RUN_STOP_TOOL, {"run_id": run_id}).status_code == 200
