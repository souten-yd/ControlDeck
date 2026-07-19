from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import (
    User, Workflow, WorkflowExecution, WorkflowNodeRun, WorkflowPinnedData, WorkflowSecret, WorkflowTestCase,
    WorkflowVersion, utcnow,
)
from app.security.crypto import encrypt_text
from app.security.deps import require_permission
from app.workflows import engine
from app.workflows.contracts import build_input_schema, build_output_schema, final_outputs
from app.workflows.publish_validation import check_publishability
from app.workflows.redaction import collect_sensitive_values, redact

MAX_VERSIONS_PER_WORKFLOW = 20
logger = logging.getLogger(__name__)

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


@router.post("/workflows/preview-definition")
def preview_definition(
    body: DryRunBody,
    user: User = Depends(require_permission("workflows.run")),
):
    """Canonical editor preview endpoint; never creates an execution or calls an executor."""
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
    published = db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == wf.id, WorkflowVersion.published_at.is_not(None),
        ).order_by(WorkflowVersion.published_at.desc()).limit(1)
    ).scalar_one_or_none()
    draft_checksum = hashlib.sha256((wf.definition_json or "{}").encode()).hexdigest()
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "definition": json.loads(wf.definition_json or "{}"),
        "enabled": wf.enabled,
        "state": "published" if published and published.checksum == draft_checksum
        and published.name == wf.name and published.description == wf.description else "draft",
        "published_version": published.version if published else None,
        "published_version_id": published.id if published else None,
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
    user: User = Depends(require_permission("workflows.edit")), db: Session = Depends(get_db)
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
    user: User = Depends(require_permission("workflows.edit")),
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
    latest_number = db.execute(
        select(WorkflowVersion.version)
        .where(WorkflowVersion.workflow_id == wf.id)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none() or 0
    checksum = hashlib.sha256((wf.definition_json or "{}").encode()).hexdigest()
    safe_definition = engine.safe_definition_snapshot(json.loads(wf.definition_json or "{}"))
    db.add(WorkflowVersion(
        workflow_id=wf.id, version=latest_number + 1, name=wf.name,
        description=wf.description,
        definition_json=json.dumps(safe_definition, ensure_ascii=False), checksum=checksum, note=note,
    ))
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
    user: User = Depends(require_permission("workflows.edit")),
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
        out.append({
            "id": v.id, "version": v.version, "name": v.name, "note": v.note,
            "checksum": v.checksum, "created_at": v.created_at,
            "published_at": v.published_at, "node_count": node_count,
        })
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


@router.get("/workflows/{workflow_id}/versions/{version_id}")
def get_version(
    workflow_id: int,
    version_id: int,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    version = db.get(WorkflowVersion, version_id)
    if version is None or version.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="バージョンが見つかりません")
    return {
        "id": version.id, "workflow_id": version.workflow_id, "version": version.version,
        "name": version.name, "definition": json.loads(version.definition_json or "{}"),
        "input_schema": json.loads(version.input_schema_json or "{}"),
        "output_schema": json.loads(version.output_schema_json or "{}"),
        "checksum": version.checksum, "note": version.note,
        "created_at": version.created_at, "published_at": version.published_at,
    }


@router.post("/workflows/{workflow_id}/publish")
def publish_workflow(
    workflow_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    """現在のdraftを検証済みimmutable versionとして本番経路へ固定する。"""
    workflow = _get(db, workflow_id)
    definition = json.loads(workflow.definition_json or "{}")
    check = check_publishability(db, workflow, definition)
    if not check["publishable"]:
        raise HTTPException(status_code=409, detail=check)
    version = engine._ensure_execution_version(db, workflow)
    version.input_schema_json = json.dumps(build_input_schema(definition), ensure_ascii=False)
    version.output_schema_json = json.dumps(build_output_schema(definition), ensure_ascii=False)
    version.published_at = utcnow()
    version.note = "公開版"
    db.commit()
    audit.record(
        db, "workflow.publish", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"version_id": version.id, "version": version.version, "warnings": len(check["warnings"])},
    )
    return {
        "workflow_id": workflow_id, "version_id": version.id, "version": version.version,
        "published_at": version.published_at, "warnings": check["warnings"], "quality": check["quality"],
    }


class PublishCheckBody(BaseModel):
    definition: dict


@router.post("/workflows/{workflow_id}/publish-check")
def check_workflow_publishability(
    workflow_id: int,
    body: PublishCheckBody,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    """現在の編集内容を、実際の公開処理と同じ規則で副作用なし検証する。"""
    workflow = _get(db, workflow_id)
    return check_publishability(db, workflow, body.definition)


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


class WorkflowNodeTestBody(BaseModel):
    input_mode: str = Field(default="latest_success", pattern="^(latest_success|execution|manual|pinned)$")
    execution_id: int | None = None
    manual_context: dict = Field(default_factory=dict)
    config_override: dict = Field(default_factory=dict)


def _definition_node(workflow: Workflow, node_id: str) -> tuple[dict, dict]:
    definition = json.loads(workflow.definition_json or "{}")
    node = next((item for item in definition.get("nodes", []) if str(item.get("id")) == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="ノードが見つかりません")
    return definition, node


@router.post("/workflows/{workflow_id}/nodes/{node_id}/test")
async def test_workflow_node(
    workflow_id: int,
    node_id: str,
    body: WorkflowNodeTestBody,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """保存済み上流context、手動context、またはpinned outputで単一ノードを検証する。"""
    import asyncio
    import time as _time

    from app.workflows.nodes import DEFAULT_NODE_TIMEOUT, NODE_EXECUTORS, NODE_TIMEOUTS, NodeError

    workflow = _get(db, workflow_id)
    _, node = _definition_node(workflow, node_id)
    node_type = str(node.get("type") or "")
    if node_type in ("trigger", "control.loop") or node_type not in NODE_EXECUTORS:
        raise HTTPException(status_code=422, detail="このノードは単体テストできません")
    if body.input_mode == "pinned":
        pinned = db.execute(select(WorkflowPinnedData).where(
            WorkflowPinnedData.workflow_id == workflow_id, WorkflowPinnedData.node_id == node_id,
        )).scalar_one_or_none()
        if pinned is None:
            raise HTTPException(status_code=404, detail="固定データがありません")
        return {
            "ok": True, "output": json.loads(pinned.output_json or "{}"), "elapsed": 0,
            "status": "CACHED", "cache_source": f"pinned:{pinned.id}",
        }

    execution: WorkflowExecution | None = None
    if body.input_mode == "execution":
        execution = db.get(WorkflowExecution, body.execution_id) if body.execution_id is not None else None
        if execution is None or execution.workflow_id != workflow_id:
            raise HTTPException(status_code=404, detail="指定実行が見つかりません")
    elif body.input_mode == "latest_success":
        execution = db.execute(
            select(WorkflowExecution).where(
                WorkflowExecution.workflow_id == workflow_id, WorkflowExecution.status == "SUCCEEDED",
            ).order_by(WorkflowExecution.started_at.desc()).limit(1)
        ).scalar_one_or_none()
    context = dict(body.manual_context)
    if execution is not None:
        context = json.loads(execution.context_json or "{}")
    context["__secrets__"] = await asyncio.to_thread(engine._load_secrets)
    context.setdefault("__vars__", {})
    config = {**(node.get("config") or {}), **body.config_override}
    timeout = min(NODE_TIMEOUTS.get(node_type, DEFAULT_NODE_TIMEOUT), 180)
    started = _time.perf_counter()
    try:
        output = await asyncio.wait_for(NODE_EXECUTORS[node_type](config, context), timeout=timeout)
        sensitive = collect_sensitive_values(context)
        sensitive.update(str(value) for value in context.get("__secrets__", {}).values() if value)
        safe_output = redact(output, sensitive_values=sensitive)
        audit.record(
            db, "workflow.node_test", user=user, resource_type="workflow", resource_id=str(workflow_id),
            request=request, metadata={"node_id": node_id, "input_mode": body.input_mode},
        )
        return {
            "ok": True, "output": safe_output, "elapsed": round(_time.perf_counter() - started, 3),
            "status": "SUCCEEDED", "source_execution_id": execution.id if execution else None,
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"タイムアウト（{timeout} 秒）", "status": "TIMED_OUT"}
    except NodeError as exc:
        return {"ok": False, "error": str(exc), "status": "FAILED"}
    except Exception:
        logger.exception("workflow node test failed: workflow=%s node=%s", workflow_id, node_id)
        return {"ok": False, "error": "ノード実行に失敗しました。内部ログを確認してください", "status": "FAILED"}


class PinDataBody(BaseModel):
    output: Any
    source_execution_id: int | None = None


@router.get("/workflows/{workflow_id}/pinned-data")
def list_pinned_data(
    workflow_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    rows = db.execute(select(WorkflowPinnedData).where(WorkflowPinnedData.workflow_id == workflow_id)).scalars().all()
    return [{
        "id": row.id, "node_id": row.node_id, "output": json.loads(row.output_json or "{}"),
        "source_execution_id": row.source_execution_id, "updated_at": row.updated_at,
    } for row in rows]


@router.put("/workflows/{workflow_id}/nodes/{node_id}/pinned-data")
def put_pinned_data(
    workflow_id: int,
    node_id: str,
    body: PinDataBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    workflow = _get(db, workflow_id)
    _definition_node(workflow, node_id)
    if body.source_execution_id is not None:
        execution = db.get(WorkflowExecution, body.source_execution_id)
        if execution is None or execution.workflow_id != workflow_id:
            raise HTTPException(status_code=404, detail="固定元の実行が見つかりません")
    safe_output = redact(body.output, sensitive_values=collect_sensitive_values(body.output))
    serialized = json.dumps(safe_output, ensure_ascii=False, default=str)
    if len(serialized) > 1_000_000:
        raise HTTPException(status_code=413, detail="固定データは1MB以内にしてください")
    row = db.execute(select(WorkflowPinnedData).where(
        WorkflowPinnedData.workflow_id == workflow_id, WorkflowPinnedData.node_id == node_id,
    )).scalar_one_or_none()
    if row is None:
        row = WorkflowPinnedData(workflow_id=workflow_id, node_id=node_id)
        db.add(row)
    row.output_json = serialized
    row.source_execution_id = body.source_execution_id
    db.commit()
    audit.record(
        db, "workflow.pin", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"node_id": node_id, "source_execution_id": body.source_execution_id},
    )
    return {"id": row.id, "node_id": node_id, "output": safe_output}


@router.delete("/workflows/{workflow_id}/nodes/{node_id}/pinned-data", status_code=204)
def delete_pinned_data(
    workflow_id: int,
    node_id: str,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    row = db.execute(select(WorkflowPinnedData).where(
        WorkflowPinnedData.workflow_id == workflow_id, WorkflowPinnedData.node_id == node_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="固定データがありません")
    db.delete(row)
    db.commit()
    audit.record(
        db, "workflow.unpin", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"node_id": node_id},
    )


class WorkflowTestCaseBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    inputs: dict = Field(default_factory=dict)
    mocks: dict = Field(default_factory=dict)
    expected_outputs: dict = Field(default_factory=dict)
    assertions: list[dict] = Field(default_factory=list)


def _test_case_out(row: WorkflowTestCase) -> dict:
    return {
        "id": row.id, "workflow_id": row.workflow_id, "name": row.name,
        "inputs": json.loads(row.inputs_json or "{}"), "mocks": json.loads(row.mocks_json or "{}"),
        "expected_outputs": json.loads(row.expected_outputs_json or "{}"),
        "assertions": json.loads(row.assertions_json or "[]"),
        "last_execution_id": row.last_execution_id, "last_status": row.last_status,
        "last_result": json.loads(row.last_result_json or "{}"),
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _replace_sensitive_literals(value: Any, sensitive: set[str]) -> Any:
    """secret参照名は再現用に残し、別fieldへ複製されたliteral値だけも除去する。"""
    if isinstance(value, dict):
        return {str(key): _replace_sensitive_literals(child, sensitive) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_sensitive_literals(child, sensitive) for child in value]
    if isinstance(value, str):
        import re

        references = re.findall(r"\{\{\s*secrets\.[^}]+\}\}", value, flags=re.I)
        result = value
        for index, reference in enumerate(references):
            result = result.replace(reference, f"__CONTROL_DECK_SECRET_REF_{index}__", 1)
        for secret in sensitive:
            if secret:
                result = result.replace(secret, "***")
        for index, reference in enumerate(references):
            result = result.replace(f"__CONTROL_DECK_SECRET_REF_{index}__", reference)
        return result
    return value


def _set_test_case(row: WorkflowTestCase, body: WorkflowTestCaseBody) -> None:
    sensitive = collect_sensitive_values(body.model_dump())
    safe_inputs = _replace_sensitive_literals(engine.safe_definition_snapshot(body.inputs), sensitive)
    payloads = {
        "inputs_json": safe_inputs, "mocks_json": redact(body.mocks, sensitive_values=sensitive),
        "expected_outputs_json": redact(body.expected_outputs, sensitive_values=sensitive),
        "assertions_json": redact(body.assertions, sensitive_values=sensitive),
    }
    for field, value in payloads.items():
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        if len(serialized) > 1_000_000:
            raise HTTPException(status_code=413, detail="テストケースの各データは1MB以内にしてください")
        setattr(row, field, serialized)
    row.name = body.name.strip()


@router.get("/workflows/{workflow_id}/test-cases")
def list_workflow_test_cases(
    workflow_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    rows = db.execute(select(WorkflowTestCase).where(
        WorkflowTestCase.workflow_id == workflow_id,
    ).order_by(WorkflowTestCase.created_at)).scalars().all()
    recovered = False
    for row in rows:
        if row.last_status != "RUNNING" or row.last_execution_id is None:
            continue
        execution = db.get(WorkflowExecution, row.last_execution_id)
        if execution is not None and execution.status not in ("QUEUED", "RUNNING", "WAITING"):
            result = _evaluate_test_case(row, execution)
            row.last_result_json = json.dumps(result, ensure_ascii=False, default=str)
            row.last_status = "PASSED" if result["passed"] else "FAILED"
            recovered = True
    if recovered:
        db.commit()
    return [_test_case_out(row) for row in rows]


@router.post("/workflows/{workflow_id}/test-cases", status_code=201)
def create_workflow_test_case(
    workflow_id: int,
    body: WorkflowTestCaseBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    row = WorkflowTestCase(workflow_id=workflow_id, name=body.name.strip())
    _set_test_case(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    audit.record(
        db, "workflow.test_case_create", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"test_case_id": row.id},
    )
    return _test_case_out(row)


@router.put("/workflows/{workflow_id}/test-cases/{case_id}")
def update_workflow_test_case(
    workflow_id: int,
    case_id: int,
    body: WorkflowTestCaseBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    row = db.get(WorkflowTestCase, case_id)
    if row is None or row.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="テストケースが見つかりません")
    _set_test_case(row, body)
    row.last_status = "NEVER"
    row.last_execution_id = None
    row.last_result_json = "{}"
    db.commit()
    audit.record(
        db, "workflow.test_case_update", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"test_case_id": case_id},
    )
    return _test_case_out(row)


@router.delete("/workflows/{workflow_id}/test-cases/{case_id}", status_code=204)
def delete_workflow_test_case(
    workflow_id: int,
    case_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    row = db.get(WorkflowTestCase, case_id)
    if row is None or row.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="テストケースが見つかりません")
    db.delete(row)
    db.commit()
    audit.record(
        db, "workflow.test_case_delete", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"test_case_id": case_id},
    )


def _value_at_path(root: Any, path: str) -> tuple[bool, Any]:
    current = root
    for part in [item for item in path.split(".") if item]:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _evaluate_test_case(row: WorkflowTestCase, execution: WorkflowExecution) -> dict:
    context = json.loads(execution.context_json or "{}")
    outputs = _final_outputs(context)
    expected = json.loads(row.expected_outputs_json or "{}")
    assertions = json.loads(row.assertions_json or "[]")
    checks: list[dict] = []
    for name, wanted in expected.items():
        actual = outputs.get(name, {}).get("value")
        checks.append({"path": f"outputs.{name}.value", "operator": "equals", "expected": wanted,
                       "actual": actual, "passed": actual == wanted})
    root = {"outputs": outputs, "context": context}
    for assertion in assertions:
        path = str(assertion.get("path") or "")
        operator = str(assertion.get("operator") or "equals")
        wanted = assertion.get("expected")
        found, actual = _value_at_path(root, path)
        passed = False
        if operator == "exists":
            passed = found
        elif operator == "not_exists":
            passed = not found
        elif operator == "equals":
            passed = found and actual == wanted
        elif operator == "contains":
            passed = found and ((isinstance(actual, str) and str(wanted) in actual) or
                                (isinstance(actual, list) and wanted in actual))
        elif operator in ("gt", "gte", "lt", "lte"):
            try:
                left, right = float(actual), float(wanted)
                passed = {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[operator]
            except (TypeError, ValueError):
                passed = False
        else:
            checks.append({"path": path, "operator": operator, "expected": wanted, "actual": actual,
                           "passed": False, "error": "未対応のoperatorです"})
            continue
        checks.append({"path": path, "operator": operator, "expected": wanted, "actual": actual, "passed": passed})
    passed = execution.status == "SUCCEEDED" and all(check["passed"] for check in checks)
    return {
        "passed": passed, "execution_status": execution.status, "checks": redact(checks),
        "summary": {"passed": sum(1 for check in checks if check["passed"]), "total": len(checks)},
    }


async def _complete_test_case(case_id: int, execution_id: int) -> None:
    import asyncio

    for _ in range(3600):
        await asyncio.sleep(0.5)
        with engine.SessionLocal() as db:
            execution = db.get(WorkflowExecution, execution_id)
            row = db.get(WorkflowTestCase, case_id)
            if execution is None or row is None or row.last_execution_id != execution_id:
                return
            if execution.status in ("QUEUED", "RUNNING", "WAITING"):
                continue
            result = _evaluate_test_case(row, execution)
            row.last_result_json = json.dumps(result, ensure_ascii=False, default=str)
            row.last_status = "PASSED" if result["passed"] else "FAILED"
            db.commit()
            return
    with engine.SessionLocal() as db:
        row = db.get(WorkflowTestCase, case_id)
        if row is not None and row.last_execution_id == execution_id:
            row.last_status = "ERROR"
            row.last_result_json = json.dumps({"passed": False, "error": "評価待機がタイムアウトしました"}, ensure_ascii=False)
            db.commit()


async def _start_test_case(workflow_id: int, row: WorkflowTestCase) -> int:
    inputs = json.loads(row.inputs_json or "{}")
    execution_id = await engine.run_workflow(workflow_id, trigger_type=f"test_case:{row.id}", input_data=inputs)
    row.last_execution_id = execution_id
    row.last_status = "RUNNING"
    row.last_result_json = "{}"
    return execution_id


@router.post("/workflows/{workflow_id}/test-cases/{case_id}/run")
async def run_workflow_test_case(
    workflow_id: int,
    case_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    row = db.get(WorkflowTestCase, case_id)
    if row is None or row.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="テストケースが見つかりません")
    try:
        execution_id = await _start_test_case(workflow_id, row)
    except engine.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    import asyncio
    asyncio.create_task(_complete_test_case(case_id, execution_id))
    audit.record(
        db, "workflow.test_case_run", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"test_case_id": case_id, "execution_id": execution_id},
    )
    return {"test_case_id": case_id, "execution_id": execution_id, "status": "RUNNING"}


@router.post("/workflows/{workflow_id}/test-cases/run-batch")
async def run_workflow_test_case_batch(
    workflow_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    rows = db.execute(select(WorkflowTestCase).where(
        WorkflowTestCase.workflow_id == workflow_id,
    ).order_by(WorkflowTestCase.created_at)).scalars().all()
    if not rows:
        raise HTTPException(status_code=422, detail="テストケースがありません")
    started = []
    for row in rows:
        execution_id = await _start_test_case(workflow_id, row)
        started.append({"test_case_id": row.id, "execution_id": execution_id})
    db.commit()
    import asyncio
    for item in started:
        asyncio.create_task(_complete_test_case(item["test_case_id"], item["execution_id"]))
    audit.record(
        db, "workflow.test_case_batch", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"count": len(started), "execution_ids": [item["execution_id"] for item in started]},
    )
    return {"started": started}


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

    execution_ids = select(WorkflowExecution.id).where(WorkflowExecution.workflow_id == workflow_id)
    db.execute(sql_delete(WorkflowTestCase).where(WorkflowTestCase.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowPinnedData).where(WorkflowPinnedData.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowNodeRun).where(WorkflowNodeRun.execution_id.in_(execution_ids)))
    db.execute(sql_delete(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id))
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
        execution_id = await engine.run_workflow(
            workflow_id, trigger_type=trigger_type, input_data=input_data, published_only=True,
        )
    except engine.DefinitionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "workflow.run", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request)
    return {"execution_id": execution_id}


@router.post("/workflows/{workflow_id}/nodes/{node_id}/run-to")
async def run_workflow_to_node(
    workflow_id: int,
    node_id: str,
    request: Request,
    body: RunBody | None = None,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """トリガーから対象ノードまでを実行し、下流の副作用は起動しない。"""
    workflow = _get(db, workflow_id)
    _definition_node(workflow, node_id)
    try:
        execution_id = await engine.run_workflow(
            workflow_id, trigger_type="node_test", input_data=body.input if body else {}, stop_node_id=node_id,
        )
    except engine.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        db, "workflow.run_to_node", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"node_id": node_id},
    )
    return {"execution_id": execution_id}


@router.post("/workflows/{workflow_id}/test")
async def test_workflow(
    workflow_id: int,
    request: Request,
    body: RunBody | None = None,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """Run the saved draft explicitly as a test execution."""
    _get(db, workflow_id)
    try:
        execution_id = await engine.run_workflow(
            workflow_id, trigger_type="test", input_data=body.input if body else {}
        )
    except engine.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        db,
        "workflow.test",
        user=user,
        resource_type="workflow",
        resource_id=str(workflow_id),
        request=request,
    )
    return {"execution_id": execution_id}


@router.post("/workflows/{workflow_id}/enable")
def enable_workflow(
    workflow_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    wf = _get(db, workflow_id)
    published = db.execute(select(WorkflowVersion.id).where(
        WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.published_at.is_not(None),
    ).limit(1)).scalar_one_or_none()
    if published is None:
        raise HTTPException(status_code=409, detail="先にワークフローを公開してください")
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
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    r = db.get(WorkflowExecution, execution_id)
    if r is None:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    raw_context = json.loads(r.context_json or "{}")
    context = redact(raw_context, sensitive_values=collect_sensitive_values(raw_context))
    return {
        "id": r.id,
        "workflow_id": r.workflow_id,
        "status": r.status,
        "trigger_type": r.trigger_type,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error": str(redact(r.error, sensitive_values=collect_sensitive_values(raw_context))),
        "workflow_version_id": r.workflow_version_id,
        "definition_snapshot": json.loads(r.definition_snapshot_json or "{}"),
        "runtime_snapshot": json.loads(r.runtime_snapshot_json or "{}"),
        "input": context.get("__input__", {}),
        "outputs": final_outputs(context),
        "context": {key: value for key, value in context.items() if not key.startswith("__")},
    }


@router.get("/workflows/{workflow_id}/executions/{execution_id}/nodes")
def get_execution_nodes(
    workflow_id: int,
    execution_id: int,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    execution = db.get(WorkflowExecution, execution_id)
    if execution is None or execution.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    rows = db.execute(
        select(WorkflowNodeRun)
        .where(WorkflowNodeRun.execution_id == execution_id)
        .order_by(WorkflowNodeRun.started_at, WorkflowNodeRun.id)
    ).scalars().all()
    return [{
        "id": row.id, "execution_id": row.execution_id, "node_id": row.node_id,
        "node_type": row.node_type, "node_version": row.node_version, "status": row.status,
        "resolved_inputs": json.loads(row.resolved_inputs_json or "{}"),
        "outputs": json.loads(row.outputs_json or "{}"),
        "error": json.loads(row.error_json or "{}"),
        "logs": json.loads(row.logs_json or "[]"), "artifacts": json.loads(row.artifacts_json or "[]"),
        "token_usage": json.loads(row.token_usage_json or "{}"),
        "started_at": row.started_at, "finished_at": row.finished_at, "elapsed_ms": row.elapsed_ms,
        "attempt": row.attempt, "retry_count": row.retry_count,
        "cache_source": row.cache_source, "schema_version": row.schema_version,
    } for row in rows]


class RetryExecutionBody(BaseModel):
    version_mode: str = Field(default="current", pattern="^(current|historical)$")


@router.post("/workflows/{workflow_id}/executions/{execution_id}/retry")
async def retry_execution(
    workflow_id: int,
    execution_id: int,
    body: RetryExecutionBody,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    previous = db.get(WorkflowExecution, execution_id)
    if previous is None or previous.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    context = json.loads(previous.context_json or "{}")
    inputs = context.get("__input__") if isinstance(context.get("__input__"), dict) else {}
    definition = previous.definition_snapshot_json if body.version_mode == "historical" else None
    version_id = previous.workflow_version_id if body.version_mode == "historical" else None
    try:
        new_execution_id = await engine.run_workflow(
            workflow_id, trigger_type=f"retry:{body.version_mode}", input_data=inputs,
            definition_json=definition, workflow_version_id=version_id,
        )
    except engine.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        db, "workflow.retry", user=user, resource_type="workflow_execution",
        resource_id=str(execution_id), request=request,
        metadata={"new_execution_id": new_execution_id, "version_mode": body.version_mode},
    )
    return {"execution_id": new_execution_id, "source_execution_id": execution_id, "version_mode": body.version_mode}


@router.post("/workflows/{workflow_id}/executions/{execution_id}/resume-from/{node_id}")
async def resume_execution_from_node(
    workflow_id: int,
    execution_id: int,
    node_id: str,
    body: RetryExecutionBody,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    workflow = _get(db, workflow_id)
    previous = db.get(WorkflowExecution, execution_id)
    if previous is None or previous.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    definition = previous.definition_snapshot_json if body.version_mode == "historical" else workflow.definition_json
    parsed = json.loads(definition or "{}")
    if not any(str(node.get("id")) == node_id for node in parsed.get("nodes", [])):
        raise HTTPException(status_code=404, detail="再開ノードが対象バージョンにありません")
    previous_context = json.loads(previous.context_json or "{}")
    inputs = previous_context.get("__input__") if isinstance(previous_context.get("__input__"), dict) else {}
    try:
        new_execution_id = await engine.run_workflow(
            workflow_id, trigger_type=f"resume:{body.version_mode}:{node_id}", input_data=inputs,
            definition_json=definition if body.version_mode == "historical" else None,
            workflow_version_id=previous.workflow_version_id if body.version_mode == "historical" else None,
            start_node_id=node_id, seed_context=previous_context,
        )
    except engine.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        db, "workflow.resume_from", user=user, resource_type="workflow_execution",
        resource_id=str(execution_id), request=request,
        metadata={"new_execution_id": new_execution_id, "node_id": node_id, "version_mode": body.version_mode},
    )
    return {
        "execution_id": new_execution_id, "source_execution_id": execution_id,
        "node_id": node_id, "version_mode": body.version_mode,
    }


def _final_outputs(context: dict) -> dict[str, dict]:
    """後方互換用。新規コードはcontracts.final_outputsを利用する。"""
    return final_outputs(context)


@router.post("/workflows/{workflow_id}/executions/{execution_id}/load-inputs")
def load_execution_inputs(
    workflow_id: int,
    execution_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """Load redacted trigger inputs from a past execution into the preview form."""
    _get(db, workflow_id)
    execution = db.get(WorkflowExecution, execution_id)
    if execution is None or execution.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    raw_context = json.loads(execution.context_json or "{}")
    context = redact(raw_context, sensitive_values=collect_sensitive_values(raw_context))
    return {"execution_id": execution.id, "input": context.get("__input__", {})}


@router.get("/workflow-executions/{execution_id}/live")
def get_execution_live(
    execution_id: int,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    """実行中コンテキストのライブ参照（3 秒フラッシュを待たずに現在の状態を返す）。"""
    r = db.get(WorkflowExecution, execution_id)
    if r is None:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    live = engine.live_context(execution_id)
    raw_context = live if live is not None else json.loads(r.context_json or "{}")
    sensitive_values = collect_sensitive_values(raw_context)
    if live is not None:
        sensitive_values.update(str(value) for value in (live.get("__secrets__") or {}).values() if value)
    context = {k: v for k, v in raw_context.items() if not k.startswith("__")}
    context = redact(context, sensitive_values=sensitive_values)
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
        "error": str(redact(r.error, sensitive_values=sensitive_values)),
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
    details = engine.approval_details(execution_id, body.node_id)
    if details is None:
        raise HTTPException(status_code=409, detail="このノードは承認待ちではありません")
    approver = str(details.get("approver") or "").strip()
    if approver and approver != user.username:
        raise HTTPException(status_code=403, detail=f"この承認はユーザー '{approver}' に割り当てられています")
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
