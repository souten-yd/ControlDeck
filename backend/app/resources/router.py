from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.resources.broker import BrokerError, broker as resource_broker
from app.resources.leases import LeaseError
from app.resources.schema import ResourceRequest
from app.security.deps import require_permission

router = APIRouter(prefix="/resources", tags=["resources"])


def _not_found(exc: BrokerError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _lease_conflict(exc: LeaseError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("")
async def resource_snapshot(user: User = Depends(require_permission("system.view"))):
    return await resource_broker.snapshot()


@router.get("/requests/{request_id}")
async def resource_request(request_id: str, user: User = Depends(require_permission("system.view"))):
    try:
        return await resource_broker.request_status(request_id)
    except BrokerError as exc:
        raise _not_found(exc) from exc


@router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_resource_request(
    body: ResourceRequest,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    result = await resource_broker.submit(body)
    audit.record(
        db,
        "resource.request",
        user=user,
        resource_type="resource_request",
        resource_id=result.request_id,
        request=request,
        metadata={
            "state": result.state.value,
            "reason": result.reason.value if result.reason else None,
            "device_id": result.device_id,
            "required_bytes": body.vram.required_bytes,
        },
    )
    return result


@router.delete("/requests/{request_id}")
async def cancel_resource_request(
    request_id: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = await resource_broker.cancel_request(request_id)
    except BrokerError as exc:
        raise _not_found(exc) from exc
    audit.record(db, "resource.request.cancel", user=user, resource_type="resource_request", resource_id=request_id, request=request)
    return result


async def _lease_action(lease_id: str, action: str):
    try:
        if action == "activate":
            return await resource_broker.activate(lease_id)
        if action == "renew":
            return await resource_broker.renew(lease_id)
        return await resource_broker.release(lease_id)
    except LeaseError as exc:
        raise _lease_conflict(exc) from exc


@router.post("/leases/{lease_id}/{action}")
async def resource_lease_action(
    lease_id: str,
    action: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    if action not in {"activate", "renew", "release"}:
        raise HTTPException(status_code=404, detail="未対応のlease操作です")
    result = await _lease_action(lease_id, action)
    audit.record(
        db,
        f"resource.lease.{action}",
        user=user,
        resource_type="resource_lease",
        resource_id=lease_id,
        request=request,
        metadata={"device_id": result.device_id, "state": result.state.value},
    )
    return result

