"""Project Lab API。成果物閲覧とbrowser非依存のdurable runを提供する。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import ProjectRun, User
from app.project_lab import runs, service
from app.schemas.project_lab import ProjectRunCreate
from app.security.deps import require_permission

router = APIRouter(prefix="/project-lab", tags=["project-lab"])


def _not_found(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("/projects")
def projects(user: User = Depends(require_permission("project_lab.view"))):
    return service.list_projects()


@router.get("/projects/{project_id}")
def project(project_id: str, user: User = Depends(require_permission("project_lab.view"))):
    try:
        return service.project_detail(project_id)
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc


@router.get("/projects/{project_id}/artifacts/{artifact_path:path}")
def artifact(
    project_id: str, artifact_path: str, download: bool = Query(False),
    user: User = Depends(require_permission("project_lab.view")),
):
    try:
        project_path = service.resolve_project(project_id)
        path = service.resolve_artifact(project_path, artifact_path)
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    kind = service.ARTIFACT_KINDS.get(path.suffix.lower(), "resource")
    headers = {"Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; media-src 'self'; form-action 'none'; base-uri 'none'"}
    if kind == "svg":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path, media_type=(service.artifact_info(project_path, path) or {}).get("mimeType"),
        filename=path.name if download else None,
        content_disposition_type=disposition, headers=headers,
    )


@router.get("/projects/{project_id}/previews/{artifact_path:path}")
def artifact_preview(
    project_id: str, artifact_path: str,
    user: User = Depends(require_permission("project_lab.view")),
):
    try:
        project_path = service.resolve_project(project_id)
        path = service.resolve_artifact(project_path, artifact_path)
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    metadata = service.artifact_info(project_path, path, include_preview=True)
    if metadata is None:
        raise HTTPException(status_code=404, detail="artifact previewを生成できません")
    return {"path": metadata["path"], "previewText": metadata["previewText"], "structuredPreview": metadata["structuredPreview"]}


def _run_or_404(db: Session, run_id: int) -> ProjectRun:
    row = db.get(ProjectRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Project runが見つかりません")
    return row


@router.post("/projects/{project_id}/runs", status_code=201)
def start_project_run(
    project_id: str, body: ProjectRunCreate, request: Request,
    user: User = Depends(require_permission("project_lab.run")), db: Session = Depends(get_db),
):
    try:
        row = runs.start_run(
            db, project_id=project_id, profile_id=body.profile_id,
            timeout_seconds=body.timeout_seconds, created_by=user.id,
        )
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    except runs.ProjectRunError as exc:
        audit.record(
            db, "project_lab.run.start", user=user, resource_type="project",
            resource_id=project_id, request=request, result="failure",
            metadata={"profile_id": body.profile_id},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "project_lab.run.start", user=user, resource_type="project_run",
        resource_id=str(row.id), request=request,
        metadata={"project_id": project_id, "profile_id": body.profile_id},
    )
    return runs.run_out(db, row)


@router.get("/runs")
def project_runs(
    project_id: str | None = Query(None, max_length=128), limit: int = Query(30, ge=1, le=100),
    user: User = Depends(require_permission("project_lab.view")), db: Session = Depends(get_db),
):
    query = select(ProjectRun)
    if project_id:
        query = query.where(ProjectRun.project_id == project_id)
    rows = db.execute(query.order_by(ProjectRun.id.desc()).limit(limit)).scalars().all()
    return [runs.run_out(db, row) for row in rows]


@router.get("/runs/{run_id}")
def project_run(
    run_id: int, user: User = Depends(require_permission("project_lab.view")),
    db: Session = Depends(get_db),
):
    return runs.run_out(db, _run_or_404(db, run_id))


@router.get("/runs/{run_id}/logs")
def project_run_logs(
    run_id: int, user: User = Depends(require_permission("project_lab.view")),
    db: Session = Depends(get_db),
):
    row = _run_or_404(db, run_id)
    runs.refresh_run(db, row)
    return {"runId": row.id, "logs": runs.run_logs(row)}


@router.post("/runs/{run_id}/cancel")
def cancel_project_run(
    run_id: int, request: Request, user: User = Depends(require_permission("project_lab.run")),
    db: Session = Depends(get_db),
):
    row = _run_or_404(db, run_id)
    try:
        runs.cancel_run(db, row)
    except runs.ProjectRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "project_lab.run.cancel", user=user, resource_type="project_run",
        resource_id=str(row.id), request=request,
    )
    return runs.run_out(db, row)
