"""Authenticated AMD fan management; never accepts a sysfs path or shell command."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

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
                  "min_percent": body.min_percent},
    )
    return result
