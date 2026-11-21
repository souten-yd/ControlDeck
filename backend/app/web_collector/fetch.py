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

from app.web_collector import egress, extract

MAX_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_S = 30
PDF_MAX_PAGES = 80
UA = "Mozilla/5.0 (X11; Linux x86_64) ControlDeck-WebCollector/1.0"
_CAPTCHA = re.compile(r"captcha|cf-chl-|challenge-platform|are you a robot|ロボットではありません|hcaptcha|recaptcha", re.I)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.I)
_CANONICAL = re.compile(r"""<link[^>]+rel=["']?canonical["']?[^>]*href=["']([^"']+)["']""", re.I)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _page(url: str, status: str, **fields) -> dict:
    base = {"url": url, "final_url": url, "canonical_url": "", "status": status, "fetch_method": "http",
            "http_status": 0, "title": "", "content_type": "", "markdown": "", "language": "", "links": [],
            "metadata": {}, "content_sha256": "", "fetched_at": _now(), "warnings": [], "extractor": "", "error": ""}
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
                  keep_html: bool = False) -> dict:
    """公開の URL を 1 つ取得して本文を取り出す。"""
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
    canonical = _CANONICAL.search(html[:200000])
    page = _page(url, "success" if result["markdown"].strip() else "empty", **base,
                 canonical_url=urljoin(current, canonical.group(1)) if canonical else "",
                 title=result.get("title", ""), markdown=result["markdown"][:limit_chars],
                 language=result.get("metadata", {}).get("language", ""), links=result.get("links", []),
                 metadata=result.get("metadata", {}), warnings=result.get("warnings", []),
                 extractor=result.get("extractor", ""))
    if keep_html:
        page["html"] = html
    return page
