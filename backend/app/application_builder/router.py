from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application_builder.capabilities import capability_catalog
from app.application_builder import builds
from app.application_builder.compiler import default_spec, validate_application_spec, workflow_app_spec
from app.application_builder.design_system.components import component_catalog
from app.application_builder.patch_service import preview_patches, spec_checksum
from app.application_builder.platform_advisor import advise_platforms, preflight_application
from app.application_builder.proposal_service import ProposalGenerationError, ProposalInputError, generate_design_proposals
from app.application_builder.source_generator import (
    SourceGenerationError, bundle_metadata, generate_source_bundle, target_generator_diagnostics,
)
from app.application_builder.service import create_default_project, get_project, project_out, validate_payload, workflow_source
from app.audit import service as audit
from app.database import get_db
from app.models import ApplicationBuild, ApplicationBuildArtifact, ApplicationProject, User
from app.schemas.application_builder import (
    ApplicationDesignProposalRequest, ApplicationPatchApplyBody, ApplicationPatchPreviewBody, ApplicationProjectCreate,
    ApplicationBuildRequest, ApplicationPreflightBody, ApplicationProjectUpdate, ApplicationSourceRequest, ApplicationValidateBody, PlatformAdvisorRequest,
    WorkflowApplicationCreate,
)
from app.security.deps import require_permission
from app.workflows.contracts import build_input_schema, build_output_schema

router = APIRouter(tags=["application-builder"])


@router.get("/application-builder/schema")
def application_schema(user: User = Depends(require_permission("application_builder.view"))):
    from app.schemas.application_builder import (
        ApplicationBuildRequest, ApplicationDesignProposalRequest, ApplicationPatchOperation, ApplicationPreflightBody, ApplicationSourceRequest,
        ApplicationSpecV1, ApplicationValidateBody, PlatformAdvisorRequest,
    )

    sample = default_spec("ExampleApplication", "", None)
    return {
        "schemaVersion": 1,
        "applicationSpecSchema": ApplicationSpecV1.model_json_schema(),
        "validateRequestSchema": ApplicationValidateBody.model_json_schema(),
        "applicationSpecTemplate": sample,
        "bindingSources": [
            "workflow-input", "workflow-output", "node-output", "api", "entity", "query",
            "state", "route", "form", "system", "constant",
        ],
        "statuses": ["draft", "archived"],
        "semanticComponents": component_catalog(),
        "patchOperationSchema": ApplicationPatchOperation.model_json_schema(),
        "designProposalRequestSchema": ApplicationDesignProposalRequest.model_json_schema(),
        "platformAdvisorRequestSchema": PlatformAdvisorRequest.model_json_schema(),
        "preflightRequestSchema": ApplicationPreflightBody.model_json_schema(),
        "sourceRequestSchema": ApplicationSourceRequest.model_json_schema(),
        "buildRequestSchema": ApplicationBuildRequest.model_json_schema(),
    }


@router.get("/application-builder/capabilities")
def application_capabilities(user: User = Depends(require_permission("application_builder.view"))):
    return capability_catalog()


@router.post("/application-builder/platform-advisor")
def application_platform_advisor(
    body: PlatformAdvisorRequest,
    user: User = Depends(require_permission("application_builder.view")),
):
    """固定registryだけを使う副作用なしplatform推薦。"""
    return advise_platforms(body)


@router.post("/application-builder/preflight")
def application_preflight(
    body: ApplicationPreflightBody,
    user: User = Depends(require_permission("application_builder.view")),
    db: Session = Depends(get_db),
):
    """source生成やbuildを行わず、全targetの互換性とhost制約を検査する。"""
    first_target = next((item for item in body.spec.get("targets", []) if isinstance(item, dict)), {})
    framework = str(first_target.get("framework") or "")
    target_language = "cpp" if framework == "qt" else "csharp"
    validation = validate_payload(
        db, body.spec, workflow_id=body.workflow_id,
        workflow_version_id=body.workflow_version_id, target=target_language,
    )
    return preflight_application(body.spec, validation)


