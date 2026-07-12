"""PC 電源管理。実行は systemctl（logind 経由）。root では動作させない。

予約はプロセス内タイマーで管理する（MVP 制約: Web 再起動で予約は失われる。
将来 helper + systemd timer へ移行する）。
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/system", tags=["power"])

_scheduled: dict | None = None
_task: asyncio.Task | None = None


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
        raise HTTPException(
            status_code=502,
            detail=f"{action} を実行できませんでした（権限不足の可能性）: {err or 'unknown'}",
        )
    return {"ok": True}


@router.get("/power/schedule")
def get_schedule(user: User = Depends(require_permission("power.manage"))):
    return _scheduled


@router.post("/power/schedule")
async def schedule_power(
    body: ScheduleRequest,
    request: Request,
    user: User = Depends(require_permission("power.manage")),
    db: Session = Depends(get_db),
):
    global _scheduled, _task
    if _task and not _task.done():
        _task.cancel()
    at = datetime.now(timezone.utc) + timedelta(minutes=body.delay_minutes)
    _scheduled = {"action": body.action, "at": at.isoformat(), "by": user.username}

    async def fire():
        await asyncio.sleep(body.delay_minutes * 60)
        _execute(body.action)

    _task = asyncio.create_task(fire())
    audit.record(
        db, "power.schedule", user=user, resource_type="system", request=request,
        metadata={"action": body.action, "at": at.isoformat()},
    )
    return _scheduled


@router.delete("/power/schedule")
def cancel_schedule(
    request: Request,
    user: User = Depends(require_permission("power.manage")),
    db: Session = Depends(get_db),
):
    global _scheduled, _task
    if _task and not _task.done():
        _task.cancel()
    _scheduled = None
    _task = None
    audit.record(db, "power.schedule_cancel", user=user, resource_type="system", request=request)
    return {"ok": True}
