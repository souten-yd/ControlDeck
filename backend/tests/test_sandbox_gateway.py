"""サンドボックスの LLM 中継（app.maintenance.sandbox_gateway）。

目的と根拠: 箱から本番のモデル切り替え（POST .../models/{m}/load）を鍵なしで呼べた（2026-11-21 実測）。
中継は許した呼び出しだけを通し、Authorization・Cookie を上流へ渡さず、本文・同時実行に上限を持つ。
上流は httpx.MockTransport の偽物。
"""
import asyncio

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
