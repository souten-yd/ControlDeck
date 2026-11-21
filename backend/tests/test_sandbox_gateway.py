"""サンドボックスの LLM 中継（app.maintenance.sandbox_gateway）。

目的と根拠: 箱から本番のモデル切り替え（POST .../models/{m}/load）を鍵なしで呼べた（2026-11-21 実測）。
中継は許した呼び出しだけを通し、Authorization・Cookie を上流へ渡さず、本文・同時実行に上限を持つ。
上流は httpx.MockTransport の偽物。

MCP の中継（P2a、2026-11-21）の目的と根拠: 箱の AI に ControlDeck の道具（Project Lab の実行など）を使わせたいが、
箱へ MCP のトークンは置けない。トークンは箱の外の中継だけが持ち、箱から来た Authorization は捨てて付け替える。
呼べる道具は許可の一覧だけに絞り、一覧（/tools）も同じ範囲にする。
"""
import asyncio
import json

import httpx

from app.maintenance import sandbox_gateway as gw

API = gw.API


class _Chunks(httpx.AsyncByteStream):
    """MockTransport は content= の応答を読み込み済みにするので、ストリームとして返すための偽物。"""

    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def _resp(status=200, chunks=(b"{}",), headers=None):
    return httpx.Response(status, headers=headers, stream=_Chunks(list(chunks)))


def _client(handler, **kwargs):
    app = gw.create_app(name="box1", upstream="http://prod.invalid", transport=httpx.MockTransport(handler), **kwargs)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay")


def _ok(request):
    return _resp()


def run(coro):
    return asyncio.run(coro)


def test_only_allowed_calls_pass_and_load_needs_allow_load():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return _resp()

    async def go():
        async with _client(handler) as c:
            assert (await c.get(f"{API}/models")).status_code == 200
            assert (await c.post(f"{API}/chat/completions", json={})).status_code == 200
            assert (await c.get("/healthz")).status_code == 200
            for method, path in [("GET", "/api/v1/auth/me"), ("POST", f"{API}/models"), ("DELETE", f"{API}/models"),
                                 ("GET", f"{API}/chat/completions"), ("POST", f"{API}/models/m/load")]:
                r = await c.request(method, path)
                assert r.status_code == 403 and "detail" in r.json(), (method, path)
        async with _client(handler, allow_load=True) as c:
            assert (await c.post(f"{API}/models/qwen:3/load")).status_code == 200
            assert (await c.post(f"{API}/models/../x/load")).status_code == 403
            assert (await c.get(f"{API}/models/m/load")).status_code == 403

    run(go())
    assert ("POST", f"{API}/models/qwen%3A3/load") in seen or ("POST", f"{API}/models/qwen:3/load") in seen
    assert ("POST", f"{API}/models") not in seen


def test_headers_are_replaced_not_forwarded():
    captured = {}

    def handler(request):
        captured.update(request.headers)
        return _resp()

    async def go():
        async with _client(handler) as c:
            await c.post(f"{API}/chat/completions", json={}, headers={
                "Authorization": "Bearer secret", "Cookie": "s=1", "X-Other": "x", "Accept": "text/event-stream"})

    run(go())
    assert "authorization" not in captured and "cookie" not in captured and "x-other" not in captured
    assert captured["x-requested-with"] == "ControlDeck"
    assert captured["x-control-deck-client"] == "opencode"
    assert captured["x-control-deck-sandbox"] == "box1"
    assert captured["accept"] == "text/event-stream"


def test_body_over_32_mib_is_413(monkeypatch):
    monkeypatch.setattr(gw, "MAX_BODY_BYTES", 1024)

    async def go():
        async with _client(_ok) as c:
            assert (await c.post(f"{API}/chat/completions", content=b"x" * 2000)).status_code == 413
            assert (await c.post(f"{API}/chat/completions", content=b"x" * 100)).status_code == 200

    run(go())


