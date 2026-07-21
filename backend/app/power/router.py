"""PC電源管理。即時操作はlogind、予約は永続systemdユーザーtimerを使う。"""
from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.applications import service as application_service
from app.audit import service as audit
from app.auth import totp
from app.config import get_config
from app.database import get_db
from app.models import ManagedApplication, User, WorkflowExecution
from app.power import scheduler
from app.security import ratelimit
from app.security.deps import require_permission

router = APIRouter(prefix="/system", tags=["power"])
logger = logging.getLogger(__name__)


class PowerRequest(BaseModel):
    action: str = Field(pattern="^(reboot|shutdown)$")


class PowerNowRequest(BaseModel):
    mode: Literal["graceful", "immediate"] = "graceful"
    totp_code: str = Field(default="", max_length=32)


class ScheduleRequest(BaseModel):
    action: str = Field(pattern="^(reboot|shutdown)$")
    delay_minutes: int = Field(ge=1, le=60 * 24)
    totp_code: str = Field(default="", max_length=32)


def _execute(action: str, mode: Literal["graceful", "immediate"] = "graceful") -> tuple[bool, str]:
    command = "reboot" if action == "reboot" else "poweroff"
    argv = ["systemctl", command] if mode == "graceful" else ["systemctl", "--force", command]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


@router.post("/reboot")
def reboot(
    request: Request, body: PowerNowRequest | None = None,
    user: User = Depends(require_permission("power.manage")), db: Session = Depends(get_db),
):
    payload = body or PowerNowRequest()
    return _power_now("reboot", payload, request, user, db)


@router.post("/shutdown")
def shutdown(
    request: Request, body: PowerNowRequest | None = None,
    user: User = Depends(require_permission("power.manage")), db: Session = Depends(get_db),
):
    payload = body or PowerNowRequest()
    return _power_now("shutdown", payload, request, user, db)


@router.post("/platform/reload", status_code=202)
def reload_platform(
    request: Request,
    user: User = Depends(require_permission("power.manage")),
    db: Session = Depends(get_db),
):
    """応答後にsystemd user serviceを再起動する。Webプロセスの子として常駐させない。"""
    unit = f"control-deck-web-reload-{time.time_ns()}"
    argv = [
        "systemd-run", "--user", f"--unit={unit}", "--on-active=1s", "--collect",
        "/usr/bin/systemctl", "--user", "restart", "control-deck-web.service",
    ]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        ok, error = result.returncode == 0, result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok, error = False, str(exc)
    audit.record(
        db, "platform.reload", user=user, resource_type="system",
        result="success" if ok else "failure", request=request,
        metadata={} if ok else {"error": error[:300]},
    )
    if not ok:
        logger.warning("platform reload schedule failed: %s", error)
        raise HTTPException(status_code=502, detail="Control Deckの再読み込みを予約できませんでした")
    return {"ok": True, "reload_after_ms": 1000}


def _require_totp(code: str, *, action: str, request: Request, user: User, db: Session) -> None:
    if not get_config().security.require_totp_for_power:
        return
    ip = request.client.host if request.client else "unknown"
    rate_key = f"power-reauth:{ip}:{user.id}"
    if not ratelimit.check(rate_key, max_attempts=5, window_seconds=60):
        audit.record(db, f"power.{action}.reauth", user=user, resource_type="system", result="failure",
                     request=request, metadata={"reason": "rate_limited"})
        raise HTTPException(status_code=429, detail="認証試行回数が多すぎます。しばらく待ってから再試行してください")
    if not user.totp_enabled:
        audit.record(db, f"power.{action}.reauth", user=user, resource_type="system", result="failure",
                     request=request, metadata={"reason": "totp_not_enabled"})
        raise HTTPException(status_code=409, detail="電源操作にはTOTPの有効化が必要です")
    secret = totp.get_secret(user)
    valid = bool(secret and code and totp.verify_code(secret, code))
    if not valid and code:
        valid = totp.consume_recovery_code(user, code)
        if valid:
            db.commit()
    if not valid:
        ratelimit.record(rate_key)
        audit.record(db, f"power.{action}.reauth", user=user, resource_type="system", result="failure",
                     request=request, metadata={"reason": "invalid_code"})
        raise HTTPException(status_code=403, detail="TOTP認証コードが正しくありません")
    ratelimit.reset(rate_key)


