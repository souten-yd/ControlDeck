"""サンプルブック API と Chat アシスタント API のテスト。"""
import json
import time

from tests.conftest import CSRF_HEADERS


def _wait_execution(admin_client, execution_id: int, attempts: int = 160) -> dict:
    detail: dict = {}
    for _ in range(attempts):
        detail = admin_client.get(f"/api/v1/workflow-executions/{execution_id}").json()
        if detail.get("status") not in ("QUEUED", "RUNNING", "WAITING"):
            return detail
        time.sleep(0.03)
    return detail


def _install_publish(admin_client, sample_id: str) -> tuple[int, dict]:
    installed = admin_client.post(
        f"/api/v1/workflows/samples/{sample_id}/install", headers=CSRF_HEADERS,
    )
    assert installed.status_code == 201, installed.text
    workflow_id = installed.json()["id"]
    published = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS,
    )
    assert published.status_code == 200, published.text
    return workflow_id, admin_client.get(f"/api/v1/workflows/{workflow_id}").json()


def test_all_samples_are_valid():
    """全サンプル定義がエンジン検証を通ること（ノード追加/変更時の回帰検知）。"""
    from app.workflows import samplebook
    from app.workflows.engine import validate_definition

    assert len(samplebook.SAMPLES) >= 19
    ids = [s["id"] for s in samplebook.SAMPLES]
    assert len(ids) == len(set(ids)), "サンプル ID が重複"
    assert {
        "execution-time-travel", "local-llm-route", "pc-state-recovery",
        "ai-patch-recovery", "regression-batch",
    } <= set(ids)
    required_guide = {
        "goal", "difficulty", "estimated_minutes", "required_capabilities", "side_effects",
        "required_resources", "typed_input", "typed_output", "sample_input", "expected_assertions",
        "mock_data", "node_walkthrough", "failure_injection", "recovery_retry", "install_preview",
    }
    for s in samplebook.SAMPLES:
        validate_definition(json.dumps(s["definition"]))
        assert s["title"] and s["desc"] and s["usage"] and s["category"]
        assert required_guide <= set(s["guide"]), f"{s['id']}: guide fields missing"
        assert s["guide"]["goal"] and s["guide"]["estimated_minutes"] >= 5
        assert len(s["guide"]["failure_injection"]) >= 2
        assert len(s["guide"]["node_walkthrough"]) == len(s["definition"]["nodes"])
        assert s["guide"]["install_preview"]["node_count"] == len(s["definition"]["nodes"])


def test_every_node_has_complete_canonical_documentation():
    from app.workflows.node_metadata import node_catalog

    required = {
        "purpose", "when_to_use", "when_not_to_use", "configuration", "typed_inputs",
        "typed_outputs", "variable_examples", "side_effect", "permissions", "secrets",
        "retry_timeout_error_route", "representative_errors", "performance_cost", "recipes",
        "migration_note",
    }
    for node in node_catalog():
        docs = node["documentation"]
        assert required <= set(docs), node["type"]
        assert docs["purpose"] and len(docs["when_to_use"]) >= 2 and len(docs["when_not_to_use"]) >= 2
        assert len(docs["recipes"]) >= 2 and docs["representative_errors"]
        assert {item["key"] for item in docs["configuration"]} == set(node["config_schema"])
        assert docs["typed_outputs"] == node["output_schema"]
        assert "workflows.run" in docs["permissions"]


def test_catalog_covers_only_known_node_types():
    """LLM カタログのノード type が実行エンジンに存在すること。"""
    from app.workflows import catalog
    from app.workflows.nodes import NODE_EXECUTORS

    known = set(NODE_EXECUTORS) | {"control.loop"}
    unknown = catalog.valid_types() - known
    assert not unknown, f"カタログに未実装ノードがある: {unknown}"
    assert "\n" in catalog.catalog_prompt()


