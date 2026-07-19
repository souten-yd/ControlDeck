"""サンプルブック API と Chat アシスタント API のテスト。"""
import json

from tests.conftest import CSRF_HEADERS


def test_all_samples_are_valid():
    """全サンプル定義がエンジン検証を通ること（ノード追加/変更時の回帰検知）。"""
    from app.workflows import samplebook
    from app.workflows.engine import validate_definition

    assert len(samplebook.SAMPLES) >= 8
    ids = [s["id"] for s in samplebook.SAMPLES]
    assert len(ids) == len(set(ids)), "サンプル ID が重複"
    for s in samplebook.SAMPLES:
        validate_definition(json.dumps(s["definition"]))
        assert s["title"] and s["desc"] and s["usage"] and s["category"]


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
