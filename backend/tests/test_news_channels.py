"""ニュースチャンネル（app/news/channels.py・home.py・prefs.py）の試験。HTTP は全部モック（外へ通信しない）。

根拠: 2026-11-23 利用者「設定で検索テーマや表示のデザインなど、ニュースを便利に使うための設定を詳細にできるように」「OZY のようなページのデザインを参考に」。
守ること: チャンネルの語での所属判定と外す語・Google ニュースの RSS の整形と URL・検索のソースの組み立て・/home の形（1 回で全区画）・
設定の検査（チャンネル・表示・好み）・好みの強さがおすすめの順位に効く・ミュートの語とソース・書き出し（JSON・CSV の式の無害化）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.database import SessionLocal
from app.models import NewsAction, NewsItem
from app.news import channels, home, prefs, profile, service, sources, store
from tests.test_news import add, act, login, user_id  # noqa: F401  (試験の道具を使い回す)

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean(client, tmp_path, monkeypatch):
    for mod, name in ((sources, "_path"), (channels, "_path"), (prefs, "_path")):
        monkeypatch.setattr(mod, name, lambda n=name, m=mod: tmp_path / "news" / f"{m.__name__.rsplit('.', 1)[-1]}.json")
    monkeypatch.setattr(profile, "_muted_path", lambda: tmp_path / "news" / "muted.json")
    with SessionLocal() as db:
        db.execute(delete(NewsAction))
        db.execute(delete(NewsItem))
        db.commit()
    yield


def ch(**kw) -> dict:
    base = {"id": "t", "name": "テスト", "color": "#f5c518", "include": ["Qwen"], "exclude": [], "kinds": [], "lang": "both", "limit": 6, "search": True}
    return {**base, **kw}


def m(channel, title, kind="news", source_key="rss:x", summary=""):
    return channels.matches(channel, kind=kind, source_key=source_key, title=title, summary=summary)


# ---- 所属の判定 ----

def test_matching_terms_exclude_kinds_and_search_origin():
    c = ch(include=["Qwen", "llama.cpp"], exclude=["広告"])
    assert m(c, "qwen の新モデル")                       # 大文字小文字は区別しない
    assert m(c, "別の話", summary="llama.cpp 対応")      # 抜粋でも判定
    assert not m(c, "Qwen の広告記事")                    # 外す語が勝つ
    assert not m(c, "無関係な話")
    assert m(c, "語が入っていない題", source_key="search:t:gnews-ja")   # 検索で取った記事はそのチャンネルのもの
    assert not m(c, "Qwen の広告", source_key="search:t:gnews-ja")      # 検索で取っても外す語は効く
    assert not m(c, "語が入っていない題", source_key="search:other:gnews-ja")
    rel = ch(include=[], kinds=["release"])
    assert m(rel, "llama.cpp b9000", kind="release") and not m(rel, "Qwen", kind="news")
    both = ch(include=["Qwen"], kinds=["release"])
    assert not m(both, "Qwen の記事", kind="news") and m(both, "Qwen b1", kind="release")


def test_default_channels_are_valid_and_cover_the_examples():
    cfg = channels.load()
    names = [c["name"] for c in cfg["channels"]]
    assert names == ["ローカル LLM", "AMD・GPU", "特許", "開発ツール", "使っているソフトの更新"]
    assert cfg["channels"][4]["kinds"] == ["release"] and not cfg["channels"][4]["search"]
    assert "Inkling" in cfg["channels"][0]["include"] and cfg["archive_keep"] == 50_000 and cfg["interval_minutes"] == 30


def test_channels_validation_and_save_roundtrip():
    bad = ([{"name": ""}], [{"name": "a", "include": []}], [{"name": "a", "include": ["x"], "color": "red"}],
           [{"name": "a", "include": ["x"], "kinds": ["nope"]}], [{"name": "a", "include": ["x"], "lang": "fr"}],
           [{"name": "a", "include": ["x"], "limit": 1}], [{"name": "a", "include": ["x"], "limit": 99}],
           [{"name": "a", "include": ["x" * 61]}], [{"name": "a", "include": [str(i) for i in range(21)]}],
           [{"name": "a", "include": ["x"], "id": "Bad Id!"}], [{"name": f"c{i}", "include": ["x"]} for i in range(13)], "x")
    for channels_in in bad:
        with pytest.raises(sources.ConfigError):
            channels.normalize({"channels": channels_in})
    with pytest.raises(sources.ConfigError):
        channels.normalize({"archive_keep": 5})
    with pytest.raises(sources.ConfigError):
        channels.normalize({"interval_minutes": 1})
    saved = channels.save({"channels": [{"name": "同じ", "include": ["a", "A", " b "]}, {"name": "同じ", "include": ["c"], "limit": 10}], "archive_keep": 2000})
    assert [c["id"] for c in saved["channels"]] == ["ch1", "ch2"] and saved["channels"][0]["include"] == ["a", "b"]
    assert saved["channels"][1]["limit"] == 10 and channels.load() == saved and saved["archive_keep"] == 2000


# ---- Google ニュースの RSS ----

GNEWS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Qwen - Google ニュース</title>
<item><title>Qwen の新モデルが公開 - ITmedia NEWS</title><link>https://news.google.com/rss/articles/CBMi123?oc=5</link>
<pubDate>Sun, 22 Nov 2026 09:00:00 GMT</pubDate><description>&lt;a href="https://news.google.com/rss/articles/CBMi123"&gt;Qwen の新モデルが公開&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font&gt;ITmedia NEWS&lt;/font&gt;</description>
<source url="https://www.itmedia.co.jp">ITmedia NEWS</source></item>
<item><title>javascript の話 - X</title><link>javascript:alert(1)</link></item></channel></rss>""".encode()


