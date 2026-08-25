from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from pathlib import Path
from typing import Any

from app.config import data_dir

TOKEN_TTL_SECONDS = 10 * 60
MAX_TOKEN_TTL_SECONDS = 8 * 60 * 60
# Device credentials are intentionally a different usability/security tradeoff:
# they are paired once, scoped to one Add-on relay/device and refreshed on every
# successful reconnect. Service/workflow credentials remain capped at 8 hours.
MAX_DEVICE_TOKEN_TTL_SECONDS = 31 * 24 * 60 * 60
_KEY_NAME = "addon-token.key"


class AddonTokenError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _key_path() -> Path:
    return data_dir() / _KEY_NAME


def _signing_key() -> bytes:
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise AddonTokenError(
                "addon token keyは実行userだけが読める通常fileにしてください"
            )
        key = path.read_bytes()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
    if len(key) != 32:
        raise AddonTokenError("addon token keyが不正です")
    return key


def _max_ttl_for_kind(kind: str) -> int:
    return (
        MAX_DEVICE_TOKEN_TTL_SECONDS
        if kind == "device"
        else MAX_TOKEN_TTL_SECONDS
    )


def issue(
    addon_id: str,
    *,
    subject: str,
    kind: str,
    actor_user_id: int | None = None,
    grant_ids: list[str] | tuple[str, ...] | None = None,
    project_id: str | None = None,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
    now: int | None = None,
) -> str:
    issued = int(time.time()) if now is None else int(now)
    if actor_user_id is not None and (
        not isinstance(actor_user_id, int)
        or isinstance(actor_user_id, bool)
        or actor_user_id <= 0
    ):
        raise AddonTokenError("actor user IDが不正です")
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 1 <= ttl_seconds <= _max_ttl_for_kind(kind)
    ):
        raise AddonTokenError("token TTLが不正です")
    if project_id is not None and (
        not isinstance(project_id, str)
        or not project_id
        or len(project_id) > 128
        or project_id in {".", ".."}
        or project_id.startswith(".")
        or any(character in project_id for character in "/\\\x00")
    ):
        raise AddonTokenError("project IDが不正です")
    delegated_grants = list(dict.fromkeys(grant_ids or ()))
    if len(delegated_grants) > 8 or any(
        not isinstance(value, str)
        or not value.startswith("grant:")
        or len(value) > 128
        for value in delegated_grants
    ):
        raise AddonTokenError("delegated grant IDが不正です")
    claims: dict[str, Any] = {
        "aud": addon_id,
        "sub": subject,
        "kind": kind,
        "iat": issued,
        "exp": issued + ttl_seconds,
        "nonce": secrets.token_urlsafe(12),
    }
    if actor_user_id is not None:
        claims["actor_user_id"] = actor_user_id
    if grant_ids is not None:
        claims["grant_ids"] = delegated_grants
    if project_id is not None:
        claims["project_id"] = project_id
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    encoded = _b64encode(payload)
    signature = _b64encode(
        hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}"


def verify(
    token: str,
    *,
    addon_id: str,
    kind: str,
    subject: str | None = None,
    max_ttl_seconds: int = TOKEN_TTL_SECONDS,
    now: int | None = None,
) -> dict[str, Any]:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            raise AddonTokenError("token signatureが一致しません")
        payload = json.loads(_b64decode(encoded))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if isinstance(exc, AddonTokenError):
            raise
        raise AddonTokenError("tokenが不正です") from exc
    current = int(time.time()) if now is None else int(now)
    if payload.get("aud") != addon_id or payload.get("kind") != kind:
        raise AddonTokenError("token scopeが一致しません")
    if subject is not None and payload.get("sub") != subject:
        raise AddonTokenError("token subjectが一致しません")
    if not isinstance(payload.get("iat"), int) or not isinstance(
        payload.get("exp"), int
    ):
        raise AddonTokenError("token timeが不正です")
    if (
        not isinstance(max_ttl_seconds, int)
        or isinstance(max_ttl_seconds, bool)
        or not 1 <= max_ttl_seconds <= _max_ttl_for_kind(kind)
        or payload["iat"] > current + 30
        or payload["exp"] <= current
        or payload["exp"] - payload["iat"] > max_ttl_seconds
    ):
        raise AddonTokenError("tokenの有効期限が切れています")
    return payload
