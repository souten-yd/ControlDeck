from __future__ import annotations

import json
import logging
from email.utils import parseaddr

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts.engine import APP_METRICS, METRIC_LABELS
from app.audit import service as audit
from app.database import get_db
from app.models import AlertEvent, AlertRule, ManagedApplication, NotificationChannel, User
from app.security.crypto import decrypt_text, encrypt_text
from app.security.deps import require_permission

router = APIRouter(tags=["alerts"])
logger = logging.getLogger("control_deck.alerts")

# 監視の閲覧は全ロール、編集は設定権限
view_dep = require_permission("system.view")
edit_dep = require_permission("settings.manage")


# ---- 通知チャンネル ----
class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    channel_type: str = Field(pattern="^(discord|slack|webhook|email)$")
    url: str = Field(default="", max_length=2048)
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: str = Field(default="starttls", pattern="^(starttls|tls|none)$")
    smtp_username: str = Field(default="", max_length=320)
    # 秘密値はPydantic field errorのinputへ反射させずendpoint内で長さだけ検証する。
    smtp_password: str = ""
    from_address: str = Field(default="", max_length=320)
    to_addresses: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True

def _valid_email(value: str) -> bool:
    if not value or len(value) > 320 or any(c in value for c in "\r\n\x00"):
        return False
    name, address = parseaddr(value)
    return not name and address == value and address.count("@") == 1


