"""TOTP（RFC 6238）とリカバリーコードのサービス層。

シークレットとリカバリーコードは Fernet 暗号化して保存する。
"""
from __future__ import annotations

import base64
import io
import json
import secrets

import pyotp
import qrcode
import qrcode.image.svg

from app.config import get_config
from app.models import User
from app.security.crypto import decrypt_text, encrypt_text


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    issuer = get_config().ui.app_name
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def qr_data_uri(uri: str) -> str:
    """QR コードを SVG の data URI として返す（Pillow 不要）。"""
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/svg+xml;base64,{b64}"


def verify_code(secret: str, code: str) -> bool:
    # 前後 1 ステップの許容（時刻ずれ対策）
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def generate_recovery_codes(count: int = 10) -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]


def store_secret(user: User, secret: str) -> None:
    user.totp_secret_encrypted = encrypt_text(secret)


def get_secret(user: User) -> str | None:
    if not user.totp_secret_encrypted:
        return None
    try:
        return decrypt_text(user.totp_secret_encrypted)
    except Exception:
        return None


def store_recovery_codes(user: User, codes: list[str]) -> None:
    user.recovery_codes_encrypted = encrypt_text(json.dumps(codes))


def consume_recovery_code(user: User, code: str) -> bool:
    """リカバリーコードを検証し、使えたら消費（削除）して True。"""
    if not user.recovery_codes_encrypted:
        return False
    try:
        codes = json.loads(decrypt_text(user.recovery_codes_encrypted))
    except Exception:
        return False
    normalized = code.strip().lower()
    if normalized in codes:
        codes.remove(normalized)
        user.recovery_codes_encrypted = encrypt_text(json.dumps(codes))
        return True
    return False


def remaining_recovery_codes(user: User) -> int:
    if not user.recovery_codes_encrypted:
        return 0
    try:
        return len(json.loads(decrypt_text(user.recovery_codes_encrypted)))
    except Exception:
        return 0
