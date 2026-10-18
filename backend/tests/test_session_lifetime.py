from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import SecurityConfig, get_config
from app.database import SessionLocal
from app.models import User, UserSession
from app.security import sessions
from tests.conftest import CSRF_HEADERS


def _row(db, token):
    return db.execute(select(UserSession).where(
        UserSession.session_token_hash == sessions._hash_token(token),
    )).scalar_one()


def _advance(monkeypatch, days):
    future = datetime.now(timezone.utc) + timedelta(days=days)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    monkeypatch.setattr(sessions, "datetime", Clock)


def test_default_persists_after_idle_and_logout_revokes(admin_client, monkeypatch):
    token = admin_client.cookies.get(sessions.SESSION_COOKIE)
    _advance(monkeypatch, 90)
    response = admin_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert f"Max-Age={365 * 86400}" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    # 新しい DB session でも保持され、失効操作の後は同じ token を再利用できない。
    with SessionLocal() as db:
        assert sessions.resolve_session(db, token) is not None
    assert admin_client.post("/api/v1/auth/logout", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.get("/api/v1/auth/me", headers={"Cookie": f"cd_session={token}"}).status_code == 401


@pytest.mark.parametrize("remaining_minutes, expected", [(60, 200), (-1, 401)])
def test_legacy_session_migrates_only_when_still_valid(admin_client, remaining_minutes, expected):
    token = admin_client.cookies.get(sessions.SESSION_COOKIE)
    with SessionLocal() as db:
        row = _row(db, token)
        row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=remaining_minutes)
        db.commit()
    response = admin_client.get("/api/v1/auth/me")
    assert response.status_code == expected
    if expected == 200:
        assert "Max-Age=31536000" in response.headers["set-cookie"]
        with SessionLocal() as db:
            assert _row(db, token).expires_at.replace(tzinfo=timezone.utc) == sessions.NON_EXPIRING


def test_finite_timeout_still_renews_and_expires(client, monkeypatch):
    monkeypatch.setattr(get_config().security, "session_timeout_minutes", 480)
    response = client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "test-password-123",
    }, headers=CSRF_HEADERS)
    assert response.status_code == 200
    assert "Max-Age=28800" in response.headers["set-cookie"]
    token = client.cookies.get(sessions.SESSION_COOKIE)
    assert "set-cookie" not in client.get("/api/v1/auth/me").headers
    with SessionLocal() as db:
        _row(db, token).expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        db.commit()
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert "Max-Age=28800" in response.headers["set-cookie"]
    _advance(monkeypatch, 1)
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("days, expected", [(0, 200), (1, 401)])
def test_reenabling_timeout_applies_to_persistent_sessions(admin_client, monkeypatch, days, expected):
    monkeypatch.setattr(get_config().security, "session_timeout_minutes", 480)
    _advance(monkeypatch, days)
    response = admin_client.get("/api/v1/auth/me")
    assert response.status_code == expected
    if expected == 200:
        assert "Max-Age=28800" in response.headers["set-cookie"]


def test_revocation_and_disabled_user_reject_persistent_session(admin_client):
    token = admin_client.cookies.get(sessions.SESSION_COOKIE)
    with SessionLocal() as db:
        row = _row(db, token)
        user = db.get(User, row.user_id)
        user.is_active = False
        db.commit()
        try:
            assert admin_client.get("/api/v1/auth/me").status_code == 401
        finally:
            user.is_active = True
            db.commit()
        session_id = row.id
    assert admin_client.delete(f"/api/v1/auth/sessions/{session_id}", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.get("/api/v1/auth/me").status_code == 401


def test_secure_cookie_on_login(client, monkeypatch):
    monkeypatch.setattr(get_config().security, "secure_cookies", True)
    response = client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "test-password-123",
    }, headers=CSRF_HEADERS)
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert "Max-Age=31536000" in response.headers["set-cookie"]
    client.cookies.clear()


def test_negative_timeout_rejected():
    with pytest.raises(ValidationError):
        SecurityConfig(session_timeout_minutes=-1)
