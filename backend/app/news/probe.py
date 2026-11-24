"""任意のサイトをニュースのソースにする道具: 通信確認・RSS の発見・記事の一覧のページの巡回。

2026-11-24 利用者「任意のサイトのクロール機能を追加できるのか、サイト追加後に通信確認みたいなことがあるといい」。
- 取得は既存の安全な取得（web_collector/egress.guarded_client。内部アドレスの遮断・大きさ・時間の上限）だけを使う。新しい生の httpx は書かない。
- ログインが要る・ロボット確認（403/429/チャレンジのページ）のサイトは、すり抜けない。理由を言って止まる。
- 巡回は robots.txt を守る（Protego があればそれ、無ければ標準の robotparser）。同じサイトの中の 1 ページ（一覧）だけを読み、
  1 回の件数と取得の頻度に上限がある。記事のページ自体は巡回しない。
- 失敗の理由は code（dns・timeout・blocked・login・notfound・http・redirect・format・robots・private・toolarge・connect）で返す。
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.news import sources

MAX_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT = 15
MAX_REDIRECTS = 4
UA = "ControlDeck-Knowledge/1.0"
PAGE_ITEMS = 30                 # 一覧のページから拾う記事の上限（1 回）
PAGE_MIN_GAP = 3600.0           # 同じ一覧のページを読み直す間隔の下限（秒）。その間は前回の結果を返す
COMMON_FEEDS = ("/feed", "/rss", "/atom.xml", "/index.xml", "/feed.xml", "/rss.xml")
ERROR_LABELS = {
    "dns": "名前を解決できません", "timeout": "時間切れです", "blocked": "取れません（ロボット確認）", "login": "ログインが要るサイトです",
    "notfound": "見つかりません（404）", "http": "サーバーがエラーを返しました", "redirect": "別の URL へ移されています", "format": "形式が違います",
    "robots": "robots.txt が巡回を許していません", "private": "内部のアドレスへは取りに行きません", "toolarge": "応答が大きすぎます",
    "connect": "接続できません", "other": "取得に失敗しました",
}


class ProbeError(Exception):
    def __init__(self, code: str, detail: str = "", status: int | None = None, location: str = ""):
        self.code, self.detail, self.status, self.location = code, detail, status, location
        super().__init__(detail or ERROR_LABELS.get(code, code))

    @property
    def message(self) -> str:
        base = ERROR_LABELS.get(self.code, self.code)
        return f"{base}: {self.detail}" if self.detail else base


@dataclass
class Fetched:
    url: str            # 最後の URL（リダイレクトを追ったとき）
    status: int
    content_type: str
    body: bytes
    redirected: bool = False

    @property
    def text(self) -> str:
        head = self.body[:2048].decode("ascii", "ignore")
        found = re.search(r"charset=([A-Za-z0-9_-]+)", self.content_type or "") or re.search(r"""charset=["']?([A-Za-z0-9_-]+)""", head, re.I)
        for enc in ([found.group(1)] if found else []) + ["utf-8"]:
            try:
                return self.body.decode(enc, "replace")
            except LookupError:
                continue
        return self.body.decode("utf-8", "replace")


_CHALLENGE = re.compile(r"just a moment|cf-chl|captcha|are you a robot|access denied|ロボット|bot detection|attention required|verify you are human|enable javascript and cookies", re.I)


def classify_status(status: int, body: bytes = b"") -> ProbeError:
    if status in (401, 407):
        return ProbeError("login", f"HTTP {status}", status)
    if status in (403, 429) or (status == 503 and _CHALLENGE.search(body[:20000].decode("utf-8", "ignore"))):
        return ProbeError("blocked", f"HTTP {status}", status)
    if status in (404, 410):
        return ProbeError("notfound", f"HTTP {status}", status)
    return ProbeError("http", f"HTTP {status}", status)


