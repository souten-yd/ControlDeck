from __future__ import annotations

import time

from tests.conftest import CSRF_HEADERS


def _create_and_publish(client, name: str, definition: dict) -> int:
    created = client.post(
        "/api/v1/workflows", json={"name": name, "definition": definition}, headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    published = client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS)
    assert published.status_code == 200, published.text
    return workflow_id


def _wait(client, execution_id: int) -> dict:
    deadline = time.monotonic() + 10
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/workflow-executions/{execution_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] not in {"QUEUED", "RUNNING", "WAITING"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"execution did not finish: {body}")


def _child_definition(fail: bool) -> dict:
    nodes = [
        {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
        {"id": "result", "type": "flow.return", "config": {
            "name": "child_result", "renderer": "text", "value": "child-ok",
        }},
    ]
    edges = [{"source": "t", "target": "result"}]
    if fail:
        nodes.append({"id": "fail", "type": "flow.error", "config": {
            "code": "CHILD_EXPECTED_FAILURE", "message": "child failed deliberately",
            "details": '{"boundary":"try"}',
        }})
        edges.append({"source": "t", "target": "fail"})
    return {"nodes": nodes, "edges": edges}


def _parent_definition(child_id: int) -> dict:
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "attempt", "type": "control.try", "config": {
                "workflow_id": child_id, "timeout": 30,
            }},
            {"id": "success", "type": "flow.return", "config": {
                "name": "success", "renderer": "text", "value": "{{attempt.result}}",
            }},
            {"id": "recovered", "type": "flow.return", "config": {
                "name": "recovered", "renderer": "text", "value": "{{attempt.error.code}}",
            }},
        ],
        "edges": [
            {"source": "t", "target": "attempt"},
            {"source": "attempt", "target": "success", "branch": "success"},
            {"source": "attempt", "target": "recovered", "branch": "error"},
        ],
    }


def test_control_try_routes_published_subflow_success_and_typed_error(admin_client):
    created_ids: list[int] = []
    try:
        success_child = _create_and_publish(admin_client, "try child success", _child_definition(False))
        created_ids.append(success_child)
        success_parent = _create_and_publish(admin_client, "try parent success", _parent_definition(success_child))
        created_ids.append(success_parent)
        started = admin_client.post(
            f"/api/v1/workflows/{success_parent}/run", json={}, headers=CSRF_HEADERS,
        )
        success = _wait(admin_client, started.json()["execution_id"])
        assert success["status"] == "SUCCEEDED", success
        assert success["outputs"]["success"]["value"] == "child-ok"
        assert "recovered" not in success["outputs"]
        assert success["context"]["attempt"]["output"]["ok"] is True

        failed_child = _create_and_publish(admin_client, "try child failure", _child_definition(True))
        created_ids.append(failed_child)
        failed_parent = _create_and_publish(admin_client, "try parent recovery", _parent_definition(failed_child))
        created_ids.append(failed_parent)
        started = admin_client.post(
            f"/api/v1/workflows/{failed_parent}/run", json={}, headers=CSRF_HEADERS,
        )
        recovered = _wait(admin_client, started.json()["execution_id"])
        assert recovered["status"] == "SUCCEEDED", recovered
        assert recovered["outputs"]["recovered"]["value"] == "CHILD_EXPECTED_FAILURE"
        assert "success" not in recovered["outputs"]
        attempt = recovered["context"]["attempt"]["output"]
        assert attempt["ok"] is False and attempt["status"] == "FAILED"
        assert attempt["error"]["details"] == {"boundary": "try"}
    finally:
        for workflow_id in reversed(created_ids):
            response = admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS)
            assert response.status_code == 200, response.text
