"""調査の道具の新しい入口（Reddit の OAuth・Discourse 系のフォーラム・Stack Exchange）と、設定 → API キーの「調査」群のテスト
（HTTP はすべてモック。外へは通信しない）。

目的と根拠: 2026-11-23 利用者「Reddit の API を登録するところを作って、入っていたらそれを使って検索できるように。XDA や他の技術的に有望な
フォーラムも検索したい」。2026-11-22 の実測で Reddit の未認証の JSON・RSS は拒否された。守ること: キーがあれば OAuth を先に使いトークンを
使い回す・キーが無い／失敗なら今の経路へ・403 は「承認が要る可能性」と平易に言う・キーの値を応答・例外・ログ・監査に出さない・
ドメインは形だけ許す（localhost・IP は断る）・秘密の会話は外へ出ない・Stack Exchange は site 名に直してキーを付ける。"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from app.security.offline import confidential_scope
from app.workflows import research_keys as rk
from app.workflows import research_tools as rt
from tests.conftest import CSRF_HEADERS

API = "/api/v1/settings/keys"
SECRET = "reddit-secret-ZZZ-9876"


def run(coro):
    return asyncio.run(coro)


def mock_http(monkeypatch, handler):
    monkeypatch.setattr(rt, "_client", lambda accept="application/json": httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"Accept": accept, "User-Agent": "browser-ua"}))


@pytest.fixture(autouse=True)
def _fresh_keys():
    """データの置き場は全テストで共有なので、キーとトークンを前後で消す（他のテストへ漏らさない）。"""
    def clear():
        rt._reddit_token.clear()
        (rk._path()).unlink(missing_ok=True)  # noqa: SLF001
    clear()
    yield
    clear()


def reddit_keys():
    rk.set_keys({"reddit_client_id": "client-id-1234", "reddit_client_secret": SECRET, "reddit_username": "someone"})


POSTS = {"data": {"children": [{"data": {"title": "OAuth の結果", "permalink": "/r/LocalLLaMA/comments/abc/q/", "score": 5,
                                          "num_comments": 2, "subreddit": "LocalLLaMA", "created_utc": 1795000000, "selftext": "本文"}}]}}


def test_reddit_oauth_token_is_cached_and_search_uses_the_api(monkeypatch):
    reddit_keys()
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/v1/access_token":
            assert request.headers["authorization"].startswith("Basic ") and "grant_type=client_credentials" in request.content.decode()
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        assert request.url.host == "oauth.reddit.com" and request.headers["authorization"] == "bearer tok-1"
        assert request.headers["user-agent"] == "linux:controldeck-research:1.0 (by /u/someone)"
        return httpx.Response(200, json=POSTS)

    mock_http(monkeypatch, handler)
    out = run(rt.reddit_search("qwen", "LocalLLaMA", "month", 5))
    run(rt.reddit_search("qwen", "", "", 5))
    assert out[0]["title"] == "OAuth の結果"
    assert [r.url.path for r in seen].count("/api/v1/access_token") == 1          # トークンは使い回す
    assert seen[1].url.path == "/r/LocalLLaMA/search" and "restrict_sr=1" in str(seen[1].url) and "raw_json=1" in str(seen[1].url)
    # 期限の 60 秒前を過ぎたら取り直す
    rt._reddit_token["until"] = 0
    run(rt.reddit_search("qwen", "", "", 5))
    assert [r.url.path for r in seen].count("/api/v1/access_token") == 2


def test_reddit_thread_uses_oauth_and_without_keys_the_old_path_is_used(monkeypatch):
    thread = [{"data": {"children": [{"data": {"title": "T", "subreddit": "x", "score": 1, "num_comments": 1, "selftext": "本文", "created_utc": 1795000000}}]}},
              {"data": {"children": [{"kind": "t1", "data": {"body": "コメント", "score": 3, "replies": ""}}]}}]
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.path == "/api/v1/access_token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.host == "oauth.reddit.com":
            assert request.url.path == "/comments/abc"
            return httpx.Response(200, json=thread)
        return httpx.Response(200, json=POSTS)

    mock_http(monkeypatch, handler)
    assert run(rt.reddit_search("q", "", "", 5))[0]["title"] == "OAuth の結果" and hosts == ["www.reddit.com"]   # キーなし: 未認証
    reddit_keys()
    hosts.clear()
    assert "コメント" in run(rt.read_page("https://www.reddit.com/r/x/comments/abc/t/", "", 3000))["text"]
    assert "oauth.reddit.com" in hosts


def test_reddit_oauth_failure_falls_back_and_403_is_explained(monkeypatch):
    reddit_keys()
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.path == "/api/v1/access_token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403, text="blocked") if request.url.host == "oauth.reddit.com" else httpx.Response(200, json=POSTS)

    mock_http(monkeypatch, handler)
    assert run(rt.reddit_search("q", "", "", 5))[0]["title"] == "OAuth の結果"        # OAuth が 403 → 未認証の経路
    ok, message = run(rt.check_reddit())
    assert not ok and "承認" in message and SECRET not in message
    for status, word in ((401, "Client"), (429, "待って")):
        mock_http(monkeypatch, lambda request, s=status: httpx.Response(s, json={}))
        ok, message = run(rt.check_reddit())
        assert not ok and word in message
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"access_token": "t", "expires_in": 3600}) if request.url.path.endswith("access_token") else httpx.Response(200, json=POSTS))
    assert run(rt.check_reddit())[0]


def test_keys_never_appear_in_errors_or_logs(monkeypatch, caplog):
    reddit_keys()
    rk.set_keys({"stackexchange_key": "se-key-SECRETVALUE"})

    def boom(request):
        raise httpx.ConnectError(f"failed {request.url}")      # URL（SE のキーは query にある）を含む例外

    mock_http(monkeypatch, boom)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError) as err:
        run(rt.stackexchange_search("stackoverflow.com", "q", "", 5))
    assert "SECRETVALUE" not in str(err.value) and "SECRETVALUE" not in repr(err.value.__cause__)
    ok, message = run(rt.check_stackexchange())
    assert not ok and "SECRETVALUE" not in message
    ok, message = run(rt.check_reddit())
    assert not ok and SECRET not in message
    assert "SECRETVALUE" not in caplog.text and SECRET not in caplog.text


def test_discourse_search_and_read(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/search.json":
            return httpx.Response(200, json={"topics": [{"id": 77, "title": "ROCm の質問", "slug": "rocm-q", "posts_count": 4, "like_count": 3,
                                                          "created_at": "2026-11-01T00:00:00Z"}],
                                             "posts": [{"topic_id": 77, "blurb": "gfx1201 で動いた"}]})
        assert request.url.path == "/t/77.json"
        return httpx.Response(200, json={"title": "ROCm の質問", "posts_count": 2, "created_at": "2026-11-01T00:00:00Z", "post_stream": {"posts": [
            {"username": "a", "cooked": "<p>質問の本文</p>"}, {"username": "b", "cooked": "<p>答えは <code>HSA_OVERRIDE</code></p>"}]}})

    mock_http(monkeypatch, handler)
    out = run(rt.discourse_search("discuss.huggingface.co", "rocm", "week", 5))
    assert out[0]["url"] == "https://discuss.huggingface.co/t/rocm-q/77" and "gfx1201" in out[0]["snippet"]
    assert "after%3A" in seen[0]                                                              # time_range は after:YYYY-MM-DD
    async def searx(*a):
        raise AssertionError("SearXNG は不要")
    found = run(rt.search_web("rocm", ["discuss.huggingface.co"], "", 1, searx=searx))
    assert found["results"][0]["title"] == "ROCm の質問"
    page = run(rt.read_page("https://discuss.huggingface.co/t/rocm-q/77/2", "", 3000))
    assert page["ok"] and "質問の本文" in page["text"] and "HSA_OVERRIDE" in page["text"]


def test_user_added_forum_is_searched_and_bad_domains_are_refused(monkeypatch):
    rk.set_extra_forums(["https://Forum.Example.com/x", "forum.example.com"])
    assert rk.extra_forums() == ["forum.example.com"] and "forum.example.com" in rk.forums() and "forum.level1techs.com" in rk.forums()
    assert rt._kind("forum.example.com") == "discourse" and rt._kind("xdaforums.com") == "generic"
    for bad in ("localhost", "127.0.0.1", "10.0.0.5", "intranet", "a b.com", "evil.local", "foo_bar.com", "-x.com"):
        with pytest.raises(rk.ResearchKeyError):
            rk.valid_domain(bad)


def test_stackexchange_site_names_key_and_read(monkeypatch):
    assert [rt._se_site(d) for d in ("stackoverflow.com", "askubuntu.com", "mathoverflow.net", "unix.stackexchange.com", "stackexchange.com",
                                      "example.com")] == ["stackoverflow", "askubuntu", "mathoverflow.net", "unix", "", ""]
    seen = []

    def handler(request):
        seen.append(request.url)
        if request.url.path.endswith("/search/advanced"):
            return httpx.Response(200, json={"items": [{"title": "What&#39;s ROCm?", "link": "https://unix.stackexchange.com/q/1", "score": 4,
                                                         "answer_count": 2, "is_answered": True, "tags": ["rocm"], "creation_date": 1795000000}]})
        if request.url.path.endswith("/answers"):
            assert request.url.params["sort"] == "votes" and request.url.params["filter"] == "withbody"
            return httpx.Response(200, json={"items": [{"score": 9, "is_accepted": True, "body": "<p>採用された回答</p>"}, {"score": 1, "body": "<p>別の回答</p>"}]})
        return httpx.Response(200, json={"items": [{"title": "Q", "score": 2, "answer_count": 2, "tags": ["a"], "body": "<p>質問本文</p>", "creation_date": 1795000000}]})

    mock_http(monkeypatch, handler)
    out = run(rt.stackexchange_search("unix.stackexchange.com", "rocm", "", 5))
    assert out[0]["title"] == "What's ROCm?" and seen[0].params["site"] == "unix" and "key" not in seen[0].params   # キーなしは付けない
    rk.set_keys({"stackexchange_key": "kkk-1234567890"})
    run(rt.stackexchange_search("stackoverflow.com", "rocm", "month", 5))
    assert seen[1].params["key"] == "kkk-1234567890" and seen[1].params["site"] == "stackoverflow" and "fromdate" in seen[1].params
    page = run(rt.read_page("https://stackoverflow.com/questions/123/foo", "", 3000))
    assert page["ok"] and page["text"].index("採用された回答") < page["text"].index("別の回答") and "質問本文" in page["text"]


def test_xda_stays_on_searxng_and_confidential_conversations_do_not_connect(monkeypatch):
    touched = []
    mock_http(monkeypatch, lambda request: touched.append(request) or httpx.Response(200, json={}))
    called = []

    async def searx(query, limit, time_range, pageno):
        called.append(query)
        return [{"title": "XDA", "url": "https://xdaforums.com/t/1/", "snippet": ""}]

    found = run(rt.search_web("pixel root", ["xdaforums.com"], "", 3, searx=searx))
    assert found["results"][0]["title"] == "XDA" and called[0].startswith("site:xdaforums.com") and touched == []
    with confidential_scope():
        for sites in (["discuss.pytorch.org"], ["stackoverflow.com"], ["reddit.com"]):
            from app.security.offline import ExternalResearchBlocked
            with pytest.raises(ExternalResearchBlocked):
                run(rt.search_web("x", sites, "", 3, searx=searx))
        ok, message = run(rt.check_stackexchange())
        assert not ok
    assert touched == []


# ---- 設定 → API キー ----

def test_settings_group_save_test_and_delete(admin_client, monkeypatch):
    body = admin_client.get(API).json()
    assert "research" in {g["id"] for g in body["groups"]}
    keys = {k["id"]: k for k in body["keys"]}
    assert not keys["research:reddit"]["configured"] and "https://www.reddit.com/prefs/apps" in keys["research:reddit"]["note"]
    assert [f["name"] for f in keys["research:reddit"]["fields"]] == ["reddit_client_id", "reddit_client_secret", "reddit_username"]

    r = admin_client.put(f"{API}/research:reddit", json={"values": {"reddit_client_id": "client-id-1234", "reddit_client_secret": SECRET}}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and not {k["id"]: k for k in r.json()["keys"]}["research:reddit"]["configured"]      # 3 つそろうまで未完
    r = admin_client.put(f"{API}/research:reddit", json={"values": {"reddit_username": "someone"}}, headers=CSRF_HEADERS)
    reddit = {k["id"]: k for k in r.json()["keys"]}["research:reddit"]
    assert reddit["configured"] and reddit["hint"] == "••••1234" and SECRET not in str(r.json())
    assert admin_client.put(f"{API}/research:reddit", json={"values": {"nope": "x"}}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(f"{API}/research:zzz", json={"values": {"a": "x"}}, headers=CSRF_HEADERS).status_code == 404

    def handler(request):
        if request.url.path == "/api/v1/access_token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json=POSTS)

    mock_http(monkeypatch, handler)
    t = admin_client.post(f"{API}/research:reddit/test", headers=CSRF_HEADERS).json()
    assert t["ok"] and SECRET not in str(t)
    mock_http(monkeypatch, lambda request: httpx.Response(403, text="no"))
    t = admin_client.post(f"{API}/research:reddit/test", headers=CSRF_HEADERS).json()
    assert not t["ok"] and "承認" in t["message"]
    assert admin_client.post(f"{API}/cloud:openrouter/test", headers=CSRF_HEADERS).status_code == 404

    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"quota_remaining": 290, "quota_max": 300}))
    assert "290" in admin_client.post(f"{API}/research:stackexchange/test", headers=CSRF_HEADERS).json()["message"]

    r = admin_client.put(f"{API}/research:forums", json={"values": {"domains": "forum.example.com, discuss.example.org"}}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and rk.extra_forums() == ["forum.example.com", "discuss.example.org"]
    assert admin_client.put(f"{API}/research:forums", json={"values": {"domains": "localhost"}}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(f"{API}/research:forums", json={"values": {"domains": "192.168.0.1"}}, headers=CSRF_HEADERS).status_code == 422

    for kid in ("research:reddit", "research:stackexchange", "research:forums"):
        assert admin_client.delete(f"{API}/{kid}", headers=CSRF_HEADERS).status_code == 200
    assert rk.reddit() is None and rk.extra_forums() == []
    # 値は保存ファイルにも平文で残らない
    import app.config as cfg
    rk.set_keys({"reddit_client_secret": SECRET})
    assert SECRET not in (cfg.data_dir() / "research" / "keys.json").read_text(encoding="utf-8")
