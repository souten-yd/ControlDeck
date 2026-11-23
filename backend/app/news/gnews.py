"""Google ニュースの記事 URL（news.google.com/rss/articles/<id>）から、元の記事の URL を得る（画像の取得にだけ使う。記事の url は置き換えない）。

2026-11-24 利用者の本番の撮影: Google ニュースの記事はページの画像が 0 件（転送の包みで元の記事に届かない）。
手順は公開の実装（出典は docs/implementation-status.md）を読んで自前で書いた（新しい部品は入れない）:
1. URL の最後の区切りの <id>（base64url）を base64 として読む。先頭 0x08 0x13 0x22・末尾 0xd2 0x01 0x00 を除き、長さの 1〜2 バイトの後ろが元の URL なら、それを返す（古い形式。通信しない）。
2. 中身が "AU_yqL" で始まる新しい形式（2024 年 7 月〜）は、通信なしには解けない。次の 2 段で解く:
   a. https://news.google.com/rss/articles/<id>?hl=&gl=&ceid= のページを取り、<div data-n-a-sg=… data-n-a-ts=…> の署名と時刻を読む。
   b. https://news.google.com/_/DotsSplashUi/data/batchexecute へ f.req=[[["Fbv4je","[\"garturlreq\",[…],\"<id>\",<ts>,\"<sg>\"]",null,"generic"]]] を POST し、
      返事の ["garturlres","<元の URL>",…] から元の URL を読む。
解けない（Google が形式を変えた・混み合い・取れない）ときは None を返し、呼び出し側は諦めて画像生成へ回す。
通信は web_collector.egress.guarded_client（私的な IP は断る）。時間・大きさに上限がある。
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from urllib.parse import parse_qs, quote, urlsplit

import httpx

logger = logging.getLogger("control_deck.news.gnews")

HOST = "news.google.com"
BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
TIMEOUT_SECONDS = 8
READ_BYTES = 512 * 1024
UA = "Mozilla/5.0 (compatible; ControlDeckNews/1.0)"
_SG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')
# garturlreq の文脈（公開の実装と同じ固定の形。値は意味を持たない目印）
_CONTEXT = [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0]


def is_google_news(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == HOST


def article_id(url: str) -> str | None:
    """.../articles/<id>（または /read/<id>）の <id>。Google ニュースの URL でなければ None。"""
    parts = urlsplit(url)
    path = parts.path.split("/")
    if (parts.hostname or "").lower() == HOST and len(path) > 1 and path[-2] in ("articles", "read") and path[-1]:
        return path[-1]
    return None


def decode_legacy(art_id: str) -> str | None:
    """古い形式（ID の中に元の URL が入っている）を、通信なしで解く。新しい形式・壊れた ID は None。"""
    try:
        raw = base64.urlsafe_b64decode(art_id + "=" * (-len(art_id) % 4))
    except (binascii.Error, ValueError):
        return None
    if raw.startswith(b"\x08\x13\x22"):
        raw = raw[3:]
    if raw.endswith(b"\xd2\x01\x00"):
        raw = raw[:-3]
    if not raw:
        return None
    size, skip = (raw[0], 1) if raw[0] < 0x80 else (raw[0], 2)
    body = raw[skip:skip + size]
    if body.startswith(b"AU_yqL"):
        return None            # 新しい形式
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if urlsplit(text).scheme in ("http", "https") and urlsplit(text).hostname else None


def parse_signature(html: str) -> tuple[str, str] | None:
    """記事のページから署名（data-n-a-sg）と時刻（data-n-a-ts）を読む。"""
    sg, ts = _SG.search(html), _TS.search(html)
    return (sg.group(1), ts.group(1)) if sg and ts else None


def build_body(art_id: str, signature: str, timestamp: str) -> str:
    inner = json.dumps(["garturlreq", _CONTEXT, art_id, int(timestamp) if timestamp.isdigit() else timestamp, signature], separators=(",", ":"))
    return "f.req=" + quote(json.dumps([[["Fbv4je", inner, None, "1"]]], separators=(",", ":")))


def parse_response(text: str) -> str | None:
    """batchexecute の返事から元の URL を読む（["garturlres","<url>",…]）。読めなければ None。"""
    body = text.lstrip()
    if body.startswith(")]}'"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    for chunk in body.splitlines():          # 行ごとに「長さ」の行と JSON の行が交互に来る
        chunk = chunk.strip()
        if not chunk.startswith("["):
            continue
        try:
            rows = json.loads(chunk)
        except ValueError:
            continue
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, list) and len(row) >= 3 and isinstance(row[2], str) and "garturlres" in row[2]:
                try:
                    payload = json.loads(row[2])
                except ValueError:
                    continue
                if isinstance(payload, list) and len(payload) >= 2 and payload[0] == "garturlres" and isinstance(payload[1], str):
                    url = payload[1]
                    return url if urlsplit(url).scheme in ("http", "https") and not is_google_news(url) else None
    return None


async def _read_text(resp: httpx.Response) -> str:
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf += chunk
        if len(buf) >= READ_BYTES:
            break
    return bytes(buf[:READ_BYTES]).decode("utf-8", errors="replace")


async def resolve(url: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Google ニュースの記事 URL から元の記事の URL。解けなければ None（例外は投げない）。client は試験用。"""
    art_id = article_id(url)
    if not art_id:
        return None
    legacy = decode_legacy(art_id)
    if legacy:
        return legacy
    from app.web_collector import egress

    qs = parse_qs(urlsplit(url).query)
    locale = f"hl={quote(qs.get('hl', ['ja'])[0], safe='')}&gl={quote(qs.get('gl', ['JP'])[0], safe='')}&ceid={quote(qs.get('ceid', ['JP:ja'])[0], safe='')}"
    page = f"https://{HOST}/rss/articles/{quote(art_id, safe='')}?{locale}"
    try:
        async with (client or egress.guarded_client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": UA})) as http:
            async with http.stream("GET", page) as resp:
                if resp.status_code != 200:
                    return None
                found = parse_signature(await _read_text(resp))
            if not found:
                return None
            async with http.stream("POST", BATCHEXECUTE_URL, content=build_body(art_id, *found),
                                   headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}) as resp:
                if resp.status_code != 200:
                    return None
                return parse_response(await _read_text(resp))
    except Exception as exc:  # noqa: BLE001 - 解けなかっただけ（画像生成へ回す）。理由は種類だけ記録
        logger.debug("Google ニュースの転送を解けませんでした: %s", type(exc).__name__)
    return None
