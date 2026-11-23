"""目的: ニュースの RAG への蓄積・LLM の選択と外部送信の許可・更新のタイミング・日刊週刊のまとめの試験。外へは通信しない（LLM・埋め込み・ページ取得は全部モック）。

根拠: 2026-11-23 利用者「ニュースページ及びフィードの更新タイミング、その際に利用する LLM は個別に設定できること。このデータは基本公開情報だと
思うので OpenRouter の無料 LLM の利用を想定している。もちろんローカル LLM でも良い。検索した情報については、RAG に蓄積し、後で串刺し検索へ
利用したりできるようにして。ニュース用のデータベースは他と分けておいて。」
守ること:
- 記事は知識基盤の分野 news（public）へ入り、横断検索（クラウド向けを含む）に出る。本文の取り込みは設定を入れたときだけ。
- クラウドのモデルを選んでいて外部への送信が許可されていなければ、ローカルへ黙って切り替えず、その回のまとめを作らない（理由を残す）。
- クラウドへ出来事・チャットの題・好みは送らない。LLM が落ちたら、印つきの見出しの一覧のまとめになる。
- まとめの時刻・曜日・取得しない時間帯・通知。
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import delete, select

from app.alerts import events
from app.knowledge import context, ingest, store as kstore
from app.models_mgmt import cloud
from app.news import channels, digest, gist, llm, rag, service, settings, sources, store
from app.news import db as news_db
from app.news.db import NewsAction, NewsDigest, NewsItem
from tests.test_news import add, user_id

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
FAKE_DIM = 4


def _vec(text: str) -> np.ndarray:
    v = np.zeros(FAKE_DIM, dtype=np.float32)
    v[0 if "Qwen" in text else 1 if "ROCm" in text else 2] = 1.0
    return v


@pytest.fixture(autouse=True)
def isolated(client, tmp_path, monkeypatch):
    """設定・クラウドの設定は試験ごとの場所へ。ニュースの DB・RAG の news は空から。埋め込みと LLM は偽物（外へ出ない）。"""
    monkeypatch.setattr(sources, "_path", lambda: tmp_path / "news" / "sources.json")
    monkeypatch.setattr(settings, "_path", lambda: tmp_path / "news" / "settings.json")
    monkeypatch.setattr(channels, "_path", lambda: tmp_path / "news" / "channels.json")
    monkeypatch.setattr(cloud, "data_dir", lambda: tmp_path)
    with news_db.session() as db:
        for model in (NewsAction, NewsItem, NewsDigest):
            db.execute(delete(model))
        db.commit()
    kstore.delete_collection("news")

    async def fake_embed(chunks, collection):
        for c in chunks:
            c.vector = _vec(c.text)
        return "fake-embed", True

    async def fake_query(question):
        return _vec(question), "fake-embed"

    monkeypatch.setattr(ingest, "_embed", fake_embed)
    monkeypatch.setattr(context, "query_vector", fake_query)
    service._last.update(started=0.0, finished=None, new=0, gist=None, rag=None)
    yield
    kstore.delete_collection("news")


def item(url: str, title: str, *, summary: str = "", kind: str = "news", source: str = "S", key: str = "rss:s") -> dict:
    return {"id": 0, "kind": kind, "source": source, "source_key": key, "title": title, "url": url, "summary": summary,
            "published_at": "2026-11-23T10:00:00Z", "topics": ["Qwen"], "gist": ""}


def run(coro):
    return asyncio.run(coro)


# ---- RAG の分野 news ----

def test_items_go_to_the_news_shard_as_public_and_show_in_cross_search():
    out = run(rag.ingest_items([item("https://a.test/1", "Qwen の新モデル", summary="量子化が速い"), item("https://a.test/2", "ROCm 7 が出た")],
                               {"https://a.test/1": ["ローカル LLM"]}))
    assert out["added"] == 2 and out["embedded"] is True
    ref = kstore.find_collection("news")
    assert (ref.shard, ref.kind, ref.sensitivity) == ("news", "news", "public")
    assert kstore.shard_path("news").name == "news.sqlite3" and kstore.shard_path("news").parent.name == "knowledge"
    # 横断検索（クラウド向け＝公開だけ）にも出る
    hits = kstore.search_all("Qwen", consumer="cloud", query_vector=_vec("Qwen"), model="fake-embed")
    assert hits and hits[0]["collection"] == "news" and hits[0]["sensitivity"] == "public" and hits[0]["url"] == "https://a.test/1"
    assert "ローカル LLM" in hits[0]["text"]                                # チャンネル名も文書に入る


def test_same_url_is_updated_not_duplicated_and_unchanged_is_skipped():
    run(rag.ingest_items([item("https://a.test/1", "Qwen の新モデル")], {}))
    again = run(rag.ingest_items([item("https://a.test/1", "Qwen の新モデル")], {}))
    assert again["added"] == 0 and again["unchanged"] == 1
    run(rag.ingest_items([item("https://a.test/1", "Qwen の新モデルが更新された")], {}))
    docs = kstore.list_documents("news")
    assert len(docs) == 1 and "更新された" in docs[0]["title"]


def test_events_are_never_put_in_rag():
    out = run(rag.ingest_items([item("/settings?news-event=1-2", "警報", kind="event")], {}))
    assert out["added"] == 0 and kstore.find_collection("news") is None


def test_fulltext_is_read_only_when_the_setting_is_on(monkeypatch):
    from app.workflows import research_tools

    calls = []

    async def fake_read(url, focus="", max_chars=6000, **kw):
        calls.append(url)
        return {"ok": True, "text": "本文の段落 Qwen がとても速い。"}

    monkeypatch.setattr(research_tools, "read_page", fake_read)
    run(rag.ingest_items([item("https://a.test/1", "題")], {}, fulltext=False))
    assert calls == []
    run(rag.ingest_items([item("https://a.test/2", "別の題")], {}, fulltext=True))
    assert calls == ["https://a.test/2"]
    body = kstore.search(["news"], "とても速い", mode="fulltext")
    assert body and "本文の段落" in body[0]["text"]


def test_fulltext_stops_when_external_reading_is_blocked(monkeypatch):
    from app.security.offline import ExternalResearchBlocked
    from app.workflows import research_tools

    calls = []

    async def blocked(url, **kw):
        calls.append(url)
        raise ExternalResearchBlocked("止まっています")

    monkeypatch.setattr(research_tools, "read_page", blocked)
    out = run(rag.ingest_items([item(f"https://a.test/{i}", f"題 {i}") for i in range(3)], {}, fulltext=True))
    assert out["added"] == 3 and len(calls) == 1             # 1 回断られたら、以後の本文は読まない（題と抜粋だけで登録）


def test_prune_keeps_rag_by_default_and_removes_it_when_set(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(days=5)
    for i in range(3):
        add(f"https://p.test/{i}", title=f"Qwen {i}", published=old + timedelta(hours=i))
    with news_db.session() as db:
        rows = [store.item_dict(r) for r in db.scalars(select(NewsItem))]
    run(rag.ingest_items(rows, {}))
    assert len(kstore.list_documents("news")) == 3
    # 既定（残す）: ニュースの DB からは消えるが RAG は残る
    saved = service._save([], "keep")
    assert saved["removed_urls"] == []
    with news_db.session() as db:
        removed: list[str] = []
        assert store.prune(db, 1, removed) == 2 and len(removed) == 2
    assert len(kstore.list_documents("news")) == 3
    assert rag.delete_urls(removed) == 2 and len(kstore.list_documents("news")) == 1


def test_refresh_ingests_new_items_and_reports(monkeypatch, tmp_path):
    published = datetime.now(timezone.utc)

    async def fetch():
        return [sources.make_item(kind="news", source="Fake", source_key="rss:fake", title="Qwen が速い", url="https://f.test/1",
                                  summary="抜粋", published=published)]

    monkeypatch.setattr(sources, "build_sources", lambda cfg, ch=None: [sources.Source("rss:fake", "Fake", "news", fetch)])
    out = run(service.refresh(force=True))
    assert out["new"] == 1 and out["after"]["rag"]["added"] == 1
    assert service.status()["last"]["rag"]["added"] == 1
    assert [d["ext_id"] for d in kstore.list_documents("news")] == ["https://f.test/1"]


def test_rag_failure_does_not_fail_the_fetch(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("埋め込みの故障")

    monkeypatch.setattr(rag, "ingest_items", boom)
    saved = {"changed": [item("https://a.test/1", "題")], "removed_urls": []}
    info = run(service._after_save(saved, settings.load()))
    assert "RuntimeError" in info["rag"]["error"]


def test_semantic_search_endpoint_marks_archived_items(admin_client):
    keep = add("https://s.test/keep", title="Qwen の話", published=datetime.now(timezone.utc))
    with news_db.session() as db:
        rows = [store.item_dict(r) for r in db.scalars(select(NewsItem))]
    gone = item("https://s.test/gone", "Qwen の古い記事")
    run(rag.ingest_items(rows + [gone], {}))
    r = admin_client.get("/api/v1/news/semantic", params={"q": "Qwen"})
    assert r.status_code == 200
    body = r.json()
    by_url = {i["url"]: i for i in body["items"]}
    assert body["semantic"] is True
    assert by_url["https://s.test/keep"]["id"] == keep and "archived" not in by_url["https://s.test/keep"]
    assert by_url["https://s.test/gone"]["archived"] is True and by_url["https://s.test/gone"]["id"] == 0


def test_knowledge_find_scope_accepts_news(admin_client):
    run(rag.ingest_items([item("https://a.test/1", "Qwen の新モデル")], {}))
    r = admin_client.post("/api/v1/knowledge/find", json={"query": "Qwen", "scope": "news"}, headers={"X-Requested-With": "ControlDeck"})
    assert r.status_code == 200 and r.json()["results"] and r.json()["results"][0]["collection"] == "news"


# ---- LLM の選択と外部送信の許可 ----

def configure_openrouter(models, *, egress):
    cloud.save_provider("openrouter", {"api_key": "-".join(["sk", "or", "v1", "secretvalue1234"])})
    cloud.save_provider("openrouter", {"enabled": True, "models": models})
    if egress:
        cloud.save_egress({"news": True})


def test_news_is_a_cloud_caller_shown_in_the_egress_screen(admin_client):
    assert "news" in cloud.CALLERS and cloud.CALLER_LABELS["news"] == "ニュース（公開情報だけ）"
    r = admin_client.get("/api/v1/models/cloud")
    assert r.status_code == 200 and r.json()["egress"]["news"] is False and r.json()["caller_labels"]["news"] == "ニュース（公開情報だけ）"
    assert cloud.save_egress({"news": True})["egress"]["news"] is True


def test_options_put_free_models_first_and_mark_paid():
    configure_openrouter(["anthropic/claude-x", "meta/llama:free", "acme/zero-price", "acme/aaa:free"], egress=False)
    out = llm.options([{"id": "auto", "name": "auto"}], frozenset({"acme/zero-price"}))
    names = [(r["id"].split("/", 1)[1], r["free"], r["paid"]) for r in out["cloud"]]
    assert [n[0] for n in names] == ["acme/aaa:free", "acme/zero-price", "meta/llama:free", "anthropic/claude-x"]
    assert [r["free"] for r in out["cloud"]] == [True, True, True, False]            # 無料が先
    assert out["cloud"][-1]["id"].endswith("anthropic/claude-x") and out["cloud"][-1]["paid"] is True
    assert out["egress"] is False and out["local"] == [{"id": "auto", "name": "auto"}]


def test_cloud_model_without_permission_is_not_allowed_and_never_falls_back_to_local():
    configure_openrouter(["meta/llama:free"], egress=False)
    with pytest.raises(llm.LlmNotAllowed, match="外部への送信が許可されていません"):
        run(llm.complete("cloud:openrouter/meta/llama:free", [{"role": "user", "content": "x"}]))
    configure_openrouter(["meta/llama:free"], egress=True)
    llm.check_allowed("cloud:openrouter/meta/llama:free")                           # 許可があれば通る
    with pytest.raises(llm.LlmNotAllowed):
        llm.check_allowed("cloud:openrouter/other/model")                             # 選ばれていないモデルは通さない
    llm.check_allowed("auto")                                                         # ローカルは許可の対象外


def test_cloud_call_goes_through_the_gateway_as_the_news_caller(monkeypatch):
    configure_openrouter(["meta/llama:free"], egress=True)
    seen = {}

    class FakeProvider:
        async def complete(self, request):
            seen.update(client=request.client, model=request.model, base=request.base_url, local_only=request.local_only)
            return "答え"

    from app.models_mgmt import runtime_provider

    monkeypatch.setattr(runtime_provider, "provider_for_request", lambda request: FakeProvider())
    assert run(llm.complete("cloud:openrouter/meta/llama:free", [{"role": "user", "content": "x"}])) == "答え"
    assert seen["client"] == "news" and seen["model"] == "cloud:openrouter/meta/llama:free" and "/v1" in seen["base"]


def test_llm_options_endpoint(admin_client, monkeypatch):
    from app.news import router as news_router

    configure_openrouter(["meta/llama:free", "anthropic/claude-x"], egress=True)

    async def fake_local(user):
        return [{"id": "auto", "name": "auto"}, {"id": "qwen-local", "name": "Qwen ローカル"}]

    async def no_remote():
        return frozenset()

    monkeypatch.setattr(news_router, "_local_models", fake_local)
    monkeypatch.setattr(news_router, "_free_model_ids", no_remote)
    body = admin_client.get("/api/v1/news/llm-options").json()
    assert [m["id"] for m in body["local"]] == ["auto", "qwen-local"] and body["egress"] is True
    assert [(m["id"], m["free"]) for m in body["cloud"]] == [("cloud:openrouter/meta/llama:free", True), ("cloud:openrouter/anthropic/claude-x", False)]


# ---- まとめ ----

def seed_articles(now=None):
    now = now or datetime.now(timezone.utc)
    add("https://d.test/1", title="Qwen の新モデルが出た", summary="量子化に対応", published=now - timedelta(hours=3), topics=["Qwen"])
    add("https://d.test/2", title="llama.cpp が高速化", summary="ROCm 対応", published=now - timedelta(hours=5), topics=["llama.cpp"])
    add("https://d.test/3", title="先月の記事 Qwen", published=now - timedelta(days=20), topics=["Qwen"])
    me = user_id("digest-user")
    add(f"/settings?news-event={me}-1", title="秘密の警報", kind="event", source="ControlDeck", uid=me, published=now - timedelta(hours=1))


class Spy:
    def __init__(self, answer="## ローカル LLM\n- Qwen の新モデルが出た。[1][9]\n"):
        self.calls, self.answer = [], answer

    async def __call__(self, model, messages, **kw):
        self.calls.append((model, messages))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_digest_with_llm_cites_only_real_items_and_excludes_events_and_old_items(monkeypatch):
    seed_articles()
    spy = Spy()
    monkeypatch.setattr(llm, "complete", spy)
    row = run(digest.generate("daily", "2026-11-23"))
    assert row["fallback"] is False and row["error"] == "" and row["model"] == "auto"
    assert "[1]" in row["body"] and "[9]" not in row["body"]                          # 存在しない番号の出典は消す
    sent = "\n".join(m["content"] for m in spy.calls[0][1])
    assert "Qwen の新モデルが出た" in sent and "llama.cpp が高速化" in sent
    assert "秘密の警報" not in sent and "先月の記事" not in sent                        # 出来事・期間外は渡さない
    assert len(row["item_ids"]) == 2


def test_digest_cloud_without_permission_makes_nothing_and_does_not_use_local(monkeypatch):
    seed_articles()
    cfg = settings.load()
    cfg["llm"]["digest_model"] = "cloud:openrouter/meta/llama:free"
    cfg["digest_weekly"]["enabled"] = False
    settings.save(cfg)
    configure_openrouter(["meta/llama:free"], egress=False)
    called = []

    from app.models_mgmt import runtime_provider

    monkeypatch.setattr(runtime_provider, "provider_for_request", lambda r: called.append(r) or None)
    notified = []
    monkeypatch.setattr(events, "notify_event", lambda *a, **k: notified.append(k))
    row = run(digest.generate("daily", "2026-11-23"))
    assert row["body"] == "" and "外部への送信が許可されていません" in row["error"] and row["fallback"] is False
    assert called == [] and notified == []                                            # LLM を呼ばない・ローカルへ落とさない・通知しない
    # 見張りは「作った」と数える（毎分やり直さない）
    assert run(digest.run_due(datetime(2026, 11, 23, 23, 0))) == []


def test_cloud_digest_sends_only_public_article_fields(monkeypatch):
    seed_articles()
    cfg = settings.load()
    cfg["llm"]["digest_model"] = "cloud:openrouter/meta/llama:free"
    settings.save(cfg)
    configure_openrouter(["meta/llama:free"], egress=True)
    from app.database import SessionLocal
    from app.models import Conversation

    me = user_id("digest-user")
    with SessionLocal() as db:
        db.add(Conversation(id="newstest-digest", title="未公開の発明の相談", owner_user_id=me, updated_at=datetime.now(timezone.utc)))
        db.commit()
    spy = Spy()
    monkeypatch.setattr(llm, "complete", spy)
    try:
        run(digest.generate("daily", "2026-11-23"))
    finally:
        with SessionLocal() as db:
            db.execute(delete(Conversation).where(Conversation.id == "newstest-digest"))
            db.commit()
    model, messages = spy.calls[0]
    text = "\n".join(m["content"] for m in messages)
    assert model.startswith("cloud:") and "未公開の発明" not in text and "秘密の警報" not in text and "digest-user" not in text


def test_digest_falls_back_to_marked_headlines_when_llm_fails(monkeypatch):
    seed_articles()
    monkeypatch.setattr(llm, "complete", Spy(llm.LlmUnavailable("接続できません")))
    row = run(digest.generate("daily", "2026-11-23"))
    assert row["fallback"] is True and "接続できません" in row["error"]
    assert "- Qwen の新モデルが出た" in row["body"] and "llama.cpp が高速化" in row["body"] and "[1]" in row["body"]


def test_digest_notifies_when_made_and_regenerate_replaces(monkeypatch):
    seed_articles()
    monkeypatch.setattr(llm, "complete", Spy())
    sent = []
    monkeypatch.setattr(events, "notify_event", lambda kind, **k: sent.append((kind, k)))
    run(digest.generate("daily", "2026-11-23"))
    run(digest.generate("daily", "2026-11-23"))
    assert [k for k, _ in sent] == ["news_digest", "news_digest"]
    assert "news_digest" in events.KIND_BY_ID and events.KIND_BY_ID["news_digest"].default is True and events.KIND_BY_ID["news_digest"].label == "ニュースのまとめ"
    with news_db.session() as db:
        assert len(db.scalars(select(NewsDigest)).all()) == 1


def test_digest_api_lists_and_returns_sources(admin_client, monkeypatch):
    seed_articles()
    monkeypatch.setattr(llm, "complete", Spy())
    monkeypatch.setattr(events, "notify_event", lambda *a, **k: None)
    r = admin_client.post("/api/v1/news/digests/run", json={"period": "daily"}, headers={"X-Requested-With": "ControlDeck"})
    assert r.status_code == 200 and r.json()["period"] == "daily"
    listing = admin_client.get("/api/v1/news/digests").json()
    assert listing["latest"]["daily"]["label"] == "今朝のまとめ" and listing["latest"]["weekly"] is None
    detail = admin_client.get(f"/api/v1/news/digests/{listing['digests'][0]['id']}").json()
    assert {s["url"] for s in detail["sources"]} == {"https://d.test/1", "https://d.test/2"} and detail["sources"][0]["n"] == 1
    assert admin_client.get("/api/v1/news/digests/99999").status_code == 404
    assert admin_client.post("/api/v1/news/digests/run", json={"period": "monthly"}, headers={"X-Requested-With": "ControlDeck"}).status_code == 422


# ---- 一言要約と話題の語 ----

def test_gist_is_off_by_default_and_adds_gist_and_topics_when_on(monkeypatch):
    first = add("https://g.test/1", title="Qwen の新モデル", published=datetime.now(timezone.utc))
    rows = [store.item_dict(r) for r in news_db.session().scalars(select(NewsItem))]
    spy = Spy("1|新しいモデルが出た|Qwen, 量子化\n9|範囲外|X")
    monkeypatch.setattr(llm, "complete", spy)
    assert run(gist.enrich(rows)) == {"done": 0, "error": ""} and spy.calls == []        # 既定は切
    cfg = settings.load()
    cfg["llm"]["gist_enabled"] = True
    settings.save(cfg)
    assert run(gist.enrich(rows))["done"] == 1
    with news_db.session() as db:
        row = db.get(NewsItem, first)
        assert row.gist == "新しいモデルが出た" and "量子化" in row.topics
    assert "Qwen の新モデル" in spy.calls[0][1][1]["content"]


def test_gist_with_cloud_model_but_no_permission_does_nothing(monkeypatch):
    add("https://g.test/1", title="題", published=datetime.now(timezone.utc))
    rows = [store.item_dict(r) for r in news_db.session().scalars(select(NewsItem))]
    cfg = settings.load()
    cfg["llm"].update(gist_enabled=True, gist_model="cloud:openrouter/meta/llama:free")
    settings.save(cfg)
    configure_openrouter(["meta/llama:free"], egress=False)
    out = run(gist.enrich(rows))
    assert out["done"] == 0 and "許可されていません" in out["error"]


# ---- 更新のタイミング ----

def at(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute)          # 2026-11-23 は月曜


def test_settings_defaults_and_validation(admin_client):
    body = admin_client.get("/api/v1/news/settings").json()
    cfg = body["settings"]
    assert cfg["digest_daily"] == {"enabled": True, "time": "06:30"} and cfg["digest_weekly"] == {"enabled": True, "weekday": 0, "time": "07:00"}
    assert cfg["rag"] == {"enabled": True, "fulltext": False, "on_prune": "keep"} and cfg["llm"]["gist_enabled"] is False
    assert body["weekdays"][0] == "月" and body["interval_minutes"] == 30
    h = {"X-Requested-With": "ControlDeck"}
    for bad in ({"digest_daily": {"time": "6:30"}}, {"digest_weekly": {"weekday": 7}}, {"feed_refresh_minutes": 0},
                {"rag": {"on_prune": "maybe"}}, {"llm": {"digest_model": "bad model!"}}, {"quiet": {"start": "25:00"}}):
        assert admin_client.put("/api/v1/news/settings", json=bad, headers=h).status_code == 422, bad
    ok = admin_client.put("/api/v1/news/settings", json={"feed_refresh_minutes": 5, "digest_daily": {"enabled": False, "time": "05:00"},
                                                          "rag": {"fulltext": True, "on_prune": "delete"}}, headers=h)
    assert ok.status_code == 200 and ok.json()["settings"]["feed_refresh_minutes"] == 5 and ok.json()["settings"]["rag"]["fulltext"] is True


def test_daily_digest_is_due_after_the_time_only():
    cfg = settings.normalize({"digest_weekly": {"enabled": False}})
    assert settings.due_digests(cfg, at(23, 6, 29)) == []
    assert settings.due_digests(cfg, at(23, 6, 30)) == [("daily", "2026-11-23")]
    assert settings.due_digests(cfg, at(23, 22)) == [("daily", "2026-11-23")]         # 寝ていた後でも作る
    off = settings.normalize({"digest_daily": {"enabled": False}, "digest_weekly": {"enabled": False}})
    assert settings.due_digests(off, at(23, 12)) == []


def test_weekly_digest_follows_weekday_and_time():
    cfg = settings.normalize({"digest_daily": {"enabled": False}})                       # 月曜 07:00
    assert settings.due_digests(cfg, at(23, 6, 59)) == [("weekly", "2026-11-16")]       # 月曜の朝、まだ 7 時前: 先週の予定
    assert settings.due_digests(cfg, at(23, 7, 0)) == [("weekly", "2026-11-23")]
    assert settings.due_digests(cfg, at(25, 9)) == [("weekly", "2026-11-23")]           # 水曜でも今週の月曜の分
    fri = settings.normalize({"digest_daily": {"enabled": False}, "digest_weekly": {"weekday": 4, "time": "18:00"}})
    assert settings.due_digests(fri, at(23, 12)) == [("weekly", "2026-11-20")]
    assert settings.due_digests(fri, at(27, 18, 0)) == [("weekly", "2026-11-27")]


def test_quiet_hours_including_past_midnight():
    cfg = settings.normalize({"quiet": {"enabled": True, "start": "22:00", "end": "06:00"}})
    assert settings.in_quiet(cfg, at(23, 23)) and settings.in_quiet(cfg, at(23, 5, 59)) and not settings.in_quiet(cfg, at(23, 6, 0)) and not settings.in_quiet(cfg, at(23, 12))
    assert not settings.in_quiet(settings.normalize({}), at(23, 3))                        # 既定は切
    day = settings.normalize({"quiet": {"enabled": True, "start": "01:00", "end": "05:00"}})
    assert settings.in_quiet(day, at(23, 1, 0)) and not settings.in_quiet(day, at(23, 5, 0))


def test_run_due_makes_each_missing_digest_once(monkeypatch):
    seed_articles()
    spy = Spy()
    monkeypatch.setattr(llm, "complete", spy)
    monkeypatch.setattr(events, "notify_event", lambda *a, **k: None)
    now = datetime(2026, 11, 23, 8, 0)
    made = run(digest.run_due(now))
    assert sorted(r["period"] for r in made) == ["daily", "weekly"]
    assert run(digest.run_due(now)) == [] and len(spy.calls) == 2                          # 2 回目は作らない


def test_news_loop_skips_fetch_in_quiet_hours(monkeypatch):
    calls = []

    async def fake_refresh(force=False):
        calls.append("refresh")
        return {"started": True, "new": 0, "failed": 0}

    async def fake_due(now=None):
        calls.append("due")
        return []

    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        if seconds:
            raise asyncio.CancelledError      # 1 周したら止める（最初の待ち FIRST_DELAY は 0）
        await real_sleep(0)

    monkeypatch.setenv("CONTROL_DECK_NEWS_AUTO", "1")
    monkeypatch.setattr(service, "refresh", fake_refresh)
    monkeypatch.setattr(digest, "run_due", fake_due)
    monkeypatch.setattr(service, "FIRST_DELAY_SECONDS", 0)
    cfg = settings.normalize({"quiet": {"enabled": True, "start": "00:00", "end": "23:59"}})
    monkeypatch.setattr(settings, "load", lambda: cfg)
    monkeypatch.setattr(service.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        run(service.news_loop())
    assert calls == ["due"]                                                                # 取得はせず、まとめの確認だけ


def test_news_settings_reject_paid_cloud_models():
    """目的: ニュースのまとめ・一言要約に有料のクラウドのモデルを選べないこと。

    根拠: 2026-11-23 利用者「ニュースで有料なのは使わない方針で」。無料（:free・価格 0）とローカルは通す。
    """
    import pytest

    from app.news import llm

    llm.reject_paid({"llm": {"digest_model": "cloud:openrouter/meta/llama-3.3-70b:free", "gist_model": "auto"}})
    llm.reject_paid({"llm": {"digest_model": "cloud:openrouter/vendor/zero-priced"}}, frozenset({"vendor/zero-priced"}))
    with pytest.raises(ValueError, match="有料"):
        llm.reject_paid({"llm": {"digest_model": "cloud:openrouter/anthropic/claude-x"}})
    with pytest.raises(ValueError, match="一言要約"):
        llm.reject_paid({"llm": {"digest_model": "auto", "gist_model": "cloud:openrouter/anthropic/claude-x"}})
