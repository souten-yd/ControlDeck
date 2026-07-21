import asyncio
from datetime import timedelta
import json
import time

import pytest
from sqlalchemy import select

from tests.conftest import CSRF_HEADERS


def _wait_execution(admin_client, execution_id: int) -> dict:
    detail = {}
    for _ in range(100):
        detail = admin_client.get(f"/api/v1/workflow-executions/{execution_id}").json()
        if detail["status"] not in ("QUEUED", "RUNNING", "WAITING"):
            return detail
        time.sleep(0.05)
    return detail


def test_execution_events_are_ordered_replayable_and_redacted(admin_client):
    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "wait", "type": "util.wait", "config": {"seconds": 0.05}},
            {"id": "result", "type": "signal.display", "config": {
                "signal": "answer", "value": "{{trigger.message}}",
            }},
        ],
        "edges": [
            {"source": "trigger", "target": "wait"},
            {"source": "wait", "target": "result"},
        ],
    }
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "event replay", "definition": definition},
        headers=CSRF_HEADERS,
    )
    workflow_id = created.json()["id"]
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/test",
        json={"input": {"message": "hello", "password": "never-persist-this"}},
        headers=CSRF_HEADERS,
    )
    execution_id = started.json()["execution_id"]

    for _ in range(80):
        detail = admin_client.get(f"/api/v1/workflow-executions/{execution_id}").json()
        if detail["status"] not in ("QUEUED", "RUNNING", "WAITING"):
            break
        time.sleep(0.05)
    assert detail["status"] == "SUCCEEDED"

    response = admin_client.get(f"/api/v1/workflow-executions/{execution_id}/events")
    assert response.status_code == 200, response.text
    body = response.json()
    sequences = [event["sequence"] for event in body["events"]]
    event_types = [event["type"] for event in body["events"]]
    assert {event["execution_id"] for event in body["events"]} == {execution_id}
    assert sequences == list(range(1, len(sequences) + 1))
    assert body["latest_sequence"] == sequences[-1]
    assert event_types[0] == "execution.started"
    assert event_types[-1] == "execution.finished"
    assert "node.started" in event_types
    assert "node.finished" in event_types
    assert "never-persist-this" not in json.dumps(body, ensure_ascii=False)

    replay = admin_client.get(
        f"/api/v1/workflow-executions/{execution_id}/events",
        params={"after_sequence": sequences[1]},
    ).json()
    assert all(event["sequence"] > sequences[1] for event in replay["events"])
    assert replay["latest_sequence"] == body["latest_sequence"]
    assert replay["has_more"] is False

    reset = admin_client.get(
        f"/api/v1/workflow-executions/{execution_id}/events",
        params={"after_sequence": body["latest_sequence"] + 100},
    ).json()
    assert reset["reset_required"] is True
    assert reset["events"] == []

    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.get(f"/api/v1/workflow-executions/{execution_id}/events").status_code == 404


def test_execution_event_endpoints_require_authentication(client):
    client.cookies.clear()
    assert client.get("/api/v1/workflow-executions/1/events").status_code == 401
    assert client.get("/api/v1/workflow-executions/1/stream").status_code == 401


