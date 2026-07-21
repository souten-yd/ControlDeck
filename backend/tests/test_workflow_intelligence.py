from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditLog, Workflow, WorkflowExecution, WorkflowTestCase, WorkflowVersion
from app.workflows import runtime_route
from app.workflows.intelligence import WorkflowPatchError, preview_patch

CSRF = {"X-Requested-With": "ControlDeck"}


def _create(client, *, secret: str = "") -> dict:
    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual", "inputs": [{"key": "message", "label": "Message", "type": "text", "required": True, "sample": "hello"}]}},
            {"id": "llm", "type": "llm.chat", "config": {"base_url": "http://127.0.0.1:11434/v1", "model": "demo", "prompt": "{{trigger.message}}", "api_key": secret}},
            {"id": "result", "type": "flow.return", "config": {"name": "answer", "value": "{{llm.content}}"}},
        ],
        "edges": [{"source": "trigger", "target": "llm"}, {"source": "llm", "target": "result"}],
    }
    response = client.post("/api/v1/workflows", json={"name": "Project Intelligence test", "definition": definition}, headers=CSRF)
    assert response.status_code == 201, response.text
    return response.json()


def test_operation_patch_is_versioned_safe_and_atomic():
    definition = {"nodes": [{"id": "t", "type": "trigger", "config": {}}, {"id": "n", "type": "util.wait", "config": {"seconds": 1}}], "edges": [{"source": "t", "target": "n"}]}
    preview = preview_patch(definition, [{"op": "set_config", "node_id": "n", "key": "seconds", "value": 2}])
    assert preview["valid"] is True
    assert preview["patched_definition"]["nodes"][1]["config"]["seconds"] == 2
    assert preview["json_patch"] == [{"op": "add", "path": "/nodes/1/config/seconds", "value": 2}]
    assert definition["nodes"][1]["config"]["seconds"] == 1
    with pytest.raises(WorkflowPatchError, match="秘密値"):
        preview_patch(definition, [{"op": "set_config", "node_id": "n", "key": "api_key", "value": "literal-secret"}])
    with pytest.raises(WorkflowPatchError, match="trigger"):
        preview_patch(definition, [{"op": "remove_node", "node_id": "t"}])


def test_runtime_route_scores_live_state_and_tolerates_na(monkeypatch):
    async def snapshot():
        return {
            "gpu": {"name": None, "vram_total_bytes": None, "vram_used_bytes": None, "vram_free_bytes": None},
            "providers": [
                {"id": "a", "provider": "ollama", "base_url": "http://a/v1", "available": True, "selected": False, "managed": True, "models": ["cold"]},
                {"id": "b", "provider": "llama", "base_url": "http://b/v1", "available": True, "selected": True, "managed": True, "models": ["hot"]},
            ],
            "models": [
                {"runtime": "ollama", "base_url": "http://a/v1", "model": "cold", "loaded": False, "context_window": 8192, "vram_bytes": None},
                {"runtime": "llama", "base_url": "http://b/v1", "model": "hot", "loaded": True, "context_window": 32768, "vram_bytes": None},
            ],
        }

    monkeypatch.setattr(runtime_route, "runtime_snapshot", snapshot)
    selected = asyncio.run(runtime_route.choose_runtime(strategy="balanced"))
    assert selected["base_url"] == "http://b/v1" and selected["model"] == "hot"
    assert selected["loaded"] is True and selected["vram_free_bytes"] is None
    with pytest.raises(runtime_route.RuntimeRouteError, match="VRAM"):
        asyncio.run(runtime_route.choose_runtime(strategy="vram", min_free_vram_mb=1))


def test_ai_route_output_templates_into_llm_node(monkeypatch):
    from app.workflows import nodes

    async def choose(**kwargs):
        assert kwargs["strategy"] == "loaded"
        return {"base_url": "http://runtime/v1", "model": "routed-model", "strategy": "loaded", "score": 200, "loaded": True, "available": True, "context_window": 32768, "vram_free_bytes": None, "reason": "loaded", "candidates": [], "runtime_snapshot": {}}

    monkeypatch.setattr(runtime_route, "choose_runtime", choose)
    routed = asyncio.run(nodes.node_ai_route({"strategy": "loaded"}, {}))

    called: dict = {}

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            called["url"] = url
            called["model"] = kwargs["json"]["model"]
            return Response()

    monkeypatch.setattr(nodes.httpx, "AsyncClient", Client)
    monkeypatch.setattr("app.models_mgmt.runtime_policy.ensure_gpu_profile", lambda **kwargs: called.setdefault("profile", kwargs["base_url"]))
    output = asyncio.run(nodes.node_llm({
        "base_url": "{{route.base_url}}", "model": "{{route.model}}", "prompt": "hello", "auto_load": False,
    }, {"route": {"status": "SUCCEEDED", "output": routed}}))
    assert output["content"] == "ok" and output["model"] == "routed-model"
    assert called == {"profile": "http://runtime/v1", "url": "http://runtime/v1/chat/completions", "model": "routed-model"}


