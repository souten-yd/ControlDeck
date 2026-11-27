"""目的: ハブの関係・概要で検索の語を広げる部品（app/knowledge/hub_expand.py）と、その使う場所（RAG・特許・Web 調査）・MCP の道具（control_deck.news_knowledge）の試験。
ハブの応答はモック（hub_link.call を差し替える。ハブ・インターネットへは通信しない）。

根拠: 2026-11-27 利用者「（ニュースハブの）関係と概要あたりは参考にすることで、検索ワードを広げるとかそういうのに利用できないか。例えば特許とかにも」。
AGENTS.md「機能を取り込む条件」に従い、効果と時間は hub_expand_eval で測り（docs/implementation-status.md）、効いた所（RAG）だけ既定オン。
守ること:
- 重みは 別名 ＞ 上位・下位・置き換え ＞ 仲間・使う ＞ 概要の語。既定は 0.45 未満を足さない（測定で外れを足したため）。理由と元の Thing を付ける。
- 問いと同じ語・短すぎる語・版や hash・一般語（ハブで記事の多すぎる物・一般語の一覧）は外す。purpose=patent は製品名・社名より技術の言葉を重く。
- つながっていない・時間切れ・エラー・403（秘密の区分を許されていない）は空の結果。結果は覚える（失敗は短く）。
- 秘密の区分: 特許は常に confidential=true で問う。通常は今の会話のスコープに従う。MCP の道具は公開だけ（false）。
- 使う場所ごとに設定で入り切り（RAG の既定はオン、Web・特許はオフ）。つながっていなければオンでも呼ばない。広げても元の結果が消えない（融合）。
- MCP の道具の形・件数と大きさの上限。ハブへ呼ぶのは hub_link.call だけ（外部へ送らない）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.knowledge import hub_expand
from app.news import hub_link, settings

LLAMA = {"id": 6, "name": "llama.cpp", "type": "software", "article_count": 76, "match": {"via": "alias_exact", "terms": ["lcpp"]}, "kind": "entity"}


def graph(seed=6):
    return {"levels": [{"depth": 1, "nodes": [
        {"id": 10, "name": "ggml", "type": "tech", "article_count": 12},
        {"id": 11, "name": "Ollama", "type": "software", "article_count": 9},
        {"id": 12, "name": "Linux", "type": "software", "article_count": 900},
        {"id": 13, "name": "Meta", "type": "org", "article_count": 30}],
        "edges": [{"id": 1, "source": seed, "target": 10, "type": "part_of", "confidence": 0.9, "via": seed},      # llama.cpp は ggml の一部 → ggml は上位
                  {"id": 2, "source": 11, "target": seed, "type": "uses", "confidence": 0.9, "via": seed},      # Ollama が llama.cpp を使う → 使われる側
                  {"id": 3, "source": seed, "target": 12, "type": "competes", "confidence": 0.9, "via": seed},      # Linux は記事が多すぎる → 一般語
                  {"id": 4, "source": seed, "target": 13, "type": "made_by", "confidence": 0.9, "via": seed}]}]}


class Hub:
    """hub_link.call の模擬。呼ばれた (path, body, kwargs) を記録する。"""

    def __init__(self, seeds=None, context=None, neighbors=None, error=None):
        self.calls = []
        self.seeds = [LLAMA] if seeds is None else seeds
        self.context = context if context is not None else {"items": [{"id": 6, "name": "llama.cpp", "aliases": ["LLaMA.cpp", "llama-cpp"], "definition": "LLM推論を実行するソフトウェア"}]}
        self.neighbors = neighbors if neighbors is not None else graph()
        self.error = error

    def __call__(self, method, path, **kwargs):
        self.calls.append((path, kwargs.get("json_body"), kwargs))
        if self.error:
            raise self.error
        if path == "/retrieve/seeds":
            return {"items": self.seeds}
        if path == "/things/context":
            return self.context
        if path == "/graph/neighbors":
            return self.neighbors
        raise AssertionError(path)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    hub_expand.clear_cache()
    monkeypatch.setattr(hub_link, "ready", lambda state=None: True)
    yield
    hub_expand.clear_cache()


def terms_of(result):
    return {t["term"]: t for t in result["terms"]}


def test_weights_reasons_and_exclusions(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(hub_link, "call", hub)
    out = hub_expand.expand("lcpp の最新版", purpose="rag", min_weight=0.0)
    got = terms_of(out)
    assert got["LLaMA.cpp"]["weight"] == pytest.approx(1.0) and "別名" in got["LLaMA.cpp"]["reason"]      # 同じ語は別名（重い方）にまとまる。正式名は別名と同じ語になる
    assert "llama.cpp" not in got and "lcpp" not in got
    assert got["llama-cpp"]["weight"] == pytest.approx(1.0) and got["llama-cpp"]["source_thing"] == "llama.cpp"      # 別名が最も重い
    assert got["ggml"]["weight"] == pytest.approx(0.7 * 0.9) and "上位" in got["ggml"]["reason"]
    assert got["Ollama"]["weight"] == pytest.approx(0.5 * 0.9) and "使われる" in got["Ollama"]["reason"]
    assert "Linux" not in got                           # ハブで記事が多すぎる＝一般語
    assert got["Meta"]["weight"] == pytest.approx(0.3 * 0.9)      # 作り手は軽く
    assert got["llama-cpp"]["weight"] > got["ggml"]["weight"] > got["Ollama"]["weight"] > got["ソフトウェア"]["weight"] if "ソフトウェア" in got else got["llama-cpp"]["weight"] > got["Ollama"]["weight"]
    assert [t["weight"] for t in out["terms"]] == sorted((t["weight"] for t in out["terms"]), reverse=True)
    assert out["things"][0]["name"] == "llama.cpp" and out["reason"] == "ok"
    # 既定は 0.45 未満（使われる 0.45 は含む・作り手 0.27・概要の語 0.3 は含まない）
    hub_expand.clear_cache()
    default = terms_of(hub_expand.expand("lcpp の最新版", purpose="rag"))
    assert "ggml" in default and "Ollama" in default and "Meta" not in default and "推論" not in default


def test_canonical_name_is_added_when_the_query_used_an_alias(monkeypatch):
    monkeypatch.setattr(hub_link, "call", Hub(context={"items": [{"id": 6, "name": "llama.cpp", "aliases": [], "definition": ""}]}))
    got = terms_of(hub_expand.expand("lcpp の最新版", purpose="rag"))
    assert got["llama.cpp"]["weight"] == pytest.approx(0.9) and "lcpp" in got["llama.cpp"]["reason"] and "正式名" in got["llama.cpp"]["reason"]


def test_short_version_and_generic_words_are_dropped(monkeypatch):
    hub = Hub(context={"items": [{"id": 6, "name": "llama.cpp", "aliases": ["b12803", "v2.1.3", "ab", "software", "LCPP"], "definition": ""}]})
    monkeypatch.setattr(hub_link, "call", hub)
    got = terms_of(hub_expand.expand("llama.cpp", purpose="rag", min_weight=0.0))
    assert set(got) & {"b12803", "v2.1.3", "ab", "software"} == set() and "LCPP" in got


def test_patent_prefers_technical_words_over_product_names(monkeypatch):
    hub = Hub(neighbors={"levels": [{"depth": 1, "nodes": [{"id": 10, "name": "量子化", "type": "tech", "article_count": 5}, {"id": 11, "name": "ProductX", "type": "product", "article_count": 5}],
                                     "edges": [{"id": 1, "source": 6, "target": 10, "type": "part_of", "confidence": 0.9, "via": 6},
                                               {"id": 2, "source": 6, "target": 11, "type": "part_of", "confidence": 0.9, "via": 6}]}]})
    monkeypatch.setattr(hub_link, "call", hub)
    rag = terms_of(hub_expand.expand("llama.cpp", purpose="rag", min_weight=0.0))
    hub_expand.clear_cache()
    patent = terms_of(hub_expand.expand("llama.cpp", purpose="patent", min_weight=0.0))
    assert rag["量子化"]["weight"] == rag["ProductX"]["weight"]
    assert patent["量子化"]["weight"] > patent["ProductX"]["weight"] * 2
    assert patent["量子化"]["weight"] > rag["量子化"]["weight"]


def test_partial_matches_are_not_seeds(monkeypatch):
    hub = Hub(seeds=[{**LLAMA, "match": {"via": "partial", "terms": ["llama"]}}])
    monkeypatch.setattr(hub_link, "call", hub)
    out = hub_expand.expand("llama の話", purpose="rag")
    assert out["terms"] == [] and out["reason"] == "no_match" and [c[0] for c in hub.calls] == ["/retrieve/seeds"]      # 種が無ければ文脈・周りは呼ばない


def test_not_linked_timeout_error_and_forbidden_are_empty(monkeypatch):
    monkeypatch.setattr(hub_link, "ready", lambda state=None: False)
    monkeypatch.setattr(hub_link, "call", Hub(error=AssertionError("つながっていないのに呼んだ")))
    assert hub_expand.expand("x y", purpose="rag")["reason"] == "not_linked"
    monkeypatch.setattr(hub_link, "ready", lambda state=None: True)
    for error, reason in ((hub_link.HubError("ハブが時間内に答えませんでした", 0), "error"), (hub_link.HubError("時間切れ", 0), "timeout"), (hub_link.HubError("区分が許されていません", 403), "forbidden")):
        hub_expand.clear_cache()
        monkeypatch.setattr(hub_link, "call", Hub(error=error))
        out = hub_expand.expand("llama.cpp", purpose="rag")
        assert out["terms"] == [] and out["things"] == [] and out["reason"] == reason


def test_results_are_cached_and_failures_too(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(hub_link, "call", hub)
    first = hub_expand.expand("lcpp の最新版", purpose="rag")
    n = len(hub.calls)
    second = hub_expand.expand("lcpp の最新版", purpose="rag")
    assert len(hub.calls) == n and second["terms"] == first["terms"] and second.get("cached") is True
    hub_expand.expand("lcpp の最新版", purpose="web")      # 使う場所が違えば別（重みが違う）
    assert len(hub.calls) > n
    failing = Hub(error=hub_link.HubError("down", 0))
    monkeypatch.setattr(hub_link, "call", failing)
    hub_expand.expand("別の問い", purpose="rag")
    hub_expand.expand("別の問い", purpose="rag")
    assert len(failing.calls) == 1      # ハブが落ちている間、毎回待たない


def test_confidential_mark_follows_the_scope_and_patent_is_always_confidential(monkeypatch):
    from app.security import offline

    hub = Hub()
    monkeypatch.setattr(hub_link, "call", hub)
    hub_expand.expand("llama.cpp", purpose="rag")
    assert hub.calls[0][1]["confidential"] is False
    hub_expand.clear_cache()
    with offline.confidential_scope():
        hub_expand.expand("llama.cpp", purpose="rag")
    assert any(c[1].get("confidential") is True for c in hub.calls)
    hub.calls.clear()
    hub_expand.clear_cache()
    hub_expand.expand("llama.cpp", purpose="patent")
    assert all(c[1]["confidential"] is True for c in hub.calls)
    # 呼ぶのは hub_link.call だけ。時間（budget）を呼び出しの timeout に渡す
    assert all(c[2].get("timeout") is not None and c[2]["timeout"] <= 0.8 for c in hub.calls)


def test_enabled_follows_settings_and_link(monkeypatch):
    cfg = settings.normalize({})
    monkeypatch.setattr(settings, "load", lambda: cfg)
    assert hub_expand.enabled("rag") is True and hub_expand.enabled("web") is False and hub_expand.enabled("patent") is False
    assert settings.normalize({"hub": {"features": {"expand_web": True, "expand_rag": False}}})["hub"]["features"]["expand_web"] is True
    monkeypatch.setattr(hub_link, "ready", lambda state=None: False)
    assert hub_expand.enabled("rag") is False      # つながっていなければオンでも呼ばない
    assert hub_expand.enabled("nonsense") is False


def test_or_query_and_fusion_keep_the_original_order():
    terms = [{"term": "a b", "weight": 1}, {"term": 'c"d', "weight": 0.9}] + [{"term": f"t{i}", "weight": 0.5} for i in range(8)]
    q = hub_expand.or_query(terms, max_terms=3)
    assert q == '"a b" OR "c d" OR "t0"'
    base = [{"chunk_ref": f"x:{i}", "score": 1} for i in range(1, 4)]
    extra = [{"chunk_ref": "x:9", "score": 1}, {"chunk_ref": "x:1", "score": 1}]
    fused = hub_expand.fuse_hits(base, extra)
    assert [h["chunk_ref"] for h in fused][:2] == ["x:1", "x:2"] and "x:9" in [h["chunk_ref"] for h in fused]      # 両方に出たものが上がり、元の上位は残る
    assert len({h["chunk_ref"] for h in fused}) == len(fused)


# ---- 使う場所 ----

def test_rag_search_fuses_the_expanded_query_only_when_on(monkeypatch):
    from app.workflows import rag

    calls = []

    def fake_search(names, question, **kw):
        calls.append(question)
        if question.startswith('"'):
            return [{"chunk_ref": "news:9", "doc_ref": "news:9", "score": 1.0, "text": "正式名の記事", "parent": None, "source": "s", "locator": {}}]
        return [{"chunk_ref": "news:1", "doc_ref": "news:1", "score": 1.0, "text": "別名の記事", "parent": None, "source": "s", "locator": {}}]

    async def expand(query, *, purpose, **kw):
        assert purpose == "rag"
        return {"terms": [{"term": "llama.cpp", "weight": 0.9, "reason": "正式名", "source_thing": "llama.cpp"}], "things": [], "reason": "ok", "elapsed_ms": 1.0}

    monkeypatch.setattr(rag.store, "search", fake_search)
    monkeypatch.setattr(hub_expand, "aexpand", expand)
    monkeypatch.setattr(rag, "collection_exists", lambda c: True)
    monkeypatch.setattr(rag, "get_config", lambda c: {"search_mode": "fulltext", "embed_model": "", "embed_base_url": "", "hybrid_weight": 0.5})

    async def no_rerank(question, matches, top_k, budget_s=None):
        return matches[:top_k]

    monkeypatch.setattr(rag, "_maybe_rerank", no_rerank)
    monkeypatch.setattr(hub_expand, "enabled", lambda purpose: False)
    off = asyncio.run(rag.search("c", "LLaMA.cpp", 5))
    assert [m["doc_ref"] for m in off["matches"]] == ["news:1"] and "expanded_terms" not in off and len(calls) == 1
    calls.clear()
    monkeypatch.setattr(hub_expand, "enabled", lambda purpose: purpose == "rag")
    on = asyncio.run(rag.search("c", "LLaMA.cpp", 5))
    assert {m["doc_ref"] for m in on["matches"]} == {"news:1", "news:9"} and on["matches"][0]["doc_ref"] == "news:1"      # 元の結果が先・広げた分が足される
    assert on["expanded_terms"][0]["term"] == "llama.cpp" and calls == ["LLaMA.cpp", '"llama.cpp"']


def test_patent_search_widens_only_when_on_and_honours_excluded_terms(monkeypatch):
    from app.knowledge import documents
    from app.patents import service

    def doc(ref):
        return {"ref": ref, "title": ref, "collection": "c", "sensitivity": "public", "ai_use": "allowed", "score": 1.0, "hits": [{"text": "t", "locator": {}}], "relation": "match"}

    seen = []

    def fake_docs(question, **kw):
        seen.append(question)
        return [doc("general:2")] if question.startswith('"') else [doc("general:1")]

    async def no_vec(question):
        return None, ""

    async def expand(query, *, purpose, **kw):
        assert purpose == "patent"
        return {"terms": [{"term": "静電気放電", "weight": 1.0, "reason": "ESD の別名", "source_thing": "ESD"}, {"term": "クランプ", "weight": 0.8, "reason": "下位", "source_thing": "ESD"}],
                "things": [], "reason": "ok", "elapsed_ms": 1.0}

    monkeypatch.setattr(documents, "search_documents", fake_docs)
    monkeypatch.setattr(service, "_query_vector", no_vec)
    monkeypatch.setattr(hub_expand, "aexpand", expand)
    monkeypatch.setattr(hub_expand, "enabled", lambda purpose: False)
    info: dict = {}
    off = asyncio.run(service.search("ESD", expansion=info))
    assert [h["doc_ref"] for h in off] == ["general:1"] and info == {}
    monkeypatch.setattr(hub_expand, "enabled", lambda purpose: purpose == "patent")
    seen.clear()
    info = {}
    on = asyncio.run(service.search("ESD", expansion=info, exclude_terms=("クランプ",)))
    assert [h["doc_ref"] for h in on] == ["general:1", "general:2"] and on[1]["via"] == "hub_expand"
    assert [t["term"] for t in info["terms"]] == ["静電気放電"] and seen[1] == '"静電気放電"'      # 外した語は検索にも画面にも出ない


def test_web_search_adds_up_to_two_queries_when_on(monkeypatch):
    from app.workflows import research_tools

    asked = []

    async def fake_search_web(query, sites, time_range, limit, *, searx):
        asked.append(query)
        return {"results": [{"title": query, "url": f"https://example.com/{len(asked)}", "snippet": ""}], "notes": []}

    async def expand(query, *, purpose, **kw):
        assert purpose == "web"
        return {"terms": [{"term": f"w{i}", "weight": 0.9, "reason": "r", "source_thing": "s"} for i in range(5)], "things": [], "reason": "ok", "elapsed_ms": 1.0}

    monkeypatch.setattr(research_tools, "search_web", fake_search_web)
    monkeypatch.setattr(hub_expand, "aexpand", expand)
    monkeypatch.setattr(hub_expand, "enabled", lambda purpose: False)
    assert asyncio.run(research_tools._hub_expand_searches("q", None, "", 10, None)) == [] and asked == []
    monkeypatch.setattr(hub_expand, "enabled", lambda purpose: purpose == "web")
    out = asyncio.run(research_tools._hub_expand_searches("q", None, "", 10, None))
    assert asked == ["q w0", "q w1"] and len(out) == 2      # 追加は 2 本まで


# ---- MCP の道具 ----

def test_news_knowledge_tool_shape_limits_and_public_only(monkeypatch):
    from app.addons import agent_mcp

    card = {"id": 6, "name": "llama.cpp", "type": "software", "status": "experimental", "definition": "LLM推論を実行するソフトウェア" + "あ" * 400, "aliases": ["LLaMA.cpp"],
            "relations": {"upper": [{"id": 10, "name": "ggml"}], "uses": [{"id": 12, "name": "CUDA"}]},
            "facts": [{"text": "b12801 が公開された", "src_label": ["GitHub 2026-11-27 d4267"]}], "change_points": ["v1.0 に"], "opposing": ["遅い"], "open_questions": []}
    seeds = [LLAMA, {"kind": "document", "id": 1, "title": "llama.cpp が 1.0 に", "source": "窓の杜", "published_at": "2026-11-16T08:00:00Z"}]
    hub = Hub(seeds=seeds, context={"items": [card]})
    monkeypatch.setattr(hub_link, "call", hub)
    tool = agent_mcp._news_knowledge_tool()
    assert tool["name"] == agent_mcp.NEWS_KNOWLEDGE_TOOL == "control_deck.news_knowledge" and tool["inputSchema"]["required"] == ["query"]
    out = asyncio.run(agent_mcp._run_news_knowledge({"query": "llama.cpp の代わり"}))
    assert "llama.cpp (#6)" in out["text"] and "別名: LLaMA.cpp" in out["text"] and "上位: ggml" in out["text"] and "反対の見方: 遅い" in out["text"] and "根拠の記事" in out["text"]
    assert len(out["text"]) < 1200 and out["sources"][0]["source"] == "窓の杜"      # 概要は 300 文字まで
    assert all(c[1].get("confidential") is False for c in hub.calls)      # MCP の相手は cloud とみなす＝公開だけ
    with pytest.raises(Exception):
        asyncio.run(agent_mcp._run_news_knowledge({"query": "x", "extra": 1}))
    with pytest.raises(Exception):
        asyncio.run(agent_mcp._run_news_knowledge({"query": "x" * 501}))


def test_news_cards_cap_size_and_are_empty_when_not_linked(monkeypatch):
    big = {"id": 6, "name": "n", "type": "t", "status": "s", "definition": "d", "facts": [{"text": "x" * 100}] * 4, "aliases": ["a" * 50] * 6}
    monkeypatch.setattr(hub_link, "call", Hub(context={"items": [big] * 3}, seeds=[{**LLAMA, "id": 6}, {**LLAMA, "id": 7}, {**LLAMA, "id": 8}, {**LLAMA, "id": 9}]))
    out = hub_expand.news_cards("n", max_chars=500)
    assert len(out["text"]) <= 500 and out["truncated"] is True and len(out["things"]) <= hub_expand.CARD_THINGS_MAX
    monkeypatch.setattr(hub_link, "ready", lambda state=None: False)
    assert hub_expand.news_cards("n")["reason"] == "not_linked"
    monkeypatch.setattr(hub_link, "ready", lambda state=None: True)
    monkeypatch.setattr(hub_link, "call", Hub(error=hub_link.HubError("down", 0)))
    assert hub_expand.news_cards("n")["reason"] == "error"


def test_hub_link_call_accepts_a_short_timeout(monkeypatch):
    """検索の補助は待てないので、呼び出しごとに短い timeout を渡せる（既定の 30 秒を変えない）。"""
    seen = []
    real = hub_link._client

    def spy(timeout=None):
        seen.append(timeout)
        return real(timeout)

    monkeypatch.setattr(hub_link, "_client", spy)
    with pytest.raises(hub_link.HubError):
        hub_link.call("GET", "/x", url="http://127.0.0.1:9", token="t", timeout=0.2)
    assert seen == [0.2]
    assert hub_link._client().timeout.read == hub_link.READ_TIMEOUT


def test_the_module_sends_nothing_outside_the_hub():
    """外へ送るのは hub_link.call だけ（httpx・requests・guarded_client を直に使わない）。"""
    import app.knowledge.hub_expand as module
    from pathlib import Path

    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "httpx" not in text and "requests" not in text and "guarded_client" not in text and "shell=True" not in text
