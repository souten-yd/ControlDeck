"""Home の通知の 1 行（利用者が知っておくべきことのうち、いま未解決で最も重要な 1 件）。

2026-11-23 利用者の依頼。既に持っている「利用者が知るべきこと」の元（監視の警報・失敗したアプリ・更新の失敗・
承認待ち）を集め、重要度（エラー > 警告 > 注意 > 情報）→ 新しさの順に並べて返す。新しい集め先は作らない。
解決の判定は元の状態そのもの（警報が解除された・アプリが動いた・次の更新が成功した・承認が済んだ）なので、
ここには「解決済み」の記録を持たない。持つのは利用者ごとの「閉じた」印だけ（data_dir/push/home-notice-dismissed.json）。
閉じた印の鍵には元の id を含める（同じ種類でも新しく起きたものは、また出る）。いまの一覧に無い鍵は読むたびに掃除する。
お休みモード（通知の送信）とは別: ここは画面の表示なので、静かな時間でも出す。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import data_dir
from app.database import get_db
from app.models import AlertEvent, ManagedApplication, User
from app.security.deps import get_current_user, user_permissions

router = APIRouter(tags=["alerts"])
logger = logging.getLogger("control_deck.alerts.home_notice")

# 重要度の順位（大きいほど重要）。画面の札: error=エラー / warning=警告 / attention=注意 / info=情報。
RANK = {"error": 3, "warning": 2, "attention": 1, "info": 0}
# 監視の警報のうち「止まっている・壊れている」ものはエラー、数値の超過は警告。
_ERROR_METRICS = {"app_down", "app_health_failed", "app_restart_loop"}
_UPDATE_FAILURE_DAYS = 7
_lock = threading.Lock()


def _path():
    return data_dir() / "push" / "home-notice-dismissed.json"


def _read_all() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_all(everything: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(everything, handle)
    os.replace(tmp, path)


def _ts(value: datetime | float | int | None) -> float:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    return float(value or 0)


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _collect(db: Session, user: User, can) -> list[dict]:
    items: list[dict] = []
    # 1) 監視の警報（解除されれば status が変わって消える）
    if can("system.view"):
        rules = {}
        rows = db.execute(select(AlertEvent).where(AlertEvent.status == "active")
                          .order_by(AlertEvent.triggered_at.desc()).limit(20)).scalars().all()
        if rows:
            from app.models import AlertRule

            rules = {r.id: r.metric for r in db.execute(select(AlertRule)).scalars().all()}
        for e in rows:
            level = "error" if rules.get(e.rule_id) in _ERROR_METRICS else "warning"
            items.append({"key": f"alert:{e.id}", "level": level, "title": e.rule_name or "警報",
                          "message": _short(e.message), "to": "/settings/notifications", "at": _ts(e.triggered_at)})
    # 2) 失敗したアプリ（動き直せば消える）
    if can("apps.view"):
        from app.applications import service as apps

        for a in db.execute(select(ManagedApplication)).scalars().all():
            try:
                runtime = apps.runtime_info(a, include_health=False)
            except Exception:  # noqa: BLE001 - 1 つの取得失敗で行全体を止めない
                continue
            if getattr(runtime, "status", "") == "FAILED":
                items.append({"key": f"app:{a.id}", "level": "error", "title": f"{a.name} が失敗しています",
                              "message": "ログを確認してください", "to": f"/logs?app={a.id}&stream=stderr",
                              "at": _ts(a.updated_at)})
    if can("settings.manage"):
        # 3) 更新の失敗（次の更新が成功すれば状態が置き換わって消える。古いものは出さない）
        try:
            from app.maintenance import self_update

            state = self_update._saved_state()
            finished = _ts(state.get("finished_at"))
            if state.get("phase") == "failed" and time.time() - finished < _UPDATE_FAILURE_DAYS * 86400:
                items.append({"key": f"update:{int(finished)}", "level": "error", "title": "更新に失敗しました",
                              "message": _short(state.get("error") or "原因不明。前の版で動いています"),
                              "to": "/settings/system", "at": finished})
        except Exception:  # noqa: BLE001
            logger.debug("更新の状態を読めません", exc_info=True)
        # 4) 承認待ち（承認・却下で消える）
        try:
            from app.maintenance import approvals

            for rec in approvals.list_all(include_done=False):
                if rec.get("status") == "pending":
                    items.append({"key": f"approval:{rec.get('id')}", "level": "attention",
                                  "title": "承認待ち", "message": _short(rec.get("title") or ""),
                                  "to": "/?approvals=1", "at": _ts(rec.get("created_at"))})
        except Exception:  # noqa: BLE001
            logger.debug("承認待ちを読めません", exc_info=True)
    return items


def build(db: Session, user: User, can) -> dict:
    """未解決で、本人が閉じていないものを重要度 → 新しさの順に返す。"""
    items = _collect(db, user, can)
    alive = {i["key"] for i in items}
    with _lock:
        everything = _read_all()
        mine = [k for k in everything.get(str(user.id), []) if isinstance(k, str)]
        kept = [k for k in mine if k in alive]  # 解決した元の「閉じた」印は掃除する
        if kept != mine:
            everything[str(user.id)] = kept
            _write_all(everything)
    open_items = [i for i in items if i["key"] not in kept]
    open_items.sort(key=lambda i: (RANK[i["level"]], i["at"]), reverse=True)
    return {"items": open_items, "total": len(open_items)}


class DismissBody(BaseModel):
    key: str = Field(min_length=1, max_length=80)


@router.get("/home-notices")
def home_notices(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    perms = user_permissions(user)
    return build(db, user, lambda perm: perm in perms)


@router.post("/home-notices/dismiss")
def dismiss_home_notice(body: DismissBody, user: User = Depends(get_current_user)):
    """この件を閉じる（本人の画面だけ。元の状態は変えない）。"""
    with _lock:
        everything = _read_all()
        mine = [k for k in everything.get(str(user.id), []) if isinstance(k, str)]
        if body.key not in mine:
            mine.append(body.key)
        everything[str(user.id)] = mine[-200:]
        _write_all(everything)
    return {"ok": True}
