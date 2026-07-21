from __future__ import annotations

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditLog, Role, User, UserSession
from app.security.passwords import hash_password
from tests.conftest import CSRF_HEADERS


def _role_id(name: str) -> int:
    with SessionLocal() as db:
        return db.execute(select(Role.id).where(Role.name == name)).scalar_one()


def test_user_and_custom_role_lifecycle_revokes_sessions_and_audits(admin_client):
    role_response = admin_client.post(
        "/api/v1/roles",
        json={"name": "test_app_reader", "permissions": ["apps.view"]},
        headers=CSRF_HEADERS,
    )
    assert role_response.status_code == 201, role_response.text
    role_id = role_response.json()["id"]
    user_response = admin_client.post(
        "/api/v1/users",
        json={
            "username": "access-managed-user",
            "display_name": "Managed User",
            "password": "managed-password-123",
            "role_id": role_id,
        },
        headers=CSRF_HEADERS,
    )
    assert user_response.status_code == 201, user_response.text
    user_id = user_response.json()["id"]
    assert "password" not in user_response.json()

    admin_client.cookies.clear()
    login = admin_client.post(
        "/api/v1/auth/login",
        json={"username": "access-managed-user", "password": "managed-password-123"},
        headers=CSRF_HEADERS,
    )
    assert login.status_code == 200
    assert admin_client.get("/api/v1/apps").status_code == 200

    # custom role変更は利用者sessionを即時失効する。
    admin_client.cookies.clear()
    assert admin_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
        headers=CSRF_HEADERS,
    ).status_code == 200
    changed_role = admin_client.patch(
        f"/api/v1/roles/{role_id}", json={"permissions": ["logs.view"]}, headers=CSRF_HEADERS,
    )
    assert changed_role.status_code == 200
    with SessionLocal() as db:
        active = db.execute(
            select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        ).scalars().all()
        assert active == []

    viewer_role = _role_id("viewer")
    updated = admin_client.patch(
        f"/api/v1/users/{user_id}",
        json={"role_id": viewer_role, "new_password": "managed-password-456"},
        headers=CSRF_HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["role_name"] == "viewer"
    assert admin_client.delete(f"/api/v1/roles/{role_id}", headers=CSRF_HEADERS).status_code == 200

    admin_client.cookies.clear()
    assert admin_client.post(
        "/api/v1/auth/login",
        json={"username": "access-managed-user", "password": "managed-password-123"},
        headers=CSRF_HEADERS,
    ).status_code == 401
    assert admin_client.post(
        "/api/v1/auth/login",
        json={"username": "access-managed-user", "password": "managed-password-456"},
        headers=CSRF_HEADERS,
    ).status_code == 200

    admin_client.cookies.clear()
    assert admin_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
        headers=CSRF_HEADERS,
    ).status_code == 200
    disabled = admin_client.patch(
        f"/api/v1/users/{user_id}", json={"is_active": False}, headers=CSRF_HEADERS,
    )
    assert disabled.status_code == 200
    with SessionLocal() as db:
        actions = set(db.execute(
            select(AuditLog.action).where(
                AuditLog.resource_id.in_([str(user_id), str(role_id)]),
                AuditLog.action.in_(["user.create", "user.update", "role.create", "role.update", "role.delete"]),
            )
        ).scalars().all())
        assert actions == {"user.create", "user.update", "role.create", "role.update", "role.delete"}


def test_management_prevents_self_change_presets_and_privilege_escalation(admin_client):
    me = admin_client.get("/api/v1/auth/me").json()
    assert admin_client.patch(
        f"/api/v1/users/{me['id']}", json={"is_active": False}, headers=CSRF_HEADERS,
    ).status_code == 409
    administrator_role = _role_id("administrator")
    assert admin_client.patch(
        f"/api/v1/roles/{administrator_role}", json={"permissions": []}, headers=CSRF_HEADERS,
    ).status_code == 409
    assert admin_client.delete(
        f"/api/v1/roles/{administrator_role}", headers=CSRF_HEADERS,
    ).status_code == 409

    all_permissions = admin_client.get("/api/v1/roles/permissions").json()
    full_role = admin_client.post(
        "/api/v1/roles",
        json={"name": "test_full_manager", "permissions": all_permissions},
        headers=CSRF_HEADERS,
    )
    assert full_role.status_code == 201
    full_user = admin_client.post(
        "/api/v1/users",
        json={
            "username": "full-user-manager",
            "password": "full-manager-password",
            "role_id": full_role.json()["id"],
        },
        headers=CSRF_HEADERS,
    )
    assert full_user.status_code == 201
    admin_client.cookies.clear()
    assert admin_client.post(
        "/api/v1/auth/login",
        json={"username": "full-user-manager", "password": "full-manager-password"},
        headers=CSRF_HEADERS,
    ).status_code == 200
    last_admin = admin_client.patch(
        f"/api/v1/users/{me['id']}", json={"is_active": False}, headers=CSRF_HEADERS,
    )
    assert last_admin.status_code == 409
    assert "最後の有効な管理者" in last_admin.json()["detail"]

    admin_client.cookies.clear()
    assert admin_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
        headers=CSRF_HEADERS,
    ).status_code == 200

    manager_role = admin_client.post(
        "/api/v1/roles",
        json={"name": "test_user_manager", "permissions": ["users.manage"]},
        headers=CSRF_HEADERS,
    )
    assert manager_role.status_code == 201
    manager_role_id = manager_role.json()["id"]
    manager = admin_client.post(
        "/api/v1/users",
        json={
            "username": "limited-user-manager",
            "password": "limited-manager-password",
            "role_id": manager_role_id,
        },
        headers=CSRF_HEADERS,
    )
    assert manager.status_code == 201

    admin_client.cookies.clear()
    assert admin_client.post(
        "/api/v1/auth/login",
        json={"username": "limited-user-manager", "password": "limited-manager-password"},
        headers=CSRF_HEADERS,
    ).status_code == 200
    assert admin_client.post(
        "/api/v1/roles",
        json={"name": "forbidden_power_role", "permissions": ["power.manage"]},
        headers=CSRF_HEADERS,
    ).status_code == 403
    admin_id = me["id"]
    assert admin_client.patch(
        f"/api/v1/users/{admin_id}", json={"is_active": False}, headers=CSRF_HEADERS,
    ).status_code == 403


def test_viewer_cannot_manage_users(client):
    with SessionLocal() as db:
        viewer = db.execute(select(User).where(User.username == "access-active-viewer")).scalar_one_or_none()
        if viewer is None:
            viewer = User(
                username="access-active-viewer",
                password_hash=hash_password("active-viewer-password"),
                role_id=_role_id("viewer"),
            )
            db.add(viewer)
            db.commit()
    client.cookies.clear()
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "access-active-viewer", "password": "active-viewer-password"},
        headers=CSRF_HEADERS,
    ).status_code == 200
    assert client.get("/api/v1/users").status_code == 403
