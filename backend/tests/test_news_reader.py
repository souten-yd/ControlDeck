"""目的: ニュースリーダー（app/news/reader.py）の試験。本文の取り出し・LLM・通信は全部モック。

根拠: 2026-11-24 利用者「各ニュースの項目を押すと…本文を取得して、英語だった場合は日本語に翻訳して、リーダーみたいな形で表示…冒頭にその全文の要約を書いて、
要約の下に元リンク先のアドレスを記載した上で、翻訳文をその下から書く」「LLM で変換が終わったものから順次変えていけば、見れない不具合は発生しない」。
守ること: 本文の段落の分け方・訳の途中の状態を API が返す・要約と訳の保存と再利用（2 回目は LLM を呼ばない）・取り出せないとき・
CPU の LLM を 2 つ起動しない（リーダーは順番を取り、下ごしらえは譲る）・Google ニュースは元の URL に解いてから取る。
"""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from sqlalchemy import delete

from app.news import cpu_llm, reader
from app.news import db as news_db
from app.news.db import NewsItem, NewsReader
from tests.conftest import CSRF_HEADERS
from tests.test_news import add, login, NOW

BODY_EN = ("# Heading One\n\n" + "The new model improves accuracy while using less compute. " * 3 + "\n\n- first point about speed\n- second point about memory\n\n"
           "> A quote from the maintainers about the release schedule.\n\n```\nprint('x')\n```\n\n" + "Second paragraph describes the benchmark results in detail. " * 3)


@pytest.fixture(autouse=True)
def clean_reader(client):
    with news_db.session() as db:
        db.execute(delete(NewsReader))
        db.commit()
    reader._jobs.clear()
    yield


def test_split_blocks_kinds_and_title_dropped():
    blocks = reader.split_blocks("# The Title\n\nHello world paragraph.\n\n## Sub\n\n- a one\n- b two\n\n> quoted text\n\n```\ncode\n```\n\n![alt](https://x.test/i.png)", "The Title")
    assert [b["t"] for b in blocks] == ["p", "h", "ul", "q", "code", "img"]
    assert blocks[2]["src"] == "a one\nb two" and blocks[1]["level"] == 2 and blocks[5]["src"] == "https://x.test/i.png"
    assert reader.split_blocks("![x](javascript:alert(1))", "") == [{"t": "p", "src": "![x](javascript:alert(1))"}]


def test_long_paragraph_is_split_and_window_limits():
    text = " ".join(["Sentence number %d is here." % n for n in range(200)])
    blocks = reader.split_blocks(text)
    assert len(blocks) > 1 and all(len(b["src"]) <= reader.BLOCK_MAX for b in blocks)
    assert len(reader.window(blocks, 1000)) < len(reader.window(blocks, 100000)) and reader.window(blocks, 1) == [0]
    groups = reader.chunks(blocks, reader.window(blocks, 100000))
    assert all(len(g) <= reader.CHUNK_BLOCKS for g in groups) and sum(len(g) for g in groups) == len(blocks)


def test_detect_lang_and_parse_rules():
    assert reader.detect_lang("これは日本語の文章です。ニュースを読みます。") == "ja" and reader.detect_lang("This is English text only.") == "other"
    assert reader.parse_translations('{"1": "訳です", "2": "still english"}', ["a", "b"]) == {1: "訳です"}
    assert reader.parse_translations("not json", ["a"]) == {}
    assert reader.parse_summary('{"summary": "日本語の要約です。"}') == "日本語の要約です。" and reader.parse_summary('{"summary": "english"}') == ""


class FakeServer:
    def __init__(self, stop_after: int | None = None):
        self.calls: list[str] = []
        self.stop_after = stop_after

    async def complete(self, messages, *, max_tokens, schema=None, temperature=0.2, think=None):
        user = messages[-1]["content"]
        self.calls.append(user)
        if self.stop_after is not None and len(self.calls) > self.stop_after:
            raise cpu_llm.CpuLlmUnavailable("止まりました")
        if "要約" in messages[0]["content"]:
            return json.dumps({"summary": "新しいモデルの要約です。"}, ensure_ascii=False)
        count = len(schema["required"])
        return json.dumps({str(n): f"訳{n}" for n in range(1, count + 1)}, ensure_ascii=False)


