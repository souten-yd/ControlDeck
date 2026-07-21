from __future__ import annotations

import asyncio
import json
import time

import pytest
from sqlalchemy import select

from tests.conftest import CSRF_HEADERS


def _wait(client, execution_id: int) -> dict:
    deadline = time.monotonic() + 15
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/workflow-executions/{execution_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] not in {"QUEUED", "RUNNING", "WAITING"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"execution did not finish: {body}")


def _subflow_result(index: int, *, ok: bool = True) -> dict:
    return {
        "execution_id": 1000 + index,
        "status": "SUCCEEDED" if ok else "FAILED",
        "ok": ok,
        "outputs": {"result": {"value": f"value-{index}"}} if ok else {},
        "result": f"value-{index}" if ok else "",
        "count": 1 if ok else 0,
        "error": None if ok else {"code": "CHILD_FAILED", "message": "fixed failure", "retryable": False},
    }


def test_subflow_map_is_bounded_version_pinned_ordered_and_secret_safe(monkeypatch):
    from app.workflows import nodes
    from app.workflows.contracts import build_fields_schema, validate_public_inputs

    array_schema = build_fields_schema([{"key": "items", "type": "json_array", "required": True}])
    assert array_schema["properties"]["items"]["type"] == "array"
    assert validate_public_inputs(array_schema, {"items": [{"id": 1}]}) == []
    assert validate_public_inputs(array_schema, {"items": {"id": 1}})
    assert asyncio.run(nodes.node_wait(
        {"seconds": "{{trigger.delay}}"},
        {"trigger": {"output": {"delay": 0}}},
    ))["waited_seconds"] == 0

    active = 0
    max_active = 0
    calls: list[tuple[int, tuple[int, str], str, object]] = []

    async def fake_run(_config, iteration_ctx, *, input_base, published_snapshot, trigger_type):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        index = input_base["index"]
        calls.append((index, published_snapshot, trigger_type, input_base["item"]))
        assert iteration_ctx["map"]["output"]["item"] == input_base["item"]
        await asyncio.sleep(0.01 * (4 - index))
        active -= 1
        return _subflow_result(index)

    monkeypatch.setattr(nodes, "_published_subflow_snapshot", lambda _workflow_id: (77, "{}"))
    monkeypatch.setattr(nodes, "_run_subflow", fake_run)
    monkeypatch.setattr(nodes, "_audit_subflow_map", lambda **_kwargs: None)
    output = asyncio.run(nodes.node_flow_map(
        {
            "workflow_id": 9, "items": ["one", "must-never-persist-map", "three", "four"],
            "parallel": 3, "failure_policy": "collect", "__workflow_id": 8,
            "__execution_id": 2, "__node_id": "map",
        },
        {"__secrets__": {"MAP_SECRET": "must-never-persist-map"}, "__subflow_lineage__": [8]},
    ))

    assert max_active == 3
    assert [item["index"] for item in output["results"]] == [0, 1, 2, 3]
    assert output["results"][1]["item"] == "***"
    assert output["target_version_id"] == 77 and output["all_succeeded"] is True
    assert all(snapshot == (77, "{}") and trigger == "subflow:map" for _, snapshot, trigger, _ in calls)
    assert [item for *_rest, item in sorted(calls)] == ["one", "must-never-persist-map", "three", "four"]


def test_subflow_map_stop_policy_stops_after_failed_batch_and_cycle_is_preflight(monkeypatch):
    from app.workflows import nodes

    calls: list[int] = []

    async def fake_run(_config, _ctx, *, input_base, **_kwargs):
        calls.append(input_base["index"])
        return _subflow_result(input_base["index"], ok=input_base["index"] != 1)

    monkeypatch.setattr(nodes, "_published_subflow_snapshot", lambda _workflow_id: (88, "{}"))
    monkeypatch.setattr(nodes, "_run_subflow", fake_run)
    monkeypatch.setattr(nodes, "_audit_subflow_map", lambda **_kwargs: None)
    with pytest.raises(nodes.NodeError) as stopped:
        asyncio.run(nodes.node_flow_map(
            {
                "workflow_id": 9, "items": [1, 2, 3, 4, 5], "parallel": 2,
                "failure_policy": "stop", "__workflow_id": 8, "__node_id": "map",
            }, {"__subflow_lineage__": [8]},
        ))
    assert stopped.value.code == "SUBFLOW_MAP_FAILED"
    assert stopped.value.details["failed_indexes"] == [1]
    assert calls == [0, 1]

    monkeypatch.setattr(
        nodes, "_published_subflow_snapshot",
        lambda _workflow_id: (_ for _ in ()).throw(AssertionError("cycle must fail before DB lookup")),
    )
    with pytest.raises(nodes.NodeError) as cycle:
        asyncio.run(nodes.node_flow_map(
            {"workflow_id": 8, "items": [], "__workflow_id": 8, "__node_id": "map"},
            {"__subflow_lineage__": [8]},
        ))
    assert cycle.value.code == "SUBFLOW_CYCLE" and cycle.value.retryable is False


