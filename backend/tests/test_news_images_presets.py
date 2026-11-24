"""目的: ニュースの「画像の取得」「自分のリポジトリの除外」「プリセット」「チャンネルの語を変えたときの振り分け」「ホームの表示数」の試験。外へ通信しない（HTTP は全部モック）。

根拠: 2026-11-23 利用者「本来ここには、ソースから取得した画像が表示されるべき…極力画像を撮ってほしい」「私が作っているコントロールデックとか私のリポジトリのデータは特に表示する必要はない」
「ニュースのソースの右側にプリセットボタン…無料のものを優先」「チャンネル02…以前設定していた AMD とか GPU とかの設定がそのまま残っている」「ホームに出す記事の数をどこかで設定できるように」。
守ること: og:image・twitter:image の取り出しと壊れた HTML・私的な IP・大きすぎる応答・取れなかった記事を再試行しない・自分のリポジトリは設定の移行（1 回）と一覧・チャンネルから除く・
プリセットの形（https・重複なし・分野・有料のサイトを入れない・設定の検査を通る）・語を変えたとき古い検索の結果が出ない・検索のやり直しの対象・home_items の範囲。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import delete

from app.news import channels, home, images, presets, prefs, router, sources, store
from app.news import db as news_db
from app.news.db import NewsAction, NewsItem
from tests.test_news import add

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean(client, tmp_path, monkeypatch):
    for mod in (sources, channels, prefs):
        monkeypatch.setattr(mod, "_path", lambda m=mod: tmp_path / "news" / f"{m.__name__.rsplit('.', 1)[-1]}.json")
    with news_db.session() as db:
        db.execute(delete(NewsAction))
        db.execute(delete(NewsItem))
        db.commit()
    yield


# ---- 画像の取り出し ----

def test_extract_image_prefers_og_then_twitter_and_resolves_relative():
    page = '<html><head><meta name="twitter:image" content="/t.png"><meta property="og:image" content="/o.jpg"></head></html>'
    assert images.extract_image(page, "https://a.test/x/y") == "https://a.test/o.jpg"
    assert images.extract_image('<meta content="https://c.test/t.png" name="twitter:image">', "https://a.test/") == "https://c.test/t.png"
    assert images.extract_image('<link rel="image_src" href="//cdn.test/i.png">', "https://a.test/") == "https://cdn.test/i.png"


def test_extract_image_rejects_unsafe_and_survives_broken_html():
    assert images.extract_image('<meta property="og:image" content="javascript:alert(1)">', "https://a.test/") is None
    assert images.extract_image('<meta property="og:image" content="data:image/png;base64,AAAA">', "https://a.test/") is None
    assert images.extract_image("<<<html><meta property=og:image content=https://a.test/p.png <<< 壊れた", "https://a.test/") in (None, "https://a.test/p.png")
    assert images.extract_image(b"\xff\xfe\x00 not html", "https://a.test/") is None
    assert images.extract_image("", "https://a.test/") is None


def test_rss_media_enclosure_and_first_img_are_used():
    rss = b"""<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
      <item><title>A</title><link>https://a.test/1</link><media:content url="https://i.test/a.jpg" medium="image"/></item>
      <item><title>B</title><link>https://a.test/2</link><enclosure url="https://i.test/b.png" type="image/png"/></item>
      <item><title>C</title><link>https://a.test/3</link><description>&lt;p&gt;&lt;img src="https://i.test/c.webp"&gt;&lt;/p&gt;</description></item>
      <item><title>D</title><link>https://a.test/4</link></item></channel></rss>"""
    got = {i["title"]: i["image_url"] for i in sources.parse_feed(rss, url="https://a.test/feed", name="A")}
    assert got == {"A": "https://i.test/a.jpg", "B": "https://i.test/b.png", "C": "https://i.test/c.webp", "D": None}


def _mock_client(handler):
    def make(**kw):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return make