def install(monkeypatch, server: FakeServer, markdown=BODY_EN, status="success"):
    entered = {"n": 0, "priority": None}

    @contextlib.asynccontextmanager
    async def session(priority=False):
        entered["n"] += 1
        entered["priority"] = priority
        yield server

    async def collect(url, **kw):
        collect.urls.append(url)
        return {"status": status, "markdown": markdown, "final_url": url, "error": "HTTP 403" if status != "success" else "", "extractor": "test"}
    collect.urls = []
    monkeypatch.setattr(reader.cpu_llm, "session", session)
    monkeypatch.setattr(reader.cpu_llm, "check_ready", lambda: (True, ""))
    monkeypatch.setattr("app.web_collector.fetch.collect", collect)
    return entered, collect


def run_job(item_id: int) -> None:
    async def go():
        reader.ensure_job(item_id)
        await reader._jobs[item_id] if item_id in reader._jobs else None
    asyncio.run(go())


def test_job_extracts_summarizes_translates_and_saves(client, monkeypatch):
    item = add("https://en.test/a", title="Heading One", source="EN")
    server = FakeServer()
    entered, collect = install(monkeypatch, server)
    run_job(item)
    assert entered["n"] == 1 and entered["priority"] is True and collect.urls == ["https://en.test/a"]
    page = reader.view(*reader._load(item))
    assert page["status"] == "ready" and page["summary"] == "新しいモデルの要約です。" and page["domain"] == "en.test"
    assert page["progress"]["done"] == page["progress"]["total"] > 0 and page["lang"] == "other"
    translated = [b for b in page["blocks"] if "ja" in b]
    assert translated and all(b["ja"].startswith("訳") for b in translated)
    assert not any("ja" in b for b in page["blocks"] if b["t"] == "code")
    # 保存済みなら次からは LLM も通信も使わない
    calls = len(server.calls)
    run_job(item)
    assert len(server.calls) == calls and len(collect.urls) == 1 and entered["n"] == 1


def test_api_returns_midway_state_and_requires_visibility(client, monkeypatch):
    item = add("https://en.test/b", title="T")
    blocks = reader.split_blocks(BODY_EN, "")
    blocks[0]["ja"] = "最初の訳"
    reader._save(item, status="ok", blocks=json.dumps(blocks), lang="other", summary="", text_hash="h", final_url="https://en.test/b")
    monkeypatch.setattr(reader, "ensure_job", lambda i: False)
    login(client, "news-reader")
    page = client.get(f"/api/v1/news/items/{item}/reader").json()
    assert page["summary"] == "" and page["blocks"][0]["ja"] == "最初の訳" and page["blocks"][1]["ja"] is None
    assert 0 < page["progress"]["done"] < page["progress"]["total"]
    secret = add("https://en.test/ev", kind="event", uid=999999)
    assert client.get(f"/api/v1/news/items/{secret}/reader").status_code == 404
    assert client.get("/api/v1/news/items/424242/reader").status_code == 404
    assert client.post(f"/api/v1/news/items/{item}/reader/more", headers=CSRF_HEADERS).status_code == 200


def test_japanese_article_is_summarized_not_translated(client, monkeypatch):
    item = add("https://ja.test/a", title="日本語")
    server = FakeServer()
    install(monkeypatch, server, markdown="これは日本語の記事の本文です。" * 20 + "\n\n" + "二つ目の段落も日本語で書かれています。" * 10)
    run_job(item)
    page = reader.view(*reader._load(item))
    assert page["summary"] and page["progress"]["total"] == 0 and all("ja" not in b for b in page["blocks"]) and len(server.calls) == 1


def test_extraction_failure_keeps_excerpt_and_can_retry(client, monkeypatch):
    item = add("https://blocked.test/a", title="T", summary="抜粋です")
    server = FakeServer()
    install(monkeypatch, server, markdown="", status="blocked")
    run_job(item)
    page = reader.view(*reader._load(item))
    assert page["status"] == "failed" and page["excerpt"] == "抜粋です" and "blocked" in page["error"] and not server.calls
    assert reader.needs_job(reader._load(item)[1]) is False
    reader.retry(item)
    assert reader._load(item)[1] is None


