from __future__ import annotations

import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app.addons import bridge, execution as addon_execution, health, registry
from app.addons.schema import AddonManifestV2, parse_manifest
from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.resources.broker import broker as resource_broker
from app.security.deps import get_current_user, require_permission, user_permissions

router = APIRouter(prefix="/addons", tags=["addons"])
DISABLE_GRACE_SECONDS = 2.0


async def _wait_for_disable_grace() -> None:
    await asyncio.sleep(DISABLE_GRACE_SECONDS)


class EnableAddonRequest(BaseModel):
    granted_capabilities: list[str] | None = Field(default=None, max_length=32)


def _bridge_error(exc: bridge.BridgeAccessError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail())


def _registry_error(exc: registry.AddonRegistryError, status_code: int = 404) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("/effective")
def effective_addons(
    user: User = Depends(get_current_user),
    if_none_match: str | None = Header(default=None),
):
    payload = registry.effective_for_permissions(user_permissions(user))
    etag = payload["etag"]
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "private, no-cache"})


@router.get("/execution-contributions")
async def execution_contributions(user: User = Depends(get_current_user)):
    """Return only enabled, available and user-authorized executable contributions."""
    permissions = user_permissions(user)
    groups = {
        kind: addon_execution.discover(kind, permissions)
        for kind in ("workflow_executors", "agent_tools", "context_actions")
    }
    schema_errors: dict[str, str] = {}
    for contribution in groups["workflow_executors"]:
        key = f"{contribution['addon_id']}:{contribution['id']}"
        try:
            input_schema, output_schema = await addon_execution.workflow_schemas(
                contribution["addon_id"], contribution["id"], permissions=permissions,
            )
            contribution["input_schema"] = input_schema
            contribution["output_schema"] = output_schema
        except addon_execution.AddonExecutionError as exc:
            schema_errors[key] = exc.code
    groups["workflow_executors"] = [
        item for item in groups["workflow_executors"]
        if f"{item['addon_id']}:{item['id']}" not in schema_errors
    ]
    return {"revision": registry.revision(), "contributions": groups, "schema_errors": schema_errors}


async def _effective_event_stream(request: Request, permissions: set[str]):
    previous = -1
    while True:
        payload = registry.effective_for_permissions(permissions)
        current = payload["revision"]
        if current != previous:
            yield f"event: addons.effective.changed\ndata: {json.dumps({'revision': current, 'etag': payload['etag']})}\n\n"
            previous = current
        if await request.is_disconnected():
            return
        await asyncio.to_thread(registry.wait_for_revision, previous, 25.0)


@router.get("/effective/events")
def effective_addon_events(request: Request, user: User = Depends(get_current_user)):
    return StreamingResponse(
        _effective_event_stream(request, user_permissions(user)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("")
def list_addons(user: User = Depends(require_permission("settings.manage"))):
    try:
        return registry.list_addons()
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc, 503) from exc


@router.post("", status_code=201)
def install_addon(
    body: dict,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    if len(json.dumps(body, ensure_ascii=False).encode()) > 64 * 1024:
        raise HTTPException(status_code=413, detail="manifestは64KiB以下にしてください")
    try:
        parsed = parse_manifest(body)
        if not isinstance(parsed.manifest, AddonManifestV2):
            raise ValueError("/addonsにはapi_version 2 manifestが必要です")
        result = registry.install(parsed)
    except (ValueError, ValidationError, registry.AddonRegistryError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        db, "addon.install", user=user, resource_type="addon", resource_id=parsed.manifest.id, request=request,
        metadata={
            "version": parsed.manifest.version,
            "contribution_counts": {
                name: len(getattr(parsed.manifest.contributions, name))
                for name in type(parsed.manifest.contributions).model_fields
            },
            "requested_capability_count": len(parsed.manifest.host_capabilities),
            "warning_count": len(parsed.warnings),
        },
    )
    return result


@router.get("/{addon_id}")
def addon_detail(addon_id: str, user: User = Depends(require_permission("settings.manage"))):
    try:
        return registry.status(addon_id)
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc) from exc


@router.get("/{addon_id}/activity")
def addon_activity(addon_id: str, user: User = Depends(require_permission("settings.manage"))):
    try:
        return registry.activity(addon_id)
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc) from exc


