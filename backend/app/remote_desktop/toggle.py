"""リモートデスクトップの有効・無効（設定の 1 項目、全体で 1 つ）。

2026-11-21 利用者「リモートデスクトップは一旦停止する。設定に有効・無効のボタンを。オフになったら、
通信そのものやサーバーの起動やホームのアイコンも消す」。

- 保存: data/remote_desktop.json（既存の設定の置き場 = data_dir()/<機能>.json と同じ作り）。既定は有効（今までどおり）。
- 強制は ControlDeck 側: /api/v1/remote/* と tunnel の WebSocket を断る（router.py の依存）、
  /api/v1/meta の enabled_features から "remote-desktop" を外す（ナビ・ホームのアイコン・/remote の画面）。
- サーバー（xrdp）: 特権補助の system-service（stop/disable, enable/start）で止める・起こす。
  補助や一覧が古いときは設定だけ保存し、「補助の更新が要ります」を結果に残して画面に出す（隠さない）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.applications import system_services
from app.config import data_dir, isolated

FEATURE_ID = "remote-desktop"
DISABLED_DETAIL = "リモートデスクトップは無効です（設定で有効にできます）"
HELPER_UPDATE_MESSAGE = "補助の更新が要ります（sudo ./deck.sh service を 1 回）"

# xrdp 本体と、そのセッション管理。止める順は本体 → sesman、起こす順は sesman → 本体。
SERVICE_IDS = ("remote-desktop", "remote-desktop-session")

# 古い補助（enable/disable を知らない）や空の一覧が返す文言。これらは「補助の更新が要る」へ読み替える。
_OLD_HELPER_MESSAGES = (
    "不正なsystem service操作です",
    "system serviceが許可されていません",
    "system service定義または操作が許可されていません",
    "特権helperが導入されていません",
)


def _path() -> Path:
    return data_dir() / "remote_desktop.json"


def _read() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(value: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_enabled() -> bool:
    return bool(_read().get("enabled", True))


def last_apply() -> dict | None:
    value = _read().get("apply")
    return value if isinstance(value, dict) else None


def view() -> dict:
    return {"enabled": is_enabled(), "apply": last_apply()}


def _run_service(service_id: str, action: str) -> tuple[str, str]:
    """('ok'|'needs_helper_update'|'error', 利用者向けの文) を返す。"""
    try:
        service = system_services.require_service(service_id)
    except system_services.SystemServiceError:
        return "needs_helper_update", HELPER_UPDATE_MESSAGE
    if action not in service.actions:
        return "needs_helper_update", HELPER_UPDATE_MESSAGE
    ok, message = system_services.control(service_id, action)
    if ok:
        return "ok", ""
    if any(marker in message for marker in _OLD_HELPER_MESSAGES):
        return "needs_helper_update", HELPER_UPDATE_MESSAGE
    return "error", message or "サーバー（xrdp）を操作できませんでした"


def apply_server(enabled: bool) -> dict:
    """xrdp を止めて起動時にも立ち上げない（無効）／起動時に立ち上げて起こす（有効）。結果を保存して返す。"""
    if isolated():
        # 検証用インスタンス（E2E など）は、同じ機械の本番の xrdp を触らない。設定の保存と強制だけを行う。
        result = {"state": "skipped", "message": "検証用インスタンスのためサーバーは操作しません", "enabled": enabled, "at": time.time()}
        data = _read()
        data["apply"] = result
        _write(data)
        return result
    if enabled:
        steps = [(sid, action) for sid in reversed(SERVICE_IDS) for action in ("enable", "start")]
    else:
        steps = [(sid, action) for sid in SERVICE_IDS for action in ("stop", "disable")]
    state, message = "ok", ""
    for service_id, action in steps:
        step_state, step_message = _run_service(service_id, action)
        if step_state == "needs_helper_update":
            state, message = step_state, step_message
            break  # 補助が古いなら残りも同じ。
        if step_state == "error" and state == "ok":
            state, message = "error", step_message  # 失敗しても残りの手順は試す（止めるべきものを残さない）。
    result = {"state": state, "message": message, "enabled": enabled, "at": time.time()}
    data = _read()
    data["apply"] = result
    _write(data)
    return result


def set_enabled(enabled: bool) -> dict:
    data = _read()
    data["enabled"] = bool(enabled)
    _write(data)
    apply_server(bool(enabled))
    return view()
