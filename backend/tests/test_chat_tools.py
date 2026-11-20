"""AI アシスタントの会話で、モデル自身が道具を呼ぶ仕組み（tool calling）。

根拠: 2026-11-20 利用者「ローカル LLM の会話でモデル自身が道具を呼ぶ仕組みを入れてほしい」「毎回チェックする必要はない。
Claude と同じ方法で」。資料が大きい会話では、検索の道具を渡し、探すかどうか・何で探すかをモデルが決める。
実機（Qwen3-4B-Instruct、llama.cpp）で、挨拶・お礼・計算では呼ばず、「さっき調べた番組表で夜 9 時は？」では
「神奈川 今日の地上波 番組表 夜9時」と具体的な語で検索してから答えた。"""
import asyncio
import json

import httpx
import pytest

from app.models_mgmt import runtime_provider as rp


async def _none():
    return None


def _client(monkeypatch, handler, seen=None):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(rp.httpx, "AsyncClient", factory)
    monkeypatch.setattr(rp.LlmRuntimeProvider, "_prepare", lambda self, request: _none())


def test_stream_joins_tool_call_fragments_and_sends_tools(monkeypatch):
    """道具の呼び出しは index ごとの断片で流れてくる。つないで 1 つの tool_call の chunk で返す。tools は渡したときだけ送る。"""
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        body = "".join("data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in [
            {"choices": [{"delta": {"content": "調べます。"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "search_conversation_materials", "arguments": '{"query": "神奈川'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' 番組表"}'}}]}}]},
        ]) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body)

    _client(monkeypatch, handler)
    from app.workflows import chat_tools

    request = rp.RuntimeChatRequest("http://127.0.0.1:9999", "m", [{"role": "user", "content": "x"}],
                                    tools=[chat_tools.TOOL_SEARCH_MATERIALS])

    async def collect():
        return [c async for c in rp.OpenAICompatibleRuntimeProvider().stream_chat(request, request_id="t1")]

    chunks = asyncio.run(collect())
    assert [c.type for c in chunks] == ["content", "tool_call"]
    call = chunks[-1].tool_calls[0]
    assert call["id"] == "call_a" and call["name"] == "search_conversation_materials"
    assert json.loads(call["arguments"]) == {"query": "神奈川 番組表"}
    assert sent[0]["tools"][0]["function"]["name"] == "search_conversation_materials" and sent[0]["tool_choice"] == "auto"
    # 道具を渡さない要求には tools を付けない
    plain = rp.OpenAICompatibleRuntimeProvider()._payload(
        rp.RuntimeChatRequest("http://127.0.0.1:9999", "m", [{"role": "user", "content": "x"}]), stream=True)
    assert "tools" not in plain and "tool_choice" not in plain


def test_endpoint_rejecting_tools_raises_tools_unsupported(monkeypatch):
    """tools 付きの要求を 400 で断る runtime は ToolsUnsupported（呼び出し側が道具なしで続ける）。道具なしの 400 は従来の失敗。"""
    _client(monkeypatch, lambda request: httpx.Response(400, text='{"error":"tools not supported"}'))
    from app.workflows import chat_tools

    async def collect(tools):
        request = rp.RuntimeChatRequest("http://127.0.0.1:9999", "m", [{"role": "user", "content": "x"}], tools=tools)
        return [c async for c in rp.OpenAICompatibleRuntimeProvider().stream_chat(request, request_id="t2")]

    with pytest.raises(rp.ToolsUnsupported):
        asyncio.run(collect([chat_tools.TOOL_SEARCH_MATERIALS]))
    with pytest.raises(rp.RuntimeProviderError):
        asyncio.run(collect(None))


def test_search_tool_runs_the_models_query_without_score_gate(monkeypatch):
    """道具はモデルが書いた検索語で探し、点数の門で切らずに上位を返す。空の検索語・不明な道具・検索の失敗も文で返す（会話は止めない）。"""
    from app.workflows import chat_tools, rag

    asked = {}

    async def search(collection, question, top_k, **kwargs):
        asked.update(collection=collection, question=question, top_k=top_k, **kwargs)
        return {"context": "[R1] 番組表 21:00 ニュース" * 50, "count": 3}

    monkeypatch.setattr(rag, "search", search)
    text, record = asyncio.run(chat_tools.run({"name": chat_tools.SEARCH_MATERIALS, "arguments": '{"query": "神奈川 番組表"}'},
                                              collection="chat-x", top_k=6, max_chars=100))
    assert asked["question"] == "神奈川 番組表" and asked["top_k"] == 6 and "min_rerank_score" not in asked
    assert len(text) == 100 and record == {"name": chat_tools.SEARCH_MATERIALS, "query": "神奈川 番組表", "count": 3}
    assert "空" in asyncio.run(chat_tools.run({"name": chat_tools.SEARCH_MATERIALS, "arguments": "{}"},
                                              collection="c", top_k=1, max_chars=10))[0]
    assert "不明" in asyncio.run(chat_tools.run({"name": "rm_rf", "arguments": "{}"}, collection="c", top_k=1, max_chars=10))[0]

    async def broken(*args, **kwargs):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(rag, "search", broken)
    text, record = asyncio.run(chat_tools.run({"name": chat_tools.SEARCH_MATERIALS, "arguments": '{"query": "q"}'},
                                              collection="c", top_k=1, max_chars=100))
    assert "失敗" in text and record["error"] == "RuntimeError"


