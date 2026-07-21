from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditLog, WorkflowStateEntry
from app.workflows import resilience
from app.workflows.nodes import NodeError, node_control_rate_limit, node_data_batch

CSRF = {"X-Requested-With": "ControlDeck"}


def _create_workflow(client, name: str, definition: dict | None = None) -> int:
    definition = definition or {
        "nodes": [{"id": "t", "type": "trigger", "config": {"mode": "manual"}}],
        "edges": [],
    }
    response = client.post("/api/v1/workflows", json={"name": name, "definition": definition}, headers=CSRF)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _wait(client, execution_id: int) -> dict:
    deadline = time.monotonic() + 5
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = client.get(f"/api/v1/workflow-executions/{execution_id}/live").json()
        if latest["status"] not in {"RUNNING", "QUEUED", "WAITING"}:
            return latest
        time.sleep(0.02)
    raise AssertionError(latest)


def test_batch_node_preserves_order_and_validates_bounds():
    result = asyncio.run(node_data_batch({"input": [1, 2, 3, 4, 5], "batch_size": 2}, {}))
    assert result == {
        "batches": [[1, 2], [3, 4], [5]], "batch_count": 3,
        "item_count": 5, "batch_size": 2,
    }
    with pytest.raises(NodeError) as invalid:
        asyncio.run(node_data_batch({"input": {"not": "array"}, "batch_size": 2}, {}))
    assert invalid.value.code == "BATCH_INPUT_INVALID" and invalid.value.retryable is False


def test_rate_limit_is_atomic_durable_and_secret_safe(admin_client):
    workflow_id = _create_workflow(admin_client, "rate-limit-state")

    def acquire(index: int) -> dict:
        return resilience.acquire_rate_limit(
            workflow_id=workflow_id, execution_id=None, node_id=f"rate-{index}",
            scope="vendor-api", max_calls=4, window_seconds=10, now=100,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(acquire, range(16)))
    assert sum(item["acquired"] for item in results) == 4
    assert sorted(item["used"] for item in results if item["acquired"]) == [1, 2, 3, 4]
    assert all(item["retry_after_seconds"] == 10 for item in results if not item["acquired"])

    # A fresh Session/function call observes the persisted window, then a new window resets it.
    blocked = resilience.acquire_rate_limit(
        workflow_id=workflow_id, execution_id=None, node_id="blocked", scope="vendor-api",
        max_calls=4, window_seconds=10, now=109.9,
    )
    renewed = resilience.acquire_rate_limit(
        workflow_id=workflow_id, execution_id=None, node_id="renewed", scope="vendor-api",
        max_calls=4, window_seconds=10, now=110,
    )
    assert blocked["acquired"] is False
    assert renewed["acquired"] is True and renewed["used"] == 1 and renewed["remaining"] == 3

    secret = "never-store-this-scope"
    with pytest.raises(resilience.WorkflowControlError, match="Secret値"):
        resilience.acquire_rate_limit(
            workflow_id=workflow_id, execution_id=None, node_id="unsafe",
            scope=f"api-{secret}", max_calls=1, window_seconds=1, sensitive_values={secret},
        )
    with SessionLocal() as db:
        rows = db.execute(select(WorkflowStateEntry).where(
            WorkflowStateEntry.workflow_id == workflow_id,
            WorkflowStateEntry.namespace == resilience.NAMESPACE,
        )).scalars().all()
        audits = db.execute(select(AuditLog).where(
            AuditLog.resource_id == str(workflow_id), AuditLog.action == "workflow.rate_limit",
        )).scalars().all()
        assert len(rows) == 1 and rows[0].state_key == "rate/vendor-api"
        assert audits and secret not in "".join(item.metadata_json for item in audits)

    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF).status_code == 200
    with SessionLocal() as db:
        assert db.execute(select(WorkflowStateEntry).where(
            WorkflowStateEntry.workflow_id == workflow_id,
        )).scalars().all() == []


