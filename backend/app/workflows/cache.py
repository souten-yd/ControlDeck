"""Workflow-scoped durable bounded TTL cache operations."""
from __future__ import annotations

from datetime import timedelta
import json
import re
import time
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.audit import service as audit
from app.database import SessionLocal
from app.models import WorkflowCacheEntry, utcnow
from app.workflows.redaction import redact

CACHE_NAMESPACE_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}\Z")
CACHE_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
MAX_CACHE_ENTRIES_PER_WORKFLOW = 10_000
MAX_CACHE_PAYLOAD_BYTES = 256 * 1024
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 30 * 24 * 60 * 60


class WorkflowCacheError(ValueError):
    pass


def validate_namespace(value: Any) -> str:
    namespace = str(value or "").strip()
    if not CACHE_NAMESPACE_RE.fullmatch(namespace):
        raise WorkflowCacheError("cache namespaceは英字で始まる1〜64文字の英数字・._-で指定してください")
    return namespace


def validate_key(value: Any, sensitive_values: set[str]) -> str:
    key = str(value or "").strip()
    if not CACHE_KEY_RE.fullmatch(key):
        raise WorkflowCacheError("cache keyは1〜128文字の英数字・._:/-で指定してください")
    if any(secret and secret in key for secret in sensitive_values):
        raise WorkflowCacheError("Secret値をcache keyへ使用できません")
    return key


def validate_ttl(value: Any) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowCacheError("cache TTLは秒数で指定してください") from exc
    if ttl < 1 or ttl > MAX_TTL_SECONDS:
        raise WorkflowCacheError("cache TTLは1秒〜30日で指定してください")
    return ttl


def _serialized_payload(value: Any, sensitive_values: set[str]) -> tuple[str, int, Any]:
    safe = redact(value, sensitive_values=sensitive_values)
    try:
        payload = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowCacheError("cache valueはJSONとして保存できる値にしてください") from exc
    size = len(payload.encode("utf-8"))
    if size > MAX_CACHE_PAYLOAD_BYTES:
        raise WorkflowCacheError("cache valueは256KiB以内にしてください")
    return payload, size, safe


def _operate_once(
    *, workflow_id: int, execution_id: int | None, node_id: str, operation: str,
    namespace: str, key: str, value: Any = None, ttl_seconds: Any = DEFAULT_TTL_SECONDS,
    sensitive_values: set[str] | None = None,
) -> dict[str, Any]:
    if workflow_id <= 0:
        raise WorkflowCacheError("Workflow実行内でのみcacheを使用できます")
    op = str(operation or "").strip().lower()
    if op not in {"set", "get", "delete", "size"}:
        raise WorkflowCacheError("cache operationはset/get/delete/sizeから選択してください")
    safe_values = sensitive_values or set()
    safe_namespace = validate_namespace(namespace)
    safe_key = "" if op == "size" else validate_key(key, safe_values)
    now = utcnow()

    with SessionLocal() as db:
        # 読み取り時も期限切れを返さず、同時に物理削除する。
        db.execute(delete(WorkflowCacheEntry).where(
            WorkflowCacheEntry.workflow_id == workflow_id,
            WorkflowCacheEntry.expires_at <= now,
        ))
        row: WorkflowCacheEntry | None = None
        stored = False
        deleted = False
        result_value: Any = None
        payload_size = 0
        expires_at: str | None = None

        if op == "set":
            ttl = validate_ttl(ttl_seconds)
            payload, payload_size, result_value = _serialized_payload(value, safe_values)
            row = db.execute(select(WorkflowCacheEntry).where(
                WorkflowCacheEntry.workflow_id == workflow_id,
                WorkflowCacheEntry.namespace == safe_namespace,
                WorkflowCacheEntry.cache_key == safe_key,
            )).scalar_one_or_none()
            expiry = now + timedelta(seconds=ttl)
            if row is None:
                count = int(db.scalar(select(func.count()).select_from(WorkflowCacheEntry).where(
                    WorkflowCacheEntry.workflow_id == workflow_id,
                )) or 0)
                if count >= MAX_CACHE_ENTRIES_PER_WORKFLOW:
                    raise WorkflowCacheError("Workflow cacheは10,000 key上限に達しています")
                row = WorkflowCacheEntry(
                    workflow_id=workflow_id, namespace=safe_namespace, cache_key=safe_key,
                    payload_json=payload, payload_size_bytes=payload_size,
                    written_by_execution_id=execution_id, expires_at=expiry,
                )
                db.add(row)
            else:
                row.payload_json = payload
                row.payload_size_bytes = payload_size
                row.written_by_execution_id = execution_id
                row.expires_at = expiry
                row.updated_at = now
            db.flush()
            stored = True
            expires_at = row.expires_at.isoformat()
        elif op == "get":
            row = db.execute(select(WorkflowCacheEntry).where(
                WorkflowCacheEntry.workflow_id == workflow_id,
                WorkflowCacheEntry.namespace == safe_namespace,
                WorkflowCacheEntry.cache_key == safe_key,
            )).scalar_one_or_none()
            if row is not None:
                result_value = json.loads(row.payload_json)
                payload_size = row.payload_size_bytes
                expires_at = row.expires_at.isoformat()
        elif op == "delete":
            removed = db.execute(delete(WorkflowCacheEntry).where(
                WorkflowCacheEntry.workflow_id == workflow_id,
                WorkflowCacheEntry.namespace == safe_namespace,
                WorkflowCacheEntry.cache_key == safe_key,
            ))
            deleted = bool(removed.rowcount)

        size = int(db.scalar(select(func.count()).select_from(WorkflowCacheEntry).where(
            WorkflowCacheEntry.workflow_id == workflow_id,
            WorkflowCacheEntry.namespace == safe_namespace,
        )) or 0)
        if op in {"set", "delete"}:
            audit.record(
                db, f"workflow.cache_{op}", username="workflow-engine",
                resource_type="workflow", resource_id=str(workflow_id),
                metadata={
                    "execution_id": execution_id, "node_id": node_id[:64],
                    "namespace": safe_namespace, "key": safe_key,
                    "entry_id": row.id if row is not None else None, "size": size,
                },
            )
        else:
            db.commit()

    return {
        "operation": op, "namespace": safe_namespace, "key": safe_key,
        "found": row is not None if op == "get" else False,
        "value": result_value, "payload_size_bytes": payload_size,
        "expires_at": expires_at, "size": size, "stored": stored, "deleted": deleted,
    }


def operate(
    *, workflow_id: int, execution_id: int | None, node_id: str, operation: str,
    namespace: str, key: str = "", value: Any = None,
    ttl_seconds: Any = DEFAULT_TTL_SECONDS, sensitive_values: set[str] | None = None,
) -> dict[str, Any]:
    """Retry bounded SQLite write contention without hiding permanent failures."""
    for attempt in range(5):
        try:
            return _operate_once(
                workflow_id=workflow_id, execution_id=execution_id, node_id=node_id,
                operation=operation, namespace=namespace, key=key, value=value,
                ttl_seconds=ttl_seconds, sensitive_values=sensitive_values,
            )
        except IntegrityError:
            if attempt == 4:
                raise WorkflowCacheError("cacheの同時更新が競合しました。再試行してください")
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 4:
                raise WorkflowCacheError("cacheを更新できませんでした") from exc
        time.sleep(0.01 * (attempt + 1))
    raise WorkflowCacheError("cacheを更新できませんでした")