def test_models_without_tools_fall_back_to_the_keyword_rule(monkeypatch):
    """道具を受け付けないモデルでは、道具の案内を外し、従来どおり発言が資料を指すときだけ探して抜粋を入れる。"""
    from app.workflows import chat_persist, chat_tools, rag

    async def search(collection, question, top_k, **kwargs):
        return {"context": "[R1] 番組表 21:00 ニュース", "count": 1}

    monkeypatch.setattr(rag, "search", search)

    class Job:
        def log(self, *args, **kwargs):
            pass

    base = [{"role": "system", "content": "あなたは AI アシスタント"},
            {"role": "system", "content": chat_tools.materials_note(50_000)}]
    tool = {"collection": "chat-x", "top_k": 4, "max_chars": 1000, "said": ["番組表を調べて", "さっきの番組表の内容は？"]}
    out = asyncio.run(chat_persist._materials_without_tools(base + [{"role": "user", "content": "さっきの番組表の内容は？"}], tool, Job()))
    assert not any(chat_tools.SEARCH_MATERIALS in str(m["content"]) for m in out)
    assert "関係する抜粋" in out[1]["content"] and "番組表 21:00" in out[1]["content"]
    greet = {**tool, "said": ["番組表を調べて", "ありがとう"]}
    out = asyncio.run(chat_persist._materials_without_tools(base + [{"role": "user", "content": "ありがとう"}], greet, Job()))
    assert [m["role"] for m in out] == ["system", "user"]
    # 覚えたモデルには次から道具を渡さない
    chat_tools._unsupported.discard(("http://x", "m"))
    assert chat_tools.supported("http://x", "m")
    chat_tools.mark_unsupported("http://x", "m")
    assert not chat_tools.supported("http://x", "m")
    chat_tools._unsupported.discard(("http://x", "m"))


def _setup_job(conv, msg):
    from app.database import SessionLocal
    from app.models import ChatMessage, Conversation

    db = SessionLocal()
    try:
        db.add(Conversation(id=conv, owner_user_id=None))
        db.flush()
        db.add(ChatMessage(id=msg, conversation_id=conv, role="assistant", status="generating"))
        db.commit()
    finally:
        db.close()


def _run_job(cp, conv, msg, history):
    from app.jobs import service as jobs

    async def scenario():
        job = jobs.create("chat.completion", "t", lambda j: cp._run_chat_job(
            j, msg, conv, history, {"base_url": "http://127.0.0.1:9/v1", "model": "m", "mode": "chat",
                                    "thinking": "off", "max_output_tokens": 128}))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if job.status not in ("queued", "running"):
                break
        return job

    return asyncio.run(scenario())


def _large_materials(monkeypatch, cp, searched):
    from app.models_mgmt import context_budget
    from app.workflows import rag

    async def fake_ctx(base_url, model):
        return 32768

    async def search(collection, question, top_k, **kwargs):
        searched.append(question)
        return {"context": "[R1] 番組表 21:00 ニュースウオッチ9", "count": 1}

    monkeypatch.setattr(cp, "_context_max", fake_ctx)
    monkeypatch.setattr(rag, "collection_exists", lambda name: True)
    monkeypatch.setattr(rag, "collection_materials", lambda name, limit: None)   # 大きい資料（全部は入らない）
    monkeypatch.setattr(rag, "collection_size_chars", lambda name: 120_000)
    monkeypatch.setattr(rag, "search", search)
    monkeypatch.setattr(context_budget, "for_model", lambda base, model: type("B", (), {"rag_chars": 8000, "rag_top_k": 6})())