@router.post("/application-builder/validate")
def validate_application(
    body: ApplicationValidateBody,
    user: User = Depends(require_permission("application_builder.view")),
    db: Session = Depends(get_db),
):
    """executor、LLM、network、subprocess、secret解決を行わない静的検証。"""
    return validate_payload(
        db, body.spec, workflow_id=body.workflow_id,
        workflow_version_id=body.workflow_version_id, target=body.target,
    )


@router.post("/application-builder/patches/preview")
def preview_application_patches(
    body: ApplicationPatchPreviewBody,
    user: User = Depends(require_permission("application_builder.view")),
):
    """副作用なしでPatch、lock、結果Specを検証する。"""
    return preview_patches(body.spec, body.patches)


@router.get("/application-projects")
def list_projects(
    workflow_id: int | None = Query(default=None, ge=1),
    user: User = Depends(require_permission("application_builder.view")),
    db: Session = Depends(get_db),
):
    statement = select(ApplicationProject).order_by(ApplicationProject.updated_at.desc())
    if workflow_id is not None:
        statement = statement.where(ApplicationProject.workflow_id == workflow_id)
    return [project_out(row) for row in db.execute(statement).scalars().all()]


@router.post("/application-projects", status_code=201)
def create_project(
    body: ApplicationProjectCreate, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    workflow = None
    definition = None
    if body.workflow_id is not None:
        workflow, definition, _version = workflow_source(db, body.workflow_id)
    if body.spec is not None:
        spec = body.spec
    elif workflow is not None and definition is not None:
        spec = workflow_app_spec(
            body.name, body.description, workflow.id,
            input_schema=build_input_schema(definition), output_schema=build_output_schema(definition),
        )
    else:
        spec = default_spec(body.name, body.description, None)
    issues = validate_application_spec(spec)
    if any(item.severity == "error" for item in issues):
        raise HTTPException(status_code=422, detail={"diagnostics": [item.model_dump(by_alias=True) for item in issues]})
    app = spec.get("application") or {}
    target_profile = next(iter(spec.get("targets") or []), {})
    row = ApplicationProject(
        name=body.name, description=body.description, workflow_id=workflow.id if workflow else None,
        application_spec_json=json.dumps(spec, ensure_ascii=False), schema_version=1,
        target="csharp", application_type=str(app.get("applicationType") or "web"),
        ui_framework=str(target_profile.get("framework") or "aspnet-blazor"), status="draft", created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit.record(db, "application_project.create", user=user, resource_type="application_project", resource_id=str(row.id), request=request, metadata={"workflow_id": row.workflow_id})
    return project_out(row)


@router.post("/workflows/{workflow_id}/application-projects", status_code=201)
def create_from_workflow(
    workflow_id: int, body: WorkflowApplicationCreate, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    workflow, _definition, version_id = workflow_source(db, workflow_id, source=body.source)
    row = create_default_project(
        db, workflow, name=body.name, description=body.description,
        source=body.source, workflow_version_id=version_id,
    )
    row.created_by = user.id
    db.add(row)
    db.commit()
    db.refresh(row)
    audit.record(db, "application_project.create", user=user, resource_type="application_project", resource_id=str(row.id), request=request, metadata={"workflow_id": workflow_id, "source": body.source})
    return project_out(row)


@router.get("/application-projects/{project_id}")
def project_detail(
    project_id: int, user: User = Depends(require_permission("application_builder.view")),
    db: Session = Depends(get_db),
):
    return project_out(get_project(db, project_id))


@router.get("/application-projects/{project_id}/source-preview")
def preview_project_source(
    project_id: int, target_id: str = Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
    user: User = Depends(require_permission("application_builder.view")), db: Session = Depends(get_db),
):
    """保存済みsnapshotから、file writeなしで生成結果とchecksumをpreviewする。"""
    row = get_project(db, project_id)
    spec, validation = _source_input(db, row)
    target = next((item for item in spec.get("targets", []) if isinstance(item, dict) and item.get("id") == target_id), {})
    source_phase = "E7" if target.get("framework") == "aspnet-blazor" else "B2.5"
    diagnostics = [*validation["diagnostics"], *[
        item.model_dump(by_alias=True)
        for item in target_generator_diagnostics(spec, validation.get("workflowIr"), target_id=target_id)
    ]]
    if any(item.get("severity") == "error" for item in diagnostics):
        return {
            "phase": source_phase, "ready": False, "diagnostics": diagnostics,
            "sideEffects": {"executor": False, "network": False, "subprocess": False, "filesystemWrite": False, "secretResolution": False},
        }
    try:
        bundle = generate_source_bundle(spec, validation.get("workflowIr"), target_id=target_id)
    except SourceGenerationError as exc:
        return {
            "phase": source_phase, "ready": False,
            "diagnostics": [item.model_dump(by_alias=True) for item in exc.diagnostics],
            "sideEffects": {"executor": False, "network": False, "subprocess": False, "filesystemWrite": False, "secretResolution": False},
        }
    return {"ready": True, "diagnostics": diagnostics, **bundle_metadata(bundle)}


@router.post("/application-projects/{project_id}/source-archive")
def download_project_source(
    project_id: int, body: ApplicationSourceRequest, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    """決定的source ZIPをメモリ内生成し、重要操作として監査する。"""
    row = get_project(db, project_id)
    spec, validation = _source_input(db, row)
    diagnostics = [*validation["diagnostics"], *[
        item.model_dump(by_alias=True)
        for item in target_generator_diagnostics(spec, validation.get("workflowIr"), target_id=body.target_id)
    ]]
    if any(item.get("severity") == "error" for item in diagnostics):
        raise HTTPException(status_code=422, detail={"diagnostics": diagnostics})
    try:
        bundle = generate_source_bundle(spec, validation.get("workflowIr"), target_id=body.target_id)
    except SourceGenerationError as exc:
        raise HTTPException(status_code=422, detail={
            "diagnostics": [item.model_dump(by_alias=True) for item in exc.diagnostics],
        }) from exc
    audit.record(
        db, "application_project.source_generate", user=user,
        resource_type="application_project", resource_id=str(row.id), request=request,
        metadata={
            "target_id": body.target_id, "generator": bundle.manifest["generator"],
            "source_checksum": bundle.source_checksum, "archive_checksum": bundle.archive_checksum,
            "file_count": len(bundle.files), "archive_bytes": len(bundle.archive_bytes),
        },
    )
    return Response(
        content=bundle.archive_bytes, media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle.archive_name}"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "X-ControlDeck-Source-SHA256": bundle.archive_checksum,
        },
    )


@router.post("/application-projects/{project_id}/builds", status_code=202)
def start_project_build(
    project_id: int, body: ApplicationBuildRequest, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    row = get_project(db, project_id)
    spec, validation = _source_input(db, row)
    target = next((item for item in spec.get("targets", []) if isinstance(item, dict) and item.get("id") == body.target_id), None)
    if target is None:
        raise HTTPException(status_code=422, detail="Target is not present in the saved Application Spec")
    framework = str(target.get("framework") or "")
    if framework not in {"csharp-console", "aspnet-blazor"}:
        raise HTTPException(status_code=422, detail="Local build is available only for generated C# targets")
    diagnostics = [*validation["diagnostics"], *[
        item.model_dump(by_alias=True)
        for item in target_generator_diagnostics(spec, validation.get("workflowIr"), target_id=body.target_id)
    ]]
    if any(item.get("severity") == "error" for item in diagnostics):
        raise HTTPException(status_code=422, detail={"diagnostics": diagnostics})
    try:
        bundle = generate_source_bundle(spec, validation.get("workflowIr"), target_id=body.target_id)
        build = builds.start_build(
            db, project_id=row.id, target_id=body.target_id, framework=framework,
            timeout_seconds=body.timeout_seconds, bundle=bundle, created_by=user.id,
        )
    except SourceGenerationError as exc:
        audit.record(
            db, "application_build.start", user=user, resource_type="application_project",
            resource_id=str(row.id), request=request, result="failure",
            metadata={"target_id": body.target_id, "framework": framework, "reason": "source-validation"},
        )
        raise HTTPException(status_code=422, detail={
            "diagnostics": [item.model_dump(by_alias=True) for item in exc.diagnostics],
        }) from exc
    except builds.ApplicationBuildError as exc:
        audit.record(
            db, "application_build.start", user=user, resource_type="application_project",
            resource_id=str(row.id), request=request, result="failure",
            metadata={"target_id": body.target_id, "framework": framework, "reason": "build-start"},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "application_build.start", user=user, resource_type="application_build",
        resource_id=str(build.id), request=request,
        metadata={"project_id": row.id, "target_id": body.target_id, "framework": framework, "source_checksum": bundle.source_checksum},
    )
    return builds.build_out(db, build)


@router.get("/application-projects/{project_id}/builds")
def list_project_builds(
    project_id: int, limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_permission("application_builder.view")), db: Session = Depends(get_db),
):
    get_project(db, project_id)
    rows = db.execute(select(ApplicationBuild).where(
        ApplicationBuild.project_id == project_id,
    ).order_by(ApplicationBuild.id.desc()).limit(limit)).scalars().all()
    return [builds.build_out(db, row) for row in rows]


def _build_or_404(db: Session, build_id: int) -> ApplicationBuild:
    row = db.get(ApplicationBuild, build_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Application build not found")
    return row


@router.get("/application-builds/{build_id}")
def application_build_detail(
    build_id: int, user: User = Depends(require_permission("application_builder.view")), db: Session = Depends(get_db),
):
    return builds.build_out(db, _build_or_404(db, build_id))


@router.get("/application-builds/{build_id}/logs")
def application_build_logs(
    build_id: int, user: User = Depends(require_permission("application_builder.view")), db: Session = Depends(get_db),
):
    return builds.build_out(db, _build_or_404(db, build_id), include_logs=True)


@router.post("/application-builds/{build_id}/cancel")
def cancel_application_build(
    build_id: int, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    row = _build_or_404(db, build_id)
    try:
        row = builds.cancel_build(db, row)
    except builds.ApplicationBuildError as exc:
        audit.record(db, "application_build.cancel", user=user, resource_type="application_build", resource_id=str(row.id), request=request, result="failure", metadata={"project_id": row.project_id})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "application_build.cancel", user=user, resource_type="application_build", resource_id=str(row.id), request=request, metadata={"project_id": row.project_id})
    return builds.build_out(db, row)


@router.get("/application-builds/{build_id}/artifacts/{artifact_id}")
def download_application_build_artifact(
    build_id: int, artifact_id: int, request: Request,
    user: User = Depends(require_permission("application_builder.view")), db: Session = Depends(get_db),
):
    row = _build_or_404(db, build_id)
    artifact = db.get(ApplicationBuildArtifact, artifact_id)
    if artifact is None or artifact.build_id != row.id:
        raise HTTPException(status_code=404, detail="Application build artifact not found")
    try:
        path = builds.artifact_path(row, artifact)
    except builds.ApplicationBuildError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "application_build.artifact_download", user=user, resource_type="application_build", resource_id=str(row.id), request=request, metadata={"artifact_id": artifact.id, "kind": artifact.kind, "checksum": artifact.checksum})
    return FileResponse(path, media_type=artifact.mime_type, filename=path.name, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/application-builds/{build_id}", status_code=204)
def delete_application_build(
    build_id: int, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    row = _build_or_404(db, build_id)
    project_id = row.project_id
    try:
        builds.delete_build(db, row)
    except builds.ApplicationBuildError as exc:
        audit.record(db, "application_build.delete", user=user, resource_type="application_build", resource_id=str(build_id), request=request, result="failure", metadata={"project_id": project_id})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "application_build.delete", user=user, resource_type="application_build", resource_id=str(build_id), request=request, metadata={"project_id": project_id})


@router.patch("/application-projects/{project_id}")
def update_project(
    project_id: int, body: ApplicationProjectUpdate, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    row = get_project(db, project_id)
    if body.spec is not None:
        issues = validate_application_spec(body.spec)
        if any(item.severity == "error" for item in issues):
            raise HTTPException(status_code=422, detail={"diagnostics": [item.model_dump(by_alias=True) for item in issues]})
        row.application_spec_json = json.dumps(body.spec, ensure_ascii=False)
        row.schema_version = int(body.spec.get("schemaVersion") or 1)
        app = body.spec.get("application") or {}
        row.application_type = str(app.get("applicationType") or row.application_type)
        target_profile = next(iter(body.spec.get("targets") or []), {})
        row.ui_framework = str(target_profile.get("framework") or row.ui_framework)
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    db.commit()
    db.refresh(row)
    audit.record(db, "application_project.update", user=user, resource_type="application_project", resource_id=str(row.id), request=request, metadata={})
    return project_out(row)


@router.post("/application-projects/{project_id}/patches/apply")
def apply_project_patches(
    project_id: int, body: ApplicationPatchApplyBody, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    row = get_project(db, project_id)
    try:
        current_spec = json.loads(row.application_spec_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail="保存済みApplication Specが不正です") from exc
    current_checksum = spec_checksum(current_spec)
    if current_checksum != body.base_checksum:
        raise HTTPException(status_code=409, detail={
            "code": "PATCH_BASE_CHANGED",
            "message": "編集中にApplication Specが更新されました。最新状態で差分を再確認してください。",
            "currentChecksum": current_checksum,
        })
    result = preview_patches(current_spec, body.patches)
    if not result["valid"]:
        raise HTTPException(status_code=422, detail={"diagnostics": result["diagnostics"]})
    patched_spec = result["patchedSpec"]
    row.application_spec_json = json.dumps(patched_spec, ensure_ascii=False)
    row.schema_version = int(patched_spec.get("schemaVersion") or 1)
    app = patched_spec.get("application") or {}
    row.application_type = str(app.get("applicationType") or row.application_type)
    target_profile = next(iter(patched_spec.get("targets") or []), {})
    row.ui_framework = str(target_profile.get("framework") or row.ui_framework)
    db.commit()
    db.refresh(row)
    audit.record(
        db, "application_project.patch_apply", user=user,
        resource_type="application_project", resource_id=str(row.id), request=request,
        metadata={
            "patch_count": len(body.patches), "base_checksum": current_checksum,
            "result_checksum": result["resultChecksum"],
        },
    )
    return {"project": project_out(row), "patch": result}


@router.post("/application-projects/{project_id}/design-proposals")
async def create_design_proposals(
    project_id: int, body: ApplicationDesignProposalRequest, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    """実codeを生成せず、3つのApplication Spec Patch案を生成・静的検証する。"""
    row = get_project(db, project_id)
    try:
        spec = json.loads(row.application_spec_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail="保存済みApplication Specが不正です") from exc
    from app.models_mgmt.providers import list_providers

    providers = await list_providers(include_unavailable=True)
    endpoint = next((item for item in providers if str(item.get("base_url", "")).rstrip("/") == body.base_url.rstrip("/")), None)
    if endpoint is None or body.model not in endpoint.get("models", []):
        raise HTTPException(status_code=422, detail="登録済みLLM endpointとmodelを選択してください")
    try:
        result = await generate_design_proposals(spec, body)
    except ProposalInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProposalGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.record(
        db, "application_project.design_proposals", user=user,
        resource_type="application_project", resource_id=str(row.id), request=request,
        metadata={
            "scope": body.scope, "mode": body.mode, "proposal_count": len(result["proposals"]),
            "provider": str(endpoint.get("provider") or "unknown"), "model": body.model,
        },
    )
    return result


@router.delete("/application-projects/{project_id}", status_code=204)
def delete_project(
    project_id: int, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    row = get_project(db, project_id)
    project_builds = db.execute(select(ApplicationBuild).where(ApplicationBuild.project_id == project_id)).scalars().all()
    for build in project_builds:
        try:
            builds.delete_build(db, build)
        except builds.ApplicationBuildError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.delete(row)
    db.commit()
    audit.record(db, "application_project.delete", user=user, resource_type="application_project", resource_id=str(project_id), request=request, metadata={"deleted_build_count": len(project_builds)})


def _source_input(db: Session, row: ApplicationProject) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        spec = json.loads(row.application_spec_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail="保存済みApplication Specが不正です") from exc
    validation = validate_payload(db, spec, workflow_id=row.workflow_id, workflow_version_id=None, target="csharp")
    return spec, validation
