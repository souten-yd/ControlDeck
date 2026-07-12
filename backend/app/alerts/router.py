from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts.engine import METRIC_LABELS
from app.audit import service as audit
from app.database import get_db
from app.models import AlertEvent, AlertRule, NotificationChannel, User
from app.security.crypto import decrypt_text, encrypt_text
from app.security.deps import require_permission

router = APIRouter(tags=["alerts"])

# 監視の閲覧は全ロール、編集は設定権限
view_dep = require_permission("system.view")
edit_dep = require_permission("settings.manage")


# ---- 通知チャンネル ----
class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    channel_type: str = Field(pattern="^(discord|slack|webhook)$")
    url: str = Field(min_length=8, max_length=1024)
    enabled: bool = True


def _channel_out(ch: NotificationChannel) -> dict:
    try:
        url = decrypt_text(ch.url_encrypted)
        masked = url[:24] + "…" if len(url) > 24 else url
    except Exception:
        masked = "(復号失敗)"
    return {"id": ch.id, "name": ch.name, "channel_type": ch.channel_type, "url_preview": masked, "enabled": ch.enabled}


@router.get("/alert-channels")
def list_channels(user: User = Depends(view_dep), db: Session = Depends(get_db)):
    rows = db.execute(select(NotificationChannel)).scalars().all()
    return [_channel_out(c) for c in rows]


@router.post("/alert-channels", status_code=201)
def create_channel(body: ChannelBody, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL は http(s) で指定してください")
    ch = NotificationChannel(
        name=body.name, channel_type=body.channel_type,
        url_encrypted=encrypt_text(body.url), enabled=body.enabled,
    )
    db.add(ch)
    db.commit()
    audit.record(db, "alert.channel_create", user=user, resource_type="channel", resource_id=str(ch.id), request=request)
    return _channel_out(ch)


@router.post("/alert-channels/{channel_id}/test")
async def test_channel(channel_id: int, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    ch = db.get(NotificationChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="チャンネルが見つかりません")
    from app.alerts.notify import send_notification

    ok = await send_notification(ch.channel_type, decrypt_text(ch.url_encrypted), "Control Deck テスト通知", "この通知が届けば設定は正常です。")
    return {"ok": ok}


@router.delete("/alert-channels/{channel_id}")
def delete_channel(channel_id: int, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    ch = db.get(NotificationChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="チャンネルが見つかりません")
    db.delete(ch)
    db.commit()
    audit.record(db, "alert.channel_delete", user=user, resource_type="channel", resource_id=str(channel_id), request=request)
    return {"ok": True}


# ---- アラートルール ----
class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    metric: str = Field(pattern="^(cpu_percent|memory_percent|cpu_temp_c|gpu_percent|gpu_temp_c|vram_percent|disk_percent|app_down)$")
    operator: str = Field(default="gt", pattern="^(gt|gte|lt|lte)$")
    threshold: float = 90.0
    duration_seconds: int = Field(default=60, ge=0, le=86400)
    cooldown_seconds: int = Field(default=600, ge=0, le=86400)
    app_id: int | None = None
    channel_ids: list[int] = []
    enabled: bool = True


def _rule_out(r: AlertRule) -> dict:
    return {
        "id": r.id, "name": r.name, "metric": r.metric, "metric_label": METRIC_LABELS.get(r.metric, r.metric),
        "operator": r.operator, "threshold": r.threshold, "duration_seconds": r.duration_seconds,
        "cooldown_seconds": r.cooldown_seconds, "app_id": r.app_id,
        "channel_ids": json.loads(r.channel_ids_json or "[]"), "enabled": r.enabled,
        "last_triggered_at": r.last_triggered_at,
    }


@router.get("/alert-rules")
def list_rules(user: User = Depends(view_dep), db: Session = Depends(get_db)):
    return [_rule_out(r) for r in db.execute(select(AlertRule).order_by(AlertRule.name)).scalars().all()]


@router.post("/alert-rules", status_code=201)
def create_rule(body: RuleBody, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    if body.metric == "app_down" and body.app_id is None:
        raise HTTPException(status_code=422, detail="アプリ停止アラートには対象アプリが必要です")
    r = AlertRule(
        name=body.name, metric=body.metric, operator=body.operator, threshold=body.threshold,
        duration_seconds=body.duration_seconds, cooldown_seconds=body.cooldown_seconds,
        app_id=body.app_id, channel_ids_json=json.dumps(body.channel_ids), enabled=body.enabled,
    )
    db.add(r)
    db.commit()
    audit.record(db, "alert.rule_create", user=user, resource_type="alert_rule", resource_id=str(r.id), request=request, metadata={"name": r.name})
    return _rule_out(r)


@router.patch("/alert-rules/{rule_id}")
def update_rule(rule_id: int, body: RuleBody, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="ルールが見つかりません")
    r.name, r.metric, r.operator, r.threshold = body.name, body.metric, body.operator, body.threshold
    r.duration_seconds, r.cooldown_seconds, r.app_id = body.duration_seconds, body.cooldown_seconds, body.app_id
    r.channel_ids_json, r.enabled = json.dumps(body.channel_ids), body.enabled
    db.commit()
    audit.record(db, "alert.rule_update", user=user, resource_type="alert_rule", resource_id=str(rule_id), request=request)
    return _rule_out(r)


@router.delete("/alert-rules/{rule_id}")
def delete_rule(rule_id: int, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    r = db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="ルールが見つかりません")
    db.delete(r)
    db.commit()
    audit.record(db, "alert.rule_delete", user=user, resource_type="alert_rule", resource_id=str(rule_id), request=request)
    return {"ok": True}


# ---- アラートイベント ----
@router.get("/alert-events")
def list_events(
    active_only: bool = False,
    limit: int = Query(default=50, le=200),
    user: User = Depends(view_dep),
    db: Session = Depends(get_db),
):
    stmt = select(AlertEvent).order_by(AlertEvent.triggered_at.desc()).limit(limit)
    if active_only:
        stmt = stmt.where(AlertEvent.status == "active")
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": e.id, "rule_name": e.rule_name, "message": e.message, "value": e.value,
            "status": e.status, "triggered_at": e.triggered_at, "resolved_at": e.resolved_at,
        }
        for e in rows
    ]
