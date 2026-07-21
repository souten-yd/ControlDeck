from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import time

from sqlalchemy import select

from app.database import SessionLocal
from app.models import WorkflowExecutionEvent, WorkflowNodeRun, WorkflowPause, utcnow
from app.workflows import engine

CSRF = {"X-Requested-With": "ControlDeck"}


def _wait(client, execution_id: int, predicate, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = client.get(f"/api/v1/workflow-executions/{execution_id}/live").json()
        if predicate(latest):
            return latest
        time.sleep(0.05)
    raise AssertionError(f"execution did not reach expected state: {latest}")


def test_approval_pause_is_db_backed_and_resumes_with_typed_response(admin_client):
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "gate", "type": "human.approval", "config": {
                "message": "変更内容を確認してください",
                "approver": "admin",
                "approval_timeout_seconds": 30,
                "form_schema": {
                    "type": "object",
                    "properties": {"comment": {"type": "string"}},
                },
            }},
            {"id": "out", "type": "signal.display", "config": {
                "signal": "answer", "value": "{{gate.response.comment}}",
            }},
        ],
        "edges": [{"source": "t", "target": "gate"}, {"source": "gate", "target": "out"}],
    }
    workflow_id = admin_client.post(
        "/api/v1/workflows", json={"name": "durable pause", "definition": definition}, headers=CSRF,
    ).json()["id"]
    admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF)
    execution_id = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF,
    ).json()["execution_id"]

    waiting = _wait(admin_client, execution_id, lambda item: item["status"] == "WAITING")
    assert waiting["pending_approvals"][0]["node_id"] == "gate"
    assert waiting["pending_approvals"][0]["form_schema"]["properties"]["comment"]["type"] == "string"
    # In-memory task/futureがなくてもDBから同じpending contractを復元できる。
    engine._live.clear()
    engine._running.pop(execution_id, None)
    assert engine.pending_approvals(execution_id)[0]["message"] == "変更内容を確認してください"

    with SessionLocal() as db:
        pause = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id,
        )).scalar_one()
        assert pause.status == "PENDING"
        assert len(pause.token_hash) == 64
        int(pause.token_hash, 16)
        assert pause.token_hash != hashlib.sha256("変更内容を確認してください".encode()).hexdigest()
        assert json.loads(pause.response_json) == {}

    invalid = admin_client.post(
        f"/api/v1/workflow-executions/{execution_id}/approve",
        json={"node_id": "gate", "approve": True, "response": {"comment": 42}}, headers=CSRF,
    )
    assert invalid.status_code == 422
    assert engine.pending_approvals(execution_id)[0]["node_id"] == "gate"

    approved = admin_client.post(
        f"/api/v1/workflow-executions/{execution_id}/approve",
        json={"node_id": "gate", "approve": True, "response": {"comment": "修正版で続行"}},
        headers=CSRF,
    )
    assert approved.status_code == 200, approved.text
    finished = _wait(admin_client, execution_id, lambda item: item["status"] == "SUCCEEDED")
    assert finished["context"]["gate"]["output"]["response"] == {"comment": "修正版で続行"}
    assert finished["context"]["out"]["output"]["value"] == "修正版で続行"

    with SessionLocal() as db:
        pause = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id,
        )).scalar_one()
        assert pause.status == "APPROVED"
        assert pause.resumed_at is not None
        event_types = db.execute(select(WorkflowExecutionEvent.event_type).where(
            WorkflowExecutionEvent.execution_id == execution_id,
        ).order_by(WorkflowExecutionEvent.sequence)).scalars().all()
        trigger_runs = db.execute(select(WorkflowNodeRun).where(
            WorkflowNodeRun.execution_id == execution_id, WorkflowNodeRun.node_id == "t",
        )).scalars().all()
    assert "execution.paused" in event_types
    assert "execution.resumed" in event_types
    assert len(trigger_runs) == 1  # checkpoint以前の成功nodeを副作用込みで再実行しない


def test_human_form_is_durable_and_exposes_schema_validated_response(admin_client):
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "form", "type": "human.form", "config": {
                "message": "公開内容を入力してください",
                "approver": "admin",
                "form_timeout_seconds": 30,
                "inputs": [
                    {"key": "title", "label": "タイトル", "type": "text", "required": True, "maxLength": 40},
                    {"key": "priority", "label": "優先度", "type": "select", "required": True, "options": "low,high"},
                    {"key": "notify", "label": "通知", "type": "boolean"},
                ],
            }},
            {"id": "out", "type": "flow.return", "config": {
                "name": "result", "renderer": "text",
                "value": "{{form.response.title}}/{{form.response.priority}}/{{form.response.notify}}",
            }},
        ],
        "edges": [{"source": "t", "target": "form"}, {"source": "form", "target": "out"}],
    }
    workflow_id = admin_client.post(
        "/api/v1/workflows", json={"name": "durable human form", "definition": definition}, headers=CSRF,
    ).json()["id"]
    published = admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF)
    assert published.status_code == 200, published.text
    execution_id = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF,
    ).json()["execution_id"]

    waiting = _wait(admin_client, execution_id, lambda item: item["status"] == "WAITING")
    pending = waiting["pending_approvals"][0]
    assert pending["interaction_type"] == "form"
    assert pending["form_schema"]["required"] == ["title", "priority"]
    assert pending["form_schema"]["properties"]["priority"]["enum"] == ["low", "high"]
    engine._live.clear()
    engine._running.pop(execution_id, None)
    restored = engine.pending_approvals(execution_id)[0]
    assert restored["interaction_type"] == "form"

    with SessionLocal() as db:
        pause = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id,
        )).scalar_one()
        assert pause.pause_type == "form"
        assert pause.status == "PENDING"

    invalid = admin_client.post(
        f"/api/v1/workflow-executions/{execution_id}/approve",
        json={"node_id": "form", "approve": True, "response": {"title": "release", "priority": "urgent"}},
        headers=CSRF,
    )
    assert invalid.status_code == 422
    submitted = admin_client.post(
        f"/api/v1/workflow-executions/{execution_id}/approve",
        json={"node_id": "form", "approve": True, "response": {
            "title": "release", "priority": "high", "notify": True,
        }}, headers=CSRF,
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["interaction_type"] == "form"
    finished = _wait(admin_client, execution_id, lambda item: item["status"] == "SUCCEEDED")
    assert finished["context"]["form"]["output"]["submitted"] is True
    assert finished["context"]["form"]["output"]["response"] == {
        "title": "release", "priority": "high", "notify": True,
    }
    assert finished["context"]["out"]["output"]["value"] == "release/high/true"

    with SessionLocal() as db:
        pause = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id,
        )).scalar_one()
        assert pause.status == "APPROVED"
        form_runs = db.execute(select(WorkflowNodeRun).where(
            WorkflowNodeRun.execution_id == execution_id,
            WorkflowNodeRun.node_id == "form",
        )).scalars().all()
        trigger_runs = db.execute(select(WorkflowNodeRun).where(
            WorkflowNodeRun.execution_id == execution_id,
            WorkflowNodeRun.node_id == "t",
        )).scalars().all()
    assert len(form_runs) == 2  # WAITING checkpointとresume後の完了run
    assert len(trigger_runs) == 1