def test_business_event_contract_rejects_unsafe_names_and_payloads():
    from app.workflows import business_events
    from app.workflows.validation import semantic_check

    assert business_events.validate_event_name("report.completed") == "report.completed"
    with pytest.raises(business_events.WorkflowBusinessEventError):
        business_events.validate_event_name("1invalid")
    with pytest.raises(business_events.WorkflowBusinessEventError):
        business_events.validate_event_name("secret-value.event", {"secret-value"})
    with pytest.raises(business_events.WorkflowBusinessEventError):
        business_events.prepare_payload(["object required"], set())
    with pytest.raises(business_events.WorkflowBusinessEventError):
        business_events.prepare_payload({"value": float("nan")}, set())
    with pytest.raises(business_events.WorkflowBusinessEventError):
        business_events.prepare_payload({"value": "x" * (64 * 1024)}, set())

    nodes = [
        {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
        {"id": "emit", "type": "event.emit", "config": {
            "event_name": "valid.event", "payload": "{invalid",
        }},
    ]
    errors, _warnings = semantic_check(nodes, [{"source": "trigger", "target": "emit"}])
    assert any("有効なJSON object" in error for error in errors)


def test_business_event_delivers_published_contract_and_redacts_payload(admin_client):
    from app.database import SessionLocal
    from app.models import AuditLog, WorkflowBusinessEvent, WorkflowEventDelivery, WorkflowExecution

    receiver_definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {
                "mode": "event", "event_source": "workflow", "event_name": "report.completed",
            }},
            {"id": "result", "type": "output.render", "config": {
                "name": "received", "renderer": "text", "value": "{{trigger.data.message}}",
            }},
        ],
        "edges": [{"source": "trigger", "target": "result"}],
    }
    receiver = admin_client.post(
        "/api/v1/workflows", json={"name": "business event receiver", "definition": receiver_definition},
        headers=CSRF_HEADERS,
    ).json()
    assert admin_client.post(f"/api/v1/workflows/{receiver['id']}/publish", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.post(f"/api/v1/workflows/{receiver['id']}/enable", headers=CSRF_HEADERS).status_code == 200

    sender_definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "emit", "type": "event.emit", "config": {
                "event_name": "report.completed",
                "payload": {"message": "{{trigger.message}}", "password": "{{trigger.password}}"},
            }},
            {"id": "result", "type": "output.render", "config": {
                "name": "delivery", "renderer": "json", "value": "{{emit}}",
            }},
        ],
        "edges": [{"source": "trigger", "target": "emit"}, {"source": "emit", "target": "result"}],
    }
    sender = admin_client.post(
        "/api/v1/workflows", json={"name": "business event sender", "definition": sender_definition},
        headers=CSRF_HEADERS,
    ).json()
    started = admin_client.post(
        f"/api/v1/workflows/{sender['id']}/test",
        json={"input": {"message": "ready", "password": "must-never-persist"}}, headers=CSRF_HEADERS,
    )
    assert started.status_code == 200, started.text
    sender_detail = _wait_execution(admin_client, started.json()["execution_id"])
    assert sender_detail["status"] == "SUCCEEDED", sender_detail

    with SessionLocal() as db:
        event = db.execute(select(WorkflowBusinessEvent).where(
            WorkflowBusinessEvent.source_execution_id == sender_detail["id"],
        )).scalar_one()
        delivery = db.execute(select(WorkflowEventDelivery).where(
            WorkflowEventDelivery.business_event_id == event.id,
        )).scalar_one()
        assert event.status == "DISPATCHED" and delivery.status == "DISPATCHED"
        assert delivery.attempts == 1 and delivery.target_workflow_id == receiver["id"]
        assert "must-never-persist" not in event.payload_json
        assert json.loads(event.payload_json)["password"] == "***"
        receiver_execution_id = delivery.target_execution_id
        receiver_execution = db.get(WorkflowExecution, receiver_execution_id)
        assert receiver_execution is not None and receiver_execution.trigger_type == "event:workflow"
        emit_audit = db.execute(select(AuditLog).where(
            AuditLog.action == "workflow.event_emit", AuditLog.resource_id == str(sender["id"]),
        )).scalar_one()
        delivery_audit = db.execute(select(AuditLog).where(
            AuditLog.action == "workflow.event_deliver", AuditLog.resource_id == str(receiver["id"]),
        )).scalar_one()
        audit_text = emit_audit.metadata_json + delivery_audit.metadata_json
        assert "must-never-persist" not in audit_text and "payload" not in audit_text

    receiver_detail = _wait_execution(admin_client, int(receiver_execution_id))
    assert receiver_detail["status"] == "SUCCEEDED"
    assert receiver_detail["context"]["result"]["output"]["value"] == "ready"
    assert receiver_detail["context"]["trigger"]["output"]["event_name"] == "report.completed"
    assert receiver_detail["context"]["trigger"]["output"]["event_id"] == event.event_id

    assert admin_client.delete(f"/api/v1/workflows/{sender['id']}", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.delete(f"/api/v1/workflows/{receiver['id']}", headers=CSRF_HEADERS).status_code == 200


def test_business_event_retry_is_bounded_error_safe_and_restart_recoverable(admin_client, monkeypatch):
    from app.database import SessionLocal
    from app.models import AuditLog, WorkflowBusinessEvent, WorkflowEventDelivery, utcnow
    from app.workflows import business_events, engine

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "result", "type": "output.render", "config": {"name": "result", "value": "ok"}},
        ],
        "edges": [{"source": "trigger", "target": "result"}],
    }
    source = admin_client.post(
        "/api/v1/workflows", json={"name": "event retry source", "definition": definition}, headers=CSRF_HEADERS,
    ).json()
    target = admin_client.post(
        "/api/v1/workflows", json={"name": "event retry target", "definition": definition}, headers=CSRF_HEADERS,
    ).json()
    source_run = admin_client.post(
        f"/api/v1/workflows/{source['id']}/test", json={"input": {}}, headers=CSRF_HEADERS,
    ).json()["execution_id"]
    assert _wait_execution(admin_client, source_run)["status"] == "SUCCEEDED"

    with SessionLocal() as db:
        event = WorkflowBusinessEvent(
            event_id="11111111-1111-4111-8111-111111111111", event_name="retry.event",
            source_workflow_id=source["id"], source_execution_id=source_run, source_node_id="emit",
            payload_json="{}", payload_size_bytes=2, lineage_json=json.dumps([source["id"]]),
            hop=1, status="PENDING",
        )
        db.add(event); db.flush()
        delivery = WorkflowEventDelivery(
            business_event_id=event.id, target_workflow_id=target["id"],
            status="DELIVERING", attempts=1,
        )
        db.add(delivery); db.commit()
        event_id, delivery_id = event.id, delivery.id

    async def recover_run(*_args, **_kwargs):
        return source_run

    monkeypatch.setattr(engine, "run_workflow", recover_run)
    assert asyncio.run(business_events.dispatch_pending_events_once()) == 1
    with SessionLocal() as db:
        event = db.get(WorkflowBusinessEvent, event_id)
        delivery = db.get(WorkflowEventDelivery, delivery_id)
        assert event.status == "DISPATCHED"
        assert delivery.status == "DISPATCHED" and delivery.attempts == 2
        assert delivery.target_execution_id == source_run

        second = WorkflowBusinessEvent(
            event_id="22222222-2222-4222-8222-222222222222", event_name="failure.event",
            source_workflow_id=source["id"], source_execution_id=source_run, source_node_id="emit",
            payload_json="{}", payload_size_bytes=2, lineage_json=json.dumps([source["id"]]),
            hop=1, status="PENDING",
        )
        db.add(second); db.flush()
        failed_delivery = WorkflowEventDelivery(
            business_event_id=second.id, target_workflow_id=target["id"], status="PENDING", attempts=1,
        )
        db.add(failed_delivery); db.commit()
        second_id, failed_delivery_id = second.id, failed_delivery.id

    async def failed_run(*_args, **_kwargs):
        raise RuntimeError("must-never-persist-error-body")

    monkeypatch.setattr(engine, "run_workflow", failed_run)
    assert asyncio.run(business_events.dispatch_pending_events_once()) == 1
    assert asyncio.run(business_events.dispatch_pending_events_once()) == 1
    with SessionLocal() as db:
        event = db.get(WorkflowBusinessEvent, second_id)
        delivery = db.get(WorkflowEventDelivery, failed_delivery_id)
        assert event.status == "FAILED"
        assert delivery.status == "FAILED" and delivery.attempts == 3
        assert delivery.last_error == "RuntimeError"
        persisted = event.payload_json + delivery.last_error
        assert "must-never-persist-error-body" not in persisted
        audits = db.execute(select(AuditLog).where(
            AuditLog.action == "workflow.event_deliver", AuditLog.resource_id == str(target["id"]),
        )).scalars().all()
        assert any(item.result == "retry" for item in audits)
        assert any(item.result == "failure" for item in audits)
        assert "must-never-persist-error-body" not in json.dumps([item.metadata_json for item in audits])

        event.created_at = utcnow() - timedelta(days=8)
        db.commit()
    assert business_events._prune_completed() == 1
    with SessionLocal() as db:
        assert db.get(WorkflowBusinessEvent, second_id) is None
        assert db.get(WorkflowEventDelivery, failed_delivery_id) is None

    assert admin_client.delete(f"/api/v1/workflows/{source['id']}", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.delete(f"/api/v1/workflows/{target['id']}", headers=CSRF_HEADERS).status_code == 200
