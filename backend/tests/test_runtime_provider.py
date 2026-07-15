import asyncio
import json

import httpx
import pytest

from app.models_mgmt import runtime_provider as rp


def _client_factory(monkeypatch, handler):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(rp.httpx, "AsyncClient", factory)


def test_openai_stream_normalizes_content_thinking_and_usage(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        body = (
            'data: {"choices":[{"delta":{"reasoning_content":"考"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"答"}}],"usage":{"completion_tokens":1}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    _client_factory(monkeypatch, handler)
    monkeypatch.setattr(rp.LlmRuntimeProvider, "_prepare", lambda self, request: _async_none())
    provider = rp.OpenAICompatibleRuntimeProvider()
    request = rp.RuntimeChatRequest("http://127.0.0.1:9999", "m", [{"role": "user", "content": "x"}])

    async def collect():
        return [chunk async for chunk in provider.stream_chat(request, request_id="openai-test")]

    chunks = asyncio.run(collect())
    assert [(item.type, item.content) for item in chunks[:2]] == [("thinking", "考"), ("content", "答")]
    assert chunks[2].type == "usage" and chunks[2].usage["completion_tokens"] == 1
    assert provider.active_request_count == 0


def test_ollama_native_stream_normalizes_json_lines(monkeypatch):
    def handler(request):
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["think"] is False and payload["stream"] is True
        return httpx.Response(200, text=(
            '{"message":{"thinking":"検討"}}\n'
            '{"message":{"content":"完了"},"done":true}\n'
        ))

    _client_factory(monkeypatch, handler)
    monkeypatch.setattr(rp.LlmRuntimeProvider, "_prepare", lambda self, request: _async_none())
    provider = rp.OllamaRuntimeProvider()
    request = rp.RuntimeChatRequest(
        "http://127.0.0.1:11434/v1", "m", [], thinking=False, keep_alive="30m",
    )

    async def collect():
        return [chunk async for chunk in provider.stream_chat(request)]

    chunks = asyncio.run(collect())
    assert [(item.type, item.content) for item in chunks] == [("thinking", "検討"), ("content", "完了")]


def test_complete_structured_output_falls_back_without_leaking_key(monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) < 3:
            return httpx.Response(400, text="secret-provider-body")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    _client_factory(monkeypatch, handler)
    monkeypatch.setattr(rp.LlmRuntimeProvider, "_prepare", lambda self, request: _async_none())
    request = rp.RuntimeChatRequest(
        "http://127.0.0.1:9999/v1", "m", [], api_key="super-secret",
        response_format={"type": "json_schema", "schema": {"type": "object"}},
    )
    result = asyncio.run(rp.OpenAICompatibleRuntimeProvider().complete(request))
    assert result == "{}" and len(calls) == 3
    assert "response_format" not in calls[-1]


def test_explicit_cancel_and_task_cancel_cleanup_active_registry():
    class SlowProvider(rp.LlmRuntimeProvider):
        async def _prepare(self, request):
            return None

        async def _complete_impl(self, request):
            return ""

        async def _stream_impl(self, request, cancel_event):
            yield rp.RuntimeChunk("content", content="first")
            await cancel_event.wait()

    async def explicit():
        provider = SlowProvider()
        stream = provider.stream_chat(rp.RuntimeChatRequest("http://x", "m", []), request_id="r1")
        assert (await anext(stream)).content == "first"
        assert await provider.cancel("r1") is True
        with pytest.raises(rp.GenerationCancelled):
            await anext(stream)
        assert provider.active_request_count == 0
        assert await provider.cancel("r1") is False

    async def task_cancel():
        provider = SlowProvider()

        async def consume():
            async for _ in provider.stream_chat(rp.RuntimeChatRequest("http://x", "m", []), request_id="r2"):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.active_request_count == 0

    asyncio.run(explicit())
    asyncio.run(task_cancel())


async def _async_none():
    return None


def test_chat_job_cancel_notifies_runtime_before_generic_cancel(admin_client, monkeypatch):
    from app.jobs import service as jobs

    calls = []
    fake = jobs.Job(id="chat-cancel-1", kind="chat.completion", title="chat", owner_user_id=1)

    async def cancel_runtime(request_id):
        calls.append(("runtime", request_id))
        return True

    monkeypatch.setattr(jobs, "get", lambda job_id: fake)
    monkeypatch.setattr(jobs, "visible_to", lambda job, user_id: True)
    monkeypatch.setattr(jobs, "cancel", lambda job_id: calls.append(("job", job_id)) or True)
    monkeypatch.setattr(rp, "cancel_request", cancel_runtime)
    response = admin_client.post(
        "/api/v1/jobs/chat-cancel-1/cancel", headers={"X-Requested-With": "ControlDeck"},
    )
    assert response.status_code == 200
    assert calls == [("runtime", "chat-cancel-1"), ("job", "chat-cancel-1")]