def test_waiting_execution_can_be_canceled_without_an_in_memory_task(admin_client):
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "gate", "type": "human.approval", "config": {"approval_timeout_seconds": 30}},
            {"id": "out", "type": "signal.display", "config": {"signal": "result", "value": "done"}},
        ],
        "edges": [{"source": "t", "target": "gate"}, {"source": "gate", "target": "out"}],
    }
    workflow_id = admin_client.post(
        "/api/v1/workflows", json={"name": "cancel durable pause", "definition": definition}, headers=CSRF,
    ).json()["id"]
    admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF)
    execution_id = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF,
    ).json()["execution_id"]
    _wait(admin_client, execution_id, lambda item: item["status"] == "WAITING")
    engine._running.pop(execution_id, None)

    canceled = admin_client.post(f"/api/v1/workflow-executions/{execution_id}/cancel", headers=CSRF)
    assert canceled.status_code == 200, canceled.text
    result = _wait(admin_client, execution_id, lambda item: item["status"] == "CANCELED")
    assert result["pending_approvals"] == []
    with SessionLocal() as db:
        pause = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id,
        )).scalar_one()
        assert pause.status == "CANCELED"


def test_durable_delay_survives_task_loss_and_resumes_without_rerunning_upstream(admin_client):
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "upstream", "type": "var.set", "config": {"value": "once"}},
            {"id": "delay", "type": "control.delay", "config": {
                "seconds": 3600, "message": "再開可能な待機",
            }},
            {"id": "gate", "type": "human.approval", "config": {
                "message": "delay後の承認", "approval_timeout_seconds": 30,
            }},
            {"id": "out", "type": "flow.return", "config": {
                "name": "result", "renderer": "text",
                "value": "{{upstream.value}}/{{delay.durable}}/{{gate.approved}}",
            }},
        ],
        "edges": [
            {"source": "t", "target": "upstream"},
            {"source": "upstream", "target": "delay"},
            {"source": "delay", "target": "gate"},
            {"source": "gate", "target": "out"},
        ],
    }
    workflow_id = admin_client.post(
        "/api/v1/workflows", json={"name": "durable delay", "definition": definition}, headers=CSRF,
    ).json()["id"]
    admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF)
    execution_id = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF,
    ).json()["execution_id"]

    waiting = _wait(admin_client, execution_id, lambda item: item["status"] == "WAITING")
    assert waiting["pending_approvals"] == []
    deadline = time.monotonic() + 2
    while execution_id in engine._running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert execution_id not in engine._running
    engine._live.clear()
    with SessionLocal() as db:
        pause = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id,
            WorkflowPause.pause_type == "delay",
        )).scalar_one()
        assert pause.pause_type == "delay" and pause.status == "PENDING"
        pause.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    async def recover_and_finish() -> None:
        assert await engine.recover_paused_workflows_once() == 1
        task = engine._running.get(execution_id)
        assert task is not None
        await task

    asyncio.run(recover_and_finish())
    waiting_again = _wait(admin_client, execution_id, lambda item: item["status"] == "WAITING")
    assert waiting_again["pending_approvals"][0]["node_id"] == "gate"
    # 最新checkpointは未解決approval。過去のCOMPLETED delayを再消費しない。
    assert asyncio.run(engine.recover_paused_workflows_once()) == 0
    approved = admin_client.post(
        f"/api/v1/workflow-executions/{execution_id}/approve",
        json={"node_id": "gate", "approve": True, "response": {}}, headers=CSRF,
    )
    assert approved.status_code == 200, approved.text
    finished = _wait(admin_client, execution_id, lambda item: item["status"] == "SUCCEEDED")
    assert finished["context"]["delay"]["output"]["durable"] is True
    assert finished["context"]["out"]["output"]["value"] == "once/true/true"
    with SessionLocal() as db:
        pause = db.execute(select(WorkflowPause).where(
            WorkflowPause.execution_id == execution_id,
            WorkflowPause.pause_type == "delay",
        )).scalar_one()
        assert pause.status == "COMPLETED" and pause.resumed_at is not None
        upstream_runs = db.execute(select(WorkflowNodeRun).where(
            WorkflowNodeRun.execution_id == execution_id,
            WorkflowNodeRun.node_id == "upstream",
        )).scalars().all()
    assert len(upstream_runs) == 1
