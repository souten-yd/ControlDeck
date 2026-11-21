"""設定 →「リモートデスクトップ」の有効・無効（settings.manage だけ。変更は監査に残す）。"""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.remote_desktop import router as remote_router
from app.remote_desktop import toggle
from app.security.deps import require_permission

router = APIRouter(prefix="/settings/remote-desktop", tags=["settings"])


class RemoteDesktopBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.get("")
def get_remote_desktop(user: User = Depends(require_permission("settings.manage"))):
    return toggle.view()


@router.put("")
def put_remote_desktop(
    body: RemoteDesktopBody, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    # def にして threadpool で動かす（subprocess・DB が event loop を止めない。AGENTS.md ルール 9）。
    result = toggle.set_enabled(body.enabled)
    if not body.enabled:
        for websocket in list(remote_router.open_tunnels):
            try:
                anyio.from_thread.run(websocket.close, 4403, toggle.DISABLED_DETAIL)
            except Exception:  # noqa: BLE001 - すでに閉じた中継は無視
                pass
    apply = result["apply"] or {}
    audit.record(
        db, "remote_desktop.enable" if body.enabled else "remote_desktop.disable",
        user=user, resource_type="settings", resource_id="remote_desktop", request=request,
        result="success" if apply.get("state") == "ok" else "failure",
        metadata={"enabled": body.enabled, "server": apply.get("state"), "message": apply.get("message")},
    )
    return result
