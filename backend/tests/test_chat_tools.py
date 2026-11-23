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

    def only_ours(request):
        # httpx.AsyncClient の差し替えはプロセス全体に効く。全体実行では、他の試験が残した裏の処理の通信まで
        # ここへ来て、記録（sent[0]）を横取りして落ちた（2026-11-23）。試験の宛先だけを handler へ渡す
        if request.url.host != "127.0.0.1" or request.url.port != 9999:
            return httpx.Response(503, text="test transport")
        return handler(request)

    transport = httpx.MockTransport(only_ours)

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


@pytest.fixture(autouse=True)
def _no_shared_knowledge(monkeypatch):
    """ここは会話の資料・Web 検索の道具を確かめる。ナレッジ横断の道具（test_knowledge_documents.py）は出さない
    （DB はテスト全体で共有され、他のテストが作ったコレクションで道具の一覧が変わるため）。"""
    from app.knowledge import store

    monkeypatch.setattr(store, "has_shared_collections", lambda: False)


def _setup_job(conv, msg):
    """会話と回答の行を作る。ID は他のテストと重ならないよう tt- を付ける（DB はテスト全体で共有）。"""
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

    _setup_job("tt-cvt", "tt-amt")
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
    job = _run_job(cp, "tt-cvt", "tt-amt", [{"role": "user", "content": "さっきの番組表で 21 時は？"}])
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
        message = db.get(ChatMessage, "tt-amt")
        assert message.content == "21 時はニュースウオッチ9 です。" and message.status == "completed"
    finally:
        db.close()


def test_chat_job_falls_back_when_the_model_cannot_use_tools(client, monkeypatch):
    """tools を受け付けないモデル: 覚えて、言い方の判定で探して道具なしで生成し直す。次の発言からは最初から道具を渡さない。"""
    import app.workflows.chat_persist as cp
    from app.models_mgmt.runtime_provider import RuntimeChunk, ToolsUnsupported
    from app.workflows import chat_tools

    _setup_job("tt-cvu", "tt-amu")
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
        job = _run_job(cp, "tt-cvu", "tt-amu", [{"role": "user", "content": "さっき調べた番組表の内容を教えて"}])
        assert job.status == "succeeded", job.error
        assert [bool(r.tools) for r in requests] == [True, False]
        assert searched == ["さっき調べた番組表の内容を教えて"]          # 従来の方式（発言そのもの）で探した
        assert any("関係する抜粋" in str(m.get("content")) for m in requests[1].messages)
        assert not chat_tools.supported("http://127.0.0.1:9/v1", "m")
    finally:
        chat_tools._unsupported.discard(("http://127.0.0.1:9/v1", "m"))


def test_unverified_runtimes_keep_the_keyword_rule(client, monkeypatch):
    """道具を確かめていない runtime（Ollama・外部など）には道具を渡さず、従来どおり言い方の判定で探す。
    黙って道具を無視する runtime に渡すと、大きい資料を一度も探さなくなるため。"""
    import app.workflows.chat_persist as cp
    from app.models_mgmt import runtime_provider as rp
    from app.models_mgmt.runtime_provider import RuntimeChunk

    assert rp.LlamaCppRuntimeProvider.tools_verified and rp.RadianceRuntimeProvider.tools_verified
    assert rp.StrataRuntimeProvider.tools_verified and rp.MayaRuntimeProvider.tools_verified
    assert not rp.OllamaRuntimeProvider.tools_verified
    assert not rp.OpenAICompatibleRuntimeProvider.tools_verified
    _setup_job("tt-cvv", "tt-amv")
    searched, requests = [], []

    class Provider:
        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            yield RuntimeChunk("content", content="答え")

        async def cancel(self, request_id):
            return False

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    _large_materials(monkeypatch, cp, searched)
    job = _run_job(cp, "tt-cvv", "tt-amv", [{"role": "user", "content": "さっき調べた番組表の内容を教えて"}])
    assert job.status == "succeeded", job.error
    assert [bool(r.tools) for r in requests] == [False] and searched == ["さっき調べた番組表の内容を教えて"]


