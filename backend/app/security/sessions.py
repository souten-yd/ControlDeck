"""サーバー側セッション。トークンは 256bit 乱数、DB には SHA-256 ハッシュのみ保存。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import User, UserSession

SESSION_COOKIE = "cd_session"
# NOT NULL の既存 DB と互換な無期限マーカー。Cookie の保存期間とは独立。
NON_EXPIRING = datetime.max.replace(tzinfo=timezone.utc)
PERSISTENT_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def set_session_cookie(response: Response, token: str) -> None:
    cfg = get_config().security
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=cfg.session_timeout_minutes * 60 or PERSISTENT_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=cfg.secure_cookies,
        path="/",
    )


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
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=timeout)
                        if timeout else NON_EXPIRING),
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
    now = datetime.now(timezone.utc)
    timeout_minutes = get_config().security.session_timeout_minutes
    timeout = timedelta(minutes=timeout_minutes)
    # 有期限設定へ戻した場合は、無期限だったセッションにも未使用時間を適用。
    was_persistent = expires == NON_EXPIRING
    if timeout_minutes and was_persistent:
        last_seen = row.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        expires = last_seen + timeout
    # 設定変更前に既に期限が切れていたセッションは復活させない。
    if expires <= now:
        return None
    # role を一緒に読む。あとから user.role を触ると、そこで DB へ問い合わせが
    # 走る。権限の判定は async の endpoint の中で行われるので、その問い合わせは
    # event loop の上で同期的に待つことになり、pool が空なら loop ごと止まる。
    user = db.get(User, row.user_id, options=[joinedload(User.role)])
    if user is None or not user.is_active:
        return None
    row.last_seen_at = now
    row.renewed = False
    if not timeout_minutes:
        row.expires_at = NON_EXPIRING
        # ブラウザに無期限 Cookie はないため、アクセスのたびに保存期間を更新。
        # WebSocket 認証が先に走っても、次の HTTP 応答で必ず Cookie を更新する。
        row.renewed = True
    elif was_persistent or expires - now < timeout / 2:
        row.expires_at = now + timeout
        row.renewed = True          # cookie も延ばす合図。DB の列ではない
    db.commit()
    return row, user


def revoke_session(db: Session, token: str) -> None:
    row = db.execute(
        select(UserSession).where(UserSession.session_token_hash == _hash_token(token))
    ).scalar_one_or_none()
    if row is not None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
