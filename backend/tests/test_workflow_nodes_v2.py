import asyncio

import pytest

from tests.conftest import CSRF_HEADERS, _sandbox


def run(coro):
    return asyncio.run(coro)


def test_string_ops():
    from app.workflows.nodes import node_string_op

    assert run(node_string_op({"text": " Hi ", "op": "trim"}, {}))["result"] == "Hi"
    assert run(node_string_op({"text": "ab", "op": "upper"}, {}))["result"] == "AB"
    assert run(node_string_op({"text": "a,b,c", "op": "split", "sep": ","}, {}))["result"] == ["a", "b", "c"]
    assert run(node_string_op({"text": "hello world", "op": "replace", "find": "world", "replace": "deck"}, {}))["result"] == "hello deck"
    assert run(node_string_op({"text": '{"a": {"b": 5}}', "op": "json_extract", "path": "a.b"}, {}))["result"] == "5"


def test_variable_and_template_chain():
    from app.workflows.nodes import node_set_variable, node_string_op

    ctx = {}
    ctx["v"] = {"output": run(node_set_variable({"value": "World"}, ctx))}
    out = run(node_string_op({"text": "Hello {{v.value}}", "op": "template"}, ctx))
    assert out["result"] == "Hello World"


def test_markdown():
    from app.workflows.nodes import node_markdown

    out = run(node_markdown({"text": "# 見出し\n\n- a\n- b"}, {}))
    assert "<h1>見出し</h1>" in out["html"]
    assert "<li>a</li>" in out["html"]


def test_file_read_write(monkeypatch):
    from app.workflows.nodes import node_file_read, node_file_write

    target = str(_sandbox / "wf-io.txt")
    run(node_file_write({"path": target, "content": "line1\n"}, {}))
    run(node_file_write({"path": target, "content": "line2\n", "append": True}, {}))
    out = run(node_file_read({"path": target}, {}))
    assert out["content"] == "line1\nline2\n"


def test_file_write_outside_root_rejected():
    from app.workflows.nodes import NodeError, node_file_write

    with pytest.raises(NodeError):
        run(node_file_write({"path": "/etc/evil.txt", "content": "x"}, {}))


def test_wol_packet_validation():
    from app.workflows.nodes import NodeError, node_wol

    out = run(node_wol({"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "127.0.0.1"}, {}))
    assert out["sent"] is True
    with pytest.raises(NodeError):
        run(node_wol({"mac": "not-a-mac"}, {}))


def test_git_subcommand_allowlist():
    from app.workflows.nodes import NodeError, node_git

    with pytest.raises(NodeError):
        run(node_git({"subcommand": "config", "args": "--global user.name x"}, {}))
    # 許可サブコマンド（リポジトリ外でも exit!=0 で返るがエラーにはならない構造）
    out = run(node_git({"subcommand": "rev-parse", "args": "--is-inside-work-tree"}, {}))
    assert "exit_code" in out


def test_ssh_host_validation():
    from app.workflows.nodes import NodeError, node_ssh

    with pytest.raises(NodeError):
        run(node_ssh({"host": "evil; rm -rf", "command": "ls"}, {}))


def test_python_exec_disabled_by_default():
    from app.workflows.nodes import NodeError, node_python_exec

    with pytest.raises(NodeError, match="無効"):
        run(node_python_exec({"code": "print(1)"}, {}))


def test_loop_foreach_execution():
    from app.workflows.engine import _execute_graph

    (_sandbox / "loop-out.txt").write_text("")
    nodes = [
        {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
        {"id": "loop", "type": "control.loop", "config": {"mode": "foreach", "items": '["x","y","z"]'}},
        {"id": "w", "type": "file.write", "config": {"path": str(_sandbox / "loop-out.txt"), "content": "{{loop.item}}", "append": True}},
    ]
    edges = [
        {"source": "t", "target": "loop"},
        {"source": "loop", "target": "w", "branch": "body"},
    ]
    ctx = {}
    run(_execute_graph(nodes, edges, ctx))
    assert (_sandbox / "loop-out.txt").read_text() == "xyz"
    assert ctx["loop"]["output"]["total"] == 3


def test_loop_count_and_done_branch():
    from app.workflows.engine import _execute_graph

    (_sandbox / "loop-count.txt").write_text("")
    nodes = [
        {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
        {"id": "loop", "type": "control.loop", "config": {"mode": "count", "count": 3}},
        {"id": "body", "type": "file.write", "config": {"path": str(_sandbox / "loop-count.txt"), "content": "*", "append": True}},
        {"id": "after", "type": "var.set", "config": {"value": "done"}},
    ]
    edges = [
        {"source": "t", "target": "loop"},
        {"source": "loop", "target": "body", "branch": "body"},
        {"source": "loop", "target": "after", "branch": "done"},
    ]
    ctx = {}
    run(_execute_graph(nodes, edges, ctx))
    assert (_sandbox / "loop-count.txt").read_text() == "***"
    assert ctx["after"]["output"]["value"] == "done"


def test_scrape_selector(monkeypatch):
    import httpx

    from app.workflows import nodes

    html = '<html><body><h1 class="t">タイトル</h1><a href="/x">link</a></body></html>'

    class FakeResponse:
        status_code = 200
        text = html

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    out = run(nodes.node_scrape({"url": "http://x.test", "selector": "h1.t"}, {}))
    assert out["first"] == "タイトル"
    out2 = run(nodes.node_scrape({"url": "http://x.test", "selector": "a", "attribute": "href"}, {}))
    assert out2["results"] == ["/x"]


def test_workflow_api_accepts_v2_nodes(admin_client):
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "s", "type": "string.op", "config": {"text": "abc", "op": "upper"}},
            {"id": "loop", "type": "control.loop", "config": {"mode": "count", "count": 2}},
        ],
        "edges": [
            {"source": "t", "target": "s"},
            {"source": "s", "target": "loop"},
        ],
    }
    r = admin_client.post(
        "/api/v1/workflows", json={"name": "v2", "definition": definition}, headers=CSRF_HEADERS
    )
    assert r.status_code == 201, r.text
    admin_client.delete(f"/api/v1/workflows/{r.json()['id']}", headers=CSRF_HEADERS)
