"""画像の無い記事の画像を、記事のページから取る（og:image・twitter:image）。

2026-11-23 利用者「本来ここにはソースから取得した画像が表示されるべき…極力画像を撮ってほしい」。
- RSS が持つ画像（media:content・enclosure・本文の最初の img）は sources.parse_feed が先に取る。ここはそれでも無かった記事の分。
- 対象は **新しく入った記事だけ**・1 回の取得で最大 MAX_PER_RUN 件・同時 CONCURRENCY・1 件 PER_ITEM_SECONDS 秒・先頭 READ_BYTES だけ読む。
- 通信は web_collector.egress.guarded_client（私的・内部のアドレスは断る。リダイレクトは各段を検査して追う）。http/https だけ。
- 取れなかった記事にも image_tried の印を付け、再試行しない。保存するのは画像の URL だけ（画像そのものはブラウザが読む）。
- リリース（GitHub の汎用の画像）・出来事・Google ニュースの転送 URL は対象にしない。
"""
from __future__ import annotations

import asyncio
import logging
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from sqlalchemy import select, update

from app.news import db as news_db
from app.news.db import NewsItem
from app.news.sources import safe_url

logger = logging.getLogger("control_deck.news.images")

MAX_PER_RUN = 60
CONCURRENCY = 4
PER_ITEM_SECONDS = 8
READ_BYTES = 256 * 1024
MAX_REDIRECTS = 3
SKIP_KINDS = ("release", "event")
SKIP_HOSTS = ("news.google.com",)
UA = "Mozilla/5.0 (compatible; ControlDeckNews/1.0)"

_IMAGE_META = ("og:image", "og:image:secure_url", "og:image:url", "twitter:image", "twitter:image:src")


class _MetaScanner(HTMLParser):
    """<meta> と <link rel=image_src> だけ拾う。壊れた HTML でも落ちない（例外は呼び出し側で握る）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in _IMAGE_META and a.get("content"):
                self.found.setdefault(key, a["content"])
        elif tag == "link" and a.get("rel", "").lower() == "image_src" and a.get("href"):
            self.found.setdefault("image_src", a["href"])


def extract_image(markup: bytes | str, base_url: str) -> str | None:
    """ページの HTML から画像の URL（絶対・http/https）を 1 つ返す。無い・壊れていれば None。優先: og:image → twitter:image → image_src。"""
    text = markup.decode("utf-8", errors="replace") if isinstance(markup, bytes) else str(markup or "")
    scanner = _MetaScanner()
    try:
        scanner.feed(text)
        scanner.close()
    except Exception:  # noqa: BLE001 - 壊れた HTML。ここまでに拾えた分で判定する
        pass
    for key in (*_IMAGE_META, "image_src"):
        value = scanner.found.get(key)
        if value:
            found = safe_url(value, base_url)
            if found:
                return found
    return None


async def fetch_page_image(url: str) -> str | None:
    """記事のページの先頭だけ読んで画像の URL を返す。取れない・内部のアドレス・大きすぎる等は None（例外は投げない）。"""
    from app.web_collector import egress

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.hostname in SKIP_HOSTS:
        return None
    current = url
    try:
        async with egress.guarded_client(timeout=PER_ITEM_SECONDS, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"}) as client:
            for _ in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                        nxt = urljoin(current, resp.headers["location"])
                        if urlsplit(nxt).scheme not in ("http", "https"):
                            return None
                        current = nxt
                        continue
                    if resp.status_code >= 400:
                        return None
                    kind = resp.headers.get("content-type", "").lower()
                    if kind and "html" not in kind and "xml" not in kind:
                        return None
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf += chunk
                        if len(buf) >= READ_BYTES:
                            break
                    return extract_image(bytes(buf[:READ_BYTES]), current)
    except Exception as exc:  # noqa: BLE001 - 取れなかっただけ。理由は種類だけ記録（URL は載せない）
        logger.debug("記事の画像を取れませんでした: %s", type(exc).__name__)
    return None


def _candidates(ids: list[int]) -> list[tuple[int, str]]:
    with news_db.session() as db:
        rows = db.execute(select(NewsItem.id, NewsItem.url).where(
            NewsItem.id.in_(ids or [0]), NewsItem.image_url.is_(None), NewsItem.image_tried.is_(False),
            NewsItem.kind.not_in(SKIP_KINDS)).order_by(NewsItem.published_at.desc()).limit(MAX_PER_RUN)).all()
        return [(i, u) for i, u in rows]


def _store(results: dict[int, str | None], tried: list[int]) -> None:
    with news_db.session() as db:
        for item_id, image in results.items():
            if image:
                db.execute(update(NewsItem).where(NewsItem.id == item_id).values(image_url=image))
        for i in range(0, len(tried), 400):
            db.execute(update(NewsItem).where(NewsItem.id.in_(tried[i:i + 400])).values(image_tried=True))
        db.commit()


async def fill(new_ids: list[int]) -> dict:
    """新しく入った記事（new_ids）のうち画像の無いものへ、ページの画像を付ける。{"tried": 件数, "found": 件数}。"""
    todo = await asyncio.to_thread(_candidates, new_ids)
    if not todo:
        return {"tried": 0, "found": 0}
    gate = asyncio.Semaphore(CONCURRENCY)
    results: dict[int, str | None] = {}

    async def one(item_id: int, url: str) -> None:
        async with gate:
            try:
                results[item_id] = await asyncio.wait_for(fetch_page_image(url), PER_ITEM_SECONDS + 2)
            except Exception:  # noqa: BLE001
                results[item_id] = None

    await asyncio.gather(*[one(i, u) for i, u in todo])
    await asyncio.to_thread(_store, results, [i for i, _ in todo])
    return {"tried": len(todo), "found": sum(1 for v in results.values() if v)}
