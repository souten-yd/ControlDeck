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


def test_trash_restore_and_permanent_delete(admin_client):
    base = str(_sandbox)
    path = f"{base}/trash-me.txt"
    admin_client.put("/api/v1/files/text", json={"path": path, "content": "restore me"}, headers=CSRF_HEADERS)

    r = admin_client.request("DELETE", f"/api/v1/files?path={path}", headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert r.json()["trashed"] is True
    trash_id = r.json()["trash_id"]
    assert not (_sandbox / "trash-me.txt").exists()

    rows = admin_client.get("/api/v1/files/trash").json()
    assert any(row["id"] == trash_id and row["original_path"] == path for row in rows)
    r = admin_client.post(f"/api/v1/files/trash/{trash_id}/restore", headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert (_sandbox / "trash-me.txt").read_text() == "restore me"

    r = admin_client.request("DELETE", f"/api/v1/files?path={path}", headers=CSRF_HEADERS)
    trash_id = r.json()["trash_id"]
    assert admin_client.delete(f"/api/v1/files/trash/{trash_id}", headers=CSRF_HEADERS).status_code == 200
    assert all(row["id"] != trash_id for row in admin_client.get("/api/v1/files/trash").json())


def test_resumable_upload_roundtrip_and_offset_guard(admin_client):
    base = str(_sandbox)
    content = b"first chunk-second chunk"
    r = admin_client.post(
        "/api/v1/files/uploads",
        json={"directory": base, "filename": "resumable.bin", "size": len(content)},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    upload_id = r.json()["id"]

    first = content[:11]
    r = admin_client.put(
        f"/api/v1/files/uploads/{upload_id}/chunk?offset=0", content=first, headers=CSRF_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert r.json()["received"] == len(first)
    assert admin_client.get(f"/api/v1/files/uploads/{upload_id}").json()["received"] == len(first)

    # 再送や順序違いでファイルを壊さない
    r = admin_client.put(
        f"/api/v1/files/uploads/{upload_id}/chunk?offset=0", content=b"bad", headers=CSRF_HEADERS,
    )
    assert r.status_code == 403

    r = admin_client.put(
        f"/api/v1/files/uploads/{upload_id}/chunk?offset={len(first)}",
        content=content[len(first):], headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    r = admin_client.post(f"/api/v1/files/uploads/{upload_id}/complete", headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert (_sandbox / "resumable.bin").read_bytes() == content
    admin_client.request("DELETE", f"/api/v1/files?path={base}/resumable.bin&permanent=true", headers=CSRF_HEADERS)


def test_resumable_upload_cancel(admin_client):
    r = admin_client.post(
        "/api/v1/files/uploads",
        json={"directory": str(_sandbox), "filename": "cancel.bin", "size": 3},
        headers=CSRF_HEADERS,
    )
    upload_id = r.json()["id"]
    assert admin_client.delete(f"/api/v1/files/uploads/{upload_id}", headers=CSRF_HEADERS).status_code == 204
    assert admin_client.get(f"/api/v1/files/uploads/{upload_id}").status_code == 404
