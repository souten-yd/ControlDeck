"""作業場の画面の記録（撮影・操作テストの動画）の一覧・配信・削除（ROADMAP U28・U34）。

目的: 箱の AI が書ける repo の中の記録を、ホストが安全に一覧・配信・削除できること。
- 一覧は `.controldeck/media` と `test-results` の画像・動画だけ、新しい順。symlink（ファイルも階層も）は辿らない。
- 配信は一覧に出た相対パスだけ（`..`・絶対パス・置き場の外・許さない拡張子は断る）。content-type は拡張子で決め、
  nosniff・CSP sandbox を付ける。Range に応じる。大きすぎるファイルは返さない。
- 削除は 1 件・作業場ぶん・全作業場ぶん。持ち主だけ。監査に件数と大きさを残す。取り込み・マージの後に自動で消す設定。
- ターンの終わりに、この回に撮ったものを meta.media へ。provider の Playwright MCP の引数に caps と output-dir が入る。
根拠: 2026-11-23 利用者「スクリーンショットを撮って表示する・操作テストの動画をチャットに出す・容量をとるので後で個別・一斉に消せる」。
podman は呼ばない（モック）。
"""
import os
import time

import pytest

from app.maintenance import sandbox, workspace_media
from tests.conftest import CSRF_HEADERS
from tests.test_dev_workspaces import api, repo, workspaces_create_box  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _put(box, rel, data=PNG, mtime=None):
    path = box.repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def test_list_is_newest_first_and_only_media_in_the_two_roots(repo):  # noqa: F811
    box = workspaces_create_box()
    now = time.time()
    _put(box, ".controldeck/media/old.png", mtime=now - 100)
    _put(box, ".controldeck/media/sub/test.webm", b"v" * 10, mtime=now - 10)
    _put(box, "test-results/a/shot.jpg", mtime=now - 50)
    _put(box, "src/logo.png")                      # 置き場の外
    _put(box, ".controldeck/media/note.txt", b"x")  # 拡張子が違う
    listed = workspace_media.media_list(box)
    assert [i["path"] for i in listed["items"]] == [
        ".controldeck/media/sub/test.webm", "test-results/a/shot.jpg", ".controldeck/media/old.png"]
    assert [i["kind"] for i in listed["items"]] == ["video", "image", "image"]
    assert listed["count"] == 3 and listed["total_size"] == 10 + 2 * len(PNG)
    assert [i["path"] for i in workspace_media.media_list(box, since=now - 60)["items"]][-1] == "test-results/a/shot.jpg"


def test_symlinks_are_neither_listed_nor_followed(repo, tmp_path):  # noqa: F811
    box = workspaces_create_box()
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG)
    folder = tmp_path / "outside"
    folder.mkdir()
    (folder / "x.png").write_bytes(PNG)
    media = box.repo / ".controldeck" / "media"
    media.mkdir(parents=True)
    (media / "link.png").symlink_to(secret)
    (media / "dir").symlink_to(folder)
    assert workspace_media.media_list(box)["items"] == []
    for rel in (".controldeck/media/link.png", ".controldeck/media/dir/x.png"):
        with pytest.raises(workspace_media.MediaNotFound):
            workspace_media.open_file(box, rel)
        with pytest.raises(workspace_media.MediaNotFound):
            workspace_media.delete_one(box, rel)
    assert secret.exists() and (folder / "x.png").exists()
    # repo や work の途中が symlink に差し替えられても開かない
    (box.path / "work" / "real").mkdir()
    (box.path / "work" / "repo").rename(box.path / "work" / "real" / "repo")
    (box.path / "work" / "repo").symlink_to(box.path / "work" / "real" / "repo")
    assert workspace_media.media_list(box)["items"] == []


@pytest.mark.parametrize("rel", ["../x.png", ".controldeck/media/../../x.png", "/etc/passwd", ".controldeck/media/a.txt",
                                 "src/a.png", "", ".controldeck/media//a.png", ".controldeck/media\\a.png", "test-results"])
