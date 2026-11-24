"""目的: ニュースの「サイトを追加」（RSS の発見・記事の一覧のページの巡回）と「通信確認」の試験。外へ通信しない（HTTP は probe.http_fetch の差し替えで全部モック）。

根拠: 2026-11-24 利用者「任意のサイトのクロール機能を追加できるのか、サイト追加後に通信確認みたいなことがあるといい」
「日本の AI だったりとか政治の情報メディアのサイトからクローリング先を追加して欲しい」。以前から「有料なのは使わない（無料を優先）」。
守ること: RSS/Atom の判定・HTML からの RSS の発見（link rel と /feed など）・一覧のページから記事らしいリンクだけを拾う（外のサイト・案内・タグを除く・件数の上限）・
robots.txt を守る・頻度の上限（前回の結果を返す）・ロボット確認とログインを回避せず理由を言う・通信確認の結果の形（成功と各種の失敗）・設定の検査（pages）・プリセットの形。
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import CSRF_HEADERS

from app.news import presets, probe, sources

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>First article</title><link>https://site.test/a/1</link><pubDate>Mon, 23 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Second article</title><link>https://site.test/a/2</link><pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Third article</title><link>https://site.test/a/3</link><pubDate>Sat, 21 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Fourth article</title><link>https://site.test/a/4</link><pubDate>Fri, 20 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>"""

HTML_LINKED = b"""<html><head><title>Site A</title><link rel="alternate" type="application/rss+xml" href="/custom.xml"></head><body></body></html>"""


def serve(monkeypatch, pages: dict, calls: list | None = None):
    """pages: url -> (status, headers, body) か probe.ProbeError。無い URL は 404。"""
    async def fake(url, *, timeout=15):
        if calls is not None:
            calls.append(url)
        got = pages.get(url, (404, {}, b""))
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(probe, "http_fetch", fake)


def ok(body: bytes, ctype: str = "text/html; charset=utf-8", url_headers: dict | None = None):
    return (200, {"content-type": ctype, **(url_headers or {})}, body)


def run(coro):
    return asyncio.run(coro)


LIST_HTML = """<html><head><title>News</title></head><body>
<nav><a href="/">ホーム</a><a href="/about/">会社概要について詳しく</a><a href="/login">ログインはこちらから</a></nav>
<a href="/news/2026/11/23/article-one-is-here">政治の新しい動きについての記事</a>
<time datetime="2026-11-23T09:00:00+09:00"></time><a href="/news/2026/11/22/article-two-is-here">AI の最新の動向を解説する記事</a>
<a href="https://other.test/news/2026/11/23/x-y-z-w">外のサイトの記事の見出しです</a>
<a href="/tag/ai/">タグ AI の一覧のページ</a>
<a href="/news/2026/11/21/article-three">Three english words headline long enough</a>
<a href="/news/2026/11/21/short">Home</a>
<a href="http://site.test/news/2026/11/20/insecure">http の記事の見出しです</a>
<a href="/news/2026/11/23/article-one-is-here#c">政治の新しい動きについての記事</a>
<a href="/photo.jpg">画像のファイルへのリンクです</a>
</body></html>"""


def test_feed_detection_and_non_feed():
    assert len(probe.feed_items(RSS, "https://site.test/feed")) == 4
    with pytest.raises(probe.ProbeError) as e:
        probe.feed_items(b"<html><body>no</body></html>", "https://site.test/")
    assert e.value.code == "format"


def test_list_links_picks_only_article_like_same_site_links():
    links = probe.list_links(LIST_HTML, "https://site.test/news/")
    urls = [link["url"] for link in links]
    assert urls == ["https://site.test/news/2026/11/23/article-one-is-here", "https://site.test/news/2026/11/22/article-two-is-here",
                    "https://site.test/news/2026/11/21/article-three"]
    assert links[1]["published"].startswith("2026-11-23")      # 直前の <time> を拾う
    assert all("other.test" not in u and "/tag/" not in u and "/about" not in u for u in urls)


def test_list_links_limit_and_www_is_same_site():
    body = "".join(f'<a href="/news/2026/11/{i % 28 + 1:02d}/article-number-{i}">記事の見出しその{i}番です</a>' for i in range(80))
    assert len(probe.list_links(body, "https://www.site.test/")) == probe.PAGE_ITEMS
    assert probe.list_links('<a href="https://www.site.test/news/2026/11/1/aaa-bbb-ccc-ddd">www つきの記事の見出し</a>', "https://site.test/")


def test_discover_feed_url_as_is(monkeypatch):
    serve(monkeypatch, {"https://site.test/feed": ok(RSS, "application/rss+xml")})
    r = run(probe.discover("https://site.test/feed"))
    assert r["mode"] == "feed" and r["feeds"][0]["count"] == 4 and r["feeds"][0]["titles"][0] == "First article"
    assert r["feeds"][0]["latest"] == "2026-11-23"


