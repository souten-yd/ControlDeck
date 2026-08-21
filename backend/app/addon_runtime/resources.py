from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addon_runtime.schema import RuntimeResourceRequest
from app.addon_runtime.service import audit_runtime, host_job
from app.addons import tokens
from app.resources.broker import BrokerError, broker
from app.resources.leases import LeaseError
from app.resources.schema import LeaseState, ResourceRequest

router = APIRouter(prefix="/{addon_id}/resources")
ActiveResourceAuth = Annotated[
    RuntimePrincipal, Depends(require_runtime_capability("resources.acquire")),
]
CleanupResourceAuth = Annotated[
    RuntimePrincipal, Depends(require_runtime_capability("resources.acquire", allow_inactive=True)),
]


def _resource_request(body: RuntimeResourceRequest, principal: RuntimePrincipal) -> ResourceRequest:
    return ResourceRequest.model_validate({
        **body.model_dump(mode="json", by_alias=True),
        "owner": f"addon:{principal.addon_id}",
    })


async def _owned_request(principal: RuntimePrincipal, request_id: str):
    try:
        value = await broker.request_status(request_id)
    except BrokerError as exc:
        raise HTTPException(status_code=404, detail="Resource requestが見つかりません") from exc
    if value.owner != f"addon:{principal.addon_id}":
        raise HTTPException(status_code=404, detail="Resource requestが見つかりません")
    host_job(principal, value.job_id, active_only=False)
    return value


async def _owned_lease(principal: RuntimePrincipal, lease_id: str):
    try:
        value = await broker.lease_status(lease_id)
    except BrokerError as exc:
        raise HTTPException(status_code=404, detail="Resource leaseが見つかりません") from exc
    if value.owner != f"addon:{principal.addon_id}":
        raise HTTPException(status_code=404, detail="Resource leaseが見つかりません")
    host_job(principal, value.job_id, active_only=False)
    return value


@router.post("/leases/{lease_id}/credential/refresh")
async def refresh_lease_credential(
    lease_id: str,
    request: Request,
    principal: ActiveResourceAuth,
) -> dict[str, str | int]:
    lease = await _owned_lease(principal, lease_id)
    if lease.state not in {LeaseState.GRANTED, LeaseState.ACTIVE}:
        raise HTTPException(status_code=409, detail="終了したleaseのcredentialは更新できません")
    host_job(principal, lease.job_id)
    token = tokens.issue(
        principal.addon_id,
        subject=principal.subject,
        kind="service",
        actor_user_id=principal.actor_user_id,
        grant_ids=sorted(principal.grant_ids) if principal.grant_ids is not None else None,
    )
    payload = tokens.verify(token, addon_id=principal.addon_id, kind="service")
    audit_runtime(
        request,
        principal,
        "addon.runtime.resource.credential.refresh",
        "resource_lease",
        lease_id,
        {"job_id": lease.job_id},
    )
    return {"access_token": token, "token_type": "Bearer", "expires_at": payload["exp"]}


@router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_request(
    body: RuntimeResourceRequest,
    request: Request,
    principal: ActiveResourceAuth,
):
    host_job(principal, body.job_id)
    result = await broker.submit(_resource_request(body, principal))
    audit_runtime(request, principal, "addon.runtime.resource.request", "resource_request", result.request_id, {
        "job_id": body.job_id,
        "state": result.state.value,
        "priority": body.priority,
        "class": body.workload_class.value,
    })
    return result


@router.get("/requests/{request_id}")
async def get_request(request_id: str, principal: CleanupResourceAuth):
    return await _owned_request(principal, request_id)


@router.delete("/requests/{request_id}")
async def cancel_request(request_id: str, request: Request, principal: CleanupResourceAuth):
    await _owned_request(principal, request_id)
    result = await broker.cancel_request(request_id)
    audit_runtime(request, principal, "addon.runtime.resource.request.cancel", "resource_request", request_id)
    return result


@router.post("/leases/{lease_id}/{action}")
async def lease_action(
    lease_id: str,
    action: str,
    request: Request,
    principal: CleanupResourceAuth,
):
    if action not in {"activate", "renew", "release"}:
        raise HTTPException(status_code=404, detail="未対応のlease操作です")
    if not principal.active and action != "release":
        raise HTTPException(status_code=409, detail="無効化中または無効なAdd-onはleaseを延長できません")
    await _owned_lease(principal, lease_id)
    try:
        result = (
            await broker.activate(lease_id) if action == "activate"
            else await broker.renew(lease_id) if action == "renew"
            else await broker.release(lease_id)
        )
    except LeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_runtime(request, principal, f"addon.runtime.resource.lease.{action}", "resource_lease", lease_id, {
        "job_id": result.job_id,
        "device_id": result.device_id,
        "state": result.state.value,
    })
    return result