def test_subflow_map_collects_real_published_children_and_audits_without_payload(admin_client):
    from app.database import SessionLocal
    from app.models import AuditLog, WorkflowExecution

    secret = "must-never-persist-map-integration"
    assert admin_client.put(
        "/api/v1/workflows-secrets/MAP_TEST_SECRET", json={"value": secret}, headers=CSRF_HEADERS,
    ).status_code == 200
    created_ids: list[int] = []
    try:
        child_definition = {
            "nodes": [
                {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
                {"id": "is_ok", "type": "condition.if", "config": {
                    "left": "{{t.item}}", "op": "ne", "right": "bad",
                }},
                {"id": "result", "type": "flow.return", "config": {
                    "name": "child_result", "renderer": "text", "value": "child={{t.item}}; index={{t.index}}",
                }},
                {"id": "failed", "type": "flow.error", "config": {
                    "code": "MAP_CHILD_REJECTED", "message": "fixed child rejection",
                }},
            ],
            "edges": [
                {"source": "t", "target": "is_ok"},
                {"source": "is_ok", "target": "result", "branch": "true"},
                {"source": "is_ok", "target": "failed", "branch": "false"},
            ],
        }
        child_response = admin_client.post(
            "/api/v1/workflows", json={"name": "map child", "definition": child_definition}, headers=CSRF_HEADERS,
        )
        assert child_response.status_code == 201, child_response.text
        child_id = child_response.json()["id"]
        created_ids.append(child_id)
        published = admin_client.post(f"/api/v1/workflows/{child_id}/publish", headers=CSRF_HEADERS)
        assert published.status_code == 200, published.text
        child_version_id = published.json()["version_id"]

        parent_definition = {
            "nodes": [
                {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
                {"id": "map", "type": "flow.map", "config": {
                    "workflow_id": child_id,
                    "items": '["one","bad","{{secrets.MAP_TEST_SECRET}}"]',
                    "parallel": 2, "failure_policy": "collect", "timeout": 30,
                }},
                {"id": "result", "type": "flow.return", "config": {
                    "name": "mapped", "renderer": "json", "value": "{{map.results}}",
                }},
            ],
            "edges": [{"source": "t", "target": "map"}, {"source": "map", "target": "result"}],
        }
        parent_response = admin_client.post(
            "/api/v1/workflows", json={"name": "map parent", "definition": parent_definition}, headers=CSRF_HEADERS,
        )
        assert parent_response.status_code == 201, parent_response.text
        parent_id = parent_response.json()["id"]
        created_ids.append(parent_id)
        assert admin_client.post(f"/api/v1/workflows/{parent_id}/publish", headers=CSRF_HEADERS).status_code == 200
        started = admin_client.post(f"/api/v1/workflows/{parent_id}/run", json={}, headers=CSRF_HEADERS)
        assert started.status_code == 200, started.text
        parent = _wait(admin_client, started.json()["execution_id"])
        assert parent["status"] == "SUCCEEDED", parent
        mapped = parent["context"]["map"]["output"]
        assert [item["index"] for item in mapped["results"]] == [0, 1, 2]
        assert mapped["succeeded"] == 2 and mapped["failed"] == 1 and mapped["all_succeeded"] is False
        assert mapped["results"][0]["result"] == "child=one; index=0"
        assert mapped["results"][1]["error"]["code"] == "MAP_CHILD_REJECTED"
        assert mapped["results"][2]["item"] == "***"
        assert mapped["target_version_id"] == child_version_id
        assert secret not in json.dumps(parent, ensure_ascii=False)

        with SessionLocal() as db:
            children = db.execute(select(WorkflowExecution).where(
                WorkflowExecution.id.in_(mapped["execution_ids"]),
            ).order_by(WorkflowExecution.id)).scalars().all()
            assert len(children) == 3
            assert {child.workflow_version_id for child in children} == {child_version_id}
            assert {child.trigger_type for child in children} == {"subflow:map"}
            assert secret not in "".join(child.context_json + child.runtime_snapshot_json for child in children)
            audit = db.execute(select(AuditLog).where(
                AuditLog.action == "workflow.subflow_map", AuditLog.resource_id == str(parent_id),
            )).scalar_one()
            assert audit.result == "success"
            assert secret not in audit.metadata_json and "items" not in audit.metadata_json
            metadata = json.loads(audit.metadata_json)
            assert metadata["target_version_id"] == child_version_id
            assert metadata["succeeded"] == 2 and metadata["failed"] == 1
    finally:
        for workflow_id in reversed(created_ids):
            response = admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS)
            assert response.status_code == 200, response.text
        assert admin_client.delete(
            "/api/v1/workflows-secrets/MAP_TEST_SECRET", headers=CSRF_HEADERS,
        ).status_code == 204
