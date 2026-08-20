from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.addons import health, registry
from app.addons.schema import AddonManifestV2, parse_manifest
from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import get_current_user, require_permission, user_permissions

router = APIRouter(prefix="/addons", tags=["addons"])


class EnableAddonRequest(BaseModel):
    granted_capabilities: list[str] | None = Field(default=None, max_length=32)


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
def disable_addon(
    addon_id: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = registry.set_enabled(addon_id, False)
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc) from exc
    audit.record(db, "addon.disable", user=user, resource_type="addon", resource_id=addon_id, request=request)
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
def uninstall_addon(
    addon_id: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = registry.uninstall(addon_id)
    except registry.AddonRegistryError as exc:
        raise _registry_error(exc) from exc
    audit.record(db, "addon.uninstall", user=user, resource_type="addon", resource_id=addon_id, request=request)
    return result