def test_discover_finds_link_rel_and_common_places(monkeypatch):
    calls: list = []
    serve(monkeypatch, {"https://site.test/": ok(HTML_LINKED), "https://site.test/custom.xml": ok(RSS, "application/xml"),
                        "https://site.test/rss.xml": ok(RSS, "application/xml"), "https://site.test/robots.txt": (404, {}, b"")}, calls)
    r = run(probe.discover("https://site.test/"))
    assert r["mode"] == "feeds" and r["title"] == "Site A"
    assert {f["url"] for f in r["feeds"]} == {"https://site.test/custom.xml", "https://site.test/rss.xml"}
    for path in probe.COMMON_FEEDS:
        assert f"https://site.test{path}" in calls


def test_discover_falls_back_to_list_page_and_reports_robots(monkeypatch):
    serve(monkeypatch, {"https://site.test/news/": ok(LIST_HTML.encode()), "https://site.test/robots.txt": ok(b"User-agent: *\nDisallow: /news/", "text/plain")})
    r = run(probe.discover("https://site.test/news/"))
    assert r["mode"] == "page" and r["feeds"] == [] and r["page"]["count"] == 3 and r["page"]["robots"] is False


def test_discover_does_not_bypass_challenge_or_login(monkeypatch):
    serve(monkeypatch, {"https://a.test/": (403, {}, b"Just a moment..."), "https://b.test/": (401, {}, b""),
                        "https://c.test/": ok(b"<html><title>Just a moment...</title><body>Enable JavaScript and cookies to continue</body></html>")})
    assert run(probe.discover("https://a.test/"))["error"]["code"] == "blocked"
    assert run(probe.discover("https://b.test/"))["error"]["code"] == "login"
    assert run(probe.discover("https://c.test/"))["error"]["code"] == "blocked"
    assert run(probe.discover("http://a.test/"))["error"]["code"] == "format"


def test_fetch_follows_redirects_only_when_asked(monkeypatch):
    serve(monkeypatch, {"https://a.test/feed": (301, {"location": "https://b.test/feed.xml"}, b""), "https://b.test/feed.xml": ok(RSS, "application/xml")})
    assert run(probe.fetch("https://a.test/feed")).url == "https://b.test/feed.xml"
    with pytest.raises(probe.ProbeError) as e:
        run(probe.fetch("https://a.test/feed", follow=False))
    assert e.value.code == "redirect" and e.value.location == "https://b.test/feed.xml"


def test_crawl_respects_robots_and_gap(monkeypatch):
    probe._crawled.clear()
    calls: list = []
    serve(monkeypatch, {"https://site.test/news/": ok(LIST_HTML.encode()), "https://site.test/robots.txt": ok(b"User-agent: *\nAllow: /\n", "text/plain")}, calls)
    items = run(probe.crawl_list("https://site.test/news/", "S", now=1000.0))
    assert len(items) == 3 and items[0]["source_key"] == "page:https://site.test/news/"
    n = len(calls)
    assert run(probe.crawl_list("https://site.test/news/", "S", now=1000.0 + 60)) == items and len(calls) == n       # 頻度の上限: 通信しない
    run(probe.crawl_list("https://site.test/news/", "S", now=1000.0 + probe.PAGE_MIN_GAP + 1))
    assert len(calls) > n
    probe._crawled.clear()
    serve(monkeypatch, {"https://site.test/news/": ok(LIST_HTML.encode()), "https://site.test/robots.txt": ok(b"User-agent: *\nDisallow: /news", "text/plain")})
    with pytest.raises(probe.ProbeError) as e:
        run(probe.crawl_list("https://site.test/news/", "S", now=1.0))
    assert e.value.code == "robots"


def test_robots_missing_allows_but_server_error_denies(monkeypatch):
    serve(monkeypatch, {"https://a.test/robots.txt": (404, {}, b""), "https://b.test/robots.txt": (503, {}, b"")})
    assert run(probe.robots_allows("https://a.test/x")) is True
    assert run(probe.robots_allows("https://b.test/x")) is False


def test_check_success_shape_and_failures(monkeypatch):
    serve(monkeypatch, {"https://site.test/feed": ok(RSS, "application/rss+xml"), "https://site.test/old": (301, {"location": "https://site.test/feed"}, b""),
                        "https://gone.test/feed": (404, {}, b""), "https://bot.test/feed": (403, {}, b""), "https://html.test/feed": ok(b"<html></html>"),
                        "https://slow.test/feed": probe.ProbeError("timeout", "15 秒以内に応答がありません"),
                        "https://dns.test/feed": probe.ProbeError("dns", "名前を解決できません: dns.test")})
    good = run(probe.check("rss", "https://site.test/feed", "S"))
    assert good["ok"] and good["count"] == 4 and good["titles"] == ["First article", "Second article", "Third article"] and good["status"] == 200 and good["ms"] >= 0
    assert good["error"] is None and good["latest"] == "2026-11-23"
    codes = {u: run(probe.check("rss", f"https://{u}.test/feed" if u != "site-old" else "https://site.test/old"))["error"]["code"]
             for u in ("gone", "bot", "html", "slow", "dns", "site-old")}
    assert codes == {"gone": "notfound", "bot": "blocked", "html": "format", "slow": "timeout", "dns": "dns", "site-old": "redirect"}
    bad = run(probe.check("rss", "https://bot.test/feed"))
    assert bad["ok"] is False and bad["error"]["status"] == 403 and "ロボット確認" in bad["error"]["message"]