def test_samples_list_and_install(admin_client):
    r = admin_client.get("/api/v1/workflows/samples")
    assert r.status_code == 200, r.text
    samples = r.json()
    assert any(s["id"] == "hello-llm" for s in samples)
    sample = samples[0]
    assert sample["node_count"] == len(sample["definition"]["nodes"])
    assert sample["node_types"]

    # コピー登録 → ワークフロー一覧に出て、エディタで開ける形式であること
    r = admin_client.post(f"/api/v1/workflows/samples/{sample['id']}/install", headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]
    r = admin_client.get(f"/api/v1/workflows/{wf_id}")
    assert r.status_code == 200
    assert r.json()["definition"]["nodes"]

    r = admin_client.post("/api/v1/workflows/samples/nope/install", headers=CSRF_HEADERS)
    assert r.status_code == 404


def test_every_sample_is_safe_previewable_and_publishable(admin_client):
    """サンプルが見本だけで終わらず、コピー直後に公開できることを保証する。"""
    samples = admin_client.get("/api/v1/workflows/samples").json()
    assert any(sample["id"] == "order-analysis" and sample["node_count"] >= 6 for sample in samples)
    for sample in samples:
        preview = admin_client.post(
            "/api/v1/workflows/preview-definition",
            json={"definition": sample["definition"], "input": {}},
            headers=CSRF_HEADERS,
        )
        assert preview.status_code == 200, f"{sample['id']}: {preview.text}"
        assert preview.json()["valid"] is True, f"{sample['id']}: {preview.json()['errors']}"

        installed = admin_client.post(
            f"/api/v1/workflows/samples/{sample['id']}/install", headers=CSRF_HEADERS,
        )
        assert installed.status_code == 201, f"{sample['id']}: {installed.text}"
        workflow_id = installed.json()["id"]
        checked = admin_client.post(
            f"/api/v1/workflows/{workflow_id}/publish-check",
            json={"definition": sample["definition"]},
            headers=CSRF_HEADERS,
        )
        assert checked.status_code == 200
        assert checked.json()["publishable"] is True, f"{sample['id']}: {checked.json()['blocking']}"
        published = admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS)
        assert published.status_code == 200, f"{sample['id']}: {published.text}"
        assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_complex_order_sample_executes_with_typed_outputs(admin_client):
    """複合サンプルは公開できるだけでなく、外部依存なしで最終出力まで実行できる。"""
    import time

    installed = admin_client.post(
        "/api/v1/workflows/samples/order-analysis/install", headers=CSRF_HEADERS,
    )
    workflow_id = installed.json()["id"]
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS).status_code == 200
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run",
        json={"input": {
            "orders": [
                {"id": "A", "region": "東", "amount": 9000},
                {"id": "B", "region": "西", "amount": 3000},
                {"id": "C", "region": "東", "amount": 7000},
            ],
            "minimum": 5000,
        }},
        headers=CSRF_HEADERS,
    )
    assert started.status_code == 200, started.text
    execution_id = started.json()["execution_id"]
    for _ in range(100):
        execution = admin_client.get(f"/api/v1/workflow-executions/{execution_id}").json()
        if execution["status"] not in ("QUEUED", "RUNNING", "WAITING"):
            break
        time.sleep(0.03)
    assert execution["status"] == "SUCCEEDED", execution
    assert execution["outputs"]["order_count"]["value"] == "2"
    assert len(execution["outputs"]["orders"]["value"]) == 2
    assert execution["outputs"]["sales_by_region"]["value"] == [
        {"group": "東", "value": 16000.0, "count": 2},
    ]
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_regression_batch_sample_executes_and_asserts_typed_output(admin_client):
    workflow_id, _ = _install_publish(admin_client, "regression-batch")
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run",
        json={"input": {"items": [1, 2, 3, 4, 5]}}, headers=CSRF_HEADERS,
    )
    assert started.status_code == 200, started.text
    execution = _wait_execution(admin_client, started.json()["execution_id"])
    assert execution["status"] == "SUCCEEDED", execution
    assert execution["outputs"]["batches"]["value"] == [[1, 2], [3, 4], [5]]
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_time_travel_sample_replays_historical_and_current_versions(admin_client):
    workflow_id, workflow = _install_publish(admin_client, "execution-time-travel")
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run",
        json={"input": {"message": "same input"}}, headers=CSRF_HEADERS,
    )
    original = _wait_execution(admin_client, started.json()["execution_id"])
    assert original["status"] == "SUCCEEDED", original
    assert original["outputs"]["result"]["value"] == "v1: same input"

    definition = workflow["definition"]
    format_node = next(node for node in definition["nodes"] if node["id"] == "format")
    format_node["config"]["template"] = "v2: {{trigger.message}}"
    patched = admin_client.patch(
        f"/api/v1/workflows/{workflow_id}", json={"definition": definition}, headers=CSRF_HEADERS,
    )
    assert patched.status_code == 200, patched.text
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS).status_code == 200

    replayed = {}
    for mode in ("historical", "current"):
        retry = admin_client.post(
            f"/api/v1/workflows/{workflow_id}/executions/{original['id']}/retry",
            json={"version_mode": mode}, headers=CSRF_HEADERS,
        )
        assert retry.status_code == 200, retry.text
        replayed[mode] = _wait_execution(admin_client, retry.json()["execution_id"])
    assert replayed["historical"]["outputs"]["result"]["value"] == "v1: same input"
    assert replayed["current"]["outputs"]["result"]["value"] == "v2: same input"
    assert replayed["historical"]["workflow_version_id"] == original["workflow_version_id"]
    assert replayed["current"]["workflow_version_id"] != original["workflow_version_id"]
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_ai_patch_sample_fails_then_diagnosis_patch_recovers(admin_client):
    workflow_id, workflow = _install_publish(admin_client, "ai-patch-recovery")
    failed_start = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={"input": {}}, headers=CSRF_HEADERS,
    )
    failed = _wait_execution(admin_client, failed_start.json()["execution_id"])
    # Node status is TIMED_OUT; the workflow-level terminal status is FAILED.
    assert failed["status"] == "FAILED", failed

    diagnosis = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/intelligence/diagnose",
        json={"execution_id": failed["id"], "use_ai": False}, headers=CSRF_HEADERS,
    )
    assert diagnosis.status_code == 200, diagnosis.text
    option = diagnosis.json()["options"][0]
    assert option["operations"] and option["preview"]["valid"] is True
    applied = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/intelligence/patch-apply",
        json={
            "patch_version": 1, "operations": option["operations"],
            "expected_updated_at": workflow["updated_at"],
        }, headers=CSRF_HEADERS,
    )
    assert applied.status_code == 200, applied.text
    assert admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS).status_code == 200
    recovered_start = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={"input": {}}, headers=CSRF_HEADERS,
    )
    recovered = _wait_execution(admin_client, recovered_start.json()["execution_id"])
    assert recovered["status"] == "SUCCEEDED", recovered
    assert recovered["outputs"]["result"]["value"] == "timeoutを解消しました"
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_pc_state_recovery_sample_persists_progress(admin_client):
    workflow_id, workflow = _install_publish(admin_client, "pc-state-recovery")
    definition = workflow["definition"]
    checkpoint = next(node for node in definition["nodes"] if node["id"] == "checkpoint")
    checkpoint["config"]["seconds"] = 0.1
    assert admin_client.patch(
        f"/api/v1/workflows/{workflow_id}", json={"definition": definition}, headers=CSRF_HEADERS,
    ).status_code == 200
    republished = admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS)
    assert republished.status_code == 200, republished.text
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={"input": {}}, headers=CSRF_HEADERS,
    )
    execution = _wait_execution(admin_client, started.json()["execution_id"])
    assert execution["status"] == "SUCCEEDED", execution
    assert execution["outputs"]["progress"]["value"] == "1"
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_install_substitutes_llm_model(admin_client):
    """コピー時に base_url/model を渡すとサンプル既定 LLM が差し替わる。"""
    r = admin_client.post(
        "/api/v1/workflows/samples/hello-llm/install",
        json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen3:8b"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    wf = admin_client.get(f"/api/v1/workflows/{r.json()['id']}").json()
    llm = next(n for n in wf["definition"]["nodes"] if n["type"] == "llm.chat")
    assert llm["config"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert llm["config"]["model"] == "qwen3:8b"

    # 元のサンプル定義は書き換わっていない（deep copy されている）
    from app.workflows import samplebook

    original = next(s for s in samplebook.SAMPLES if s["id"] == "hello-llm")
    llm0 = next(n for n in original["definition"]["nodes"] if n["type"] == "llm.chat")
    assert llm0["config"]["model"] == samplebook.MODEL


def test_register_workflow_validates_definition(admin_client):
    bad = {"name": "x", "definition": {"nodes": [{"id": "a", "type": "nope"}], "edges": []}}
    r = admin_client.post("/api/v1/chat/register-workflow", json=bad, headers=CSRF_HEADERS)
    assert r.status_code == 422

    good = {
        "name": "chat 登録テスト",
        "definition": {
            "nodes": [
                {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
                {"id": "s", "type": "signal.display", "config": {"signal": "reply", "value": "ok"}},
            ],
            "edges": [{"source": "t", "target": "s"}],
        },
    }
    r = admin_client.post("/api/v1/chat/register-workflow", json=good, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    assert r.json()["id"]


def test_chat_search_rejects_unknown_mode(admin_client):
    r = admin_client.post(
        "/api/v1/chat/search",
        json={"query": "テスト", "mode": "nope"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 422


def test_generated_definition_validator():
    from app.workflows.chat_router import _extract_json, _validate_generated

    data = _extract_json('前置き {"name":"x","nodes":[],"edges":[]} 後置き')
    assert data["name"] == "x"
    fenced = _extract_json('説明 {未完}\n```json\n{"name":"fenced","nodes":[],"edges":[]}\n```')
    assert fenced["name"] == "fenced"

    ok = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "s", "type": "signal.display", "config": {"value": "ok"}},  # value 必須（意味検証）
        ],
        "edges": [{"source": "trigger", "target": "s"}],
    }
    assert _validate_generated(ok) == []
    bad = {"nodes": [{"id": "a", "type": "magic.node", "config": {}}], "edges": []}
    problems = _validate_generated(bad)
    assert problems and "magic.node" in problems[0]


def test_workflow_generation_uses_configured_schema_mode(admin_client, monkeypatch):
    """reasoningを無制限に走らせず、構造化出力を要求する。"""
    from app.workflows import chat_router

    seen = {}

    async def fake_llm(messages, base_url, model, api_key, temperature=0.4, **kwargs):
        seen.update(kwargs)
        return json.dumps({
            "name": "生成テスト",
            "nodes": [
                {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
                {"id": "out", "type": "signal.display", "config": {"value": "ok"}},
            ],
            "edges": [{"source": "trigger", "target": "out"}],
        })

    monkeypatch.setattr(chat_router, "_llm", fake_llm)
    monkeypatch.setattr(chat_router, "_workflow_max_tokens", lambda base_url, model: 32768)
    r = admin_client.post(
        "/api/v1/chat/generate-workflow",
        json={"goal": "okを表示"},
        headers=CSRF_HEADERS,
    )

    assert r.status_code == 200, r.text
    assert r.json()["valid"] is True
    assert seen["max_tokens"] == 32768
    assert seen["disable_thinking"] is True
    assert seen["response_format"]["type"] == "json_schema"


def test_persistent_chat_defaults_to_fast_non_thinking_mode():
    from app.workflows.chat_persist import SendBody

    body = SendBody(content="hello")
    assert body.thinking is None
    assert not hasattr(body, "max_output_tokens")
