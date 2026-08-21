from __future__ import annotations

from fastapi import HTTPException, Request

from app.addon_runtime.auth import RuntimePrincipal
from app.audit import service as audit
from app.database import SessionLocal
from app.jobs import service as jobs


def host_job(principal: RuntimePrincipal, job_id: str, *, active_only: bool = True) -> jobs.Job:
    job = jobs.get(job_id)
    if job is None or not jobs.addon_owns(job, principal.addon_id):
        raise HTTPException(status_code=404, detail="Add-on Host Jobが見つかりません")
    if principal.subject.startswith("job:"):
        if principal.subject.removeprefix("job:") != job_id:
            raise HTTPException(status_code=403, detail="service tokenのJob scopeが一致しません")
    else:
        try:
            owner_user_id = int(principal.subject)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="service token subjectがJob操作に使えません") from exc
        if job.owner_user_id != owner_user_id or not job.kind.startswith(f"addon.runtime.{principal.addon_id}"):
            raise HTTPException(status_code=403, detail="service tokenのuser scopeが一致しません")
    if active_only and job.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Add-on Host Jobはすでに終了しています")
    return job


def audit_runtime(
    request: Request,
    principal: RuntimePrincipal,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict | None = None,
) -> None:
    db = SessionLocal()
    try:
        audit.record(
            db,
            action,
            username=f"addon:{principal.addon_id}",
            resource_type=resource_type,
            resource_id=resource_id,
            request=request,
            metadata=metadata,
        )
    finally:
        db.close()
