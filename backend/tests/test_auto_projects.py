"""作業場所の自動作成と表示名（Nexarch・AI アシスタント・Project Lab 共通）の試験。

目的: CodeDEV 直下に変わらないフォルダ名で作業場所を自動で作り、利用者に見える名前は
manifest の name だけにして後から書き換えられること、Project Lab から見られること、
Nexarch の「新しい作業場所」と AI アシスタントの会話ごとの作業場所が同じ部品を使うことを守る。

根拠: 利用者の発言（2026-11-27）「AI アシスタントからの OpenCode もそうだが、作業場所は既定の
作業場所に自動で名前を振ってもらって、後で書き換えられるようにし、そこを Project Lab からも
見られればよくないか？」。フォルダ名を変えないのは、OpenCode の会話の履歴がフォルダのパスに
結び付くため（指示書）。CodeDEV の外は realpath で断る（AGENTS.md のパス検証）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from app.commander import accounts, consoles, workdir
from app.integrations.opencode import projects
from app.project_lab import service
from tests.conftest import CSRF_HEADERS


@pytest.fixture
def codedev(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "CodeDEV"
    root.mkdir()
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    return root.resolve()


def _manifest(path: Path) -> dict:
    return json.loads((path / ".controldeck" / "project.json").read_text(encoding="utf-8"))


def test_create_auto_project_folder_name_manifest_and_collision(codedev):
    first = projects.create_auto_project("nexarch", "調査用")
    assert re.fullmatch(r"nexarch-\d{8}-\d{6}(-\d+)?", first["folder"])
    assert first["name"] == "調査用"
    path = Path(first["path"])
    assert path.parent == codedev and (path / ".git").exists()
    manifest = _manifest(path)
    assert manifest["name"] == "調査用" and manifest["created_by"] == "nexarch" and manifest["schemaVersion"] == 1
    # Project Lab の読み込みで有効な manifest になる
    assert service.load_manifest(path).created_by == "nexarch"
    # 同じ秒でも重ならない
    names = {projects.create_auto_project("chat", "")["folder"] for _ in range(3)}
    assert len(names) == 3 and all(n.startswith("chat-") for n in names)
    with pytest.raises(projects.ProjectError):
        projects.create_auto_project("bogus", "x")


def test_default_name_and_name_validation(codedev):
    created = projects.create_auto_project("team", "   ")
    assert re.fullmatch(r"新しい作業場所 \d+/\d+ \d\d:\d\d", created["name"])
    assert projects.default_name(datetime(2026, 11, 27, 15, 30)) == "新しい作業場所 11/27 15:30"
    with pytest.raises(projects.ProjectError):
        projects.create_auto_project("chat", "a\nb")
    with pytest.raises(projects.ProjectError):
        projects.create_auto_project("chat", "あ" * 81)
    assert projects.create_auto_project("chat", "あ" * 80)["name"] == "あ" * 80


def test_set_display_name_keeps_other_fields_and_folder(codedev):
    created = projects.create_auto_project("chat", "元の名前")
    path = Path(created["path"])
    manifest = _manifest(path)
    manifest["description"] = "説明"
    manifest["profiles"] = [{"id": "p", "label": "L", "type": "cli", "command": ["echo"]}]
    (path / ".controldeck" / "project.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = projects.set_display_name(created["folder"], "新しい名前")
    assert result["name"] == "新しい名前" and path.is_dir()
    after = _manifest(path)
    assert after["name"] == "新しい名前" and after["description"] == "説明"
    assert after["profiles"] == manifest["profiles"] and after["created_by"] == "chat"
    assert projects.display_name(path) == "新しい名前"
    with pytest.raises(projects.ProjectError):
        projects.set_display_name(created["folder"], "")
    # manifest が無い既存フォルダにも書ける。無ければフォルダ名
    plain = codedev / "plain"
    plain.mkdir()
    assert projects.display_name(plain) == "plain"
    projects.set_display_name("plain", "表示")
    assert projects.display_name(plain) == "表示" and service.load_manifest(plain).name == "表示"


def test_paths_outside_codedev_are_refused(codedev, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (codedev / "link").symlink_to(outside)
    for bad in ("../outside", str(outside), "link", "..", "a/b", "", str(codedev)):
        with pytest.raises(projects.ProjectError):
            projects.set_display_name(bad, "x")
    assert projects.display_name_if_codedev(outside) is None
    assert not (outside / ".controldeck").exists()


def test_project_lab_rename_api_and_created_by(admin_client, codedev):
    created = projects.create_auto_project("team", "チーム作業")
    folder = created["folder"]
    listed = {p["id"]: p for p in admin_client.get("/api/v1/project-lab/projects").json()}
    assert listed[folder]["name"] == "チーム作業" and listed[folder]["createdBy"] == "team"

    r = admin_client.put(f"/api/v1/project-lab/projects/{folder}/name", json={"name": "改名後"}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    detail = admin_client.get(f"/api/v1/project-lab/projects/{folder}").json()
    assert detail["name"] == "改名後" and detail["id"] == folder and detail["createdBy"] == "team"
    assert (codedev / folder).is_dir()
    assert admin_client.put(f"/api/v1/project-lab/projects/{folder}/name", json={"name": ""}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(f"/api/v1/project-lab/projects/{folder}/name", json={"name": "a\tb"}, headers=CSRF_HEADERS).status_code == 400
    assert admin_client.put("/api/v1/project-lab/projects/nope/name", json={"name": "x"}, headers=CSRF_HEADERS).status_code == 404
    assert admin_client.put(f"/api/v1/project-lab/projects/{folder}/name", json={"name": "x"}).status_code in (401, 403)
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog

    with SessionLocal() as db:
        actions = db.execute(select(AuditLog.action)).scalars().all()
    assert "project_lab.project.rename" in actions


class _FakeManager:
    def __init__(self):
        self.created: list[dict] = []
        self.sessions: list[dict] = []

    def list_sessions(self):
        return list(self.sessions)

    def create_session(self, *, cwd, command, env=None):
        sid = f"s{len(self.created)}"
        self.created.append({"cwd": cwd})
        self.sessions.append({"id": sid, "cwd": cwd, "commander_cli": "claude", "alive": True})
        return {"id": sid, "cwd": cwd}

    def tag_session(self, sid, marks):
        pass

    def capture_text(self, sid, lines):
        return ""

    def kill_session(self, sid):
        pass


def test_nexarch_new_workspace_creates_in_codedev_and_opens_there(admin_client, codedev, monkeypatch):
    fake = _FakeManager()
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)
    monkeypatch.setattr(workdir, "roots", lambda: [codedev.parent])
    body = {"cli": "claude", "account": "default", "count": 1, "label": "", "new_workspace": {"name": "夜の作業"}}
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS, json=body)
    assert r.status_code == 201, r.text
    workspace = r.json()["workspace"]
    assert workspace["name"] == "夜の作業" and workspace["folder"].startswith("nexarch-")
    assert fake.created[0]["cwd"] == workspace["path"] == str(codedev / workspace["folder"])
    assert _manifest(codedev / workspace["folder"])["created_by"] == "nexarch"
    # 一覧のタイルには表示名が出る
    listed = consoles.list_consoles()
    assert listed[0]["commander_workdir_name"] == "夜の作業"
    # cwd も new_workspace も無ければ 422
    bad = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS, json={"cli": "claude", "count": 1})
    assert bad.status_code == 422


def test_nexarch_new_workspace_refused_when_codedev_outside_roots(admin_client, codedev, monkeypatch, tmp_path):
    fake = _FakeManager()
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setattr(workdir, "roots", lambda: [other.resolve()])
    body = {"cli": "claude", "account": "default", "count": 1, "label": "", "new_workspace": {"name": ""}}
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS, json=body)
    assert r.status_code == 400
    assert list(codedev.iterdir()) == [] and fake.created == []


def test_chat_workspace_per_conversation(codedev, monkeypatch):
    """会話ごとに別の作業場所。作った後は _last_code_project が同じ場所を返す（次のターンで使い回す）。"""
    import json as _json

    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.integrations.opencode import provider
    from app.models import ChatMessage, Conversation, utcnow

    monkeypatch.setattr(provider, "get_settings", lambda: {"project_path": ""})
    a = cp._auto_chat_workspace("これは会話のタイトルです" * 10)
    b = cp._auto_chat_workspace("新しい会話")
    assert a and b and a != b and a.startswith("chat-")
    assert projects.display_name(codedev / a) == ("これは会話のタイトルです" * 10)[:40]
    assert re.fullmatch(r"新しい作業場所 .*", projects.display_name(codedev / b))

    with SessionLocal() as db:
        conv = Conversation(id="c" * 32, title="t")
        db.add(conv)
        db.flush()
        db.add(ChatMessage(
            id="m" * 32, conversation_id=conv.id, role="assistant", content="x",
            meta_json=_json.dumps({"project_path": str(codedev / a)}), created_at=utcnow(),
        ))
        db.commit()
        assert cp._last_code_project(db, conv.id) == (a, "")
        assert cp._last_code_project(db, "other") == ("", "")
        db.query(ChatMessage).filter(ChatMessage.conversation_id == conv.id).delete()
        db.delete(db.get(Conversation, conv.id))
        db.commit()


def test_chat_workspace_is_created_even_when_default_project_path_is_set(codedev, monkeypatch, tmp_path):
    """OpenCode の設定の既定の作業場所があっても、AI アシスタントの会話は会話ごとの作業場所を作る（使い回さない）。

    根拠: 2026-11-27 利用者「AI アシスタントからの OpenCode も自動で名前を振って」。本番は既定に
    /data1tb/ControlDeck/app が入っていて、全部の会話が 1 つのフォルダに混ざっていた。
    """
    import app.workflows.chat_persist as cp
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "get_settings", lambda: {"project_path": str(tmp_path)})
    folder = cp._auto_chat_workspace("題")
    assert folder.startswith("chat-") and (codedev / folder).is_dir()
