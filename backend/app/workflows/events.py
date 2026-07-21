"""Workflow execution event persistence and replay helpers."""
from __future__ import annotations

import json
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import WorkflowExecution, WorkflowExecutionEvent, utcnow
from app.workflows.redaction import collect_sensitive_values, redact

MAX_EVENT_PAYLOAD_BYTES = 64_000
MAX_EVENTS_PER_EXECUTION = 2_000
EVENT_RETENTION = timedelta(days=7)
MAX_REPLAY_BATCH = 200

# ControlDeck runs one workflow engine process. This lock also serializes parallel DAG
# branches so a per-execution sequence cannot be allocated twice.
_sequence_lock = threading.Lock()


def _safe_payload(payload: dict[str, Any], sensitive_values: set[str] | None = None) -> str:
    sensitive = collect_sensitive_values(payload)
    sensitive.update(value for value in (sensitive_values or set()) if value)
    safe = redact(payload, sensitive_values=sensitive)
    encoded = json.dumps(safe, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) <= MAX_EVENT_PAYLOAD_BYTES:
        return encoded
    return json.dumps(
        {"truncated": True, "original_bytes": len(encoded.encode("utf-8"))},
        ensure_ascii=False,
    )


def append_event(
    execution_id: int,
    event_type: str,
    *,
    node_id: str | None = None,
    payload: dict[str, Any] | None = None,
    sensitive_values: set[str] | None = None,
) -> int | None:
    """Persist an event and sequence atomically before it can be observed by SSE."""
    with _sequence_lock, SessionLocal() as db:
        execution = db.get(WorkflowExecution, execution_id)
        if execution is None:
            return None
        sequence = int(execution.last_event_sequence or 0) + 1
        execution.last_event_sequence = sequence
        db.add(WorkflowExecutionEvent(
            execution_id=execution_id,
            sequence=sequence,
            event_type=str(event_type)[:48],
            node_id=str(node_id)[:64] if node_id else None,
            payload_json=_safe_payload(payload or {}, sensitive_values),
        ))
        db.commit()
        if sequence % 100 == 0:
            _prune(db, execution_id, sequence)
        return sequence


def _prune(db: Session, execution_id: int, latest_sequence: int) -> None:
    sequence_floor = max(0, latest_sequence - MAX_EVENTS_PER_EXECUTION)
    cutoff = utcnow() - EVENT_RETENTION
    db.execute(delete(WorkflowExecutionEvent).where(
        WorkflowExecutionEvent.execution_id == execution_id,
        (WorkflowExecutionEvent.sequence <= sequence_floor) | (WorkflowExecutionEvent.created_at < cutoff),
    ))
    db.commit()


def serialize(row: WorkflowExecutionEvent) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {"invalid_payload": True}
    return {
        "execution_id": row.execution_id,
        "sequence": row.sequence,
        "type": row.event_type,
        "node_id": row.node_id,
        "timestamp": row.created_at,
        "payload": payload,
    }


def replay(db: Session, execution_id: int, after_sequence: int, limit: int = MAX_REPLAY_BATCH) -> dict[str, Any]:
    execution = db.get(WorkflowExecution, execution_id)
    if execution is None:
        raise LookupError(execution_id)
    latest = int(execution.last_event_sequence or 0)
    earliest = db.execute(select(func.min(WorkflowExecutionEvent.sequence)).where(
        WorkflowExecutionEvent.execution_id == execution_id,
    )).scalar_one_or_none()
    reset_required = bool(
        after_sequence > latest
        or (
            latest > after_sequence
            and (earliest is None or after_sequence + 1 < int(earliest))
        )
    )
    if reset_required:
        rows: list[WorkflowExecutionEvent] = []
    else:
        rows = list(db.execute(select(WorkflowExecutionEvent).where(
            WorkflowExecutionEvent.execution_id == execution_id,
            WorkflowExecutionEvent.sequence > max(0, after_sequence),
        ).order_by(WorkflowExecutionEvent.sequence).limit(max(1, min(limit, MAX_REPLAY_BATCH)))).scalars())
    events = [serialize(row) for row in rows]
    cursor = events[-1]["sequence"] if events else max(0, after_sequence)
    return {
        "events": events,
        "latest_sequence": latest,
        "next_sequence": cursor,
        "has_more": cursor < latest and not reset_required,
        "reset_required": reset_required,
    }
