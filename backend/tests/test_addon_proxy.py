from __future__ import annotations

import asyncio

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from app.addons.schema import AddonHealthReport
from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


class OneChunkStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes):
        self.content = content

    async def __aiter__(self):
        yield self.content


@pytest.fixture()
def enabled_addon(admin_client, monkeypatch, tmp_path):
    from app.addons import health, registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "addon-proxy-data")
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()

    async def healthy(addon_id: str, client=None):
        return registry.update_health(addon_id, AddonHealthReport.model_validate({
            "status": "healthy", "contract_version": "2.0",
        }))

    monkeypatch.setattr(health, "recheck", healthy)
    assert admin_client.post("/api/v1/addons", json=addon_manifest(), headers=CSRF_HEADERS).status_code == 201
    assert admin_client.post("/api/v1/addons/fake-addon/enable", headers=CSRF_HEADERS).status_code == 200
    return admin_client, registry


def test_addon_frame_strips_host_credentials_injects_scoped_token_and_drops_set_cookie(enabled_addon, monkeypatch):
    client, _registry = enabled_addon
    from app.addons import proxy, tokens

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(302, headers={
            "Location": "/next", "Set-Cookie": "addon=secret", "Content-Type": "text/plain",
        }, stream=OneChunkStream(b"redirect"), request=request)

    monkeypatch.setattr(proxy, "_new_http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False,
    ))
    response = client.get(
        "/addon-frame/fake-addon/",
        headers={"Authorization": "Bearer control-deck-secret", "X-CSRF-Token": "secret", "Origin": "https://deck.example"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/addon-frame/fake-addon/next"
    assert "set-cookie" not in response.headers
    assert response.headers["content-security-policy"].startswith("sandbox ")
    assert "cookie" not in captured and "x-csrf-token" not in captured and "origin" not in captured
    assert captured["authorization"].startswith("Bearer ")
    token = captured["authorization"].removeprefix("Bearer ")
    payload = tokens.verify(token, addon_id="fake-addon", kind="service")
    assert payload["aud"] == "fake-addon" and payload["sub"].isdigit()


def test_addon_frame_requires_auth_enabled_and_effective_view(enabled_addon):
    client, _registry = enabled_addon
    assert client.post("/api/v1/addons/fake-addon/disable", headers=CSRF_HEADERS).status_code == 200
    assert client.get("/addon-frame/fake-addon/").status_code == 409
    client.cookies.clear()
    assert client.get("/addon-frame/fake-addon/").status_code == 401


def test_addon_frame_preserves_sse_without_buffering(enabled_addon, monkeypatch):
    client, _registry = enabled_addon
    from app.addons import proxy

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream", "Set-Cookie": "forbidden=1"},
            stream=OneChunkStream(b"event: progress\ndata: 1\n\n"),
            request=request,
        )

    monkeypatch.setattr(proxy, "_new_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    response = client.get("/addon-frame/fake-addon/events")
    assert response.status_code == 200
    assert response.content == b"event: progress\ndata: 1\n\n"
    assert response.headers["x-accel-buffering"] == "no"
    assert "set-cookie" not in response.headers


def test_addon_frame_rejects_oversize_response_before_rendering(enabled_addon, monkeypatch):
    client, _registry = enabled_addon
    from app.addons import proxy

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OneChunkStream(b"12345"), request=request)

    monkeypatch.setattr(proxy, "MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(proxy, "_new_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    response = client.get("/addon-frame/fake-addon/large")
    assert response.status_code == 502
    assert response.json()["detail"] == "拡張機能のresponseが32MiBを超えています"


def test_addon_service_token_is_audience_bound_and_expires(monkeypatch, tmp_path):
    from app.addons import tokens

    monkeypatch.setattr(tokens, "data_dir", lambda: tmp_path)
    token = tokens.issue("fake-addon", subject="42", kind="service", now=100)
    assert tokens.verify(token, addon_id="fake-addon", kind="service", subject="42", now=101)["exp"] == 700
    with pytest.raises(tokens.AddonTokenError):
        tokens.verify(token, addon_id="other-addon", kind="service", now=101)
    with pytest.raises(tokens.AddonTokenError):
        tokens.verify(token, addon_id="fake-addon", kind="service", now=700)


def test_addon_frame_websocket_relays_messages_with_scoped_token(enabled_addon, monkeypatch):
    client, _registry = enabled_addon
    from app.addons import proxy, tokens

    sent: list[str | bytes] = []
    captured: dict[str, object] = {}

    class FakeUpstream:
        subprotocol = None

        async def send(self, message):
            sent.append(message)

        async def __aiter__(self):
            yield "upstream-ready"
            await asyncio.Event().wait()

    class FakeConnection:
        async def __aenter__(self):
            return FakeUpstream()

        async def __aexit__(self, *_args):
            return None

    def connect(url, headers, subprotocols):
        captured.update({"url": url, "headers": headers, "subprotocols": subprotocols})
        return FakeConnection()

    monkeypatch.setattr(proxy, "_connect_websocket", connect)
    with client.websocket_connect("/addon-frame/fake-addon/ws?run=1", headers={"Origin": "null"}) as socket:
        assert socket.receive_text() == "upstream-ready"
        socket.send_text("browser-message")
    assert sent == ["browser-message"]
    assert captured["url"] == "ws://127.0.0.1:9130/ws?run=1"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    token = headers["Authorization"].removeprefix("Bearer ")
    assert tokens.verify(token, addon_id="fake-addon", kind="service")["aud"] == "fake-addon"


def test_addon_frame_websocket_rejects_disabled_addon(enabled_addon):
    client, _registry = enabled_addon
    assert client.post("/api/v1/addons/fake-addon/disable", headers=CSRF_HEADERS).status_code == 200
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/addon-frame/fake-addon/ws"):
            pass
    assert rejected.value.code == 4409


def test_addon_frame_websocket_uses_bridge_subprotocol_when_opaque_frame_has_no_cookie(enabled_addon, monkeypatch):
    client, _registry = enabled_addon
    from app.addons import proxy

    session = client.post("/api/v1/addons/fake-addon/bridge/handshake", headers=CSRF_HEADERS, json={
        "bridge_version": "1.0", "view_id": "workspace",
    }).json()

    class WaitingUpstream:
        subprotocol = None
        async def send(self, _message): pass
        async def __aiter__(self): await asyncio.Event().wait(); yield "never"
    class Connection:
        async def __aenter__(self): return WaitingUpstream()
        async def __aexit__(self, *_args): return None

    captured: dict[str, object] = {}
    monkeypatch.setattr(proxy, "_connect_websocket", lambda url, headers, subprotocols: (
        captured.update({"protocols": subprotocols}) or Connection()
    ))
    client.cookies.clear()
    protocol = f"control-deck-bridge.{session['session_nonce']}"
    with client.websocket_connect(
        "/addon-frame/fake-addon/ws",
        headers={"Origin": "null"},
        subprotocols=[protocol],
    ):
        pass
    assert captured["protocols"] == []
