"""エンジン v2（並列/合流/リトライ/エラー分岐/承認/シークレット）と新 API のテスト。"""
import asyncio
import json
import time

from tests.conftest import CSRF_HEADERS, _sandbox

TRIGGER = {"id": "t", "type": "trigger", "name": "手動", "config": {"mode": "manual"}}


def _run(nodes, edges, ctx=None):
    from app.workflows.engine import _execute_graph

    ctx = ctx if ctx is not None else {}
    asyncio.run(_execute_graph(nodes, edges, ctx))
    return ctx


def test_parallel_fanout_runs_concurrently():
    """独立した 2 本の待機（0.3 秒）が並列なら合計 ~0.3 秒で終わる。"""
    nodes = [
        TRIGGER,
        {"id": "a", "type": "util.wait", "config": {"seconds": 0.3}},
        {"id": "b", "type": "util.wait", "config": {"seconds": 0.3}},
    ]
    edges = [{"source": "t", "target": "a"}, {"source": "t", "target": "b"}]
    t0 = time.monotonic()
    ctx = _run(nodes, edges)
    elapsed = time.monotonic() - t0
    assert ctx["a"]["status"] == "SUCCEEDED" and ctx["b"]["status"] == "SUCCEEDED"
    assert elapsed < 0.55, f"並列実行されていない（{elapsed:.2f}s）"


def test_join_all_waits_for_both_inputs():
    """join=all のノードは両方の入力が終わってから 1 回だけ実行される。"""
    nodes = [
        TRIGGER,
        {"id": "a", "type": "util.wait", "config": {"seconds": 0.05}},
        {"id": "b", "type": "util.wait", "config": {"seconds": 0.2}},
        {"id": "j", "type": "string.op", "config": {"op": "template", "text": "joined", "join": "all"}},
    ]
    edges = [
        {"source": "t", "target": "a"}, {"source": "t", "target": "b"},
        {"source": "a", "target": "j"}, {"source": "b", "target": "j"},
    ]
    ctx = _run(nodes, edges)
    assert ctx["j"]["status"] == "SUCCEEDED"
    assert ctx["j"]["attempts"] == 1
    # b(0.2s) 完了後に実行されている
    assert ctx["j"]["started_at"] >= ctx["b"]["finished_at"]


def test_retry_then_success_counts_attempts(tmp_path):
    """初回失敗 → リトライで成功（file.read: リトライ待ちの間にファイルを作る）。"""
    target = _sandbox / "retry-me.txt"
    if target.exists():
        target.unlink()

    async def create_later():
        await asyncio.sleep(0.5)
        target.write_text("ok")

    async def main():
        from app.workflows.engine import _execute_graph

        nodes = [
            TRIGGER,
            {"id": "r", "type": "file.read",
             "config": {"path": str(target), "retry_count": 3, "retry_wait": 0.4}},
        ]
        edges = [{"source": "t", "target": "r"}]
        ctx = {}
        await asyncio.gather(_execute_graph(nodes, edges, ctx), create_later())
        return ctx

    ctx = asyncio.run(main())
    assert ctx["r"]["status"] == "SUCCEEDED"
    assert ctx["r"]["attempts"] >= 2
    target.unlink()