def test_auto_mode_lets_the_model_search_the_web_and_cites_sources(client, monkeypatch):
    """機能の選択が「自動」なら Web 検索も道具で渡す。呼ばれたら検索して出典を登録し、[R番号] 付きの結果を渡す。
    回答は Web 検索の回答として会話の資料へ保存する（後の質問で探せるように）。"""
    import app.workflows.chat_persist as cp
    from app.models_mgmt.runtime_provider import RuntimeChunk
    from app.workflows import chat_router as cr
    from app.workflows import chat_tools, rag

    _setup_job("tt-cvw", "tt-amw")
    requests, saved, bases = [], [], []

    class Provider:
        tools_verified = True

        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            if len(requests) == 1:
                yield RuntimeChunk("tool_call", tool_calls=[{"id": "w1", "name": chat_tools.WEB_SEARCH,
                                                            "arguments": '{"query": "横浜 天気 明日"}'}])
            else:
                yield RuntimeChunk("content", content="明日は晴れです [R1]。")

        async def cancel(self, request_id):
            return False

    queries = []

    async def fake_searx(query, limit, time_range, pageno):
        queries.append(query)
        return [{"title": "横浜の天気", "url": "https://example.invalid/w", "snippet": "明日は晴れ"}]

    async def fake_read(url, focus="", max_chars=6000, **kwargs):   # 時期が大事な依頼（明日の天気）は本文の日付を確かめる
        return {"ok": True, "url": url, "title": "横浜の天気", "date": "", "text": "2026年11月23日 横浜 晴れ " * 15, "links": [],
                "total_chars": 20, "truncated": False}

    async def register(conv, text, source, basis="source_fact"):
        saved.append(source)
        bases.append(basis)

    async def fake_ctx(base_url, model):
        return 32768

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    import datetime as dt

    from app.workflows import research_time, research_tools

    monkeypatch.setattr(research_tools, "searx_for", lambda params, base_url, model: fake_searx)
    monkeypatch.setattr(research_tools, "read_page", fake_read)
    monkeypatch.setattr(research_time, "now_jst", lambda: dt.datetime(2026, 11, 22, 15, 40, tzinfo=research_time.TZ))
    monkeypatch.setattr(cp, "register_chat_document", register)
    monkeypatch.setattr(cp, "_context_max", fake_ctx)
    monkeypatch.setattr(rag, "collection_exists", lambda name: False)
    from app.jobs import service as jobs

    async def scenario():
        job = jobs.create("chat.completion", "t", lambda j: cp._run_chat_job(
            j, "tt-amw", "tt-cvw", [{"role": "user", "content": "明日の横浜の天気は？"}],
            {"base_url": "http://127.0.0.1:9/v1", "model": "m", "mode": "chat", "mode_choice": "auto",
             "thinking": "off", "max_output_tokens": 128}))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if job.status not in ("queued", "running"):
                break
        return job

    job = asyncio.run(scenario())
    assert job.status == "succeeded", job.error
    assert [t["function"]["name"] for t in requests[0].tools] == [chat_tools.WEB_SEARCH, chat_tools.READ_PAGE, chat_tools.HF_MODELS]   # 資料が無いので Web の 2 つと Hugging Face
    tool_msg = [m for m in requests[1].messages if m.get("role") == "tool"][0]["content"]
    assert "横浜の天気" in tool_msg and "[R" in tool_msg
    assert "2026-11-23" in tool_msg and "依頼の期間に合う" in tool_msg   # 明日（11/23）の天気は、本文の日付で確かめた
    assert any("11月23日" in q for q in queries)   # 「明日」は絶対の日付の検索語でも探す
    assert "2026年11月22日（日）15時40分" in "".join(str(m.get("content")) for m in requests[0].messages)   # 今の日時を渡す
    events = [e for e in job.events if e.get("message") == "sources"]
    assert events and events[-1]["sources"][0]["url"] == "https://example.invalid/w"
    assert saved and saved[0].startswith("web回答")
    assert bases[0] == "model_inference"     # AI の回答は推論として保存（2026-11-21）
    # 「自動」以外（対話を選んだ）では Web の道具を渡さない
    requests.clear()
    _setup_job("tt-cvx", "tt-amx")

    async def scenario2():
        job = jobs.create("chat.completion", "t", lambda j: cp._run_chat_job(
            j, "tt-amx", "tt-cvx", [{"role": "user", "content": "明日の横浜の天気は？"}],
            {"base_url": "http://127.0.0.1:9/v1", "model": "m", "mode": "chat", "mode_choice": "chat",
             "thinking": "off", "max_output_tokens": 128}))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if job.status not in ("queued", "running"):
                break
        return job

    class Plain(Provider):
        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            yield RuntimeChunk("content", content="答え")

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Plain())
    assert asyncio.run(scenario2()).status == "succeeded" and requests[0].tools is None