def _email_destination(body: ChannelBody) -> str:
    return json.dumps(
        {
            "host": body.smtp_host,
            "port": body.smtp_port,
            "security": body.smtp_security,
            "username": body.smtp_username,
            "password": body.smtp_password,
            "from_address": body.from_address,
            "to_addresses": body.to_addresses,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _masked_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if local and domain else "***"


def _channel_out(ch: NotificationChannel) -> dict:
    try:
        destination = decrypt_text(ch.url_encrypted)
        if ch.channel_type == "email":
            settings = json.loads(destination)
            recipients = settings.get("to_addresses", [])
            masked = f"{_masked_email(settings.get('from_address', ''))} → {len(recipients)}件"
        else:
            masked = destination[:24] + "…" if len(destination) > 24 else destination
    except Exception:
        masked = "(復号失敗)"
    return {"id": ch.id, "name": ch.name, "channel_type": ch.channel_type, "url_preview": masked, "enabled": ch.enabled}


@router.get("/alert-channels")
def list_channels(user: User = Depends(view_dep), db: Session = Depends(get_db)):
    rows = db.execute(select(NotificationChannel)).scalars().all()
    return [_channel_out(c) for c in rows]


@router.post("/alert-channels", status_code=201)
def create_channel(body: ChannelBody, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    if body.channel_type == "email":
        invalid = (
            not body.smtp_host
            or any(c in body.smtp_host for c in "/\\\r\n\x00")
            or len(body.smtp_password) > 1024
            or not _valid_email(body.from_address)
            or not body.to_addresses
            or any(not _valid_email(value) for value in body.to_addresses)
        )
        if invalid:
            raise HTTPException(status_code=422, detail="メール通知設定を正しく指定してください")
    elif not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL は http(s) で指定してください")
    destination = _email_destination(body) if body.channel_type == "email" else body.url
    ch = NotificationChannel(
        name=body.name, channel_type=body.channel_type,
        url_encrypted=encrypt_text(destination), enabled=body.enabled,
    )
    db.add(ch)
    db.commit()
    audit.record(db, "alert.channel_create", user=user, resource_type="channel", resource_id=str(ch.id), request=request)
    return _channel_out(ch)


@router.post("/alert-channels/{channel_id}/test")
async def test_channel(channel_id: int, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    ch = db.get(NotificationChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="チャンネルが見つかりません")
    from app.alerts.notify import send_notification

    try:
        destination = decrypt_text(ch.url_encrypted)
    except Exception as error:
        logger.warning("通知設定の復号失敗 (%s, %s)", ch.channel_type, type(error).__name__)
        audit.record(
            db, "alert.channel_test", user=user, resource_type="channel", resource_id=str(ch.id),
            result="failure", request=request, metadata={"channel_type": ch.channel_type, "reason": "decrypt"},
        )
        raise HTTPException(status_code=500, detail="チャンネル設定を読み込めません") from None
    ok = await send_notification(ch.channel_type, destination, "Control Deck テスト通知", "この通知が届けば設定は正常です。")
    audit.record(
        db, "alert.channel_test", user=user, resource_type="channel", resource_id=str(ch.id),
        result="success" if ok else "failure", request=request,
        metadata={"channel_type": ch.channel_type},
    )
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
    metric: str = Field(pattern="^(cpu_percent|memory_percent|cpu_temp_c|gpu_percent|gpu_temp_c|vram_percent|disk_percent|app_down|app_health_failed|app_restart_loop)$")
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
    app_id = _validated_rule_app(body, db)
    r = AlertRule(
        name=body.name, metric=body.metric, operator=body.operator, threshold=body.threshold,
        duration_seconds=body.duration_seconds, cooldown_seconds=body.cooldown_seconds,
        app_id=app_id, channel_ids_json=json.dumps(body.channel_ids), enabled=body.enabled,
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
    app_id = _validated_rule_app(body, db)
    r.name, r.metric, r.operator, r.threshold = body.name, body.metric, body.operator, body.threshold
    r.duration_seconds, r.cooldown_seconds, r.app_id = body.duration_seconds, body.cooldown_seconds, app_id
    r.channel_ids_json, r.enabled = json.dumps(body.channel_ids), body.enabled
    db.commit()
    audit.record(db, "alert.rule_update", user=user, resource_type="alert_rule", resource_id=str(rule_id), request=request)
    return _rule_out(r)


def _validated_rule_app(body: RuleBody, db: Session) -> int | None:
    if body.metric not in APP_METRICS:
        return None
    if body.app_id is None or db.get(ManagedApplication, body.app_id) is None:
        raise HTTPException(status_code=422, detail="アプリ監視アラートには登録済みの対象アプリが必要です")
    if body.metric == "app_restart_loop" and body.threshold < 1:
        raise HTTPException(status_code=422, detail="再起動回数のしきい値は1以上にしてください")
    return body.app_id


@router.delete("/alert-rules/{rule_id}")
def delete_rule(rule_id: int, request: Request, user: User = Depends(edit_dep), db: Session = Depends(get_db)):
    from sqlalchemy import delete as sql_delete

    r = db.get(AlertRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="ルールが見つかりません")
    # 関連イベントも削除（残留したアラートがダッシュボードに残らないように）
    db.execute(sql_delete(AlertEvent).where(AlertEvent.rule_id == rule_id))
    db.delete(r)
    db.commit()
    audit.record(db, "alert.rule_delete", user=user, resource_type="alert_rule", resource_id=str(rule_id), request=request)
    return {"ok": True}


@router.post("/alert-events/dismiss")
def dismiss_events(
    request: Request,
    event_id: int | None = None,
    user: User = Depends(edit_dep),
    db: Session = Depends(get_db),
):
    """アクティブなアラートを手動で解除する。event_id 未指定なら全 active を解除。"""
    from sqlalchemy import update as sql_update

    from app.models import utcnow

    stmt = sql_update(AlertEvent).where(AlertEvent.status == "active").values(
        status="dismissed", resolved_at=utcnow()
    )
    if event_id is not None:
        stmt = stmt.where(AlertEvent.id == event_id)
    result = db.execute(stmt)
    db.commit()
    audit.record(db, "alert.dismiss", user=user, resource_type="alert_event", resource_id=str(event_id or "all"), request=request)
    return {"ok": True, "dismissed": result.rowcount}


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