def test_rate_limit_node_rejects_without_retry(admin_client):
    workflow_id = _create_workflow(admin_client, "rate-limit-node")
    base = {
        "__workflow_id": workflow_id, "scope": "strict-api", "max_calls": 1,
        "window_seconds": 60, "mode": "reject", "max_wait_seconds": 0,
    }
    assert asyncio.run(node_control_rate_limit({**base, "__node_id": "first"}, {}))["acquired"] is True
    with pytest.raises(NodeError) as rejected:
        asyncio.run(node_control_rate_limit({**base, "__node_id": "second"}, {}))
    assert rejected.value.code == "RATE_LIMITED" and rejected.value.retryable is False
    assert rejected.value.details["scope"] == "strict-api"


def test_circuit_breaker_transitions_and_allows_only_one_half_open_probe(admin_client):
    workflow_id = _create_workflow(admin_client, "circuit-state")

    def operate(operation: str, now: float) -> dict:
        return resilience.operate_circuit_breaker(
            workflow_id=workflow_id, execution_id=None, node_id=operation,
            scope="vendor-api", operation=operation, failure_threshold=2,
            recovery_seconds=10, now=now,
        )

    assert operate("check", 90)["state"] == "CLOSED"
    assert operate("record_failure", 99)["state"] == "CLOSED"
    opened = operate("record_failure", 100)
    assert opened["state"] == "OPEN" and opened["consecutive_failures"] == 2
    # A success from a request that started before OPEN must not close the circuit.
    assert operate("record_success", 101)["state"] == "OPEN"
    blocked = operate("check", 105)
    assert blocked["allowed"] is False and blocked["retry_after_seconds"] == 5

    with ThreadPoolExecutor(max_workers=8) as pool:
        probes = list(pool.map(lambda _: operate("check", 110), range(12)))
    assert sum(item["allowed"] for item in probes) == 1
    assert next(item for item in probes if item["allowed"])["state"] == "HALF_OPEN"
    assert all(item["state"] == "HALF_OPEN" for item in probes)

    closed = operate("record_success", 111)
    assert closed["state"] == "CLOSED" and closed["consecutive_failures"] == 0
    assert operate("check", 112)["allowed"] is True
    operate("record_failure", 113)
    operate("record_failure", 114)
    assert operate("check", 124)["probe"] is True
    reopened = operate("record_failure", 125)
    assert reopened["state"] == "OPEN" and reopened["allowed"] is True
    assert operate("reset", 126)["state"] == "CLOSED"


def test_circuit_check_routes_allowed_and_blocked_in_real_execution(admin_client):
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "breaker", "type": "control.circuit_breaker", "config": {
                "operation": "check", "scope": "routed-api",
                "failure_threshold": 1, "recovery_seconds": 60,
            }},
            {"id": "allowed", "type": "flow.return", "config": {"name": "allowed_result", "value": "allowed"}},
            {"id": "blocked", "type": "flow.return", "config": {"name": "blocked_result", "value": "blocked"}},
        ],
        "edges": [
            {"source": "t", "target": "breaker"},
            {"source": "breaker", "target": "allowed", "branch": "allowed"},
            {"source": "breaker", "target": "blocked", "branch": "blocked"},
        ],
    }
    workflow_id = _create_workflow(admin_client, "circuit-route", definition)
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF).status_code == 200

    resilience.operate_circuit_breaker(
        workflow_id=workflow_id, execution_id=None, node_id="seed", scope="routed-api",
        operation="record_failure", failure_threshold=1, recovery_seconds=60,
    )
    started = admin_client.post(f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF)
    blocked_run = _wait(admin_client, started.json()["execution_id"])
    assert blocked_run["status"] == "SUCCEEDED"
    assert blocked_run["context"]["blocked"]["output"]["value"] == "blocked"
    assert blocked_run["context"]["allowed"]["status"] == "SKIPPED"

    resilience.operate_circuit_breaker(
        workflow_id=workflow_id, execution_id=None, node_id="reset", scope="routed-api",
        operation="reset", failure_threshold=1, recovery_seconds=60,
    )
    started = admin_client.post(f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF)
    allowed_run = _wait(admin_client, started.json()["execution_id"])
    assert allowed_run["context"]["allowed"]["output"]["value"] == "allowed"
    assert allowed_run["context"]["blocked"]["status"] == "SKIPPED"
