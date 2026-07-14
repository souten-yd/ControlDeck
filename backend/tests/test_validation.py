"""ワークフロー意味検証・品質スコアのテスト。"""


def _t(**c):
    return {"id": "trigger", "type": "trigger", "config": {"mode": "manual", **c}}


def test_detects_dangling_reference():
    from app.workflows.validation import semantic_check

    nodes = [_t(), {"id": "a", "type": "llm.chat", "config": {"model": "m", "prompt": "{{nope.x}}"}}]
    edges = [{"source": "trigger", "target": "a"}]
    errors, _ = semantic_check(nodes, edges)
    assert any("存在しない変数" in e for e in errors)


def test_allows_valid_references():
    from app.workflows.validation import semantic_check

    nodes = [
        _t(),
        {"id": "a", "type": "web.search", "config": {"query": "x"}},
        {"id": "b", "type": "llm.chat", "config": {"model": "m", "prompt": "{{a.text}} {{trigger.message}} {{vars.y}} {{secrets.k}}"}},
    ]
    edges = [{"source": "trigger", "target": "a"}, {"source": "a", "target": "b"}]
    errors, _ = semantic_check(nodes, edges)
    assert errors == []


def test_detects_missing_required():
    from app.workflows.validation import semantic_check

    nodes = [_t(), {"id": "a", "type": "rag.query", "config": {"collection": "docs"}}]  # question 欠落
    edges = [{"source": "trigger", "target": "a"}]
    errors, _ = semantic_check(nodes, edges)
    assert any("question" in e for e in errors)


def test_detects_unreachable():
    from app.workflows.validation import semantic_check

    nodes = [_t(), {"id": "a", "type": "util.now", "config": {}}, {"id": "orphan", "type": "util.now", "config": {}}]
    edges = [{"source": "trigger", "target": "a"}]
    _, warnings = semantic_check(nodes, edges)
    assert any("到達できません" in w and "orphan" in w for w in warnings)


def test_quality_score_ranges():
    from app.workflows.validation import quality_score

    good = [
        _t(),
        {"id": "a", "type": "web.search", "config": {"query": "x", "retry_count": 2}},
        {"id": "b", "type": "signal.display", "config": {"value": "{{a.text}}"}},
    ]
    edges = [{"source": "trigger", "target": "a"}, {"source": "a", "target": "b"}]
    q_ok = quality_score(good, edges, run_ok=True)
    assert q_ok["score"] >= 90 and q_ok["label"] == "動作確認済み"

    bad = [_t(), {"id": "a", "type": "llm.chat", "config": {"model": "", "prompt": ""}}]
    q_bad = quality_score(bad, [{"source": "trigger", "target": "a"}], run_ok=None)
    assert q_bad["score"] < q_ok["score"] and q_bad["label"] == "要修正"


def test_generate_workflow_validator_includes_semantic():
    from app.workflows.chat_router import _validate_generated

    ok = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "s", "type": "signal.display", "config": {"value": "hi"}},
        ],
        "edges": [{"source": "trigger", "target": "s"}],
    }
    assert _validate_generated(ok) == []
    # 意味エラー（必須欠落）を検出
    bad = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "l", "type": "llm.chat", "config": {"model": "", "prompt": ""}},
        ],
        "edges": [{"source": "trigger", "target": "l"}],
    }
    problems = _validate_generated(bad)
    assert problems and any("必須設定" in p for p in problems)
