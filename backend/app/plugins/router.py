from fastapi import APIRouter, Depends, HTTPException, Request

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.plugins import registry
from app.plugins.schema import PluginManifest
from app.security.deps import require_permission

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("")
def plugins(user: User = Depends(require_permission("settings.manage"))):
    try:
        return registry.list_plugins()
    except registry.PluginError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("", status_code=201)
def install_plugin(
    body: PluginManifest, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    try:
        result = registry.install(body)
    except registry.PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "plugin.install", user=user, resource_type="plugin", resource_id=body.id, request=request,
                 metadata={"version": body.version, "capabilities": body.capabilities})
    return result


@router.post("/{plugin_id}/{action}")
def apply_plugin(
    plugin_id: str, action: str, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    if action not in {"enable", "disable", "uninstall"}:
        raise HTTPException(status_code=404, detail="未対応の操作です")
    try:
        result = registry.uninstall(plugin_id) if action == "uninstall" else registry.set_enabled(plugin_id, action == "enable")
    except registry.PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, f"plugin.{action}", user=user, resource_type="plugin", resource_id=plugin_id, request=request)
    return result
