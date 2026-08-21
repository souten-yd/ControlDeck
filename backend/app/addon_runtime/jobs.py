from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addon_runtime.schema import RuntimeJobCreate, RuntimeJobUpdate
from app.addon_runtime.service import RuntimeAuthorityError, audit_runtime, host_job, principal_user_id
from app.database import SessionLocal
from app.jobs import service as jobs
from app.models import User

router = APIRouter(prefix="/{addon_id}/jobs")
JobAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability("jobs.write"))]
JobControlAuth = Annotated[
    RuntimePrincipal, Depends(require_runtime_capability("jobs.write", allow_inactive=True)),
]
MAX_RESULT_BYTES = 16 * 1024


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_or_attach_job(
    body: RuntimeJobCreate,
    request: Request,
    principal: JobAuth,
):
    if principal.subject.startswith("job:"):
        job = host_job(principal, principal.subject.removeprefix("job:"))
        created = False
    else:
        try:
            owner_user_id = principal_user_id(principal)
        except RuntimeAuthorityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        with SessionLocal() as db:
            user = db.get(User, owner_user_id)
            if user is None or not user.is_active:
                raise HTTPException(status_code=403, detail="service tokenのuserが有効ではありません")
        try:
            job = jobs.create_external(principal.addon_id, body.title, owner_user_id=owner_user_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        created = True
    audit_runtime(request, principal, "addon.runtime.job.attach" if not created else "addon.runtime.job.create", "job", job.id)
    return {"created": created, "job": job.to_dict()}


@router.patch("/{host_job_id}")
async def update_job(
    host_job_id: str,
    body: RuntimeJobUpdate,
    request: Request,
    principal: JobAuth,
):
    job = host_job(principal, host_job_id)
    if body.result is not None and len(json.dumps(body.result, ensure_ascii=False, default=str).encode()) > MAX_RESULT_BYTES:
        raise HTTPException(status_code=413, detail="terminal resultが16KiB上限を超えています")
    progress = body.progress
    try:
        jobs.update_external(
            job,
            addon_id=principal.addon_id,
            phase=body.phase,
            completed=progress.completed if progress else None,
            total=progress.total if progress else None,
            message=body.message,
            wait_reason=body.wait_reason,
            terminal_status=body.status,
            result=body.result,
            error=body.error,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        code = 429 if "2Hz" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    audit_runtime(request, principal, "addon.runtime.job.update", "job", job.id, {
        "phase": body.phase,
        "terminal_status": body.status,
        "has_progress": body.progress is not None,
    })
    return job.to_dict()


@router.get("/{host_job_id}/control")
async def job_control(host_job_id: str, principal: JobControlAuth):
    job = host_job(principal, host_job_id, active_only=False)
    return {
        "host_job_id": job.id,
        "cancel_requested": job.status == "canceled",
        "status": job.status,
        "revision": job.revision,
    }
