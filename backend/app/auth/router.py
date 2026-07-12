from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.config import get_config
from app.database import get_db
from app.models import User, UserSession
from app.schemas.auth import LoginRequest, SessionOut, UserOut
from app.security import ratelimit
from app.security.deps import get_current_user, user_permissions
from app.security.passwords import verify_password
from app.security.sessions import (
    SESSION_COOKIE,
    _hash_token,
    create_session,
    resolve_session,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    cfg = get_config()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=cfg.security.session_timeout_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=cfg.security.secure_cookies,
        path="/",
    )


@router.post("/login")
def login(
    body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    ip = request.client.host if request.client else "unknown"
    if not ratelimit.allow(f"login:{ip}", max_attempts=10, window_seconds=60) or not ratelimit.allow(
        f"login:{ip}:{body.username}", max_attempts=5, window_seconds=60
    ):
        audit.record(db, "login", username=body.username, result="rate_limited", request=request)
        raise HTTPException(status_code=429, detail="試行回数が多すぎます。しばらく待ってから再試行してください")

    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        audit.record(db, "login", username=body.username, result="failure", request=request)
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが正しくありません")

    from app.models import utcnow

    user.last_login_at = utcnow()
    token = create_session(
        db, user, ip, request.headers.get("user-agent", "")
    )
    ratelimit.reset(f"login:{ip}:{body.username}")
    audit.record(db, "login", user=user, result="success", request=request)
    _set_session_cookie(response, token)
    return _user_out(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE, "")
    resolved = resolve_session(db, token)
    if resolved is not None:
        _, user = resolved
        audit.record(db, "logout", user=user, request=request)
    revoke_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role.name,
        permissions=sorted(user_permissions(user)),
        totp_enabled=user.totp_enabled,
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.get("/sessions")
def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    current_hash = _hash_token(request.cookies.get(SESSION_COOKIE, ""))
    rows = (
        db.execute(
            select(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .order_by(UserSession.last_seen_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        SessionOut(
            id=s.id,
            ip_address=s.ip_address,
            user_agent=s.user_agent,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            current=s.session_token_hash == current_hash,
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}")
def revoke_other_session(
    session_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(UserSession, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")
    from app.models import utcnow

    row.revoked_at = utcnow()
    db.commit()
    audit.record(db, "session.revoke", user=user, resource_type="session", resource_id=str(session_id), request=request)
    return {"ok": True}