def test_fetch_page_image_reads_only_the_head_and_follows_redirects(monkeypatch):
    from app.web_collector import egress

    big = b"<html><head>" + b"x" * (images.READ_BYTES + 10) + b'<meta property="og:image" content="https://i.test/late.png"></head>'

    def handler(req: httpx.Request):
        if req.url.path == "/redir":
            return httpx.Response(302, headers={"location": "/ok"})
        if req.url.path == "/ok":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b'<meta property="og:image" content="/p.png">')
        if req.url.path == "/big":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=big)
        if req.url.path == "/bin":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
        if req.url.path == "/file":
            return httpx.Response(302, headers={"location": "file:///etc/passwd"})
        return httpx.Response(404)

    monkeypatch.setattr(egress, "guarded_client", _mock_client(handler))
    run = lambda path: asyncio.run(images.fetch_page_image("https://a.test" + path))
    assert run("/redir") == "https://a.test/p.png"
    assert run("/big") is None            # 先頭 256KB の外の画像は見ない
    assert run("/bin") is None
    assert run("/file") is None
    assert run("/missing") is None
    assert asyncio.run(images.fetch_page_image("ftp://a.test/x")) is None
    assert asyncio.run(images.fetch_page_image("https://news.google.com/rss/articles/x")) is None


def test_fetch_page_image_refuses_private_addresses():
    """実物の guarded_client を使う（私的・loopback の IP は接続前に断る。外へは出ない）。"""
    for url in ("http://127.0.0.1/x", "http://10.0.0.5/x", "http://169.254.169.254/latest/meta-data", "http://[::1]/x"):
        assert asyncio.run(images.fetch_page_image(url)) is None


def test_fill_only_new_items_without_image_and_never_retries(monkeypatch):
    a = add("https://a.test/1", title="画像なし")
    b = add("https://a.test/2", title="取れない")
    c = add("https://a.test/3", title="すでに画像あり", image="https://i.test/c.png")
    d = add("https://a.test/4", title="リリース", kind="release", source_key="github:x/y")
    old = add("https://a.test/5", title="新着でない", published=NOW - timedelta(days=1))
    calls: list[str] = []

    async def fake(url):
        calls.append(url)
        return "https://i.test/a.png" if url.endswith("/1") else None

    monkeypatch.setattr(images, "fetch_page_image", fake)
    out = asyncio.run(images.fill([a, b, c, d]))
    assert out["tried"] == 3 and out["found"] == 1 and sorted(calls) == ["https://a.test/1", "https://a.test/2", "https://a.test/5"]
    with news_db.session() as db:
        rows = {r.id: r for r in db.query(NewsItem)}
    assert rows[a].image_url == "https://i.test/a.png" and rows[a].image_tried and rows[b].image_url is None and rows[b].image_tried
    assert rows[old].image_tried and not rows[d].image_tried          # 新着でない記事は、枠の残りで埋め合わせる
    calls.clear()
    assert asyncio.run(images.fill([a, b]))["tried"] == 0 and calls == []       # 取れなかった記事も再試行しない


def test_fill_is_capped_per_run(monkeypatch):
    ids = [add(f"https://a.test/n{i}", title=f"n{i}", published=NOW - timedelta(minutes=i)) for i in range(images.MAX_PER_RUN + 15)]

    async def fake(url):
        return None

    monkeypatch.setattr(images, "fetch_page_image", fake)
    assert asyncio.run(images.fill(ids))["tried"] == images.MAX_PER_RUN


def test_backfill_old_items_up_to_40_within_the_run_cap(monkeypatch):
    """目的: 取得のたびに、画像を試していない古い記事を新しい順に最大 40 件、新着と合わせて 1 回 60 件の中で試すこと。
    根拠: 2026-11-24 利用者の本番: 画像を取るのは新しい記事だけで、既存の 860 件は image_tried が 0 のまま。"""
    old = [add(f"https://o.test/{i}", title=f"old{i}", published=NOW - timedelta(days=2, minutes=i)) for i in range(70)]
    fresh = [add(f"https://n.test/{i}", title=f"new{i}", published=NOW - timedelta(minutes=i)) for i in range(30)]
    seen: list[str] = []

    async def fake(url):
        seen.append(url)
        return None

    monkeypatch.setattr(images, "fetch_page_image", fake)
    assert asyncio.run(images.fill(fresh))["tried"] == 60          # 新着 30 + 古い 30（枠 60 の残り）
    assert sum(u.startswith("https://o.test/") for u in seen) == 30 and "https://o.test/0" in seen
    seen.clear()
    assert asyncio.run(images.fill([]))["tried"] == 40             # 新着が無ければ古い記事 40 件（最大）
    assert "https://o.test/30" in seen and "https://o.test/0" not in seen


