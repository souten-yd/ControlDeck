"""PC電源管理。即時操作はlogind、予約は永続systemdユーザーtimerを使う。"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import require_permission
from app.power import scheduler

router = APIRouter(prefix="/system", tags=["power"])
logger = logging.getLogger(__name__)

class PowerRequest(BaseModel):
    action: str = Field(pattern="^(reboot|shutdown)$")


class ScheduleRequest(BaseModel):
    action: str = Field(pattern="^(reboot|shutdown)$")
    delay_minutes: int = Field(ge=1, le=60 * 24)


def _execute(action: str) -> tuple[bool, str]:
    argv = ["systemctl", "reboot"] if action == "reboot" else ["systemctl", "poweroff"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


@router.post("/reboot")
def reboot(request: Request, user: User = Depends(require_permission("power.manage")), db: Session = Depends(get_db)):
    return _power_now("reboot", request, user, db)


@router.post("/shutdown")
def shutdown(request: Request, user: User = Depends(require_permission("power.manage")), db: Session = Depends(get_db)):
    return _power_now("shutdown", request, user, db)


def _power_now(action: str, request: Request, user: User, db: Session):
    ok, err = _execute(action)
    audit.record(
        db, f"power.{action}", user=user, resource_type="system",
        result="success" if ok else "failure", request=request,
        metadata={} if ok else {"error": err[:300]},
    )
    if not ok:
        logger.warning("power action failed: action=%s error=%s", action, err)
        raise HTTPException(
            status_code=502,
            detail=f"{action} を実行できませんでした。サーバーログを確認してください。",
        )
    return {"ok": True}


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
