from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User, Workflow, WorkflowExecution, WorkflowSecret, WorkflowVersion
from app.security.crypto import encrypt_text
from app.security.deps import require_permission
from app.workflows import engine

MAX_VERSIONS_PER_WORKFLOW = 20

router = APIRouter(tags=["workflows"])


@router.get("/workflows/llm-endpoints")
async def llm_endpoints(user: User = Depends(require_permission("workflows.edit"))):
    """ローカルで稼働中の OpenAI 互換 LLM サーバーを検出する。

    管理アプリの待受ポート + 代表的な LLM ポート（Ollama/llama.cpp/LM Studio 等）へ
    GET /v1/models を試し、応答したものをモデル一覧付きで返す。
    """
    from app.models_mgmt.providers import list_providers

    return await list_providers(include_unavailable=False, exclude_port=get_server_port())


def get_server_port() -> int:
    from app.config import get_config

    return get_config().server.port


class ScrapeAnalyzeBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/workflows/scrape/analyze")
async def scrape_analyze(
    body: ScrapeAnalyzeBody, user: User = Depends(require_permission("workflows.edit"))
):
    """URL を取得し、候補セレクタ + ビューワ用サニタイズ HTML を返す。"""
    from app.workflows import scrape_tools as st

    try:
        html, status, final_url = await st.fetch(body.url)
    except st.ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "status_code": status,
        "final_url": final_url,
        "candidates": st.analyze(html),
        "viewer_html": st.sanitize_for_viewer(html, final_url),
    }


class Extractor(BaseModel):
    name: str = ""
    selector: str = ""
    attribute: str = "text"
    multiple: bool = False


class ScrapePreviewBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    extractors: list[Extractor] = Field(default_factory=list, max_length=30)


@router.post("/workflows/scrape/preview")
async def scrape_preview(
    body: ScrapePreviewBody, user: User = Depends(require_permission("workflows.edit"))
):
    """URL を取得し、各抽出器の結果プレビュー（抽出ワード↔結果の対比）を返す。"""
    from app.workflows import scrape_tools as st

    try:
        html, status, _ = await st.fetch(body.url)
    except st.ScrapeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    results = {}
    for ex in body.extractors:
        key = ex.name or ex.selector
        if not ex.selector:
            continue
        results[key] = st.preview(html, ex.selector, ex.attribute, ex.multiple)
    return {"status_code": status, "results": results}


class WorkflowBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    definition: dict = Field(default_factory=dict)


class WorkflowPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    definition: dict | None = None


class DryRunBody(BaseModel):
    definition: dict
    input: dict = Field(default_factory=dict)


@router.get("/workflows/node-catalog")
def workflow_node_catalog(user: User = Depends(require_permission("workflows.run"))):
    """backendを正とする全nodeの型・capability・副作用metadata。"""
    from app.workflows.node_metadata import node_catalog

    return node_catalog()


@router.post("/workflows/dry-run-definition")
def dry_run_definition(
    body: DryRunBody,
    user: User = Depends(require_permission("workflows.run")),
):
    """編集中definitionを保存/実行せず静的シミュレーションする。"""
    from app.workflows.dry_run import simulate_definition

    return simulate_definition(body.definition, body.input)


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
        if definition != wf.definition_json:
            _snapshot_version(db, wf, note="保存前の状態")
        wf.definition_json = definition
    db.commit()
    audit.record(db, "workflow.update", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request)
    return _out(wf, db)


# ---- バージョン管理 ----


def _snapshot_version(db: Session, wf: Workflow, note: str = "") -> None:
    """現在の定義をスナップショットとして保存し、上限を超えた古い版を削除する。"""
    db.add(WorkflowVersion(workflow_id=wf.id, name=wf.name, definition_json=wf.definition_json, note=note))
    olds = db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == wf.id)
        .order_by(WorkflowVersion.created_at.desc())
        .offset(MAX_VERSIONS_PER_WORKFLOW)
    ).scalars().all()
    for old in olds:
        db.delete(old)


@router.get("/workflows/{workflow_id}/versions")
def list_versions(
    workflow_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    rows = db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.created_at.desc())
    ).scalars().all()
    out = []
    for v in rows:
        try:
            node_count = len(json.loads(v.definition_json or "{}").get("nodes", []))
        except json.JSONDecodeError:
            node_count = 0
        out.append({"id": v.id, "name": v.name, "note": v.note, "created_at": v.created_at, "node_count": node_count})
    return out


@router.post("/workflows/{workflow_id}/versions/{version_id}/restore")
def restore_version(
    workflow_id: int,
    version_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    wf = _get(db, workflow_id)
    v = db.get(WorkflowVersion, version_id)
    if v is None or v.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="バージョンが見つかりません")
    _snapshot_version(db, wf, note="復元前の状態")
    wf.definition_json = v.definition_json
    db.commit()
    audit.record(db, "workflow.restore", user=user, resource_type="workflow",
                 resource_id=str(workflow_id), request=request, metadata={"version_id": version_id})
    return _out(wf, db)


# ---- シークレット（{{secrets.名前}}） ----


@router.get("/workflows-secrets")
def list_secrets(user: User = Depends(require_permission("workflows.edit")), db: Session = Depends(get_db)):
    rows = db.execute(select(WorkflowSecret).order_by(WorkflowSecret.name)).scalars().all()
    return [{"name": s.name, "updated_at": s.updated_at} for s in rows]  # 値は返さない


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


