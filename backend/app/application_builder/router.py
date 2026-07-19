from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application_builder.capabilities import capability_catalog
from app.application_builder.compiler import default_spec, validate_application_spec
from app.application_builder.design_system.components import component_catalog
from app.application_builder.patch_service import preview_patches, spec_checksum
from app.application_builder.proposal_service import ProposalGenerationError, ProposalInputError, generate_design_proposals
from app.application_builder.service import create_default_project, get_project, project_out, validate_payload, workflow_source
from app.audit import service as audit
from app.database import get_db
from app.models import ApplicationProject, User
from app.schemas.application_builder import (
    ApplicationDesignProposalRequest, ApplicationPatchApplyBody, ApplicationPatchPreviewBody, ApplicationProjectCreate,
    ApplicationProjectUpdate, ApplicationValidateBody, WorkflowApplicationCreate,
)
from app.security.deps import require_permission

router = APIRouter(tags=["application-builder"])


@router.get("/application-builder/schema")
def application_schema(user: User = Depends(require_permission("application_builder.view"))):
    from app.schemas.application_builder import (
        ApplicationDesignProposalRequest, ApplicationPatchOperation, ApplicationSpecV1, ApplicationValidateBody,
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
    }


@router.get("/application-builder/capabilities")
def application_capabilities(user: User = Depends(require_permission("application_builder.view"))):
    return capability_catalog()


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
    if body.workflow_id is not None:
        workflow, _definition, _version = workflow_source(db, body.workflow_id)
    spec = body.spec or default_spec(body.name, body.description, body.workflow_id)
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
    db.delete(row)
    db.commit()
    audit.record(db, "application_project.delete", user=user, resource_type="application_project", resource_id=str(project_id), request=request, metadata={})
