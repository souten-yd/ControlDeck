"""応答の圧縮。逐次流すものは縮めない。"""

from __future__ import annotations

from app.security.compression import SelectiveGZipMiddleware


def _scope(path: str, accept: str = "*/*") -> dict:
    return {
        "type": "http",
        "path": path,
        "headers": [(b"accept", accept.encode()), (b"accept-encoding", b"gzip")],
    }


def test_static_and_api_responses_are_compressed():
    plain = SelectiveGZipMiddleware._plain
    assert plain(_scope("/addon-frame/sonic-forge/app.js")) is False
    assert plain(_scope("/api/v1/terminals")) is False
    assert plain(_scope("/")) is False


def test_the_llm_gateway_is_left_alone():
    """token を 1 つずつ返す経路を縮めると、まとめてからでないと出せなくなる。"""
    plain = SelectiveGZipMiddleware._plain
    assert plain(_scope("/api/v1/llm/v1/chat/completions")) is True
    assert plain(_scope("/api/v1/llm/v1/models")) is True


def test_event_streams_are_left_alone_wherever_they_are():
    plain = SelectiveGZipMiddleware._plain
    assert plain(_scope("/api/v1/workflows/events", accept="text/event-stream")) is True
    assert plain(_scope("/api/v1/workflows/events", accept="application/json")) is False


def test_websocket_scopes_are_never_touched():
    assert SelectiveGZipMiddleware._plain({"type": "websocket", "path": "/x", "headers": []}) is False


def test_the_addon_frame_actually_shrinks(admin_client):
    """実際に縮むこと。ここが効かないと携帯では待ち時間になる。"""
    response = admin_client.get(
        "/addon-frame/sonic-forge/app.js", headers={"Accept-Encoding": "gzip"}
    )
    if response.status_code != 200:
        import pytest

        pytest.skip("sonic-forge が入っていない")
    assert response.headers.get("content-encoding") == "gzip"