@router.put("/workflows-secrets/{name}")
def put_secret(
    name: str,
    body: SecretBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    import re

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise HTTPException(status_code=422, detail="名前は英字始まりの英数字と _ のみ（64 文字まで）")
    row = db.execute(select(WorkflowSecret).where(WorkflowSecret.name == name)).scalar_one_or_none()
    if row is None:
        row = WorkflowSecret(name=name, value_encrypted="")
        db.add(row)
    row.value_encrypted = encrypt_text(body.value)
    db.commit()
    audit.record(db, "workflow.secret_set", user=user, resource_type="secret", resource_id=name, request=request)
    return {"name": name}


@router.delete("/workflows-secrets/{name}", status_code=204)
def delete_secret(
    name: str,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(WorkflowSecret).where(WorkflowSecret.name == name)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="シークレットが見つかりません")
    db.delete(row)
    db.commit()
    audit.record(db, "workflow.secret_delete", user=user, resource_type="secret", resource_id=name, request=request)


# ---- ノード単体テスト ----


class TestNodeBody(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    config: dict = Field(default_factory=dict)
    dry_run: bool = False  # 既存API互換。新UIは明示的にtrueを送る。


@router.post("/workflows/test-node")
async def test_node(body: TestNodeBody, user: User = Depends(require_permission("workflows.edit"))):
    """dry_run=trueは副作用なしpreview。falseは既存の実executorテスト。"""
    import asyncio

    from app.workflows.nodes import DEFAULT_NODE_TIMEOUT, NODE_EXECUTORS, NODE_TIMEOUTS, NodeError

    if body.dry_run:
        from app.workflows.dry_run import simulate_node

        try:
            return simulate_node(body.type, body.config)
        except engine.DefinitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.type in ("trigger", "control.loop"):
        raise HTTPException(status_code=422, detail="このノードは単体テストできません")
    executor = NODE_EXECUTORS.get(body.type)
    if executor is None:
        raise HTTPException(status_code=422, detail=f"未知のノード種類: {body.type}")
    ctx: dict = {"__vars__": {}, "__secrets__": await asyncio.to_thread(engine._load_secrets)}
    timeout = min(NODE_TIMEOUTS.get(body.type, DEFAULT_NODE_TIMEOUT), 180)
    import time as _time

    t0 = _time.time()
    try:
        output = await asyncio.wait_for(executor(body.config, ctx), timeout=timeout)
        return {"ok": True, "output": output, "elapsed": round(_time.time() - t0, 2)}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"タイムアウト（{timeout} 秒）", "elapsed": round(_time.time() - t0, 2)}
    except NodeError as e:
        return {"ok": False, "error": str(e), "elapsed": round(_time.time() - t0, 2)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "elapsed": round(_time.time() - t0, 2)}


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
    input: dict = Field(default_factory=dict)


@router.post("/workflows/{workflow_id}/dry-run")
def dry_run_workflow(
    workflow_id: int,
    body: RunBody | None = None,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """保存済みworkflowをExecution作成なしで静的シミュレーションする。"""
    from app.workflows.dry_run import simulate_definition

    workflow = _get(db, workflow_id)
    try:
        definition = json.loads(workflow.definition_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"定義JSONが不正です: {exc}") from exc
    return simulate_definition(definition, body.input if body else {})


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


@router.get("/workflow-executions/{execution_id}/live")
def get_execution_live(
    execution_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """実行中コンテキストのライブ参照（3 秒フラッシュを待たずに現在の状態を返す）。"""
    r = db.get(WorkflowExecution, execution_id)
    if r is None:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    live = engine.live_context(execution_id)
    context = ({k: v for k, v in live.items() if not k.startswith("__")}
               if live is not None else json.loads(r.context_json or "{}"))
    context = {k: v for k, v in context.items() if not k.startswith("__")}
    # LLM ノードのトークン合計（コスト把握用）
    total_tokens = 0
    for entry in context.values():
        if isinstance(entry, dict) and isinstance(entry.get("output"), dict):
            t = entry["output"].get("tokens")
            if isinstance(t, (int, float)):
                total_tokens += int(t)
    return {
        "id": r.id,
        "workflow_id": r.workflow_id,
        "status": r.status if live is None else ("RUNNING" if r.status == "RUNNING" else r.status),
        "running": live is not None,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error": r.error,
        "context": context,
        "pending_approvals": engine.pending_approvals(execution_id),
        "total_tokens": total_tokens,
    }


class ApproveBody(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    approve: bool = True


@router.post("/workflow-executions/{execution_id}/approve")
def approve_execution_node(
    execution_id: int,
    body: ApproveBody,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """承認待ちノードを承認/却下して実行を再開する。"""
    if not engine.resolve_approval(execution_id, body.node_id, body.approve):
        raise HTTPException(status_code=409, detail="このノードは承認待ちではありません")
    audit.record(db, "workflow.approve" if body.approve else "workflow.reject", user=user,
                 resource_type="workflow_execution", resource_id=str(execution_id),
                 request=request, metadata={"node_id": body.node_id})
    return {"ok": True, "approved": body.approve}


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
