"""1 ページの取得（W1: HTTP）。Deep Research・Workflow・公開データの取得・Patent の調査が同じ `collect` を使う。

- 接続は egress（解決した IP にそのまま接続）。リダイレクトは各段で同じ検査を通して最大 MAX_REDIRECTS 段。
- 応答の大きさ・時間に上限。PDF は pypdf で（ページ数の上限）。HTML は extract（Trafilatura か従来の抽出）。
- 失敗は例外にせず `status` で返す（blocked / timeout / empty / security_rejected / http_error / too_large /
  unsupported / captcha）。呼び出し側は status を見て扱いを決める。
- 応答の形は docs/PLAN-web-collector.md §2（設計案 §7）に合わせる。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import io
import re
from urllib.parse import urljoin, urlsplit

import httpx

from app.security.offline import ExternalResearchBlocked
from app.web_collector import egress, extract

MAX_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_S = 30
PDF_MAX_PAGES = 80
UA = "Mozilla/5.0 (X11; Linux x86_64) ControlDeck-WebCollector/1.0"
_CAPTCHA = re.compile(r"captcha|cf-chl-|challenge-platform|are you a robot|ロボットではありません|hcaptcha|recaptcha|prove your humanity", re.I)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.I)
_META = re.compile(r"""<meta\s+[^>]*?(?:name|property)\s*=\s*["']([^"']+)["'][^>]*?content\s*=\s*["']([^"']*)["']""", re.I)
# 書誌のメタデータ（Highwire Press の citation_*・Dublin Core の DC.*・Open Graph）。Google Patents・Google Scholar に載る論文・
# J-STAGE・arXiv などが持つ。本文に出ない出願人・発明者・著者・DOI がここにある（2026-11-21 本番の評価: Google Patents の
# 出願人・発明者は本文に無く、meta の DC.contributor にだけあった）。
_BIBLIO_KEYS = {
    "citation_title": "題名", "dc.title": "題名", "citation_patent_publication_number": "公開番号",
    "citation_patent_application_number": "出願番号", "citation_patent_number": "特許番号", "citation_author": "著者",
    "dc.contributor": "出願人・発明者", "citation_publisher": "発行者", "citation_journal_title": "掲載誌",
    "citation_publication_date": "発行日", "citation_date": "日付", "dc.date": "日付", "citation_doi": "DOI",
    "citation_pdf_url": "PDF", "dc.type": "種類", "article:published_time": "公開日時", "og:site_name": "サイト",
}


def biblio(html: str) -> dict[str, list[str]]:
    """head の meta から書誌を拾う（同じ名前は出た順に並べ、重複を除く）。"""
    import html as htmllib

    out: dict[str, list[str]] = {}
    for name, content in _META.findall(html[:300000]):
        label = _BIBLIO_KEYS.get(name.strip().casefold())
        value = " ".join(htmllib.unescape(content).split())[:300]
        if label and value and value not in out.setdefault(label, []):
            out[label].append(value)
    # 種別記号の付いた番号（US6285999B1）は meta に無く <title> にある。質問は種別記号付きで書かれやすいので足す
    if "patent" in [v.casefold() for v in out.get("種類", [])]:
        title_tag = re.search(r"<title[^>]*>([^<]{0,300})", html[:300000], re.I)
        for number in _PATENT_NO.findall(title_tag.group(1) if title_tag else ""):
            if number not in out.setdefault("公開番号", []):
                out["公開番号"].insert(0, number)
    # DC.contributor は発明者と出願人（権利者）が混ざる。法人の印で分ける（2026-11-21 本番の評価: 「出願人は？」に答えられなかった）
    mixed = out.pop("出願人・発明者", [])
    if mixed:
        orgs = [v for v in mixed if _ORG.search(v)]
        people = [v for v in mixed if v not in orgs]
        if orgs:
            out["出願人・権利者（法人）"] = orgs
        if people:
            out["発明者・著者"] = people
    return {k: v[:30] for k, v in out.items() if v}


_PATENT_NO = re.compile(r"\b([A-Z]{2}\d{4,12}[A-Z]\d?)\b")
_ORG = re.compile(r"株式会社|有限会社|合同会社|大学|研究所|機構|法人|\b(?:Inc|Corp|Corporation|Co|Ltd|LLC|GmbH|AG|SA|BV|NV|"
                  r"PLC|KK|Limited|University|Universit[aäé]t?|Institute|Foundation|Laboratories|Labs|Company)\b\.?", re.I)


def biblio_block(data: dict[str, list[str]]) -> str:
    """書誌を本文の先頭に置く Markdown（RAG で出願人・日付も見つかるように）。"""
    if not data:
        return ""
    lines = []
    for label, values in data.items():
        if label == "PDF":
            continue
        if label in ("公開番号", "出願番号", "特許番号"):
            # meta は "US:6285999" "JP:2026116114:A" の形。質問は "US6285999B1" "特開2026-116114" と書くので、詰めた形も並べる
            values = [*values, *[v.replace(":", "").replace("/", "").replace(",", "") for v in values]]
        lines.append(f"- {label}: {'、'.join(dict.fromkeys(values))}")
    if not lines:
        return ""
    # 見出しに番号と題名を入れる。見出しは分けた断片の先頭（パンくず）に付くので、「US6285999B1 の出願人は？」のように
    # 番号で聞かれたとき、ほかの文献の書誌より上に来る（2026-11-21 本番の評価で、番号の無い見出しだと別の文献の書誌が先に出た）
    number = next((v.replace(":", "").replace("/", "").replace(",", "") for key in ("公開番号", "特許番号", "出願番号")
                   for v in data.get(key, [])), "")
    title = (data.get("題名") or [""])[0]
    name = " ".join(x for x in (number, title[:80]) if x)
    return f"## 書誌（{name}）\n\n" + "\n".join(lines) if name else "## 書誌（ページのメタデータ）\n\n" + "\n".join(lines)


_CANONICAL = re.compile(r"""<link[^>]+rel=["']?canonical["']?[^>]*href=["']([^"']+)["']""", re.I)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _page(url: str, status: str, **fields) -> dict:
    base = {"url": url, "final_url": url, "canonical_url": "", "status": status, "fetch_method": "http",
            "http_status": 0, "title": "", "content_type": "", "markdown": "", "language": "", "links": [], "tables": [], "tables_meta": {},
            "metadata": {}, "biblio": {}, "content_sha256": "", "fetched_at": _now(), "warnings": [], "extractor": "", "error": ""}
    base.update(fields)
    return base


def _decode(content: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    else:
        meta = _META_CHARSET.search(content[:4096])
        if meta:
            charset = meta.group(1).decode("ascii", "ignore")
    for candidate in (charset, "utf-8", "cp932", "euc-jp"):
        if not candidate:
            continue
        try:
            return content.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def _pdf_text(content: bytes, limit_chars: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages, used = [], 0
    for page in reader.pages[:PDF_MAX_PAGES]:
        text = page.extract_text() or ""
        pages.append(text)
        used += len(text)
        if used >= limit_chars:
            break
    return "\n\n".join(pages)


async def collect(url: str, *, mode: str = "article", timeout: float = TIMEOUT_S, max_bytes: int = MAX_BYTES,
                  policy: egress.Policy | None = None, limit_chars: int = 2_000_000,
                  keep_html: bool = False, render: str = "never") -> dict:
    """公開の URL を 1 つ取得して本文を取り出す。

    render: "never"（静的な HTTP だけ）/ "auto"（静的で足りず、JavaScript が要ると判定したら、機能「Web 収集のブラウザ」が
    有効なときだけブラウザへ。初期 JSON に本文があればブラウザを使わずそこから取る）。"""
    policy = policy or egress.DEFAULT_POLICY
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return _page(url, "security_rejected", error="http(s) の公開 URL だけを取得します")
    current = url
    try:
        async with egress.guarded_client(timeout=timeout, policy=policy,
                                         headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
                                                  "Accept-Language": "ja,en;q=0.8"}) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                if urlsplit(current).scheme not in ("http", "https"):
                    return _page(url, "security_rejected", final_url=current, error="http(s) 以外へのリダイレクト")
                async with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                        current = urljoin(current, response.headers["location"])
                        continue
                    content_type = response.headers.get("content-type", "").casefold()
                    declared = int(response.headers.get("content-length") or 0)
                    if declared > max_bytes:
                        return _page(url, "too_large", final_url=current, http_status=response.status_code,
                                     content_type=content_type, error=f"{declared} バイト")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            return _page(url, "too_large", final_url=current, http_status=response.status_code,
                                         content_type=content_type, error=f"{max_bytes} バイトを超えました")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    status_code = response.status_code
                    break
            else:
                return _page(url, "http_error", final_url=current, error="リダイレクトが多すぎます")
    except egress.EgressRejected as exc:
        return _page(url, "security_rejected", final_url=current, error=str(exc))
    except ExternalResearchBlocked as exc:  # オフライン調査（取得の前に止まった）
        return _page(url, "security_rejected", final_url=current, error=str(exc))
    except httpx.TimeoutException:
        return _page(url, "timeout", final_url=current, error=f"{timeout} 秒で応答がありません")
    except (httpx.HTTPError, OSError) as exc:
        return _page(url, "http_error", final_url=current, error=f"{type(exc).__name__}: {exc}"[:300])

    digest = hashlib.sha256(content).hexdigest()
    base = {"final_url": current, "http_status": status_code, "content_type": content_type, "content_sha256": digest}
    if status_code >= 400:
        snippet = _decode(content[:20000], content_type) if content else ""
        if status_code in (403, 429, 503) and _CAPTCHA.search(snippet):
            return _page(url, "captcha", **base, error=f"HTTP {status_code}（ロボット確認。回避しません）")
        return _page(url, "blocked" if status_code in (401, 403, 429) else "http_error", **base,
                     error=f"HTTP {status_code}")
    if "pdf" in content_type or current.casefold().endswith(".pdf") or content[:5] == b"%PDF-":
        try:
            text = await asyncio.to_thread(_pdf_text, content, limit_chars)
        except Exception as exc:  # noqa: BLE001 - 壊れた PDF
            return _page(url, "empty", **base, error=f"PDF を読めません: {exc}"[:300])
        return _page(url, "success" if text.strip() else "empty", **base, markdown=text[:limit_chars], extractor="pypdf")
    if content_type and "html" not in content_type and not content_type.startswith("text/"):
        return _page(url, "unsupported", **base, error=f"本文として扱えない種類: {content_type}")
    html = _decode(content, content_type)
    if _CAPTCHA.search(html[:20000]) and len(html) < 60000:
        return _page(url, "captcha", **base, error="ロボット確認のページ（回避しません）")
    if "html" not in content_type and "<html" not in html[:2000].casefold():
        return _page(url, "success" if html.strip() else "empty", **base, markdown=html[:limit_chars], extractor="text")
    result = await extract.extract(html, current, mode)
    from app.web_collector import html_tables

    tables, tables_meta, table_warnings = await asyncio.to_thread(html_tables.table_bundle, html)
    canonical = _CANONICAL.search(html[:200000])
    page = _page(url, "success" if result["markdown"].strip() else "empty", **base,
                 canonical_url=urljoin(current, canonical.group(1)) if canonical else "",
                 title=result.get("title", ""), markdown=result["markdown"][:limit_chars],
                 language=result.get("metadata", {}).get("language", ""), links=result.get("links", []),
                 metadata=result.get("metadata", {}), biblio=biblio(html), tables=tables, tables_meta=tables_meta,
                 warnings=[*result.get("warnings", []), *table_warnings],
                 extractor=result.get("extractor", ""))
    if render == "auto" and page["status"] in ("success", "empty"):
        page = await _maybe_render(page, html, mode, policy, timeout, limit_chars)
    if keep_html:
        page["html"] = page.pop("_rendered_html", None) or html
    page.pop("_rendered_html", None)
    return page


async def _maybe_render(page: dict, html: str, mode: str, policy: egress.Policy, timeout: float, limit_chars: int) -> dict:
    """静的な取得で足りないときの段階: 初期 JSON → ブラウザ。止める条件（robots 拒否・403/429・CAPTCHA・ログイン）は回避せず、
    その状態を返す。ブラウザを起動できないなど環境の問題は、静的な結果に警告を足して返す（失敗にしない）。"""
    from app.web_collector import browser, js_detect, robots

    verdict = js_detect.assess(html, page["markdown"])
    page["js_check"] = verdict.as_dict()
    if verdict.json_text:
        text = (page["markdown"].strip() + "\n\n" + verdict.json_text).strip()
        return {**page, "status": "success", "markdown": text[:limit_chars], "fetch_method": "data_api",
                "warnings": [*page["warnings"], f"初期 JSON（{verdict.json_source}）から本文を取りました（ブラウザは使っていません）"]}
    if not verdict.needs_js:
        return page
    if not await asyncio.to_thread(browser.is_enabled):
        return {**page, "warnings": [*page["warnings"], "JavaScript が要るページですが、機能「Web 収集のブラウザ」が使えません"]}
    target = page["final_url"]
    ok, reason = await robots.check(target, policy=policy)
    if not ok:
        return {**page, "status": "robots_denied", "error": reason}
    try:
        out = await browser.render(target, policy=policy, timeout_s=min(timeout, 30))
    except ExternalResearchBlocked as exc:
        return {**page, "status": "security_rejected", "error": str(exc)}
    except browser.BrowserError as exc:
        return {**page, "warnings": [*page["warnings"], f"ブラウザを使えませんでした: {exc}"]}
    stats = out.get("stats") or {}
    if out["status"] in ("blocked", "captcha", "login_required", "security_rejected"):
        return {**page, "status": out["status"], "error": out["error"], "browser": stats}
    if out["status"] != "success":
        return {**page, "warnings": [*page["warnings"], f"ブラウザでも取れませんでした（{out['status']}: {out['error']}）"], "browser": stats}
    result = await extract.extract(out["html"], out["final_url"], mode)
    if len(result["markdown"].strip()) <= len(page["markdown"].strip()):
        return {**page, "warnings": [*page["warnings"], "ブラウザで開いても本文は増えませんでした"], "browser": stats}
    from app.web_collector import html_tables

    tables, tables_meta, table_warnings = await asyncio.to_thread(html_tables.table_bundle, out["html"])
    return {**page, "status": "success", "fetch_method": "browser", "markdown": result["markdown"][:limit_chars],
            "tables": tables, "tables_meta": tables_meta,
            "title": result.get("title") or page["title"], "links": result.get("links", []) or page["links"],
            "metadata": result.get("metadata", {}) or page["metadata"], "extractor": result.get("extractor", ""),
            "warnings": [*page["warnings"], *(out.get("warnings") or []), *table_warnings, "ブラウザで描画して取りました"],
            "browser": stats, "_rendered_html": out["html"]}
