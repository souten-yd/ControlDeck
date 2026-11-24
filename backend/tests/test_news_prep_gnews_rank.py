"""目的: ニュースの Google ニュースの転送の解き方（手順の単体。中身はモック）と、おすすめの順位（チャンネルに合う記事が上）と、画像生成が下ごしらえの描写を使うことの試験。外へ通信しない。

根拠: 2026-11-24 利用者「Google ニュースの記事はページの画像が 0 件」「ON AIR とホームの 1 位が Bored.com（HN のトップ）でチャンネルに合わない」「日本語の題の記事は決まり文句に落ち、3 件が同じ地球儀の絵になった」。
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote

import httpx
import pytest
from sqlalchemy import delete

from app.news import channels, gnews, home, imagegen, profile, sources, store
from app.news import db as news_db
from app.news.db import NewsAction, NewsItem
from tests.test_news import add, user_id

NOW = datetime.now(timezone.utc)


def _legacy_id(url: str) -> str:
    raw = b"\x08\x13\x22" + bytes([len(url)]) + url.encode() + b"\xd2\x01\x00"
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_gnews_legacy_id_is_decoded_offline_and_new_style_is_not():
    url = "https://example.com/news/article-1?x=1"
    gid = _legacy_id(url)
    assert gnews.article_id(f"https://news.google.com/rss/articles/{gid}?oc=5") == gid
    assert gnews.decode_legacy(gid) == url
    new_style = base64.urlsafe_b64encode(b"\x08\x13\x22" + bytes([20]) + b"AU_yqLabcdefghijklmn" + b"\xd2\x01\x00").decode().rstrip("=")
    assert gnews.decode_legacy(new_style) is None
    assert gnews.decode_legacy("!!!notbase64") is None
    assert gnews.article_id("https://example.com/articles/abc") is None and gnews.article_id("https://news.google.com/foo") is None


def test_gnews_signature_body_and_response_parsing():
    html = '<c-wiz><div jscontroller="x" data-n-a-sg="SIG123" data-n-a-ts="1700000000"></div></c-wiz>'
    assert gnews.parse_signature(html) == ("SIG123", "1700000000") and gnews.parse_signature("<html></html>") is None
    body = gnews.build_body("AU_yqLxyz", "SIG123", "1700000000")
    req = json.loads(unquote(parse_qs(body)["f.req"][0]))
    assert req[0][0][0] == "Fbv4je"
    inner = json.loads(req[0][0][1])
    assert inner[0] == "garturlreq" and inner[2] == "AU_yqLxyz" and inner[3] == 1700000000 and inner[4] == "SIG123"
    payload = json.dumps(["garturlres", "https://real.test/a", 1])
    text = ")]}'\n\n123\n" + json.dumps([["wrb.fr", "Fbv4je", payload, None, None, None, "1"], ["di", 5], ["af.httprm", 5, "x", 1]]) + "\n25\n[[\"e\",4,null,null,131]]"
    assert gnews.parse_response(text) == "https://real.test/a"
    assert gnews.parse_response(")]}'\n\n12\n[[\"e\",4]]") is None
    loop = json.dumps(["garturlres", "https://news.google.com/x", 1])
    assert gnews.parse_response(json.dumps([["wrb.fr", "Fbv4je", loop]])) is None          # 転送先がまた Google ニュースなら使わない


def test_gnews_resolve_new_style_does_two_requests_and_gives_up_on_failure():
    gid = "AU_yqL" + "x" * 30
    page = '<div data-n-a-sg="SIG" data-n-a-ts="42"></div>'
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, text=page)
        assert b"SIG" in request.content or "SIG" in unquote(request.content.decode())
        return httpx.Response(200, text=")]}'\n\n9\n" + json.dumps([["wrb.fr", "Fbv4je", json.dumps(["garturlres", "https://real.test/a", 1])]]))

    def client(h):
        return httpx.AsyncClient(transport=httpx.MockTransport(h))

    got = asyncio.run(gnews.resolve(f"https://news.google.com/rss/articles/{gid}?oc=5", client(handler)))
    assert got == "https://real.test/a" and [m for m, _ in seen] == ["GET", "POST"] and seen[0][1].startswith("https://news.google.com/rss/articles/")
    assert asyncio.run(gnews.resolve(f"https://news.google.com/rss/articles/{gid}", client(lambda r: httpx.Response(429)))) is None
    assert asyncio.run(gnews.resolve(f"https://news.google.com/rss/articles/{gid}", client(lambda r: httpx.Response(200, text="<html>no sig</html>")))) is None
    assert asyncio.run(gnews.resolve("https://example.com/x")) is None


def test_gnews_resolve_refuses_private_addresses_with_real_guard():
    """実物の guarded_client を使う（Google の API が私的な IP を返しても、後段の取得で断る）。ここでは通信せず legacy の解きだけ確かめる。"""
    gid = _legacy_id("http://127.0.0.1/secret")
    assert asyncio.run(gnews.resolve(f"https://news.google.com/rss/articles/{gid}")) == "http://127.0.0.1/secret"
    from app.news import images
    assert asyncio.run(images.fetch_page_image("http://127.0.0.1/secret")) is None


# ---- 順位 ----

@pytest.fixture
def clean(client, tmp_path, monkeypatch):
    monkeypatch.setattr(channels, "_path", lambda: tmp_path / "news" / "channels.json")
    with news_db.session() as db:
        db.execute(delete(NewsAction)); db.execute(delete(NewsItem)); db.commit()
    yield


def test_channel_fit_beats_a_newer_unrelated_article_in_ranking_and_onair(clean):
    """目的: おすすめの点でチャンネルに合う記事を強く上げ、合わない記事（HN のトップなど）は下げる（消さない）。ON AIR は合う記事から選ぶ。新着の並びは変えない。
    根拠: 2026-11-24 利用者の本番: ON AIR とホームの 1 位が Bored.com（HN のトップ）だった。"""
    uid = user_id("rank-user")
    off = add("https://hn.test/bored", title="Bored.com: fun websites to explore when bored", source_key="hn:top", published=NOW)
    mine = add("https://a.test/qwen", title="Qwen 新モデルが llama.cpp に対応", published=NOW - timedelta(days=1))
    with news_db.session() as db:
        rows = db.query(NewsItem).all()
        ranked = profile.rank(db, uid, rows, store.states(db, uid, [r.id for r in rows]), NOW)
        assert [r.id for r, _ in ranked] == [mine, off] and len(ranked) == 2          # 消さない
        data = home.build(db, uid, NOW)
        assert data["onair"]["id"] == mine
        latest = store.list_items(db, uid, view="latest", now=NOW)
        assert [i["id"] for i in latest["items"]][0] == off                          # 新着は今のまま
    with news_db.session() as db:
        db.query(NewsItem).filter(NewsItem.id == mine).delete(); db.commit()
        assert home.build(db, uid, NOW)["onair"]["id"] == off                        # 合う記事が無ければ全体の 1 位


def test_imagegen_prompt_uses_prep_description_over_fixed_phrase():
    """目的: 下ごしらえの英語の描写があれば、決まり文句（地球儀）ではなくそれを絵のプロンプトにすること。"""
    plain = imagegen.build_prompt("日本の経済ニュース", "", "news")
    assert "globe" in plain or "economy" in plain
    used = imagegen.build_prompt("日本の経済ニュース", "", "news", img="a sleeping cat on a stack of old books")
    assert used.startswith("a sleeping cat on a stack of old books") and imagegen.STYLE in used
    assert imagegen.build_prompt("x", img="日本語の描写").count("日本語") == 0           # 英語以外は使わない


def test_gnews_signature_at_the_end_of_a_large_page_is_found():
    """目的: Google ニュースの中継のページが 512KB を超え、署名が末尾にあっても解けること。

    根拠: 2026-11-24 本番で中継のページが約 593KB、署名は 591,579 文字目にあり、先頭 512KB だけ読んでいたため
    Google ニュースの記事は 1 件も元の記事に解けなかった（リーダーの本文・記事の画像が取れない原因の 6 割）。
    """
    import asyncio

    import httpx

    from app.news import gnews

    page = ("x" * (600 * 1024)) + '<div data-n-a-sg="SIG" data-n-a-ts="123"></div>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page)
        return httpx.Response(200, text=')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://example.com/a\\",1]",null,null,null,"1"]]')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(gnews.resolve("https://news.google.com/rss/articles/CBMiAUFVX3lxTE5ldw?hl=ja", client=client)) == "https://example.com/a"