def _power_now(action: str, body: PowerNowRequest, request: Request, user: User, db: Session):
    _require_totp(body.totp_code, action=action, request=request, user=user, db=db)
    ok, err = _execute(action, body.mode)
    audit.record(
        db, f"power.{action}", user=user, resource_type="system",
        result="success" if ok else "failure", request=request,
        metadata={"mode": body.mode, **({} if ok else {"error": err[:300]})},
    )
    if not ok:
        logger.warning("power action failed: action=%s error=%s", action, err)
        raise HTTPException(
            status_code=502,
            detail=f"{action} を実行できませんでした。サーバーログを確認してください。",
        )
    return {"ok": True}


@router.get("/power/safety")
def power_safety(
    user: User = Depends(require_permission("power.manage")), db: Session = Depends(get_db),
):
    """確認画面用の件数だけを返し、session IDや本文は公開しない。"""
    applications = db.execute(select(ManagedApplication)).scalars().all()
    active_statuses = {"STARTING", "RUNNING", "STOPPING", "RESTARTING", "DEGRADED"}
    running_apps = sum(
        application_service.runtime_info(item, include_health=False).status in active_statuses
        for item in applications if item.application_type != "url_shortcut"
    )
    running_workflows = db.scalar(
        select(func.count()).select_from(WorkflowExecution).where(
            WorkflowExecution.status.in_(["QUEUED", "RUNNING", "WAITING"])
        )
    ) or 0
    # どちらも接続本文・session IDを読まず、現在のWebSocket数だけを返す。
    from app.remote_desktop import activity as remote_activity
    from app.terminals.router import streams as terminal_streams

    return {
        "running_apps": running_apps,
        "running_workflows": running_workflows,
        "connected_terminals": terminal_streams.stream_count(),
        "connected_remote_desktops": remote_activity.count(),
        "totp_required": get_config().security.require_totp_for_power,
        "totp_enabled": user.totp_enabled,
    }


@router.get("/power/schedule")
def get_schedule(user: User = Depends(require_permission("power.manage"))):
    return scheduler.read_state()


@router.post("/power/schedule")
def schedule_power(
    body: ScheduleRequest,
    request: Request,
    user: User = Depends(require_permission("power.manage")),
    db: Session = Depends(get_db),
):
    _require_totp(body.totp_code, action=f"schedule.{body.action}", request=request, user=user, db=db)
    at = datetime.now(timezone.utc) + timedelta(minutes=body.delay_minutes)
    try:
        state = scheduler.install(body.action, at, user.username)
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning("power schedule setup failed: action=%s error=%s", body.action, e)
        audit.record(db, "power.schedule", user=user, resource_type="system", result="failure",
                     request=request, metadata={"action": body.action, "error": str(e)[:300]})
        raise HTTPException(status_code=502, detail="電源予約を登録できませんでした。サーバーログを確認してください。") from e
    audit.record(
        db, "power.schedule", user=user, resource_type="system", request=request,
        metadata={"action": body.action, "at": at.isoformat(), "backend": "systemd-user-timer"},
    )
    return state


@router.delete("/power/schedule")
def cancel_schedule(
    request: Request,
    user: User = Depends(require_permission("power.manage")),
    db: Session = Depends(get_db),
):
    try:
        scheduler.cancel()
    except (OSError, RuntimeError) as e:
        logger.warning("power schedule cancellation failed: error=%s", e)
        audit.record(db, "power.schedule_cancel", user=user, resource_type="system", result="failure",
                     request=request, metadata={"error": str(e)[:300]})
        raise HTTPException(status_code=502, detail="電源予約を取消できませんでした。サーバーログを確認してください。") from e
    audit.record(db, "power.schedule_cancel", user=user, resource_type="system", request=request)
    return {"ok": True}
