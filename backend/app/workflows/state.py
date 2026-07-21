"""Workflow-scoped durable typed state with optimistic concurrency."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.audit import service as audit
from app.database import SessionLocal
from app.models import WorkflowStateEntry, utcnow
from app.workflows.redaction import redact

STATE_NAMESPACE_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}\Z")
STATE_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
VALUE_TYPES = {"auto", "string", "number", "integer", "boolean", "object", "array"}
MAX_STATE_ENTRIES_PER_WORKFLOW = 10_000
MAX_STATE_PAYLOAD_BYTES = 256 * 1024


class WorkflowStateError(ValueError):
    pass


class WorkflowStateConflict(WorkflowStateError):
    pass


class _ConcurrentWrite(RuntimeError):
    pass


def validate_namespace(value: Any) -> str:
    namespace = str(value or "").strip()
    if not STATE_NAMESPACE_RE.fullmatch(namespace):
        raise WorkflowStateError("state namespaceは英字で始まる1〜64文字の英数字・._-で指定してください")
    return namespace


def validate_key(value: Any, sensitive_values: set[str]) -> str:
    key = str(value or "").strip()
    if not STATE_KEY_RE.fullmatch(key):
        raise WorkflowStateError("state keyは1〜128文字の英数字・._:/-で指定してください")
    if any(secret and secret in key for secret in sensitive_values):
        raise WorkflowStateError("Secret値をstate keyへ使用できません")
    return key


def parse_expected_version(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise WorkflowStateError("expected versionは0以上の整数で指定してください")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowStateError("expected versionは0以上の整数で指定してください") from exc
    if version < 0 or str(value).strip() not in {str(version), f"+{version}"}:
        raise WorkflowStateError("expected versionは0以上の整数で指定してください")
    return version


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    raise WorkflowStateError("state valueはnull以外のJSON string/number/integer/boolean/object/arrayにしてください")


def _validate_type(value: Any, value_type: str) -> None:
    valid = {
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }
    if not valid.get(value_type, False):
        raise WorkflowStateError(f"state valueは{value_type}型にしてください")


def _serialized_payload(
    value: Any, configured_type: str, sensitive_values: set[str], existing_type: str | None = None,
) -> tuple[str, int, Any, str]:
    requested_type = str(configured_type or "auto").strip().lower()
    if requested_type not in VALUE_TYPES:
        raise WorkflowStateError("state value typeが不正です")
    safe = redact(value, sensitive_values=sensitive_values)
    target_type = existing_type or (_infer_type(safe) if requested_type == "auto" else requested_type)
    if existing_type is not None and requested_type != "auto" and requested_type != existing_type:
        raise WorkflowStateError(
            f"stateの型は{existing_type}で固定されています。型を変更するには一度deleteしてください"
        )
    _validate_type(safe, target_type)
    try:
        payload = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowStateError("state valueは有限値を持つJSONとして保存してください") from exc
    size = len(payload.encode("utf-8"))
    if size > MAX_STATE_PAYLOAD_BYTES:
        raise WorkflowStateError("state valueは256KiB以内にしてください")
    return payload, size, safe, target_type


def _check_expected(row: WorkflowStateEntry | None, expected: int | None) -> None:
    if expected is None:
        return
    current = row.version if row is not None else 0
    if current != expected:
        raise WorkflowStateConflict(
            f"state versionが競合しました（expected={expected}, current={current}）"
        )


def _operate_once(
    *, workflow_id: int, execution_id: int | None, node_id: str, operation: str,
    namespace: str, key: str, value: Any = None, value_type: str = "auto",
    expected_version: Any = None, delta: Any = 1,
    sensitive_values: set[str] | None = None,
) -> dict[str, Any]:
    if workflow_id <= 0:
        raise WorkflowStateError("Workflow実行内でのみstateを使用できます")
    op = str(operation or "").strip().lower()
    if op not in {"get", "set", "delete", "increment"}:
        raise WorkflowStateError("state operationはget/set/delete/incrementから選択してください")
    safe_values = sensitive_values or set()
    safe_namespace = validate_namespace(namespace)
    safe_key = validate_key(key, safe_values)
    expected = parse_expected_version(expected_version)
    now = utcnow()

    with SessionLocal() as db:
        row = db.execute(select(WorkflowStateEntry).where(
            WorkflowStateEntry.workflow_id == workflow_id,
            WorkflowStateEntry.namespace == safe_namespace,
            WorkflowStateEntry.state_key == safe_key,
        )).scalar_one_or_none()
        _check_expected(row, expected)

        stored = False
        deleted = False
        result_value: Any = None
        result_type: str | None = None
        version = row.version if row is not None else 0
        payload_size = row.payload_size_bytes if row is not None else 0
        created_at = row.created_at.isoformat() if row is not None else None
        updated_at = row.updated_at.isoformat() if row is not None else None

        if op == "get":
            if row is not None:
                result_value = json.loads(row.payload_json)
                result_type = row.value_type
        elif op == "set":
            payload, payload_size, result_value, result_type = _serialized_payload(
                value, value_type, safe_values, row.value_type if row is not None else None,
            )
            if row is None:
                count = int(db.scalar(select(func.count()).select_from(WorkflowStateEntry).where(
                    WorkflowStateEntry.workflow_id == workflow_id,
                )) or 0)
                if count >= MAX_STATE_ENTRIES_PER_WORKFLOW:
                    raise WorkflowStateError("Workflow stateは10,000 key上限に達しています")
                row = WorkflowStateEntry(
                    workflow_id=workflow_id, namespace=safe_namespace, state_key=safe_key,
                    value_type=result_type, payload_json=payload, payload_size_bytes=payload_size,
                    version=1, written_by_execution_id=execution_id,
                )
                db.add(row)
                db.flush()
                version = 1
                created_at = row.created_at.isoformat()
                updated_at = row.updated_at.isoformat()
            else:
                previous_version = row.version
                changed = db.execute(update(WorkflowStateEntry).where(
                    WorkflowStateEntry.id == row.id,
                    WorkflowStateEntry.version == previous_version,
                ).values(
                    payload_json=payload, payload_size_bytes=payload_size,
                    written_by_execution_id=execution_id, version=previous_version + 1,
                    updated_at=now,
                ))
                if changed.rowcount != 1:
                    raise _ConcurrentWrite
                version = previous_version + 1
                updated_at = now.isoformat()
            stored = True
        elif op == "increment":
            if row is None:
                raise WorkflowStateError("increment対象のstateが見つかりません。先にsetしてください")
            if row.value_type not in {"integer", "number"}:
                raise WorkflowStateError("increment対象はintegerまたはnumber型にしてください")
            if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                try:
                    delta = float(str(delta))
                except (TypeError, ValueError) as exc:
                    raise WorkflowStateError("increment deltaは数値で指定してください") from exc
            if row.value_type == "integer" and (not isinstance(delta, int) or isinstance(delta, bool)):
                raise WorkflowStateError("integer stateのdeltaは整数にしてください")
            current = json.loads(row.payload_json)
            result_value = current + delta
            payload, payload_size, result_value, result_type = _serialized_payload(
                result_value, row.value_type, safe_values, row.value_type,
            )
            previous_version = row.version
            changed = db.execute(update(WorkflowStateEntry).where(
                WorkflowStateEntry.id == row.id,
                WorkflowStateEntry.version == previous_version,
            ).values(
                payload_json=payload, payload_size_bytes=payload_size,
                written_by_execution_id=execution_id, version=previous_version + 1,
                updated_at=now,
            ))
            if changed.rowcount != 1:
                raise _ConcurrentWrite
            version = previous_version + 1
            updated_at = now.isoformat()
            stored = True
        elif op == "delete" and row is not None:
            removed = db.execute(delete(WorkflowStateEntry).where(
                WorkflowStateEntry.id == row.id,
                WorkflowStateEntry.version == row.version,
            ))
            if removed.rowcount != 1:
                raise _ConcurrentWrite
            deleted = True
            version = row.version

        if op in {"set", "increment", "delete"}:
            audit.record(
                db, f"workflow.state_{op}", username="workflow-engine",
                resource_type="workflow", resource_id=str(workflow_id),
                metadata={
                    "execution_id": execution_id, "node_id": node_id[:64],
                    "namespace": safe_namespace, "key": safe_key,
                    "entry_id": row.id if row is not None else None,
                    "version": version, "value_type": result_type or (row.value_type if row else None),
                    "deleted": deleted,
                },
            )

    return {
        "operation": op, "namespace": safe_namespace, "key": safe_key,
        "found": row is not None if op != "delete" else deleted,
        "value": result_value, "value_type": result_type,
        "version": version, "payload_size_bytes": payload_size,
        "stored": stored, "deleted": deleted,
        "created_at": created_at, "updated_at": updated_at,
    }


def operate(
    *, workflow_id: int, execution_id: int | None, node_id: str, operation: str,
    namespace: str, key: str, value: Any = None, value_type: str = "auto",
    expected_version: Any = None, delta: Any = 1,
    sensitive_values: set[str] | None = None,
) -> dict[str, Any]:
    """Retry internal CAS/SQLite contention; explicit expected-version conflicts fail immediately."""
    for attempt in range(7):
        try:
            return _operate_once(
                workflow_id=workflow_id, execution_id=execution_id, node_id=node_id,
                operation=operation, namespace=namespace, key=key, value=value,
                value_type=value_type, expected_version=expected_version, delta=delta,
                sensitive_values=sensitive_values,
            )
        except (IntegrityError, _ConcurrentWrite):
            if attempt == 6:
                raise WorkflowStateError("stateの同時更新が競合しました。再試行してください")
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 6:
                raise WorkflowStateError("stateを更新できませんでした") from exc
        time.sleep(0.005 * (attempt + 1))
    raise WorkflowStateError("stateを更新できませんでした")
