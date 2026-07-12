import json

from tests.conftest import CSRF_HEADERS


def test_login_requires_csrf_header(client):
    r = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}
    )
    assert r.status_code == 403  # X-Requested-With なし


def test_login_wrong_password(client):
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 401


def test_unauthenticated_access_denied(client):
    client.cookies.clear()
    assert client.get("/api/v1/apps").status_code == 401
    assert client.get("/api/v1/system/overview").status_code == 401
    assert client.get("/api/v1/audit").status_code == 401


def test_login_success_and_me(admin_client):
    r = admin_client.get("/api/v1/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert body["role"] == "administrator"
    assert "apps.start" in body["permissions"]


def test_login_recorded_in_audit(admin_client):
    r = admin_client.get("/api/v1/audit")
    assert r.status_code == 200
    actions = [e["action"] for e in r.json()]
    assert "login" in actions


def test_logout(admin_client):
    r = admin_client.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert admin_client.get("/api/v1/auth/me").status_code == 401


def test_viewer_cannot_edit_apps(client):
    # viewer ロールのユーザーを直接作成
    from app.database import SessionLocal
    from app.models import Role, User
    from app.security.passwords import hash_password
    from sqlalchemy import select

    db = SessionLocal()
    try:
        role = db.execute(select(Role).where(Role.name == "viewer")).scalar_one()
        if not db.execute(select(User).where(User.username == "ro")).scalar_one_or_none():
            db.add(
                User(username="ro", password_hash=hash_password("viewer-pass-123"), role_id=role.id)
            )
            db.commit()
    finally:
        db.close()

    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "ro", "password": "viewer-pass-123"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    assert client.get("/api/v1/apps").status_code == 200
    r = client.post(
        "/api/v1/apps",
        json={"name": "x", "application_type": "python_script"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 403
    assert client.post("/api/v1/system/reboot", headers=CSRF_HEADERS).status_code == 403
    client.cookies.clear()