def test_check_page_and_other_kinds(monkeypatch):
    serve(monkeypatch, {"https://site.test/news/": ok(LIST_HTML.encode()), "https://site.test/robots.txt": (404, {}, b"")})
    page = run(probe.check("page", "https://site.test/news/", "S"))
    assert page["ok"] and page["count"] == 3

    async def boom():
        raise sources.SkippedSource("Reddit のキーが未設定のため取得しません")
    monkeypatch.setattr(probe, "_fetcher_for", lambda kind, value: boom)
    assert run(probe.check("reddit", "LocalLLaMA"))["error"]["code"] == "skipped"

    class Err(RuntimeError):
        status = 429
    async def limited():
        raise Err("HTTP 429")
    monkeypatch.setattr(probe, "_fetcher_for", lambda kind, value: limited)
    assert run(probe.check("github", "a/b"))["error"]["code"] == "blocked"


def test_pages_config_validation_and_build_sources():
    cfg = sources.normalize({"pages": [{"name": "X", "url": "https://x.test/news/"}, {"name": "dup", "url": "https://x.test/news/"}]})
    assert cfg["pages"] == [{"name": "X", "url": "https://x.test/news/"}]
    with pytest.raises(sources.ConfigError):
        sources.normalize({"pages": [{"url": "http://x.test/"}]})
    assert sources.normalize({})["pages"] == []
    keys = [s.key for s in sources.build_sources(cfg)]
    assert "page:https://x.test/news/" in keys


def test_new_presets_are_free_https_unique_and_grouped():
    ids = [p["id"] for p in presets.PRESETS]
    assert len(ids) == len(set(ids))
    groups = {g["id"] for g in presets.GROUPS}
    assert {"xr", "dev"} <= groups
    for p in presets.PRESETS:
        assert p["group"] in groups
        if p["kind"] == "rss":
            assert p["value"].startswith("https://") and p["verified"]
    new = [p for p in presets.PRESETS if p["verified"] == presets.VERIFIED_2]
    assert len(new) >= 30
    assert not any(h in p["value"] for p in presets.PRESETS for h in ("nikkei.com/news", "bloomberg", "wsj.com"))
    sources.normalize({"rss": [{"name": p["name"], "url": p["value"]} for p in presets.PRESETS if p["kind"] == "rss"]})


def test_check_and_discover_api_for_admin(admin_client, monkeypatch):
    client = admin_client
    serve(monkeypatch, {"https://site.test/feed": ok(RSS, "application/rss+xml")})
    r = client.post("/api/v1/news/sources/check", json={"kind": "rss", "value": "https://site.test/feed"}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["count"] == 4
    r = client.post("/api/v1/news/sources/discover", json={"url": "https://site.test/feed"}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["mode"] == "feed"
    assert client.post("/api/v1/news/sources/check", json={"kind": "bogus", "value": "x"}, headers=CSRF_HEADERS).status_code == 422


LEDGE_HTML = """<html><body>
<a href="/article/20001"><img src="a.jpg" alt="Anthropicの数学者、Claudeと証明を発見したと主張のサムネイル画像">Anthropicの数学者、Claudeと共に新しい証明を発見したと主張</a>
<a href="/article/20001">Anthropicの数学者、Claudeと証明を発見したと主張のサムネイル画像</a>
<a href="/article/20002">ビジネス2026/11/16 [MON]「人類はAIを使いこなせるか」専門家が語る未来</a>
<a href="/article/20003">ビジネス2026/11/20 [FRI]AnthropicがNASDAQ上場を検討と報道</a>
<a href="/article/20004">2026/11/21</a>
<a href="/article/20004">国内の生成AI導入が進む、最新の調査結果を公開</a>
</body></html>"""


def test_list_links_cleans_headlines_ledge_style():
    """目的: 一覧の巡回の見出しから、画像の説明・先頭の分類と日付を外し、同じ URL では見出しらしいものを選ぶ。
    根拠: 2026-11-24 利用者の本番確認（Ledge.ai の見出しに「のサムネイル画像」「ビジネス2026/11/16 [MON]」が混ざった）。"""
    links = {link["url"].rsplit("/", 1)[1]: link for link in probe.list_links(LEDGE_HTML, "https://ledge.ai/")}
    assert links["20001"]["title"] == "Anthropicの数学者、Claudeと共に新しい証明を発見したと主張"
    assert links["20002"]["title"] == "「人類はAIを使いこなせるか」専門家が語る未来" and links["20002"]["published"] == "2026-11-16"
    assert links["20003"]["title"] == "AnthropicがNASDAQ上場を検討と報道" and links["20003"]["published"] == "2026-11-20"
    assert links["20004"]["title"] == "国内の生成AI導入が進む、最新の調査結果を公開" and links["20004"]["published"] == ""
    assert all("サムネイル" not in link["title"] for link in links.values())
