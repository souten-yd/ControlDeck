"""Durable Workflow-to-Workflow business event outbox and delivery."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
import re
import threading
from typing import Any
import uuid

from sqlalchemy import delete, func, select

from app.audit import service as audit
from app.database import SessionLocal
from app.models import (
    WorkflowBusinessEvent, WorkflowEventDelivery, utcnow,
)
from app.workflows.redaction import collect_sensitive_values, redact

EVENT_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,127}\Z")
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
MAX_EVENT_TARGETS = 100
MAX_EVENT_HOPS = 8
MAX_DELIVERY_ATTEMPTS = 3
MAX_OUTBOX_EVENTS = 10_000
EVENT_RETENTION = timedelta(days=7)

logger = logging.getLogger(__name__)


class _CrossLoopAsyncLock:
    """複数event loopから呼ばれてもoutbox配送をprocess内で直列化する。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    async def __aenter__(self):
        # TestClient／管理CLI等が別loopを持ってもasyncio.Lockのloop束縛を起こさない。
        acquire = asyncio.create_task(asyncio.to_thread(self._lock.acquire))
        try:
            await asyncio.shield(acquire)
        except asyncio.CancelledError:
            # workerが後から取得して永久lockしないよう、取得完了後に必ず解放する。
            acquired = await acquire
            if acquired:
                self._lock.release()
            raise
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        self._lock.release()


_dispatch_lock = _CrossLoopAsyncLock()


class WorkflowBusinessEventError(ValueError):
    pass


def validate_event_name(value: Any, sensitive_values: set[str] | None = None) -> str:
    name = str(value or "").strip()
    if not EVENT_NAME_RE.fullmatch(name):
        raise WorkflowBusinessEventError(
            "event名は英字で始まる1〜128文字の英数字・._-で指定してください"
        )
    if any(secret and secret in name for secret in (sensitive_values or set())):
        raise WorkflowBusinessEventError("Secret値をevent名へ使用できません")
    return name


def prepare_payload(value: Any, sensitive_values: set[str]) -> tuple[dict[str, Any], str, int]:
    if not isinstance(value, dict):
        raise WorkflowBusinessEventError("event payloadはJSON objectにしてください")
    sensitive = collect_sensitive_values(value)
    sensitive.update(item for item in sensitive_values if item)
    safe = redact(value, sensitive_values=sensitive)
    try:
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowBusinessEventError("event payloadは有限値を持つJSON objectにしてください") from exc
    size = len(encoded.encode("utf-8"))
    if size > MAX_EVENT_PAYLOAD_BYTES:
        raise WorkflowBusinessEventError("event payloadは64KiB以内にしてください")
    return safe, encoded, size


def _validated_lineage(value: Any, source_workflow_id: int) -> list[int]:
    lineage: list[int] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            try:
                workflow_id = int(item)
            except (TypeError, ValueError):
                continue
            if workflow_id > 0 and workflow_id not in lineage:
                lineage.append(workflow_id)
    if source_workflow_id not in lineage:
        lineage.append(source_workflow_id)
    return lineage[-MAX_EVENT_HOPS:]


def _prune_completed() -> int:
    cutoff = utcnow() - EVENT_RETENTION
    with SessionLocal() as db:
        event_ids = list(db.scalars(select(WorkflowBusinessEvent.id).where(
            WorkflowBusinessEvent.created_at < cutoff,
            WorkflowBusinessEvent.status.in_(("DISPATCHED", "PARTIAL_FAILED", "FAILED")),
        ).limit(1000)))
        if not event_ids:
            return 0
        db.execute(delete(WorkflowEventDelivery).where(
            WorkflowEventDelivery.business_event_id.in_(event_ids),
        ))
        db.execute(delete(WorkflowBusinessEvent).where(WorkflowBusinessEvent.id.in_(event_ids)))
        db.commit()
        return len(event_ids)


def _persist_event(
    *, event_name: str, payload_json: str, payload_size: int,
    source_workflow_id: int, source_execution_id: int, source_node_id: str,
    lineage: list[int], hop: int, targets: list[int],
) -> int:
    with SessionLocal() as db:
        count = int(db.scalar(select(func.count()).select_from(WorkflowBusinessEvent)) or 0)
        if count >= MAX_OUTBOX_EVENTS:
            raise WorkflowBusinessEventError("event outboxは10,000件上限に達しています")
        row = WorkflowBusinessEvent(
            event_id=str(uuid.uuid4()), event_name=event_name,
            source_workflow_id=source_workflow_id, source_execution_id=source_execution_id,
            source_node_id=source_node_id[:64], payload_json=payload_json,
            payload_size_bytes=payload_size, lineage_json=json.dumps(lineage),
            hop=hop, status="PENDING",
        )
        db.add(row)
        db.flush()
        for target_id in targets:
            db.add(WorkflowEventDelivery(
                business_event_id=row.id, target_workflow_id=target_id, status="PENDING",
            ))
        audit.record(
            db, "workflow.event_emit", username="workflow-engine",
            resource_type="workflow", resource_id=str(source_workflow_id),
            metadata={
                "event_id": row.event_id, "event_name": event_name,
                "source_execution_id": source_execution_id,
                "source_node_id": source_node_id[:64], "target_count": len(targets),
                "hop": hop,
            },
        )
        return row.id


