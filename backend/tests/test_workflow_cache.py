from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import time

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AuditLog, WorkflowCacheEntry, utcnow
from app.workflows import cache as workflow_cache

CSRF = {"X-Requested-With": "ControlDeck"}


def _definition(operation: str, *, key: str = "answer", value=None, ttl_seconds: int = 3600) -> dict:
    config: dict = {"operation": operation, "namespace": "runtime", "key": key}
    if operation == "set":
        config.update(value=value, ttl_seconds=ttl_seconds)
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "cache", "type": "data.cache", "config": config},
            {"id": "out", "type": "flow.return", "config": {
                "name": "result", "renderer": "json", "value": "{{cache.value}}",
            }},
        ],
        "edges": [{"source": "t", "target": "cache"}, {"source": "cache", "target": "out"}],
    }


def _wait(client, execution_id: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = client.get(f"/api/v1/workflow-executions/{execution_id}/live").json()
        if latest["status"] not in {"RUNNING", "QUEUED", "WAITING"}:
            return latest
        time.sleep(0.03)
    raise AssertionError(f"cache execution did not finish: {latest}")


def _run(client, workflow_id: int) -> dict:
    response = client.post(f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF)
    assert response.status_code == 200, response.text
    result = _wait(client, response.json()["execution_id"])
    assert result["status"] == "SUCCEEDED", result
    return result["context"]["cache"]["output"]


def _patch(client, workflow_id: int, definition: dict) -> None:
    response = client.patch(
        f"/api/v1/workflows/{workflow_id}", json={"definition": definition}, headers=CSRF,
    )
    assert response.status_code == 200, response.text
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF)
    assert published.status_code == 200, published.text


def test_durable_cache_set_get_overwrite_expire_scope_and_workflow_cleanup(admin_client):
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "durable cache", "definition": _definition("set", value={"version": 1})},
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF).status_code == 200

    stored = _run(admin_client, workflow_id)
    assert stored["stored"] is True and stored["size"] == 1
    assert stored["value"] == {"version": 1} and stored["expires_at"]

    _patch(admin_client, workflow_id, _definition("set", value={"version": 2}))
    overwritten = _run(admin_client, workflow_id)
    assert overwritten["value"] == {"version": 2} and overwritten["size"] == 1
    _patch(admin_client, workflow_id, _definition("get"))
    fetched = _run(admin_client, workflow_id)
    assert fetched["found"] is True and fetched["value"] == {"version": 2}

    other = admin_client.post(
        "/api/v1/workflows",
        json={"name": "isolated cache", "definition": _definition("get")}, headers=CSRF,
    ).json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{other}/publish", headers=CSRF).status_code == 200
    isolated = _run(admin_client, other)
    assert isolated["found"] is False and isolated["value"] is None

    with SessionLocal() as db:
        entry = db.execute(select(WorkflowCacheEntry).where(
            WorkflowCacheEntry.workflow_id == workflow_id,
        )).scalar_one()
        entry.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    expired = _run(admin_client, workflow_id)
    assert expired["found"] is False and expired["value"] is None and expired["size"] == 0

    workflow_cache.operate(
        workflow_id=workflow_id, execution_id=None, node_id="seed", operation="set",
        namespace="runtime", key="cleanup", value={"left": True}, ttl_seconds=60,
    )
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowCacheEntry).where(
            WorkflowCacheEntry.workflow_id == workflow_id,
        )) == 0


def test_cache_delete_size_secret_safety_and_bounded_validation(admin_client):
    workflow_id = admin_client.post(
        "/api/v1/workflows",
        json={"name": "cache safety", "definition": _definition("get")}, headers=CSRF,
    ).json()["id"]
    secret = "cache-secret-must-not-persist"
    stored = workflow_cache.operate(
        workflow_id=workflow_id, execution_id=None, node_id="seed", operation="set",
        namespace="safe", key="token", value={"token": secret}, ttl_seconds=60,
        sensitive_values={secret},
    )
    assert stored["value"] == {"token": "***"}
    with SessionLocal() as db:
        row = db.execute(select(WorkflowCacheEntry).where(
            WorkflowCacheEntry.workflow_id == workflow_id,
        )).scalar_one()
        assert secret not in row.payload_json and json.loads(row.payload_json) == {"token": "***"}
        audit_row = db.execute(select(AuditLog).where(
            AuditLog.action == "workflow.cache_set",
            AuditLog.resource_id == str(workflow_id),
        ).order_by(AuditLog.id.desc())).scalars().first()
        assert audit_row is not None and secret not in audit_row.metadata_json

    assert workflow_cache.operate(
        workflow_id=workflow_id, execution_id=None, node_id="size", operation="size",
        namespace="safe",
    )["size"] == 1
    deleted = workflow_cache.operate(
        workflow_id=workflow_id, execution_id=None, node_id="delete", operation="delete",
        namespace="safe", key="token",
    )
    assert deleted["deleted"] is True and deleted["size"] == 0
    assert workflow_cache.operate(
        workflow_id=workflow_id, execution_id=None, node_id="delete", operation="delete",
        namespace="safe", key="token",
    )["deleted"] is False

    with pytest.raises(workflow_cache.WorkflowCacheError, match="Secret値"):
        workflow_cache.operate(
            workflow_id=workflow_id, execution_id=None, node_id="unsafe", operation="get",
            namespace="safe", key=f"prefix:{secret}", sensitive_values={secret},
        )
    with pytest.raises(workflow_cache.WorkflowCacheError, match="1秒〜30日"):
        workflow_cache.operate(
            workflow_id=workflow_id, execution_id=None, node_id="ttl", operation="set",
            namespace="safe", key="ttl", value=True, ttl_seconds=0,
        )
    with pytest.raises(workflow_cache.WorkflowCacheError, match="256KiB"):
        workflow_cache.operate(
            workflow_id=workflow_id, execution_id=None, node_id="large", operation="set",
            namespace="safe", key="large", value="x" * (256 * 1024), ttl_seconds=60,
        )


def test_concurrent_cache_set_keeps_one_valid_entry(admin_client):
    workflow_id = admin_client.post(
        "/api/v1/workflows",
        json={"name": "cache concurrency", "definition": _definition("get")}, headers=CSRF,
    ).json()["id"]

    def set_value(value: int) -> dict:
        return workflow_cache.operate(
            workflow_id=workflow_id, execution_id=None, node_id=f"writer-{value}",
            operation="set", namespace="concurrent", key="shared",
            value={"value": value}, ttl_seconds=60,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(set_value, range(32)))
    assert len(results) == 32 and all(item["stored"] for item in results)
    current = workflow_cache.operate(
        workflow_id=workflow_id, execution_id=None, node_id="read", operation="get",
        namespace="concurrent", key="shared",
    )
    assert current["found"] is True and current["value"]["value"] in range(32)
    assert current["size"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowCacheEntry).where(
            WorkflowCacheEntry.workflow_id == workflow_id,
            WorkflowCacheEntry.namespace == "concurrent",
            WorkflowCacheEntry.cache_key == "shared",
        )) == 1
