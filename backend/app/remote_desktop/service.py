"""リモート接続の設定管理。パスワード等は暗号化保存する。"""
from __future__ import annotations

import json

from app.models import RemoteConnection
from app.security.crypto import decrypt_text, encrypt_text

# guacd へ渡すパラメータ名（プロトコル別）
SECRET_KEYS = {"password", "private-key", "passphrase"}


def build_guacd_params(conn: RemoteConnection) -> dict[str, str]:
    """guacd の connect に渡すパラメータ辞書を組み立てる。"""
    params: dict[str, str] = {
        "hostname": conn.host,
        "port": str(conn.port),
    }
    if conn.username:
        params["username"] = conn.username
    # 非機微パラメータ
    try:
        params.update({k: str(v) for k, v in json.loads(conn.params_json or "{}").items()})
    except (json.JSONDecodeError, AttributeError):
        pass
    # RDP は既定で証明書無視（自己署名対応）。ユーザー設定があれば上書きされる
    if conn.protocol == "rdp":
        params.setdefault("ignore-cert", "true")
        params.setdefault("resize-method", "display-update")
        # security 既定は "any"（xrdp と互換。Windows/NLA は接続設定で nla を選択）
        params.setdefault("security", "any")
        # xrdp + FreeRDP2(guacd) はビットマップ/グリフキャッシュが噛み合わず、
        # 「再描画イベントが起きた領域しか表示されない（他は黒）」状態になるため無効化する
        params.setdefault("disable-bitmap-caching", "true")
        params.setdefault("disable-offscreen-caching", "true")
        params.setdefault("disable-glyph-caching", "true")
    # 機微パラメータ
    if conn.secret_params_encrypted:
        try:
            secrets = json.loads(decrypt_text(conn.secret_params_encrypted))
            params.update({k: str(v) for k, v in secrets.items()})
        except Exception:
            pass
    return params


def set_secret_params(conn: RemoteConnection, secrets: dict[str, str]) -> None:
    filtered = {k: v for k, v in secrets.items() if v}
    conn.secret_params_encrypted = encrypt_text(json.dumps(filtered)) if filtered else None


def to_out(conn: RemoteConnection) -> dict:
    has_secret = bool(conn.secret_params_encrypted)
    return {
        "id": conn.id,
        "name": conn.name,
        "protocol": conn.protocol,
        "host": conn.host,
        "port": conn.port,
        "username": conn.username,
        "params": json.loads(conn.params_json or "{}"),
        "has_password": has_secret,
        "is_self": bool(conn.is_self),
        "created_at": conn.created_at,
    }
