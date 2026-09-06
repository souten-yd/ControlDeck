from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addon_runtime.schema import RuntimeJobCreate, RuntimeJobUpdate
from app.addon_runtime.service import (
    RuntimeAuthorityError,
    audit_runtime,
    host_job,
    host_job_control,
    principal_user_id,
)
from app.addons import tokens
from app.database import SessionLocal
from app.jobs import service as jobs
from app.models import User

router = APIRouter(prefix="/{addon_id}/jobs")
JobAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability("jobs.write"))]
JobControlAuth = Annotated[
    RuntimePrincipal, Depends(require_runtime_capability("jobs.write", allow_inactive=True)),
]
MAX_RESULT_BYTES = 16 * 1024


def _fresh_job_credential(
    principal: RuntimePrincipal, *, subject: str | None = None
) -> dict[str, str | int]:
    token = tokens.issue(
        principal.addon_id,
        subject=subject or principal.subject,
        kind="service",
        actor_user_id=principal.actor_user_id,
        grant_ids=sorted(principal.grant_ids) if principal.grant_ids is not None else None,
    )
    payload = tokens.verify(token, addon_id=principal.addon_id, kind="service")
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_at": payload["exp"],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_or_attach_job(
    body: RuntimeJobCreate,
    request: Request,
    principal: JobAuth,
):
    if principal.subject.startswith("job:") and not body.detached:
        job = host_job(principal, principal.subject.removeprefix("job:"))
        created = False
    else:
        if principal.subject.startswith("job:"):
            parent = host_job(principal, principal.subject.removeprefix("job:"))
            owner_user_id = parent.owner_user_id
            if owner_user_id is None:
                raise HTTPException(status_code=403, detail="Add-on Host Jobのownerを解決できません")
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
    audit_runtime(
        request,
        principal,
        "addon.runtime.job.attach" if not created else "addon.runtime.job.create",
        "job",
        job.id,
    )
    result: dict[str, object] = {"created": created, "job": job.to_dict()}
    if created:
        result.update(_fresh_job_credential(principal, subject=f"job:{job.id}"))
    return result


@router.post("/{host_job_id}/credential/refresh")
async def refresh_job_credential(
    host_job_id: str,
    request: Request,
    principal: JobAuth,
):
    """Roll the short-lived service credential while an owned Host Job is active.

    This is the generic heartbeat for long CPU-only setup, meeting and other
    durable executions that may not own a Resource Broker lease or AI residency
    hold. The caller must still present a currently valid service credential;
    heartbeat does not turn an expired bearer token into a refresh token.
    """
    job = host_job(principal, host_job_id)
    result = _fresh_job_credential(principal)
    audit_runtime(
        request,
        principal,
        "addon.runtime.job.credential.refresh",
        "job",
        job.id,
        {"status": job.status},
    )
    return result


@router.patch("/{host_job_id}")
async def update_job(
    host_job_id: str,
    body: RuntimeJobUpdate,
    request: Request,
    principal: JobAuth,
):
    job = host_job(principal, host_job_id)
    if body.result is not None and len(
        json.dumps(body.result, ensure_ascii=False, default=str).encode()
    ) > MAX_RESULT_BYTES:
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
    audit_runtime(
        request,
        principal,
        "addon.runtime.job.update",
        "job",
        job.id,
        {
            "phase": body.phase,
            "terminal_status": body.status,
            "has_progress": body.progress is not None,
        },
    )
    return job.to_dict()


@router.get("/{host_job_id}/control")
async def job_control(host_job_id: str, principal: JobControlAuth):
    job = await host_job_control(principal, host_job_id)
    return {
        "host_job_id": job["id"],
        "cancel_requested": job["status"] == "canceled",
        "status": job["status"],
        "revision": job["revision"],
    }