def test_llm_stops_midway_and_resumes_from_there(client, monkeypatch):
    item = add("https://en.test/c", title="T")
    server = FakeServer(stop_after=2)
    install(monkeypatch, server, markdown="\n\n".join(f"Paragraph {n} " + "word " * 120 for n in range(8)))
    run_job(item)
    row = reader._load(item)[1]
    assert row.llm_error and row.summary and reader.needs_job(row) is False
    done = sum(1 for b in reader.blocks_of(row) if b.get("ja"))
    assert 0 < done < 8
    reader.retry(item)
    server2 = FakeServer()
    install(monkeypatch, server2, markdown="")
    run_job(item)
    page = reader.view(*reader._load(item))
    assert page["progress"]["done"] == page["progress"]["total"] == 8 and len(server2.calls) < 8


def test_more_raises_limit_and_translates_rest(client, monkeypatch):
    item = add("https://en.test/d", title="T")
    body = "\n\n".join(f"Paragraph {n} " + "word " * 300 for n in range(14))     # 約 21,000 文字
    install(monkeypatch, FakeServer(), markdown=body)
    run_job(item)
    page = reader.view(*reader._load(item))
    assert page["remaining"] > 0 and page["progress"]["done"] == page["progress"]["total"] < 14
    assert reader.more(item)
    run_job(item)
    page = reader.view(*reader._load(item))
    assert page["progress"]["total"] > 7 and page["progress"]["done"] == page["progress"]["total"]


def test_google_news_resolves_before_fetching(client, monkeypatch):
    item = add("https://news.google.com/rss/articles/CBMiXXXX", title="T")
    entered, collect = install(monkeypatch, FakeServer())

    async def resolve(url, client=None):
        return "https://origin.test/story"
    monkeypatch.setattr(reader.gnews, "resolve", resolve)
    run_job(item)
    assert collect.urls == ["https://origin.test/story"]
    assert reader.view(*reader._load(item))["domain"] == "origin.test"


def test_external_model_respects_egress_permission(client, monkeypatch):
    item = add("https://en.test/e", title="T")
    install(monkeypatch, FakeServer())
    monkeypatch.setattr(reader.settings, "load", lambda: {"llm": {"prep_model": "cloud:openrouter/x:free"}})

    def deny(model):
        raise reader.llm.LlmNotAllowed("外部への送信が許可されていません")
    monkeypatch.setattr(reader.llm, "check_allowed", deny)
    run_job(item)
    row = reader._load(item)[1]
    assert "許可" in row.llm_error and row.status == "ok" and not row.summary


def test_reader_takes_turn_before_prep_and_prep_yields(monkeypatch):
    """リーダーが順番を待っている間は、下ごしらえが次のバッチの前に譲る（CPU の LLM を 2 つ起動しない）。"""
    from app.news import prep

    async def go():
        cpu_llm._run_lock.acquire()
        try:
            waiter = asyncio.create_task(_enter())
            for _ in range(50):
                if cpu_llm.priority_waiting():
                    break
                await asyncio.sleep(0.02)
            assert cpu_llm.priority_waiting() == 1
            calls = []

            async def ask(part):
                calls.append(1)
                return {}
            items = [{"id": n} for n in range(25)]
            result = await prep._run_batches(items, 10, ask, ask_yield=lambda: cpu_llm.priority_waiting() > 0)
            assert not calls and "譲" in result["error"]
        finally:
            cpu_llm._run_lock.release()
        await waiter

    async def _enter():
        monkeypatch.setattr(cpu_llm.CpuLlmServer, "start", lambda self: None)
        monkeypatch.setattr(cpu_llm.CpuLlmServer, "stop", lambda self: None)
        async with cpu_llm.session(priority=True):
            pass
    monkeypatch.setattr(prep, "_mark_failed", lambda ids: None)
    asyncio.run(go())
    assert cpu_llm.priority_waiting() == 0
