"""ワークフロー実行エンジンとスケジューラー（v2: 並列 DAG 実行）。

v2 の実行モデル:
- ノードは「最初の生きた入力」で発火（従来互換）。config.join=="all" で全入力待ち合流。
- 分岐で選ばれなかった経路には dead 信号を伝播し、合流ノードの待ちを解決する。
- 独立した枝は並列実行（同時実行ノード数は MAX_PARALLEL_NODES で制限）。
- ノード共通設定: retry_count / retry_wait / node_timeout / on_error(stop|continue|branch) /
  require_approval(実行前承認) / join。失敗はerror、時間切れはtimeout edgeへ分岐する。
- 実行中コンテキストは _live からライブ参照でき、定期的に DB へフラッシュされる。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import platform
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from jsonschema import Draft202012Validator, SchemaError

from app.database import SessionLocal
from app.models import (
    Workflow, WorkflowExecution, WorkflowNodeRun, WorkflowPause, WorkflowSecret, WorkflowVersion, utcnow,
)
from app.workflows.nodes import (
    DEFAULT_NODE_TIMEOUT,
    NODE_EXECUTORS,
    NODE_TIMEOUTS,
    NodeError,
    render_template,
)
from app.workflows.contracts import build_fields_schema
from app.workflows.redaction import collect_sensitive_values, is_sensitive_key, redact
from app.workflows import events as execution_events
from app.workflows import artifacts as workflow_artifacts

logger = logging.getLogger("control_deck.workflows")

MAX_STEPS = 300
MAX_PARALLEL_NODES = 4
EXECUTION_TIMEOUT = 3600 * 2
APPROVAL_TIMEOUT = 86400  # 承認待ちの上限（秒）
DELAY_MAX_SECONDS = 7 * 86400
MAX_SUBFLOW_DEPTH = 3

# セマフォを持たずに実行するノード（待機・サブフロー等。枠を長時間占有させない）
_UNMETERED = {"util.wait", "control.delay", "flow.call", "trigger"}

# 実行中タスク（キャンセル用）と、ライブ参照用のコンテキスト
_running: dict[int, asyncio.Task] = {}
_live: dict[int, dict] = {}
def _emit_event(
    execution_id: int, event_type: str, *, node_id: str | None = None,
    payload: dict[str, Any] | None = None, sensitive_values: set[str] | None = None,
) -> None:
    """Observability must not make the workflow itself fail."""
    try:
        execution_events.append_event(
            execution_id, event_type, node_id=node_id, payload=payload,
            sensitive_values=sensitive_values,
        )
    except Exception:
        logger.exception("workflow event persistence failed: execution=%s type=%s", execution_id, event_type)


class DefinitionError(ValueError):
    pass


class WorkflowSuspended(Exception):
    """正常なdurable checkpoint。失敗としてexecutionを終了させない。"""

    def __init__(self, node_id: str):
        super().__init__(f"workflow paused at {node_id}")
        self.node_id = node_id


class PauseResponseError(ValueError):
    pass


class ArtifactPersistenceError(RuntimeError):
    """Executorの副作用後にartifact保存だけが失敗したことを示す。再試行しない。"""



def parse_definition(definition_json: str) -> tuple[list[dict], list[dict]]:
    try:
        d = json.loads(definition_json or "{}")
    except json.JSONDecodeError as e:
        raise DefinitionError(f"定義が不正な JSON です: {e}")
    nodes = d.get("nodes", [])
    edges = d.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise DefinitionError("nodes / edges は配列である必要があります")
    return nodes, edges


def validate_definition(definition_json: str) -> None:
    nodes, edges = parse_definition(definition_json)
    try:
        raw_definition = json.loads(definition_json or "{}")
    except json.JSONDecodeError as exc:
        raise DefinitionError(f"定義が不正な JSON です: {exc}") from exc
    ids = set()
    triggers = 0
    for n in nodes:
        nid = n.get("id")
        ntype = n.get("type")
        if not nid or nid in ids:
            raise DefinitionError(f"ノード ID が重複または欠落しています: {nid}")
        ids.add(nid)
        if ntype == "trigger":
            triggers += 1
        elif ntype == "control.loop":
            pass  # エンジンが直接処理する制御ノード
        elif ntype not in NODE_EXECUTORS:
            raise DefinitionError(f"未知のノード種類: {ntype}")
    if nodes and triggers != 1:
        raise DefinitionError("トリガーノードは 1 つ必要です")
    for e in edges:
        if e.get("source") not in ids or e.get("target") not in ids:
            raise DefinitionError("エッジの参照先ノードが存在しません")
    return_ids = {str(node.get("id")) for node in nodes if node.get("type") == "flow.return"}
    if any(str(edge.get("source")) in return_ids for edge in edges):
        raise DefinitionError("flow.returnは終端専用です。後続エッジを接続できません")
    groups = raw_definition.get("groups", [])
    if not isinstance(groups, list):
        raise DefinitionError("groups は配列である必要があります")
    group_ids: set[str] = set()
    grouped_nodes: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or not str(group.get("id") or "").strip():
            raise DefinitionError("グループIDが欠落しています")
        group_id = str(group["id"])
        if group_id in group_ids:
            raise DefinitionError(f"グループIDが重複しています: {group_id}")
        group_ids.add(group_id)
        node_ids = group.get("node_ids", [])
        if not isinstance(node_ids, list) or not all(isinstance(node_id, str) for node_id in node_ids):
            raise DefinitionError(f"グループ {group_id} の node_ids は文字列配列である必要があります")
        if any(node_id not in ids for node_id in node_ids):
            raise DefinitionError(f"グループ {group_id} が存在しないノードを参照しています")
        duplicate = grouped_nodes.intersection(node_ids)
        if duplicate:
            raise DefinitionError(f"ノードは複数グループへ所属できません: {sorted(duplicate)[0]}")
        grouped_nodes.update(node_ids)


def _edge_branch(e: dict) -> str | None:
    return e.get("source_handle") or e.get("branch") or e.get("sourceHandle") or None


# ---- ライブ状況・承認 ----


def live_context(execution_id: int) -> dict | None:
    """実行中コンテキストのライブ参照（終了後は None → DB を見る）。"""
    return _live.get(execution_id)


def pending_approvals(execution_id: int) -> list[dict[str, Any]]:
    """UI/API共通のDB-backed human interaction contract。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(WorkflowPause).where(
                WorkflowPause.execution_id == execution_id,
                WorkflowPause.pause_type.in_(("approval", "form")),
                WorkflowPause.status == "PENDING",
            ).order_by(WorkflowPause.created_at, WorkflowPause.id)
        ).scalars().all()
        return [{
            "pause_id": row.id,
            "node_id": row.node_id,
            **({"interaction_type": "form"} if row.pause_type == "form" else {}),
            "message": row.message or ("入力が必要です" if row.pause_type == "form" else "承認が必要です"),
            "approver": row.approver,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "form_schema": json.loads(row.form_schema_json or "{}"),
        } for row in rows]


