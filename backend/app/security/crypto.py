"""環境変数など秘密値の保存用暗号化（Fernet）。鍵は data_dir 内 0600 で保管。"""
from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet

from app.config import data_dir


@lru_cache
def _fernet() -> Fernet:
    key_path = data_dir() / "secret.key"
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    return Fernet(key)


def encrypt_text(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_text(token: str, *, ttl_seconds: int | None = None) -> str:
    """ttl_secondsを渡すと、発行からその秒数を過ぎたtokenを無効として扱う。"""
    if ttl_seconds is None:
        return _fernet().decrypt(token.encode()).decode()
    return _fernet().decrypt(token.encode(), ttl=ttl_seconds).decode()


SECRET_KEY_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASS",
    "API_KEY",
    "PRIVATE_KEY",
    "AUTH",
    "COOKIE",
)


def is_secret_key(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SECRET_KEY_MARKERS)


def mask_env(env: dict[str, str]) -> dict[str, str]:
    """表示・ログ用に秘密らしき値をマスクする。"""
    return {k: ("••••••" if is_secret_key(k) else v) for k, v in env.items()}


def keyed_digest(purpose: str, message: str, *, length: int = 32) -> str:
    """data_dir の鍵から作る、推測できない固定の印（hex）。

    同じ purpose と message なら何度でも同じ値になるので、DB に控えを持たずに照合できる。
    鍵そのものは外へ出ない（HMAC-SHA256）。"""
    import hashlib
    import hmac

    key = hashlib.sha256(b"control-deck-keyed-digest:" + _fernet()._signing_key).digest()  # noqa: SLF001
    return hmac.new(key, f"{purpose}\x00{message}".encode(), hashlib.sha256).hexdigest()[:length]
