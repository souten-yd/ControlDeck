from __future__ import annotations

import pyotp
from sqlalchemy import func, select, update

from app.database import SessionLocal
from app.models import AuditLog, Role, User, UserSession, utcnow
from app.security import ratelimit
from app.security.passwords import hash_password
from tests.conftest import CSRF_HEADERS


def _ensure_user(username: str, password: str) -> int:
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            role = db.execute(select(Role).where(Role.name == "viewer")).scalar_one()
            user = User(username=username, password_hash=hash_password(password), role_id=role.id)
            db.add(user)
            db.commit()
        return user.id


def test_password_change_reauthenticates_revokes_sessions_and_audits(client):
    username = "credential-password-user"
    original = "original-password-123"
    replacement = "replacement-password-456"
    user_id = _ensure_user(username, original)
    client.cookies.clear()
    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": original},
            headers=CSRF_HEADERS,
        )
        assert response.status_code == 200

    try:
        for _ in range(5):
            wrong = client.post(
                "/api/v1/auth/password",
                json={"current_password": "incorrect-password", "new_password": replacement},
                headers=CSRF_HEADERS,
            )
            assert wrong.status_code == 400
        limited = client.post(
            "/api/v1/auth/password",
            json={"current_password": "incorrect-password", "new_password": replacement},
            headers=CSRF_HEADERS,
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "900"
        ratelimit.reset(f"password.change:testclient:{user_id}")
        same = client.post(
            "/api/v1/auth/password",
            json={"current_password": original, "new_password": original},
            headers=CSRF_HEADERS,
        )
        assert same.status_code == 400
        changed = client.post(
            "/api/v1/auth/password",
            json={"current_password": original, "new_password": replacement},
            headers=CSRF_HEADERS,
        )
        assert changed.status_code == 200
        assert changed.json()["sessions_revoked"] is True
        assert client.get("/api/v1/auth/me").status_code == 401

        with SessionLocal() as db:
            active = db.scalar(
                select(func.count()).select_from(UserSession).where(
                    UserSession.user_id == user_id, UserSession.revoked_at.is_(None),
                )
            )
            assert active == 0
            results = db.execute(
                select(AuditLog.result).where(
                    AuditLog.user_id == user_id, AuditLog.action == "password.change",
                )
            ).scalars().all()
            assert "failure" in results
            assert "rate_limited" in results
            assert "success" in results

        client.cookies.clear()
        assert client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": original},
            headers=CSRF_HEADERS,
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": replacement},
            headers=CSRF_HEADERS,
        ).status_code == 200
    finally:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.password_hash = hash_password(original)
            db.execute(
                update(UserSession).where(UserSession.user_id == user_id).values(revoked_at=utcnow())
            )
            db.commit()
        ratelimit.reset(f"password.change:testclient:{user_id}")
        client.cookies.clear()


def test_totp_verify_and_disable_have_shared_user_endpoint_limits(client):
    username = "credential-totp-user"
    password = "totp-password-123"
    user_id = _ensure_user(username, password)
    client.cookies.clear()
    assert client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=CSRF_HEADERS,
    ).status_code == 200
    setup = client.post("/api/v1/auth/totp/setup", headers=CSRF_HEADERS)
    assert setup.status_code == 200
    secret = setup.json()["secret"]

    verify_key = f"totp.verify:testclient:{user_id}"
    disable_key = f"totp.disable:testclient:{user_id}"
    try:
        for _ in range(5):
            assert client.post(
                "/api/v1/auth/totp/verify", json={"code": "abcdef"}, headers=CSRF_HEADERS,
            ).status_code == 400
        limited = client.post(
            "/api/v1/auth/totp/verify", json={"code": "abcdef"}, headers=CSRF_HEADERS,
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "300"
        with SessionLocal() as db:
            verify_results = db.execute(
                select(AuditLog.result).where(
                    AuditLog.user_id == user_id, AuditLog.action == "totp.verify",
                )
            ).scalars().all()
            assert "failure" in verify_results
            assert "rate_limited" in verify_results

        ratelimit.reset(verify_key)
        enabled = client.post(
            "/api/v1/auth/totp/verify",
            json={"code": pyotp.TOTP(secret).now()},
            headers=CSRF_HEADERS,
        )
        assert enabled.status_code == 200
        for _ in range(5):
            assert client.post(
                "/api/v1/auth/totp/disable", json={"code": "abcdef"}, headers=CSRF_HEADERS,
            ).status_code == 400
        limited = client.post(
            "/api/v1/auth/totp/disable", json={"code": "abcdef"}, headers=CSRF_HEADERS,
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "300"
        with SessionLocal() as db:
            disable_results = db.execute(
                select(AuditLog.result).where(
                    AuditLog.user_id == user_id, AuditLog.action == "totp.disable",
                )
            ).scalars().all()
            assert "failure" in disable_results
            assert "rate_limited" in disable_results
    finally:
        ratelimit.reset(verify_key)
        ratelimit.reset(disable_key)
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.totp_enabled = False
            user.totp_secret_encrypted = None
            user.recovery_codes_encrypted = None
            db.commit()
        client.cookies.clear()
