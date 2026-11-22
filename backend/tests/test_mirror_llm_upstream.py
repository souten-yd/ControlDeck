"""ミラーの「上流の LLM」（config の llm_upstream。計画の P3）。

目的と根拠: 開発用の箱のミラーはモデルの登録が空で、AI アシスタント・Patent の AI・ナレッジの分析が
LLM を使えなかった（2026-11-22）。ミラー（CONTROL_DECK_ISOLATED=1）は llm_upstream（箱の中の中継）を
「ローカルの対象」として扱い、中継の先の本番のローカルのモデルへ送る。守ること:
- 本番（ISOLATED でない）では llm_upstream を読まない（本番の LLM の経路を変えない）。
- loopback 以外の URL は掴まない（設定ミスで箱の外へ出さない）。
- モデル一覧・chat（ストリーム含む）は上流へそのまま渡り、機密の印（x-control-deck-confidential）も落ちない。
- 内部の AI 機能（resolve_ai_target・RuntimeChatRequest）は上流を使い、local_only の検査を通る。
- mirror_config が中継の URL を書く。
上流は httpx.MockTransport の偽物。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import get_config
from app.models_mgmt import ai_gateway, gateway
from app.models_mgmt.runtime_provider import RuntimeChatRequest, provider_for_request

_REAL = httpx.AsyncClient
UP = "http://127.0.0.1:19999/api/v1/llm/v1"


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.fixture
def upstream(monkeypatch):
    """ISOLATED + llm_upstream。偽の上流が受けた要求を seen に貯める。"""
    monkeypatch.setenv("CONTROL_DECK_ISOLATED", "1")
    monkeypatch.setattr(get_config(), "llm_upstream", UP)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            body = json.dumps({"object": "list", "data": [{"id": "auto"}, {"id": "m1"}]}).encode()
            return httpx.Response(200, headers={"content-type": "application/json"}, stream=_Chunks([body]))
        sse = [b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
               b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n', b"data: [DONE]\n\n"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_Chunks(sse))

    real = _REAL

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def test_upstream_is_read_only_when_isolated(monkeypatch):
    monkeypatch.setattr(get_config(), "llm_upstream", UP)
    monkeypatch.delenv("CONTROL_DECK_ISOLATED", raising=False)
    assert gateway.upstream_base() == ""
    monkeypatch.setenv("CONTROL_DECK_ISOLATED", "1")
    assert gateway.upstream_base() == UP


@pytest.mark.parametrize("value", ["", "https://127.0.0.1:1/api/v1/llm/v1", "http://example.com:80/api/v1/llm/v1",
                                   "http://127.0.0.1/api/v1/llm/v1", "http://127.0.0.1:1/other"])
def test_upstream_rejects_unset_and_non_loopback(monkeypatch, value):
    monkeypatch.setenv("CONTROL_DECK_ISOLATED", "1")
    monkeypatch.setattr(get_config(), "llm_upstream", value)
    assert gateway.upstream_base() == ""


def test_internal_target_goes_to_upstream_only_in_mirror(monkeypatch, upstream):
    assert gateway.resolve_internal_target(gateway.base_url(), "auto") == (UP, "auto")
    assert gateway.resolve_internal_target(gateway.base_url(), "cloud:x") == (UP, "cloud:x")
    assert gateway.resolve_internal_target("http://example.test/v1", "m") == ("http://example.test/v1", "m")
    monkeypatch.delenv("CONTROL_DECK_ISOLATED")
    with pytest.raises(Exception):   # 本番は従来どおり実インスタンスを探す（空なら 404）
        gateway.resolve_internal_target(gateway.base_url(), "auto")


def test_ai_target_uses_upstream_and_passes_local_check(upstream):
    async def go():
        target = await ai_gateway.resolve_ai_target("text.generate")
        assert (target.base_url, target.model, target.gateway_managed) == (UP, "auto", True)
        await asyncio.to_thread(ai_gateway.require_local_target, target)
        assert await ai_gateway.local_capability_available("text.generate")
    asyncio.run(go())


def test_ai_target_unavailable_when_upstream_is_empty_or_down(monkeypatch, upstream):
    real = _REAL

    def empty(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []}))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", empty)
    with pytest.raises(ai_gateway.AITargetUnavailable):
        asyncio.run(ai_gateway.resolve_ai_target("text.generate"))

    def down(request):
        raise httpx.ConnectError("down")

    def broken(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(down)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", broken)
    assert asyncio.run(ai_gateway.local_capability_available("text.generate")) is False


def test_runtime_chat_request_streams_via_upstream_with_confidential_mark(upstream):
    """内部の AI 機能（RuntimeChatRequest, local_only）が上流へ送り、機密の印・呼び出し元が付く。"""
    async def go():
        request = RuntimeChatRequest(base_url=gateway.base_url(), model="auto", messages=[{"role": "user", "content": "hi"}],
                                     local_only=True, client="assistant")
        provider = provider_for_request(request)
        assert request.base_url == UP
        return "".join([c.content async for c in provider.stream_chat(request) if c.type == "content"])

    assert asyncio.run(go()) == "hello"
    chat = [r for r in upstream if r.url.path.endswith("/chat/completions")]
    assert chat and chat[0].url.host == "127.0.0.1" and chat[0].url.port == 19999
    assert chat[0].headers[gateway.CONFIDENTIAL_HEADER] == "1"
    assert chat[0].headers[gateway.CLIENT_HEADER] == "assistant"
    assert json.loads(chat[0].content)["model"] == "auto"


def test_mirror_gateway_relays_models_and_chat_stream(admin_client, upstream):
    key = gateway.get_api_key(create=True)
    auth = {"Authorization": f"Bearer {key}"}
    models = admin_client.get("/api/v1/llm/v1/models", headers=auth)
    assert models.status_code == 200 and [m["id"] for m in models.json()["data"]] == ["auto", "m1"]
    with admin_client.stream("POST", "/api/v1/llm/v1/chat/completions", headers={**auth, gateway.CONFIDENTIAL_HEADER: "1"},
                             json={"model": "cloud:x", "stream": True, "messages": []}) as response:
        body = b"".join(response.iter_raw())
    assert response.status_code == 200 and body.count(b"data:") == 3 and b"[DONE]" in body
    chat = [r for r in upstream if r.url.path.endswith("/chat/completions")][0]
    assert chat.headers[gateway.CONFIDENTIAL_HEADER] == "1"          # 機密の印は上流へ渡る
    assert json.loads(chat.content)["model"] == "cloud:x"            # 外部の判断は中継・本番に任せる（ここで握らない）


def test_relay_forwards_confidential_header():
    from app.maintenance import sandbox_gateway

    assert gateway.CONFIDENTIAL_HEADER in sandbox_gateway.FORWARD_REQUEST_HEADERS
