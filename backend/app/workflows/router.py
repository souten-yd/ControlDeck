from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User, Workflow, WorkflowExecution
from app.security.deps import require_permission
from app.workflows import engine

router = APIRouter(tags=["workflows"])


@router.get("/workflows/llm-endpoints")
async def llm_endpoints(user: User = Depends(require_permission("workflows.edit"))):
    """ローカルで稼働中の OpenAI 互換 LLM サーバーを検出する。

    管理アプリの待受ポート + 代表的な LLM ポート（Ollama/llama.cpp/LM Studio 等）へ
    GET /v1/models を試し、応答したものをモデル一覧付きで返す。
    """
    import asyncio

    import httpx

    ports: set[int] = {11434, 8080, 1234, 8000, 5001}
    try:
        from app.applications import service as apps_svc
        from app.database import SessionLocal
        from app.models import ManagedApplication

        def app_ports() -> set[int]:
            db = SessionLocal()
            try:
                found: set[int] = set()
                for app in db.query(ManagedApplication).all():
                    rt = apps_svc.runtime_info(app)
                    found.update(rt.listening_ports or [])
                return found
            finally:
                db.close()

        ports |= await asyncio.to_thread(app_ports)
    except Exception:
        pass
    ports.discard(get_server_port())

    async def probe(port: int) -> dict | None:
        base = f"http://127.0.0.1:{port}/v1"
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                r = await client.get(f"{base}/models")
            if r.status_code != 200:
                return None
            data = r.json()
            models = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
            return {"base_url": base, "models": [m for m in models if m][:50]}
        except Exception:
            return None

    results = await asyncio.gather(*(probe(p) for p in sorted(ports)))
    return [r for r in results if r]


def get_server_port() -> int:
    from app.config import get_config

    return get_config().server.port


class WorkflowBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    definition: dict = {}


class WorkflowPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    definition: dict | None = None


def _get(db: Session, workflow_id: int) -> Workflow:
    wf = db.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="ワークフローが見つかりません")
    return wf


def _out(wf: Workflow, db: Session) -> dict:
    last = db.execute(
        select(WorkflowExecution)
        .where(WorkflowExecution.workflow_id == wf.id)
        .order_by(WorkflowExecution.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "definition": json.loads(wf.definition_json or "{}"),
        "enabled": wf.enabled,
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
        "last_execution": {
            "id": last.id,
            "status": last.status,
            "started_at": last.started_at,
            "finished_at": last.finished_at,
        }
        if last
        else None,
    }


@router.get("/workflows")
def list_workflows(
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)
):
    rows = db.execute(select(Workflow).order_by(Workflow.name)).scalars().all()
    return [_out(w, db) for w in rows]


@router.post("/workflows", status_code=201)
def create_workflow(
    body: WorkflowBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    definition = json.dumps(body.definition, ensure_ascii=False)
    try:
        engine.validate_definition(definition)
    except engine.DefinitionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    wf = Workflow(
        name=body.name, description=body.description,
        definition_json=definition, created_by=user.id,
    )
    db.add(wf)
    db.commit()
    audit.record(db, "workflow.create", user=user, resource_type="workflow", resource_id=str(wf.id), request=request, metadata={"name": wf.name})
    return _out(wf, db)


@router.get("/workflows/{workflow_id}")
def get_workflow(
    workflow_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    return _out(_get(db, workflow_id), db)


@router.patch("/workflows/{workflow_id}")
def update_workflow(
    workflow_id: int,
    body: WorkflowPatch,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    wf = _get(db, workflow_id)
    if body.name is not None:
        wf.name = body.name
    if body.description is not None:
        wf.description = body.description
    if body.definition is not None:
        definition = json.dumps(body.definition, ensure_ascii=False)
        try:
            engine.validate_definition(definition)
        except engine.DefinitionError as e:
            raise HTTPException(status_code=422, detail=str(e))
        wf.definition_json = definition
    db.commit()
    audit.record(db, "workflow.update", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request)
    return _out(wf, db)


@router.delete("/workflows/{workflow_id}")
def delete_workflow(
    workflow_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    wf = _get(db, workflow_id)
    name = wf.name
    from sqlalchemy import delete as sql_delete

    db.execute(sql_delete(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id))
    db.delete(wf)
    db.commit()
    audit.record(db, "workflow.delete", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request, metadata={"name": name})
    return {"ok": True}


class RunBody(BaseModel):
    input: dict = {}


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: int,
    request: Request,
    body: RunBody | None = None,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    input_data = body.input if body else {}
    trigger_type = "chat" if input_data.get("message") else "manual"
    try:
        execution_id = await engine.run_workflow(workflow_id, trigger_type=trigger_type, input_data=input_data)
    except engine.DefinitionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "workflow.run", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request)
    return {"execution_id": execution_id}


@router.post("/workflows/{workflow_id}/enable")
def enable_workflow(
    workflow_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    wf = _get(db, workflow_id)
    wf.enabled = True
    db.commit()
    audit.record(db, "workflow.enable", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request)
    return {"ok": True}


@router.post("/workflows/{workflow_id}/disable")
def disable_workflow(
    workflow_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    wf = _get(db, workflow_id)
    wf.enabled = False
    db.commit()
    audit.record(db, "workflow.disable", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request)
    return {"ok": True}


@router.get("/workflow-executions")
def list_executions(
    workflow_id: int | None = None,
    limit: int = Query(default=30, le=200),
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    stmt = select(WorkflowExecution).order_by(WorkflowExecution.started_at.desc()).limit(limit)
    if workflow_id is not None:
        stmt = stmt.where(WorkflowExecution.workflow_id == workflow_id)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "workflow_id": r.workflow_id,
            "status": r.status,
            "trigger_type": r.trigger_type,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "error": r.error,
        }
        for r in rows
    ]


@router.get("/workflow-executions/{execution_id}")
def get_execution(
    execution_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    r = db.get(WorkflowExecution, execution_id)
    if r is None:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    return {
        "id": r.id,
        "workflow_id": r.workflow_id,
        "status": r.status,
        "trigger_type": r.trigger_type,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error": r.error,
        "context": json.loads(r.context_json or "{}"),
    }


@router.post("/workflow-executions/{execution_id}/cancel")
def cancel_execution(
    execution_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    r = db.get(WorkflowExecution, execution_id)
    if r is None:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    if not engine.cancel_execution(execution_id):
        raise HTTPException(status_code=409, detail="この実行はすでに終了しています")
    audit.record(db, "workflow.cancel", user=user, resource_type="workflow_execution", resource_id=str(execution_id), request=request)
    return {"ok": True}
