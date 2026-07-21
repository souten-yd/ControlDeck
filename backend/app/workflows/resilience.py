"""Workflow-scoped durable rate limiting and circuit breaker state machines."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.audit import service as audit
from app.database import SessionLocal
from app.models import WorkflowStateEntry, utcnow

SCOPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}\Z")
NAMESPACE = "workflow-control"


class WorkflowControlError(ValueError):
    pass


class _ConcurrentWrite(RuntimeError):
    pass


def validate_scope(value: Any, sensitive_values: set[str] | None = None) -> str:
    scope = str(value or "").strip()
    if not SCOPE_RE.fullmatch(scope):
        raise WorkflowControlError("制御スコープは英字で始まる1〜64文字の英数字・._-で指定してください")
    if any(secret and secret in scope for secret in (sensitive_values or set())):
        raise WorkflowControlError("Secret値を制御スコープへ使用できません")
    return scope


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _json(value: dict[str, Any]) -> tuple[str, int]:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return payload, len(payload.encode("utf-8"))


def _operate(
    *, workflow_id: int, execution_id: int | None, node_id: str, key: str,
    action: str, transition: Callable[[dict[str, Any] | None], tuple[dict[str, Any] | None, dict[str, Any]]],
    scope: str,
) -> dict[str, Any]:
    """Apply a transition with optimistic locking and audit the final decision."""
    if workflow_id <= 0:
        raise WorkflowControlError("Workflow実行内でのみ実行制御を使用できます")
    for attempt in range(7):
        try:
            with SessionLocal() as db:
                row = db.execute(select(WorkflowStateEntry).where(
                    WorkflowStateEntry.workflow_id == workflow_id,
                    WorkflowStateEntry.namespace == NAMESPACE,
                    WorkflowStateEntry.state_key == key,
                )).scalar_one_or_none()
                current = json.loads(row.payload_json) if row is not None else None
                next_value, result = transition(current)
                changed = next_value is not None and next_value != current
                if changed:
                    payload, size = _json(next_value)
                    now = utcnow()
                    if row is None:
                        row = WorkflowStateEntry(
                            workflow_id=workflow_id, namespace=NAMESPACE, state_key=key,
                            value_type="object", payload_json=payload, payload_size_bytes=size,
                            version=1, written_by_execution_id=execution_id,
                        )
                        db.add(row)
                        db.flush()
                        result["version"] = 1
                    else:
                        previous_version = row.version
                        updated = db.execute(update(WorkflowStateEntry).where(
                            WorkflowStateEntry.id == row.id,
                            WorkflowStateEntry.version == previous_version,
                        ).values(
                            payload_json=payload, payload_size_bytes=size,
                            version=previous_version + 1,
                            written_by_execution_id=execution_id, updated_at=now,
                        ))
                        if updated.rowcount != 1:
                            raise _ConcurrentWrite
                        result["version"] = previous_version + 1
                else:
                    result["version"] = row.version if row is not None else 0
                audit.record(
                    db, action, username="workflow-engine", resource_type="workflow",
                    resource_id=str(workflow_id),
                    result="success" if result.get("allowed", result.get("acquired", True)) else "blocked",
                    metadata={
                        "execution_id": execution_id, "node_id": node_id[:64],
                        "scope": scope, "state": result.get("state"),
                        "operation": result.get("operation"),
                    },
                )
                return result
        except (IntegrityError, _ConcurrentWrite):
            if attempt == 6:
                raise WorkflowControlError("実行制御の同時更新が競合しました。再試行してください")
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 6:
                raise WorkflowControlError("実行制御stateを更新できませんでした") from exc
        time.sleep(0.005 * (attempt + 1))
    raise WorkflowControlError("実行制御stateを更新できませんでした")


def acquire_rate_limit(
    *, workflow_id: int, execution_id: int | None, node_id: str, scope: Any,
    max_calls: int, window_seconds: float, sensitive_values: set[str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    safe_scope = validate_scope(scope, sensitive_values)
    if max_calls < 1 or max_calls > 10_000:
        raise WorkflowControlError("最大実行数は1〜10,000にしてください")
    if window_seconds < 0.1 or window_seconds > 86_400:
        raise WorkflowControlError("時間窓は0.1秒〜24時間にしてください")
    timestamp = time.time() if now is None else now

    def transition(current: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        started = float((current or {}).get("window_started_at", timestamp))
        used = int((current or {}).get("used", 0))
        if timestamp >= started + window_seconds:
            started, used = timestamp, 0
        reset_at = started + window_seconds
        if used >= max_calls:
            return None, {
                "acquired": False, "scope": safe_scope, "used": used, "remaining": 0,
                "max_calls": max_calls, "window_seconds": window_seconds,
                "reset_at": _iso(reset_at), "retry_after_seconds": max(0.0, reset_at - timestamp),
            }
        used += 1
        state = {"window_started_at": started, "used": used}
        return state, {
            "acquired": True, "scope": safe_scope, "used": used,
            "remaining": max_calls - used, "max_calls": max_calls,
            "window_seconds": window_seconds, "reset_at": _iso(reset_at),
            "retry_after_seconds": 0.0,
        }

    return _operate(
        workflow_id=workflow_id, execution_id=execution_id, node_id=node_id,
        key=f"rate/{safe_scope}", action="workflow.rate_limit", transition=transition, scope=safe_scope,
    )


def operate_circuit_breaker(
    *, workflow_id: int, execution_id: int | None, node_id: str, scope: Any,
    operation: str, failure_threshold: int, recovery_seconds: float,
    sensitive_values: set[str] | None = None, now: float | None = None,
) -> dict[str, Any]:
    safe_scope = validate_scope(scope, sensitive_values)
    op = str(operation or "check").strip().lower()
    if op not in {"check", "record_success", "record_failure", "status", "reset"}:
        raise WorkflowControlError("回路遮断操作はcheck/record_success/record_failure/status/resetから選択してください")
    if failure_threshold < 1 or failure_threshold > 1_000:
        raise WorkflowControlError("失敗しきい値は1〜1,000にしてください")
    if recovery_seconds < 0.1 or recovery_seconds > 604_800:
        raise WorkflowControlError("回復待機は0.1秒〜7日にしてください")
    timestamp = time.time() if now is None else now

    def transition(current: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        state = str((current or {}).get("state", "CLOSED"))
        failures = max(0, int((current or {}).get("consecutive_failures", 0)))
        opened_at = (current or {}).get("opened_at")
        probe_leased_at = (current or {}).get("probe_leased_at")
        allowed = True
        probe = False
        retry_at: float | None = None

        if op == "check":
            if state == "OPEN":
                retry_at = float(opened_at or timestamp) + recovery_seconds
                if timestamp >= retry_at:
                    state, probe, probe_leased_at, retry_at = "HALF_OPEN", True, timestamp, None
                else:
                    allowed = False
            elif state == "HALF_OPEN":
                lease_until = float(probe_leased_at or timestamp) + recovery_seconds
                if timestamp >= lease_until:
                    probe, probe_leased_at = True, timestamp
                else:
                    allowed, retry_at = False, lease_until
        elif op == "record_failure":
            failures += 1
            if state == "HALF_OPEN" or failures >= failure_threshold:
                state, opened_at, probe_leased_at = "OPEN", timestamp, None
                failures = max(failures, failure_threshold)
        elif op == "record_success":
            # OPEN中に遅れて到着した旧requestの成功で回路を閉じない。回復は
            # HALF_OPENとしてleaseされたprobeの成功、または明示resetだけが行う。
            if state != "OPEN":
                state, failures, opened_at, probe_leased_at = "CLOSED", 0, None, None
        elif op == "reset":
            state, failures, opened_at, probe_leased_at = "CLOSED", 0, None, None

        next_value = {
            "state": state, "consecutive_failures": failures,
            "opened_at": opened_at, "probe_leased_at": probe_leased_at,
        }
        result = {
            "operation": op, "scope": safe_scope, "allowed": allowed, "probe": probe,
            "state": state, "consecutive_failures": failures,
            "failure_threshold": failure_threshold, "recovery_seconds": recovery_seconds,
            "retry_at": _iso(retry_at) if retry_at is not None else None,
            "retry_after_seconds": max(0.0, retry_at - timestamp) if retry_at is not None else 0.0,
        }
        return (next_value if next_value != (current or {}) else None), result

    return _operate(
        workflow_id=workflow_id, execution_id=execution_id, node_id=node_id,
        key=f"circuit/{safe_scope}", action="workflow.circuit_breaker",
        transition=transition, scope=safe_scope,
    )
