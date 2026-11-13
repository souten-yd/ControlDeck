"""Authenticated AMD fan management; never accepts a sysfs path or shell command."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.monitoring import fan_control
from app.security.deps import require_permission

router = APIRouter(prefix="/system/fan-control", tags=["fan-control"])


@router.get("")
def get_fan_control(user: User = Depends(require_permission("system.view"))):
    try:
        return fan_control.status()
    except fan_control.FanControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/history")
def get_fan_history(since: float = 0.0, user: User = Depends(require_permission("system.view"))):
    return fan_control.history(since)


class AutoTuneRequest(BaseModel):
    action: Literal["start", "cancel"]


@router.post("/autotune")
def post_fan_autotune(
    body: AutoTuneRequest,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = fan_control.autotune(body.action)
    except fan_control.FanControlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "system.fan_control.autotune", user=user, request=request,
                 resource_type="amd-gpu", resource_id="active", metadata={"action": body.action})
    return result


@router.put("")
def put_fan_control(
    body: fan_control.FanSettings,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = fan_control.configure(body)
    except (fan_control.FanControlError, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        db, "system.fan_control", user=user, request=request,
        resource_type="amd-gpu", resource_id=body.bdf or "auto",
        metadata={"enabled": body.enabled, "targets_c": body.targets_c,
                  "min_percent": body.min_percent, "allow_zero_rpm": body.allow_zero_rpm,
                  "startup_mode": body.startup_mode,
                  "control_mode": body.control_mode},
    )
    return result
