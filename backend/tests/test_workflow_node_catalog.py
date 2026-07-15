from __future__ import annotations

import asyncio
import json

import pytest

from tests.conftest import _sandbox


def run(awaitable):
    return asyncio.run(awaitable)


def test_parallel_loop_isolates_iteration_context(monkeypatch):
    from app.workflows import nodes
    from app.workflows.engine import _execute_graph

    async def capture(config, ctx):
        item = nodes.render_template("{{loop.item}}", ctx)
        await asyncio.sleep(0.02 if item == "a" else 0)
        return {"seen": nodes.render_template("{{loop.item}}", ctx)}

    monkeypatch.setitem(nodes.NODE_EXECUTORS, "test.capture", capture)
    definition_nodes = [
        {"id": "t", "type": "trigger", "config": {}},
        {"id": "loop", "type": "control.loop", "config": {
            "mode": "foreach", "items": '["a","b","c"]', "parallel": 3,
        }},
        {"id": "capture", "type": "test.capture", "config": {}},
    ]
    edges = [
        {"source": "t", "target": "loop"},
        {"source": "loop", "target": "capture", "branch": "body"},
    ]
    context = {}
    run(_execute_graph(definition_nodes, edges, context))
    results = context["loop"]["output"]["results"]
    assert [item["outputs"]["capture"]["seen"] for item in results] == ["a", "b", "c"]
    assert context["capture"]["output"]["seen"] == "c"


def test_node_progress_is_recorded_without_cross_task_state(monkeypatch):
    from app.workflows import nodes
    from app.workflows.engine import _execute_graph

    async def progressing(config, ctx):
        nodes.report_progress(str(config["label"]), 1, 2)
        await asyncio.sleep(0)
        return {"ok": True}

    monkeypatch.setitem(nodes.NODE_EXECUTORS, "test.progress", progressing)
    graph_nodes = [
        {"id": "t", "type": "trigger", "config": {}},
        {"id": "a", "type": "test.progress", "config": {"label": "A"}},
        {"id": "b", "type": "test.progress", "config": {"label": "B"}},
    ]
    context = {}
    run(_execute_graph(graph_nodes, [{"source": "t", "target": "a"}, {"source": "t", "target": "b"}], context))
    assert context["a"]["progress"]["message"] == "A"
    assert context["b"]["progress"]["message"] == "B"


def test_data_transform_json_schema_and_csv():
    from app.workflows.nodes import node_data_transform

    parsed = run(node_data_transform({"operation": "json_get", "input": '{"a":[1,2]}', "path": "a.1"}, {}))
    assert parsed["value"] == 2
    changed = run(node_data_transform({
        "operation": "json_set", "input": '{"a":{"b":1}}', "path": "a.b", "value": "3",
    }, {}))
    assert changed["value"] == {"a": {"b": 3}}
    checked = run(node_data_transform({
        "operation": "schema_validate", "input": '{"n":"bad"}',
        "schema": '{"type":"object","properties":{"n":{"type":"number"}},"required":["n"]}',
    }, {}))
    assert checked["valid"] is False and checked["errors"]
    rows = run(node_data_transform({"operation": "csv_to_json", "input": "name,n\na,1\nb,2\n"}, {}))
    assert rows["count"] == 2 and rows["rows"][1]["name"] == "b"
    csv_out = run(node_data_transform({"operation": "json_to_csv", "input": json.dumps(rows["rows"])}, {}))
    assert "name,n" in csv_out["csv"] and csv_out["count"] == 2


def test_file_glob_filters_symlink_escape():
    from app.workflows.nodes import NodeError, node_file_glob

    base = _sandbox / "glob-base"
    outside = _sandbox / "glob-outside"
    base.mkdir(exist_ok=True)
    outside.mkdir(exist_ok=True)
    (base / "inside.txt").write_text("ok")
    (outside / "outside.txt").write_text("no")
    link = base / "escape"
    link.unlink(missing_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    result = run(node_file_glob({"base_path": str(base), "pattern": "**/*.txt", "kind": "files"}, {}))
    assert result["count"] == 1 and result["matches"][0]["name"] == "inside.txt"
    with pytest.raises(NodeError, match="相対指定"):
        run(node_file_glob({"base_path": str(base), "pattern": "../*.txt"}, {}))


@pytest.mark.parametrize(
    ("operation", "response", "expected_key"),
    [
        ("embedding", {"data": [{"embedding": [0.1, 0.2]}], "model": "embed"}, "vectors"),
        ("rerank", {"results": [{"index": 0, "relevance_score": 0.9}]}, "results"),
        ("judge", {"choices": [{"message": {"content": '{"score":88,"reason":"good"}'}}]}, "score"),
    ],
)
def test_ai_utility_normalizes_operations(monkeypatch, operation, response, expected_key):
    from app.models_mgmt import runtime_policy
    from app.workflows import nodes

    monkeypatch.setattr(runtime_policy, "ensure_gpu_profile", lambda **kwargs: None)

    class Response:
        status_code = 200
        content = json.dumps(response).encode()

        def json(self):
            return response

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(nodes.httpx, "AsyncClient", Client)
    config = {
        "operation": operation, "base_url": "http://127.0.0.1:8090/v1", "model": "model",
        "input": "hello", "query": "q", "documents": '["doc"]', "rubric": "quality",
    }
    result = run(nodes.node_ai_utility(config, {}))
    assert expected_key in result
