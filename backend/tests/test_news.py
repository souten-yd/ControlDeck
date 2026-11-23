"""目的: ニュースの専門ページ（app/news）の試験。HTTP は全部モック（外へ通信しない）。

根拠: 2026-11-23 利用者「ニュース機能もつけて…私専門のニュースチャンネルを開設して。よく開くニュースとか傾向を把握してカスタマイズして」
「過去のニュースはアーカイブにして、いつでも閲覧できるようにすること」。
守ること: ソースの整形（外から来た文は平文・url は http/https だけ）・同じ url は更新だけ・1 つのソースの失敗が他を止めない・
event はその人宛てだけ（秘密の会話の題は伏せたまま）・好みの重み（秘密の会話は使わない）・50,000 件の切り捨てで star が残る・移行の up/down。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.news import db as news_db
from app.models import Conversation, User
from app.news.db import NewsAction, NewsItem
from app.news import profile, service, sources, store
from app.news.topics import extract_topics
from app.security.passwords import hash_password
from tests.conftest import CSRF_HEADERS

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_news(client, tmp_path, monkeypatch):
    """記事・操作・チャットの題は試験ごとに空から。設定・外した語のファイルも試験ごとの場所へ。"""
    monkeypatch.setattr(sources, "_path", lambda: tmp_path / "news" / "sources.json")
    monkeypatch.setattr(profile, "_muted_path", lambda: tmp_path / "news" / "muted.json")
    with news_db.session() as db:
        db.execute(delete(NewsAction))
        db.execute(delete(NewsItem))
        db.commit()
    with SessionLocal() as db:
        db.execute(delete(Conversation).where(Conversation.id.like("newstest%")))
        db.commit()
    service._status.clear()
    service._last.update(started=0.0, finished=None, new=0)
    yield


def user_id(name: str, role: str = "viewer") -> int:
    from app.models import Role

    with SessionLocal() as db:
        row = db.scalar(select(User).where(User.username == name))
        if row is None:
            role_id = db.scalar(select(Role.id).where(Role.name == role))
            row = User(username=name, password_hash=hash_password("-".join(["news", "test", "password"])), role_id=role_id)
            db.add(row)
            db.commit()
        return row.id


def login(client, name: str, role: str = "viewer"):
    user_id(name, role)
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"username": name, "password": "-".join(["news", "test", "password"])}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    return client


def add(url: str, *, title="記事", kind="news", source="S", source_key="rss:s", topics=(), published=NOW, summary="", uid=None, image=None) -> int:
    with news_db.session() as db:
        row = NewsItem(kind=kind, source=source, source_key=source_key, title=title, url=url, summary=summary, image_url=image,
                       published_at=published, fetched_at=NOW, topics=json.dumps(list(topics), ensure_ascii=False), user_id=uid)
        db.add(row)
        db.commit()
        return row.id


def act(uid: int, item_id: int, action: str, at=NOW):
    with news_db.session() as db:
        db.add(NewsAction(user_id=uid, item_id=item_id, action=action, at=at))
        db.commit()


# ---- 話題の語 ----

def test_topics_use_dictionary_proper_nouns_and_skip_versions():
    got = extract_topics("Qwen3.6 が llama.cpp に対応、GLM-5.3 と R9700 のベンチ", "Radeon のドライバと LiteLLM")
    assert got[:4] == ["Qwen", "llama.cpp", "GLM", "R9700"] and "Radeon" in got
    assert "v1.2.3" not in extract_topics("llama.cpp b6789 v1.2.3 released")
    assert "ニュース" not in extract_topics("ニュース アップデート")
    assert "ジェミニ" in extract_topics("新しいジェミニが出た")
    assert extract_topics("Mesa 26.1 released", "", ["llama.cpp"])[0] == "llama.cpp"      # 出どころ由来の語は先頭
    assert len(extract_topics("Qwen Llama Gemma DeepSeek Mistral GLM Kimi MiniMax")) == 6   # 上限


# ---- ソースの整形 ----

def test_release_items_for_github_and_codeberg_skip_drafts_and_flatten_markdown():
    rows = [{"name": "b9000", "tag_name": "b9000", "html_url": "https://github.com/ggml-org/llama.cpp/releases/tag/b9000",
             "published_at": "2026-11-22T10:00:00Z", "body": "## 変更\n* **fast** [docs](https://x.test) <script>x()</script>"},
            {"name": "draft", "draft": True, "html_url": "https://github.com/a/b/releases/tag/d"},
            {"name": "bad", "html_url": "javascript:alert(1)"}]
    got = (sources.release_items(rows, host="GitHub", repo="ggml-org/llama.cpp"))
    assert len(got) == 1
    assert got[0]["kind"] == "release" and got[0]["source_key"] == "github:ggml-org/llama.cpp" and got[0]["title"] == "llama.cpp b9000"
    assert "<" not in got[0]["summary"] and "fast docs" in got[0]["summary"] and "https" not in got[0]["summary"]
    assert got[0]["extra_topics"] == ("llama.cpp",)
    cb = sources.release_items([{"name": "v1", "tag_name": "v1", "html_url": "https://codeberg.org/StillDeadcode/radiance/releases/tag/v1",
                                 "created_at": "2026-11-20T00:00:00+09:00", "prerelease": True}], host="Codeberg", repo="StillDeadcode/radiance")
    assert cb[0]["source_key"] == "codeberg:StillDeadcode/radiance" and cb[0]["title"].endswith("(pre-release)")


RSS = b"""<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Item A &amp; B</title><link>https://example.com/a</link><pubDate>Sun, 22 Nov 2026 09:00:00 +0000</pubDate>
<description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description><media:thumbnail url="https://img.example.com/a.jpg"/></item>
<item><title>Item B</title><link>https://example.com/b</link><enclosure url="https://img.example.com/b.png" type="image/png" length="1"/></item>
<item><title>Item C</title><link>https://example.com/c</link><description>&lt;img src="/c.webp"&gt; text</description></item>
<item><title>Item D</title><link>ftp://example.com/d</link></item>
<item><title>Item E</title><link>https://example.com/e</link><enclosure url="javascript:alert(1)" type="image/png"/></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom 1</title>
<link rel="alternate" href="https://example.com/atom1"/><link rel="enclosure" type="image/jpeg" href="https://img.example.com/at.jpg"/>
<updated>2026-11-21T00:00:00Z</updated><summary>Sum</summary></entry></feed>"""


def test_rss_and_atom_pick_images_and_drop_unsafe_links():
    got = sources.parse_feed(RSS, url="https://example.com/feed", name="Ex")
    by = {i["url"]: i for i in got}
    assert set(by) == {"https://example.com/a", "https://example.com/b", "https://example.com/c", "https://example.com/e"}   # ftp は捨てる
    assert by["https://example.com/a"]["title"] == "Item A & B" and by["https://example.com/a"]["summary"] == "Hello world"
    assert by["https://example.com/a"]["image_url"] == "https://img.example.com/a.jpg"
    assert by["https://example.com/b"]["image_url"] == "https://img.example.com/b.png"
    assert by["https://example.com/c"]["image_url"] == "https://example.com/c.webp"          # 本文の最初の <img>（相対は絶対に）
    assert by["https://example.com/e"]["image_url"] is None                                    # javascript: の画像は捨てる
    assert by["https://example.com/a"]["published_at"] == datetime(2026, 11, 22, 9, tzinfo=timezone.utc)
    atom = sources.parse_feed(ATOM, url="https://example.com/atom", name="At")
    assert atom[0]["url"] == "https://example.com/atom1" and atom[0]["image_url"] == "https://img.example.com/at.jpg"


def test_hn_hf_discourse_reddit_shapes():
    hn = sources.hn_items({"hits": [{"objectID": "1", "title": "Story", "url": "https://s.test/x", "points": 99, "num_comments": 7, "created_at_i": 1795000000},
                                    {"objectID": "2", "title": "Ask HN", "url": None, "points": 1, "num_comments": 0, "created_at_i": 1795000000},
                                    {"objectID": "3", "title": "x", "url": "data:text/html,x", "created_at_i": 1795000000}]})
    assert [i["url"] for i in hn] == ["https://s.test/x", "https://news.ycombinator.com/item?id=2"] and "99 点" in hn[0]["summary"]
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    hf = sources.hf_items([{"id": "unsloth/Qwen-GGUF", "url": "https://huggingface.co/unsloth/Qwen-GGUF", "created": recent, "downloads": 12345, "likes": 9, "pipeline": "text-generation", "params_b": 7.0},
                           {"id": "old/Model", "url": "https://huggingface.co/old/Model", "created": "2020-01-01"}])
    assert [i["source_key"] for i in hf] == ["hf"] and hf[0]["kind"] == "model" and "12,345" in hf[0]["summary"]
    dc = sources.discourse_items({"topic_list": {"topics": [
        {"id": 5, "title": "Thread", "slug": "thread", "created_at": "2026-11-22T00:00:00Z", "image_url": "https://f.test/i.png", "excerpt": "<b>hi</b>"},
        {"id": 6, "title": "Pinned", "slug": "p", "pinned": True}]}}, "forum.example.com")
    assert len(dc) == 1 and dc[0]["url"] == "https://forum.example.com/t/thread/5" and dc[0]["summary"] == "hi" and dc[0]["kind"] == "forum"
    rd = sources.reddit_items({"data": {"children": [
        {"data": {"title": "Post", "permalink": "/r/LocalLLaMA/comments/x/post/", "created_utc": 1795000000, "score": 10, "num_comments": 2, "selftext": "body",
                  "preview": {"images": [{"source": {"url": "https://preview.redd.it/a.jpg?x=1&y=2"}}]}}},
        {"data": {"title": "nsfw", "permalink": "/r/a/comments/y/", "over_18": True}}]}}, "LocalLLaMA")
    assert len(rd) == 1 and rd[0]["kind"] == "reddit" and rd[0]["image_url"].startswith("https://preview.redd.it/") and rd[0]["url"].startswith("https://www.reddit.com/r/")


def test_reddit_is_fetched_only_with_keys(monkeypatch):
    from app.workflows import research_tools as rt

    async def none(path, params):
        return None

    monkeypatch.setattr(rt, "_reddit_oauth", none)
    with pytest.raises(sources.SkippedSource):
        asyncio.run(sources.fetch_reddit("LocalLLaMA"))

    async def some(path, params):
        assert path == "/r/LocalLLaMA/hot"
        return {"data": {"children": [{"data": {"title": "T", "permalink": "/r/LocalLLaMA/comments/1/t/", "created_utc": 1795000000}}]}}

    monkeypatch.setattr(rt, "_reddit_oauth", some)
    assert len(asyncio.run(sources.fetch_reddit("LocalLLaMA"))) == 1


def test_non_http_urls_and_image_urls_are_dropped():
    assert sources.make_item(kind="news", source="s", source_key="k", title="t", url="javascript:alert(1)") is None
    assert sources.make_item(kind="news", source="s", source_key="k", title="", url="https://a.test/") is None
    item = sources.make_item(kind="news", source="s", source_key="k", title="<b>t</b>", url="https://a.test/", image="file:///etc/passwd",
                             summary="x" * 1000)
    assert item["title"] == "t" and item["image_url"] is None and len(item["summary"]) <= sources.SUMMARY_CHARS


# ---- 設定 ----

def test_config_defaults_and_validation():
    cfg = sources.load_config()
    assert cfg["github"] == ["Niko1221/Strata", "ggml-org/llama.cpp", "sst/opencode"]
    assert cfg["codeberg"] == ["StillDeadcode/radiance"] and cfg["hackernews"] and cfg["reddit"] == ["LocalLLaMA"]
    keys = [s.key for s in sources.build_sources(cfg)]
    assert "github:ggml-org/llama.cpp" in keys and "codeberg:StillDeadcode/radiance" in keys and "hn" in keys and "hf" in keys
    assert "rss:https://gigazine.net/news/rss_2.0/" in keys and "discourse:forum.level1techs.com" in keys and "reddit:LocalLLaMA" in keys
    off = sources.build_sources({**cfg, "enabled": {**cfg["enabled"], "release": False, "reddit": False}})
    assert not [s for s in off if s.kind in ("release", "reddit")]
    for bad in ({"github": ["not a repo"]}, {"rss": [{"name": "x", "url": "http://a.test/feed"}]}, {"rss": [{"url": "https://u:p@a.test/"}]},
                {"discourse": ["127.0.0.1"]}, {"reddit": ["a b"]}, {"github": [f"a/b{i}" for i in range(sources.MAX_PER_LIST + 1)]}, {"rss": "x"}, []):
        with pytest.raises(sources.ConfigError):
            sources.normalize(bad)
    saved = sources.save_config({"github": ["a/b", "a/b"], "reddit": ["r/Foo_1"], "rss": [{"name": "N", "url": "https://x.test/f.xml"}]})
    assert saved["github"] == ["a/b"] and saved["reddit"] == ["Foo_1"] and sources.load_config() == saved


def test_sources_api_needs_settings_manage(client):
    login(client, "news-viewer")
    assert client.get("/api/v1/news/sources").status_code == 200
    assert client.put("/api/v1/news/sources", json={"github": ["a/b"]}, headers=CSRF_HEADERS).status_code == 403
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "-".join(["test", "password", "123"])}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    ok = client.put("/api/v1/news/sources", json={"github": ["a/b"], "enabled": {"event": False}}, headers=CSRF_HEADERS)
    assert ok.status_code == 200 and ok.json()["config"]["github"] == ["a/b"] and ok.json()["config"]["enabled"]["event"] is False
    assert client.put("/api/v1/news/sources", json={"github": ["bad"]}, headers=CSRF_HEADERS).status_code == 422
    got = client.get("/api/v1/news/sources").json()
    assert got["config"]["github"] == ["a/b"] and "reddit_key" in got and "sources" in got


# ---- 取得の実行 ----

def _item(url, title="t", key="k", kind="news", published=NOW):
    return sources.make_item(kind=kind, source=key, source_key=key, title=title, url=url, published=published)


def test_same_url_updates_without_moving_published_at():
    first = datetime.now(timezone.utc) - timedelta(days=1)
    with news_db.session() as db:
        assert store.upsert_items(db, [_item("https://a.test/1", "古い題", published=first)]) ["new"] == 1
        later = sources.make_item(kind="news", source="k", source_key="k", title="新しい題 Qwen", url="https://a.test/1", published=first + timedelta(hours=3),
                                  image="https://a.test/i.png")
        assert store.upsert_items(db, [later]) ["updated"] == 1
        rows = db.scalars(select(NewsItem)).all()
    assert len(rows) == 1 and rows[0].title == "新しい題 Qwen" and rows[0].image_url == "https://a.test/i.png"
    assert abs(store.as_utc(rows[0].published_at) - first) < timedelta(seconds=1) and "Qwen" in rows[0].topics


def test_one_failing_source_does_not_stop_others(monkeypatch):
    async def good():
        return [_item("https://a.test/ok", key="good")]

    async def boom():
        raise RuntimeError("HTTP 500")

    async def slow():
        await asyncio.sleep(5)
        return []

    async def skipped():
        raise sources.SkippedSource("キーが未設定")

    monkeypatch.setattr(service, "SOURCE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(sources, "build_sources", lambda cfg, channels=None: [sources.Source("good", "Good", "news", good), sources.Source("boom", "Boom", "release", boom),
                                                              sources.Source("slow", "Slow", "forum", slow), sources.Source("skip", "Skip", "reddit", skipped)])
    result = asyncio.run(service.refresh(force=True))
    assert result["started"] and result["new"] == 1 and result["failed"] == 2
    by = {s["source_key"]: s for s in service.status()["sources"]}
    assert by["good"]["ok"] and by["good"]["count"] == 1
    assert not by["boom"]["ok"] and "HTTP 500" in by["boom"]["error"]
    assert not by["slow"]["ok"] and "以内" in by["slow"]["error"]
    assert by["skip"]["ok"] and "キー" in by["skip"]["skipped"]
    with news_db.session() as db:
        assert db.scalar(select(NewsItem.url)) == "https://a.test/ok"


def test_refresh_does_not_run_twice_at_once(monkeypatch):
    started = []

    async def slow():
        started.append(1)
        await asyncio.sleep(0.1)
        return []

    monkeypatch.setattr(sources, "build_sources", lambda cfg, channels=None: [sources.Source("s", "S", "news", slow)])

    async def both():
        return await asyncio.gather(service.refresh(force=True), service.refresh(force=True))

    first, second = asyncio.run(both())
    assert [first["started"], second["started"]].count(True) == 1 and len(started) == 1
    assert second["reason"] == "running" or first["reason"] == "running"
    assert asyncio.run(service.refresh())["reason"] == "recent"       # 直後の「取り直す」連打は断る


def test_refresh_api_runs_and_reports(admin_client, monkeypatch):
    async def good():
        return [_item("https://a.test/api", key="g")]

    monkeypatch.setattr(sources, "build_sources", lambda cfg, channels=None: [sources.Source("g", "G", "news", good)])
    r = admin_client.post("/api/v1/news/refresh", headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["new"] == 1


# ---- 出来事 ----

def test_events_are_recorded_for_recipients_only_even_when_disabled_or_quiet(client, monkeypatch):
    from app.alerts import events, webpush

    monkeypatch.setattr(webpush, "send_in_background", lambda **kw: None)
    alice, bob = user_id("news-alice"), user_id("news-bob")
    events.save_prefs(alice, kinds={"update": False})                               # 種類を無効にしていても残す
    events.local_now = lambda: datetime(2026, 11, 23, 23, 30)                       # お休み中でも残す
    result = events.notify_event("update", title="更新が終わりました", body="v1.2 に更新", url="/settings/system", user_ids=[alice])
    assert result == {"users": 0}                                                    # 通知は出ない
    with news_db.session() as db:
        rows = db.scalars(select(NewsItem).where(NewsItem.kind == "event")).all()
    assert len(rows) == 1 and rows[0].user_id == alice and rows[0].title == "更新が終わりました" and rows[0].source == "ControlDeck"
    assert "/settings/system" in rows[0].url
    login(client, "news-alice")
    mine = client.get("/api/v1/news/items?view=latest&kind=event").json()["items"]
    assert [i["title"] for i in mine] == ["更新が終わりました"] and mine[0]["url"].startswith("/settings/system")
    login(client, "news-bob")
    assert client.get("/api/v1/news/items?view=latest&kind=event").json()["items"] == []
    assert client.get("/api/v1/news/archive").json()["total"] == 0
    assert client.get("/api/v1/news/trends").json()["topics"] == []
    only = client.post(f"/api/v1/news/items/{mine[0]['id']}/action", json={"action": "open"}, headers=CSRF_HEADERS)
    assert only.status_code == 404                                                   # 他人宛ての event は見えない・触れない
    assert bob != alice


def test_event_per_permission_and_secret_chat_title_stays_hidden(monkeypatch):
    from app.alerts import chat_events, events, webpush

    monkeypatch.setattr(webpush, "send_in_background", lambda **kw: None)
    admin = user_id("admin")
    events.notify_event("alert", title="警報", body="CPU", permission="settings.manage")      # 権限で宛先を決める（購読が無くても残す）
    with news_db.session() as db:
        assert db.scalar(select(NewsItem.user_id).where(NewsItem.title == "警報")) == admin
    owner = user_id("news-owner")
    info = {"title": "特許の秘密の発明メモ", "secret": True, "owner": owner, "status": "completed", "content": "秘密の本文", "meta": {}}
    chat_events._send("code_done", "newstestsecret", info, "コード開発が終わりました", summary="秘密の要約")
    with news_db.session() as db:
        row = db.scalar(select(NewsItem).where(NewsItem.user_id == owner))
    assert row.title == "秘密の会話" and "秘密の" not in row.summary.replace("秘密の会話", "") and "特許" not in row.title + row.summary


# ---- 好み ----

def test_interest_weights_from_actions_and_chats_exclude_secret_chats():
    me, other = user_id("news-me"), user_id("news-other")
    liked = add("https://a.test/liked", topics=["Qwen"], source_key="rss:a", source="A")
    opened = add("https://a.test/opened", topics=["Ollama"], source_key="rss:b", source="B")
    bad = add("https://a.test/bad", topics=["Spam"], source_key="rss:c", source="C")
    hidden = add("https://a.test/hidden", topics=["Ads"], source_key="rss:d", source="D")
    act(me, liked, "like"); act(me, opened, "open"); act(me, bad, "dislike"); act(me, hidden, "hide")
    act(other, bad, "like")                                           # 他の人の操作は混ざらない
    with SessionLocal() as db:
        db.add_all([
            Conversation(id="newstest1", title="Radeon のドライバ", owner_user_id=me, updated_at=NOW - timedelta(days=1)),
            Conversation(id="newstest2", title="Gemma の秘密の検討", owner_user_id=me, updated_at=NOW, confidential_at=NOW),
            Conversation(id="newstest3", title="Mistral の古い話", owner_user_id=me, updated_at=NOW - timedelta(days=40)),
            Conversation(id="newstest4", title="Phoronix 他人の話 GLM", owner_user_id=other, updated_at=NOW),
        ])
        db.commit()
    with news_db.session() as db:
        found = profile.interests(db, me, NOW)
    t, s = found["topics"], found["sources"]
    assert t["Qwen"] == pytest.approx(3.0) and t["Ollama"] == pytest.approx(1.0) and t["Spam"] == pytest.approx(-3.0) and t["Ads"] == pytest.approx(-2.0)
    assert s["rss:a"] > s["rss:b"] > 0 > s["rss:c"]                    # like > open、dislike は負
    assert "Radeon" in t and 0 < t["Radeon"] < 0.5                      # チャットの題は少しだけ（1 日で少し減る）
    assert "Gemma" not in t and "Mistral" not in t and "GLM" not in t   # 秘密の会話・30 日より前・他人の会話は使わない
    old = datetime(2026, 11, 23, tzinfo=timezone.utc) + timedelta(days=30)
    with news_db.session() as db:
        assert profile.interests(db, me, old)["topics"]["Qwen"] == pytest.approx(1.5, abs=0.02)   # 30 日で半減


def test_recommended_orders_by_recency_times_interest_and_hides_hidden(client):
    me = user_id("news-reco")
    now = datetime.now(timezone.utc)
    fresh_plain = add("https://a.test/fresh", title="新しいだけ", published=now - timedelta(hours=1), topics=["Other"], source_key="rss:x")
    older_loved = add("https://a.test/older", title="好みの話題", published=now - timedelta(days=1), topics=["Qwen"], source_key="rss:y")
    much_older_loved = add("https://a.test/much", title="かなり前", published=now - timedelta(days=12), topics=["Qwen"], source_key="rss:y")
    disliked_fresh = add("https://a.test/dis", title="嫌い", published=now, topics=["Qwen"], source_key="rss:y")
    gone = add("https://a.test/gone", title="隠した", published=now, topics=["Qwen"], source_key="rss:z")
    liked_before = add("https://a.test/before", title="前に好んだ", published=now - timedelta(days=20), topics=["Qwen"], source_key="rss:y")
    for _ in range(1):
        act(me, liked_before, "like", at=now); act(me, liked_before, "star", at=now)
    act(me, disliked_fresh, "dislike", at=now); act(me, gone, "hide", at=now)
    login(client, "news-reco")
    rec = client.get("/api/v1/news/items?view=recommended").json()["items"]
    order = [i["title"] for i in rec]
    assert "隠した" not in order
    assert order.index("好みの話題") < order.index("新しいだけ")             # 1 日前でも、好みの話題は新しいだけの記事より上
    assert order.index("好みの話題") < order.index("かなり前")
    assert order.index("嫌い") > order.index("好みの話題")                    # dislike は新しくても下がる
    assert all("score" in i for i in rec)
    latest = [i["title"] for i in client.get("/api/v1/news/items?view=latest").json()["items"]]
    assert latest[0] in ("嫌い",) and "隠した" not in latest and latest.index("新しいだけ") < latest.index("好みの話題")
    assert fresh_plain and older_loved and much_older_loved


def test_profile_api_and_mute(client):
    me = user_id("news-prof")
    a = add("https://a.test/p1", topics=["Qwen", "GGUF"], source_key="rss:p", source="P")
    b = add("https://a.test/p2", topics=["Spam"], source_key="rss:q", source="Q")
    act(me, a, "star"); act(me, b, "dislike")
    login(client, "news-prof")
    prof = client.get("/api/v1/news/profile").json()
    assert {x["topic"] for x in prof["topics"]} == {"Qwen", "GGUF"}
    assert prof["sources"][0]["source"] == "P" and prof["avoided_sources"][0]["source"] == "Q"
    after = client.put("/api/v1/news/profile/mute", json={"topic": "Qwen", "muted": True}, headers=CSRF_HEADERS).json()
    assert "Qwen" not in [x["topic"] for x in after["topics"]] and after["muted"] == ["Qwen"]
    back = client.put("/api/v1/news/profile/mute", json={"topic": "Qwen", "muted": False}, headers=CSRF_HEADERS).json()
    assert "Qwen" in [x["topic"] for x in back["topics"]] and back["muted"] == []


# ---- API ----

def test_actions_toggle_and_exclusive_like_dislike(client):
    login(client, "news-act")
    item = add("https://a.test/act", topics=["Qwen"])
    def post(action, on=True):
        r = client.post(f"/api/v1/news/items/{item}/action", json={"action": action, "on": on}, headers=CSRF_HEADERS)
        assert r.status_code == 200, r.text
        return r.json()["state"]
    assert post("open")["open"] and post("like")["like"]
    s = post("dislike")
    assert s["dislike"] and not s["like"]
    assert post("star")["star"] and not post("star", False)["star"]
    assert post("hide")["hide"]
    listed = client.get("/api/v1/news/items?view=latest").json()["items"]
    assert listed == []                                                        # 隠した記事は新着に出ない
    shown = client.get(f"/api/v1/news/items?view=latest&q=記事&date=2026-11-23").json()["items"]
    assert shown and shown[0]["state"]["hide"] and shown[0]["state"]["dislike"]   # 日付・検索で探すときは出る
    assert client.post(f"/api/v1/news/items/{item}/action", json={"action": "nope"}, headers=CSRF_HEADERS).status_code == 422
    assert client.post("/api/v1/news/items/999999/action", json={"action": "open"}, headers=CSRF_HEADERS).status_code == 404
    client.cookies.clear()
    assert client.get("/api/v1/news/items").status_code == 401


def test_filters_pagination_search_archive_trends_starred(client):
    me = user_id("news-list")
    base = datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)
    ids = []
    for i in range(7):
        ids.append(add(f"https://a.test/n{i}", title=f"Qwen の話 {i}", kind="news", source="Feed", source_key="rss:f", published=base - timedelta(hours=i),
                       topics=["Qwen"], summary="本文 llama.cpp"))
    add("https://a.test/r1", title="llama.cpp b1", kind="release", source="GitHub x/y", source_key="github:x/y", published=base, topics=["llama.cpp"])
    add("https://a.test/m1", title="モデル", kind="model", source="Hugging Face", source_key="hf", published=base - timedelta(days=3), topics=["GGUF"])
    add("https://a.test/old", title="とても古い記事", published=base - timedelta(days=100), topics=["Old"])
    login(client, "news-list")
    first = client.get("/api/v1/news/items?view=latest&limit=3").json()
    assert len(first["items"]) == 3 and first["next"]
    seen = [i["id"] for i in first["items"]]
    nxt = first
    while nxt["next"]:
        nxt = client.get(f"/api/v1/news/items?view=latest&limit=3&before={nxt['next']}").json()
        seen += [i["id"] for i in nxt["items"]]
    assert len(seen) == len(set(seen)) == 10                                    # 取りこぼし・重複なし
    assert {i["kind"] for i in client.get("/api/v1/news/items?view=latest&kind=release").json()["items"]} == {"release"}
    assert len(client.get("/api/v1/news/items?view=latest&kind=release,model").json()["items"]) == 2
    assert client.get("/api/v1/news/items?kind=bogus").status_code == 422
    assert len(client.get("/api/v1/news/items?view=latest&source=rss:f").json()["items"]) == 7
    assert len(client.get("/api/v1/news/items?view=latest&source=Hugging Face").json()["items"]) == 1
    assert len(client.get("/api/v1/news/items?view=latest&topic=GGUF").json()["items"]) == 1
    assert len(client.get("/api/v1/news/items?view=latest&q=とても古い").json()["items"]) == 1         # 全文検索（3 文字以上）
    assert len(client.get("/api/v1/news/items?view=latest&q=古い").json()["items"]) == 1               # 短い語は LIKE
    assert len(client.get("/api/v1/news/items?view=latest&q=Qwen 話 3").json()["items"]) == 1            # AND（短い語を含む）
    assert len(client.get("/api/v1/news/items?view=latest&q=llama.cpp").json()["items"]) == 8         # 抜粋・題の両方
    assert client.get("/api/v1/news/items?view=latest&q=%25").json()["items"] == []                    # LIKE の記号は文字として扱う
    assert client.get("/api/v1/news/items?view=latest&q=\"").json()["items"] == []                      # FTS の記号で壊れない
    assert client.get("/api/v1/news/items?date=bad").status_code == 422
    day = client.get("/api/v1/news/items?view=latest&date=" + store.local_day(base)).json()["items"]
    assert 1 <= len(day) <= 9 and all(store.local_day(datetime.fromisoformat(i["published_at"].replace("Z", "+00:00"))) == store.local_day(base) for i in day)
    arch = client.get("/api/v1/news/archive").json()
    assert arch["total"] == 10 and arch["days"] == sorted(arch["days"], key=lambda d: d["date"], reverse=True) and sum(d["count"] for d in arch["days"]) == 10
    only = client.get("/api/v1/news/archive?kind=release").json()
    assert only["total"] == 1
    # 話題の動き: 基準日を固定して直接（API は今日が基準）
    with news_db.session() as db:
        tr = store.trends(db, me, 14, now=base)
    assert tr["days"][-1] == store.local_day(base) and len(tr["days"]) == 14
    top = tr["topics"][0]
    assert top["topic"] == "Qwen" and top["total"] == 7 and len(top["counts"]) == 14
    assert "Old" not in [t["topic"] for t in tr["topics"]] and tr["sources"][0] == {"source": "Feed", "count": 7}
    assert len(tr["topics"]) <= 8
    assert client.get("/api/v1/news/trends?days=14").status_code == 200 and client.get("/api/v1/news/trends?days=0").status_code == 422
    # star の一覧
    client.post(f"/api/v1/news/items/{ids[0]}/action", json={"action": "star"}, headers=CSRF_HEADERS)
    st = client.get("/api/v1/news/items?view=starred").json()["items"]
    assert [i["id"] for i in st] == [ids[0]] and st[0]["state"]["star"]
    assert client.get("/api/v1/news/archive").json()["starred"] == 1


def test_response_urls_are_http_or_internal_only(client):
    with news_db.session() as db:
        now = datetime.now(timezone.utc)
        db.add(NewsItem(kind="news", source="S", source_key="k", title="危ない", url="javascript:alert(1)", summary="", image_url="data:image/png;base64,AAA",
                        published_at=now, fetched_at=now, topics="[]"))
        db.add(NewsItem(kind="news", source="S", source_key="k", title="安全", url="https://ok.test/a", summary="", image_url="javascript:x",
                        published_at=now, fetched_at=now, topics="[]"))
        db.commit()
    login(client, "news-url")
    got = {i["title"]: i for i in client.get("/api/v1/news/items?view=latest").json()["items"]}
    assert got["危ない"]["url"] is None and got["危ない"]["image_url"] is None
    assert got["安全"]["url"] == "https://ok.test/a" and got["安全"]["image_url"] is None


# ---- 保存期間 ----

def test_prune_keeps_starred_and_removes_oldest(monkeypatch):
    me = user_id("news-prune")
    ids = [add(f"https://a.test/p{i}", published=NOW - timedelta(days=10 - i)) for i in range(10)]    # p0 が一番古い
    act(me, ids[0], "star")
    with news_db.session() as db:
        assert store.prune(db, 10) == 0
        assert store.prune(db, 6) == 4
        left = {r.url for r in db.scalars(select(NewsItem))}
        assert left == {"https://a.test/p0", *{f"https://a.test/p{i}" for i in range(5, 10)}}            # star と新しい 5 件
        assert db.scalar(select(NewsAction.id)) is not None
        monkeypatch.setattr(store, "MAX_ITEMS", 2)
        assert store.prune(db) == 4 and db.scalar(select(NewsItem.id).where(NewsItem.id == ids[0])) == ids[0]


# ---- 別の DB ----

def test_news_tables_live_in_their_own_file_not_the_main_db():
    from sqlalchemy import inspect as sa_inspect

    from app.database import engine as main_engine

    assert not [t for t in sa_inspect(main_engine).get_table_names() if t.startswith("news_")]
    file = news_db.path()
    assert file.name == "news.sqlite3" and file.parent.name == "news" and file.exists()
    with sqlite3.connect(file) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert {"news_items", "news_actions", "news_digests", "news_items_fts", "news_items_ai", "news_items_ad", "news_items_au"} <= names
        assert "users" not in names
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_fts_triggers_follow_insert_update_delete():
    add("https://a.test/1", title="日本語のタイトルです")
    with news_db.session() as db:
        row = db.scalar(select(NewsItem))
        row.title = "書き換えた題名です"
        db.commit()
    with sqlite3.connect(news_db.path()) as c:
        assert c.execute("SELECT rowid FROM news_items_fts WHERE news_items_fts MATCH '\"題名で\"'").fetchall() != []
        assert c.execute("SELECT rowid FROM news_items_fts WHERE news_items_fts MATCH '\"タイトル\"'").fetchall() == []
    with news_db.session() as db:
        db.execute(delete(NewsItem))
        db.commit()
    with sqlite3.connect(news_db.path()) as c:
        assert c.execute("SELECT rowid FROM news_items_fts WHERE news_items_fts MATCH '\"題名で\"'").fetchall() == []


def test_late_columns_are_added_to_an_existing_file(tmp_path, monkeypatch):
    """あとから足した列は、古いファイルを開いたとき 1 回足される（知識基盤と同じ仕組み）。"""
    from app.news import db as module

    file = tmp_path / "old" / "news" / "news.sqlite3"
    file.parent.mkdir(parents=True)
    with sqlite3.connect(file) as c:
        c.execute("CREATE TABLE news_items (id INTEGER PRIMARY KEY, kind TEXT)")
    monkeypatch.setattr(module, "path", lambda: file)
    monkeypatch.setattr(module, "_LATE_COLUMNS", [("news_items", "extra_note", "TEXT NOT NULL DEFAULT ''")])
    try:
        module.engine()
        with sqlite3.connect(file) as c:
            assert "extra_note" in {r[1] for r in c.execute("PRAGMA table_info(news_items)")}
    finally:
        module._engines.pop(str(file), None)


def test_codeberg_without_releases_reads_tags(monkeypatch):
    """目的: Release の機能を切った Codeberg のリポジトリでも、タグから更新の記事が取れること。

    根拠: 2026-11-23 本番で「Codeberg StillDeadcode/radiance」が HTTP 404 で失敗していた（has_releases が false、タグ v2.2.0 などは有る）。
    """
    import asyncio

    from app.news import sources
    from app.workflows import research_tools as rt

    async def fake_get(url, **_kw):
        if url.endswith("/releases"):
            raise rt.HttpError(404)
        assert url.endswith("/tags")
        return [{"name": "v2.2.0", "message": "radiance 2.2.0", "commit": {"created": "2026-11-19T22:29:27+01:00"}}]

    monkeypatch.setattr(rt, "_get_json", fake_get)
    items = asyncio.run(sources.fetch_codeberg("StillDeadcode/radiance"))
    assert [i["title"] for i in items] == ["radiance v2.2.0"]
    assert items[0]["url"] == "https://codeberg.org/StillDeadcode/radiance/src/tag/v2.2.0"

    async def other_error(url, **_kw):
        raise rt.HttpError(500)

    monkeypatch.setattr(rt, "_get_json", other_error)
    import pytest

    with pytest.raises(rt.HttpError):
        asyncio.run(sources.fetch_codeberg("StillDeadcode/radiance"))