def _event_snapshot(event_db_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with SessionLocal() as db:
        event = db.get(WorkflowBusinessEvent, event_db_id)
        if event is None:
            raise WorkflowBusinessEventError("event outboxが見つかりません")
        deliveries = db.execute(select(WorkflowEventDelivery).where(
            WorkflowEventDelivery.business_event_id == event.id,
        ).order_by(WorkflowEventDelivery.id)).scalars().all()
        envelope = {
            "event_source": "workflow", "event_name": event.event_name,
            "event_id": event.event_id, "data": json.loads(event.payload_json),
            "source_workflow_id": event.source_workflow_id,
            "source_execution_id": event.source_execution_id,
            "source_node_id": event.source_node_id,
            "emitted_at": event.created_at.isoformat(),
        }
        delivery_rows = [{
            "id": row.id, "target_workflow_id": row.target_workflow_id,
            "status": row.status, "attempts": row.attempts,
            "target_execution_id": row.target_execution_id,
        } for row in deliveries]
        return {
            "id": event.id, "event_id": event.event_id, "event_name": event.event_name,
            "source_execution_id": event.source_execution_id,
            "lineage": json.loads(event.lineage_json or "[]"), "hop": event.hop,
            "status": event.status, "envelope": envelope,
        }, delivery_rows


def _mark_delivering(delivery_id: int) -> tuple[bool, int]:
    with SessionLocal() as db:
        row = db.get(WorkflowEventDelivery, delivery_id)
        if row is None or row.status == "DISPATCHED" or row.status == "FAILED":
            return False, int(row.attempts if row else 0)
        row.status = "DELIVERING"
        row.attempts += 1
        row.updated_at = utcnow()
        db.commit()
        return True, row.attempts


def _mark_delivery_result(
    delivery_id: int, *, execution_id: int | None = None, error_type: str = "", attempts: int,
) -> None:
    with SessionLocal() as db:
        row = db.get(WorkflowEventDelivery, delivery_id)
        if row is None:
            return
        if execution_id is not None:
            row.status = "DISPATCHED"
            row.target_execution_id = execution_id
            row.last_error = ""
        else:
            row.status = "FAILED" if attempts >= MAX_DELIVERY_ATTEMPTS else "PENDING"
            # Provider／definition由来の例外本文はSecretを含み得る。永続化するのは型だけ。
            row.last_error = str(error_type or "DeliveryError")[:128]
        row.updated_at = utcnow()
        event = db.get(WorkflowBusinessEvent, row.business_event_id)
        audit.record(
            db, "workflow.event_deliver", username="workflow-engine",
            resource_type="workflow", resource_id=str(row.target_workflow_id),
            result="success" if execution_id is not None else ("failure" if row.status == "FAILED" else "retry"),
            metadata={
                "event_id": event.event_id if event else "",
                "event_name": event.event_name if event else "",
                "target_workflow_id": row.target_workflow_id,
                "target_execution_id": execution_id,
                "attempt": attempts, "delivery_status": row.status,
                "error_type": row.last_error,
            },
        )


def _finalize_event(event_db_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        event = db.get(WorkflowBusinessEvent, event_db_id)
        if event is None:
            raise WorkflowBusinessEventError("event outboxが見つかりません")
        rows = db.execute(select(WorkflowEventDelivery).where(
            WorkflowEventDelivery.business_event_id == event.id,
        ).order_by(WorkflowEventDelivery.id)).scalars().all()
        dispatched = [row for row in rows if row.status == "DISPATCHED"]
        pending = [row for row in rows if row.status in {"PENDING", "DELIVERING"}]
        failed = [row for row in rows if row.status == "FAILED"]
        if not rows or len(dispatched) == len(rows):
            event.status = "DISPATCHED"
            event.dispatched_at = utcnow()
        elif pending:
            event.status = "PARTIAL"
        elif dispatched:
            event.status = "PARTIAL_FAILED"
            event.dispatched_at = utcnow()
        else:
            event.status = "FAILED"
            event.dispatched_at = utcnow()
        db.commit()
        return {
            "event_id": event.event_id, "event_name": event.event_name,
            "status": event.status, "target_count": len(rows),
            "delivered_count": len(dispatched), "failed_count": len(failed),
            "execution_ids": [row.target_execution_id for row in dispatched if row.target_execution_id],
            "failed_workflow_ids": [row.target_workflow_id for row in failed],
            "durable": True,
        }


async def dispatch_event(event_db_id: int) -> dict[str, Any]:
    """Launch every unresolved delivery. A crash may retry with the same event_id."""
    from app.workflows import engine

    async with _dispatch_lock:
        event, deliveries = await asyncio.to_thread(_event_snapshot, event_db_id)
        for delivery in deliveries:
            if delivery["status"] in {"DISPATCHED", "FAILED"}:
                continue
            should_run, attempts = await asyncio.to_thread(_mark_delivering, delivery["id"])
            if not should_run:
                continue
            try:
                execution_id = await engine.run_workflow(
                    delivery["target_workflow_id"], trigger_type="event:workflow",
                    input_data=event["envelope"], published_only=True,
                    event_lineage=event["lineage"], event_hop=event["hop"],
                )
            except Exception as exc:
                logger.warning(
                    "business event delivery failed: event=%s target=%s attempt=%s error=%s",
                    event["event_id"], delivery["target_workflow_id"], attempts, type(exc).__name__,
                )
                await asyncio.to_thread(
                    _mark_delivery_result, delivery["id"], error_type=type(exc).__name__,
                    attempts=attempts,
                )
            else:
                await asyncio.to_thread(
                    _mark_delivery_result, delivery["id"], execution_id=execution_id,
                    attempts=attempts,
                )
        result = await asyncio.to_thread(_finalize_event, event_db_id)
        await asyncio.to_thread(
            engine._emit_event, event["source_execution_id"], "workflow.event_emitted",
            payload={
                "event_id": result["event_id"], "event_name": result["event_name"],
                "status": result["status"], "target_count": result["target_count"],
                "delivered_count": result["delivered_count"],
            },
        )
        return result


async def emit_event(
    *, event_name: Any, payload: Any, source_workflow_id: int,
    source_execution_id: int, source_node_id: str, lineage: Any = None,
    current_hop: int = 0, sensitive_values: set[str] | None = None,
) -> dict[str, Any]:
    from app.workflows import engine

    if source_workflow_id <= 0 or source_execution_id <= 0:
        raise WorkflowBusinessEventError("Workflow実行内でのみeventを発行できます")
    safe_values = sensitive_values or set()
    name = validate_event_name(event_name, safe_values)
    _safe_payload, payload_json, payload_size = prepare_payload(payload, safe_values)
    event_lineage = _validated_lineage(lineage, source_workflow_id)
    hop = int(current_hop or 0) + 1
    if hop > MAX_EVENT_HOPS:
        raise WorkflowBusinessEventError(f"event連鎖は{MAX_EVENT_HOPS} hopまでです")
    targets, _skipped = await asyncio.to_thread(
        engine.event_trigger_targets, "workflow", {"event_name": name}, event_lineage,
    )
    if len(targets) > MAX_EVENT_TARGETS:
        raise WorkflowBusinessEventError(f"event subscriberは{MAX_EVENT_TARGETS}件までです")
    await asyncio.to_thread(_prune_completed)
    event_db_id = await asyncio.to_thread(
        _persist_event, event_name=name, payload_json=payload_json,
        payload_size=payload_size, source_workflow_id=source_workflow_id,
        source_execution_id=source_execution_id, source_node_id=source_node_id,
        lineage=event_lineage, hop=hop, targets=targets,
    )
    return await dispatch_event(event_db_id)


async def dispatch_pending_events_once() -> int:
    with SessionLocal() as db:
        event_ids = list(db.scalars(select(WorkflowBusinessEvent.id).where(
            WorkflowBusinessEvent.status.in_(("PENDING", "PARTIAL")),
        ).order_by(WorkflowBusinessEvent.id).limit(100)))
    for event_id in event_ids:
        try:
            await dispatch_event(event_id)
        except Exception:
            logger.exception("pending business event dispatch failed: event_db_id=%s", event_id)
    await asyncio.to_thread(_prune_completed)
    return len(event_ids)


async def delivery_loop() -> None:
    while True:
        try:
            await dispatch_pending_events_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("business event delivery loop failed")
        await asyncio.sleep(5)
