from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AuditLog, WorkflowStateEntry
from app.workflows import state as workflow_state

CSRF = {"X-Requested-With": "ControlDeck"}


def _definition(
    operation: str, *, key: str = "counter", value=None, value_type: str = "auto",
    expected_version=None, delta=1,
) -> dict:
    config: dict = {
        "operation": operation, "namespace": "runtime", "key": key,
        "value_type": value_type,
    }
    if operation == "set":
        config["value"] = value
    if operation == "increment":
        config["delta"] = delta
    if expected_version is not None:
        config["expected_version"] = expected_version
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "state", "type": "data.state", "config": config},
            {"id": "out", "type": "flow.return", "config": {
                "name": "result", "renderer": "json", "value": "{{state.value}}",
            }},
        ],
        "edges": [{"source": "t", "target": "state"}, {"source": "state", "target": "out"}],
    }


def _wait(client, execution_id: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = client.get(f"/api/v1/workflow-executions/{execution_id}/live").json()
        if latest["status"] not in {"RUNNING", "QUEUED", "WAITING"}:
            return latest
        time.sleep(0.03)
    raise AssertionError(f"state execution did not finish: {latest}")


def _run(client, workflow_id: int) -> dict:
    response = client.post(f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF)
    assert response.status_code == 200, response.text
    result = _wait(client, response.json()["execution_id"])
    assert result["status"] == "SUCCEEDED", result
    return result["context"]["state"]["output"]


def _patch(client, workflow_id: int, definition: dict) -> None:
    response = client.patch(
        f"/api/v1/workflows/{workflow_id}", json={"definition": definition}, headers=CSRF,
    )
    assert response.status_code == 200, response.text
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF)
    assert published.status_code == 200, published.text


def test_durable_state_is_typed_versioned_scoped_and_deleted_with_workflow(admin_client):
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "durable state", "definition": _definition("set", value=1, value_type="integer")},
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF).status_code == 200

    initial = _run(admin_client, workflow_id)
    assert initial["stored"] is True and initial["value"] == 1
    assert initial["value_type"] == "integer" and initial["version"] == 1

    _patch(admin_client, workflow_id, _definition("set", value=2, value_type="auto", expected_version=1))
    updated = _run(admin_client, workflow_id)
    assert updated["value"] == 2 and updated["version"] == 2
    _patch(admin_client, workflow_id, _definition("get"))
    fetched = _run(admin_client, workflow_id)
    assert fetched["found"] is True and fetched["value"] == 2
    assert fetched["value_type"] == "integer" and fetched["version"] == 2

    other = admin_client.post(
        "/api/v1/workflows",
        json={"name": "isolated state", "definition": _definition("get")}, headers=CSRF,
    ).json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{other}/publish", headers=CSRF).status_code == 200
    isolated = _run(admin_client, other)
    assert isolated["found"] is False and isolated["value"] is None and isolated["version"] == 0

    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowStateEntry).where(
            WorkflowStateEntry.workflow_id == workflow_id,
        )) == 0


