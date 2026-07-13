from tests.conftest import CSRF_HEADERS


def test_create_app_with_inline_python_code(admin_client):
    r = admin_client.post(
        "/api/v1/apps",
        json={"name": "inline", "application_type": "python_script", "python_path": "/usr/bin/python3", "code": "print('hi')\n"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert r.json()["script_path"].endswith(f"app-{aid}.py")
    # コード読み出し
    r = admin_client.get(f"/api/v1/apps/{aid}/code")
    assert r.status_code == 200
    assert r.json()["managed"] is True
    assert "print('hi')" in r.json()["code"]
    # 編集（コード更新）
    r = admin_client.patch(f"/api/v1/apps/{aid}", json={"code": "print('updated')\n"}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert "updated" in admin_client.get(f"/api/v1/apps/{aid}/code").json()["code"]
    admin_client.delete(f"/api/v1/apps/{aid}", headers=CSRF_HEADERS)


def test_test_run_python(admin_client):
    r = admin_client.post(
        "/api/v1/apps/test-run",
        json={"application_type": "python_script", "python_path": "/usr/bin/python3", "code": "print(2+3)"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["exit_code"] == 0
    assert r.json()["stdout"].strip() == "5"


def test_test_run_shell(admin_client):
    r = admin_client.post(
        "/api/v1/apps/test-run",
        json={"application_type": "shell_script", "code": "echo ok123"},
        headers=CSRF_HEADERS,
    )
    assert r.json()["stdout"].strip() == "ok123"


def test_test_run_captures_error(admin_client):
    r = admin_client.post(
        "/api/v1/apps/test-run",
        json={"application_type": "python_script", "python_path": "/usr/bin/python3", "code": "raise SystemExit(3)"},
        headers=CSRF_HEADERS,
    )
    assert r.json()["exit_code"] == 3
    assert r.json()["ok"] is False


def test_test_run_requires_edit_permission(client):
    client.cookies.clear()
    from tests.conftest import CSRF_HEADERS as H
    # viewer を作って確認
    from app.database import SessionLocal
    from app.models import Role, User
    from app.security.passwords import hash_password
    from sqlalchemy import select
    db = SessionLocal()
    try:
        if not db.execute(select(User).where(User.username == "ro2")).scalar_one_or_none():
            role = db.execute(select(Role).where(Role.name == "viewer")).scalar_one()
            db.add(User(username="ro2", password_hash=hash_password("viewer-pass-123"), role_id=role.id))
            db.commit()
    finally:
        db.close()
    client.post("/api/v1/auth/login", json={"username": "ro2", "password": "viewer-pass-123"}, headers=H)
    r = client.post("/api/v1/apps/test-run", json={"application_type": "shell_script", "code": "echo x"}, headers=H)
    assert r.status_code == 403
    client.cookies.clear()
