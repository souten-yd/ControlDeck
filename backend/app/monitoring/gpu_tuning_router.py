"""GPU のコア周波数・電圧オフセットの API（値だけを受け取り、経路やコマンドは受け取らない）。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.monitoring import gpu_tuning
from app.security.deps import require_permission

router = APIRouter(prefix="/system/gpu-tuning", tags=["gpu-tuning"])
STATUS_BY_CODE = {"permission": 403, "unsupported": 409, "nothing": 409, "read": 503, "range": 422}


class ApplyBody(BaseModel):
    core_offset_mhz: int | None = Field(default=None, ge=-5000, le=5000)
    voltage_offset_mv: int | None = Field(default=None, ge=-1000, le=1000)


def _http(exc: gpu_tuning.GpuTuningError) -> HTTPException:
    return HTTPException(status_code=STATUS_BY_CODE.get(exc.code, 502), detail=exc.message)


@router.get("")
async def get_status(user: User = Depends(require_permission("system.view"))):
    del user
    return await asyncio.to_thread(gpu_tuning.status)


@router.post("/apply")
async def post_apply(body: ApplyBody, request: Request, user: User = Depends(require_permission("settings.manage")),
                     db=Depends(get_db)):
    metadata = {"core_offset_mhz": body.core_offset_mhz, "voltage_offset_mv": body.voltage_offset_mv}
    try:
        result = await asyncio.to_thread(gpu_tuning.apply, body.core_offset_mhz, body.voltage_offset_mv)
    except gpu_tuning.GpuTuningError as exc:
        audit.record(db, "gpu.tuning.apply", user=user, resource_type="gpu", request=request, result="failure",
                     metadata={**metadata, "code": exc.code})
        raise _http(exc) from exc
    audit.record(db, "gpu.tuning.apply", user=user, resource_type="gpu", resource_id=result.get("bdf", ""), request=request,
                 metadata=metadata)
    return result


@router.post("/restore")
async def post_restore(request: Request, user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    try:
        result = await asyncio.to_thread(gpu_tuning.restore)
    except gpu_tuning.GpuTuningError as exc:
        audit.record(db, "gpu.tuning.restore", user=user, resource_type="gpu", request=request, result="failure",
                     metadata={"code": exc.code})
        raise _http(exc) from exc
    audit.record(db, "gpu.tuning.restore", user=user, resource_type="gpu", resource_id=result.get("bdf", ""), request=request)
    return result