def test_state_conflict_type_lock_delete_recreate_and_secret_safety(admin_client):
    workflow_id = admin_client.post(
        "/api/v1/workflows",
        json={"name": "state safety", "definition": _definition("get")}, headers=CSRF,
    ).json()["id"]
    secret = "state-secret-must-not-persist"
    created = workflow_state.operate(
        workflow_id=workflow_id, execution_id=None, node_id="create", operation="set",
        namespace="safe", key="profile", value={"token": secret}, value_type="object",
        expected_version=0, sensitive_values={secret},
    )
    assert created["version"] == 1 and created["value"] == {"token": "***"}

    with pytest.raises(workflow_state.WorkflowStateConflict, match="expected=0, current=1"):
        workflow_state.operate(
            workflow_id=workflow_id, execution_id=None, node_id="duplicate", operation="set",
            namespace="safe", key="profile", value={}, value_type="object", expected_version=0,
        )
    with pytest.raises(workflow_state.WorkflowStateConflict, match="expected=9, current=1"):
        workflow_state.operate(
            workflow_id=workflow_id, execution_id=None, node_id="stale", operation="delete",
            namespace="safe", key="profile", expected_version=9,
        )
    with pytest.raises(workflow_state.WorkflowStateError, match="型はobjectで固定"):
        workflow_state.operate(
            workflow_id=workflow_id, execution_id=None, node_id="wrong-type", operation="set",
            namespace="safe", key="profile", value="changed", value_type="string",
        )
    with pytest.raises(workflow_state.WorkflowStateError, match="Secret値"):
        workflow_state.operate(
            workflow_id=workflow_id, execution_id=None, node_id="unsafe-key", operation="get",
            namespace="safe", key=f"prefix:{secret}", sensitive_values={secret},
        )

    with SessionLocal() as db:
        row = db.execute(select(WorkflowStateEntry).where(
            WorkflowStateEntry.workflow_id == workflow_id,
        )).scalar_one()
        assert secret not in row.payload_json and json.loads(row.payload_json) == {"token": "***"}
        audit_row = db.execute(select(AuditLog).where(
            AuditLog.action == "workflow.state_set",
            AuditLog.resource_id == str(workflow_id),
        ).order_by(AuditLog.id.desc())).scalars().first()
        assert audit_row is not None and secret not in audit_row.metadata_json

    deleted = workflow_state.operate(
        workflow_id=workflow_id, execution_id=None, node_id="delete", operation="delete",
        namespace="safe", key="profile", expected_version=1,
    )
    assert deleted["deleted"] is True and deleted["version"] == 1
    recreated = workflow_state.operate(
        workflow_id=workflow_id, execution_id=None, node_id="recreate", operation="set",
        namespace="safe", key="profile", value="new type", value_type="string", expected_version=0,
    )
    assert recreated["value_type"] == "string" and recreated["version"] == 1

    with pytest.raises(workflow_state.WorkflowStateError, match="256KiB"):
        workflow_state.operate(
            workflow_id=workflow_id, execution_id=None, node_id="large", operation="set",
            namespace="safe", key="large", value="x" * (256 * 1024), value_type="string",
        )


def test_concurrent_state_increment_is_atomic_and_expected_version_allows_one_writer(admin_client):
    workflow_id = admin_client.post(
        "/api/v1/workflows",
        json={"name": "state concurrency", "definition": _definition("get")}, headers=CSRF,
    ).json()["id"]
    workflow_state.operate(
        workflow_id=workflow_id, execution_id=None, node_id="seed", operation="set",
        namespace="metrics", key="count", value=0, value_type="integer", expected_version=0,
    )

    def increment(index: int) -> dict:
        return workflow_state.operate(
            workflow_id=workflow_id, execution_id=None, node_id=f"worker-{index}",
            operation="increment", namespace="metrics", key="count", delta=1,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(increment, range(32)))
    assert len(results) == 32 and all(item["stored"] for item in results)
    current = workflow_state.operate(
        workflow_id=workflow_id, execution_id=None, node_id="read", operation="get",
        namespace="metrics", key="count",
    )
    assert current["value"] == 32 and current["version"] == 33

    def compare_and_set(value: int) -> str:
        try:
            workflow_state.operate(
                workflow_id=workflow_id, execution_id=None, node_id=f"cas-{value}",
                operation="set", namespace="metrics", key="count", value=value,
                value_type="integer", expected_version=33,
            )
            return "stored"
        except workflow_state.WorkflowStateConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(compare_and_set, range(8)))
    assert outcomes.count("stored") == 1 and outcomes.count("conflict") == 7
    with SessionLocal() as db:
        row = db.execute(select(WorkflowStateEntry).where(
            WorkflowStateEntry.workflow_id == workflow_id,
            WorkflowStateEntry.namespace == "metrics",
            WorkflowStateEntry.state_key == "count",
        )).scalar_one()
        assert row.version == 34