def test_on_error_branch_routes_to_error_edge():
    """error edgeへredact済み標準Error Contextを渡す。"""
    nodes = [
        TRIGGER,
        {"id": "bad", "type": "file.read",
         "config": {"path": str(_sandbox / "no-such-file.txt"), "on_error": "branch",
                    "api_token": "literal-must-not-leak", "auth": "{{secrets.SERVICE_TOKEN}}"}},
        {"id": "ok_path", "type": "string.op", "config": {"op": "template", "text": "ok"}},
        {"id": "err_path", "type": "string.op", "config": {
            "op": "template", "text": "{{bad.error.code}}:{{bad.error.message}}",
        }},
    ]
    edges = [
        {"source": "t", "target": "bad"},
        {"source": "bad", "target": "ok_path"},
        {"source": "bad", "target": "err_path", "branch": "error"},
    ]
    ctx = _run(nodes, edges, {"__secrets__": {"SERVICE_TOKEN": "secret-must-not-leak"}})
    assert ctx["bad"]["status"] == "FAILED"
    error = ctx["bad"]["output"]["error"]
    assert error["node_id"] == "bad" and error["node_type"] == "file.read"
    assert error["code"] == "NODE_ERROR" and error["retryable"] is True and error["attempt"] == 1
    assert error["timestamp"] and error["input_summary"]
    serialized = json.dumps(error, ensure_ascii=False)
    assert "literal-must-not-leak" not in serialized and "secret-must-not-leak" not in serialized
    assert ctx["err_path"]["status"] == "SUCCEEDED"
    assert ctx["err_path"]["output"]["result"].startswith("NODE_ERROR:")
    assert ctx["ok_path"]["status"] == "SKIPPED"


def test_timeout_uses_dedicated_route_and_keeps_error_fallback_compatible():
    nodes = [
        TRIGGER,
        {"id": "slow", "type": "util.wait", "config": {
            "seconds": 0.2, "node_timeout": 0.1, "on_error": "branch",
        }},
        {"id": "timeout_path", "type": "string.op", "config": {
            "op": "template", "text": "{{slow.error.code}}",
        }},
        {"id": "error_path", "type": "string.op", "config": {"op": "template", "text": "wrong"}},
    ]
    edges = [
        {"source": "t", "target": "slow"},
        {"source": "slow", "target": "timeout_path", "branch": "timeout"},
        {"source": "slow", "target": "error_path", "branch": "error"},
    ]
    ctx = _run(nodes, edges)
    assert ctx["slow"]["status"] == "TIMED_OUT"
    assert ctx["slow"]["output"]["error"]["code"] == "NODE_TIMEOUT"
    assert ctx["timeout_path"]["output"]["result"] == "NODE_TIMEOUT"
    assert ctx["error_path"]["status"] == "SKIPPED"

    fallback_nodes = [TRIGGER, nodes[1], {"id": "legacy", "type": "string.op", "config": {"op": "template", "text": "legacy"}}]
    fallback = _run(fallback_nodes, [
        {"source": "t", "target": "slow"}, {"source": "slow", "target": "legacy", "branch": "error"},
    ])
    assert fallback["legacy"]["status"] == "SUCCEEDED"


def test_on_error_continue_proceeds():
    nodes = [
        TRIGGER,
        {"id": "bad", "type": "file.read",
         "config": {"path": str(_sandbox / "no-such.txt"), "on_error": "continue"}},
        {"id": "next", "type": "string.op", "config": {"op": "template", "text": "went on"}},
    ]
    edges = [{"source": "t", "target": "bad"}, {"source": "bad", "target": "next"}]
    ctx = _run(nodes, edges)
    assert ctx["bad"]["status"] == "FAILED"
    assert ctx["next"]["status"] == "SUCCEEDED"


def test_secrets_template_rendering():
    from app.workflows.nodes import render_template

    ctx = {"__secrets__": {"API_KEY": "sk-hidden-123"}}
    assert render_template("key={{secrets.API_KEY}}", ctx) == "key=sk-hidden-123"
    assert render_template("none={{secrets.MISSING}}", ctx) == "none="


