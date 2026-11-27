"""Nexarch（AI Commander）の全体の設定。自動再開で送る文言と、チームの既定（T3b: 既定のルーター・既定の役割）。

`data/ai-commander/settings.json`（0600）。コンソールごとの文言（tmux の印 @cd-commander-resume_message）が
あればそちらが勝ち、無ければここの文言、ここも無ければ DEFAULT_RESUME_MESSAGE。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.config import data_dir

DEFAULT_RESUME_MESSAGE = "続けてください"
MESSAGE_MAX = 500


def _path() -> Path:
    return data_dir() / "ai-commander" / "settings.json"


def _read() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def clean_message(value: str) -> str:
    """1 行の文言（改行・tab などの制御文字は断る）。空は「使わない＝上の段に任せる」。"""
    message = value.strip()
    if len(message) > MESSAGE_MAX or any(ord(c) < 0x20 or ord(c) == 0x7f for c in message):
        raise ValueError(f"文言は {MESSAGE_MAX} 文字以内の 1 行にしてください（改行・制御文字は使えません）")
    return message


def resume_message() -> str:
    """全体の自動再開の文言（未設定なら既定）。"""
    value = _read().get("resume_message")
    return value if isinstance(value, str) and value.strip() else DEFAULT_RESUME_MESSAGE


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def default_router() -> dict | None:
    """既定のルーター（endpoint）。未設定・壊れていれば None。"""
    value = _read().get("default_router")
    return value if isinstance(value, dict) and value.get("cli") else None


def default_roles() -> list[dict]:
    """既定の役割（新しいチームがこれを写して始まる）。未設定なら空（組み込みの 3 つを使う）。"""
    value = _read().get("default_roles")
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def set_team_defaults(router: dict | None, roles: list[dict] | None) -> dict:
    """既定のルーターと既定の役割を保存する（検証済みの値を渡す。roles が None なら変えない・[] なら組み込みに戻す）。"""
    data = _read()
    if router:
        data["default_router"] = router
    else:
        data.pop("default_router", None)
    if roles is not None:
        if roles:
            data["default_roles"] = roles
        else:
            data.pop("default_roles", None)
    _write(data)
    return get()


def get() -> dict:
    return {"resume_message": resume_message(), "default_resume_message": DEFAULT_RESUME_MESSAGE,
            "default_router": default_router(), "default_roles": default_roles()}


def set_resume_message(value: str) -> dict:
    """全体の文言を変える。空なら既定へ戻す。"""
    message = clean_message(value)
    data = _read()
    if message:
        data["resume_message"] = message
    else:
        data.pop("resume_message", None)
    _write(data)
    return get()


def message_for(row: dict) -> str:
    """そのコンソールへ送る文言: コンソールの文言 → 全体 → 既定。"""
    own = str(row.get("commander_resume_message") or "").strip()
    return own or resume_message()
