"""OpenCode Zen（無料モデル）の API キーの窓口。

Zen の無料モデルは OpenCode の中からしか使えない（他の道具は 403）ので、ControlDeck のゲートウェイは通らない。
開発用サンドボックスの箱の OpenCode が opencode.ai へ直接繋ぐときだけ、環境変数 OPENCODE_API_KEY で渡す。
保存は cloud.py のキーと同じ部品（security/crypto の暗号化・0600・原子的な置き換え）。値は返さず末尾 4 文字だけ。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.config import data_dir

MAX_LENGTH = 512


class ZenKeyError(ValueError):
    pass


def _path() -> Path:
    return data_dir() / "opencode-zen.json"


def _read() -> dict:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def get() -> str:
    """復号したキー。無ければ空。"""
    from app.security.crypto import decrypt_text

    token = _read().get("key")
    if not token:
        return ""
    try:
        return decrypt_text(str(token))
    except Exception:  # noqa: BLE001 - 秘密鍵が変わった等。キー無しとして扱い、値は出さない
        return ""


def hint() -> str:
    return str(_read().get("key_hint") or "")


def configured() -> bool:
    return bool(_read().get("key"))


def set(key: str) -> None:  # noqa: A001
    from app.security.crypto import encrypt_text

    key = (key or "").strip()
    if not key:
        clear()
        return
    if len(key) > MAX_LENGTH or any(ch.isspace() for ch in key):
        raise ZenKeyError("キーの形が正しくありません")
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": encrypt_text(key), "key_hint": key[-4:]}), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def clear() -> None:
    try:
        _path().unlink()
    except FileNotFoundError:
        pass