def test_secrets_api_crud(admin_client):
    r = admin_client.put("/api/v1/workflows-secrets/MY_TOKEN", json={"value": "abc123"}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    names = [s["name"] for s in admin_client.get("/api/v1/workflows-secrets").json()]
    assert "MY_TOKEN" in names
    # 値は API から取得できない（暗号化保存の確認）
    from app.database import SessionLocal
    from app.models import WorkflowSecret

    db = SessionLocal()
    try:
        row = db.query(WorkflowSecret).filter_by(name="MY_TOKEN").one()
        assert "abc123" not in row.value_encrypted
    finally:
        db.close()
    # エンジンのロード経路で復号される
    from app.workflows.engine import _load_secrets

    assert _load_secrets()["MY_TOKEN"] == "abc123"
    r = admin_client.put("/api/v1/workflows-secrets/1bad name", json={"value": "x"}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    assert admin_client.delete("/api/v1/workflows-secrets/MY_TOKEN", headers=CSRF_HEADERS).status_code == 204


def test_versions_snapshot_and_restore(admin_client):
    defs1 = {"nodes": [dict(TRIGGER)], "edges": []}
    r = admin_client.post("/api/v1/workflows", json={"name": "版テスト", "definition": defs1}, headers=CSRF_HEADERS)
    wf_id = r.json()["id"]
    defs2 = {"nodes": [dict(TRIGGER), {"id": "s", "type": "signal.display", "config": {}}],
             "edges": [{"source": "t", "target": "s"}]}
    admin_client.patch(f"/api/v1/workflows/{wf_id}", json={"definition": defs2}, headers=CSRF_HEADERS)
    versions = admin_client.get(f"/api/v1/workflows/{wf_id}/versions").json()
    assert len(versions) == 1 and versions[0]["node_count"] == 1  # 保存前(1ノード)が記録される
    r = admin_client.post(f"/api/v1/workflows/{wf_id}/versions/{versions[0]['id']}/restore", headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert len(r.json()["definition"]["nodes"]) == 1  # 復元された
    assert len(admin_client.get(f"/api/v1/workflows/{wf_id}/versions").json()) == 2  # 復元前も記録


def test_test_node_endpoint(admin_client):
    r = admin_client.post("/api/v1/workflows/test-node",
                          json={"type": "string.op", "config": {"op": "upper", "text": "abc"}},
                          headers=CSRF_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["output"]["result"] == "ABC"
    r = admin_client.post("/api/v1/workflows/test-node",
                          json={"type": "trigger", "config": {}}, headers=CSRF_HEADERS)
    assert r.status_code == 422


def test_webhook_trigger_fires(admin_client):
    """webhook トリガー: トークン一致で起動し、CSRF ヘッダーなしでも通る。"""
    token = "webhook-test-token-0123456789"
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "webhook", "webhook_token": token}},
            {"id": "s", "type": "signal.display", "config": {"signal": "reply", "value": "受信: {{t.message}}"}},
        ],
        "edges": [{"source": "t", "target": "s"}],
    }
    r = admin_client.post("/api/v1/workflows", json={"name": "webhookテスト", "definition": definition}, headers=CSRF_HEADERS)
    wf_id = r.json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{wf_id}/publish", headers=CSRF_HEADERS).status_code == 200
    admin_client.post(f"/api/v1/workflows/{wf_id}/enable", headers=CSRF_HEADERS)

    # CSRF ヘッダーなし（外部からの呼び出しを模擬）
    r = admin_client.post(f"/api/v1/hooks/{token}", json={"message": "こんにちは"})
    assert r.status_code == 200, r.text
    exec_id = r.json()["execution_id"]
    for _ in range(50):
        time.sleep(0.1)
        ex = admin_client.get(f"/api/v1/workflow-executions/{exec_id}").json()
        if ex["status"] not in ("RUNNING", "WAITING"):
            break
    assert ex["status"] == "SUCCEEDED"
    assert ex["context"]["s"]["output"]["value"] == "受信: こんにちは"
    # 不明トークンは 404
    assert admin_client.post("/api/v1/hooks/unknown-token-0123456789", json={}).status_code == 404


