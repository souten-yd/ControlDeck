"""サーバー側セッション。トークンは 256bit 乱数、DB には SHA-256 ハッシュのみ保存。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import User, UserSession

SESSION_COOKIE = "cd_session"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, ip: str, user_agent: str) -> str:
    token = secrets.token_urlsafe(32)
    timeout = get_config().security.session_timeout_minutes
    db.add(
        UserSession(
            user_id=user.id,
            session_token_hash=_hash_token(token),
            ip_address=ip[:64],
            user_agent=user_agent[:256],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=timeout),
        )
    )
    db.commit()
    return token


def resolve_session(db: Session, token: str) -> tuple[UserSession, User] | None:
    if not token:
        return None
    row = db.execute(
        select(UserSession).where(UserSession.session_token_hash == _hash_token(token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return row, user


def revoke_session(db: Session, token: str) -> None:
    row = db.execute(
        select(UserSession).where(UserSession.session_token_hash == _hash_token(token))
    ).scalar_one_or_none()
    if row is not None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
