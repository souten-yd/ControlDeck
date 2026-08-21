from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from app.addon_runtime.auth import authorize_runtime
from app.audit import service as audit
from app.database import SessionLocal
from app.addon_runtime.jobs import router as jobs_router
from app.addon_runtime.resources import router as resources_router
from app.addon_runtime.files import router as files_router

router = APIRouter(prefix="/addon-runtime", tags=["addon-runtime"])
router.include_router(jobs_router)
router.include_router(resources_router)
router.include_router(files_router)


@router.post("/token/introspect")
def introspect_service_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    addon_id_header: Annotated[str | None, Header(alias="X-Control-Deck-Addon-ID")] = None,
):
    result: dict = {"active": False}
    audit_result = "failure"
    reason = "invalid_token"
    try:
        principal = authorize_runtime(
            request,
            authorization=authorization,
            header_addon_id=addon_id_header,
        )
        result = {
            "active": True,
            "addon_id": principal.addon_id,
            "subject": principal.subject,
            "expires_at": principal.expires_at,
            "granted_capabilities": sorted(principal.granted_capabilities),
        }
        audit_result = "success"
        reason = "active"
    except HTTPException as exc:  # introspection intentionally collapses all auth failures
        reason = {
            400: "missing_addon_id",
            403: "scope_mismatch",
            409: "addon_inactive",
        }.get(exc.status_code, "invalid_token")

    db = SessionLocal()
    try:
        audit.record(
            db,
            "addon.runtime.token.introspect",
            username=f"addon:{addon_id_header}" if addon_id_header else "addon:unknown",
            resource_type="addon",
            resource_id=addon_id_header or "",
            result=audit_result,
            request=request,
            metadata={"result_code": reason},
        )
    finally:
        db.close()
    return result