def approval_details(execution_id: int, node_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.execute(
            select(WorkflowPause).where(
                WorkflowPause.execution_id == execution_id,
                WorkflowPause.node_id == node_id,
                WorkflowPause.pause_type.in_(("approval", "form")),
                WorkflowPause.status == "PENDING",
            ).order_by(WorkflowPause.id.desc()).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "pause_id": row.id, "node_id": row.node_id, "interaction_type": row.pause_type,
            "message": row.message,
            "approver": row.approver,
            "expires_at": row.expires_at.isoformat(),
            "form_schema": json.loads(row.form_schema_json or "{}"),
        }


async def resolve_approval(
    execution_id: int, node_id: str, approve: bool, response: dict[str, Any] | None = None,
) -> bool:
    """Pending pauseを一度だけ解決し、同じexecution snapshotを継続する。"""
    response = response or {}
    with SessionLocal() as db:
        row = db.execute(
            select(WorkflowPause).where(
                WorkflowPause.execution_id == execution_id,
                WorkflowPause.node_id == node_id,
                WorkflowPause.pause_type.in_(("approval", "form")),
                WorkflowPause.status == "PENDING",
            ).order_by(WorkflowPause.id.desc()).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return False
        try:
            schema = json.loads(row.form_schema_json or "{}")
            Draft202012Validator.check_schema(schema)
        except (json.JSONDecodeError, SchemaError) as exc:
            raise PauseResponseError("入力フォームのschemaが不正です") from exc
        # Reject/cancelでは入力値を利用・保存しない。必須項目が空でも操作でき、
        # 未完成のフォーム値をcheckpointへ残さない。
        if not approve:
            response = {}
        errors = sorted(Draft202012Validator(schema).iter_errors(response), key=lambda item: list(item.path)) if approve else []
        if errors:
            path = ".".join(str(part) for part in errors[0].path)
            raise PauseResponseError(f"入力がschemaに一致しません: {path or 'response'}: {errors[0].message}")
        safe_response = redact(response, sensitive_values=collect_sensitive_values(response))
        serialized = json.dumps(safe_response, ensure_ascii=False, default=str)
        if len(serialized.encode()) > 64_000:
            raise PauseResponseError("入力は64KB以内にしてください")
        row.status = "APPROVED" if approve else "REJECTED"
        row.response_json = serialized
        row.resumed_at = utcnow()
        execution = db.get(WorkflowExecution, execution_id)
        if execution is None or execution.status not in ("WAITING", "RUNNING"):
            db.rollback()
            return False
        execution.status = "RUNNING"
        db.commit()
        pause_id = row.id
        pause_type = row.pause_type
    _emit_event(
        execution_id, "execution.resumed", node_id=node_id,
        payload={
            "pause_id": pause_id,
            "interaction_type": pause_type,
            "decision": (
                "submitted" if pause_type == "form" and approve else
                ("canceled" if pause_type == "form" else ("approved" if approve else "rejected"))
            ),
        },
    )
    await _resume_pause(pause_id)
    return True


def _set_exec_status(execution_id: int, status: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(WorkflowExecution, execution_id)
        if row is not None and row.status in ("RUNNING", "WAITING") and row.status != status:
            row.status = status
            db.commit()
            _emit_event(execution_id, "execution.status", payload={"status": status})
    finally:
        db.close()

def _load_secrets() -> dict[str, str]:
    """{{secrets.名前}} 用。暗号化ストアから全シークレットを復号して返す。"""
    from app.models import WorkflowSecret
    from app.security.crypto import decrypt_text

    db = SessionLocal()
    try:
        out: dict[str, str] = {}
        for s in db.query(WorkflowSecret).all():
            try:
                out[s.name] = decrypt_text(s.value_encrypted)
            except Exception:
                continue
        return out
    finally:
        db.close()


def _json_limited(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return json.dumps({"truncated": True, "preview": text[:limit]}, ensure_ascii=False)


def _persist_pause(
    execution_id: int, node_run_id: int | None, node_id: str, *, message: str,
    approver: str, form_schema: dict[str, Any], expires_at: datetime,
    pause_type: str = "approval",
) -> int:
    """Create one durable pending pause and move execution/node run to WAITING."""
    token = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        existing = db.execute(
            select(WorkflowPause).where(
                WorkflowPause.execution_id == execution_id,
                WorkflowPause.node_id == node_id,
                WorkflowPause.pause_type == pause_type,
                WorkflowPause.status == "PENDING",
            ).order_by(WorkflowPause.id.desc()).limit(1)
        ).scalar_one_or_none()
        if existing is None:
            existing = WorkflowPause(
                execution_id=execution_id, node_id=node_id, pause_type=pause_type,
                message=message, approver=approver,
                form_schema_json=_json_limited(form_schema, 64_000),
                status="PENDING", token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=expires_at,
            )
            db.add(existing)
            db.flush()
        execution = db.get(WorkflowExecution, execution_id)
        if execution is not None and execution.status in ("RUNNING", "WAITING"):
            execution.status = "WAITING"
        if node_run_id is not None:
            node_run = db.get(WorkflowNodeRun, node_run_id)
            if node_run is not None:
                node_run.status = (
                    "WAITING_FORM" if pause_type == "form" else
                    ("WAITING_APPROVAL" if pause_type == "approval" else "WAITING_DELAY")
                )
        db.commit()
        pause_id = existing.id
    _emit_event(
        execution_id, "execution.paused", node_id=node_id,
        payload={
            "pause_id": pause_id, "pause_type": pause_type,
            "status": "WAITING", "expires_at": expires_at.isoformat(),
        },
    )
    return pause_id


def _build_error_context(
    node: dict[str, Any], run_context: dict[str, Any], *, message: str, code: str,
    retryable: bool, attempt: int, details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """error routeへ渡す有限・redact済みの標準Error Context。"""
    sensitive = collect_sensitive_values(run_context)
    sensitive.update(str(value) for value in (run_context.get("__secrets__") or {}).values() if value)
    upstream = {
        str(key): {
            "status": value.get("status"),
            "output": value.get("output"),
        }
        for key, value in run_context.items()
        if not str(key).startswith("__") and key != node.get("id") and isinstance(value, dict)
    }
    summary = redact(
        {"config": node.get("config") or {}, "upstream": upstream},
        sensitive_values=sensitive,
    )
    try:
        limited_summary = json.loads(_json_limited(summary, 64_000))
    except (TypeError, ValueError, json.JSONDecodeError):
        limited_summary = {"truncated": True}
    result = {
        "node_id": str(node.get("id") or ""),
        "node_type": str(node.get("type") or ""),
        "message": redact(str(message), sensitive_values=sensitive),
        "code": str(code),
        "retryable": bool(retryable),
        "attempt": max(1, int(attempt)),
        "input_summary": limited_summary,
        "timestamp": utcnow().isoformat(),
    }
    if details:
        try:
            result["details"] = json.loads(_json_limited(
                redact(details, sensitive_values=sensitive), 16_000,
            ))
        except (TypeError, ValueError, json.JSONDecodeError):
            result["details"] = {"truncated": True}
    return result


def safe_definition_snapshot(value: Any, key: str = "") -> Any:
    """secret参照名は再現用に残し、定義へ直書きされた秘密値だけを除く。"""
    if is_sensitive_key(key):
        if isinstance(value, str) and "{{secrets." in value:
            return value
        return "***"
    if isinstance(value, dict):
        return {str(child_key): safe_definition_snapshot(child, str(child_key)) for child_key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_definition_snapshot(child) for child in value]
    return value


def version_definition_snapshot(definition: dict[str, Any]) -> dict[str, Any]:
    """公開・実行版向けsnapshot。Webhook tokenは不可逆hashだけを照合用に残す。"""
    snapshot = safe_definition_snapshot(definition)
    source_nodes = definition.get("nodes") if isinstance(definition.get("nodes"), list) else []
    target_nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    for source, target in zip(source_nodes, target_nodes, strict=False):
        if source.get("type") != "trigger" or not isinstance(source.get("config"), dict):
            continue
        token = str(source["config"].get("webhook_token") or "")
        target_config = target.get("config") if isinstance(target.get("config"), dict) else None
        if token and target_config is not None:
            target_config["webhook_token_hash"] = hashlib.sha256(token.encode()).hexdigest()
    return snapshot


def _start_node_run(execution_id: int, node: dict, context: dict[str, Any]) -> int | None:
    """秘密値を除いた上流snapshotを保存する。保存失敗で本体実行は落とさない。"""
    try:
        sensitive = collect_sensitive_values(context)
        sensitive.update(str(value) for value in (context.get("__secrets__") or {}).values() if value)
        upstream = redact(
            {key: value for key, value in context.items() if not key.startswith("__") and key != node["id"]},
            sensitive_values=sensitive,
        )
        config = redact(node.get("config") or {}, sensitive_values=sensitive)
        with SessionLocal() as db:
            row = WorkflowNodeRun(
                execution_id=execution_id, node_id=str(node["id"]), node_type=str(node.get("type") or ""),
                node_version=int(node.get("version") or 1), status="RUNNING",
                resolved_inputs_json=_json_limited({"config": config, "upstream": upstream}, 256_000),
                started_at=utcnow(),
            )
            db.add(row)
            db.commit()
            _emit_event(
                execution_id, "node.started", node_id=str(node["id"]),
                payload={"status": "RUNNING", "node_type": str(node.get("type") or "")},
            )
            return row.id
    except Exception:
        logger.exception("node run start persistence failed: execution=%s node=%s", execution_id, node.get("id"))
        return None


def _finish_node_run(
    node_run_id: int | None, entry: dict[str, Any], context: dict[str, Any] | None = None,
) -> None:
    if node_run_id is None:
        return
    created_paths = []
    try:
        with SessionLocal() as db:
            row = db.get(WorkflowNodeRun, node_run_id)
            if row is None:
                return
            output = entry.get("output") if isinstance(entry.get("output"), dict) else {}
            raw_token_count = output.get("tokens") if isinstance(output.get("tokens"), (int, float)) else None
            sensitive = collect_sensitive_values(context or {})
            sensitive.update(str(value) for value in ((context or {}).get("__secrets__") or {}).values() if value)
            safe_output = redact(output, sensitive_values=sensitive)
            safe_output, stored_artifacts, created_paths = workflow_artifacts.compact_output(
                db,
                execution_id=row.execution_id,
                node_run_id=row.id,
                node_id=row.node_id,
                output=safe_output,
                sensitive=bool(safe_output.get("sensitive")),
            )
            usage = safe_output.get("usage") if isinstance(safe_output.get("usage"), dict) else {}
            if raw_token_count is not None and "total_tokens" not in usage:
                usage = {**usage, "total_tokens": raw_token_count}
            raw_logs = safe_output.get("logs")
            logs = raw_logs if isinstance(raw_logs, list) else ([raw_logs] if isinstance(raw_logs, str) else [])
            raw_artifacts = safe_output.get("artifacts")
            artifacts = raw_artifacts if isinstance(raw_artifacts, list) else []
            artifacts = [*artifacts, *(workflow_artifacts.reference(item) for item in stored_artifacts)]
            if isinstance(safe_output.get("path"), str):
                artifacts = [*artifacts, {"path": safe_output["path"]}]
            row.status = str(entry.get("status") or "FAILED")
            row.outputs_json = _json_limited(safe_output, 1_000_000)
            error_context = entry.get("error_context")
            row.error_json = _json_limited(
                redact(
                    error_context if isinstance(error_context, dict) else {"message": str(entry.get("error") or "")},
                    sensitive_values=sensitive,
                ),
                64_000,
            )
            row.logs_json = _json_limited(redact(logs, sensitive_values=sensitive), 256_000)
            row.artifacts_json = _json_limited(redact(artifacts, sensitive_values=sensitive), 256_000)
            row.token_usage_json = _json_limited(usage, 32_000)
            row.attempt = int(entry.get("attempts") or 0)
            row.retry_count = max(0, row.attempt - 1)
            row.finished_at = utcnow()
            if row.started_at is not None:
                started = row.started_at
                finished = row.finished_at
                if started.tzinfo is None and finished.tzinfo is not None:
                    started = started.replace(tzinfo=finished.tzinfo)
                row.elapsed_ms = max(0, int((finished - started).total_seconds() * 1000))
            db.commit()
            for artifact in stored_artifacts:
                _emit_event(
                    row.execution_id, "artifact.created", node_id=row.node_id,
                    payload=workflow_artifacts.reference(artifact), sensitive_values=sensitive,
                )
            _emit_event(
                row.execution_id, "node.finished", node_id=row.node_id,
                payload={
                    "status": row.status, "attempt": row.attempt,
                    "elapsed_ms": row.elapsed_ms,
                }, sensitive_values=sensitive,
            )
    except Exception as exc:
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("orphan workflow artifact cleanup failed: %s", path)
        logger.exception("node run finish persistence failed: node_run=%s", node_run_id)
        if isinstance(exc, workflow_artifacts.WorkflowArtifactError):
            raise ArtifactPersistenceError(str(exc)) from exc


# ---- v2 DAG 実行 ----


async def _execute_graph(
    nodes: list[dict], edges: list[dict], context: dict[str, Any], execution_id: int | None = None,
    *, resume_completed: bool = False, workflow_id: int = 0,
) -> None:
    node_by_id = {n["id"]: n for n in nodes}
    trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
    if trigger is None:
        raise DefinitionError("トリガーノードがありません")

    steps = {"n": 0}
    sem = asyncio.Semaphore(MAX_PARALLEL_NODES)
    outgoing: dict[str, list[dict]] = {}
    for e in edges:
        outgoing.setdefault(e["source"], []).append(e)
    completed_ids = {
        str(node_id) for node_id, entry in context.items()
        if resume_completed and isinstance(entry, dict)
        and entry.get("status") in {"SUCCEEDED", "FAILED", "TIMED_OUT", "SKIPPED"}
    }

    async def run_single(node: dict, run_context: dict[str, Any]) -> dict:
        """1 ノードの実行（承認ゲート → リトライ付き実行 → 記録）。"""
        nid, ntype = node["id"], node.get("type", "")
        config = node.get("config") or {}
        steps["n"] += 1
        if steps["n"] > MAX_STEPS:
            raise NodeError(f"ステップ数が上限（{MAX_STEPS}）を超えました")
        executor = NODE_EXECUTORS.get(ntype)
        if executor is None:
            raise NodeError(f"未知のノード種類: {ntype}")
        entry: dict[str, Any] = {"status": "PENDING", "name": node.get("name") or nid, "type": ntype}
        run_context[nid] = entry
        node_run_id = await asyncio.to_thread(_start_node_run, execution_id, node, run_context) if execution_id is not None else None

        if bool(node.get("disabled")) and ntype != "trigger":
            now = utcnow().isoformat()
            entry.update(
                status="SKIPPED", output={"disabled": True, "skipped": True},
                started_at=now, finished_at=now, attempts=0,
            )
            await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
            return entry

        def fail_entry(
            message: str, code: str, status: str, attempt: int, *, retryable: bool,
            details: dict[str, Any] | None = None,
        ) -> None:
            error_context = _build_error_context(
                node, run_context, message=message, code=code, retryable=retryable,
                attempt=attempt, details=details,
            )
            entry.update(
                status=status, error=error_context["message"], error_context=error_context,
                output={"error": error_context}, finished_at=utcnow().isoformat(), attempts=max(1, attempt),
            )

        # 実行前のhuman interaction。任意ノードの承認と、明示フォームを同じ
        # durable checkpointで扱い、pause種別だけをDB上で分離する。
        interaction_type = "form" if ntype == "human.form" else "approval"
        requires_approval = bool(config.get("require_approval")) or ntype in {"human.approval", "human.form"}
        pause_response: dict[str, Any] = {}
        if requires_approval and ntype != "trigger" and execution_id is not None:
            decisions = run_context.get("__pause_decisions__")
            decision = decisions.pop(nid, None) if isinstance(decisions, dict) else None
            sensitive = collect_sensitive_values(run_context)
            sensitive.update(str(value) for value in (run_context.get("__secrets__") or {}).values() if value)
            prompt = redact(
                render_template(str(config.get("message") or (
                    "必要な情報を入力してください" if interaction_type == "form" else "この処理を続行しますか？"
                )), run_context),
                sensitive_values=sensitive,
            )
            approver = str(config.get("approver") or "").strip()
            try:
                timeout_key = "form_timeout_seconds" if interaction_type == "form" else "approval_timeout_seconds"
                approval_timeout = max(0.1, min(float(config.get(timeout_key) or APPROVAL_TIMEOUT), APPROVAL_TIMEOUT))
            except (TypeError, ValueError):
                approval_timeout = float(APPROVAL_TIMEOUT)
            waiting_since = utcnow()
            entry.update(
                status="WAITING_FORM" if interaction_type == "form" else "WAITING_APPROVAL",
                waiting_since=waiting_since.isoformat(),
                approval={
                    "message": prompt,
                    "approver": approver,
                    "expires_at": (waiting_since + timedelta(seconds=approval_timeout)).isoformat(),
                },
            )
            if decision is None:
                raw_schema = config.get("form_schema")
                if interaction_type == "form" and isinstance(config.get("inputs"), list):
                    form_schema = build_fields_schema(config["inputs"])
                else:
                    form_schema = raw_schema if isinstance(raw_schema, dict) else {}
                await asyncio.to_thread(
                    _persist_pause, execution_id, node_run_id, nid,
                    message=str(prompt), approver=approver, form_schema=form_schema,
                    expires_at=waiting_since + timedelta(seconds=approval_timeout),
                    pause_type=interaction_type,
                )
                raise WorkflowSuspended(nid)
            decision_status = str(decision.get("status") or "") if isinstance(decision, dict) else ""
            pause_response = decision.get("response") if isinstance(decision, dict) and isinstance(decision.get("response"), dict) else {}
            if decision_status == "EXPIRED":
                label = "フォーム入力" if interaction_type == "form" else "承認"
                fail_entry(
                    f"{label}待ちがタイムアウトしました",
                    "FORM_TIMEOUT" if interaction_type == "form" else "APPROVAL_TIMEOUT",
                    "TIMED_OUT", 1, retryable=False,
                )
                await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
                if str(config.get("on_error", "stop")) == "stop":
                    raise NodeError(f"ノード {entry['name']} の{label}待ちがタイムアウトしました")
                return entry
            if decision_status != "APPROVED":
                message = "フォーム入力がキャンセルされました" if interaction_type == "form" else "実行が却下されました"
                code = "FORM_CANCELED" if interaction_type == "form" else "APPROVAL_REJECTED"
                fail_entry(message, code, "FAILED", 1, retryable=False)
                await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
                if str(config.get("on_error", "stop")) == "stop":
                    raise NodeError(message)
                return entry

        # service再起動を越える明示delay。通常sleepでworker枠や実行taskを保持せず、
        # DB checkpointの期限到来後に同じexecution snapshotから継続する。
        if ntype == "control.delay" and execution_id is not None:
            decisions = run_context.get("__pause_decisions__")
            decision = decisions.pop(nid, None) if isinstance(decisions, dict) else None
            try:
                rendered_seconds = render_template(str(config.get("seconds", 1)), run_context)
                delay_seconds = max(0.1, min(float(rendered_seconds), float(DELAY_MAX_SECONDS)))
            except (TypeError, ValueError) as exc:
                fail_entry("Delayの秒数は数値で指定してください", "DELAY_SECONDS_INVALID", "FAILED", 1, retryable=False)
                await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
                if str(config.get("on_error", "stop")) == "stop":
                    raise NodeError(str(entry["error"]), code="DELAY_SECONDS_INVALID", retryable=False) from exc
                return entry
            waiting_since = utcnow()
            scheduled_for = waiting_since + timedelta(seconds=delay_seconds)
            entry.update(
                status="WAITING_DELAY", waiting_since=waiting_since.isoformat(),
                delay={"seconds": delay_seconds, "scheduled_for": scheduled_for.isoformat()},
            )
            if decision is None:
                await asyncio.to_thread(
                    _persist_pause, execution_id, node_run_id, nid,
                    message=render_template(str(config.get("message") or "待機中"), run_context),
                    approver="", form_schema={}, expires_at=scheduled_for, pause_type="delay",
                )
                raise WorkflowSuspended(nid)
            decision_status = str(decision.get("status") or "") if isinstance(decision, dict) else ""
            pause_response = decision.get("response") if isinstance(decision, dict) and isinstance(decision.get("response"), dict) else {}
            if decision_status != "COMPLETED":
                fail_entry("Delay checkpointを再開できません", "DELAY_RESUME_INVALID", "FAILED", 1, retryable=False)
                await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
                if str(config.get("on_error", "stop")) == "stop":
                    raise NodeError(str(entry["error"]), code="DELAY_RESUME_INVALID", retryable=False)
                return entry

        retries = max(0, min(int(config.get("retry_count", 0) or 0), 5))
        retry_wait = max(0.0, min(float(config.get("retry_wait", 5) or 5), 300.0))
        default_timeout = NODE_TIMEOUTS.get(ntype, DEFAULT_NODE_TIMEOUT)
        try:
            timeout = max(0.1, min(float(config.get("node_timeout") or default_timeout), EXECUTION_TIMEOUT))
        except (TypeError, ValueError):
            timeout = float(default_timeout)
        attempt = 0
        entry.update(status="RUNNING", started_at=utcnow().isoformat())
        while True:
            attempt += 1
            retryable_error = True
            error_details: dict[str, Any] = {}
            from app.workflows import nodes as workflow_nodes

            token = workflow_nodes._progress_reporter.set(
                lambda message, current=0, total=0: entry.update(
                    progress={"message": str(message)[:200], "current": int(current), "total": int(total)}
                )
            )
            try:
                effective_config = {
                    **config,
                    "__pause_response": pause_response,
                    "__workflow_id": workflow_id,
                    "__execution_id": execution_id,
                    "__node_id": nid,
                }
                if ntype in _UNMETERED:
                    output = await asyncio.wait_for(executor(effective_config, run_context), timeout=timeout)
                else:
                    async with sem:
                        output = await asyncio.wait_for(executor(effective_config, run_context), timeout=timeout)
                workflow_artifacts.ensure_output_size(output)
                entry.update(status="SUCCEEDED", output=output, finished_at=utcnow().isoformat(), attempts=attempt)
                var_name = str(config.get("output_var") or "").strip()
                if var_name:
                    run_context.setdefault("__vars__", {})[var_name] = output
                await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
                return entry
            except asyncio.CancelledError:
                entry.update(status="CANCELED", finished_at=utcnow().isoformat())
                await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
                raise
            except asyncio.TimeoutError:
                err, final_status, error_code = "タイムアウト", "TIMED_OUT", "NODE_TIMEOUT"
            except ArtifactPersistenceError as e:
                # Executorは完了済み。副作用の二重実行を避けるためartifact保存失敗は再試行しない。
                err, final_status, error_code = str(e), "FAILED", "ARTIFACT_PERSISTENCE"
                attempt = retries + 1
            except NodeError as e:
                err, final_status, error_code = str(e), "FAILED", e.code
                retryable_error = e.retryable
                error_details = e.details
            except Exception as e:  # 想定外もリトライ対象にする
                err, final_status, error_code = f"{type(e).__name__}: {e}", "FAILED", "UNEXPECTED_ERROR"
            finally:
                workflow_nodes._progress_reporter.reset(token)
            if attempt <= retries and retryable_error:
                entry.update(status="RETRYING", error=err, attempts=attempt)
                await asyncio.sleep(retry_wait)
                entry["status"] = "RUNNING"
                continue
            fail_entry(
                err, error_code, final_status, attempt,
                retryable=retryable_error, details=error_details,
            )
            await asyncio.to_thread(_finish_node_run, node_run_id, entry, run_context)
            if str(config.get("on_error", "stop")) == "stop":
                raise NodeError(f"ノード {entry['name']} が失敗しました: {err}")
            return entry

    class DagRun:
        """発火カウント方式の DAG 実行状態（メイン/ループ反復ごとに 1 つ）。"""

        def __init__(self, tg: asyncio.TaskGroup, run_context: dict[str, Any]):
            self.tg = tg
            self.context = run_context
            self.lock = asyncio.Lock()
            self.received: dict[str, int] = {}
            self.live_received: dict[str, int] = {}
            self.arrivals: dict[str, list[str]] = {}
            self.successful_arrivals: dict[str, list[str]] = {}
            self.ran: set[str] = set()
            self.incoming = {nid: 0 for nid in node_by_id}
            self.incoming_sources: dict[str, list[str]] = {nid: [] for nid in node_by_id}
            for e in edges:
                self.incoming[e["target"]] = self.incoming.get(e["target"], 0) + 1
                self.incoming_sources.setdefault(e["target"], []).append(str(e["source"]))

        async def fire(self, target: str, live: bool, source: str | None = None) -> None:
            node = node_by_id.get(target)
            if node is None:
                return
            config = node.get("config") or {}
            merge_mode = str(config.get("mode") or "wait_all") if node.get("type") == "control.merge" else ""
            join_all = str(config.get("join", "")) == "all" or merge_mode in {"wait_all", "collect"}
            async with self.lock:
                self.received[target] = self.received.get(target, 0) + 1
                if live:
                    self.live_received[target] = self.live_received.get(target, 0) + 1
                    if source and source not in self.arrivals.setdefault(target, []):
                        self.arrivals[target].append(source)
                    source_entry = self.context.get(source or "")
                    if source and isinstance(source_entry, dict) and source_entry.get("status") == "SUCCEEDED":
                        if source not in self.successful_arrivals.setdefault(target, []):
                            self.successful_arrivals[target].append(source)
                if target in self.ran:
                    return
                resolved = self.received[target] >= self.incoming.get(target, 0)
                lives = self.live_received.get(target, 0)
                successes = len(self.successful_arrivals.get(target, []))
                if merge_mode == "first_success":
                    run = successes >= 1 or resolved
                    if not run:
                        return
                elif merge_mode == "quorum":
                    quorum = max(1, min(int(config.get("quorum") or 1), max(1, self.incoming.get(target, 1))))
                    run = successes >= quorum or resolved
                    if not run:
                        return
                elif join_all:
                    if not resolved:
                        return  # 全入力が揃うまで待つ
                    run = lives > 0
                else:
                    run = live  # 最初の生きた入力で発火（従来互換）
                    if not run and not (resolved and lives == 0):
                        return
                if run:
                    self.ran.add(target)
            if run:
                self.tg.create_task(self.exec_node(target))
            else:
                # 全入力が dead → このノードは実行されない。下流へ dead を伝播
                self.context.setdefault(target, {"status": "SKIPPED"})
                for e in outgoing.get(target, []):
                    await self.fire(e["target"], live=False, source=target)

        async def start(self, node_id: str) -> None:
            async with self.lock:
                if node_id in self.ran:
                    return
                self.ran.add(node_id)
            self.tg.create_task(self.exec_node(node_id))

        async def exec_node(self, nid: str) -> None:
            node = node_by_id.get(nid)
            if node is None:
                return
            cached_entry = self.context.get(nid)
            if nid in completed_ids and isinstance(cached_entry, dict):
                await self.propagate(node, cached_entry)
                return
            if node.get("type") == "control.loop":
                await run_loop(node, self.context)
                await self.propagate(node, self.context[nid])
                return
            run_node = node
            if node.get("type") == "control.merge":
                mode = str((node.get("config") or {}).get("mode") or "wait_all")
                if mode in {"wait_all", "collect"}:
                    source_ids = self.incoming_sources.get(nid, [])
                elif mode in {"first_success", "quorum"}:
                    source_ids = self.successful_arrivals.get(nid, [])
                else:
                    source_ids = self.arrivals.get(nid, [])
                run_node = {**node, "config": {**(node.get("config") or {}), "__merge_source_ids": source_ids}}
            entry = await run_single(run_node, self.context)
            await self.propagate(node, entry)

        async def propagate(self, node: dict[str, Any], entry: dict[str, Any]) -> None:
            nid = str(node.get("id") or "")
            failed = entry["status"] in ("FAILED", "TIMED_OUT")
            on_error = str((node.get("config") or {}).get("on_error", "stop"))
            outs = outgoing.get(nid, [])
            if node.get("type") == "control.loop":
                for edge in outs:
                    branch = _edge_branch(edge)
                    if branch != "body":
                        await self.fire(edge["target"], live=branch not in {"error", "timeout"}, source=nid)
            elif node.get("type") == "control.try" and not failed:
                branch = "success" if bool((entry.get("output") or {}).get("ok")) else "error"
                for edge in outs:
                    await self.fire(
                        edge["target"], live=(_edge_branch(edge) or "success") == branch, source=nid,
                    )
            elif node.get("type") == "condition.if" and not failed:
                branch = "true" if (entry.get("output") or {}).get("result") else "false"
                for e in outs:
                    await self.fire(e["target"], live=(_edge_branch(e) or "true") == branch, source=nid)
            elif node.get("type") == "control.circuit_breaker" and not failed:
                output = entry.get("output") or {}
                branch = (
                    "allowed" if str(output.get("operation") or "") != "check"
                    else ("allowed" if bool(output.get("allowed")) else "blocked")
                )
                for e in outs:
                    await self.fire(e["target"], live=(_edge_branch(e) or "allowed") == branch, source=nid)
            elif failed and on_error == "branch":
                failure_branch = "timeout" if entry["status"] == "TIMED_OUT" else "error"
                has_timeout_route = any(_edge_branch(edge) == "timeout" for edge in outs)
                for e in outs:
                    branch = _edge_branch(e)
                    # 後方互換: timeout専用edgeがない既存flowではerror edgeがtimeoutも受ける。
                    live = branch == failure_branch or (
                        failure_branch == "timeout" and not has_timeout_route and branch == "error"
                    )
                    await self.fire(e["target"], live=live, source=nid)
            else:  # 成功、または continue で失敗を無視して先へ
                for e in outs:
                    await self.fire(e["target"], live=_edge_branch(e) not in {"error", "timeout"}, source=nid)

    async def run_loop(node: dict, parent_context: dict[str, Any]) -> None:
        from app.workflows.nodes import render_template

        node_id = node["id"]
        config = node.get("config") or {}
        mode = config.get("mode", "count")
        entry: dict[str, Any] = {"status": "RUNNING", "started_at": utcnow().isoformat(),
                                 "name": node.get("name") or node_id, "type": "control.loop"}
        parent_context[node_id] = entry

        items: list[Any]
        if mode == "foreach":
            raw = render_template(str(config.get("items", "")), parent_context).strip()
            try:
                parsed = json.loads(raw)
                items = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                items = [line for line in raw.splitlines() if line.strip()]
        else:
            count = max(1, min(int(config.get("count", 1) or 1), 100))
            items = list(range(count))
        items = items[:100]
        parallel = max(1, min(int(config.get("parallel", 1) or 1), 5))
        body_edges = [e for e in outgoing.get(node_id, []) if _edge_branch(e) == "body"]

        async def one_iteration(index: int, item: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            iteration_context = dict(parent_context)
            iteration_context["__vars__"] = dict(parent_context.get("__vars__") or {})
            iteration_context[node_id] = {
                "status": "RUNNING", "name": entry["name"], "type": "control.loop",
                "output": {"index": index, "item": item, "total": len(items)},
            }
            async with asyncio.TaskGroup() as tg2:
                sub = DagRun(tg2, iteration_context)
                for e in body_edges:
                    await sub.start(e["target"])
            outputs = {
                key: value.get("output")
                for key, value in iteration_context.items()
                if (key not in parent_context or value is not parent_context[key])
                and isinstance(value, dict) and "output" in value
            }
            return {"index": index, "item": item, "outputs": outputs}, iteration_context

        if parallel <= 1:
            completed = []
            for index, item in enumerate(items):
                completed.append(await one_iteration(index, item))
                entry["progress"] = {"message": "ループ実行中", "current": index + 1, "total": len(items)}
        else:
            completed = []
            for base in range(0, len(items), parallel):
                batch = list(enumerate(items))[base : base + parallel]
                completed.extend(await asyncio.gather(*(one_iteration(i, it) for i, it in batch)))
                entry["progress"] = {"message": "並列ループ実行中", "current": min(base + len(batch), len(items)), "total": len(items)}
        if completed:
            # 従来互換: done側からbody nodeを参照する場合は最後の反復結果を見せる。
            last_context = completed[-1][1]
            for key, value in last_context.items():
                if key not in {
                    "__secrets__", "__input__", "__depth__", "__vars__",
                    "__event_lineage__", "__event_hop__", node_id,
                }:
                    parent_context[key] = value
            parent_context["__vars__"] = last_context.get("__vars__", parent_context.get("__vars__", {}))
        results = [result for result, _iteration_context in completed]
        entry.update(
            status="SUCCEEDED",
            output={"index": len(items) - 1, "item": items[-1] if items else None,
                    "total": len(items), "done": True, "results": results},
            finished_at=utcnow().isoformat(),
        )

    async with asyncio.TaskGroup() as tg:
        dag = DagRun(tg, context)
        await dag.start(trigger["id"])


# ---- 実行管理 ----


def _ensure_execution_version(db, wf: Workflow) -> WorkflowVersion:
    definition = wf.definition_json or "{}"
    checksum = hashlib.sha256(definition.encode()).hexdigest()
    existing = db.execute(
        select(WorkflowVersion)
        .where(
            WorkflowVersion.workflow_id == wf.id, WorkflowVersion.checksum == checksum,
            WorkflowVersion.name == wf.name, WorkflowVersion.description == wf.description,
        )
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    latest = db.execute(
        select(WorkflowVersion.version)
        .where(WorkflowVersion.workflow_id == wf.id)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none() or 0
    version = WorkflowVersion(
        workflow_id=wf.id, version=latest + 1, name=wf.name,
        description=wf.description,
        definition_json=json.dumps(version_definition_snapshot(json.loads(definition)), ensure_ascii=False),
        checksum=checksum, note="実行スナップショット",
    )
    db.add(version)
    db.flush()
    return version


def _runtime_snapshot(db, nodes: list[dict]) -> dict[str, Any]:
    models = []
    node_versions = {}
    for node in nodes:
        node_id = str(node.get("id") or "")
        node_versions[node_id] = int(node.get("version") or 1)
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        if config.get("model") or config.get("llm_model"):
            models.append({
                "node_id": node_id,
                "endpoint": str(config.get("base_url") or config.get("llm_base_url") or ""),
                "model": str(config.get("model") or config.get("llm_model") or ""),
                "sampling": {
                    key: config[key] for key in ("temperature", "top_p", "top_k", "seed") if key in config
                },
            })
    return {
        "python": platform.python_version(), "node_versions": node_versions, "models": models,
        "secret_names": [row.name for row in db.query(WorkflowSecret).order_by(WorkflowSecret.name).all()],
    }


def _partial_replay_graph(
    nodes: list[dict], edges: list[dict], start_node_id: str, seed_context: dict[str, Any],
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    node_by_id = {str(node.get("id") or ""): node for node in nodes}
    if start_node_id not in node_by_id:
        raise DefinitionError("再開ノードが現在の定義に存在しません")
    trigger = next((node for node in nodes if node.get("type") == "trigger"), None)
    if trigger is None or start_node_id == trigger.get("id"):
        return nodes, edges, {}

    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        outgoing.setdefault(source, []).append(target)
        incoming.setdefault(target, []).append(source)

    descendants = {start_node_id}
    stack = [start_node_id]
    while stack:
        for target in outgoing.get(stack.pop(), []):
            if target not in descendants:
                descendants.add(target)
                stack.append(target)
    ancestors: set[str] = set()
    stack = [start_node_id]
    while stack:
        for source in incoming.get(stack.pop(), []):
            if source not in ancestors:
                ancestors.add(source)
                stack.append(source)

    trigger_id = str(trigger["id"])
    partial_nodes = [trigger] + [node for node in nodes if str(node.get("id")) in descendants]
    partial_edges = [
        edge for edge in edges
        if str(edge.get("source")) in descendants and str(edge.get("target")) in descendants
    ]
    partial_edges.insert(0, {"id": f"__resume__{start_node_id}", "source": trigger_id, "target": start_node_id})
    retained = {
        key: value for key, value in seed_context.items()
        if key in ancestors and key != trigger_id and isinstance(value, dict)
    }
    variables: dict[str, Any] = {}
    for node_id in ancestors:
        node = node_by_id.get(node_id) or {}
        name = str((node.get("config") or {}).get("output_var") or "").strip()
        entry = seed_context.get(node_id)
        if name and isinstance(entry, dict) and "output" in entry:
            variables[name] = entry["output"]
    retained["__vars__"] = variables
    return partial_nodes, partial_edges, retained


def _run_to_graph(nodes: list[dict], edges: list[dict], target_node_id: str) -> tuple[list[dict], list[dict]]:
    """対象ノードへ到達する祖先だけを残し、下流の副作用を実行しない。"""
    node_ids = {str(node.get("id") or "") for node in nodes}
    if target_node_id not in node_ids:
        raise DefinitionError("対象ノードが現在の定義に存在しません")
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        incoming.setdefault(str(edge.get("target") or ""), []).append(str(edge.get("source") or ""))
    retained = {target_node_id}
    stack = [target_node_id]
    while stack:
        for source in incoming.get(stack.pop(), []):
            if source not in retained:
                retained.add(source)
                stack.append(source)
    return (
        [node for node in nodes if str(node.get("id") or "") in retained],
        [edge for edge in edges if str(edge.get("source") or "") in retained and str(edge.get("target") or "") in retained],
    )


async def _launch_execution(
    execution_id: int, workflow_id: int, nodes: list[dict], edges: list[dict], context: dict[str, Any],
    *, resume_completed: bool = False,
) -> None:
    """Run or continue one persisted execution; a durable pause ends this task without finishing the row."""
    existing = _running.get(execution_id)
    if existing is not None and not existing.done():
        return

    async def runner() -> None:
        context.setdefault("__vars__", {})
        _live[execution_id] = context
        status = "SUCCEEDED"
        error = ""

        def flush(final_status: str | None = None) -> None:
            with SessionLocal() as db2:
                row = db2.get(WorkflowExecution, execution_id)
                if row is None:
                    return
                sensitive_values = collect_sensitive_values(context)
                sensitive_values.update(
                    str(value) for value in (context.get("__secrets__") or {}).values() if value
                )
                saved = redact(
                    {key: value for key, value in context.items() if key != "__secrets__"},
                    sensitive_values=sensitive_values,
                )
                saved = workflow_artifacts.compact_execution_context(db2, execution_id, saved)
                row.context_json = json.dumps(saved, ensure_ascii=False, default=str)
                if final_status is not None:
                    row.status = final_status
                    row.error = str(redact(error, sensitive_values=sensitive_values))
                    row.finished_at = utcnow()
                db2.commit()
                if final_status is not None:
                    _emit_event(
                        execution_id, "execution.finished",
                        payload={"status": final_status, "error": row.error},
                        sensitive_values=sensitive_values,
                    )

        async def flusher() -> None:
            while True:
                await asyncio.sleep(3)
                await asyncio.to_thread(flush)

        def flatten(exc: BaseException) -> list[BaseException]:
            if isinstance(exc, BaseExceptionGroup):
                flattened: list[BaseException] = []
                for child in exc.exceptions:
                    flattened.extend(flatten(child))
                return flattened
            return [exc]

        flush_task = asyncio.create_task(flusher())
        try:
            await asyncio.wait_for(
                _execute_graph(
                    nodes, edges, context, execution_id, resume_completed=resume_completed,
                    workflow_id=workflow_id,
                ),
                timeout=EXECUTION_TIMEOUT,
            )
        except WorkflowSuspended:
            status, error = "WAITING", ""
        except asyncio.TimeoutError:
            status, error = "TIMED_OUT", "実行全体がタイムアウトしました"
        except asyncio.CancelledError:
            status, error = "CANCELED", "キャンセルされました"
        except (NodeError, DefinitionError) as exc:
            status, error = "FAILED", str(exc)[:500]
        except BaseExceptionGroup as group:
            causes = flatten(group)
            material = [cause for cause in causes if not isinstance(cause, asyncio.CancelledError)]
            if material and all(isinstance(cause, WorkflowSuspended) for cause in material):
                status, error = "WAITING", ""
            elif any(isinstance(cause, asyncio.CancelledError) for cause in causes) and not material:
                status, error = "CANCELED", "キャンセルされました"
            elif material and all(isinstance(cause, (NodeError, DefinitionError)) for cause in material):
                status, error = "FAILED", "; ".join(str(cause) for cause in material)[:500]
            else:
                logger.exception("workflow %s execution failed", workflow_id)
                status = "FAILED"
                error = "; ".join(f"{type(cause).__name__}: {cause}" for cause in material)[:500]
        except Exception:
            logger.exception("workflow %s execution failed", workflow_id)
            status, error = "FAILED", "内部エラー"
        finally:
            flush_task.cancel()
            _running.pop(execution_id, None)
            _live.pop(execution_id, None)
            await asyncio.to_thread(flush, None if status == "WAITING" else status)

    task = asyncio.create_task(runner())
    _running[execution_id] = task


async def run_workflow(
    workflow_id: int, trigger_type: str = "manual", input_data: dict | None = None, depth: int = 0,
    *, definition_json: str | None = None, workflow_version_id: int | None = None,
    start_node_id: str | None = None, seed_context: dict[str, Any] | None = None,
    stop_node_id: str | None = None,
    published_only: bool = False, event_lineage: list[int] | tuple[int, ...] | None = None,
    event_hop: int = 0, subflow_lineage: list[int] | tuple[int, ...] | None = None,
) -> int:
    """実行レコードを作成しバックグラウンドで実行。実行 ID を返す。

    input_data: チャットフロー等の入力（trigger ノードの出力へ展開される）。
    depth: サブフロー呼び出しの深さ（flow.call の再帰暴走防止）。
    """
    if depth > MAX_SUBFLOW_DEPTH:
        raise DefinitionError(f"サブフローの深さが上限（{MAX_SUBFLOW_DEPTH}）を超えました")
    db = SessionLocal()
    try:
        wf = db.get(Workflow, workflow_id)
        if wf is None:
            raise DefinitionError("ワークフローが見つかりません")
        if published_only and definition_json is None:
            published = db.execute(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == workflow_id,
                    WorkflowVersion.published_at.is_not(None),
                ).order_by(WorkflowVersion.published_at.desc()).limit(1)
            ).scalar_one_or_none()
            if published is None:
                raise DefinitionError("公開済みバージョンがありません。先にワークフローを公開してください")
            definition_json = published.definition_json
            workflow_version_id = published.id
        definition = definition_json if definition_json is not None else wf.definition_json
        nodes, edges = parse_definition(definition)
        snapshot_nodes = nodes
        retained_context: dict[str, Any] = {}
        if start_node_id:
            nodes, edges, retained_context = _partial_replay_graph(
                nodes, edges, start_node_id, seed_context or {},
            )
        if stop_node_id:
            nodes, edges = _run_to_graph(nodes, edges, stop_node_id)
        if workflow_version_id is not None:
            version = db.get(WorkflowVersion, workflow_version_id)
            if version is None or version.workflow_id != workflow_id:
                raise DefinitionError("実行するワークフローバージョンが見つかりません")
        else:
            version = _ensure_execution_version(db, wf)
        definition_object = json.loads(definition or "{}")
        safe_definition = safe_definition_snapshot(definition_object)
        execution = WorkflowExecution(
            workflow_id=workflow_id, workflow_version_id=version.id,
            status="RUNNING", trigger_type=trigger_type,
            definition_snapshot_json=json.dumps(safe_definition, ensure_ascii=False),
            runtime_snapshot_json=json.dumps({
                **_runtime_snapshot(db, snapshot_nodes), "resume_from_node_id": start_node_id,
                "run_to_node_id": stop_node_id,
            }, ensure_ascii=False),
        )
        db.add(execution)
        db.commit()
        execution_id = execution.id
    finally:
        db.close()

    await asyncio.to_thread(
        _emit_event, execution_id, "execution.started",
        payload={"status": "RUNNING", "trigger_type": trigger_type},
    )

    context: dict[str, Any] = {
        **retained_context,
        "__input__": input_data or {},
        "__depth__": depth,
        "__event_lineage__": list(event_lineage or []),
        "__event_hop__": max(0, int(event_hop or 0)),
        "__subflow_lineage__": list(subflow_lineage or []),
        "__secrets__": await asyncio.to_thread(_load_secrets),
    }
    await _launch_execution(execution_id, workflow_id, nodes, edges, context)
    return execution_id


async def _resume_pause(pause_id: int) -> None:
    """Rebuild a continuation only from persisted execution/pause state."""
    with SessionLocal() as db:
        pause = db.get(WorkflowPause, pause_id)
        if pause is None or pause.status not in ("APPROVED", "REJECTED", "EXPIRED", "COMPLETED"):
            return
        execution = db.get(WorkflowExecution, pause.execution_id)
        if execution is None or execution.status not in ("RUNNING", "WAITING"):
            return
        definition = execution.definition_snapshot_json or "{}"
        saved_context = json.loads(execution.context_json or "{}")
        response = json.loads(pause.response_json or "{}")
        workflow_id = execution.workflow_id
        execution_id = execution.id
        node_id = pause.node_id
        decision_status = pause.status
        execution.status = "RUNNING"
        db.commit()

    nodes, edges = parse_definition(definition)
    context: dict[str, Any] = {
        **saved_context,
        "__input__": saved_context.get("__input__") if isinstance(saved_context.get("__input__"), dict) else {},
        "__depth__": int(saved_context.get("__depth__") or 0),
        "__event_lineage__": list(saved_context.get("__event_lineage__") or []),
        "__event_hop__": int(saved_context.get("__event_hop__") or 0),
        "__subflow_lineage__": list(saved_context.get("__subflow_lineage__") or []),
        "__secrets__": await asyncio.to_thread(_load_secrets),
        "__pause_decisions__": {node_id: {"status": decision_status, "response": response}},
    }
    await _launch_execution(execution_id, workflow_id, nodes, edges, context, resume_completed=True)


async def recover_paused_workflows_once() -> int:
    """Expire due pauses and restart already-resolved continuations after a service crash."""
    now = utcnow()
    resume_ids: list[int] = []
    with SessionLocal() as db:
        due_delays = db.execute(
            select(WorkflowPause).where(
                WorkflowPause.pause_type == "delay",
                WorkflowPause.status == "PENDING", WorkflowPause.expires_at <= now,
            )
        ).scalars().all()
        for pause in due_delays:
            pause.status = "COMPLETED"
            pause.resumed_at = now
            pause.response_json = json.dumps({
                "scheduled_for": pause.expires_at.isoformat(),
                "resumed_at": now.isoformat(),
            }, ensure_ascii=False)
            execution = db.get(WorkflowExecution, pause.execution_id)
            if execution is not None and execution.status == "WAITING":
                execution.status = "RUNNING"
                resume_ids.append(pause.id)
        expired = db.execute(
            select(WorkflowPause).where(
                WorkflowPause.pause_type.in_(("approval", "form")),
                WorkflowPause.status == "PENDING", WorkflowPause.expires_at <= now,
            )
        ).scalars().all()
        for pause in expired:
            pause.status = "EXPIRED"
            pause.resumed_at = now
            execution = db.get(WorkflowExecution, pause.execution_id)
            if execution is not None and execution.status == "WAITING":
                execution.status = "RUNNING"
                resume_ids.append(pause.id)
        pause_rows = db.execute(
            select(WorkflowPause).join(
                WorkflowExecution, WorkflowExecution.id == WorkflowPause.execution_id,
            ).where(
                WorkflowExecution.status.in_(("RUNNING", "WAITING")),
            ).order_by(WorkflowPause.execution_id, WorkflowPause.id.desc())
        ).scalars().all()
        # 過去の解決済みpauseではなく、executionごとの最新checkpointだけを再開する。
        # delay後にapprovalへ到達したflowで古いCOMPLETED delayを再消費しない。
        latest_by_execution: dict[int, WorkflowPause] = {}
        for pause in pause_rows:
            latest_by_execution.setdefault(pause.execution_id, pause)
        resume_ids.extend(
            pause.id for pause in latest_by_execution.values()
            if pause.status in {"APPROVED", "REJECTED", "EXPIRED", "COMPLETED"}
        )
        db.commit()
    launched = 0
    for pause_id in dict.fromkeys(resume_ids):
        pause = None
        with SessionLocal() as db:
            pause = db.get(WorkflowPause, pause_id)
        if pause is not None and pause.execution_id not in _running:
            await _resume_pause(pause_id)
            launched += 1
    return launched


async def pause_recovery_loop() -> None:
    while True:
        try:
            await recover_paused_workflows_once()
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("workflow pause recovery failed")
            await asyncio.sleep(1)


def cancel_execution(execution_id: int) -> bool:
    task = _running.get(execution_id)
    if task and not task.done():
        task.cancel()
        return True
    with SessionLocal() as db:
        execution = db.get(WorkflowExecution, execution_id)
        if execution is None or execution.status != "WAITING":
            return False
        pauses = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id, WorkflowPause.status == "PENDING",
        )).scalars().all()
        for pause in pauses:
            pause.status = "CANCELED"
            pause.resumed_at = utcnow()
        execution.status = "CANCELED"
        execution.error = "キャンセルされました"
        execution.finished_at = utcnow()
        db.commit()
    _emit_event(execution_id, "execution.finished", payload={"status": "CANCELED", "error": "キャンセルされました"})
    return True


# ---- スケジューラー ----


def _next_run_after(trigger_config: dict, last: datetime | None, now: datetime) -> bool:
    """トリガー設定に基づき、いま実行すべきか判定する。"""
    mode = trigger_config.get("mode", "manual")
    if mode == "interval":
        minutes = max(1, int(trigger_config.get("interval_minutes", 60)))
        if last is None:
            return True
        return now - last >= timedelta(minutes=minutes)
    if mode == "daily":
        hhmm = str(trigger_config.get("time", "08:00"))
        try:
            hour, minute = (int(x) for x in hhmm.split(":"))
        except ValueError:
            return False
        today_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < today_at:
            return False
        return last is None or last < today_at
    if mode == "cron":
        expr = str(trigger_config.get("cron", ""))
        try:
            from croniter import croniter

            base = last or (now - timedelta(days=1))
            next_time = croniter(expr, base).get_next(datetime)
            return next_time <= now
        except Exception:
            return False
    return False


async def scheduler_loop() -> None:
    """30 秒ごとに有効なワークフローのスケジュールトリガーを評価する。"""
    from app.maintenance.watchdog import beat

    while True:
        try:
            beat("scheduler")
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc)

            def find_due() -> list[tuple[int, dict]]:
                db = SessionLocal()
                due: list[tuple[int, dict]] = []
                try:
                    for wf, definition in _published_workflow_definitions(db):
                        try:
                            nodes, _ = parse_definition(definition)
                        except DefinitionError:
                            continue
                        trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
                        if trigger is None:
                            continue
                        config = trigger.get("config") or {}
                        if config.get("mode") in ("interval", "daily", "cron"):
                            last_row = db.execute(
                                select(WorkflowExecution.started_at)
                                .where(WorkflowExecution.workflow_id == wf.id)
                                .order_by(WorkflowExecution.started_at.desc())
                                .limit(1)
                            ).scalar_one_or_none()
                            if last_row is not None and last_row.tzinfo is None:
                                last_row = last_row.replace(tzinfo=timezone.utc)
                            if _next_run_after(config, last_row, now):
                                due.append((wf.id, config))
                    return due
                finally:
                    db.close()

            for wf_id, _config in await asyncio.to_thread(find_due):
                logger.info("scheduled workflow %s triggered", wf_id)
                await run_workflow(wf_id, trigger_type="schedule", published_only=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler loop error")


# ---- イベントトリガー（アラート連動） ----

SYSTEM_EVENT_SOURCES = frozenset({"gpu", "vram", "disk", "llama_server", "systemd", "file"})
_SYSTEM_PAYLOAD_MAX_ITEMS = 32
_SYSTEM_PAYLOAD_MAX_TEXT = 2000


def _published_workflow_definitions(db) -> list[tuple[Workflow, str]]:
    """有効なworkflowと最新の公開定義を返す。下書きは自動起動判定に使わない。"""
    rows = db.execute(select(Workflow).where(Workflow.enabled.is_(True))).scalars().all()
    published: list[tuple[Workflow, str]] = []
    for workflow in rows:
        version = db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.published_at.is_not(None),
            ).order_by(WorkflowVersion.published_at.desc(), WorkflowVersion.version.desc()).limit(1)
        ).scalar_one_or_none()
        if version is not None:
            definition = version.definition_json
            # 旧版互換: 公開後に下書きが変わっていない場合だけ、平文tokenから一時hashを補う。
            try:
                parsed = json.loads(definition or "{}")
                trigger = next((n for n in parsed.get("nodes", []) if n.get("type") == "trigger"), None)
                config = (trigger or {}).get("config") or {}
                current_checksum = hashlib.sha256((workflow.definition_json or "{}").encode()).hexdigest()
                if config.get("mode") == "webhook" and not config.get("webhook_token_hash") and version.checksum == current_checksum:
                    draft = json.loads(workflow.definition_json or "{}")
                    draft_trigger = next((n for n in draft.get("nodes", []) if n.get("type") == "trigger"), None)
                    token = str(((draft_trigger or {}).get("config") or {}).get("webhook_token") or "")
                    if token:
                        config["webhook_token_hash"] = hashlib.sha256(token.encode()).hexdigest()
                        definition = json.dumps(parsed, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass
            published.append((workflow, definition))
    return published


def _bounded_event_payload(payload: dict) -> dict[str, Any]:
    """内部イベントをJSON互換の小さな値へ制限し、秘密らしいキーを除外する。"""
    blocked = {"password", "passwd", "secret", "token", "authorization", "cookie"}
    result: dict[str, Any] = {}
    for raw_key, value in list(payload.items())[:_SYSTEM_PAYLOAD_MAX_ITEMS]:
        key = str(raw_key)[:128]
        if any(part in key.lower() for part in blocked):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            result[key] = value
        elif isinstance(value, str):
            result[key] = value[:_SYSTEM_PAYLOAD_MAX_TEXT]
        else:
            try:
                encoded = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                encoded = str(value)
            result[key] = encoded[:_SYSTEM_PAYLOAD_MAX_TEXT]
    return result


def _published_system_configs(event_source: str) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        configs: list[dict[str, Any]] = []
        for _workflow, definition in _published_workflow_definitions(db):
            try:
                nodes, _ = parse_definition(definition)
            except DefinitionError:
                continue
            trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
            config = (trigger or {}).get("config") or {}
            if config.get("mode") == "system" and config.get("system_event") == event_source:
                configs.append(config)
        return configs
    finally:
        db.close()


def event_trigger_targets(
    event_source: str, payload: dict, lineage: list[int] | tuple[int, ...] = (),
) -> tuple[list[int], list[int]]:
    """Resolve latest published event subscribers and exclude the current event lineage."""
    lineage_ids = {int(item) for item in lineage if int(item) > 0}
    db = SessionLocal()
    try:
        targets: list[int] = []
        skipped: list[int] = []
        for wf, definition in _published_workflow_definitions(db):
            try:
                nodes, _ = parse_definition(definition)
            except DefinitionError:
                continue
            trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
            if trigger is None:
                continue
            config = trigger.get("config") or {}
            if config.get("mode") != "event" or config.get("event_source", "alert") != event_source:
                continue
            if event_source == "workflow":
                if str(config.get("event_name") or "").strip() != str(payload.get("event_name") or "").strip():
                    continue
            else:
                rule_filter = str(config.get("rule_filter", "") or "").strip()
                if rule_filter and rule_filter not in str(payload.get("rule", "")):
                    continue
            if wf.id in lineage_ids:
                skipped.append(wf.id)
            else:
                targets.append(wf.id)
        return sorted(targets), sorted(skipped)
    finally:
        db.close()


async def fire_event_triggers(event_source: str, payload: dict) -> list[int]:
    """イベント発生時に、該当するイベントトリガーのワークフローを起動する。

    trigger.config: {mode: "event", event_source: "alert", rule_filter: "部分一致(任意)"}
    """
    execution_ids = []
    targets, _skipped = await asyncio.to_thread(event_trigger_targets, event_source, payload)
    for wf_id in targets:
        try:
            execution_ids.append(await run_workflow(wf_id, trigger_type="event", input_data=payload, published_only=True))
            logger.info("event trigger (%s) fired workflow %s", event_source, wf_id)
        except DefinitionError:
            continue
    return execution_ids


async def fire_system_triggers(event_source: str, payload: dict) -> list[int]:
    """監視系イベントで最新の公開済みworkflowだけを起動する。

    trigger.config: {mode: "system", system_event: "gpu|vram|disk|llama_server|systemd|file",
                     resource_filter: "任意の部分一致"}
    """
    source = str(event_source).strip().lower()
    if source not in SYSTEM_EVENT_SOURCES:
        raise ValueError(f"unsupported system event source: {source}")
    safe_payload = _bounded_event_payload(payload)
    safe_payload["event_source"] = source

    def find_targets() -> list[int]:
        db = SessionLocal()
        try:
            targets: list[int] = []
            for wf, definition in _published_workflow_definitions(db):
                try:
                    nodes, _ = parse_definition(definition)
                except DefinitionError:
                    continue
                trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
                config = (trigger or {}).get("config") or {}
                if config.get("mode") != "system" or config.get("system_event") != source:
                    continue
                if source == "file":
                    configured_path = str(config.get("file_path", "") or "").strip()
                    if not configured_path or configured_path != str(safe_payload.get("resource", "")):
                        continue
                resource_filter = str(config.get("resource_filter", "") or "").strip().lower()
                resource = " ".join(str(safe_payload.get(key, "")) for key in ("resource", "rule", "metric", "app"))
                if resource_filter and resource_filter not in resource.lower():
                    continue
                targets.append(wf.id)
            return targets
        finally:
            db.close()

    execution_ids: list[int] = []
    for wf_id in await asyncio.to_thread(find_targets):
        try:
            execution_ids.append(await run_workflow(
                wf_id, trigger_type=f"system:{source}", input_data=safe_payload, published_only=True,
            ))
            logger.info("system trigger (%s) fired workflow %s", source, wf_id)
        except DefinitionError:
            continue
    return execution_ids


async def system_event_loop() -> None:
    """公開済みsystem triggerが必要とする軽量な状態変化だけをpollする。"""
    from app.applications import systemd as systemd_service
    from app.files import service as file_service
    from app.models_mgmt import llama

    file_states: dict[str, tuple[bool, int, int, bool]] = {}
    llama_states: dict[str, str] = {}
    while True:
        try:
            file_configs = await asyncio.to_thread(_published_system_configs, "file")
            watched_paths: set[str] = set()
            for config in file_configs:
                raw_path = str(config.get("file_path", "") or "").strip()
                if not raw_path:
                    continue
                try:
                    candidate = await asyncio.to_thread(file_service.resolve, raw_path, must_exist=False)
                    exists = candidate.exists()
                    path = await asyncio.to_thread(file_service.resolve, raw_path) if exists else candidate
                    stat = path.stat() if exists else None
                    signature = (exists, stat.st_mtime_ns if stat else 0, stat.st_size if stat else 0, path.is_dir() if exists else False)
                except (OSError, ValueError, file_service.FileAccessError):
                    logger.warning("system file trigger path rejected: %s", raw_path)
                    continue
                normalized = str(path)
                watched_paths.add(normalized)
                previous = file_states.get(normalized)
                file_states[normalized] = signature
                if previous is not None and previous != signature:
                    change = "created" if not previous[0] and exists else "deleted" if previous[0] and not exists else "modified"
                    await fire_system_triggers("file", {
                        "resource": normalized, "change": change, "exists": exists,
                        "size": stat.st_size if stat else 0, "modified_at_ns": stat.st_mtime_ns if stat else 0,
                    })
            file_states = {path: state for path, state in file_states.items() if path in watched_paths}

            if await asyncio.to_thread(_published_system_configs, "llama_server"):
                runtime = await asyncio.to_thread(llama.runtime_status)
                aliases = [str(item.get("alias") or "llama") for item in runtime.get("instances", [])]
                current: dict[str, str] = {}
                for alias in aliases:
                    status = await asyncio.to_thread(systemd_service.query_status, llama.unit_name(alias))
                    current[alias] = str(status.get("status") or "UNKNOWN")
                    previous = llama_states.get(alias)
                    if previous is not None and previous != current[alias]:
                        await fire_system_triggers("llama_server", {
                            "resource": alias, "status": current[alias], "previous_status": previous,
                        })
                llama_states = current
            else:
                llama_states.clear()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("system event loop error")
        await asyncio.sleep(2)
