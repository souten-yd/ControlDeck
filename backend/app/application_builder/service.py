from __future__ import annotations

import copy
import json
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application_builder.compiler import compile_application, compile_workflow, default_spec, workflow_app_spec
from app.application_builder.diagnostics import diagnostic
from app.models import ApplicationProject, Workflow, WorkflowVersion
from app.schemas.application_builder import ApplicationSpecV1
from app.workflows.contracts import build_input_schema, build_output_schema


def project_out(row: ApplicationProject) -> dict[str, Any]:
    try:
        spec = json.loads(row.application_spec_json or "{}")
    except json.JSONDecodeError:
        spec = {}
    return {
        "id": row.id, "name": row.name, "description": row.description, "workflow_id": row.workflow_id,
        "spec": spec, "schema_version": row.schema_version, "target": row.target,
        "application_type": row.application_type, "ui_framework": row.ui_framework,
        "status": row.status, "created_by": row.created_by,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def get_project(db: Session, project_id: int) -> ApplicationProject:
    row = db.get(ApplicationProject, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Application Projectが見つかりません")
    return row


def workflow_source(
    db: Session, workflow_id: int, *, source: str = "draft", version_id: int | None = None,
) -> tuple[Workflow, dict[str, Any], int | None]:
    workflow = db.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="ワークフローが見つかりません")
    if source == "published" or version_id is not None:
        statement = select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id)
        if version_id is not None:
            statement = statement.where(WorkflowVersion.id == version_id)
        else:
            statement = statement.where(WorkflowVersion.published_at.is_not(None)).order_by(WorkflowVersion.published_at.desc()).limit(1)
        version = db.execute(statement).scalar_one_or_none()
        if version is None:
            raise HTTPException(status_code=409, detail="公開版がありません")
        return workflow, json.loads(version.definition_json or "{}"), version.id
    return workflow, json.loads(workflow.definition_json or "{}"), None


def validate_payload(
    db: Session, spec: dict[str, Any], *, workflow_id: int | None, workflow_version_id: int | None,
    target: str,
) -> dict[str, Any]:
    application_ir = compile_application(spec)
    try:
        normalized_spec = ApplicationSpecV1.model_validate(spec).model_dump(by_alias=True)
    except ValidationError:
        normalized_spec = copy.deepcopy(spec)
    workflow_ir = None
    diagnostics = list(application_ir.diagnostics)
    bindings = spec.get("workflows") if isinstance(spec.get("workflows"), list) else []
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict) or not isinstance(binding.get("workflowId"), int):
            diagnostics.append(diagnostic(
                "WORKFLOW_BINDING_INVALID", "error", "workflowIdは整数で指定してください",
                path=f"workflows.{index}.workflowId", source="binding-validator",
            ))
            continue
        bound_workflow = db.get(Workflow, binding["workflowId"])
        if bound_workflow is None:
            diagnostics.append(diagnostic(
                "WORKFLOW_REFERENCE_MISSING", "error", f"Workflow #{binding['workflowId']} が存在しません",
                path=f"workflows.{index}.workflowId", source="binding-validator",
            ))
        version_id = binding.get("workflowVersionId")
        if version_id is not None:
            version = db.get(WorkflowVersion, version_id)
            if version is None or version.workflow_id != binding["workflowId"]:
                diagnostics.append(diagnostic(
                    "WORKFLOW_VERSION_REFERENCE_INVALID", "error", "Workflow versionが対象Workflowに属していません",
                    path=f"workflows.{index}.workflowVersionId", source="binding-validator",
                ))
    if workflow_id is not None and not any(
        isinstance(binding, dict) and binding.get("workflowId") == workflow_id for binding in bindings
    ):
        diagnostics.append(diagnostic(
            "WORKFLOW_BINDING_MISSING", "error", f"Application SpecにWorkflow #{workflow_id} のbindingがありません",
            path="workflows", source="binding-validator",
        ))
    if workflow_id is not None:
        if workflow_version_id is None:
            binding = next((item for item in spec.get("workflows", []) if item.get("workflowId") == workflow_id), None)
            if isinstance(binding, dict) and binding.get("workflowVersionId") is not None:
                workflow_version_id = int(binding["workflowVersionId"])
        workflow, definition, resolved_version_id = workflow_source(
            db, workflow_id, source="published" if workflow_version_id else "draft", version_id=workflow_version_id,
        )
        workflow_ir = compile_workflow(
            definition, name=workflow.name, workflow_id=workflow.id,
            workflow_version_id=resolved_version_id, target=target,
        )
        diagnostics.extend(workflow_ir.diagnostics)
    from app.application_builder.builds import build_capability
    from app.application_builder.capabilities import FRAMEWORK_BY_ID

    source_target = next((
        item for item in spec.get("targets", [])
        if isinstance(item, dict) and FRAMEWORK_BY_ID.get(str(item.get("framework") or ""), {}).get("source") is True
    ), None)
    generation_available = bool(source_target) and not any(item.severity == "error" for item in diagnostics)
    if workflow_ir is not None:
        generation_available = generation_available and all(node.codegen.source_available for node in workflow_ir.nodes)
    local_build = build_capability()
    return {
        "valid": not any(item.severity == "error" for item in diagnostics),
        "normalizedSpec": normalized_spec,
        "workflowIr": workflow_ir.model_dump(by_alias=True) if workflow_ir else None,
        "applicationIr": application_ir.model_dump(by_alias=True),
        "diagnostics": [item.model_dump(by_alias=True) for item in diagnostics],
        "capability": {
            "target": target, "generationAvailable": generation_available,
            "buildAvailable": bool(generation_available and local_build["available"]),
            "note": "対応するC# Console／ASP.NET sourceは、SDK検出時にnetwork denied・resource制限付きsystemd user unitでbuild／self-testできます。",
        },
    }


def create_default_project(
    db: Session, workflow: Workflow, *, name: str | None, description: str | None,
    source: str = "draft", workflow_version_id: int | None = None,
) -> ApplicationProject:
    project_name = name or f"{workflow.name} App"
    project_description = workflow.description if description is None else description
    _workflow, definition, resolved_version_id = workflow_source(
        db, workflow.id, source=source, version_id=workflow_version_id,
    )
    spec = workflow_app_spec(
        project_name, project_description, workflow.id,
        input_schema=build_input_schema(definition), output_schema=build_output_schema(definition),
        workflow_version_id=resolved_version_id, source=source,
    )
    return ApplicationProject(
        name=project_name, description=project_description, workflow_id=workflow.id,
        application_spec_json=json.dumps(spec, ensure_ascii=False), schema_version=1,
        target="csharp", application_type="web", ui_framework="aspnet-blazor", status="draft",
    )
