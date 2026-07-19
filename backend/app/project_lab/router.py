"""Project Lab read-only API。実行APIはdurable run Phaseまで追加しない。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.models import User
from app.project_lab import service
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