def test_site_logo_shared_by_two_articles_is_dropped_but_other_domains_kept():
    """目的: 同じドメインの 2 件以上の別の記事に同じ画像 URL が付いていれば、その URL はロゴとして外す（既に付いた分も）。別のドメインの共有・1 件だけ・生成画像は残す。
    根拠: 2026-11-24 利用者の本番: フォーラム（Discourse）はどの投稿でも同じサイトのロゴが og:image に返る。"""
    logo = "https://forum.test/logo.png"
    a = add("https://forum.test/t/1", title="a", image=logo)
    b = add("https://forum.test/t/2", title="b", image=logo)
    c = add("https://forum.test/t/3", title="c", image="https://forum.test/own.png")
    d = add("https://other.test/1", title="d", image="https://cdn.test/shared.png")
    e = add("https://news.test/1", title="e", image="https://cdn.test/shared.png")
    g = add("https://www.site.test/1", title="g", image="https://img.test/x.png")
    h = add("https://site.test/2", title="h", image="https://img.test/x.png")
    gen = "/api/v1/news/images/1.webp"
    i1 = add("https://gen.test/1", title="i1", image=gen)
    i2 = add("https://gen.test/2", title="i2", image=gen)
    assert images.drop_logos() == 4
    with news_db.session() as db:
        rows = {r.id: r for r in db.query(NewsItem)}
    assert rows[a].image_url is None and rows[b].image_url is None and rows[a].image_tried
    assert rows[g].image_url is None and rows[h].image_url is None               # www. の有無は同じドメイン
    assert rows[c].image_url and rows[d].image_url and rows[e].image_url         # 別のドメインの共有は残す
    assert rows[i1].image_url == gen and rows[i2].image_url == gen


def test_google_news_items_resolve_to_original_url_for_the_image(monkeypatch):
    """目的: Google ニュースの記事は転送を解いた元の URL からページの画像を取り、記事の url は置き換えないこと。解けなければ画像は付けない（画像生成へ）。
    根拠: 2026-11-24 利用者の本番: Google ニュースの記事はページの画像が 0 件。"""
    gnews_url = "https://news.google.com/rss/articles/CBMiAAAA?oc=5"
    ok = add(gnews_url, title="g1", source_key="search:ai:gnews-ja")
    fail = add("https://news.google.com/rss/articles/CBMiBBBB?oc=5", title="g2", source_key="search:ai:gnews-ja")
    from app.news import gnews

    async def fake_resolve(url, client=None):
        return "https://real.test/article" if url == gnews_url else None

    fetched: list[str] = []

    async def fake_fetch(url):
        fetched.append(url)
        return "https://real.test/og.png"

    monkeypatch.setattr(gnews, "resolve", fake_resolve)
    monkeypatch.setattr(images, "fetch_page_image", fake_fetch)
    out = asyncio.run(images.fill([ok, fail]))
    assert out["tried"] == 2 and out["found"] == 1 and fetched == ["https://real.test/article"]
    with news_db.session() as db:
        rows = {r.id: r for r in db.query(NewsItem)}
    assert rows[ok].image_url == "https://real.test/og.png" and rows[ok].url == gnews_url
    assert rows[fail].image_url is None and rows[fail].image_tried


