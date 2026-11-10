"""Discoverable WebAuthn credentials. Biometric material stays on the authenticator."""
from __future__ import annotations

import json
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from webauthn import (generate_authentication_options, generate_registration_options,
                      options_to_json, verify_authentication_response, verify_registration_response)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, ResidentKeyRequirement,
                                     UserVerificationRequirement, PublicKeyCredentialDescriptor)

from app.audit import service as audit
from app.auth import totp
from app.auth.router import _user_out, _check_credential_limit, _PASSWORD_WINDOW_SECONDS
from app.database import get_db
from app.models import Passkey, PasskeyChallenge, User, utcnow
from app.schemas.passkeys import PasskeyRegistration, PasskeyResponse
from app.security import ratelimit
from app.security.deps import get_current_user
from app.security.passwords import verify_password
from app.security.sessions import SESSION_COOKIE, _hash_token, create_session, set_session_cookie

router = APIRouter(prefix="/auth", tags=["auth"])


def _origin(request: Request) -> tuple[str, str]:
    origin = request.headers.get("origin", "")
    parsed = urlsplit(origin)
    # Never trust an arbitrary Origin as the RP identity. Match the serving host,
    # including its port. TLS may terminate at the trusted reverse proxy.
    if (not parsed.hostname or parsed.netloc.lower() != request.url.netloc.lower()
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise HTTPException(400, "パスキーはHTTPS（またはlocalhost）の同じ接続先で利用してください")
    return origin, parsed.hostname


def _limit(request: Request) -> None:
    key = f"passkey:{request.client.host if request.client else 'unknown'}"
    if not ratelimit.check(key, max_attempts=30, window_seconds=60):
        raise HTTPException(429, "試行回数が多すぎます。しばらく待ってから再試行してください")
    ratelimit.record(key)


def _issue(db: Session, request: Request, response: Response, purpose: str,
           *, user: User | None = None, handle: str = "", name: str = "") -> PasskeyChallenge:
    origin, rp_id = _origin(request)
    token = secrets.token_urlsafe(32)
    cookie = f"cd_passkey_{purpose}"
    old = request.cookies.get(cookie)
    db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < utcnow()))
    if old:
        db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.id == _hash_token(old)))
    row = PasskeyChallenge(id=_hash_token(token), challenge=bytes_to_base64url(secrets.token_bytes(32)),
        purpose=purpose, origin=origin, rp_id=rp_id, user_id=user.id if user else None,
        session_hash=_hash_token(request.cookies.get(SESSION_COOKIE, "")) if user else "",
        user_handle=handle, name=name, expires_at=utcnow() + timedelta(minutes=5))
    db.add(row)
    db.commit()
    response.set_cookie(cookie, token, max_age=300, httponly=True, samesite="strict",
                        secure=origin.startswith("https:"), path="/api/v1/auth")
    return row


def _consume(db: Session, request: Request, response: Response, purpose: str,
             user: User | None = None) -> PasskeyChallenge:
    origin, rp_id = _origin(request)
    cookie = f"cd_passkey_{purpose}"
    key = _hash_token(request.cookies.get(cookie, ""))
    row = db.get(PasskeyChallenge, key)
    if (row is None or row.purpose != purpose or row.origin != origin or row.rp_id != rp_id
            or (user is not None and (row.user_id != user.id or row.session_hash !=
                _hash_token(request.cookies.get(SESSION_COOKIE, ""))))):
        raise HTTPException(400, "認証を最初からやり直してください")
    # Atomic single use, including failed verification attempts and concurrent requests.
    removed = db.execute(delete(PasskeyChallenge).where(
        PasskeyChallenge.id == key, PasskeyChallenge.expires_at > utcnow()),
        execution_options={"synchronize_session": False})
    db.commit()
    response.delete_cookie(cookie, path="/api/v1/auth")
    if removed.rowcount != 1:
        raise HTTPException(400, "認証の有効期限が切れました。やり直してください")
    return row


@router.get("/passkeys")
def list_passkeys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": row.id, "name": row.name, "rp_id": row.rp_id,
             "created_at": row.created_at, "last_used_at": row.last_used_at}
            for row in db.scalars(select(Passkey).where(Passkey.user_id == user.id))]


