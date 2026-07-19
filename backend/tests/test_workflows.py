import asyncio
import json

import pytest

from tests.conftest import CSRF_HEADERS, _sandbox


def _definition(nodes, edges):
    return {"nodes": nodes, "edges": edges}


TRIGGER = {"id": "t", "type": "trigger", "name": "手動", "config": {"mode": "manual"}}


def test_validate_rejects_bad_definitions():
    from app.workflows.engine import DefinitionError, validate_definition

    with pytest.raises(DefinitionError):
        validate_definition(json.dumps({"nodes": [{"id": "a", "type": "nope"}], "edges": []}))
    with pytest.raises(DefinitionError):
        validate_definition(
            json.dumps({"nodes": [TRIGGER], "edges": [{"source": "t", "target": "ghost"}]})
        )
    with pytest.raises(DefinitionError):  # トリガー 2 個
        validate_definition(
            json.dumps({"nodes": [TRIGGER, {**TRIGGER, "id": "t2"}], "edges": []})
        )


def test_template_rendering():
    from app.workflows.nodes import render_template

    ctx = {"n1": {"output": {"status_code": 200, "nested": {"key": "値"}}}}
    assert render_template("code={{n1.status_code}}", ctx) == "code=200"
    assert render_template("v={{ n1.nested.key }}", ctx) == "v=値"
    assert render_template("missing={{nope.x}}", ctx) == "missing="


def test_condition_and_wait_graph():
    """trigger → wait → condition → (true) file.exists のグラフを直接実行する。"""
    from app.workflows.engine import _execute_graph

    (_sandbox / "wf-flag.txt").write_text("x")
    nodes = [
        TRIGGER,
        {"id": "w", "type": "util.wait", "config": {"seconds": 0.05}},
        {"id": "c", "type": "condition.if", "config": {"left": "1", "op": "eq", "right": "1"}},
        {"id": "f", "type": "file.exists", "config": {"path": str(_sandbox / "wf-flag.txt")}},
        {"id": "never", "type": "util.wait", "config": {"seconds": 0}},
    ]
    edges = [
        {"source": "t", "target": "w"},
        {"source": "w", "target": "c"},
        {"source": "c", "target": "f", "branch": "true"},
        {"source": "c", "target": "never", "branch": "false"},
    ]
    ctx = {}
    asyncio.run(_execute_graph(nodes, edges, ctx))
    assert ctx["c"]["output"]["result"] is True
    assert ctx["f"]["output"]["exists"] is True
    # v2: false ブランチは実行されず SKIPPED として記録される（dead-path 伝播）
    assert ctx["never"]["status"] == "SKIPPED"
    assert "output" not in ctx["never"]


def test_workflow_api_crud_and_run(admin_client):
    definition = _definition(
        [TRIGGER, {"id": "w", "type": "util.wait", "name": "待機", "config": {"seconds": 0.05}}],
        [{"source": "t", "target": "w"}],
    )
    r = admin_client.post(
        "/api/v1/workflows",
        json={"name": "テストWF", "definition": definition},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    # 不正定義は 422
    r = admin_client.patch(
        f"/api/v1/workflows/{wf_id}",
        json={"definition": {"nodes": [{"id": "x", "type": "bad"}], "edges": []}},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 422

    # 実行 → 完了までポーリング
    r = admin_client.post(f"/api/v1/workflows/{wf_id}/run", headers=CSRF_HEADERS)
    assert r.status_code == 200
    execution_id = r.json()["execution_id"]
    import time

    for _ in range(50):
        r = admin_client.get(f"/api/v1/workflow-executions/{execution_id}")
        if r.json()["status"] not in ("QUEUED", "RUNNING"):
            break
        time.sleep(0.1)
    body = r.json()
    assert body["status"] == "SUCCEEDED", body
    assert body["context"]["w"]["output"]["waited_seconds"] == 0.05

    # enable / disable
    assert admin_client.post(f"/api/v1/workflows/{wf_id}/enable", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.post(f"/api/v1/workflows/{wf_id}/disable", headers=CSRF_HEADERS).status_code == 200
    # 削除
    assert admin_client.delete(f"/api/v1/workflows/{wf_id}", headers=CSRF_HEADERS).status_code == 200


def test_preview_test_outputs_and_historical_inputs_are_integrated_and_redacted(admin_client):
    definition = _definition(
        [
            TRIGGER,
            {
                "id": "result",
                "type": "signal.display",
                "name": "結果",
                "config": {"signal": "answer", "value": "{{t.message}} / {{t.password}}"},
            },
        ],
        [{"source": "t", "target": "result"}],
    )
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "preview-loop", "definition": definition},
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]

    preview = admin_client.post(
        "/api/v1/workflows/preview-definition",
        json={"definition": definition, "input": {"message": "hello", "api_token": "hidden"}},
        headers=CSRF_HEADERS,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["dry_run"] is True
    assert preview.json()["input"]["api_token"] == "***"

    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/test",
        json={"input": {"message": "hello", "password": "do-not-store"}},
        headers=CSRF_HEADERS,
    )
    assert started.status_code == 200, started.text
    execution_id = started.json()["execution_id"]

    import time

    detail = None
    for _ in range(50):
        response = admin_client.get(f"/api/v1/workflow-executions/{execution_id}")
        detail = response.json()
        if detail["status"] not in ("QUEUED", "RUNNING"):
            break
        time.sleep(0.1)
    assert detail is not None and detail["status"] == "SUCCEEDED", detail
    assert detail["input"] == {"message": "hello", "password": "***"}
    assert detail["outputs"]["answer"]["value"] == "hello / ***"
    assert "do-not-store" not in json.dumps(detail, ensure_ascii=False)

    loaded = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/executions/{execution_id}/load-inputs",
        headers=CSRF_HEADERS,
    )
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["input"] == {"message": "hello", "password": "***"}

    from app.database import SessionLocal
    from app.models import WorkflowExecution

    with SessionLocal() as db:
        stored = db.get(WorkflowExecution, execution_id)
        assert stored is not None
        assert "do-not-store" not in stored.context_json

    admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS)


