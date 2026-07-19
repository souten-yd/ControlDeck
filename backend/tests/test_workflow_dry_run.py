"""副作用なしdry-runとnode metadata契約。"""
from __future__ import annotations

import re
from pathlib import Path


def _definition() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "trigger", "name": "開始", "config": {}},
            {"id": "write", "type": "file.write", "name": "保存", "config": {
                "path": "/tmp/should-not-exist", "content": "{{secrets.PRIVATE_NAME}}",
                "api_token": "must-not-leak",
            }},
            {"id": "notify", "type": "notify.webhook", "name": "通知", "config": {
                "url": "https://example.invalid/hook", "message": "done",
            }},
        ],
        "edges": [
            {"source": "start", "target": "write"},
            {"source": "write", "target": "notify"},
        ],
    }


def test_dry_run_never_calls_executor_or_leaks_secret(monkeypatch):
    from app.workflows import nodes
    from app.workflows.dry_run import simulate_definition

    called = []

    async def forbidden(config, context):
        called.append(True)
        raise AssertionError("executor must not be called")

    monkeypatch.setitem(nodes.NODE_EXECUTORS, "file.write", forbidden)
    monkeypatch.setitem(nodes.NODE_EXECUTORS, "notify.webhook", forbidden)
    result = simulate_definition(_definition(), {"password": "input-secret"})
    assert result["valid"] is True
    assert called == []
    assert result["summary"]["side_effects"] == {"external": 1, "write": 1}
    serialized = str(result)
    assert "must-not-leak" not in serialized
    assert "PRIVATE_NAME" not in serialized
    assert "input-secret" not in serialized
    assert "{{secrets.***}}" in serialized


def test_dry_run_reports_errors_cycle_and_unreachable():
    from app.workflows.dry_run import simulate_definition

    definition = _definition()
    definition["nodes"][1]["config"]["path"] = ""
    definition["nodes"][1]["config"]["retry_count"] = "not-a-number"
    definition["nodes"].append({"id": "orphan", "type": "util.now", "config": {}})
    definition["edges"].append({"source": "notify", "target": "write"})
    result = simulate_definition(definition)
    assert result["valid"] is False
    assert any("path" in error for error in result["errors"])
    assert any(item["id"] == "orphan" and item["status"] == "UNREACHABLE" for item in result["plan"])
    assert len(result["plan"]) == 4  # cycleでも有限


def test_node_metadata_matches_executor_catalog_and_frontend():
    from app.workflows.catalog import valid_types
    from app.workflows.node_metadata import node_catalog
    from app.workflows.nodes import NODE_EXECUTORS

    expected = set(NODE_EXECUTORS) | {"control.loop"}
    catalog = node_catalog()
    metadata = {item["type"] for item in catalog}
    assert metadata == expected
    assert expected <= valid_types()

    source = (Path(__file__).parents[2] / "frontend/src/features/workflows/nodeTypes.ts").read_text(encoding="utf-8")
    frontend = set(re.findall(r'^  "([a-z][\w.]*)"\s*:', source, re.MULTILINE)) | {"trigger"}
    assert expected <= frontend

    wait_schema = next(item for item in catalog if item["type"] == "util.wait")["config_schema"]
    assert wait_schema["retry_count"]["type"] == "integer"
    assert wait_schema["retry_wait"]["type"] == "number"
    assert wait_schema["node_timeout"]["type"] == "number"
    assert wait_schema["on_error"]["type"] == "string"
    approval = next(item for item in catalog if item["type"] == "human.approval")
    assert approval["supports"]["retry"] is False
    assert approval["config_schema"]["approval_timeout_seconds"]["type"] == "number"
    merge = next(item for item in catalog if item["type"] == "control.merge")
    assert merge["config_schema"]["quorum"]["type"] == "integer"
    assert merge["output_schema"]["items"] == "array"


def test_node_metadata_guided_configuration_is_safe_and_consistent():
    from app.workflows.node_metadata import node_catalog

    catalog = {item["type"]: item for item in node_catalog()}
    assert catalog["http.request"]["initial_config"] == {"method": "GET", "timeout": 30}
    assert catalog["http.request"]["ui_hints"]["primary_input"] == "url"
    assert catalog["http.request"]["ui_hints"]["primary_output"] == "body"
    assert catalog["output.render"]["initial_config"]["renderer"] == "auto"
    assert catalog["output.render"]["ui_hints"]["examples"][0]["config"]["renderer"] == "markdown"
    assert catalog["research.deep"]["initial_config"]["depth"] == "standard"
    assert catalog["research.deep"]["config_schema"]["depth"]["recommended"] == "standard"
    assert catalog["control.loop"]["config_schema"]["parallel"]["recommended"] == 3
    for metadata in catalog.values():
        assert set(metadata["initial_config"]) <= set(metadata["config_schema"])
        serialized = str(metadata["initial_config"]).lower()
        assert "api_key" not in serialized
        assert "password" not in serialized
        assert "secret" not in serialized
    trigger_schema = catalog["trigger"]["config_schema"]
    assert "node_timeout" not in trigger_schema


def test_dry_run_api_does_not_create_execution(admin_client):
    from app.database import SessionLocal
    from app.models import WorkflowExecution
    from tests.conftest import CSRF_HEADERS

    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "dry-run-test", "definition": _definition()},
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    with SessionLocal() as db:
        before = db.query(WorkflowExecution).count()

    result = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/dry-run", json={"input": {"x": 1}}, headers=CSRF_HEADERS,
    )
    assert result.status_code == 200, result.text
    assert result.json()["dry_run"] is True
    with SessionLocal() as db:
        assert db.query(WorkflowExecution).count() == before

    node = admin_client.post(
        "/api/v1/workflows/test-node",
        json={"type": "app.stop", "config": {"app_id": 1}, "dry_run": True},
        headers=CSRF_HEADERS,
    )
    assert node.status_code == 200
    assert node.json()["status"] == "SIMULATED"
    assert admin_client.get("/api/v1/workflows/node-catalog").status_code == 200
    admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS)


def test_redaction_masks_sensitive_values_when_copied_to_other_fields():
    from app.workflows.redaction import collect_sensitive_values, redact

    payload = {
        "input": {"password": "secret-value"},
        "output": {"value": "prefix secret-value suffix"},
        "authorization": "Bearer abc",
    }
    redacted = redact(payload, sensitive_values=collect_sensitive_values(payload))
    assert redacted["input"]["password"] == "***"
    assert redacted["output"]["value"] == "prefix *** suffix"
    assert redacted["authorization"] == "***"