def test_gnews_url_and_items():
    c = ch(include=["Claude Code", "OpenCode", "Codex"])
    ja, en = channels.gnews_url(c, "ja"), channels.gnews_url(c, "en")
    assert ja.startswith("https://news.google.com/rss/search?q=%22Claude%20Code%22%20OR%20OpenCode%20OR%20Codex&hl=ja&gl=JP&ceid=JP:ja")
    assert en.endswith("hl=en-US&gl=US&ceid=US:en")
    got = sources.gnews_items(GNEWS, url=ja, channel=c, lang="ja")
    assert len(got) == 1                                   # 危ない URL の記事は捨てる
    item = got[0]
    assert item["title"] == "Qwen の新モデルが公開" and item["source"] == "ITmedia NEWS" and item["source_key"] == "search:t:gnews-ja"
    assert item["url"].startswith("https://news.google.com/rss/articles/") and item["summary"] == "" and item["kind"] == "news"


def test_gnews_fetch_goes_through_safe_http_get(monkeypatch):
    from app.knowledge import sources as ks

    seen = []
    monkeypatch.setattr(ks, "http_get", lambda url, **kw: seen.append(url) or GNEWS)
    got = asyncio.run(sources.fetch_gnews(ch(), "en"))
    assert seen and seen[0].startswith("https://news.google.com/rss/search?q=Qwen&hl=en-US") and got[0]["source_key"] == "search:t:gnews-en"


def test_search_sources_follow_channels_languages_and_switches():
    cfg = sources.normalize({})
    cs = [ch(id="a", lang="ja"), ch(id="b", lang="both"), ch(id="c", search=False), ch(id="d", include=[], kinds=["release"])]
    keys = [s.key for s in sources.search_sources(cfg, cs, reddit_key=True)]
    assert "search:a:gnews-ja" in keys and "search:a:gnews-en" not in keys and "search:a:hn" not in keys     # 日本語だけは HN・Reddit を引かない
    assert {"search:b:gnews-ja", "search:b:gnews-en", "search:b:hn", "search:b:reddit"} <= set(keys)
    assert "search:b:discourse-forum.level1techs.com" in keys
    assert not [k for k in keys if k.startswith(("search:c:", "search:d:"))]
    assert "search:b:reddit" not in [s.key for s in sources.search_sources(cfg, cs, reddit_key=False)]
    off = sources.search_sources({**cfg, "enabled": {**cfg["enabled"], "news": False, "forum": False}}, cs, reddit_key=True)
    assert [s.key for s in off] == ["search:b:reddit"]
    assert [s.key for s in sources.build_sources(cfg)] == [s.key for s in sources.build_sources(cfg, [])]