def test_chat_job_lets_the_model_search_materials_then_answers(client, monkeypatch):
    """大きい資料の会話: 道具を渡し、モデルが呼んだら探して結果を渡し、続きを生成する。前もっては探さない。"""
    import app.workflows.chat_persist as cp
    from app.models_mgmt.runtime_provider import RuntimeChunk
    from app.workflows import chat_tools

    _setup_job("cvt", "amt")
    searched, requests = [], []

    class Provider:
        tools_verified = True

        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            if len(requests) == 1:
                yield RuntimeChunk("tool_call", tool_calls=[{"id": "c1", "name": chat_tools.SEARCH_MATERIALS,
                                                            "arguments": '{"query": "神奈川 番組表 21時"}'}])
            else:
                yield RuntimeChunk("content", content="21 時はニュースウオッチ9 です。")

        async def cancel(self, request_id):
            return False

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    _large_materials(monkeypatch, cp, searched)
    job = _run_job(cp, "cvt", "amt", [{"role": "user", "content": "さっきの番組表で 21 時は？"}])
    assert job.status == "succeeded", job.error
    assert searched == ["神奈川 番組表 21時"]                       # 前もっては探さず、モデルの検索語で 1 回だけ
    assert requests[0].tools and any(chat_tools.SEARCH_MATERIALS in str(m.get("content")) for m in requests[0].messages)
    tool_msgs = [m for m in requests[1].messages if m.get("role") == "tool"]
    assert tool_msgs and "ニュースウオッチ9" in tool_msgs[0]["content"] and tool_msgs[0]["tool_call_id"] == "c1"
    assert requests[1].messages[-2]["tool_calls"][0]["function"]["name"] == chat_tools.SEARCH_MATERIALS
    from app.database import SessionLocal
    from app.models import ChatMessage

    db = SessionLocal()
    try:
        message = db.get(ChatMessage, "amt")
        assert message.content == "21 時はニュースウオッチ9 です。" and message.status == "completed"
    finally:
        db.close()


def test_chat_job_falls_back_when_the_model_cannot_use_tools(client, monkeypatch):
    """tools を受け付けないモデル: 覚えて、言い方の判定で探して道具なしで生成し直す。次の発言からは最初から道具を渡さない。"""
    import app.workflows.chat_persist as cp
    from app.models_mgmt.runtime_provider import RuntimeChunk, ToolsUnsupported
    from app.workflows import chat_tools

    _setup_job("cvu", "amu")
    searched, requests = [], []

    class Provider:
        tools_verified = True

        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            if request.tools:
                raise ToolsUnsupported("LLM HTTPエラー 400")
            yield RuntimeChunk("content", content="答え")

        async def cancel(self, request_id):
            return False

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    _large_materials(monkeypatch, cp, searched)
    chat_tools._unsupported.discard(("http://127.0.0.1:9/v1", "m"))
    try:
        job = _run_job(cp, "cvu", "amu", [{"role": "user", "content": "さっき調べた番組表の内容を教えて"}])
        assert job.status == "succeeded", job.error
        assert [bool(r.tools) for r in requests] == [True, False]
        assert searched == ["さっき調べた番組表の内容を教えて"]          # 従来の方式（発言そのもの）で探した
        assert any("関係する抜粋" in str(m.get("content")) for m in requests[1].messages)
        assert not chat_tools.supported("http://127.0.0.1:9/v1", "m")
    finally:
        chat_tools._unsupported.discard(("http://127.0.0.1:9/v1", "m"))


def test_unverified_runtimes_keep_the_keyword_rule(client, monkeypatch):
    """道具を確かめていない runtime（Radiance・Strata など）には道具を渡さず、従来どおり言い方の判定で探す。
    黙って道具を無視する runtime に渡すと、大きい資料を一度も探さなくなるため。"""
    import app.workflows.chat_persist as cp
    from app.models_mgmt import runtime_provider as rp
    from app.models_mgmt.runtime_provider import RuntimeChunk

    assert rp.LlamaCppRuntimeProvider.tools_verified and not rp.RadianceRuntimeProvider.tools_verified
    assert not rp.StrataRuntimeProvider.tools_verified and not rp.OpenAICompatibleRuntimeProvider.tools_verified
    _setup_job("cvv", "amv")
    searched, requests = [], []

    class Provider:
        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            yield RuntimeChunk("content", content="答え")

        async def cancel(self, request_id):
            return False

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    _large_materials(monkeypatch, cp, searched)
    job = _run_job(cp, "cvv", "amv", [{"role": "user", "content": "さっき調べた番組表の内容を教えて"}])
    assert job.status == "succeeded", job.error
    assert [bool(r.tools) for r in requests] == [False] and searched == ["さっき調べた番組表の内容を教えて"]