def test_execution_snapshot_node_runs_and_current_or_historical_retry(admin_client):
    import time

    def wait_for(execution_id: int) -> dict:
        detail = {}
        for _ in range(80):
            response = admin_client.get(f"/api/v1/workflow-executions/{execution_id}")
            detail = response.json()
            if detail.get("status") not in ("QUEUED", "RUNNING", "WAITING"):
                return detail
            time.sleep(0.05)
        return detail

    old_definition = _definition([
        TRIGGER,
        {"id": "result", "type": "signal.display", "name": "出力", "config": {
            "signal": "answer", "value": "old {{t.message}}",
            "api_key": "literal-must-not-enter-snapshot", "auth": "{{secrets.SERVICE_TOKEN}}",
        }},
    ], [{"source": "t", "target": "result"}])
    created = admin_client.post(
        "/api/v1/workflows", json={"name": "replay", "definition": old_definition}, headers=CSRF_HEADERS,
    )
    workflow_id = created.json()["id"]
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/test", json={"input": {"message": "hello"}}, headers=CSRF_HEADERS,
    )
    original_id = started.json()["execution_id"]
    original = wait_for(original_id)
    assert original["status"] == "SUCCEEDED"
    assert original["outputs"]["answer"]["value"] == "old hello"
    serialized = json.dumps(original, ensure_ascii=False)
    assert "literal-must-not-enter-snapshot" not in serialized
    assert "{{secrets.SERVICE_TOKEN}}" in serialized
    assert original["workflow_version_id"]
    assert original["runtime_snapshot"]["node_versions"] == {"t": 1, "result": 1}

    node_runs = admin_client.get(
        f"/api/v1/workflows/{workflow_id}/executions/{original_id}/nodes"
    )
    assert node_runs.status_code == 200, node_runs.text
    rows = node_runs.json()
    assert [row["node_id"] for row in rows] == ["t", "result"]
    assert all(row["status"] == "SUCCEEDED" and row["elapsed_ms"] is not None for row in rows)
    assert "literal-must-not-enter-snapshot" not in json.dumps(rows, ensure_ascii=False)

    version = admin_client.get(
        f"/api/v1/workflows/{workflow_id}/versions/{original['workflow_version_id']}"
    )
    assert version.status_code == 200
    assert version.json()["checksum"] and "literal-must-not-enter-snapshot" not in version.text

    new_definition = json.loads(json.dumps(old_definition))
    new_definition["nodes"][1]["config"]["value"] = "new {{t.message}}"
    patched = admin_client.patch(
        f"/api/v1/workflows/{workflow_id}", json={"definition": new_definition}, headers=CSRF_HEADERS,
    )
    assert patched.status_code == 200

    historical = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/executions/{original_id}/retry",
        json={"version_mode": "historical"}, headers=CSRF_HEADERS,
    )
    current = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/executions/{original_id}/retry",
        json={"version_mode": "current"}, headers=CSRF_HEADERS,
    )
    assert historical.status_code == 200 and current.status_code == 200
    historical_detail = wait_for(historical.json()["execution_id"])
    current_detail = wait_for(current.json()["execution_id"])
    assert historical_detail["outputs"]["answer"]["value"] == "old hello"
    assert current_detail["outputs"]["answer"]["value"] == "new hello"
    assert historical_detail["workflow_version_id"] == original["workflow_version_id"]
    assert current_detail["workflow_version_id"] != original["workflow_version_id"]

    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_node_test_pinned_data_and_resume_from_cached_upstream(admin_client):
    import time

    def wait_for(execution_id: int) -> dict:
        for _ in range(80):
            detail = admin_client.get(f"/api/v1/workflow-executions/{execution_id}").json()
            if detail.get("status") not in ("QUEUED", "RUNNING", "WAITING"):
                return detail
            time.sleep(0.05)
        return detail

    definition = _definition([
        TRIGGER,
        {"id": "a", "type": "string.op", "name": "上流", "config": {
            "op": "upper", "text": "old {{t.message}}", "output_var": "cached_a",
        }},
        {"id": "b", "type": "string.op", "name": "再開点", "config": {
            "op": "upper", "text": "{{a.result}} b-old",
        }},
        {"id": "out", "type": "signal.display", "name": "出力", "config": {
            "signal": "answer", "value": "{{b.result}}",
        }},
    ], [
        {"source": "t", "target": "a"}, {"source": "a", "target": "b"},
        {"source": "b", "target": "out"},
    ])
    created = admin_client.post(
        "/api/v1/workflows", json={"name": "node replay", "definition": definition}, headers=CSRF_HEADERS,
    )
    workflow_id = created.json()["id"]
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/test", json={"input": {"message": "hello"}}, headers=CSRF_HEADERS,
    )
    original_id = started.json()["execution_id"]
    original = wait_for(original_id)
    assert original["outputs"]["answer"]["value"] == "OLD HELLO B-OLD"

    tested = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/b/test",
        json={"input_mode": "execution", "execution_id": original_id}, headers=CSRF_HEADERS,
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["output"]["result"] == "OLD HELLO B-OLD"
    assert tested.json()["source_execution_id"] == original_id

    run_to = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/b/run-to",
        json={"input": {"message": "until"}}, headers=CSRF_HEADERS,
    )
    assert run_to.status_code == 200, run_to.text
    run_to_detail = wait_for(run_to.json()["execution_id"])
    assert run_to_detail["status"] == "SUCCEEDED"
    assert run_to_detail["context"]["b"]["output"]["result"] == "OLD UNTIL B-OLD"
    assert "out" not in run_to_detail["context"]
    assert run_to_detail["runtime_snapshot"]["run_to_node_id"] == "b"

    pinned = admin_client.put(
        f"/api/v1/workflows/{workflow_id}/nodes/a/pinned-data",
        json={"output": {"result": "fixed", "api_token": "must-redact"}, "source_execution_id": original_id},
        headers=CSRF_HEADERS,
    )
    assert pinned.status_code == 200 and pinned.json()["output"]["api_token"] == "***"
    cached = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/a/test",
        json={"input_mode": "pinned"}, headers=CSRF_HEADERS,
    )
    assert cached.json()["status"] == "CACHED" and cached.json()["output"]["result"] == "fixed"

    changed = json.loads(json.dumps(definition))
    changed["nodes"][1]["config"]["text"] = "new {{t.message}}"
    changed["nodes"][2]["config"]["text"] = "{{a.result}} b-new"
    assert admin_client.patch(
        f"/api/v1/workflows/{workflow_id}", json={"definition": changed}, headers=CSRF_HEADERS,
    ).status_code == 200
    resumed = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/executions/{original_id}/resume-from/b",
        json={"version_mode": "current"}, headers=CSRF_HEADERS,
    )
    assert resumed.status_code == 200, resumed.text
    resumed_detail = wait_for(resumed.json()["execution_id"])
    assert resumed_detail["outputs"]["answer"]["value"] == "OLD HELLO B-NEW"
    assert resumed_detail["context"]["a"]["output"]["result"] == "OLD HELLO"
    resumed_runs = admin_client.get(
        f"/api/v1/workflows/{workflow_id}/executions/{resumed.json()['execution_id']}/nodes"
    ).json()
    assert [row["node_id"] for row in resumed_runs] == ["t", "b", "out"]
    assert resumed_detail["runtime_snapshot"]["resume_from_node_id"] == "b"

    assert admin_client.delete(
        f"/api/v1/workflows/{workflow_id}/nodes/a/pinned-data", headers=CSRF_HEADERS,
    ).status_code == 204
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_viewer_cannot_run_workflows(client):
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "ro", "password": "viewer-pass-123"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    assert client.get("/api/v1/workflows").status_code == 403  # viewer に workflows.run はない
    client.cookies.clear()


def test_schedule_judgement():
    from datetime import datetime, timedelta, timezone

    from app.workflows.engine import _next_run_after

    now = datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)
    # interval: 前回から 60 分経過で実行
    assert _next_run_after({"mode": "interval", "interval_minutes": 60}, now - timedelta(minutes=61), now)
    assert not _next_run_after({"mode": "interval", "interval_minutes": 60}, now - timedelta(minutes=30), now)
    # daily: 指定時刻を過ぎ、当日未実行なら実行
    assert _next_run_after({"mode": "daily", "time": "08:30"}, now - timedelta(days=1), now)
    assert not _next_run_after({"mode": "daily", "time": "09:30"}, now - timedelta(days=1), now)
    # cron: 毎時 0 分
    assert _next_run_after({"mode": "cron", "cron": "0 * * * *"}, now - timedelta(hours=2), now)
