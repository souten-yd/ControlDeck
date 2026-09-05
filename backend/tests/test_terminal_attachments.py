"""ターミナルへ渡す一時画像の置き場。溜め込まないことを確かめる。"""

from __future__ import annotations

import time

import pytest

from app.terminals import attachments


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


@pytest.fixture()
def store(tmp_path):
    return attachments.AttachmentStore(tmp_path / "shm")


def test_stores_an_image_and_returns_a_readable_path(store):
    stored = store.put(PNG, "image/png")
    assert stored.path.read_bytes() == PNG
    assert stored.path.suffix == ".png"
    # ターミナルへそのまま打ち込むので、引用が要る文字を含めない
    assert " " not in stored.path.name and "'" not in stored.path.name
    assert stored.path.stat().st_mode & 0o077 == 0


def test_rejects_non_images_and_oversized_files(store):
    with pytest.raises(ValueError):
        store.put(PNG, "application/pdf")
    with pytest.raises(ValueError):
        store.put(b"", "image/png")
    with pytest.raises(ValueError):
        store.put(b"x" * (attachments.MAX_ATTACHMENT_BYTES + 1), "image/png")


def test_content_type_decides_the_suffix_not_the_uploaded_name(store):
    assert store.put(PNG, "image/jpeg").path.suffix == ".jpg"
    assert store.put(PNG, "image/webp; charset=binary").path.suffix == ".webp"


def test_expired_entries_are_swept(store, monkeypatch):
    old = store.put(PNG, "image/png")
    monkeypatch.setattr(attachments, "TTL_SECONDS", 0)
    time.sleep(0.01)
    store.put(PNG, "image/png")
    assert not old.path.exists()


def test_oldest_entries_drop_once_the_count_limit_is_passed(store, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_ATTACHMENTS", 3)
    kept = []
    for _ in range(5):
        kept.append(store.put(PNG, "image/png"))
        time.sleep(0.01)
    alive = [entry.path.name for entry in store.list()]
    assert len(alive) == 3
    assert kept[-1].path.name in alive
    assert kept[0].path.name not in alive


def test_oldest_entries_drop_once_the_size_limit_is_passed(store, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_TOTAL_BYTES", len(PNG) * 2)
    first = store.put(PNG, "image/png")
    time.sleep(0.01)
    store.put(PNG, "image/png")
    time.sleep(0.01)
    store.put(PNG, "image/png")
    assert not first.path.exists()


def test_discard_removes_one_entry_and_reports_unknown_ids(store):
    stored = store.put(PNG, "image/png")
    assert store.discard(stored.id) is True
    assert not stored.path.exists()
    assert store.discard(stored.id) is False
    assert store.discard("../escape") is False


def test_a_new_store_does_not_inherit_the_previous_one(tmp_path):
    root = tmp_path / "shm"
    left_over = attachments.AttachmentStore(root).put(PNG, "image/png")
    assert left_over.path.exists()
    attachments.AttachmentStore(root)
    assert not left_over.path.exists()


def test_upload_endpoint_returns_a_path_and_rejects_other_types(admin_client, tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "store", attachments.AttachmentStore(tmp_path / "shm"))
    from tests.conftest import CSRF_HEADERS

    ok = admin_client.post(
        "/api/v1/terminals/attachments",
        files={"file": ("photo.png", PNG, "image/png")},
        headers=CSRF_HEADERS,
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    from pathlib import Path

    assert Path(body["path"]).read_bytes() == PNG
    assert body["size"] == len(PNG)

    listed = admin_client.get("/api/v1/terminals/attachments")
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]

    bad = admin_client.post(
        "/api/v1/terminals/attachments",
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
        headers=CSRF_HEADERS,
    )
    assert bad.status_code == 422

    gone = admin_client.delete(f"/api/v1/terminals/attachments/{body['id']}", headers=CSRF_HEADERS)
    assert gone.status_code == 204
    assert not Path(body["path"]).exists()


def test_upload_requires_authentication(client):
    from tests.conftest import CSRF_HEADERS

    client.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    denied = client.post(
        "/api/v1/terminals/attachments",
        files={"file": ("photo.png", PNG, "image/png")},
        headers=CSRF_HEADERS,
    )
    assert denied.status_code in (401, 403)
    # client は session スコープ。後続のテストのために入り直しておく。
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
        headers=CSRF_HEADERS,
    )
