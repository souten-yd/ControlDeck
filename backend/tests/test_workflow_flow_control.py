from __future__ import annotations

import asyncio
import json
import time

import pytest

from tests.conftest import CSRF_HEADERS


def run(coro):
    return asyncio.run(coro)


def test_disabled_node_is_skipped_without_losing_graph_route():
    from app.workflows.engine import _execute_graph

    context: dict = {}
    run(_execute_graph([
        {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
        {"id": "disabled", "type": "var.set", "disabled": True, "config": {"name": "unsafe", "value": "must-not-run"}},
        {"id": "after", "type": "flow.note", "config": {"text": "continued"}},
    ], [
        {"source": "t", "target": "disabled"},
        {"source": "disabled", "target": "after"},
    ], context))
    assert context["disabled"]["status"] == "SKIPPED"
    assert context["disabled"]["output"] == {"disabled": True, "skipped": True}
    assert context["after"]["status"] == "SUCCEEDED"


def test_flow_control_executors_are_typed_and_deterministic():
    from app.workflows.nodes import (
        NodeError,
        node_flow_error,
        node_flow_note,
        node_flow_return,
        node_test_assert,
    )

    context = {"source": {"output": {"value": "42"}}}
    returned = run(node_flow_return({
        "name": "answer", "renderer": "json_tree",
        "value": '{"value": {{source.value}}}',
    }, context))
    assert returned["terminal"] is True
    assert returned["output_contract"] is True
    assert returned["value"] == {"value": 42}

    assert run(node_flow_note({"level": "warning", "text": "value={{source.value}}"}, context)) == {
        "level": "warning", "note": "value=42",
    }
    assert run(node_test_assert({"actual": "{{source.value}}", "operator": "gte", "expected": "40"}, context)) == {
        "passed": True, "operator": "gte", "actual": "42", "expected": "40",
    }

    with pytest.raises(NodeError) as assertion:
        run(node_test_assert({
            "actual": "{{source.value}}", "operator": "lt", "expected": "40",
            "message": "範囲外です",
        }, context))
    assert assertion.value.code == "ASSERTION_FAILED"
    assert assertion.value.retryable is False
    assert assertion.value.details == {"operator": "lt", "actual": "42", "expected": "40"}

    with pytest.raises(NodeError) as deliberate:
        run(node_flow_error({
            "code": "quota exceeded", "message": "停止: {{source.value}}",
            "details": '{"observed": {{source.value}}}',
        }, context))
    assert deliberate.value.code == "QUOTA_EXCEEDED"
    assert deliberate.value.retryable is False
    assert deliberate.value.details == {"observed": 42}


def test_typed_error_routes_once_and_return_is_leaf_only():
    from app.workflows.engine import DefinitionError, _execute_graph, validate_definition
    from app.workflows.validation import semantic_check

    nodes = [
        {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
        {"id": "boom", "type": "flow.error", "config": {
            "code": "EXPECTED_STOP", "message": "planned", "details": '{"source":"test"}',
            "retry_count": 5, "retry_wait": 0, "on_error": "branch",
        }},
        {"id": "recovery", "type": "var.set", "config": {
            "value": "{{boom.error.code}}/{{boom.error.details.source}}",
        }},
        {"id": "result", "type": "flow.return", "config": {
            "name": "result", "renderer": "text", "value": "{{recovery.value}}",
        }},
    ]
    edges = [
        {"source": "t", "target": "boom"},
        {"source": "boom", "target": "recovery", "branch": "error"},
        {"source": "recovery", "target": "result"},
    ]
    context: dict = {}
    run(_execute_graph(nodes, edges, context))
    assert context["boom"]["attempts"] == 1
    assert context["boom"]["error_context"]["code"] == "EXPECTED_STOP"
    assert context["boom"]["error_context"]["retryable"] is False
    assert context["result"]["output"]["value"] == "EXPECTED_STOP/test"

    invalid_edges = [*edges, {"source": "result", "target": "recovery"}]
    errors, _warnings = semantic_check(nodes, invalid_edges)
    assert any("終端専用" in error for error in errors)
    with pytest.raises(DefinitionError, match="終端専用"):
        validate_definition(json.dumps({"nodes": nodes, "edges": invalid_edges}))


def test_flow_return_public_runner_contract(admin_client):
    definition = {
        "nodes": [
            {"id": "start", "type": "trigger", "config": {
                "mode": "manual", "inputs": [{
                    "key": "value", "label": "値", "type": "text", "required": True,
                }],
            }},
            {"id": "note", "type": "flow.note", "config": {
                "level": "info", "text": "入力を検証します",
            }},
            {"id": "assert", "type": "test.assert", "config": {
                "actual": "{{start.value}}", "operator": "eq", "expected": "ok",
            }},
            {"id": "result", "type": "flow.return", "config": {
                "name": "result", "title": "検証結果", "renderer": "text",
                "value": "{{start.value}}", "copyable": True,
            }},
        ],
        "edges": [
            {"source": "start", "target": "note"},
            {"source": "note", "target": "assert"},
            {"source": "assert", "target": "result"},
        ],
    }
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "flow-control-contract", "definition": definition},
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    try:
        published = admin_client.post(
            f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS,
        )
        assert published.status_code == 200, published.text
        detail = admin_client.get(f"/api/v1/workflow-runner/{workflow_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["output_schema"]["properties"]["result"]["type"] == "string"

        started = admin_client.post(
            f"/api/v1/workflow-runner/{workflow_id}/runs",
            json={"input": {"value": "ok"}}, headers=CSRF_HEADERS,
        )
        assert started.status_code == 200, started.text
        execution_id = started.json()["execution_id"]
        deadline = time.monotonic() + 10
        body: dict = {}
        while time.monotonic() < deadline:
            response = admin_client.get(f"/api/v1/workflow-runner/executions/{execution_id}")
            assert response.status_code == 200, response.text
            body = response.json()
            if body["status"] not in {"QUEUED", "RUNNING", "WAITING"}:
                break
            time.sleep(0.05)
        assert body["status"] == "SUCCEEDED", body
        assert body["outputs"]["result"]["value"] == "ok"
        assert body["outputs"]["result"]["copyable"] is True
        assert "source_node_id" not in body["outputs"]["result"]
    finally:
        admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS)
