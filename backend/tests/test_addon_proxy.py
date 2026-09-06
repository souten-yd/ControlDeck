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
    frame_cookie = response.headers["set-cookie"]
    assert "addon=secret" not in frame_cookie
    assert "HttpOnly" in frame_cookie and "Secure" in frame_cookie and "SameSite=None" in frame_cookie
    assert response.headers["content-security-policy"].startswith("sandbox ")
    assert "cookie" not in captured and "x-csrf-token" not in captured and "origin" not in captured
    assert captured["authorization"].startswith("Bearer ")
    token = captured["authorization"].removeprefix("Bearer ")
    payload = tokens.verify(token, addon_id="fake-addon", kind="service")
    assert payload["aud"] == "fake-addon" and payload["sub"].isdigit()
    raw_frame_token = frame_cookie.split(";", 1)[0].split("=", 1)[1]
    frame_payload = tokens.verify(raw_frame_token, addon_id="fake-addon", kind="frame")
    assert frame_payload["actor_user_id"] == int(frame_payload["sub"])


def test_addon_frame_cookie_authenticates_opaque_subresources(enabled_addon, monkeypatch):
    client, _registry = enabled_addon
    from app.addons import proxy

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=OneChunkStream(b"asset"), request=request)

    monkeypatch.setattr(proxy, "_new_http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    bridge_session = client.post(
        "/api/v1/addons/fake-addon/bridge/handshake",
        headers=CSRF_HEADERS,
        json={"bridge_version": "1.0", "view_id": "workspace"},
    ).json()["session_nonce"]
    root = client.get("/addon-frame/fake-addon/")
    frame_token = root.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    client.cookies.clear()
    asset = client.get(
        "/addon-frame/fake-addon/app.js",
        headers={"Cookie": f"cd_addon_frame={frame_token}", "Sec-Fetch-Dest": "script"},
    )
    assert asset.status_code == 200 and asset.content == b"asset"
    assert "cookie" not in requests[-1].headers and "origin" not in requests[-1].headers

    mutation_without_bridge = client.post(
        "/addon-frame/fake-addon/action",
        headers={"Cookie": f"cd_addon_frame={frame_token}"},
    )
    assert mutation_without_bridge.status_code == 403
    mutation = client.post(
        "/addon-frame/fake-addon/action",
        headers={
            "Cookie": f"cd_addon_frame={frame_token}",
            "X-Control-Deck-Bridge-Session": bridge_session,
        },
    )
    assert mutation.status_code == 200
    assert "x-control-deck-bridge-session" not in requests[-1].headers

    opaque_fetch_without_bridge = client.get(
        "/addon-frame/fake-addon/data",
        headers={
            "Cookie": f"cd_addon_frame={frame_token}",
            "Origin": "null",
            "Sec-Fetch-Dest": "empty",
        },
    )
    assert opaque_fetch_without_bridge.status_code == 403
    preflight = client.options(
        "/addon-frame/fake-addon/data",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-control-deck-bridge-session",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "null"
    mutation_preflight = client.options(
        "/addon-frame/fake-addon/action",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-control-deck-bridge-session",
        },
    )
    assert mutation_preflight.status_code == 204
    assert mutation_preflight.headers["access-control-allow-headers"] == "content-type, x-control-deck-bridge-session"
    opaque_fetch = client.get(
        "/addon-frame/fake-addon/data",
        headers={
            "Cookie": f"cd_addon_frame={frame_token}",
            "Origin": "null",
            "Sec-Fetch-Dest": "empty",
            "X-Control-Deck-Bridge-Session": bridge_session,
        },
    )
    assert opaque_fetch.status_code == 200
    assert opaque_fetch.headers["access-control-allow-origin"] == "null"
    assert opaque_fetch.headers["access-control-allow-credentials"] == "true"
    assert "x-control-deck-bridge-session" not in requests[-1].headers

    wrong_scope = client.get(
        "/addon-frame/other-addon/app.js",
        headers={"Cookie": f"cd_addon_frame={frame_token}", "Origin": "null"},
    )
    assert wrong_scope.status_code == 401

    tampered = client.get(
        "/addon-frame/fake-addon/app.js",
        headers={"Cookie": f"cd_addon_frame={frame_token}x", "Origin": "null"},
    )
    assert tampered.status_code == 401


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
    assert "forbidden=1" not in response.headers["set-cookie"]
    assert "cd_addon_frame=" in response.headers["set-cookie"]


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


def test_addon_frame_enforces_request_limit_and_upstream_timeout(enabled_addon, monkeypatch):
    client, _registry = enabled_addon
    from app.addons import proxy

    monkeypatch.setattr(proxy, "MAX_REQUEST_BYTES", 4)
    oversized = client.post("/addon-frame/fake-addon/upload", content=b"12345")
    assert oversized.status_code == 413

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream", request=request)

    monkeypatch.setattr(proxy, "_new_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(timeout)))
    response = client.get("/addon-frame/fake-addon/slow")
    assert response.status_code == 502
    assert response.json()["detail"] == "拡張機能serviceへ接続できません"


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


def test_addon_frame_data_works_without_a_cookie_over_plain_http(enabled_addon, monkeypatch):
    """cookie を保存できない経路でも add-on の API へ届くこと。

    add-on は不透明 origin の sandbox で動くので、frame cookie は `SameSite=None`
    でなければ送られない。そして `SameSite=None` は `Secure` を要求する。平文
    HTTP の LAN アドレスは browser から「信頼できる origin」と見なされないため、
    この cookie はそもそも保存できない——localhost と違って抜け道が無い。

    bridge session は header で来るのでこの制限を受けない。中身は cookie と同じ
    利用者を指し、発行にはこの add-on を開ける第一者の session が要る。
    """
    client, _registry = enabled_addon
    from app.addons import proxy

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OneChunkStream(b"data"), request=request)

    monkeypatch.setattr(proxy, "_new_http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    bridge_session = client.post(
        "/api/v1/addons/fake-addon/bridge/handshake",
        headers=CSRF_HEADERS,
        json={"bridge_version": "1.0", "view_id": "workspace"},
    ).json()["session_nonce"]
    client.cookies.clear()

    fetched = client.get(
        "/addon-frame/fake-addon/data",
        headers={
            "Origin": "null",
            "Sec-Fetch-Dest": "empty",
            "X-Control-Deck-Bridge-Session": bridge_session,
        },
    )
    assert fetched.status_code == 200 and fetched.content == b"data"

    # 何も持たない相手は今までどおり弾く。cookie が無いことを通す理由にはしない。
    anonymous = client.get(
        "/addon-frame/fake-addon/data",
        headers={"Origin": "null", "Sec-Fetch-Dest": "empty"},
    )
    assert anonymous.status_code == 401


def test_addon_frame_cookie_outlives_a_short_absence(enabled_addon):
    """画面を開いたまま少し離れただけで API が一斉に 401 にならないこと。

    10 分で切れていたため、37 分放置しただけで SonicForge のライブラリと生成が
    両方落ちた。利用者からは「データを取得できませんでした」としか見えない。
    """
    client, _registry = enabled_addon
    from app.addons import tokens

    root = client.get("/addon-frame/fake-addon/")
    cookie = root.headers["set-cookie"]
    assert f"Max-Age={tokens.FRAME_TOKEN_TTL_SECONDS}" in cookie
    assert tokens.FRAME_TOKEN_TTL_SECONDS >= 30 * 24 * 60 * 60
    # sandbox の不透明 origin から送るには両方が要る。片方でも欠けると送られない。
    assert "SameSite=None" in cookie and "Secure" in cookie
    assert "HttpOnly" in cookie