def test_stream_chunks_arrive_separately():
    def handler(request):
        return _resp(headers={"content-type": "text/event-stream"},
                     chunks=[b"data: 1\n\n", b"data: 2\n\n", b"data: [DONE]\n\n"])

    app = gw.create_app(name="box1", upstream="http://prod.invalid", transport=httpx.MockTransport(handler))
    sent = []

    async def go():
        # ASGITransport は本文をまとめてしまうので、ASGI のメッセージを直接受ける
        messages = [{"type": "http.request", "body": b"{}", "more_body": False}]

        async def receive():
            if messages:
                return messages.pop()
            await asyncio.Event().wait()  # 切断の監視は、応答が終わるまで待たせる

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": f"{API}/chat/completions", "raw_path": b"",
                 "query_string": b"", "headers": [(b"content-type", b"application/json")],
                 "server": ("relay", 80), "scheme": "http", "http_version": "1.1"}
        await app(scope, receive, send)

    run(go())
    chunks = [m["body"] for m in sent if m["type"] == "http.response.body" and m.get("body")]
    assert len(chunks) == 3 and chunks[0] == b"data: 1\n\n"


def test_concurrency_limit_returns_429_without_waiting():
    async def go():
        gate = asyncio.Event()

        async def handler(request):
            await gate.wait()
            return _resp()

        async with _client(handler, max_concurrent=2) as c:
            tasks = [asyncio.create_task(c.post(f"{API}/chat/completions", json={})) for _ in range(2)]
            await asyncio.sleep(0.1)
            blocked = await c.post(f"{API}/chat/completions", json={})
            gate.set()
            done = await asyncio.gather(*tasks)
            after = await c.post(f"{API}/chat/completions", json={})
        return blocked.status_code, [r.status_code for r in done], after.status_code

    blocked, done, after = run(go())
    assert blocked == 429 and done == [200, 200] and after == 200


def test_upstream_down_is_502_and_slot_is_released():
    def handler(request):
        raise httpx.ConnectError("down")

    async def go():
        async with _client(handler, max_concurrent=1) as c:
            first = await c.post(f"{API}/chat/completions", json={})
            second = await c.post(f"{API}/chat/completions", json={})
        return first.status_code, second.status_code

    assert run(go()) == (502, 502)


def test_cloud_models_are_refused_unless_allowed():
    """箱の OpenCode はローカル LLM だけ（2026-11-21 利用者）。外部のモデルへの chat は既定で断り、上流へ送らない。"""
    seen = []

    def handler(request):
        seen.append(request.content)
        return _resp()

    async def go():
        async with _client(handler) as c:
            r = await c.post(f"{API}/chat/completions", json={"model": "cloud:openrouter/deepseek/x", "messages": []})
            assert r.status_code == 403 and "cloud" in r.json()["detail"]
            assert (await c.post(f"{API}/chat/completions", json={"model": " CLOUD:x"})).status_code == 403
            assert (await c.post(f"{API}/chat/completions", json={"model": "qwen-flash-next"})).status_code == 200
            assert (await c.post(f"{API}/chat/completions", content=b"not json")).status_code == 200
        async with _client(handler, allow_cloud=True) as c:
            assert (await c.post(f"{API}/chat/completions", json={"model": "cloud:openrouter/x"})).status_code == 200

    run(go())
    assert len(seen) == 3 and all(b"cloud:openrouter/deepseek" not in body for body in seen)


MCP = gw.MCP_API
TOKEN = "relay-owned-token.sig"


def _mcp_client(handler):
    return _client(handler, mcp_token=TOKEN)


