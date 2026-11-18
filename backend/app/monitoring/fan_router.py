"""Authenticated AMD fan management; never accepts a sysfs path or shell command."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

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
    # 調整に使う LLM（未指定なら登録済みの最初の LLM）。止まっていれば起動してから始める
    llm: str | None = Field(default=None, max_length=120)


@router.post("/autotune")
async def post_fan_autotune(
    body: AutoTuneRequest,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    """自動調整: LLM に同じ文言で長文を書かせ続けて、熱モデルと PID の効き具合を決める（30〜90 分）。"""
    from app.models_mgmt import local_llm
    from app.monitoring import fan_tune

    if body.action == "cancel":
        return fan_tune.cancel()
    try:
        llm = fan_tune.choose_llm(body.llm)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not await local_llm.ensure_ready(llm["alias"]):
        raise HTTPException(status_code=422, detail=f"{llm['alias']} を起動できませんでした")
    try:
        result = fan_tune.start(llm)
    except fan_control.FanControlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "system.fan_control.autotune", user=user, request=request,
                 resource_type="amd-gpu", resource_id="active", metadata={"action": body.action})
    return result


@router.get("/log-summary")
def get_fan_log_summary(hours: float = 24.0, user: User = Depends(require_permission("system.view"))):
    """診断の記録（1 分ごと）から、目標を超えた回数・緊急の最大回転の回数など。"""
    return fan_control.log_summary(max(1.0, min(hours, 48.0)))


@router.post("/refit")
def post_fan_refit(
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    """記録から作り直す: 直近 2 日の記録で熱モデルと PID の効き具合を作り直す（数秒、GPU は使わない）。"""
    try:
        result = fan_control.refit_from_log()
    except (fan_control.FanControlError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "system.fan_control.refit", user=user, request=request,
                 resource_type="amd-gpu", resource_id="active",
                 metadata={"pid_kp": result["pid_kp"], "pid_ti": result["pid_ti"],
                           "points": result["changes"]["points"]})
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
        metadata={"enabled": body.enabled, "limits_c": body.limits_c,
                  "margin_c": body.margin_c, "min_percent": body.min_percent,
                  "smoothing_s": body.smoothing_s, "startup_mode": body.startup_mode},
    )
    return result