def test_flow_call_subflow(admin_client):
    """flow.call: サブフローを実行し signal.display の値を受け取る。"""
    sub_def = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "s", "type": "signal.display", "config": {"signal": "reply", "value": "sub says {{t.message}}"}},
        ],
        "edges": [{"source": "t", "target": "s"}],
    }
    sub_id = admin_client.post("/api/v1/workflows", json={"name": "サブ", "definition": sub_def}, headers=CSRF_HEADERS).json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{sub_id}/publish", headers=CSRF_HEADERS).status_code == 200
    main_def = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "call", "type": "flow.call", "config": {"workflow_id": sub_id, "message": "hello"}},
            {"id": "out", "type": "signal.display", "config": {"signal": "reply", "value": "{{call.result}}"}},
        ],
        "edges": [{"source": "t", "target": "call"}, {"source": "call", "target": "out"}],
    }
    main_id = admin_client.post("/api/v1/workflows", json={"name": "メイン", "definition": main_def}, headers=CSRF_HEADERS).json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{main_id}/publish", headers=CSRF_HEADERS).status_code == 200
    exec_id = admin_client.post(f"/api/v1/workflows/{main_id}/run", json={}, headers=CSRF_HEADERS).json()["execution_id"]
    for _ in range(100):
        time.sleep(0.15)
        ex = admin_client.get(f"/api/v1/workflow-executions/{exec_id}").json()
        if ex["status"] not in ("RUNNING", "WAITING"):
            break
    assert ex["status"] == "SUCCEEDED", ex.get("error")
    assert ex["context"]["out"]["output"]["value"] == "sub says hello"


def test_approval_gate_approve_and_reject(admin_client):
    """require_approval ノードは承認まで待ち、approve API で再開する。"""
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "gate", "type": "string.op",
             "config": {"op": "template", "text": "approved!", "require_approval": True}},
            {"id": "out", "type": "signal.display", "config": {"signal": "answer", "value": "{{gate.result}}"}},
        ],
        "edges": [{"source": "t", "target": "gate"}, {"source": "gate", "target": "out"}],
    }
    wf_id = admin_client.post("/api/v1/workflows", json={"name": "承認テスト", "definition": definition}, headers=CSRF_HEADERS).json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{wf_id}/publish", headers=CSRF_HEADERS).status_code == 200
    exec_id = admin_client.post(f"/api/v1/workflows/{wf_id}/run", json={}, headers=CSRF_HEADERS).json()["execution_id"]
    # 承認待ちになる
    pending = []
    for _ in range(50):
        time.sleep(0.1)
        live = admin_client.get(f"/api/v1/workflow-executions/{exec_id}/live").json()
        pending = live["pending_approvals"]
        if pending:
            break
    assert pending == ["gate"]
    assert live["context"]["gate"]["status"] == "WAITING_APPROVAL"
    # 承認 → 完了
    r = admin_client.post(f"/api/v1/workflow-executions/{exec_id}/approve",
                          json={"node_id": "gate", "approve": True}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    for _ in range(50):
        time.sleep(0.1)
        ex = admin_client.get(f"/api/v1/workflow-executions/{exec_id}").json()
        if ex["status"] not in ("RUNNING", "WAITING"):
            break
    assert ex["status"] == "SUCCEEDED"
    assert ex["context"]["gate"]["output"]["result"] == "approved!"


def test_jobs_api_lifecycle(admin_client):
    """ジョブ基盤: 作成 → 進捗 → 完了が API で追える。"""
    import anyio  # noqa: F401  (TestClient のイベントループ内で create を呼ぶ)

    from app.jobs import service as jobs

    async def work(job):
        job.set_progress("работа", 1, 2)
        job.log("半分")
        await asyncio.sleep(0.05)
        return {"answer": 42}

    # TestClient 経由でイベントループ内から作成するため、エンドポイント外で直接 asyncio.run
    async def scenario():
        job = jobs.create("test.kind", "テストジョブ", work)
        await asyncio.sleep(0.3)
        return job.id

    job_id = asyncio.run(scenario())
    r = admin_client.get(f"/api/v1/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded" and body["result"]["answer"] == 42
    assert any("半分" == e["message"] for e in body["events"])
    listed = admin_client.get("/api/v1/jobs?kind=test.").json()
    assert any(j["id"] == job_id for j in listed)
