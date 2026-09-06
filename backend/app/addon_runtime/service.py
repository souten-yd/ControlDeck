from __future__ import annotations

from fastapi import HTTPException, Request

from app.addon_runtime.auth import RuntimePrincipal
from app.audit import service as audit
from app.database import SessionLocal
from app.jobs import service as jobs


class RuntimeAuthorityError(ValueError):
    pass


def principal_user_id(principal: RuntimePrincipal) -> int:
    if principal.actor_user_id is not None:
        return principal.actor_user_id
    try:
        return int(principal.subject)
    except ValueError as exc:
        raise RuntimeAuthorityError("service token subjectのactorを解決できません") from exc


def host_job(principal: RuntimePrincipal, job_id: str, *, active_only: bool = True) -> jobs.Job:
    job = jobs.get(job_id)
    if job is None or not jobs.addon_owns(job, principal.addon_id):
        raise HTTPException(status_code=404, detail="Add-on Host Jobが見つかりません")
    if principal.subject.startswith("job:"):
        if principal.subject.removeprefix("job:") != job_id:
            raise HTTPException(status_code=403, detail="service tokenのJob scopeが一致しません")
    else:
        try:
            owner_user_id = principal_user_id(principal)
        except RuntimeAuthorityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if job.owner_user_id != owner_user_id or not job.kind.startswith(f"addon.runtime.{principal.addon_id}"):
            raise HTTPException(status_code=403, detail="service tokenのuser scopeが一致しません")
    if active_only and job.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Add-on Host Jobはすでに終了しています")
    return job


async def host_job_control(principal: RuntimePrincipal, job_id: str) -> dict:
    """Read terminal history without restoring execution or mutation authority."""
    if jobs.get(job_id) is not None:
        return host_job(principal, job_id, active_only=False).to_dict()
    job = await jobs.get_any(job_id)
    runtime_kind = f"addon.runtime.{principal.addon_id}"
    agent_prefix = f"addon.agent_tool.{principal.addon_id}."
    if job is None or not (
        job["kind"] == runtime_kind or job["kind"].startswith(agent_prefix)
    ):
        raise HTTPException(status_code=404, detail="Add-on Host Jobが見つかりません")
    if principal.subject.startswith("job:"):
        if principal.subject.removeprefix("job:") != job_id:
            raise HTTPException(status_code=403, detail="service tokenのJob scopeが一致しません")
    else:
        try:
            owner_user_id = principal_user_id(principal)
        except RuntimeAuthorityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if job["owner_user_id"] != owner_user_id or job["kind"] != runtime_kind:
            raise HTTPException(status_code=403, detail="service tokenのuser scopeが一致しません")
    if job["status"] not in {"succeeded", "failed", "canceled", "interrupted"}:
        # A persisted running row does not prove a live executor after restart.
        raise HTTPException(status_code=409, detail="Add-on Host Jobの実行状態を確認できません")
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