def test_intelligence_preview_apply_conflict_auto_test_and_audit(admin_client):
    workflow = _create(admin_client)
    workflow_id = workflow["id"]
    report = admin_client.get(f"/api/v1/workflows/{workflow_id}/intelligence")
    assert report.status_code == 200, report.text
    assert report.json()["summary"]["nodes"] == 3
    assert report.json()["suggested_tests"][0]["inputs"] == {"message": "hello"}

    operations = [{"op": "set_config", "node_id": "llm", "key": "retry_count", "value": 2}]
    preview = admin_client.post(f"/api/v1/workflows/{workflow_id}/intelligence/patch-preview", json={"patch_version": 1, "operations": operations}, headers=CSRF)
    assert preview.status_code == 200 and preview.json()["valid"] is True
    conflict = admin_client.post(f"/api/v1/workflows/{workflow_id}/intelligence/patch-apply", json={"patch_version": 1, "operations": operations, "expected_updated_at": "2000-01-01T00:00:00Z"}, headers=CSRF)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "WORKFLOW_CONFLICT"
    applied = admin_client.post(f"/api/v1/workflows/{workflow_id}/intelligence/patch-apply", json={"patch_version": 1, "operations": operations, "expected_updated_at": workflow["updated_at"]}, headers=CSRF)
    assert applied.status_code == 200, applied.text
    assert next(node for node in applied.json()["workflow"]["definition"]["nodes"] if node["id"] == "llm")["config"]["retry_count"] == 2

    tests = admin_client.post(f"/api/v1/workflows/{workflow_id}/intelligence/auto-tests", headers=CSRF)
    assert tests.status_code == 200 and tests.json()["test_cases"][0]["name"] == "AI baseline"
    repeated = admin_client.post(f"/api/v1/workflows/{workflow_id}/intelligence/auto-tests", headers=CSRF)
    assert repeated.json()["test_cases"][0]["id"] == tests.json()["test_cases"][0]["id"]
    with SessionLocal() as db:
        assert db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id)).scalars().all()
        assert len(db.execute(select(WorkflowTestCase).where(WorkflowTestCase.workflow_id == workflow_id)).scalars().all()) == 1
        actions = {row.action for row in db.execute(select(AuditLog).where(AuditLog.resource_id == str(workflow_id))).scalars().all()}
        assert {"workflow.ai_patch_apply", "workflow.ai_test_generate"}.issubset(actions)
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF).status_code == 200


def test_ai_diagnosis_sends_only_redacted_minimal_context(admin_client, monkeypatch):
    secret = "super-secret-value-should-never-leave"
    workflow = _create(admin_client, secret=secret)
    workflow_id = workflow["id"]
    with SessionLocal() as db:
        execution = WorkflowExecution(
            workflow_id=workflow_id, status="FAILED", error=f"provider rejected {secret}",
            context_json=json.dumps({"llm": {"status": "FAILED", "error": f"bad {secret}", "error_context": {"code": "TEMP", "retryable": True}}}),
            runtime_snapshot_json=json.dumps({"authorization": secret, "model": "demo"}),
        )
        db.add(execution)
        db.commit()
        execution_id = execution.id

    from app.models_mgmt import providers
    from app.workflows import chat_router

    captured: dict = {}

    async def fake_providers(**kwargs):
        return [{"base_url": "http://runtime/v1", "models": ["diagnoser"], "provider": "test", "available": True}]

    async def fake_llm(messages, *args, **kwargs):
        captured["messages"] = messages
        # Local JSON-mode runtimes may flatten a single proposal. The endpoint
        # normalizes this shape but still validates the same operation contract.
        return json.dumps({"diagnosis": "temporary provider failure", "confidence": 0.9, "failed_node_id": "llm", "operations": [{"op": "update_node", "node_id": "llm", "changes": {"config": {"retry_count": 2}}}]})

    monkeypatch.setattr(providers, "list_providers", fake_providers)
    monkeypatch.setattr(chat_router, "_llm", fake_llm)
    response = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/intelligence/diagnose",
        json={"execution_id": execution_id, "base_url": "http://runtime/v1", "model": "diagnoser", "use_ai": True}, headers=CSRF,
    )
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "ai" and response.json()["options"][0]["preview"]["valid"] is True
    assert response.json()["options"][0]["operations"] == [{"op": "set_config", "node_id": "llm", "key": "retry_count", "value": 2}]
    sent = json.dumps(captured["messages"], ensure_ascii=False)
    assert secret not in sent and '"output"' not in sent
    assert "{{secrets.***}}" not in sent
    with SessionLocal() as db:
        audit_row = db.execute(select(AuditLog).where(AuditLog.resource_id == str(workflow_id), AuditLog.action == "workflow.ai_diagnose")).scalar_one()
        assert secret not in audit_row.metadata_json
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF).status_code == 200
