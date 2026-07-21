from __future__ import annotations

import hashlib
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import (
    ApplicationProject, User, Workflow, WorkflowArtifact, WorkflowBusinessEvent, WorkflowCacheEntry, WorkflowEventDelivery, WorkflowExecution, WorkflowExecutionEvent, WorkflowNodeRun, WorkflowPause, WorkflowPinnedData, WorkflowQueueItem, WorkflowSecret, WorkflowStateEntry, WorkflowTestCase,
    WorkflowVersion, utcnow,
)
from app.security.crypto import encrypt_text
from app.security.deps import require_permission, require_permissions
from app.workflows import engine
from app.workflows.contracts import build_input_schema, build_output_schema, final_outputs
from app.workflows.publish_validation import check_publishability
from app.workflows.redaction import collect_sensitive_values, redact
from app.workflows import events as execution_events
from app.workflows import artifacts as workflow_artifacts

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
    expected_updated_at: datetime | None = None


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
    if body.expected_updated_at is not None:
        expected = body.expected_updated_at
        current = wf.updated_at
        if expected.tzinfo is not None:
            expected = expected.astimezone(timezone.utc).replace(tzinfo=None)
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        if current != expected:
            raise HTTPException(status_code=409, detail={
                "code": "WORKFLOW_CONFLICT",
                "message": "別の画面でワークフローが更新されました",
                "updated_at": wf.updated_at.isoformat(),
            })
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
    version, _ = _ensure_current_published(db, workflow, definition)
    db.commit()
    audit.record(
        db, "workflow.publish", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"version_id": version.id, "version": version.version, "warnings": len(check["warnings"])},
    )
    return {
        "workflow_id": workflow_id, "version_id": version.id, "version": version.version,
        "published_at": version.published_at, "warnings": check["warnings"], "quality": check["quality"],
    }


def _ensure_current_published(
    db: Session, workflow: Workflow, definition: dict[str, Any],
) -> tuple[WorkflowVersion, bool]:
    """現在のdraftと同じ版が最新公開でなければ、その版だけを公開する。"""
    version = engine._ensure_execution_version(db, workflow)
    latest = db.execute(
        select(WorkflowVersion)
        .where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.published_at.is_not(None),
        )
        .order_by(WorkflowVersion.published_at.desc(), WorkflowVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    published = latest is not None and latest.id == version.id
    if not published:
        version.input_schema_json = json.dumps(build_input_schema(definition), ensure_ascii=False)
        version.output_schema_json = json.dumps(build_output_schema(definition), ensure_ascii=False)
        version.published_at = utcnow()
        version.note = "公開版"
    return version, not published


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


class WorkflowIntelligenceDiagnoseBody(BaseModel):
    execution_id: int | None = None
    instruction: str = Field(default="", max_length=4000)
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=256)
    api_key: str = Field(default="", max_length=8192)
    use_ai: bool = True


class WorkflowOperationPatchBody(BaseModel):
    patch_version: int = 1
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    expected_updated_at: datetime | None = None


def _assert_workflow_timestamp(workflow: Workflow, expected: datetime | None) -> None:
    if expected is None:
        return
    current = workflow.updated_at
    if expected.tzinfo is not None:
        expected = expected.astimezone(timezone.utc).replace(tzinfo=None)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    if current != expected:
        raise HTTPException(status_code=409, detail={
            "code": "WORKFLOW_CONFLICT", "message": "別の画面でワークフローが更新されました",
            "updated_at": workflow.updated_at.isoformat(),
        })


def _patch_preview_out(preview: dict[str, Any], *, include_definition: bool = True) -> dict[str, Any]:
    if include_definition:
        return preview
    return {key: value for key, value in preview.items() if key != "patched_definition"}


def _normalize_ai_operations(raw: Any) -> Any:
    """Normalize one common local-model shape before canonical validation.

    The public patch contract remains strict: update_node cannot replace config.
    Local models frequently nest config changes there, so split only that exact
    shape into set_config operations; the canonical validator still checks every
    node, key, value, size and secret boundary afterwards.
    """
    if not isinstance(raw, list):
        return raw
    normalized: list[Any] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("op") != "update_node" or not isinstance(item.get("changes"), dict):
            normalized.append(item)
            continue
        changes = dict(item["changes"])
        config = changes.pop("config", None)
        if isinstance(config, dict):
            normalized.extend({
                "op": "set_config", "node_id": item.get("node_id"), "key": str(key), "value": value,
            } for key, value in config.items())
        if changes:
            normalized.append({"op": "update_node", "node_id": item.get("node_id"), "changes": changes})
        elif not isinstance(config, dict):
            normalized.append(item)
    return normalized


