"""公開ワークフローを内部定義なしで操作するRunner API。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User, Workflow, WorkflowExecution, WorkflowVersion
from app.security.deps import require_permission
from app.workflows import engine
from app.workflows.contracts import final_outputs, validate_public_inputs
from app.workflows.node_metadata import SIDE_EFFECTS
from app.workflows.redaction import collect_sensitive_values, redact

router = APIRouter(prefix="/workflow-runner", tags=["workflow-runner"])


class RunnerRunBody(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class RunnerApprovalBody(BaseModel):
    approval_id: str = Field(min_length=1, max_length=64)
    approve: bool = True


def _published(db: Session, workflow_id: int) -> tuple[Workflow, WorkflowVersion]:
    workflow = db.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="公開ワークフローが見つかりません")
    version = db.execute(select(WorkflowVersion).where(
        WorkflowVersion.workflow_id == workflow_id,
        WorkflowVersion.published_at.is_not(None),
    ).order_by(WorkflowVersion.published_at.desc()).limit(1)).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="公開ワークフローが見つかりません")
    return workflow, version


def _schema(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _side_effects(version: WorkflowVersion) -> list[str]:
    try:
        definition = json.loads(version.definition_json or "{}")
    except json.JSONDecodeError:
        return []
    order = {"read": 1, "write": 2, "external": 3, "process": 4}
    found = {SIDE_EFFECTS.get(str(node.get("type") or ""), "none") for node in definition.get("nodes", [])}
    return sorted((item for item in found if item != "none"), key=lambda item: order.get(item, 99))


def _app(workflow: Workflow, version: WorkflowVersion) -> dict[str, Any]:
    input_schema = _schema(version.input_schema_json)
    output_schema = _schema(version.output_schema_json)
    return {
        "id": workflow.id, "name": version.name or workflow.name,
        "description": version.description, "version": version.version,
        "published_at": version.published_at, "enabled": workflow.enabled,
        "input_count": len(input_schema.get("properties", {})),
        "output_count": len(output_schema.get("properties", {})),
        "side_effects": _side_effects(version),
    }


def _execution(db: Session, execution_id: int, workflow_id: int | None = None) -> WorkflowExecution:
    execution = db.get(WorkflowExecution, execution_id)
    if execution is None or (workflow_id is not None and execution.workflow_id != workflow_id):
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    version = db.get(WorkflowVersion, execution.workflow_version_id) if execution.workflow_version_id else None
    if version is None or version.workflow_id != execution.workflow_id or version.published_at is None:
        raise HTTPException(status_code=404, detail="公開実行が見つかりません")
    return execution


@router.get("")
def list_apps(
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    versions = db.execute(select(WorkflowVersion).where(
        WorkflowVersion.published_at.is_not(None),
    ).order_by(WorkflowVersion.published_at.desc())).scalars().all()
    latest: dict[int, WorkflowVersion] = {}
    for version in versions:
        latest.setdefault(version.workflow_id, version)
    workflows = {row.id: row for row in db.execute(select(Workflow).where(Workflow.id.in_(latest))).scalars().all()} if latest else {}
    return [_app(workflows[workflow_id], version) for workflow_id, version in latest.items() if workflow_id in workflows]


@router.get("/executions/{execution_id}")
def get_run(
    execution_id: int, user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    execution = _execution(db, execution_id)
    live = engine.live_context(execution_id)
    raw = live if live is not None else json.loads(execution.context_json or "{}")
    sensitive = collect_sensitive_values(raw)
    if live is not None:
        sensitive.update(str(value) for value in (live.get("__secrets__") or {}).values() if value)
    clean = redact(raw, sensitive_values=sensitive)
    approvals = []
    for item in engine.pending_approvals(execution_id):
        approvals.append({
            "approval_id": str(item.get("node_id") or ""),
            "message": str(redact(item.get("message") or "承認が必要です", sensitive_values=sensitive)),
            "approver": str(item.get("approver") or ""),
            "expires_at": item.get("expires_at"),
        })
    return {
        "id": execution.id, "workflow_id": execution.workflow_id, "status": execution.status,
        "trigger_type": execution.trigger_type, "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "error": str(redact(execution.error, sensitive_values=sensitive)),
        "input": clean.get("__input__", {}),
        "outputs": final_outputs(clean, expose_source=False),
        "pending_approvals": approvals,
    }


@router.post("/executions/{execution_id}/cancel")
def cancel_run(
    execution_id: int, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    _execution(db, execution_id)
    if not engine.cancel_execution(execution_id):
        raise HTTPException(status_code=409, detail="この実行はすでに終了しています")
    audit.record(db, "workflow.runner_cancel", user=user, resource_type="workflow_execution",
                 resource_id=str(execution_id), request=request)
    return {"ok": True}


@router.post("/executions/{execution_id}/approval")
def approve_run(
    execution_id: int, body: RunnerApprovalBody, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    _execution(db, execution_id)
    details = engine.approval_details(execution_id, body.approval_id)
    if details is None:
        raise HTTPException(status_code=409, detail="この承認は待機中ではありません")
    approver = str(details.get("approver") or "").strip()
    if approver and approver != user.username:
        raise HTTPException(status_code=403, detail=f"この承認はユーザー '{approver}' に割り当てられています")
    if not engine.resolve_approval(execution_id, body.approval_id, body.approve):
        raise HTTPException(status_code=409, detail="この承認は待機中ではありません")
    audit.record(db, "workflow.runner_approve" if body.approve else "workflow.runner_reject",
                 user=user, resource_type="workflow_execution", resource_id=str(execution_id),
                 request=request)
    return {"ok": True, "approved": body.approve}


@router.get("/{workflow_id}")
def get_app(
    workflow_id: int, user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    workflow, version = _published(db, workflow_id)
    return {
        **_app(workflow, version),
        "input_schema": _schema(version.input_schema_json),
        "output_schema": _schema(version.output_schema_json),
    }


@router.get("/{workflow_id}/runs")
def list_runs(
    workflow_id: int, limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    _published(db, workflow_id)
    rows = db.execute(select(WorkflowExecution).where(
        WorkflowExecution.workflow_id == workflow_id,
        WorkflowExecution.workflow_version_id.in_(select(WorkflowVersion.id).where(
            WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.published_at.is_not(None),
        )),
    ).order_by(WorkflowExecution.started_at.desc()).limit(limit)).scalars().all()
    return [{
        "id": row.id, "status": row.status, "trigger_type": row.trigger_type,
        "started_at": row.started_at, "finished_at": row.finished_at,
    } for row in rows]


@router.post("/{workflow_id}/runs")
async def start_run(
    workflow_id: int, body: RunnerRunBody, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    _workflow, version = _published(db, workflow_id)
    errors = validate_public_inputs(_schema(version.input_schema_json), body.input)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    try:
        execution_id = await engine.run_workflow(
            workflow_id, trigger_type="runner", input_data=body.input, published_only=True,
        )
    except engine.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "workflow.runner_run", user=user, resource_type="workflow",
                 resource_id=str(workflow_id), request=request,
                 metadata={"execution_id": execution_id, "version_id": version.id})
    return {"execution_id": execution_id, "version": version.version}