@router.post("/passkeys/register/options")
def register_options(body: PasskeyRegistration, request: Request, response: Response,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _limit(request)
    origin, rp_id = _origin(request)
    key = _check_credential_limit("passkey.register", request, user, db,
                                 window_seconds=_PASSWORD_WINDOW_SECONDS)
    valid = verify_password(user.password_hash, body.password)
    if valid and user.totp_enabled:
        secret = totp.get_secret(user)
        valid = bool(body.totp_code and ((secret and totp.verify_code(secret, body.totp_code))
                     or totp.consume_recovery_code(user, body.totp_code)))
    if not valid:
        ratelimit.record(key)
        audit.record(db, "passkey.register", user=user, result="failure", request=request)
        raise HTTPException(400, "パスワードまたは認証コードが正しくありません")
    existing = list(db.scalars(select(Passkey).where(Passkey.user_id == user.id)))
    if len(existing) >= 10:
        raise HTTPException(409, "登録上限です。不要なパスキーを削除してください")
    handle = existing[0].user_handle if existing else bytes_to_base64url(secrets.token_bytes(32))
    challenge = _issue(db, request, response, "register", user=user, handle=handle, name=body.name.strip() or "パスキー")
    ratelimit.reset(key)
    options = generate_registration_options(rp_id=rp_id, rp_name="ControlDeck", user_name=user.username,
        user_display_name=user.display_name or user.username, user_id=base64url_to_bytes(handle),
        challenge=base64url_to_bytes(challenge.challenge),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
                             for p in existing if p.rp_id == rp_id])
    return json.loads(options_to_json(options))


@router.post("/passkeys/register/verify")
def register_verify(body: PasskeyResponse, request: Request, response: Response,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _limit(request)
    challenge = _consume(db, request, response, "register", user)
    try:
        verified = verify_registration_response(credential=body.credential,
            expected_challenge=base64url_to_bytes(challenge.challenge), expected_rp_id=challenge.rp_id,
            expected_origin=challenge.origin, require_user_verification=True)
    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
        audit.record(db, "passkey.register", user=user, result="failure", request=request)
        raise HTTPException(400, "パスキーを確認できませんでした。登録をやり直してください") from exc
    db.add(Passkey(user_id=user.id, credential_id=bytes_to_base64url(verified.credential_id),
        public_key=bytes_to_base64url(verified.credential_public_key), user_handle=challenge.user_handle,
        rp_id=challenge.rp_id, name=challenge.name, sign_count=verified.sign_count))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "このパスキーは既に登録されています") from exc
    audit.record(db, "passkey.register", user=user, request=request)
    return {"ok": True}


@router.delete("/passkeys/{key_id}")
def delete_passkey(key_id: int, request: Request, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    row = db.get(Passkey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "パスキーが見つかりません")
    db.delete(row)
    db.commit()
    audit.record(db, "passkey.delete", user=user, resource_type="passkey", resource_id=str(key_id), request=request)
    return {"ok": True}


@router.post("/login/passkey/options")
def login_options(request: Request, response: Response, db: Session = Depends(get_db)):
    _limit(request)
    row = _issue(db, request, response, "login")
    return json.loads(options_to_json(generate_authentication_options(rp_id=row.rp_id,
        challenge=base64url_to_bytes(row.challenge), user_verification=UserVerificationRequirement.REQUIRED)))


@router.post("/login/passkey/verify")
def login_verify(body: PasskeyResponse, request: Request, response: Response, db: Session = Depends(get_db)):
    _limit(request)
    challenge = _consume(db, request, response, "login")
    credential_id = body.credential.get("id")
    row = db.scalar(select(Passkey).where(Passkey.credential_id == credential_id,
                                         Passkey.rp_id == challenge.rp_id)) if isinstance(credential_id, str) else None
    user = db.get(User, row.user_id) if row else None
    try:
        if row is None or user is None or not user.is_active:
            raise ValueError("unknown credential")
        handle = body.credential.get("response", {}).get("userHandle")
        if not isinstance(handle, str) or base64url_to_bytes(handle) != base64url_to_bytes(row.user_handle):
            raise ValueError("user handle mismatch")
        previous_count = row.sign_count
        verified = verify_authentication_response(credential=body.credential,
            expected_challenge=base64url_to_bytes(challenge.challenge), expected_rp_id=challenge.rp_id,
            expected_origin=challenge.origin, credential_public_key=base64url_to_bytes(row.public_key),
            credential_current_sign_count=previous_count, require_user_verification=True)
    except (WebAuthnException, ValueError, TypeError, KeyError, AttributeError) as exc:
        audit.record(db, "login.passkey", result="failure", request=request)
        raise HTTPException(401, "パスキーでログインできませんでした") from exc
    changed = db.execute(update(Passkey).where(Passkey.id == row.id, Passkey.sign_count == previous_count)
                         .values(sign_count=verified.new_sign_count, last_used_at=utcnow()))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(401, "認証をやり直してください")
    user.last_login_at = utcnow()
    token = create_session(db, user, request.client.host if request.client else "unknown",
                           request.headers.get("user-agent", ""))
    set_session_cookie(response, token)
    audit.record(db, "login.passkey", user=user, request=request)
    return _user_out(user)
