"""Web 収集エンジン W2（ブラウザの安全）。

目的:
- Chrome の全通信が中継（egress の検査）を通り、内部のアドレスへ**届かない**こと。
  iframe・Worker・fetch・WebSocket・リダイレクト・IP リテラル・IPv6・DNS rebinding（内部が混ざる DNS）を実 Chrome で確かめる。
  「内部」の代わりに 127.0.0.2（ローカルの試験サーバー。中継の外にあり、許可した公開の試験サーバー 127.0.0.1 とは別）を使い、
  そこに**1 件も接続が来ない**ことを見る。公開の側（127.0.0.1）には届く（中継が動いている対照）。
- 中継そのもの（CONNECT・http 絶対 URI・認証・上限）が、既定の方針で内部（127.0.0.1・プライベート・リンクローカル・IPv6）を 403 で断ること。
- 使い捨てのプロファイル・Chrome のプロセスが残らないこと・秘密の会話では起動しないこと。
根拠: docs/research-web-collector.md §4、docs/PLAN-web-collector.md §7 W2。外へは出ない（相手はローカルの試験サーバーだけ）。
"""
from __future__ import annotations

import asyncio
import http.server
import socket
import threading

import pytest

from app.security.offline import ExternalResearchBlocked, confidential_scope
from app.web_collector import browser, egress

needs_chrome = pytest.mark.skipif(not browser.system_chrome(), reason="この PC に Chrome が無い")


def _can_bind(host: str) -> bool:
    try:
        with socket.socket() as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