def test_hn_discourse_reddit_search_use_the_channel_terms(monkeypatch):
    from app.workflows import research_tools as rt

    calls = []

    async def get_json(url, **kw):
        calls.append((url, kw.get("params")))
        if "algolia" in url:
            return {"hits": [{"objectID": "1", "title": "Qwen released", "url": "https://a.test/q", "created_at_i": 1795000000}]}
        return {"topics": [{"id": 7, "slug": "qwen", "title": "Qwen の話", "created_at": "2026-11-22T00:00:00Z"}]}

    async def reddit(path, params):
        calls.append((path, params))
        return {"data": {"children": [{"data": {"title": "R", "permalink": "/r/x/comments/1/r/", "created_utc": 1795000000}}]}}

    monkeypatch.setattr(rt, "_get_json", get_json)
    monkeypatch.setattr(rt, "_reddit_oauth", reddit)
    c = ch(include=["Qwen", "GGUF"])
    hn = asyncio.run(sources.fetch_hn_search(c))
    dc = asyncio.run(sources.fetch_discourse_search(c, "forum.example.com"))
    rd = asyncio.run(sources.fetch_reddit_search(c))
    assert hn[0]["source_key"] == "search:t:hn" and dc[0]["source_key"] == "search:t:discourse-forum.example.com" and rd[0]["source_key"] == "search:t:reddit"
    assert calls[0][1]["query"] == "Qwen GGUF" and "Qwen GGUF" in calls[1][1]["q"] and calls[2][1]["q"] == "Qwen OR GGUF"

    async def none(path, params):
        return None

    monkeypatch.setattr(rt, "_reddit_oauth", none)
    with pytest.raises(sources.SkippedSource):
        asyncio.run(sources.fetch_reddit_search(c))


def test_search_results_are_saved_and_belong_to_the_channel():
    with SessionLocal() as db:
        item = sources.make_item(kind="news", source="ITmedia NEWS", source_key="search:t:gnews-ja", title="題に語がない", url="https://news.google.com/rss/articles/x")
        store.upsert_items(db, [item])
        row = db.query(NewsItem).one()
        assert channels.first_channel([ch()], row) == "t" and channels.first_channel([ch(id="z")], row) is None


# ---- 画面の取得（/home・/items?channel=） ----

def seed():
    add("https://a.test/1", title="Qwen の新モデル", topics=["Qwen"], published=NOW - timedelta(hours=1), source="A", source_key="rss:a")
    add("https://a.test/2", title="Radeon の新ドライバ ROCm 7", topics=["Radeon", "ROCm"], published=NOW - timedelta(hours=3), source="B", source_key="rss:b")
    add("https://a.test/3", title="llama.cpp b9000", kind="release", topics=["llama.cpp"], published=NOW - timedelta(hours=10), source="GitHub x", source_key="github:x")
    add("https://a.test/4", title="特許の AI 審査 広告", topics=["特許"], published=NOW - timedelta(days=2), source="C", source_key="rss:c")
    add("https://a.test/5", title="特許の AI 審査", topics=["特許"], published=NOW - timedelta(days=3), source="C", source_key="rss:c")
    add("https://a.test/6", title="無関係な話", topics=[], published=NOW - timedelta(days=5), source="D", source_key="rss:d")


