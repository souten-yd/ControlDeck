from __future__ import annotations

import pyotp
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.auth.policy import totp_required_for
from app.config import get_config
from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Role, User
from app.security.passwords import hash_password
from tests.conftest import CSRF_HEADERS


def _ensure_viewer() -> int:
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "totp-policy-viewer")).scalar_one_or_none()
        if user is None:
            role = db.execute(select(Role).where(Role.name == "viewer")).scalar_one()
            user = User(
                username="totp-policy-viewer",
                password_hash=hash_password("totp-policy-password"),
                role_id=role.id,
            )
            db.add(user)
            db.commit()
        user.totp_enabled = False
        user.totp_secret_encrypted = None
        user.recovery_codes_encrypted = None
        db.commit()
        return user.id


def test_all_users_policy_blocks_rest_and_websocket_until_enrollment(client):
    user_id = _ensure_viewer()
    security = get_config().security
    original_requirement = security.totp_requirement
    original_legacy = security.require_totp_for_admin
    security.totp_requirement = "all"
    security.require_totp_for_admin = False
    client.cookies.clear()
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "totp-policy-viewer", "password": "totp-policy-password"},
            headers=CSRF_HEADERS,
        )
        assert login.status_code == 200
        assert login.json()["totp_required"] is True
        assert client.get("/api/v1/auth/me").status_code == 200
        blocked = client.get("/api/v1/apps")
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "totp_setup_required"
        try:
            with client.websocket_connect("/api/v1/system/metrics/stream"):
                raise AssertionError("TOTP未設定のWebSocketを接続してはいけない")
        except WebSocketDisconnect as error:
            assert error.code == 4403

        setup = client.post("/api/v1/auth/totp/setup", headers=CSRF_HEADERS)
        assert setup.status_code == 200
        enabled = client.post(
            "/api/v1/auth/totp/verify",
            json={"code": pyotp.TOTP(setup.json()["secret"]).now()},
            headers=CSRF_HEADERS,
        )
        assert enabled.status_code == 200
        assert client.get("/api/v1/apps").status_code == 200

        denied = client.post(
            "/api/v1/auth/totp/disable",
            json={"code": pyotp.TOTP(setup.json()["secret"]).now()},
            headers=CSRF_HEADERS,
        )
        assert denied.status_code == 409
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None and user.totp_enabled is True
            audit = db.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "totp.disable",
                    AuditLog.result == "policy_denied",
                )
            ).scalar_one_or_none()
            assert audit is not None
    finally:
        security.totp_requirement = original_requirement
        security.require_totp_for_admin = original_legacy
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.totp_enabled = False
            user.totp_secret_encrypted = None
            user.recovery_codes_encrypted = None
            db.commit()
        client.cookies.clear()


def test_administrator_and_legacy_policies_are_role_scoped(client):
    _ensure_viewer()
    security = get_config().security
    original_requirement = security.totp_requirement
    original_legacy = security.require_totp_for_admin
    try:
        with SessionLocal() as db:
            admin = db.execute(select(User).where(User.username == "admin")).scalar_one()
            viewer = db.execute(select(User).where(User.username == "totp-policy-viewer")).scalar_one()
            security.totp_requirement = "administrators"
            security.require_totp_for_admin = False
            assert totp_required_for(admin) is True
            assert totp_required_for(viewer) is False
            security.totp_requirement = "optional"
            security.require_totp_for_admin = True
            assert totp_required_for(admin) is True
            assert totp_required_for(viewer) is False
    finally:
        security.totp_requirement = original_requirement
        security.require_totp_for_admin = original_legacy
