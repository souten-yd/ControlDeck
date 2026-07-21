"""Workflow-scoped durable bounded FIFO operations."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.audit import service as audit
from app.database import SessionLocal
from app.models import WorkflowQueueItem
from app.workflows.redaction import redact

QUEUE_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}\Z")
MAX_QUEUE_ITEMS = 10_000
MAX_QUEUE_PAYLOAD_BYTES = 256 * 1024


class WorkflowQueueError(ValueError):
    pass


def validate_queue_name(value: Any) -> str:
    name = str(value or "").strip()
    if not QUEUE_NAME_RE.fullmatch(name):
        raise WorkflowQueueError("queue名は英字で始まる1〜64文字の英数字・._-で指定してください")
    return name


def _serialized_payload(value: Any, sensitive_values: set[str]) -> tuple[str, int]:
    safe = redact(value, sensitive_values=sensitive_values)
    try:
        payload = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowQueueError("queue valueはJSONとして保存できる値にしてください") from exc
    size = len(payload.encode("utf-8"))
    if size > MAX_QUEUE_PAYLOAD_BYTES:
        raise WorkflowQueueError("queue valueは256KiB以内にしてください")
    return payload, size


def _operate_once(
    *, workflow_id: int, execution_id: int | None, node_id: str, operation: str,
    queue_name: str, value: Any = None, sensitive_values: set[str] | None = None,
) -> dict[str, Any]:
    """Perform one atomic queue operation and return a bounded typed result."""
    if workflow_id <= 0:
        raise WorkflowQueueError("Workflow実行内でのみqueueを使用できます")
    name = validate_queue_name(queue_name)
    op = str(operation or "").strip().lower()
    if op not in {"enqueue", "dequeue", "peek", "size"}:
        raise WorkflowQueueError("queue operationはenqueue/dequeue/peek/sizeから選択してください")

    with SessionLocal() as db:
        item_id: int | None = None
        item_value: Any = None
        item_size = 0
        item_created_at: str | None = None
        if op == "enqueue":
            count = int(db.scalar(select(func.count()).select_from(WorkflowQueueItem).where(
                WorkflowQueueItem.workflow_id == workflow_id,
                WorkflowQueueItem.queue_name == name,
            )) or 0)
            if count >= MAX_QUEUE_ITEMS:
                raise WorkflowQueueError("queueは10,000件上限に達しています")
            payload, item_size = _serialized_payload(value, sensitive_values or set())
            last_sequence = int(db.scalar(select(func.max(WorkflowQueueItem.sequence)).where(
                WorkflowQueueItem.workflow_id == workflow_id,
                WorkflowQueueItem.queue_name == name,
            )) or 0)
            item = WorkflowQueueItem(
                workflow_id=workflow_id, queue_name=name, sequence=last_sequence + 1,
                payload_json=payload, payload_size_bytes=item_size,
                enqueued_by_execution_id=execution_id,
            )
            db.add(item)
            db.flush()
            item_id = item.id
            item_created_at = item.created_at.isoformat()
        elif op == "dequeue":
            candidate_id = select(WorkflowQueueItem.id).where(
                WorkflowQueueItem.workflow_id == workflow_id,
                WorkflowQueueItem.queue_name == name,
            ).order_by(WorkflowQueueItem.sequence, WorkflowQueueItem.id).limit(1).scalar_subquery()
            row = db.execute(
                delete(WorkflowQueueItem).where(
                    WorkflowQueueItem.id == candidate_id,
                    WorkflowQueueItem.workflow_id == workflow_id,
                ).returning(
                    WorkflowQueueItem.id, WorkflowQueueItem.payload_json,
                    WorkflowQueueItem.payload_size_bytes, WorkflowQueueItem.created_at,
                )
            ).one_or_none()
            if row is not None:
                item_id = int(row.id)
                item_value = json.loads(row.payload_json)
                item_size = int(row.payload_size_bytes)
                item_created_at = row.created_at.isoformat()
        elif op == "peek":
            item = db.execute(select(WorkflowQueueItem).where(
                WorkflowQueueItem.workflow_id == workflow_id,
                WorkflowQueueItem.queue_name == name,
            ).order_by(WorkflowQueueItem.sequence, WorkflowQueueItem.id).limit(1)).scalar_one_or_none()
            if item is not None:
                item_id = item.id
                item_value = json.loads(item.payload_json)
                item_size = item.payload_size_bytes
                item_created_at = item.created_at.isoformat()

        remaining = int(db.scalar(select(func.count()).select_from(WorkflowQueueItem).where(
            WorkflowQueueItem.workflow_id == workflow_id,
            WorkflowQueueItem.queue_name == name,
        )) or 0)
        if op in {"enqueue", "dequeue"}:
            audit.record(
                db, f"workflow.queue_{op}", username="workflow-engine",
                resource_type="workflow", resource_id=str(workflow_id),
                metadata={
                    "execution_id": execution_id, "node_id": node_id[:64],
                    "queue_name": name, "item_id": item_id, "remaining": remaining,
                },
            )

    return {
        "operation": op,
        "queue": name,
        "found": item_id is not None if op in {"dequeue", "peek"} else False,
        "item_id": item_id,
        "value": item_value,
        "payload_size_bytes": item_size,
        "enqueued_at": item_created_at,
        "size": remaining,
        "enqueued": op == "enqueue" and item_id is not None,
    }


def operate(
    *, workflow_id: int, execution_id: int | None, node_id: str, operation: str,
    queue_name: str, value: Any = None, sensitive_values: set[str] | None = None,
) -> dict[str, Any]:
    """Retry bounded SQLite write contention without duplicating committed operations."""
    for attempt in range(5):
        try:
            return _operate_once(
                workflow_id=workflow_id, execution_id=execution_id, node_id=node_id,
                operation=operation, queue_name=queue_name, value=value,
                sensitive_values=sensitive_values,
            )
        except IntegrityError:
            if attempt == 4:
                raise WorkflowQueueError("queueの同時更新が競合しました。再試行してください")
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 4:
                raise WorkflowQueueError("queueを更新できませんでした") from exc
        time.sleep(0.01 * (attempt + 1))
    raise WorkflowQueueError("queueを更新できませんでした")
