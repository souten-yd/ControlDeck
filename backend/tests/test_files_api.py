import io

from tests.conftest import CSRF_HEADERS, _sandbox


def test_roots(admin_client):
    r = admin_client.get("/api/v1/files/roots")
    assert r.status_code == 200
    assert str(_sandbox) in r.json()


def test_mkdir_upload_download_roundtrip(admin_client):
    base = str(_sandbox)
    r = admin_client.post(
        "/api/v1/files/directory", json={"path": f"{base}/docs"}, headers=CSRF_HEADERS
    )
    assert r.status_code == 200

    r = admin_client.post(
        f"/api/v1/files/upload?directory={base}/docs",
        files={"file": ("hello.txt", io.BytesIO("こんにちは".encode()), "text/plain")},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200, r.text

    r = admin_client.get(f"/api/v1/files/list?path={base}/docs")
    names = [e["name"] for e in r.json()["entries"]]
    assert "hello.txt" in names

    r = admin_client.get(f"/api/v1/files/download?path={base}/docs/hello.txt")
    assert r.status_code == 200
    assert r.content.decode() == "こんにちは"

    # 上書きは overwrite=true が必要
    r = admin_client.post(
        f"/api/v1/files/upload?directory={base}/docs",
        files={"file": ("hello.txt", io.BytesIO(b"x"), "text/plain")},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 409


def test_text_edit(admin_client):
    base = str(_sandbox)
    r = admin_client.put(
        "/api/v1/files/text",
        json={"path": f"{base}/note.md", "content": "# メモ\n"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    r = admin_client.get(f"/api/v1/files/text?path={base}/note.md")
    assert r.json()["content"] == "# メモ\n"


def test_rename_copy_move_delete(admin_client):
    base = str(_sandbox)
    admin_client.put(
        "/api/v1/files/text", json={"path": f"{base}/a.txt", "content": "a"}, headers=CSRF_HEADERS
    )
    r = admin_client.patch(
        "/api/v1/files/rename", json={"path": f"{base}/a.txt", "new_name": "b.txt"}, headers=CSRF_HEADERS
    )
    assert r.status_code == 200
    admin_client.post("/api/v1/files/directory", json={"path": f"{base}/dir2"}, headers=CSRF_HEADERS)
    r = admin_client.post(
        "/api/v1/files/copy",
        json={"source": f"{base}/b.txt", "destination_dir": f"{base}/dir2"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    r = admin_client.post(
        "/api/v1/files/move",
        json={"source": f"{base}/b.txt", "destination_dir": f"{base}/dir2"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 409  # 同名が既にある
    r = admin_client.request(
        "DELETE", f"/api/v1/files?path={base}/dir2/b.txt", headers=CSRF_HEADERS
    )
    assert r.status_code == 200


def test_outside_root_rejected(admin_client):
    for path in ("/etc/passwd", "/etc/shadow", str(_sandbox / ".." / "escape")):
        r = admin_client.get(f"/api/v1/files/list?path={path}")
        assert r.status_code in (403, 404), path
        r = admin_client.get(f"/api/v1/files/download?path={path}")
        assert r.status_code in (403, 404), path


def test_symlink_escape_rejected(admin_client):
    import os

    link = _sandbox / "evil-link"
    if not link.exists():
        os.symlink("/etc", link)
    r = admin_client.get(f"/api/v1/files/list?path={link}")
    assert r.status_code == 403


def test_root_itself_not_deletable(admin_client):
    r = admin_client.request("DELETE", f"/api/v1/files?path={_sandbox}", headers=CSRF_HEADERS)
    assert r.status_code == 403


def test_viewer_cannot_write(client):
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "ro", "password": "viewer-pass-123"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    r = client.put(
        "/api/v1/files/text",
        json={"path": f"{_sandbox}/x.txt", "content": "x"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 403  # viewer に files.edit はない
    client.cookies.clear()