def test_route_skips_the_llm_decision_for_tool_capable_models(admin_client, monkeypatch):
    """道具を使えるモデルでは、曖昧な依頼を LLM で判定しない（送信ごとに 1〜1.5 秒かかっていた）。対話として送り、
    調べるかはモデルが道具で決める。明らかな依頼（論文・Deep）はルールで即決のまま。使えないモデルは従来どおり LLM で判定。"""
    from app.workflows import assistant_planner
    from tests.conftest import CSRF_HEADERS

    called = []

    async def decide(*args, **kwargs):
        called.append(args[0])
        return assistant_planner.AssistantPlan(mode="web", reason="LLM", decided_by="llm")

    class Verified:
        tools_verified = True

    monkeypatch.setattr(assistant_planner, "decide", decide)
    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Verified())
    monkeypatch.setattr("app.models_mgmt.runtime_provider.resolve_target", lambda base, model: (base, model))
    body = {"content": "この前の続きで、もう少し教えて", "base_url": "http://127.0.0.1:9/v1", "model": "m"}
    r = admin_client.post("/api/v1/chat/route", json=body, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["mode"] == "chat" and r.json()["decided_by"] == "tools" and not called
    r = admin_client.post("/api/v1/chat/route", json={**body, "content": "この分野の論文を探して"}, headers=CSRF_HEADERS)
    assert r.json()["mode"] == "academic" and r.json()["decided_by"] == "rule"
    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: object())
    r = admin_client.post("/api/v1/chat/route", json=body, headers=CSRF_HEADERS)
    assert r.json()["decided_by"] == "llm" and called


def test_final_round_turns_tool_exchanges_into_plain_notes():
    """上限まで呼んだ後の最後の回: 道具のやり取りを普通の資料にまとめる。残したままだと Radiance が 3 回中 2 回、
    呼び出しを本文に書いた（まとめると 5 回中 5 回とも本文で答えた。2026-11-20）。本文に残った呼び出しは除く。"""
    from app.workflows import chat_tools

    msgs = [{"role": "system", "content": "指示"}, {"role": "user", "content": "今週末の映画は？"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function",
                                                                 "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "[R1] 11月21日公開 ボトムズ"}]
    out = chat_tools.flatten_for_final(msgs)
    assert [m["role"] for m in out] == ["system", "system", "user"]
    assert "ボトムズ" in out[1]["content"] and chat_tools.FINAL_ROUND_NOTE in out[1]["content"]
    assert chat_tools.strip_tool_markup("答え<tool_call> <function=web_search> </function> </tool_call>") == "答え"
    assert chat_tools.strip_tool_markup("<tool_call> <function=web_search>") == ""
    assert chat_tools.strip_tool_markup("普通の答え") == "普通の答え"


def _auto_job(cp, conv, msg, history, extra=None):
    from app.jobs import service as jobs

    async def scenario():
        job = jobs.create("chat.completion", "t", lambda j: cp._run_chat_job(
            j, msg, conv, history, {"base_url": "http://127.0.0.1:9/v1", "model": "m", "mode": "chat", "mode_choice": "auto",
                                    "thinking": "off", "max_output_tokens": 128, **(extra or {})}))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if job.status not in ("queued", "running"):
                break
        return job

    return asyncio.run(scenario())


def _auto_provider(monkeypatch, cp, replies, requests):
    from app.models_mgmt.runtime_provider import RuntimeChunk
    from app.workflows import rag

    class Provider:
        tools_verified = True

        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            reply = replies[min(len(requests), len(replies)) - 1]
            if isinstance(reply, dict):
                yield RuntimeChunk("tool_call", tool_calls=[reply])
            else:
                yield RuntimeChunk("content", content=reply)

        async def cancel(self, request_id):
            return False

    async def fake_ctx(base_url, model):
        return 32768

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    monkeypatch.setattr(cp, "_context_max", fake_ctx)
    monkeypatch.setattr(rag, "collection_exists", lambda name: False)


def test_auto_mode_lets_the_model_hand_work_to_opencode(monkeypatch):
    """「自動」では、作業の依頼かどうかを会話の LLM が判断し、OpenCode に任せる道具を呼んだら同じ回答の枠のまま
    OpenCode の回へ移る（語では決めない）。作業フォルダはこの会話で前に使ったもの。
    根拠: 2026-11-20 利用者「自動でも適切に OpenCode へ遷移する作りに。すぐ移る形で」。以前は語（作って・直して）や
    会話の流れで決めていて、「M5・M6 チップはローカル LLM としてどうですか」で OpenCode が開いた。"""
    import app.workflows.chat_persist as cp
    from app.workflows import chat_tools

    _setup_job("tt-cvo", "tt-amo")
    requests, handed = [], []
    _auto_provider(monkeypatch, cp, [{"id": "o1", "name": chat_tools.HAND_OFF_OPENCODE,
                                      "arguments": '{"task": "ログイン画面にパスワード表示の切り替えを足す"}'}], requests)
    monkeypatch.setattr(cp, "_opencode_hand_off_target", lambda conv, params: ("app", ""))
    original = cp._run_chat_job

    async def run(job, assistant_id, conv_id, history, params):
        if params["mode"] == "code":
            handed.append((history, params))
            return {"assistant_message_id": assistant_id}
        return await original(job, assistant_id, conv_id, history, params)

    monkeypatch.setattr(cp, "_run_chat_job", run)
    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "ログイン画面でパスワードを見られるようにして"}]
    job = _auto_job(cp, "tt-cvo", "tt-amo", history)
    assert job.status == "succeeded", job.error
    assert chat_tools.HAND_OFF_OPENCODE in [t["function"]["name"] for t in requests[0].tools]
    assert len(handed) == 1 and len(requests) == 1                     # チャットの答えは作らずに移る
    sent_history, params = handed[0]
    assert sent_history == history                                     # 依頼はそのまま OpenCode へ
    assert params["code_project"] == "app" and params["code_agent"] == "build"
    assert params["handoff_task"] == "ログイン画面にパスワード表示の切り替えを足す"
    assert any(e.get("message") == "handoff" for e in job.events)