def test_paths_outside_the_list_are_refused(repo, rel):  # noqa: F811
    box = workspaces_create_box()
    with pytest.raises(workspace_media.MediaNotFound):
        workspace_media.open_file(box, rel)


def test_big_file_is_not_served(repo, monkeypatch):  # noqa: F811
    box = workspaces_create_box()
    _put(box, ".controldeck/media/big.webm", b"v" * 100)
    monkeypatch.setattr(workspace_media, "MAX_FILE_BYTES", 50)
    with pytest.raises(workspace_media.MediaNotFound):
        workspace_media.open_file(box, ".controldeck/media/big.webm")


def test_api_serves_with_safe_headers_and_range(repo, api):  # noqa: F811
    box = workspaces_create_box()
    _put(box, ".controldeck/media/a.png")
    _put(box, ".controldeck/media/t.webm", b"0123456789")
    url = f"/api/v1/opencode/workspaces/{box.name}/media"
    listed = api.get(url).json()
    assert {i["path"] for i in listed["items"]} == {".controldeck/media/a.png", ".controldeck/media/t.webm"}
    image = api.get(f"{url}/file", params={"path": ".controldeck/media/a.png"})
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["content-security-policy"] == "sandbox"
    video = api.get(f"{url}/file", params={"path": ".controldeck/media/t.webm"}, headers={"Range": "bytes=2-5"})
    assert video.status_code == 206 and video.content == b"2345"
    assert video.headers["content-range"] == "bytes 2-5/10" and video.headers["content-type"] == "video/webm"
    assert api.get(f"{url}/file", params={"path": ".controldeck/media/t.webm"}, headers={"Range": "bytes=-3"}).content == b"789"
    assert api.get(f"{url}/file", params={"path": ".controldeck/media/t.webm"}, headers={"Range": "bytes=50-"}).status_code == 416
    assert api.get(f"{url}/file", params={"path": "../x.png"}).status_code == 404
    assert api.get(f"{url}/file", params={"path": ".controldeck/media/none.png"}).status_code == 404


def test_api_delete_one_all_and_audit(repo, api, admin_client):  # noqa: F811
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog

    box = workspaces_create_box()
    for name in ("a.png", "b.png", "c.webm"):
        _put(box, f".controldeck/media/{name}")
    base = f"/api/v1/opencode/workspaces/{box.name}/media"
    one = api.delete(f"{base}/file", params={"path": ".controldeck/media/a.png"}, headers=CSRF_HEADERS)
    assert one.status_code == 200 and one.json() == {"count": 1, "size": len(PNG)}
    assert api.delete(f"{base}/file", params={"path": ".controldeck/media/a.png"}, headers=CSRF_HEADERS).status_code == 404
    assert api.get("/api/v1/opencode/workspaces-media").json()["count"] == 2
    done = api.delete(base, headers=CSRF_HEADERS).json()
    assert done["count"] == 2 and done["size"] == 2 * len(PNG)
    assert workspace_media.media_list(box)["count"] == 0
    with SessionLocal() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.action == "workspace.media.delete")).all()
    assert len(rows) == 2 and "2" in rows[-1].metadata_json and str(2 * len(PNG)) in rows[-1].metadata_json