@router.post("/{addon_id}/bridge/handshake")
def bridge_handshake(
    addon_id: str,
    body: bridge.BridgeHandshake,
    request: Request,
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    try:
        result = bridge.handshake(addon_id, body, user)
    except bridge.BridgeAccessError as exc:
        audit.record(db, "addon.bridge", user=user, resource_type="addon", resource_id=addon_id, result="failure", request=request, metadata={"method": "host.handshake"})
        raise _bridge_error(exc) from exc
    registry.record_activity(addon_id, "host.handshake", "success")
    audit.record(db, "addon.bridge", user=user, resource_type="addon", resource_id=addon_id, request=request, metadata={"method": "host.handshake"})
    return result


@router.post("/{addon_id}/bridge/call")
def bridge_call(
    addon_id: str,
    body: bridge.BridgeCall,
    request: Request,
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    started = time.monotonic()
    try:
        result = bridge.authorize(addon_id, body, user)
    except bridge.BridgeAccessError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        with contextlib.suppress(registry.AddonRegistryError):
            registry.record_activity(addon_id, body.method, exc.code, {"duration_ms": duration_ms, "field_count": len(body.params)})
        audit.record(db, "addon.bridge", user=user, resource_type="addon", resource_id=addon_id, result="failure", request=request, metadata={"method": body.method, "result_code": exc.code, "field_count": len(body.params)})
        raise _bridge_error(exc) from exc
    duration_ms = int((time.monotonic() - started) * 1000)
    registry.record_activity(addon_id, body.method, "success", {"duration_ms": duration_ms, "field_count": len(body.params)})
    audit.record(db, "addon.bridge", user=user, resource_type="addon", resource_id=addon_id, request=request, metadata={"method": body.method, "field_count": len(body.params)})
    return result


@router.post("/{addon_id}/enable")
async def enable_addon(
    addon_id: str,
    request: Request,
    body: EnableAddonRequest = Body(default_factory=EnableAddonRequest),
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        registry.set_enabled(addon_id, True, body.granted_capabilities)
        result = await health.recheck(addon_id)
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc, 422) from exc
    audit.record(
        db, "addon.enable", user=user, resource_type="addon", resource_id=addon_id, request=request,
        metadata={"granted_capability_count": len(result["granted_capabilities"]), "health_state": result["state"]},
    )
    return result


@router.post("/{addon_id}/disable")
async def disable_addon(
    addon_id: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        pending = registry.begin_disable(addon_id)
        if pending["enabled"]:
            await _wait_for_disable_grace()
        result = registry.complete_disable(addon_id)
        canceled_resources = await resource_broker.cancel_owner(f"addon:{addon_id}")
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc) from exc
    audit.record(
        db,
        "addon.disable",
        user=user,
        resource_type="addon",
        resource_id=addon_id,
        request=request,
        metadata={"canceled_resource_requests": canceled_resources["requests"], "canceled_resource_leases": canceled_resources["leases"]},
    )
    return result


@router.post("/{addon_id}/recheck")
async def recheck_addon(
    addon_id: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = await health.recheck(addon_id)
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc, 409) from exc
    audit.record(
        db, "addon.recheck", user=user, resource_type="addon", resource_id=addon_id, request=request,
        metadata={"health_state": result["state"]},
    )
    return result


@router.delete("/{addon_id}")
async def uninstall_addon(
    addon_id: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = registry.uninstall(addon_id)
        canceled_resources = await resource_broker.cancel_owner(f"addon:{addon_id}")
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc) from exc
    audit.record(
        db,
        "addon.uninstall",
        user=user,
        resource_type="addon",
        resource_id=addon_id,
        request=request,
        metadata={"canceled_resource_requests": canceled_resources["requests"], "canceled_resource_leases": canceled_resources["leases"]},
    )
    return result