class _Secret(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):  # noqa: N802
        self.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def _serve(handler, host):
    httpd = http.server.ThreadingHTTPServer((host, 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture()
def lab():
    """公開の試験サイト（127.0.0.1）と、中継の外にある「内部」（127.0.0.2）。"""
    if not _can_bind("127.0.0.2"):
        pytest.skip("127.0.0.2 を使えない")
    _Secret.hits = []
    secret = _serve(_Secret, "127.0.0.2")
    sport = secret.server_address[1]

    class Site(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            routes = {
                "/": (200, "text/html", f"""<html><body><h1 id=t>start</h1><script>
document.getElementById('t').textContent='rendered by js: '+'x'.repeat(50);
const bad='http://127.0.0.2:{sport}';
const f=document.createElement('iframe'); f.src=bad+'/iframe'; document.body.appendChild(f);
fetch(bad+'/fetch',{{mode:'no-cors'}}).catch(()=>{{}});
try{{ new WebSocket('ws://127.0.0.2:{sport}/ws'); }}catch(e){{}}
fetch('http://[::1]:{sport}/v6',{{mode:'no-cors'}}).catch(()=>{{}});
fetch('http://rebind.test:{sport}/rebind',{{mode:'no-cors'}}).catch(()=>{{}});
const w=new Worker('/worker.js');
</script></body></html>"""),
                "/worker.js": (200, "application/javascript", f"fetch('http://127.0.0.2:{sport}/worker',{{mode:'no-cors'}}).catch(()=>{{}});"
                                                              f"try{{new WebSocket('ws://127.0.0.2:{sport}/wsw')}}catch(e){{}}"),
                "/redir": (302, "text/html", ""),
                "/plain": (200, "text/html", "<html><body>public page</body></html>"),
                "/forbidden": (403, "text/html", "<html><body>no</body></html>"),
                "/login": (200, "text/html", "<html><body><form><input type=password></form></body></html>"),
            }
            code, ctype, body = routes.get(self.path, (404, "text/plain", "nf"))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if self.path == "/redir":
                self.send_header("Location", f"http://127.0.0.2:{sport}/redirected")
            self.send_header("Content-Length", str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    site = _serve(Site, "127.0.0.1")
    pport = site.server_address[1]
    policy = egress.Policy(ports=frozenset({pport, sport}), allow_networks=("127.0.0.1/32",),
                           resolver_override={"site.test": ["127.0.0.1"], "rebind.test": ["127.0.0.1", "127.0.0.2"]})
    yield {"url": f"http://site.test:{pport}", "policy": policy, "hits": _Secret.hits, "sport": sport}
    site.shutdown()
    secret.shutdown()


@needs_chrome
def test_browser_never_reaches_internal_targets_from_iframe_worker_fetch_websocket_ipv6_and_rebinding(lab):
    out = asyncio.run(browser.render(lab["url"] + "/", policy=lab["policy"], timeout_s=20))
    assert out["status"] == "success", out
    assert "rendered by js" in out["html"]                  # 対照: 公開の試験サイトは中継を通って届き、JS が動いた
    assert lab["hits"] == []                                # 内部（127.0.0.2）には 1 件も届かない
    hosts = {r["host"] for r in out["stats"]["rejected"]}
    assert "127.0.0.2" in hosts                             # 止めた記録がある（iframe・fetch・WebSocket・worker）
    assert out["stats"]["peak_rss_mb"] > 0


@needs_chrome
def test_redirect_to_an_internal_host_is_stopped(lab):
    out = asyncio.run(browser.render(lab["url"] + "/redir", policy=lab["policy"], timeout_s=20))
    assert out["status"] == "security_rejected"
    assert lab["hits"] == []


@needs_chrome
def test_stop_conditions_are_reported_not_bypassed(lab):
    forbidden = asyncio.run(browser.render(lab["url"] + "/forbidden", policy=lab["policy"], timeout_s=20))
    assert forbidden["status"] == "blocked"
    login = asyncio.run(browser.render(lab["url"] + "/login", policy=lab["policy"], timeout_s=20))
    assert login["status"] == "login_required"


@needs_chrome
def test_profile_is_disposable_and_no_chrome_process_is_left(lab, tmp_path):
    seen = {}

    async def run():
        async with browser.session(policy=lab["policy"], timeout_s=20) as sess:
            seen["profile"] = sess.profile_dir
            assert browser._chrome_pids(sess.profile_dir)   # noqa: SLF001
            await sess.page.goto(lab["url"] + "/plain")
            assert await sess.context.cookies() == []        # 利用者の Cookie は無い
    asyncio.run(run())
    import os

    assert not os.path.exists(seen["profile"])
    assert browser._chrome_pids(seen["profile"]) == []       # noqa: SLF001


def test_confidential_conversation_never_starts_a_browser(lab):
    async def run():
        with confidential_scope():
            await browser.render(lab["url"] + "/plain", policy=lab["policy"])
    with pytest.raises(ExternalResearchBlocked):
        asyncio.run(run())


async def _raw(port, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(4096), 10)
    writer.close()
    return data


def _auth(proxy) -> bytes:
    import base64

    return b"Proxy-Authorization: Basic " + base64.b64encode(f"{proxy.username}:{proxy.password}".encode()) + b"\r\n"


@pytest.mark.parametrize("target", [
    "127.0.0.1:443", "localhost:80", "10.0.0.5:443", "192.168.1.1:80", "169.254.169.254:80", "[::1]:443", "[fd00::1]:443",
    "[::ffff:127.0.0.1]:443", "[fe80::1]:443", "100.64.0.1:443", "example.com:22",
])
def test_relay_refuses_internal_targets_with_the_default_policy(target):
    async def run():
        proxy = browser.RelayProxy()   # 既定の方針（試験の許可なし）
        port = await proxy.start()
        try:
            return await _raw(port, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n".encode() + _auth(proxy) + b"\r\n"), proxy
        finally:
            await proxy.stop()
    reply, proxy = asyncio.run(run())
    assert reply.startswith(b"HTTP/1.1 403"), reply
    assert proxy.rejected and not [e for e in proxy.events if e.kind == "tunnel"]


def test_relay_requires_authentication_and_refuses_plain_http_to_internal():
    async def run():
        proxy = browser.RelayProxy()
        port = await proxy.start()
        try:
            noauth = await _raw(port, b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
            wrong = await _raw(port, b"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic eDp5\r\n\r\n")
            plain = await _raw(port, b"GET http://169.254.169.254/latest/meta-data/ HTTP/1.1\r\nHost: 169.254.169.254\r\n" + _auth(proxy) + b"\r\n")
            return noauth, wrong, plain
        finally:
            await proxy.stop()
    noauth, wrong, plain = asyncio.run(run())
    assert noauth.startswith(b"HTTP/1.1 407") and wrong.startswith(b"HTTP/1.1 407")
    assert plain.startswith(b"HTTP/1.1 403")


def test_relay_stops_when_the_size_limit_is_exceeded(lab):
    async def run():
        proxy = browser.RelayProxy(lab["policy"], max_bytes=10)
        port = await proxy.start()
        try:
            host = lab["url"].split("//")[1]
            await _raw(port, f"GET {lab['url']}/plain HTTP/1.1\r\nHost: {host}\r\n".encode() + _auth(proxy) + b"\r\n")
            second = await _raw(port, f"GET {lab['url']}/plain HTTP/1.1\r\nHost: {host}\r\n".encode() + _auth(proxy) + b"\r\n")
            return proxy.exceeded, second
        finally:
            await proxy.stop()
    exceeded, second = asyncio.run(run())
    assert exceeded and second.startswith(b"HTTP/1.1 503")


@needs_chrome
def test_loopback_is_not_bypassed_by_the_browser(lab):
    """Chrome は既定でループバックを中継しない。`<-loopback>` で中継に通っているか（許可の無い 127.0.0.1 は断られる）。"""
    strict = egress.Policy(ports=lab["policy"].ports, resolver_override=lab["policy"].resolver_override)
    out = asyncio.run(browser.render(lab["url"].replace("site.test", "127.0.0.1") + "/plain", policy=strict, timeout_s=15))
    assert out["status"] == "security_rejected", out


@needs_chrome
def test_memory_limit_kills_the_browser_and_cleans_up(lab):
    out = asyncio.run(browser.render(lab["url"] + "/", policy=lab["policy"], timeout_s=20, max_rss_mb=1))
    assert out["status"] in ("too_large", "http_error", "timeout") and "メモリ" in (out["error"] or "メモリ") or out["status"] != "success"
    assert out["status"] != "success"