def test_home_shape_and_channel_assignment(client, monkeypatch):
    channels.save({"channels": [ch(id="llm", name="LLM", include=["Qwen", "llama.cpp"]), ch(id="amd", name="AMD", include=["Radeon"]),
                                ch(id="pat", name="特許", include=["特許"], exclude=["広告"]), ch(id="rel", name="更新", include=[], kinds=["release"], search=False)]})
    seed()
    with SessionLocal() as db:
        data = home.build(db, user_id("newsreader"), NOW)
    assert set(data) == {"channels", "onair", "ticker", "now", "today", "week", "sections", "interest", "trends", "numbers", "generated_at"}
    assert [c["number"] for c in data["channels"]] == [1, 2, 3, 4]
    assert data["onair"]["title"] == "Qwen の新モデル" and data["onair"]["channel"] == "llm"
    shown = [i["id"] for i in data["now"] + data["today"] + data["week"]] + [data["onair"]["id"]]
    assert len(shown) == len(set(shown))                                 # 同じ記事を区画で重ねない
    assert [i["title"] for i in data["now"]] == ["Radeon の新ドライバ ROCm 7"] and [i["title"] for i in data["today"]] == ["llama.cpp b9000"]
    by = {s["id"]: s for s in data["sections"]}
    assert [i["title"] for i in by["llm"]["items"]] == ["Qwen の新モデル", "llama.cpp b9000"]
    assert [i["title"] for i in by["pat"]["items"]] == ["特許の AI 審査"]       # 外す語
    assert [i["title"] for i in by["rel"]["items"]] == ["llama.cpp b9000"]
    assert by["llm"]["color"] == "#f5c518" and by["llm"]["number"] == 1
    assert len(data["ticker"]) == 6 and data["ticker"][0]["title"] == "Qwen の新モデル"
    assert data["numbers"]["today"] >= 0 and data["numbers"]["top_channel"] is None


def test_numbers_count_reads_and_top_channel(client):
    channels.save({"channels": [ch(id="llm", name="LLM", include=["Qwen"]), ch(id="amd", name="AMD", include=["Radeon"])]})
    seed()
    uid = user_id("newsreader")
    with SessionLocal() as db:
        ids = {r.title: r.id for r in db.query(NewsItem)}
    act(uid, ids["Radeon の新ドライバ ROCm 7"], "open", at=NOW - timedelta(minutes=30))
    act(uid, ids["Qwen の新モデル"], "open", at=NOW - timedelta(days=1))
    with SessionLocal() as db:
        n = home.build(db, uid, NOW)["numbers"]
    assert n["read_30d"] == 2 and n["read_today"] == 1 and n["top_channel"]["reads"] == 1 and n["top_channel"]["id"] in ("llm", "amd")


def test_home_and_channel_items_api(client):
    c = login(client, "newsreader")
    channels.save({"channels": [ch(id="amd", name="AMD", include=["Radeon", "ROCm"])]})
    seed()
    # 取り込んだ時刻が NOW 固定のため、画面側の窓（21 日）に入るよう現在に近い日付で入れ直す
    with SessionLocal() as db:
        for row in db.query(NewsItem):
            row.published_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        db.commit()
    got = c.get("/api/v1/news/items?channel=amd&view=latest").json()
    assert [i["title"] for i in got["items"]] == ["Radeon の新ドライバ ROCm 7"] and got["items"][0]["channel"] == "amd"
    assert c.get("/api/v1/news/items?channel=none").status_code == 404
    assert c.get("/api/v1/news/home").status_code == 200 and c.get("/api/v1/news/home").json()["sections"][0]["id"] == "amd"
    client.cookies.clear()
    assert client.get("/api/v1/news/home").status_code in (401, 403)


def test_channels_api_permissions(client):
    from tests.conftest import CSRF_HEADERS

    login(client, "newsviewer")
    assert client.get("/api/v1/news/channels").status_code == 200
    assert client.put("/api/v1/news/channels", json={"channels": []}, headers=CSRF_HEADERS).status_code == 403
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    admin_client = client

    ok = admin_client.put("/api/v1/news/channels", json={"channels": [ch()], "archive_keep": 3000}, headers=CSRF_HEADERS)
    assert ok.status_code == 200 and ok.json()["archive_keep"] == 3000
    assert admin_client.put("/api/v1/news/channels", json={"channels": [{"name": "x"}]}, headers=CSRF_HEADERS).status_code == 422
    got = admin_client.get("/api/v1/news/channels").json()
    assert got["channels"][0]["id"] == "t" and "release" in got["kinds"] and got["limits"]["channels"] == 12


