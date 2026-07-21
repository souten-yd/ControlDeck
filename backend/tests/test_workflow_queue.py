from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import WorkflowQueueItem
from app.workflows import queue as workflow_queue

CSRF = {"X-Requested-With": "ControlDeck"}


def _definition(operation: str, value=None) -> dict:
    config = {"operation": operation, "queue": "jobs"}
    if operation == "enqueue":
        config["value"] = value
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "q", "type": "data.queue", "config": config},
            {"id": "out", "type": "flow.return", "config": {
                "name": "result", "renderer": "json", "value": "{{q.value}}",
            }},
        ],
        "edges": [{"source": "t", "target": "q"}, {"source": "q", "target": "out"}],
    }


def _wait(client, execution_id: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = client.get(f"/api/v1/workflow-executions/{execution_id}/live").json()
        if latest["status"] not in {"RUNNING", "QUEUED", "WAITING"}:
            return latest
        time.sleep(0.03)
    raise AssertionError(f"queue execution did not finish: {latest}")


def _run(client, workflow_id: int) -> dict:
    response = client.post(f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF)
    assert response.status_code == 200, response.text
    result = _wait(client, response.json()["execution_id"])
    assert result["status"] == "SUCCEEDED", result
    return result["context"]["q"]["output"]


def _patch(client, workflow_id: int, definition: dict) -> None:
    response = client.patch(
        f"/api/v1/workflows/{workflow_id}", json={"definition": definition}, headers=CSRF,
    )
    assert response.status_code == 200, response.text
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF)
    assert published.status_code == 200, published.text


def test_durable_queue_is_fifo_workflow_scoped_and_deleted_with_workflow(admin_client):
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "durable queue", "definition": _definition("enqueue", {"order": 1})},
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF).status_code == 200

    first = _run(admin_client, workflow_id)
    assert first["enqueued"] is True and first["size"] == 1
    _patch(admin_client, workflow_id, _definition("enqueue", {"order": 2}))
    second = _run(admin_client, workflow_id)
    assert second["size"] == 2

    # 同じqueue名でも別Workflowからは見えない。
    other = admin_client.post(
        "/api/v1/workflows",
        json={"name": "isolated queue", "definition": _definition("size")}, headers=CSRF,
    ).json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{other}/publish", headers=CSRF).status_code == 200
    isolated = _run(admin_client, other)
    assert isolated["size"] == 0

    _patch(admin_client, workflow_id, _definition("dequeue"))
    popped_first = _run(admin_client, workflow_id)
    popped_second = _run(admin_client, workflow_id)
    empty = _run(admin_client, workflow_id)
    assert popped_first["value"] == {"order": 1} and popped_first["size"] == 1
    assert popped_second["value"] == {"order": 2} and popped_second["size"] == 0
    assert empty["found"] is False and empty["value"] is None

    _patch(admin_client, workflow_id, _definition("enqueue", {"left": "for cleanup"}))
    _run(admin_client, workflow_id)
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowQueueItem).where(
            WorkflowQueueItem.workflow_id == workflow_id,
        )) == 0


def test_queue_redacts_secrets_and_concurrent_dequeue_never_duplicates(admin_client):
    workflow_id = admin_client.post(
        "/api/v1/workflows",
        json={"name": "queue concurrency", "definition": _definition("size")}, headers=CSRF,
    ).json()["id"]
    secret = "queue-secret-must-not-persist"
    redacted = workflow_queue.operate(
        workflow_id=workflow_id, execution_id=None, node_id="seed", operation="enqueue",
        queue_name="secret-items", value={"note": secret}, sensitive_values={secret},
    )
    assert redacted["enqueued"] is True
    with SessionLocal() as db:
        stored = db.execute(select(WorkflowQueueItem).where(
            WorkflowQueueItem.workflow_id == workflow_id,
            WorkflowQueueItem.queue_name == "secret-items",
        )).scalar_one()
        assert secret not in stored.payload_json
        assert "***" in stored.payload_json

    for value in range(32):
        workflow_queue.operate(
            workflow_id=workflow_id, execution_id=None, node_id="seed", operation="enqueue",
            queue_name="concurrent", value={"value": value},
        )

    def dequeue_one(index: int) -> dict:
        return workflow_queue.operate(
            workflow_id=workflow_id, execution_id=None, node_id=f"worker-{index}",
            operation="dequeue", queue_name="concurrent",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(dequeue_one, range(32)))
    found = [item for item in results if item["found"]]
    assert len(found) == 32
    assert len({item["item_id"] for item in found}) == 32
    assert {item["value"]["value"] for item in found} == set(range(32))
    assert workflow_queue.operate(
        workflow_id=workflow_id, execution_id=None, node_id="size",
        operation="size", queue_name="concurrent",
    )["size"] == 0