def test_hand_off_without_a_project_asks_the_user_to_choose_one(monkeypatch):
    """作業フォルダが決まっていなければ移らない（新しいプロジェクトを勝手に作らない）。選んで送り直すよう伝えさせる。"""
    import app.workflows.chat_persist as cp
    from app.workflows import chat_tools

    _setup_job("tt-cvp", "tt-amp")
    requests = []
    _auto_provider(monkeypatch, cp, [{"id": "o1", "name": chat_tools.HAND_OFF_OPENCODE, "arguments": '{"task": "アプリを作る"}'},
                                     "作業フォルダを選んでください。"], requests)
    monkeypatch.setattr(cp, "_opencode_hand_off_target", lambda conv, params: ("", ""))
    job = _auto_job(cp, "tt-cvp", "tt-amp", [{"role": "user", "content": "家計簿アプリを作って"}])
    assert job.status == "succeeded", job.error
    tool_msg = [m for m in requests[1].messages if m.get("role") == "tool"][0]["content"]
    assert tool_msg == chat_tools.NO_PROJECT_NOTE
    assert not any(e.get("message") == "handoff" for e in job.events)


def test_opencode_tool_is_not_offered_without_opencode_or_for_images_and_voice(monkeypatch):
    """OpenCode が無効・声の会話では渡さない。画像つきは、OpenCode のモデルが画像を読めないときだけ渡さない
    （2026-11-20: OpenCode は画像を発言に添付して読めるモデルへ渡す。読めないモデルへ任せると画像が消える）。"""
    import app.workflows.chat_persist as cp
    from app.features import registry
    from app.workflows import chat_tools

    requests = []
    _auto_provider(monkeypatch, cp, ["答え"], requests)
    monkeypatch.setattr(registry, "is_enabled", lambda feature: False)
    _setup_job("tt-cvq", "tt-amq")
    assert _auto_job(cp, "tt-cvq", "tt-amq", [{"role": "user", "content": "x"}]).status == "succeeded"
    assert chat_tools.HAND_OFF_OPENCODE not in [t["function"]["name"] for t in requests[0].tools]
    monkeypatch.setattr(registry, "is_enabled", lambda feature: True)
    monkeypatch.setattr(cp, "opencode_image_problem", lambda: "OpenCode のモデル（x）は画像を読めません")
    for i, extra in enumerate([{"voice_style": "friendly"}, {"attachments": ["0123456789abcdef.png"]}]):
        requests.clear()
        _setup_job(f"tt-cvr{i}", f"tt-amr{i}")
        assert _auto_job(cp, f"tt-cvr{i}", f"tt-amr{i}", [{"role": "user", "content": "x"}], extra).status == "succeeded"
        assert chat_tools.HAND_OFF_OPENCODE not in [t["function"]["name"] for t in (requests[0].tools or [])]
    requests.clear()
    _setup_job("tt-cvs", "tt-ams")
    assert _auto_job(cp, "tt-cvs", "tt-ams", [{"role": "user", "content": "x"}]).status == "succeeded"
    assert chat_tools.HAND_OFF_OPENCODE in [t["function"]["name"] for t in requests[0].tools]   # 有効なら渡す
    monkeypatch.setattr(cp, "opencode_image_problem", lambda: "")   # 画像を読めるモデルなら、画像つきでも任せられる
    requests.clear()
    _setup_job("tt-cvi", "tt-ami")
    assert _auto_job(cp, "tt-cvi", "tt-ami", [{"role": "user", "content": "x"}],
                     {"attachments": ["0123456789abcdef.png"]}).status == "succeeded"
    assert chat_tools.HAND_OFF_OPENCODE in [t["function"]["name"] for t in (requests[0].tools or [])]