# ---- 表示・好みの設定 ----

def test_prefs_defaults_validation_and_per_user_storage(client):
    from tests.conftest import CSRF_HEADERS

    a = login(client, "newsprefs-a")
    got = a.get("/api/v1/news/prefs").json()
    assert got["display"]["design"] == "broadcast" and got["taste"]["strength"] == 0.5 and got["taste"]["dim_read"] is True
    body = {"display": {"design": "paper", "accent": "#12AB34", "columns": "2", "ticker": False, "reduce_motion": True}, "taste": {"strength": 0.9, "mute_terms": ["広告", "広告"], "half_life_days": 14}}
    saved = a.put("/api/v1/news/prefs", json=body, headers=CSRF_HEADERS).json()
    assert saved["display"]["design"] == "paper" and saved["display"]["accent"] == "#12ab34" and saved["display"]["ticker"] is False
    assert saved["display"]["images"] is True and saved["taste"]["mute_terms"] == ["広告"] and saved["taste"]["half_life_days"] == 14
    b = login(client, "newsprefs-b")
    assert b.get("/api/v1/news/prefs").json()["display"]["design"] == "broadcast"          # 利用者ごと
    a = login(client, "newsprefs-a")
    assert a.get("/api/v1/news/prefs").json() == saved
    for bad in ({"display": {"design": "neon"}}, {"display": {"accent": "yellow"}}, {"display": {"columns": "9"}}, {"taste": {"strength": 2}},
                {"taste": {"half_life_days": 1}}, {"taste": {"mute_terms": "x"}}, {"taste": {"mute_sources": ["x"] * 51}}, {"display": []}):
        assert a.put("/api/v1/news/prefs", json=bad, headers=CSRF_HEADERS).status_code == 422, bad


def test_taste_strength_changes_ranking():
    """強さ 0 は新しさだけ、強さ 1 は興味が効いて古くても好みの記事が上へ来る。"""
    uid = user_id("newstaste")
    new = add("https://t.test/new", title="新しいが興味外", topics=["Foo"], published=NOW - timedelta(hours=1), source_key="rss:n")
    old = add("https://t.test/old", title="古いが好み", topics=["Qwen"], published=NOW - timedelta(days=4), source_key="rss:o")
    liked = add("https://t.test/liked", title="好みの印", topics=["Qwen"], published=NOW - timedelta(days=6), source_key="rss:l")
    act(uid, liked, "like", at=NOW)
    act(uid, liked, "star", at=NOW)

    def order(strength: float) -> list[int]:
        prefs.save(uid, {"taste": {"strength": strength}})
        with SessionLocal() as db:
            rows = [db.get(NewsItem, i) for i in (new, old)]
            ranked = profile.rank(db, uid, rows, store.states(db, uid, [new, old]), NOW)
        return [r.id for r, _ in ranked]

    assert order(0.0) == [new, old]
    assert order(1.0) == [old, new]


def test_chat_toggle_and_half_life_reach_interests():
    uid = user_id("newstaste2")
    item = add("https://t.test/x", title="Qwen", topics=["Qwen"], published=NOW)
    act(uid, item, "like", at=NOW - timedelta(days=30))
    prefs.save(uid, {"taste": {"half_life_days": 30}})
    with SessionLocal() as db:
        slow = profile.interests(db, uid, NOW)["topics"]["Qwen"]
    prefs.save(uid, {"taste": {"half_life_days": 3}})
    with SessionLocal() as db:
        fast = profile.interests(db, uid, NOW)["topics"]["Qwen"]
    assert slow == pytest.approx(1.5, rel=0.05) and fast < 0.01


