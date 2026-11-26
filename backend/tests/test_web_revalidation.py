"""W5c: conditional GET tests; mock transport only, no external traffic."""
import asyncio

import httpx
import pytest
from app.web_collector import fetch, egress, revalidation

URL = "https://example.test/article"
PUBLIC = {"cache-control": "public, max-age=60", "etag": '"v1"', "content-type": "text/plain"}


def run_sequence(monkeypatch, replies, *, options=None, urls=None):
    requests = []
    queue = iter(replies)

    def handler(request):
        requests.append(request)
        status, headers, body = next(queue)
        return httpx.Response(status, headers=headers, content=body)

    def client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 headers=kwargs["headers"], follow_redirects=False)
    monkeypatch.setattr(egress, "guarded_client", client)
    cache = revalidation.RevalidationCache()

    async def run():
        return [await fetch.collect(url, revalidation_cache=cache, **kw)
                for url, kw in zip(urls or [URL]*len(replies), options or [{}]*len(replies))]

    return asyncio.run(run()), requests


def test_revalidation_304_keeps_original_snapshot(monkeypatch):
    pages, requests = run_sequence(monkeypatch, [(200,PUBLIC,b"first"),(304,PUBLIC,b""),(304,PUBLIC,b""),
                                               (200,{**PUBLIC,"etag":'"v2"'},b"second")])
    assert "if-none-match" not in requests[0].headers
    assert all(r.headers["if-none-match"] == '"v1"' for r in requests[1:])
    assert pages[1]["cache_reused"] and pages[2]["cache_reused"]
    assert pages[1]["fetched_at"] == pages[0]["fetched_at"]
    assert pages[1]["content_sha256"] == pages[0]["content_sha256"]
    assert pages[1]["revalidation_http_status"] == 304
    assert pages[-1]["markdown"] == "second"
    assert pages[-1]["content_sha256"] != pages[0]["content_sha256"]


@pytest.mark.parametrize("header", [
    {"cache-control":"private"}, {"cache-control":"public,no-store"}, {"vary":"Accept-Language"},
    {"set-cookie":"session=secret"}, {"www-authenticate":"Basic"}, {"content-range":"bytes 0-4/5"},
    {"cache-control":'extension="x,public,y"'},
])
def test_ineligible_not_reused(monkeypatch, header):
    pages, requests = run_sequence(monkeypatch, [(200,{**PUBLIC,**header},b"first"),(200,PUBLIC,b"next")])
    assert "if-none-match" not in requests[1].headers
    assert pages[1]["markdown"] == "next"


@pytest.mark.parametrize("status,headers", [(403,{}),(429,{}),(503,{}),
    (304,{"cache-control":"private","etag":'"v1"'}),(304,{"cache-control":"public","etag":'"wrong"'}),
    (304,{"cache-control":"public"})])
def test_no_stale_fallback(monkeypatch, status, headers):
    pages, requests = run_sequence(monkeypatch, [(200,PUBLIC,b"first"),(status,headers,b""),(200,PUBLIC,b"next")])
    assert pages[1]["status"] != "success"
    assert not pages[1]["markdown"]
    assert "if-none-match" not in requests[2].headers


def test_exact_url_and_options_are_isolated(monkeypatch):
    pages, requests = run_sequence(monkeypatch, [(200,PUBLIC,b"first"),(200,PUBLIC,b"next"),(200,PUBLIC,b"third")],
                                  options=[{},{"limit_chars":2},{}], urls=[URL,URL,URL+"?token=secret"])
    assert all("if-none-match" not in r.headers for r in requests)
    assert pages[1]["markdown"] == "ne"


def test_redirect_drops_validator(monkeypatch):
    pages, requests = run_sequence(monkeypatch, [(200,PUBLIC,b"first"),(302,{"location":"/elsewhere"},b""),
                                  (200,PUBLIC,b"second"),(200,PUBLIC,b"third")],
                                  urls=[URL,URL,URL],options=[{}, {}, {}])
    assert requests[1].headers["if-none-match"] == '"v1"'
    assert "if-none-match" not in requests[2].headers and "if-none-match" not in requests[3].headers
    assert pages[1]["final_url"].endswith("/elsewhere")


def test_cache_is_not_global(monkeypatch):
    for _ in range(2):
        _, requests = run_sequence(monkeypatch, [(200,PUBLIC,b"first")])
        assert "if-none-match" not in requests[0].headers
