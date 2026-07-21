from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.auth import totp
from app.auth.policy import totp_required_for
from app.config import get_config
from app.database import get_db
from app.models import User, UserSession
from app.schemas.auth import (
    LoginRequest,
    PasswordChangeRequest,
    SessionOut,
    TotpSetupResponse,
    TotpVerifyRequest,
    UserOut,
)
from app.security import ratelimit
from app.security.deps import get_current_user, user_permissions
from app.security.passwords import hash_password, verify_password
from app.security.sessions import (
    SESSION_COOKIE,
    _hash_token,
    create_session,
    resolve_session,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_CREDENTIAL_MAX_FAILURES = 5
_TOTP_WINDOW_SECONDS = 5 * 60
_PASSWORD_WINDOW_SECONDS = 15 * 60


def _credential_key(kind: str, request: Request, user: User) -> str:
    peer = request.client.host if request.client else "unknown"
    return f"{kind}:{peer}:{user.id}"


def _check_credential_limit(
    kind: str,
    request: Request,
    user: User,
    db: Session,
    *,
    window_seconds: int,
) -> str:
    key = _credential_key(kind, request, user)
    if not ratelimit.check(key, max_attempts=_CREDENTIAL_MAX_FAILURES, window_seconds=window_seconds):
        audit.record(db, kind, user=user, result="rate_limited", request=request)
        raise HTTPException(
            status_code=429,
            detail="試行回数が多すぎます。しばらく待ってから再試行してください",
            headers={"Retry-After": str(window_seconds)},
        )
    return key


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
    # 失敗のみをカウントする（IP 単位 + IP×ユーザー名単位）
    if not ratelimit.check(f"login:{ip}", max_attempts=20, window_seconds=60) or not ratelimit.check(
        f"login:{ip}:{body.username}", max_attempts=5, window_seconds=60
    ):
        audit.record(db, "login", username=body.username, result="rate_limited", request=request)
        raise HTTPException(status_code=429, detail="試行回数が多すぎます。しばらく待ってから再試行してください")

    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        ratelimit.record(f"login:{ip}")
        ratelimit.record(f"login:{ip}:{body.username}")
        audit.record(db, "login", username=body.username, result="failure", request=request)
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが正しくありません")

    # 二要素認証: 有効なら TOTP / リカバリーコードを要求
    if user.totp_enabled:
        if not body.totp_code:
            # パスワードは正しいが 2FA が必要（クライアントはこのコードで入力欄を出す）
            raise HTTPException(status_code=401, detail="two_factor_required")
        secret = totp.get_secret(user)
        code_ok = (secret is not None and totp.verify_code(secret, body.totp_code)) or totp.consume_recovery_code(
            user, body.totp_code
        )
        if not code_ok:
            ratelimit.record(f"login:{ip}:{body.username}")
            audit.record(db, "login", user=user, result="totp_failure", request=request)
            raise HTTPException(status_code=401, detail="認証コードが正しくありません")

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
    required = totp_required_for(user)
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role.name,
        permissions=sorted(user_permissions(user)),
        totp_enabled=user.totp_enabled,
        recovery_codes_remaining=totp.remaining_recovery_codes(user),
        totp_required=required,
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.post("/password")
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """現在のpasswordで再認証し、成功時は全sessionを失効する。"""
    key = _check_credential_limit(
        "password.change", request, user, db, window_seconds=_PASSWORD_WINDOW_SECONDS,
    )
    if not verify_password(user.password_hash, body.current_password):
        ratelimit.record(key)
        audit.record(db, "password.change", user=user, result="failure", request=request)
        raise HTTPException(status_code=400, detail="現在のパスワードが正しくありません")
    if verify_password(user.password_hash, body.new_password):
        raise HTTPException(status_code=400, detail="新しいパスワードは現在のパスワードと異なるものにしてください")

    from app.models import utcnow

    user.password_hash = hash_password(body.new_password)
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    db.commit()
    ratelimit.reset(key)
    audit.record(db, "password.change", user=user, request=request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "sessions_revoked": True}


# ---- TOTP 二要素認証 ----


@router.post("/totp/setup")
def totp_setup(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TotpSetupResponse:
    """シークレットを生成して QR を返す。まだ有効化はしない（verify で確定）。"""
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="二要素認証は既に有効です")
    secret = totp.generate_secret()
    totp.store_secret(user, secret)  # 未確定シークレットを一時保存
    db.commit()
    uri = totp.provisioning_uri(secret, user.username)
    return TotpSetupResponse(secret=secret, qr_data_uri=totp.qr_data_uri(uri), provisioning_uri=uri)


@router.post("/totp/verify")
def totp_verify(
    body: TotpVerifyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """6 桁コードで確認して有効化し、リカバリーコードを返す（この 1 回だけ表示）。"""
    key = _check_credential_limit(
        "totp.verify", request, user, db, window_seconds=_TOTP_WINDOW_SECONDS,
    )
    secret = totp.get_secret(user)
    if secret is None:
        raise HTTPException(status_code=409, detail="先に setup を実行してください")
    if not totp.verify_code(secret, body.code):
        ratelimit.record(key)
        audit.record(db, "totp.verify", user=user, result="failure", request=request)
        raise HTTPException(status_code=400, detail="認証コードが正しくありません")
    codes = totp.generate_recovery_codes()
    totp.store_recovery_codes(user, codes)
    user.totp_enabled = True
    db.commit()
    ratelimit.reset(key)
    audit.record(db, "totp.enable", user=user, request=request)
    return {"enabled": True, "recovery_codes": codes}


@router.post("/totp/disable")
def totp_disable(
    body: TotpVerifyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """現在のコードまたはリカバリーコードで確認してから無効化する。"""
    if totp_required_for(user):
        audit.record(db, "totp.disable", user=user, result="policy_denied", request=request)
        raise HTTPException(status_code=409, detail="組織の認証ポリシーにより二要素認証は必須です")
    key = _check_credential_limit(
        "totp.disable", request, user, db, window_seconds=_TOTP_WINDOW_SECONDS,
    )
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="二要素認証は有効ではありません")
    secret = totp.get_secret(user)
    ok = (secret is not None and totp.verify_code(secret, body.code)) or totp.consume_recovery_code(user, body.code)
    if not ok:
        ratelimit.record(key)
        audit.record(db, "totp.disable", user=user, result="failure", request=request)
        raise HTTPException(status_code=400, detail="認証コードが正しくありません")
    user.totp_enabled = False
    user.totp_secret_encrypted = None
    user.recovery_codes_encrypted = None
    db.commit()
    ratelimit.reset(key)
    audit.record(db, "totp.disable", user=user, request=request)
    return {"enabled": False}


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