def test_mute_terms_and_sources_hide_from_lists():
    uid = user_id("newsmute")
    add("https://m.test/1", title="広告つきの記事", source="Spam", source_key="rss:spam", published=datetime.now(timezone.utc))
    add("https://m.test/2", title="良い記事", source="Spam", source_key="rss:spam", published=datetime.now(timezone.utc))
    add("https://m.test/3", title="別の良い記事", source="Good", source_key="rss:good", published=datetime.now(timezone.utc))
    with SessionLocal() as db:
        assert len(store.list_items(db, uid, view="latest")["items"]) == 3
    prefs.save(uid, {"taste": {"mute_terms": ["広告"]}})
    with SessionLocal() as db:
        assert [i["title"] for i in store.list_items(db, uid, view="latest")["items"]] == ["別の良い記事", "良い記事"]
        assert len(store.list_items(db, uid, view="latest", q="広告")["items"]) == 1       # 検索では探せる
    prefs.save(uid, {"taste": {"mute_sources": ["Spam"]}})
    with SessionLocal() as db:
        assert [i["title"] for i in store.list_items(db, uid, view="latest")["items"]] == ["別の良い記事"]


# ---- アーカイブ（残す件数・書き出し） ----

def test_archive_keep_prunes_but_keeps_starred(client):
    uid = user_id("newsarch")
    ids = [add(f"https://k.test/{i}", title=f"k{i}", published=NOW - timedelta(days=i)) for i in range(5)]
    act(uid, ids[4], "star")
    channels.save({"channels": [ch()], "archive_keep": 1000})
    with SessionLocal() as db:
        assert store.prune(db, channels.load()["archive_keep"]) == 0
        assert store.prune(db, 2) == 3           # 古い順に消す。star の k4 は残る
        left = {r.title for r in db.query(NewsItem)}
    assert left == {"k0", "k4"}


def test_export_json_csv_and_formula_guard(client):
    c = login(client, "newsexport")
    add("https://e.test/1", title="=HYPERLINK(\"http://x\")", summary="+cmd", topics=["Qwen", "GGUF"], source="S")
    add("https://e.test/2", title="普通の記事", topics=[], source="T")
    add("/settings", title="イベント", kind="event", uid=user_id("newsexport"))
    js = c.get("/api/v1/news/export?format=json")
    assert js.status_code == 200 and "attachment" in js.headers["content-disposition"]
    data = js.json()
    assert data["count"] == 2 and {"id", "title", "url", "topics", "starred"} <= set(data["items"][0])      # 出来事は含めない
    csv_text = c.get("/api/v1/news/export?format=csv")
    assert csv_text.headers["content-type"].startswith("text/csv") and csv_text.text.startswith("﻿")
    body = csv_text.text
    assert "'=HYPERLINK" in body and "'+cmd" in body and "Qwen、GGUF" in body and "普通の記事" in body
    assert c.get("/api/v1/news/export?format=xml").status_code == 422
    channels.save({"channels": [ch(include=["普通"])]})
    assert c.get("/api/v1/news/export?format=json&channel=t").json()["count"] == 1


def test_refresh_uses_channel_search_sources(monkeypatch):
    """取得の流れ: チャンネルの設定からも検索のソースを作って取り、記事は保存される。"""
    channels.save({"channels": [ch(id="x", include=["Qwen"], lang="ja")]})
    seen = {}

    async def fake(url_channel, lang):
        seen["lang"] = lang
        return [sources.make_item(kind="news", source="ITmedia", source_key="search:x:gnews-ja", title="Qwen 記事", url="https://news.google.com/rss/articles/zz")]

    monkeypatch.setattr(sources, "fetch_gnews", fake)
    # 外へ通信しない: 検索のソースのうち Google ニュースだけを残し、その取得も上でモックしてある
    monkeypatch.setattr(sources, "build_sources", lambda cfg, chs=None: [s for s in sources.search_sources(cfg, chs or []) if "gnews" in s.key])
    service._last.update(started=0.0)
    result = asyncio.run(service.refresh(force=True))
    assert result["new"] == 1 and seen["lang"] == "ja"