@router.get("/workflows/{workflow_id}/intelligence")
async def get_workflow_intelligence(
    workflow_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    from app.workflows.intelligence import project_intelligence
    from app.workflows.runtime_route import runtime_snapshot

    report = project_intelligence(db, _get(db, workflow_id))
    try:
        report["runtime"] = await runtime_snapshot()
    except Exception:
        logger.exception("runtime snapshot failed for workflow intelligence: workflow=%s", workflow_id)
        report["runtime"] = {"gpu": {"name": None, "vram_total_bytes": None, "vram_used_bytes": None, "vram_free_bytes": None}, "providers": [], "models": [], "available": False}
    return report


@router.post("/workflows/{workflow_id}/intelligence/diagnose")
async def diagnose_workflow(
    workflow_id: int,
    body: WorkflowIntelligenceDiagnoseBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    import time

    from app.workflows.chat_router import _extract_json, _llm
    from app.workflows.intelligence import WorkflowPatchError, deterministic_diagnosis, preview_patch

    workflow = _get(db, workflow_id)
    definition = json.loads(workflow.definition_json or "{}")
    if body.execution_id is not None:
        execution = db.get(WorkflowExecution, body.execution_id)
        if execution is None or execution.workflow_id != workflow_id:
            raise HTTPException(status_code=404, detail="指定実行が見つかりません")
    else:
        execution = db.execute(select(WorkflowExecution).where(
            WorkflowExecution.workflow_id == workflow_id,
        ).order_by(
            (WorkflowExecution.status.in_(["FAILED", "TIMED_OUT"])).desc(),
            WorkflowExecution.started_at.desc(),
        ).limit(1)).scalar_one_or_none()
    fallback = deterministic_diagnosis(definition, execution)
    diagnosis = fallback
    source = "deterministic"
    fallback_reason: str | None = None
    discarded_options: list[str] = []
    elapsed_ms = 0
    provider_type = "none"
    if body.use_ai and body.base_url.strip() and body.model.strip():
        from app.models_mgmt.providers import list_providers

        providers = await list_providers(include_unavailable=True)
        endpoint = next((item for item in providers if str(item.get("base_url") or "").rstrip("/") == body.base_url.rstrip("/")), None)
        if endpoint is None or body.model not in list(endpoint.get("models") or []):
            raise HTTPException(status_code=422, detail="登録済みLLM endpointとmodelを選択してください")
        provider_type = str(endpoint.get("provider") or "unknown")
        sensitive = collect_sensitive_values(definition)
        execution_payload: dict[str, Any] | None = None
        if execution is not None:
            try:
                context = json.loads(execution.context_json or "{}")
            except json.JSONDecodeError:
                context = {}
            failed = {
                str(node_id): redact(entry, sensitive_values=sensitive)
                for node_id, entry in context.items()
                if isinstance(entry, dict) and entry.get("status") in {"FAILED", "TIMED_OUT"}
            }
            try:
                runtime = json.loads(execution.runtime_snapshot_json or "{}")
            except json.JSONDecodeError:
                runtime = {}
            execution_payload = {
                "id": execution.id, "status": execution.status,
                "error": redact(execution.error, sensitive_values=sensitive),
                "failed_nodes": failed, "runtime": redact(runtime, sensitive_values=sensitive),
            }
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "cause": {"type": "string", "maxLength": 4000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "failed_node_id": {"type": ["string", "null"]},
                "options": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"title": {"type": "string"}, "impact": {"type": "string"}, "operations": {"type": "array", "maxItems": 100, "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "op": {"type": "string", "enum": ["set_config", "update_node", "add_node", "remove_node", "add_edge", "remove_edge"]},
                            "node_id": {"type": "string"}, "key": {"type": "string"}, "value": {},
                            "changes": {"type": "object"}, "node": {"type": "object"}, "edge": {"type": "object"},
                            "source": {"type": "string"}, "target": {"type": "string"}, "branch": {"type": "string"},
                        }, "required": ["op"],
                    }}},
                    "required": ["title", "impact", "operations"],
                }},
            }, "required": ["cause", "confidence", "options"],
        }
        payload = {
            "instruction": body.instruction or "失敗原因を診断し、安全で小さい修正案を最大3件提案してください",
            "workflow": engine.safe_definition_snapshot(definition), "execution": execution_payload,
            "operation_contract": {"patch_version": 1, "allowed": ["set_config", "update_node", "add_node", "remove_node", "add_edge", "remove_edge"]},
        }
        started = time.perf_counter()
        ai_stage = "generation"
        try:
            content = await _llm(
                [{"role": "system", "content": "Workflow診断者です。秘密値を出力せず、指定JSON Schemaだけで回答してください。set_config操作はop,node_id,key,value、update_nodeはop,node_id,changes、add_nodeはop,node、remove_nodeはop,node_id、add_edgeはop,edge、remove_edgeはop,source,targetを必ず含めます。小さく安全な変更を優先してください。"},
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                body.base_url, body.model, body.api_key or "sk-no-key", temperature=0.1,
                max_tokens=2048, disable_thinking=True,
                response_format={"type": "json_schema", "json_schema": {"name": "workflow_diagnosis", "schema": schema, "strict": True}},
                timeout_seconds=180,
            )
            ai_stage = "response_parse"
            candidate = _extract_json(content)
            ai_stage = "operation_validation"
            options = []
            raw_diagnosis = candidate.get("diagnosis")
            raw_options = candidate.get("options")
            # Some local OpenAI-compatible runtimes honor JSON mode but flatten a
            # single proposal to {diagnosis, operations}. Only the versioned
            # operations are normalized here; they still pass the same validator.
            if not isinstance(raw_options, list) and isinstance(candidate.get("operations"), list):
                raw_options = [{
                    "title": candidate.get("title") or "AI修正案",
                    "impact": candidate.get("impact") or "選択した操作だけを適用します",
                    "operations": candidate["operations"],
                }]
            for option in list(raw_options or [])[:3]:
                if not isinstance(option, dict):
                    continue
                try:
                    preview = preview_patch(definition, _normalize_ai_operations(option.get("operations") or []))
                except WorkflowPatchError as exc:
                    discarded_options.append(str(exc)[:300])
                    continue
                options.append({
                    "title": str(option.get("title") or "修正案")[:200],
                    "impact": str(option.get("impact") or "")[:2000],
                    "operations": preview["operations"], "preview": _patch_preview_out(preview, include_definition=False),
                })
            if not options:
                discarded_options.append(
                    f"response keys={','.join(sorted(str(key) for key in candidate)[:12])}; options shape={type(raw_options).__name__}, count={len(raw_options) if isinstance(raw_options, (list, dict, str)) else 0}"
                )
                raise ValueError("valid operation option is empty")
            diagnosis = {
                "cause": str(candidate.get("cause") or (
                    raw_diagnosis.get("cause") or raw_diagnosis.get("root_cause") or raw_diagnosis.get("summary")
                    if isinstance(raw_diagnosis, dict) else raw_diagnosis
                ) or fallback["cause"])[:4000],
                "confidence": max(0.0, min(float(candidate.get("confidence") or (
                    raw_diagnosis.get("confidence") if isinstance(raw_diagnosis, dict) else 0
                ) or 0), 1.0)),
                "failed_node_id": candidate.get("failed_node_id") or (
                    raw_diagnosis.get("failed_node_id") if isinstance(raw_diagnosis, dict) else None
                ), "options": options,
            }
            source = "ai"
        except Exception as exc:
            logger.warning("workflow AI diagnosis fell back: workflow=%s stage=%s reason=%s", workflow_id, ai_stage, type(exc).__name__)
            fallback_reason = f"{ai_stage}:{type(exc).__name__}"
        elapsed_ms = round((time.perf_counter() - started) * 1000)
    if source == "deterministic":
        for option in diagnosis.get("options", []):
            try:
                option["preview"] = _patch_preview_out(preview_patch(definition, option.get("operations") or []), include_definition=False)
            except WorkflowPatchError:
                option["preview"] = {"valid": False, "errors": ["修正案を適用できません"]}
    audit.record(
        db, "workflow.ai_diagnose", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request,
        metadata={"execution_id": execution.id if execution else None, "source": source, "provider": provider_type, "model": body.model if source == "ai" else "", "option_count": len(diagnosis.get("options") or [])},
    )
    return {**diagnosis, "source": source, "fallback_reason": fallback_reason, "discarded_options": discarded_options, "evaluation": {"model": body.model if source == "ai" else None, "provider": provider_type, "temperature": 0.1, "elapsed_ms": elapsed_ms}}


@router.post("/workflows/{workflow_id}/intelligence/patch-preview")
def preview_workflow_intelligence_patch(
    workflow_id: int,
    body: WorkflowOperationPatchBody,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    from app.workflows.intelligence import PATCH_VERSION, WorkflowPatchError, preview_patch

    workflow = _get(db, workflow_id)
    if body.patch_version != PATCH_VERSION:
        raise HTTPException(status_code=422, detail=f"patch_version {body.patch_version} は未対応です")
    try:
        preview = preview_patch(json.loads(workflow.definition_json or "{}"), body.operations)
    except WorkflowPatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"base_updated_at": workflow.updated_at, **preview}


@router.post("/workflows/{workflow_id}/intelligence/patch-apply")
def apply_workflow_intelligence_patch(
    workflow_id: int,
    body: WorkflowOperationPatchBody,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    from app.workflows.intelligence import PATCH_VERSION, WorkflowPatchError, preview_patch

    workflow = _get(db, workflow_id)
    _assert_workflow_timestamp(workflow, body.expected_updated_at)
    if body.patch_version != PATCH_VERSION:
        raise HTTPException(status_code=422, detail=f"patch_version {body.patch_version} は未対応です")
    before_checksum = hashlib.sha256((workflow.definition_json or "{}").encode()).hexdigest()
    try:
        preview = preview_patch(json.loads(workflow.definition_json or "{}"), body.operations)
    except WorkflowPatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not preview["valid"]:
        raise HTTPException(status_code=422, detail={"errors": preview["errors"], "warnings": preview["warnings"]})
    _snapshot_version(db, workflow, note="AI修正適用前")
    workflow.definition_json = json.dumps(preview["patched_definition"], ensure_ascii=False)
    db.commit()
    db.refresh(workflow)
    result_checksum = hashlib.sha256(workflow.definition_json.encode()).hexdigest()
    audit.record(
        db, "workflow.ai_patch_apply", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request,
        metadata={"patch_version": body.patch_version, "operation_count": len(body.operations), "json_patch_count": len(preview["json_patch"]), "base_checksum": before_checksum, "result_checksum": result_checksum},
    )
    return {"workflow": _out(workflow, db), "patch": _patch_preview_out(preview, include_definition=False)}


@router.post("/workflows/{workflow_id}/intelligence/auto-tests")
def generate_workflow_intelligence_tests(
    workflow_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    from app.workflows.intelligence import suggested_tests

    workflow = _get(db, workflow_id)
    generated = []
    for item in suggested_tests(json.loads(workflow.definition_json or "{}")):
        existing = db.execute(select(WorkflowTestCase).where(
            WorkflowTestCase.workflow_id == workflow_id, WorkflowTestCase.name == item["name"],
        )).scalar_one_or_none()
        if existing is None:
            existing = WorkflowTestCase(workflow_id=workflow_id, name=item["name"])
            _set_test_case(existing, WorkflowTestCaseBody(**{key: item[key] for key in ("name", "inputs", "mocks", "expected_outputs", "assertions")}))
            db.add(existing)
            db.flush()
        generated.append(existing)
    db.commit()
    audit.record(
        db, "workflow.ai_test_generate", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request,
        metadata={"test_case_ids": [item.id for item in generated]},
    )
    return {"test_cases": [_test_case_out(item) for item in generated]}


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

    linked_projects = db.execute(select(ApplicationProject).where(
        ApplicationProject.workflow_id == workflow_id,
    )).scalars().all()
    if linked_projects:
        raise HTTPException(
            status_code=409,
            detail=f"Application Projectが{len(linked_projects)}件接続されています。先にProjectを削除してください",
        )

    execution_ids = select(WorkflowExecution.id).where(WorkflowExecution.workflow_id == workflow_id)
    business_event_ids = select(WorkflowBusinessEvent.id).where(
        WorkflowBusinessEvent.source_workflow_id == workflow_id,
    )
    stored_artifacts = db.execute(select(WorkflowArtifact).where(
        WorkflowArtifact.execution_id.in_(execution_ids),
    )).scalars().all()
    db.execute(sql_delete(WorkflowTestCase).where(WorkflowTestCase.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowPinnedData).where(WorkflowPinnedData.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowQueueItem).where(WorkflowQueueItem.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowCacheEntry).where(WorkflowCacheEntry.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowStateEntry).where(WorkflowStateEntry.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowEventDelivery).where(
        (WorkflowEventDelivery.business_event_id.in_(business_event_ids)) |
        (WorkflowEventDelivery.target_workflow_id == workflow_id)
    ))
    db.execute(sql_delete(WorkflowBusinessEvent).where(
        WorkflowBusinessEvent.source_workflow_id == workflow_id,
    ))
    db.execute(sql_delete(WorkflowArtifact).where(WorkflowArtifact.execution_id.in_(execution_ids)))
    db.execute(sql_delete(WorkflowPause).where(WorkflowPause.execution_id.in_(execution_ids)))
    db.execute(sql_delete(WorkflowExecutionEvent).where(WorkflowExecutionEvent.execution_id.in_(execution_ids)))
    db.execute(sql_delete(WorkflowNodeRun).where(WorkflowNodeRun.execution_id.in_(execution_ids)))
    db.execute(sql_delete(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id))
    db.execute(sql_delete(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id))
    db.delete(wf)
    db.commit()
    for artifact in stored_artifacts:
        workflow_artifacts.remove_artifact_file(artifact)
    audit.record(db, "workflow.delete", user=user, resource_type="workflow", resource_id=str(workflow_id), request=request, metadata={"name": name})
    return {"ok": True}


class RunBody(BaseModel):
    input: dict = Field(default_factory=dict)


@router.post("/workflows/{workflow_id}/validate-publish-run")
async def validate_publish_run(
    workflow_id: int,
    request: Request,
    body: RunBody | None = None,
    user: User = Depends(require_permissions("workflows.edit", "workflows.run")),
    db: Session = Depends(get_db),
):
    """現在の保存済みdraftを検証し、必要時だけ公開して、その固定版を実行する。"""
    workflow = _get(db, workflow_id)
    try:
        definition = json.loads(workflow.definition_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail={
            "publishable": False, "blocking": [f"定義JSONが不正です: {exc}"],
            "warnings": [], "quality": {"score": 0, "label": "invalid"},
        }) from exc
    check = check_publishability(db, workflow, definition)
    if not check["publishable"]:
        raise HTTPException(status_code=409, detail=check)
    version, published = _ensure_current_published(db, workflow, definition)
    db.commit()

    input_data = body.input if body else {}
    trigger_type = "chat" if input_data.get("message") else "manual"
    try:
        execution_id = await engine.run_workflow(
            workflow_id,
            trigger_type=trigger_type,
            input_data=input_data,
            definition_json=version.definition_json,
            workflow_version_id=version.id,
        )
    except engine.DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        db, "workflow.validate_publish_run", user=user, resource_type="workflow",
        resource_id=str(workflow_id), request=request,
        metadata={"version_id": version.id, "version": version.version, "published": published,
                  "warnings": len(check["warnings"])},
    )
    return {
        "execution_id": execution_id, "version_id": version.id, "version": version.version,
        "published": published, "warnings": check["warnings"], "quality": check["quality"],
    }


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


@router.get("/workflow-executions/{execution_id}/events")
def get_execution_events(
    execution_id: int,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    """Return an ordered replay window for reconnecting debuggers."""
    try:
        return execution_events.replay(db, execution_id, after_sequence, limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="実行が見つかりません") from exc


@router.get("/workflow-executions/{execution_id}/stream")
async def stream_execution_events(
    execution_id: int,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    """Authenticated SSE stream with durable sequence replay and heartbeats."""
    execution = db.get(WorkflowExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlparse

        if urlparse(origin).netloc != request.headers.get("host", ""):
            raise HTTPException(status_code=403, detail="同一オリジンから接続してください")
    last_event_id = request.headers.get("last-event-id", "")
    try:
        cursor = max(after_sequence, int(last_event_id)) if last_event_id else after_sequence
    except ValueError:
        cursor = after_sequence
    # StreamingResponseの寿命中にdependency Session/transactionを保持しない。
    db.close()

    async def generate():
        nonlocal cursor
        heartbeat_at = asyncio.get_running_loop().time()
        yield "retry: 1000\n\n"
        while not await request.is_disconnected():
            with engine.SessionLocal() as stream_db:
                try:
                    batch = execution_events.replay(stream_db, execution_id, cursor)
                    current = stream_db.get(WorkflowExecution, execution_id)
                except LookupError:
                    return
            if batch["reset_required"]:
                cursor = int(batch["latest_sequence"])
                reset = json.dumps({
                    "execution_id": execution_id, "sequence": cursor, "type": "stream.reset", "node_id": None,
                    "timestamp": utcnow(),
                    "payload": {"latest_sequence": cursor},
                }, ensure_ascii=False, default=str)
                yield f"id: {cursor}\nevent: workflow\ndata: {reset}\n\n"
            else:
                for event in batch["events"]:
                    cursor = int(event["sequence"])
                    data = json.dumps(event, ensure_ascii=False, default=str)
                    yield f"id: {cursor}\nevent: workflow\ndata: {data}\n\n"
            terminal = current is None or current.status not in ("QUEUED", "RUNNING", "WAITING")
            if terminal and cursor >= int(batch["latest_sequence"]):
                closed = json.dumps({
                    "execution_id": execution_id, "sequence": cursor, "type": "stream.closed", "node_id": None,
                    "timestamp": utcnow(),
                    "payload": {"status": current.status if current else "DELETED"},
                }, ensure_ascii=False, default=str)
                yield f"id: {cursor}\nevent: workflow\ndata: {closed}\n\n"
                return
            now = asyncio.get_running_loop().time()
            if now - heartbeat_at >= 15:
                yield ": heartbeat\n\n"
                heartbeat_at = now
            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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
        "input_size": len((row.resolved_inputs_json or "{}").encode("utf-8")),
        "output_size": len((row.outputs_json or "{}").encode("utf-8")),
        "cache_source": row.cache_source, "schema_version": row.schema_version,
    } for row in rows]


@router.get("/workflows/{workflow_id}/executions/{execution_id}/artifacts")
def list_execution_artifacts(
    workflow_id: int,
    execution_id: int,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    _get(db, workflow_id)
    execution = db.get(WorkflowExecution, execution_id)
    if execution is None or execution.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="実行が見つかりません")
    rows = db.execute(
        select(WorkflowArtifact)
        .where(WorkflowArtifact.execution_id == execution_id)
        .order_by(WorkflowArtifact.created_at, WorkflowArtifact.id)
    ).scalars().all()
    return [workflow_artifacts.reference(row) | {
        "execution_id": row.execution_id,
        "node_run_id": row.node_run_id,
        "node_id": row.node_id,
        "created_at": row.created_at,
        "downloadable": not row.sensitive,
    } for row in rows]


@router.get("/workflow-artifacts/{artifact_id}/download")
def download_workflow_artifact(
    artifact_id: int,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    artifact = db.get(WorkflowArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="成果物が見つかりません")
    if artifact.sensitive:
        raise HTTPException(status_code=403, detail="機密成果物はダウンロードできません")
    try:
        path = workflow_artifacts.artifact_path(artifact)
    except workflow_artifacts.WorkflowArtifactError as exc:
        logger.warning("workflow artifact validation failed: artifact=%s error=%s", artifact_id, exc)
        raise HTTPException(status_code=404, detail="成果物が見つからないか、整合性を確認できません") from exc
    audit.record(
        db, "workflow.artifact_download", user=user,
        resource_type="workflow_artifact", resource_id=str(artifact.id), request=request,
        metadata={"execution_id": artifact.execution_id, "node_id": artifact.node_id},
    )
    return FileResponse(
        path,
        media_type=artifact.mime_type,
        filename=artifact.filename,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


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
    response: dict[str, Any] = Field(default_factory=dict)


@router.post("/workflow-executions/{execution_id}/approve")
async def approve_execution_node(
    execution_id: int,
    body: ApproveBody,
    request: Request,
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """待機中のhuman interactionを解決して実行を再開する。"""
    details = engine.approval_details(execution_id, body.node_id)
    if details is None:
        raise HTTPException(status_code=409, detail="このノードは入力待ちではありません")
    interaction_type = str(details.get("interaction_type") or "approval")
    approver = str(details.get("approver") or "").strip()
    if approver and approver != user.username:
        raise HTTPException(status_code=403, detail=f"この操作はユーザー '{approver}' に割り当てられています")
    try:
        resolved = await engine.resolve_approval(execution_id, body.node_id, body.approve, body.response)
    except engine.PauseResponseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not resolved:
        raise HTTPException(status_code=409, detail="このノードは入力待ちではありません")
    action = (
        ("workflow.form_submit" if body.approve else "workflow.form_cancel")
        if interaction_type == "form" else
        ("workflow.approve" if body.approve else "workflow.reject")
    )
    audit.record(db, action, user=user,
                 resource_type="workflow_execution", resource_id=str(execution_id),
                 request=request, metadata={"node_id": body.node_id})
    return {"ok": True, "approved": body.approve, "interaction_type": interaction_type}


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
