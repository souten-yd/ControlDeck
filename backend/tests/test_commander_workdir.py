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


def test_resolve_path_accepts_files_inside_roots_and_blocks_escape(area):
    """目的: 資料の受け渡し（brief E・2026-11-28）でファイルも選べるように、検証つきで実体のパスを返すこと。

    resolve_dir はディレクトリだけなので、資料（ファイル・フォルダ）には resolve_path を使う。
    ルートの外・シンボリックリンクで外へ出る道・ControlDeck の data_dir の中・files の拒否の場所は
    ディレクトリと同じ決まりで断る。
    根拠: 2026-11-28 の依頼（brief E）「許可ルート内 realpath/機密禁止パス/リンク逃げ検証」。
    AGENTS.md の「パスは必ず検証（symlink 脱出防止）」。"""
    root, outside = area
    assert workdir.resolve_path(str(root / "note.txt")) == (root / "note.txt").resolve()
    assert workdir.resolve_path(str(root / "proj")) == (root / "proj").resolve()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    (root / "escape.md").symlink_to(outside / "secret.txt")
    with pytest.raises(PermissionError):
        workdir.resolve_path(str(root / "escape.md"))
    with pytest.raises(FileNotFoundError):
        workdir.resolve_path(str(root / "missing.txt"))
    with pytest.raises(PermissionError):
        workdir.resolve_path(str(data_dir() / "x.txt"))


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


def test_opencode_opens_nexarch_workdir_outside_files_roots(admin_client, area, monkeypatch):
    """Nexarch の作業フォルダ（files の許可ルートの外でも）で OpenCode を開ける。検証済みの実体のパスを渡す。

    根拠: 2026-11-27 利用者「作業フォルダの件対処して」。以前は OpenCode だけ files の許可ルートの外を 400 で断っていた。
    """
    from app.commander import router as commander_router
    from app.integrations.opencode import provider

    root, _ = area
    seen = []

    def fake_tui(*, project_path, owner_user_id=None, model="", config_key="", validated_project=None, **_):
        seen.append(validated_project)
        return "exec /opt/opencode2", project_path

    monkeypatch.setattr(consoles, "manager", FakeManager())
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)
    monkeypatch.setattr(commander_router, "_warm_opencode_llm", lambda: None)
    monkeypatch.setattr(provider, "tui_command", fake_tui)
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS,
                          json={"cli": "opencode", "cwd": str(root / "proj")})
    assert r.status_code == 201, r.text
    assert seen and str(seen[0]) == os.path.realpath(root / "proj")


def test_tui_command_validated_project_must_be_a_real_absolute_dir(tmp_path, monkeypatch):
    """tui_command の validated_project は実体の絶対パスだけ（リンク・相対は断る）。"""
    import pytest
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "active_binary", lambda runtime="": ("v2", Path("/opt/opencode2")))
    monkeypatch.setattr(provider, "get_settings", lambda: {"base_url": "http://x/v1", "model": "auto"})

    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    for bad in (Path("rel"), link):
        with pytest.raises(provider.CodeAgentError, match="実体の絶対パス"):
            provider.tui_command(project_path=str(bad), validated_project=bad)