def test_mcp_paths_need_the_relay_token_and_are_closed_without_it():
    async def go():
        async with _client(_ok) as c:  # トークンを持たない中継では MCP は閉じている
            assert (await c.get(f"{MCP}/tools")).status_code == 403
            assert (await c.post(f"{MCP}/call", json={"name": "control_deck.report"})).status_code == 403
            assert (await c.get("/healthz")).json()["mcp"] is False
        async with _mcp_client(_ok) as c:
            assert (await c.get("/healthz")).json()["mcp"] is True
            assert (await c.get(f"{MCP}/tools")).status_code == 200
            for method, path in [("POST", f"{MCP}/tools"), ("GET", f"{MCP}/call"), ("GET", f"{MCP}/other")]:
                assert (await c.request(method, path)).status_code == 403, (method, path)

    run(go())


def test_mcp_authorization_is_replaced_with_the_relays_token():
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        return _resp(chunks=[b'{"tools": []}'], headers={"content-type": "application/json"})

    async def go():
        async with _mcp_client(handler) as c:
            await c.get(f"{MCP}/tools", headers={"Authorization": "Bearer from-the-box", "Cookie": "s=1"})
            await c.post(f"{MCP}/call", json={"name": "control_deck.lab_projects", "arguments": {}},
                         headers={"Authorization": "Bearer from-the-box"})

    run(go())
    assert len(seen) == 2
    for headers in seen:
        assert headers["authorization"] == f"Bearer {TOKEN}"
        assert "cookie" not in headers
        assert headers["x-requested-with"] == "ControlDeck" and headers["x-control-deck-sandbox"] == "box1"


def test_mcp_call_outside_the_allowlist_is_403_and_never_reaches_upstream():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return _resp(chunks=[b"{}"])

    async def go():
        async with _mcp_client(handler) as c:
            for body in ({"name": "control_deck.diagnostics"}, {"name": "control_deck.knowledge_search"},
                         {"name": "media.scene.create"}, {"name": "control_deck.project_output_grant"},
                         {"name": 5}, {}, [1]):
                assert (await c.post(f"{MCP}/call", json=body)).status_code == 403, body
            assert (await c.post(f"{MCP}/call", content=b"not json")).status_code == 403
            for name in sorted(gw.MCP_ALLOWED_TOOLS):
                assert (await c.post(f"{MCP}/call", json={"name": name, "arguments": {}})).status_code == 200, name

    run(go())
    assert len(seen) == len(gw.MCP_ALLOWED_TOOLS)


def test_mcp_tools_listing_is_narrowed_and_renewed_token_is_not_passed_on():
    listed = {"tools": [{"name": "control_deck.lab_run_start"}, {"name": "control_deck.diagnostics"},
                        {"name": "media.scene.create"}, {"name": "control_deck.report"}]}

    def handler(request):
        return _resp(chunks=[json.dumps(listed).encode()], headers={
            "content-type": "application/json", "X-Control-Deck-MCP-Token": "renewed-token"})

    async def go():
        async with _mcp_client(handler) as c:
            r = await c.get(f"{MCP}/tools")
            assert r.status_code == 200
            assert [t["name"] for t in r.json()["tools"]] == ["control_deck.lab_run_start", "control_deck.report"]
            assert "x-control-deck-mcp-token" not in r.headers and "renewed-token" not in r.text

    run(go())


def test_mcp_upstream_errors_pass_through_and_token_stays_out_of_logs(caplog):
    def handler(request):
        return _resp(404, chunks=[b'{"detail": "Project run not found"}'], headers={"content-type": "application/json"})

    async def go():
        async with _mcp_client(handler) as c:
            r = await c.post(f"{MCP}/call", json={"name": "control_deck.lab_run_status", "arguments": {"run_id": 1}})
            assert r.status_code == 404 and "not found" in r.text

    with caplog.at_level("DEBUG"):
        run(go())
    assert TOKEN not in caplog.text


def test_relay_reads_the_token_from_the_environment_not_argv(monkeypatch):
    import inspect

    assert gw.MCP_TOKEN_ENV == "CONTROL_DECK_SANDBOX_MCP_TOKEN"
    assert "--mcp-token" not in inspect.getsource(gw.main), "トークンは argv で受けない（ps から見える）"