def test_all_workspaces_summary_and_bulk_delete_only_mine(repo, api):  # noqa: F811
    mine = workspaces_create_box("ws1")
    other = workspaces_create_box("ws2")
    _put(mine, ".controldeck/media/a.png")
    _put(other, "test-results/b.png")
    summary = api.get("/api/v1/opencode/workspaces-media").json()
    assert summary["count"] == 2 and {w["name"] for w in summary["workspaces"]} == {"ws1", "ws2"}
    # 他人の作業場（持ち主を変えた箱）は数えない・消さない
    meta = sandbox._read_meta(other.path)
    meta["owner"] = "someone-else"
    sandbox._save_meta(other.path, meta)
    assert api.get("/api/v1/opencode/workspaces-media").json()["count"] == 1
    assert api.delete("/api/v1/opencode/workspaces-media", headers=CSRF_HEADERS).json()["count"] == 1
    assert (other.repo / "test-results" / "b.png").exists() and not (mine.repo / ".controldeck/media/a.png").exists()
    assert api.get(f"/api/v1/opencode/workspaces/{other.name}/media").status_code == 422
    assert api.delete(f"/api/v1/opencode/workspaces/{other.name}/media", headers=CSRF_HEADERS).status_code == 422
    assert api.get(f"/api/v1/opencode/workspaces/{other.name}/media/file", params={"path": "test-results/b.png"}).status_code == 422


def test_auto_delete_after_adopt_only_when_enabled(repo, api, monkeypatch):  # noqa: F811
    from app.integrations.opencode import provider, router
    from app.maintenance import workspaces

    box = workspaces_create_box()
    monkeypatch.setattr(workspaces, "adopt", lambda *a, **k: {"branch": "b", "pr_url": "", "kind": "branch"})
    for enabled in (False, True):
        _put(box, ".controldeck/media/a.png")
        monkeypatch.setattr(provider, "get_settings", lambda e=enabled: {**provider.DEFAULT_SETTINGS, "workspace_media_auto_delete": e})
        r = api.post(f"/api/v1/opencode/workspaces/{box.name}/adopt", headers=CSRF_HEADERS, json={"confirm": True})
        assert r.status_code == 200, r.text
        assert (workspace_media.media_list(box)["count"] == 0) is enabled
    assert provider.DEFAULT_SETTINGS["workspace_media_auto_delete"] is False
    assert router.WorkflowBody(workspace_media_auto_delete=True).workspace_media_auto_delete is True


def test_opencode_config_writes_media_dir_with_gitignore(repo, monkeypatch):  # noqa: F811
    """出力先は repo/.controldeck/media。git に混ざらない（.gitignore の * ）。ui-verifier に動画の道具を渡す caps が入る。"""
    import json

    from app.integrations.opencode import provider

    box = workspaces_create_box()
    monkeypatch.setattr(provider, "ui_verify_server", lambda job: {
        "type": "local", "enabled": True,
        "command": ["node", "cli.js", "--output-dir", "/x", "--caps=devtools"]})
    text = sandbox.opencode_config(box).read_text()
    command = json.loads(text)["mcp"]["servers"][provider.UI_VERIFY_SERVER]["command"]
    assert command[command.index("--output-dir") + 1] == str(box.repo / ".controldeck" / "media")
    assert (box.repo / ".controldeck" / ".gitignore").read_text() == "*\n"
    _put(box, ".controldeck/media/a.png")
    import subprocess

    status = subprocess.run(["git", "status", "--porcelain"], cwd=box.repo, capture_output=True, text=True).stdout
    assert ".controldeck" not in status


def test_turn_meta_media_lists_only_this_turns_files_up_to_12(admin_client, monkeypatch, tmp_path):
    """この回に撮った記録（ターンの始めより新しいもの）だけを最大 12 件、回答の meta.media に載せる。"""
    from tests.test_opencode_workspace_persist import _fake, _new_conversation, _ok, _send, _wait_done

    server, calls = _fake(monkeypatch, tmp_path, _ok)
    seen = {}

    def fake_list(box, since=None):
        seen["since"] = since
        return {"items": [{"path": f".controldeck/media/{n}.png", "kind": "image", "size": 5, "mtime": time.time()} for n in range(15)],
                "count": 15, "total_size": 75}

    monkeypatch.setattr(workspace_media, "media_list", fake_list)
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    media = final["meta"]["media"]
    assert media["workspace"] == server.workspace and len(media["items"]) == 12
    assert media["items"][0] == {"path": ".controldeck/media/0.png", "kind": "image", "size": 5}
    assert seen["since"] <= time.time()