def test_upsert_reports_new_ids():
    with news_db.session() as db:
        first = store.upsert_items(db, [sources.make_item(kind="news", source="S", source_key="rss:s", title="t1", url="https://a.test/1")])
        second = store.upsert_items(db, [sources.make_item(kind="news", source="S", source_key="rss:s", title="t1", url="https://a.test/1"),
                                         sources.make_item(kind="news", source="S", source_key="rss:s", title="t2", url="https://a.test/2")])
        assert len(first["new_ids"]) == 1 and len(second["new_ids"]) == 1 and second["new_ids"] != first["new_ids"]


# ---- 自分のリポジトリを出さない ----

def test_own_repo_not_in_defaults_and_removed_once_from_saved_config():
    assert "souten-yd/ControlDeck" not in sources.DEFAULT_CONFIG["github"]
    path = sources._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"github": ["ggml-org/llama.cpp", "souten-yd/ControlDeck", "Souten-YD/other"]}), encoding="utf-8")
    cfg = sources.load_config()
    assert cfg["github"] == ["ggml-org/llama.cpp"] and sources.MIGRATION_OWN_REPO in cfg["migrated"]
    assert json.loads(path.read_text(encoding="utf-8"))["github"] == ["ggml-org/llama.cpp"]       # 書き戻した
    # 利用者があとで足し直したら、もう外さない（1 回だけ）
    sources.save_config({**cfg, "github": ["souten-yd/ControlDeck"]})
    assert sources.load_config()["github"] == ["souten-yd/ControlDeck"]


def test_own_repo_items_hidden_from_list_home_channels_and_digest():
    mine = add("https://github.com/souten-yd/ControlDeck/releases/v1", title="ControlDeck v1", kind="release", source_key="github:souten-yd/ControlDeck", published=NOW)
    other = add("https://github.com/ggml-org/llama.cpp/releases/b1", title="llama.cpp b1", kind="release", source_key="github:ggml-org/llama.cpp", published=NOW)
    with news_db.session() as db:
        listed = [i["id"] for i in store.list_items(db, 1, view="latest", now=NOW)["items"]]
        assert other in listed and mine not in listed
        updates = next(c for c in channels.DEFAULT_CHANNELS if c["id"] == "updates")
        ids = [i["id"] for i in store.list_items(db, 1, view="latest", channel=updates, now=NOW)["items"]]
        assert other in ids and mine not in ids
        built = home.build(db, 1, NOW)
        seen = [i["id"] for s in built["sections"] for i in s["items"]] + [t["id"] for t in built["ticker"]]
        assert mine not in seen and other in seen
        assert all(r["id"] != mine for r in home.export_rows(db, 1))
    assert not channels.matches(updates, kind="release", source_key="github:souten-yd/ControlDeck", title="x")
    from app.news import digest

    with news_db.session() as db:
        groups = digest.gather(db, "daily", NOW + timedelta(hours=1))
    flat = [i["id"] for g in groups for i in g["items"]]
    assert mine not in flat and other in flat


# ---- プリセット ----

def test_preset_list_shape():
    items = presets.PRESETS
    groups = {g["id"] for g in presets.GROUPS}
    assert {g["label"] for g in presets.GROUPS} >= {"株式・市場", "科学技術", "テレビゲーム", "PC", "電気・電子", "AI", "ローカル AI", "日本の政治経済", "海外の政治経済"}
    assert len({p["id"] for p in items}) == len(items) and all(p["group"] in groups for p in items)
    assert all(p["verified"] in (presets.VERIFIED, presets.VERIFIED_2) for p in items)
    for p in items:
        if p["kind"] == "rss":
            assert urlsplit(p["value"]).scheme == "https", p
    rss_urls = [p["value"].lower() for p in items if p["kind"] == "rss"]
    assert len(set(rss_urls)) == len(rss_urls)
    assert sum(1 for p in items if p["group"] == "local-ai") >= 40       # ローカル AI は特に厚く
    assert {"ollama/ollama", "vllm-project/vllm", "ikawrakow/ik_llama.cpp", "unslothai/unsloth", "ggml-org/llama.cpp"} <= {p["value"] for p in items if p["kind"] == "github"}
    assert any(p["kind"] == "reddit" and p["value"] == "LocalLLaMA" and p["key"] for p in items)
    assert not any(p["value"].startswith("souten-yd/") for p in items)


