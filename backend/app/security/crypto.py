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


def decrypt_text(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


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