async def http_fetch(url: str, *, timeout: float = FETCH_TIMEOUT) -> tuple[int, dict, bytes]:
    """1 回の GET（リダイレクトは追わない）。(status, headers, 本文)。例外は ProbeError。"""
    import httpx

    from app.web_collector import egress

    try:
        async with egress.guarded_client(timeout=timeout, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml,*/*;q=0.8"}) as client:
            async with client.stream("GET", url) as resp:
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ProbeError("toolarge", f"{MAX_BYTES // 1024 // 1024}MB 以内のものだけ読めます")
                    chunks.append(chunk)
                return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, b"".join(chunks)
    except egress.EgressRejected as exc:
        raise ProbeError("dns" if "名前を解決" in str(exc) else "private", str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise ProbeError("timeout", f"{int(timeout)} 秒以内に応答がありません") from exc
    except httpx.HTTPError as exc:
        raise ProbeError("connect", type(exc).__name__) from exc


def _https(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https",) or not parts.hostname or parts.username or parts.password or len(url) > 800:
        raise ProbeError("format", "https の URL を入れてください")
    return url.strip()


async def fetch(url: str, *, follow: bool = True) -> Fetched:
    """URL を取る。follow=False は本番の RSS の取得（http_get）と同じ（リダイレクトは失敗。移動先を location に入れる）。"""
    current, hops = _https(url), 0
    while True:
        status, headers, body = await http_fetch(current)
        if 300 <= status < 400 and headers.get("location"):
            target = urljoin(current, headers["location"])
            if not follow:
                raise ProbeError("redirect", target, status, location=target)
            hops += 1
            if hops > MAX_REDIRECTS or not target.startswith("https://"):
                raise ProbeError("redirect", "移動が多すぎる・https でない移動先です", status)
            current = target
            continue
        if status >= 400:
            raise classify_status(status, body)
        if status >= 300:
            raise ProbeError("redirect", f"HTTP {status}", status)
        return Fetched(current, status, headers.get("content-type", ""), body, redirected=hops > 0)


# ---- RSS・HTML の判定 ----

def feed_items(raw: bytes, url: str, name: str = "") -> list[dict]:
    """RSS/Atom として読めれば記事の一覧、読めなければ ProbeError(format)。"""
    import xml.etree.ElementTree as ET

    try:
        items = sources.parse_feed(raw.lstrip(b"\xef\xbb\xbf \r\n\t"), url=url, name=name or url)
    except (ET.ParseError, ValueError) as exc:
        raise ProbeError("format", "RSS/Atom として読めません") from exc
    if not items:
        raise ProbeError("format", "RSS/Atom として読めません（記事が 1 件も入っていません）")
    return items


def looks_like_html(f: Fetched) -> bool:
    head = f.body[:2000].decode("utf-8", "ignore").lower()
    return "html" in (f.content_type or "").lower() or "<html" in head or "<!doctype html" in head


class _Links(HTMLParser):
    """<title>・<link rel=alternate>・<a href>（見出しの文字つき）・<time datetime> を集める。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self._in_title = "", False
        self.alternates: list[dict] = []
        self.anchors: list[dict] = []
        self._open: dict | None = None
        self._skip = 0
        self._time: str = ""

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "link" and "alternate" in a.get("rel", "").lower() and a.get("href"):
            self.alternates.append({"type": a.get("type", "").lower(), "href": a["href"], "title": a.get("title", "")})
        elif tag in ("script", "style", "nav", "footer", "header", "aside"):
            self._skip += 1 if tag in ("script", "style") else 0
        elif tag == "time" and a.get("datetime"):
            self._time = a["datetime"]
            if self._open is not None:
                self._open["time"] = self._open["time"] or a["datetime"]
        elif tag == "a" and a.get("href"):
            self._open = {"href": a["href"], "text": "", "time": self._time, "title": a.get("title", ""), "rel": a.get("rel", "")}
            self._time = ""
        elif tag == "img" and self._open is not None and a.get("alt"):
            self._open["alt"] = a["alt"]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "a" and self._open is not None:
            self.anchors.append(self._open)
            self._open = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._open is not None and not self._skip:
            self._open["text"] += data


def parse_html(text: str) -> _Links:
    parser = _Links()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 - 壊れた HTML でも拾えた分を使う
        pass
    return parser


def alternate_feeds(page: _Links, base: str) -> list[str]:
    """<link rel="alternate" type="application/rss+xml|atom+xml"> の URL（絶対 URL・順序を保って重複なし）。"""
    out: list[str] = []
    for alt in page.alternates:
        if any(k in alt["type"] for k in ("rss+xml", "atom+xml", "application/xml", "text/xml", "rdf+xml")):
            url = sources.safe_url(alt["href"], base)
            if url and url.startswith("https://") and url not in out:
                out.append(url)
    return out


def common_feed_urls(base: str) -> list[str]:
    parts = urlsplit(base)
    root = f"{parts.scheme}://{parts.netloc}"
    return [root + p for p in COMMON_FEEDS]


async def _try_feed(url: str) -> dict | None:
    try:
        got = await fetch(url)
        items = feed_items(got.body, got.url)
    except ProbeError:
        return None
    if not items:
        return None
    latest = max(i["published_at"] for i in items)
    return {"url": got.url, "count": len(items), "latest": latest.strftime("%Y-%m-%d"), "titles": [i["title"] for i in items[:3]]}


# ---- 記事の一覧のページ ----

_SKIP_PATH = re.compile(r"/(?:tag|tags|category|categories|cat|author|authors|page|search|login|signin|signup|register|about|contact|privacy|terms|rss|feed|archive|archives|ranking|help|faq|company|policy|sitemap|wp-admin|wp-login)(?:/|$)", re.I)
_SKIP_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|pdf|zip|css|js|xml|rss|ico|mp4|mp3)$", re.I)
_ARTICLE_HINT = re.compile(r"/(?:20\d\d|19\d\d)[/-]\d{1,2}|/\d{4}/\d{1,2}/|/(?:article|articles|news|entry|entries|post|posts|blog|story|stories|topics?|column|media|press|release|detail|n|p|a|items?|archives?)/[^/?#]+|[/_-]\d{5,}|/[a-z0-9]+(?:-[a-z0-9]+){3,}", re.I)


def site_of(host: str) -> str:
    """同じサイトとみなす名前（www. を除く）。サブドメインは別のサイト扱い。"""
    return (host or "").lower().removeprefix("www.")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def list_links(text: str, base: str, limit: int = PAGE_ITEMS) -> list[dict]:
    """一覧のページから、同じサイトの中の記事らしいリンク（{title, url, published}）を拾う。
    記事らしさ = 見出しの文字が一定以上ある・パスが記事の形（日付・/article/・/news/・長い数字・語をつないだ名前など）・
    タグ/カテゴリ/ページ送り/ログイン等の案内ではない・一覧自身ではない。外のサイト・http・ファイルは捨てる。"""
    page = parse_html(text)
    host = site_of(urlsplit(base).hostname or "")
    base_key = urlunsplit(urlsplit(base)._replace(fragment="")).rstrip("/")
    out, seen = [], set()
    for a in page.anchors:
        url = sources.safe_url(a["href"], base)
        if not url or not url.startswith("https://"):
            continue
        parts = urlsplit(url)
        if site_of(parts.hostname or "") != host or _SKIP_EXT.search(parts.path) or _SKIP_PATH.search(parts.path + "/"):
            continue
        clean = urlunsplit(parts._replace(fragment=""))
        if clean.rstrip("/") == base_key or parts.path in ("", "/") or clean in seen:
            continue
        title = _clean(a["text"]) or _clean(a.get("title", "")) or _clean(a.get("alt", ""))
        # CJK は短くても意味がある。英数字だけの短い語（Home・Next 等）は案内として捨てる
        cjk = bool(re.search(r"[぀-ヿ㐀-鿿]", title))
        if len(title) < (6 if cjk else 14) or not _ARTICLE_HINT.search(parts.path):
            continue
        seen.add(clean)
        out.append({"title": title[:300], "url": clean, "published": a.get("time", "")})
        if len(out) >= limit:
            break
    return out


class _Robots:
    """robots.txt の判定。Protego があればそれ（Google の仕様のワイルドカード）、無ければ標準の robotparser。"""

    def __init__(self, text: str):
        try:
            from protego import Protego

            self._p = Protego.parse(text)
            self._std = None
        except ImportError:
            self._p = None
            self._std = RobotFileParser()
            self._std.parse(text.splitlines())

    def allowed(self, url: str) -> bool:
        if self._p is not None:
            return bool(self._p.can_fetch(url, UA))
        return bool(self._std.can_fetch(UA, url))


async def robots_allows(url: str) -> bool:
    """同じホストの robots.txt が url の取得を許すか。robots.txt が無い（4xx）なら許す。取れない（5xx・通信失敗）なら許さない。"""
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        got = await fetch(robots_url)
    except ProbeError as exc:
        if exc.status is not None and 400 <= exc.status < 500:
            return True
        return False
    return _Robots(got.text).allowed(url)


def blocked_page(f: Fetched) -> ProbeError | None:
    """200 でもロボット確認・ログインの画面なら、その理由。"""
    if not looks_like_html(f):
        return None
    head = f.text[:30000]
    if _CHALLENGE.search(head) and len(list_links(head, f.url, 5)) == 0:
        return ProbeError("blocked", "ロボット確認の画面でした")
    return None


_crawled: dict[str, tuple[float, list[dict]]] = {}


async def crawl_list(url: str, name: str, *, now: float | None = None) -> list[dict]:
    """一覧のページを 1 回読んで記事（sources.make_item の形）にする。robots.txt を守り、PAGE_MIN_GAP の間は前回の結果を返す。"""
    clock = time.monotonic() if now is None else now
    cached = _crawled.get(url)
    if cached and clock - cached[0] < PAGE_MIN_GAP:
        return cached[1]
    if not await robots_allows(url):
        raise ProbeError("robots")
    got = await fetch(url)
    if not looks_like_html(got):
        raise ProbeError("format", "HTML のページではありません")
    stop = blocked_page(got)
    if stop:
        raise stop
    links = list_links(got.text, got.url)
    if not links:
        raise ProbeError("format", "記事らしいリンクが見つかりません")
    items = []
    for link in links:
        item = sources.make_item(kind="news", source=name, source_key=f"page:{url}", title=link["title"], url=link["url"], published=link["published"] or None)
        if item:
            items.append(item)
    _crawled[url] = (clock, items)
    return items


# ---- 発見（サイトを追加）----

async def discover(url: str) -> dict:
    """URL から取り込み方の候補を出す。
    {"url", "title", "mode": "feed"|"feeds"|"page"|"none", "feeds": [{url, count, latest, titles}], "page": {items, robots} | None, "error": {code,message} | None}"""
    try:
        got = await fetch(url)
    except ProbeError as exc:
        return _result(url, "", [], None, exc)
    try:
        items = feed_items(got.body, got.url)
        if items:
            latest = max(i["published_at"] for i in items)
            feed = {"url": got.url, "count": len(items), "latest": latest.strftime("%Y-%m-%d"), "titles": [i["title"] for i in items[:3]]}
            return _result(url, "", [feed], None, None)
    except ProbeError:
        pass
    if not looks_like_html(got):
        return _result(url, "", [], None, ProbeError("format", "RSS でも HTML でもありません"))
    stop = blocked_page(got)
    if stop:
        return _result(url, "", [], None, stop)
    page = parse_html(got.text)
    title = _clean(page.title)[:80]
    candidates = alternate_feeds(page, got.url)
    for extra in common_feed_urls(got.url):
        if extra not in candidates:
            candidates.append(extra)
    found = [f for f in await asyncio.gather(*[_try_feed(c) for c in candidates[:12]]) if f]
    seen, feeds = set(), []
    for f in found:        # リダイレクトの先が同じものは 1 つに
        if f["url"] not in seen:
            seen.add(f["url"])
            feeds.append(f)
    links = list_links(got.text, got.url)
    page_info = None
    if links:
        page_info = {"count": len(links), "titles": [link["title"] for link in links[:3]], "robots": await robots_allows(got.url)}
    return _result(url, title, feeds, page_info, None if feeds or page_info else ProbeError("format", "RSS も記事の一覧も見つかりません"))


def _result(url: str, title: str, feeds: list[dict], page: dict | None, error: ProbeError | None) -> dict:
    mode = "none" if error else ("feed" if len(feeds) == 1 else "feeds" if feeds else "page")
    return {"url": url, "title": title, "mode": mode, "feeds": feeds, "page": page,
            "error": {"code": error.code, "message": error.message} if error else None}


# ---- 通信確認 ----

def _failure(exc: BaseException) -> dict:
    if isinstance(exc, ProbeError):
        return {"code": exc.code, "message": exc.message, "status": exc.status, "location": exc.location}
    if isinstance(exc, asyncio.TimeoutError):
        return {"code": "timeout", "message": ERROR_LABELS["timeout"], "status": None, "location": ""}
    status = getattr(exc, "status", None)
    text = str(exc)
    if isinstance(status, int):
        err = classify_status(status)
        return {"code": err.code, "message": err.message, "status": status, "location": ""}
    code = "dns" if "名前を解決" in text else "private" if "内部のアドレス" in text else "redirect" if "リダイレクト" in text \
        else "toolarge" if "大きすぎ" in text else "other"
    m = re.search(r"HTTP (\d{3})", text)
    if m:
        err = classify_status(int(m.group(1)))
        return {"code": err.code, "message": err.message, "status": int(m.group(1)), "location": ""}
    return {"code": code, "message": f"{ERROR_LABELS[code]}: {text[:100]}" if text and code == "other" else ERROR_LABELS[code], "status": None, "location": ""}


def _summary(items: list[dict], ms: int, status: int | None) -> dict:
    latest = max((i["published_at"] for i in items), default=None)
    return {"ok": True, "count": len(items), "titles": [i["title"] for i in items[:3]], "ms": ms, "status": status,
            "latest": latest.strftime("%Y-%m-%d") if latest else "", "error": None}


async def check(kind: str, value: str, name: str = "") -> dict:
    """ソース 1 つを今この場で 1 回取る。{"ok", "count", "titles"(最新 3 件), "ms", "status", "latest", "error": {code,message,status,location}|None}。
    rss は本番の取得（リダイレクトを追わない）と同じ条件、page は巡回と同じ（robots.txt を守る・頻度の上限は付けず 1 回読む）。"""
    started = time.monotonic()
    status: int | None = None
    try:
        if kind == "rss":
            got = await asyncio.wait_for(fetch(value, follow=False), 40)
            status = got.status
            items = feed_items(got.body, value, name)
        elif kind == "page":
            if not await robots_allows(value):
                raise ProbeError("robots")
            got = await asyncio.wait_for(fetch(value), 40)
            status = got.status
            if not looks_like_html(got):
                raise ProbeError("format", "HTML のページではありません")
            stop = blocked_page(got)
            if stop:
                raise stop
            links = list_links(got.text, got.url)
            if not links:
                raise ProbeError("format", "記事らしいリンクが見つかりません")
            items = [i for i in (sources.make_item(kind="news", source=name, source_key="page", title=link["title"], url=link["url"],
                                                   published=link["published"] or None) for link in links) if i]
        else:
            fetcher = _fetcher_for(kind, value)
            items = await asyncio.wait_for(fetcher(), 40)
    except sources.SkippedSource as exc:
        return {"ok": False, "count": 0, "titles": [], "ms": int((time.monotonic() - started) * 1000), "status": status, "latest": "",
                "error": {"code": "skipped", "message": str(exc), "status": None, "location": ""}}
    except Exception as exc:  # noqa: BLE001 - 失敗の理由を言うのが仕事
        return {"ok": False, "count": 0, "titles": [], "ms": int((time.monotonic() - started) * 1000), "status": status, "latest": "", "error": _failure(exc)}
    return _summary(items, int((time.monotonic() - started) * 1000), status)


def _fetcher_for(kind: str, value: str):
    table = {"github": sources.fetch_github, "codeberg": sources.fetch_codeberg, "discourse": sources.fetch_discourse, "reddit": sources.fetch_reddit}
    if kind == "hackernews":
        return sources.fetch_hn
    if kind == "huggingface":
        return sources.fetch_hf
    if kind not in table:
        raise ProbeError("format", f"知らない種類です: {kind}")
    return lambda: table[kind](value)
