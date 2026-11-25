"""W2: 静的 → 初期 JSON → ブラウザの自動の切り替え（fetch.collect の render="auto"）と robots.txt の確認。

目的:
- JS が要るページは、機能「Web 収集のブラウザ」が有効なときだけブラウザで取る（実 Chrome。ローカルの試験サーバー）。無効なら警告つきの静的の結果。
- 初期 JSON に本文があればブラウザを起こさず取る。静的で足りるページではブラウザを起こさない。
- robots.txt が禁じたページ・403・ログイン・CAPTCHA は止まり、状態と理由を返す（別の経路で取り直さない）。
- robots.txt の解釈（Allow/Disallow の最長一致・ワイルドカード・5xx は拒否扱い）。
根拠: docs/PLAN-web-collector.md §7 W2、§6。外へは出ない。
"""
from __future__ import annotations

import asyncio
import http.server
import json
import threading

import pytest

from app.web_collector import browser, egress, fetch, robots

pytestmark = pytest.mark.skipif(not browser.system_chrome(), reason="この PC に Chrome が無い")

BODY = " ".join(f"Measured sentence {i} about gallium nitride switching loss in a half bridge converter." for i in range(20))
SHELL = '<html><head><title>spa</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
ROUTES = {
    "/spa": (200, "text/html", SHELL.replace("/app.js", "/render.js")),
    "/render.js": (200, "application/javascript", f"document.getElementById('root').innerHTML='<article><h1>Rendered</h1><p>{BODY}</p></article>';"),
    "/static": (200, "text/html", f"<html><body><article><h1>S</h1><p>{BODY} {BODY}</p></article><script src='/x.js'></script></body></html>"),
    "/jsondata": (200, "text/html", '<html><body><div id="__next"></div><script id="__NEXT_DATA__" type="application/json">'
                  + json.dumps({"props": {"post": {"body": BODY}}}) + "</script></body></html>"),
    "/private/spa": (200, "text/html", SHELL.replace("/app.js", "/render.js")),
    "/login": (200, "text/html", '<html><body><div id="root"></div><form><input type="password"></form><script src="/a.js"></script></body></html>'),
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private/\n"),
}


class _H(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):  # noqa: N802
        self.hits.append(self.path)
        code, ctype, body = ROUTES.get(self.path, (404, "text/plain", "nf"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


@pytest.fixture()
def site(monkeypatch):
    _H.hits = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    robots._cache.clear()  # noqa: SLF001
    policy = egress.Policy(ports=frozenset({port}), allow_networks=("127.0.0.1/32",), resolver_override={"site.test": ["127.0.0.1"]})
    launches = []
    real = browser.render

    async def counted(*a, **k):
        launches.append(a[0])
        return await real(*a, **k)
    monkeypatch.setattr(browser, "render", counted)
    yield {"base": f"http://site.test:{port}", "policy": policy, "launches": launches}
    httpd.shutdown()


def _collect(site, path, **kw):
    return asyncio.run(fetch.collect(site["base"] + path, policy=site["policy"], render="auto", **kw))


def test_js_page_is_rendered_by_the_browser_when_the_feature_is_enabled(site, monkeypatch):
    monkeypatch.setattr(browser, "is_enabled", lambda: True)
    page = _collect(site, "/spa")
    assert page["status"] == "success" and page["fetch_method"] == "browser", page
    assert "gallium nitride" in page["markdown"]
    assert page["browser"]["peak_rss_mb"] > 0 and page["js_check"]["needs_js"]


def test_without_the_feature_the_static_result_is_returned_with_a_warning(site):
    page = _collect(site, "/spa")
    assert page["fetch_method"] == "http" and site["launches"] == []
    assert any("Web 収集のブラウザ" in w for w in page["warnings"])


def test_initial_json_is_used_without_starting_the_browser(site, monkeypatch):
    monkeypatch.setattr(browser, "is_enabled", lambda: True)
    page = _collect(site, "/jsondata")
    assert page["status"] == "success" and page["fetch_method"] == "data_api" and "gallium nitride" in page["markdown"]
    assert site["launches"] == []


def test_static_sufficient_page_never_starts_the_browser(site, monkeypatch):
    monkeypatch.setattr(browser, "is_enabled", lambda: True)
    page = _collect(site, "/static")
    assert page["fetch_method"] == "http" and site["launches"] == []


def test_render_never_is_the_default_for_other_callers(site, monkeypatch):
    monkeypatch.setattr(browser, "is_enabled", lambda: True)
    page = asyncio.run(fetch.collect(site["base"] + "/spa", policy=site["policy"]))
    assert page["fetch_method"] == "http" and site["launches"] == []


def test_robots_denied_page_is_not_opened_in_the_browser(site, monkeypatch):
    monkeypatch.setattr(browser, "is_enabled", lambda: True)
    page = _collect(site, "/private/spa")
    assert page["status"] == "robots_denied" and "robots.txt" in page["error"]
    assert site["launches"] == []


def test_login_page_stops_with_a_reason(site, monkeypatch):
    monkeypatch.setattr(browser, "is_enabled", lambda: True)
    page = _collect(site, "/login")
    assert page["status"] == "login_required" and "ログイン" in page["error"]


def test_read_page_uses_the_browser_for_js_pages(site, monkeypatch):
    from app.workflows import research_tools

    monkeypatch.setattr(browser, "is_enabled", lambda: True)
    real = fetch.collect
    monkeypatch.setattr(fetch, "collect", lambda url, **kw: real(url, **{**kw, "policy": site["policy"]}))
    out = asyncio.run(research_tools.read_page(site["base"] + "/spa"))
    assert out["ok"] and "gallium nitride" in out["text"]


def test_robots_rules_longest_match_wildcards_and_failures():
    text = "User-agent: *\nDisallow: /a/\nAllow: /a/ok\nDisallow: /*.pdf$\n\nUser-agent: ControlDeck-WebCollector\nDisallow: /me/\n"
    # 自分の名前のグループがあれば * は見ない
    assert robots.allowed(text, "/a/x") and not robots.allowed(text, "/me/x")
    star = "User-agent: *\nDisallow: /a/\nAllow: /a/ok\nDisallow: /*.pdf$\nDisallow:\n"
    assert not robots.allowed(star, "/a/x") and robots.allowed(star, "/a/ok") and not robots.allowed(star, "/d/file.pdf")
    assert robots.allowed(star, "/d/file.pdf?x=1") and robots.allowed("", "/anything")


def test_robots_5xx_means_not_allowed_and_4xx_means_allowed(monkeypatch):
    class Srv(http.server.BaseHTTPRequestHandler):
        code = 500

        def do_GET(self):  # noqa: N802
            self.send_response(self.code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Srv)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    policy = egress.Policy(ports=frozenset({port}), allow_networks=("127.0.0.1/32",))
    try:
        robots._cache.clear()  # noqa: SLF001
        ok, reason = asyncio.run(robots.check(f"http://127.0.0.1:{port}/x", policy=policy))
        assert not ok and "確かめられません" in reason
        robots._cache.clear()  # noqa: SLF001
        Srv.code = 404
        assert asyncio.run(robots.check(f"http://127.0.0.1:{port}/x", policy=policy))[0]
    finally:
        httpd.shutdown()
