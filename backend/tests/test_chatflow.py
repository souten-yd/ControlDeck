import asyncio


def test_trigger_injects_chat_input():
    from app.workflows.nodes import node_trigger

    ctx = {"__input__": {"message": "こんにちは", "user": "souten"}}
    out = asyncio.run(node_trigger({}, ctx))
    assert out["message"] == "こんにちは"
    assert out["user"] == "souten"


def test_signal_display_node():
    from app.workflows.nodes import node_signal_display

    ctx = {"t": {"output": {"message": "hello"}}}
    out = asyncio.run(node_signal_display({"signal": "reply", "value": "応答: {{t.message}}"}, ctx))
    assert out["signal"] == "reply"
    assert out["value"] == "応答: hello"
    assert out["display"] is True


def test_chatflow_end_to_end():
    """trigger(chat) -> signal.display で入力メッセージが表示信号に流れる。"""
    from app.workflows.engine import _execute_graph

    nodes = [
        {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
        {"id": "echo", "type": "signal.display", "config": {"signal": "reply", "value": "受信: {{t.message}}"}},
    ]
    edges = [{"source": "t", "target": "echo"}]
    ctx = {"__input__": {"message": "テスト入力"}}
    asyncio.run(_execute_graph(nodes, edges, ctx))
    assert ctx["echo"]["output"]["value"] == "受信: テスト入力"
    assert ctx["echo"]["output"]["display"] is True
