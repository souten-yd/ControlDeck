"""Nexarch の作業フォルダの選択（app/commander/workdir.py・/commander/workdir/* API）の試験。

目的: コンソールの作業フォルダに選べるのは commander.workdir_roots（既定 /data1tb・/data2tb）の
中だけで、ルートの外・シンボリックリンクで外へ出る道・ControlDeck の data_dir の中・
files の拒否の場所は、一覧にも出さず、作らせず、コンソールも開かせないこと。
一覧はディレクトリだけ（ファイルは返さない）。

根拠: 利用者の指定（2026-11-27）「作業フォルダのルートは /data1tb, /data2tb として他は不要」。
AGENTS.md の「パスは必ず検証（symlink 脱出防止）」「秘密・DB を見せない」。
files.allowed_roots は Files 画面など他の画面の決まりなので変えず、Nexarch 専用の設定にした。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.commander import accounts, consoles, workdir
from app.config import data_dir, get_config
from app.database import SessionLocal
from app.models import AuditLog
from tests.conftest import CSRF_HEADERS
from tests.test_commander_consoles import FakeManager

API = "/api/v1/commander/workdir"


@pytest.fixture()
def area(monkeypatch, tmp_path):
    """tmp_path をルートにする。外側（outside）と、ルートの中の data_dir も用意する。"""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "proj").mkdir()
    (root / ".hid").mkdir()
    (root / "note.txt").write_text("x")
    monkeypatch.setattr(get_config().commander, "workdir_roots", [str(root), str(tmp_path / "nope")])
    return root, outside


def test_roots_only_existing(admin_client, area):
    root, _ = area
    r = admin_client.get(f"{API}/roots")
    assert r.status_code == 200
    assert r.json() == [str(root.resolve())]


def test_list_directories_only_with_hidden_mark(admin_client, area):
    root, _ = area
    r = admin_client.get(f"{API}/list", params={"path": str(root)})
    assert r.status_code == 200, r.text
    rows = {e["name"]: e for e in r.json()["entries"]}
    assert set(rows) == {"proj", ".hid"}, "ファイルは返さない"
    assert rows[".hid"]["hidden"] is True and rows["proj"]["hidden"] is False


def test_outside_root_is_refused(admin_client, area):
    _, outside = area
    assert admin_client.get(f"{API}/list", params={"path": str(outside)}).status_code == 400
    assert admin_client.get(f"{API}/list", params={"path": "/etc"}).status_code == 400
    assert admin_client.get(f"{API}/list", params={"path": "relative"}).status_code == 400
    r = admin_client.post(f"{API}/directory", headers=CSRF_HEADERS, json={"path": str(outside / "x")})
    assert r.status_code == 400
    assert not (outside / "x").exists()


def test_symlink_escape_is_refused_and_hidden(admin_client, area):
    root, outside = area
    (root / "link").symlink_to(outside)
    # 一覧に出さない
    names = [e["name"] for e in admin_client.get(f"{API}/list", params={"path": str(root)}).json()["entries"]]
    assert "link" not in names
    # 直接指定しても外
    assert admin_client.get(f"{API}/list", params={"path": str(root / "link")}).status_code == 400
    r = admin_client.post(f"{API}/directory", headers=CSRF_HEADERS, json={"path": str(root / "link" / "x")})
    assert r.status_code == 400
    assert not (outside / "x").exists()


def test_data_dir_and_denied_places_are_refused(admin_client, monkeypatch, tmp_path):
    data = Path(data_dir()).resolve()
    monkeypatch.setattr(get_config().commander, "workdir_roots", [str(data.parent)])
    names = [e["name"] for e in admin_client.get(f"{API}/list", params={"path": str(data.parent)}).json()["entries"]]
    assert data.name not in names, "data_dir は一覧に出さない"
    assert admin_client.get(f"{API}/list", params={"path": str(data)}).status_code == 400
    r = admin_client.post(f"{API}/directory", headers=CSRF_HEADERS, json={"path": str(data / "x")})
    assert r.status_code == 400 and not (data / "x").exists()
    # files の拒否の場所（~/.ssh）。HOME を root の中へ向ける
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(get_config().commander, "workdir_roots", [str(home)])
    assert admin_client.get(f"{API}/list", params={"path": str(home / ".ssh")}).status_code == 400
    names = [e["name"] for e in admin_client.get(f"{API}/list", params={"path": str(home)}).json()["entries"]]
    assert ".ssh" not in names


def test_make_directory_audited_and_conflict(admin_client, area):
    root, _ = area
    r = admin_client.post(f"{API}/directory", headers=CSRF_HEADERS, json={"path": str(root / "new")})
    assert r.status_code == 200, r.text
    assert (root / "new").is_dir()
    assert admin_client.post(f"{API}/directory", headers=CSRF_HEADERS,
                             json={"path": str(root / "new")}).status_code == 409
    # 階層は作らない・.. は使えない
    assert admin_client.post(f"{API}/directory", headers=CSRF_HEADERS,
                             json={"path": str(root / "a" / "b")}).status_code == 404
    assert admin_client.post(f"{API}/directory", headers=CSRF_HEADERS,
                             json={"path": str(root / "..")}).status_code == 400
    with SessionLocal() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.action == "commander.workdir.mkdir")).scalars().all()
    assert any(row.resource_id == str((root / "new").resolve()) for row in rows)


def test_create_consoles_validates_with_workdir_roots(admin_client, area, monkeypatch):
    root, outside = area
    fake = FakeManager()
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)
    url = "/api/v1/commander/consoles"
    ok = admin_client.post(url, headers=CSRF_HEADERS, json={"cli": "claude", "cwd": str(root / "proj")})
    assert ok.status_code == 201, ok.text
    assert fake.calls[0]["cwd"] == str((root / "proj").resolve())
    for bad in (str(outside), str(root / "note.txt")):
        assert admin_client.post(url, headers=CSRF_HEADERS, json={"cli": "claude", "cwd": bad}).status_code == 400
    assert admin_client.post(url, headers=CSRF_HEADERS,
                             json={"cli": "claude", "cwd": str(root / "missing")}).status_code == 404


def test_opencode_needs_files_allowed_root(admin_client, area, monkeypatch):
    """作業フォルダのルートの中でも files の許可ルートの外なら、OpenCode は 400 で理由を返す。"""
    from app.commander import router as commander_router

    root, _ = area
    monkeypatch.setattr(consoles, "manager", FakeManager())
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)
    monkeypatch.setattr(commander_router, "_warm_opencode_llm", lambda: None)
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS,
                          json={"cli": "opencode", "cwd": str(root / "proj")})
    assert r.status_code == 400
    assert "files の許可ルート" in r.json()["detail"]
    assert os.path.isdir(root / "proj")