def test_presets_exclude_paid_sites_and_pass_source_validation():
    paid = ("bloomberg.com", "wsj.com", "www.nikkei.com", "nytimes.com", "washingtonpost.com", "economist.com", "ft.com", "technologyreview.com", "seekingalpha.com", "barrons.com")
    for p in presets.PRESETS:
        host = urlsplit(p["value"]).hostname or ""
        assert not any(host == d or host.endswith("." + d) for d in paid), p
    cfg = sources.load_config()
    everything = {**cfg, "rss": [{"name": p["name"], "url": p["value"]} for p in presets.PRESETS if p["kind"] == "rss"]}
    for kind in ("github", "codeberg", "discourse", "reddit"):
        everything[kind] = [p["value"] for p in presets.PRESETS if p["kind"] == kind]
    saved = sources.normalize(everything)       # 全部足しても上限・形の検査を通る
    assert len(saved["rss"]) == len([p for p in presets.PRESETS if p["kind"] == "rss"])
    assert re.match(r"^[\w.-]+/[\w.-]+$", saved["github"][0])


def test_presets_api(client):
    from tests.test_news import login

    login(client, "news-viewer")
    body = client.get("/api/v1/news/presets").json()
    assert body["verified"] == presets.VERIFIED_2 and len(body["items"]) == len(presets.PRESETS) and body["groups"][0]["count"] > 0


# ---- チャンネルの語を変えたとき ----

def _chan(**kw):
    return {"id": "t", "name": "T", "color": "#f5c518", "include": ["Maya"], "exclude": [], "kinds": [], "lang": "both", "limit": 6, "search": True, **kw}


def test_old_search_results_do_not_show_after_terms_change():
    add("https://g.test/amd", title="AMD の新 GPU", source_key="search:t:gnews-ja", published=NOW)
    add("https://g.test/maya", title="Maya の新版", source_key="search:t:gnews-ja", published=NOW)
    channel = _chan()
    with news_db.session() as db:
        titles = [i["title"] for i in store.list_items(db, 1, view="latest", channel=channel, now=NOW)["items"]]
    assert titles == ["Maya の新版"]


def test_changed_search_channels_detects_term_lang_and_new_channels():
    old = {"t": _chan(), "u": _chan(id="u", search=False)}
    same = [_chan(), _chan(id="u", search=False)]
    assert router.changed_search_channels(old, same) == []
    assert router.changed_search_channels(old, [_chan(include=["Maya", "llama.cpp"]), _chan(id="u", search=False)]) == ["t"]
    assert router.changed_search_channels(old, [_chan(lang="ja"), _chan(id="u", search=False)]) == ["t"]
    assert router.changed_search_channels(old, [_chan(), _chan(id="u", search=False), _chan(id="v")]) == ["v"]
    assert router.changed_search_channels(old, [_chan(), _chan(id="u", include=["x"], search=True)]) == ["u"]


def test_refresh_with_channel_ids_fetches_only_those_searches(monkeypatch):
    from app.news import service

    seen: list[str] = []

    async def one():
        return []

    def build(cfg, chs=None):
        return [sources.Source("search:a:gnews-ja", "a", "news", lambda: _rec(seen, "a")), sources.Source("search:b:gnews-ja", "b", "news", lambda: _rec(seen, "b")),
                sources.Source("rss:x", "x", "news", lambda: _rec(seen, "x"))]

    monkeypatch.setattr(sources, "build_sources", build)
    service._last.update(started=0.0)
    out = asyncio.run(service.refresh(force=True, channel_ids=["b"]))
    assert out["started"] and seen == ["b"]


async def _rec(seen, name):
    seen.append(name)
    return []


# ---- ホームの表示数 ----

def test_home_items_pref_range():
    assert prefs.normalize({})["display"]["home_items"] == 5
    assert prefs.normalize({"display": {"home_items": 10}})["display"]["home_items"] == 10
    for bad in (0, 11, "x"):
        with pytest.raises(sources.ConfigError):
            prefs.normalize({"display": {"home_items": bad}})
