"""外部の公開データの取得（計画 §10、R3-2）。ResearchDataHub の connectors.py と PatentBuddy #40 を統合したもの。

- 取得先は公式 API・フィードだけ: EPO OPS（公報の書誌・要約）、USPTO ODP（米国出願の書誌）、OpenAlex（論文の書誌）、
  RSS/Atom（任意の公開フィード）、GDELT（世界のニュースの見出し）。J-PlatPat・Google Patents の自動取得はしない（規約）。
- **外へ送る検索語は利用者が「公開してよい語」と確認したものだけ**（統合の決まり 3）。案件の自由記述を自動で送らない。
- 取得は大きさ・件数・間隔に上限。内部アドレスへの接続とリダイレクトは拒む（SSRF）。環境のプロキシは使わない。
- 鍵は data_dir/knowledge-sources.json に Fernet で暗号化して置き、画面・ログ・API の応答には出さない。
- 結果は統合ストアの書誌の形（meta["biblio"]、`knowledge/biblio.py`）にそろえて取り込む。既定は機密（confidential）。
"""
from __future__ import annotations

import base64
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from app.config import data_dir

logger = logging.getLogger("control_deck.knowledge.sources")

MAX_BYTES = 8 * 1024 * 1024
MAX_RESULTS = 100
UA = "ControlDeck-Knowledge/1.0"
CONFIG_NAME = "knowledge-sources.json"

SOURCES = {
    "epo": {"label": "EPO OPS（世界の公報）", "kind": "patent", "keys": ["epo_key", "epo_secret"],
            "hint": "CQL。例: ic=H02M and pd>=2024 / ta=インバータ", "signup": "https://developers.epo.org/"},
    "uspto": {"label": "USPTO ODP（米国の出願）", "kind": "patent", "keys": ["uspto_key"],
              "hint": "検索語。例: inverter common mode", "signup": "https://data.uspto.gov/apis/getting-started"},
    "openalex": {"label": "OpenAlex（論文）", "kind": "paper", "keys": [],
                 "hint": "検索語。例: common mode noise inverter", "signup": "https://openalex.org/"},
    "rss": {"label": "RSS / Atom（公開フィード）", "kind": "news", "keys": [], "hint": "フィードの URL", "signup": ""},
    "gdelt": {"label": "GDELT（世界のニュースの見出し）", "kind": "news", "keys": [],
              "hint": "検索語（英語が多い）。例: power semiconductor", "signup": ""},
}
COLLECTIONS = {"patent": ("patent-sources", "patents", "patent"), "paper": ("papers", "general", "general"),
               "news": ("news", "general", "general")}


class SourceError(RuntimeError):
    pass


# ---- 鍵の保存（暗号化） ----

_lock = threading.Lock()


def _path():
    return data_dir() / CONFIG_NAME


def _load_raw() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _secret(name: str) -> str:
    from app.security.crypto import decrypt_text

    token = _load_raw().get("keys", {}).get(name)
    if not token:
        return ""
    try:
        return decrypt_text(token)
    except Exception:  # noqa: BLE001 - 壊れた鍵は未設定として扱う
        return ""


def set_keys(values: dict[str, str]) -> None:
    """鍵を保存する（空文字は削除）。知らない名前は拒む。"""
    from app.security.crypto import encrypt_text

    known = {k for s in SOURCES.values() for k in s["keys"]}
    with _lock:
        raw = _load_raw()
        keys = raw.setdefault("keys", {})
        for name, value in values.items():
            if name not in known:
                raise SourceError(f"知らない鍵です: {name}")
            if value:
                keys[name] = encrypt_text(str(value).strip())
            else:
                keys.pop(name, None)
        path = _path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)


def listing() -> list[dict]:
    """画面用。鍵の値は返さない（設定済みかだけ）。"""
    return [{"id": sid, "label": s["label"], "kind": s["kind"], "hint": s["hint"], "signup": s["signup"],
             "ready": all(_secret(k) for k in s["keys"]), "keys": s["keys"]} for sid, s in SOURCES.items()]


# ---- 取得（同期。呼び出し側は asyncio.to_thread） ----

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib の約束
        raise SourceError("リダイレクトは追いません")


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
_last_call: dict[str, float] = {}


def _public_host(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SourceError("https の URL だけを使えます")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except socket.gaierror as exc:
        raise SourceError(f"ホスト名を解決できません: {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SourceError("内部のアドレスへは取りに行きません")


def http_get(url: str, *, headers: dict | None = None, data: bytes | None = None, method: str | None = None,
             pace: float = 1.0, timeout: int = 30) -> bytes:
    _public_host(url)
    host = urllib.parse.urlsplit(url).hostname or ""
    wait = pace - (time.time() - _last_call.get(host, 0))
    if wait > 0:
        time.sleep(min(wait, 10))
    _last_call[host] = time.time()
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})}, data=data,
                                     method=method)
    try:
        with _opener.open(request, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SourceError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SourceError(f"接続できません: {getattr(exc, 'reason', exc)}") from exc
    if len(raw) > MAX_BYTES:
        raise SourceError("応答が大きすぎます")
    return raw


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def _date(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else ""


def _record(ext_id: str, title: str, url: str, source: str, body: str, kind: str, biblio: dict | None = None) -> dict:
    return {"ext_id": ext_id[:200], "title": (title or ext_id)[:300], "url": url, "source": source, "body": body or title,
            "kind": kind, "biblio": biblio or {}}


# EPO OPS -------------------------------------------------------------------
_epo_token = {"value": "", "exp": 0.0}


def _epo_auth() -> str:
    key, secret = _secret("epo_key"), _secret("epo_secret")
    if not key or not secret:
        raise SourceError("EPO OPS の Consumer Key / Secret が未設定です（設定してから使えます）")
    if _epo_token["value"] and _epo_token["exp"] > time.time() + 30:
        return _epo_token["value"]
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    raw = http_get("https://ops.epo.org/3.2/auth/accesstoken", data=b"grant_type=client_credentials", method="POST",
                   headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
    data = json.loads(raw.decode())
    _epo_token.update(value=data["access_token"], exp=time.time() + int(data.get("expires_in", 1200)))
    return _epo_token["value"]


def epo(query: str, limit: int) -> list[dict]:
    token = _epo_auth()
    url = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio?" + urllib.parse.urlencode(
        {"q": query, "Range": f"1-{max(1, min(limit, 100))}"})
    raw = http_get(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    return epo_docs(json.loads(raw.decode()))


def epo_docs(data: dict) -> list[dict]:
    def as_list(x):
        return x if isinstance(x, list) else ([x] if x else [])

    def txt(x):
        if isinstance(x, dict):
            return x.get("$", "")
        if isinstance(x, list):
            return " ".join(txt(i) for i in x)
        return str(x or "")

    try:
        results = data["ops:world-patent-data"]["ops:biblio-search"]["ops:search-result"]["exchange-documents"]
    except (KeyError, TypeError):
        return []
    out = []
    for ed in as_list(results):
        for d in as_list(ed.get("exchange-document")):
            pn = f"{d.get('@country', '')}{d.get('@doc-number', '')}{d.get('@kind', '')}"
            bib = d.get("bibliographic-data", {})
            titles = as_list(bib.get("invention-title"))
            title = next((txt(t) for t in titles if isinstance(t, dict) and t.get("@lang") == "ja"), "") or \
                (txt(titles[0]) if titles else pn)
            abstract = " ".join(txt(a.get("p")) for a in as_list(d.get("abstract")) if isinstance(a, dict))
            parties = bib.get("parties", {})
            applicants = [txt(a.get("applicant-name", {}).get("name")) for a in as_list(parties.get("applicants", {}).get("applicant"))
                          if isinstance(a, dict) and a.get("@data-format") in ("epodoc", "original")]
            inventors = [txt(a.get("inventor-name", {}).get("name")) for a in as_list(parties.get("inventors", {}).get("inventor"))
                         if isinstance(a, dict) and a.get("@data-format") in ("epodoc", "original")]
            ipc = [re.sub(r"\s+", " ", txt(c.get("text"))).strip()[:20]
                   for c in as_list(bib.get("classifications-ipcr", {}).get("classification-ipcr")) if isinstance(c, dict)]
            pub = as_list(bib.get("publication-reference", {}).get("document-id"))
            app = as_list(bib.get("application-reference", {}).get("document-id"))
            biblio = {"applicants": list(dict.fromkeys(a for a in applicants if a))[:6],
                      "inventors": list(dict.fromkeys(a for a in inventors if a))[:10],
                      "ipc": list(dict.fromkeys(c for c in ipc if c))[:20],
                      "publication_date": next((_date(txt(p.get("date"))) for p in pub if isinstance(p, dict) and p.get("date")), ""),
                      "filing_date": next((_date(txt(p.get("date"))) for p in app if isinstance(p, dict) and p.get("date")), ""),
                      "priority_date": "", "country": d.get("@country", ""), "kind": d.get("@kind", ""),
                      "claims_count": None, "title": title, "source": "epo"}
            out.append(_record(pn, title, f"https://worldwide.espacenet.com/patent/search?q=pn%3D{pn}", "EPO OPS",
                               f"{title}\n{abstract}", "patent", biblio))
    return out


# USPTO ODP -----------------------------------------------------------------
def uspto(query: str, limit: int) -> list[dict]:
    key = _secret("uspto_key")
    if not key:
        raise SourceError("USPTO ODP の API キーが未設定です（設定してから使えます）")
    url = "https://api.uspto.gov/api/v1/patent/applications/search?" + urllib.parse.urlencode(
        {"q": query, "limit": max(1, min(limit, 100))})
    data = json.loads(http_get(url, headers={"X-API-KEY": key, "Accept": "application/json"}).decode())
    out = []
    for item in data.get("patentFileWrapperDataBag", []) or []:
        meta = item.get("applicationMetaData", {}) or {}
        appno = item.get("applicationNumberText", "")
        title = meta.get("inventionTitle", appno)
        applicants = [a.get("applicantNameText", "") for a in meta.get("applicantBag", []) or [] if isinstance(a, dict)]
        biblio = {"applicants": [a for a in applicants if a][:6], "inventors": [], "ipc": [],
                  "filing_date": _date(meta.get("filingDate", "")), "publication_date": _date(meta.get("earliestPublicationDate", "")),
                  "priority_date": "", "country": "US", "kind": "", "claims_count": None, "title": title, "source": "uspto"}
        out.append(_record(f"US{appno}", title, f"https://data.uspto.gov/patent-file-wrapper/search/details/{appno}",
                           "USPTO ODP", title, "patent", biblio))
    return out


# OpenAlex ------------------------------------------------------------------
def openalex(query: str, limit: int) -> list[dict]:
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"search": query, "per-page": max(1, min(limit, 50)), "select": "id,doi,display_name,publication_date,authorships,abstract_inverted_index,primary_location"})
    data = json.loads(http_get(url, headers={"Accept": "application/json"}).decode())
    out = []
    for w in data.get("results", []) or []:
        inv = w.get("abstract_inverted_index") or {}
        words = sorted(((pos, word) for word, positions in inv.items() for pos in positions), key=lambda p: p[0])
        abstract = " ".join(word for _, word in words)[:20000]
        authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", []) or []][:10]
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        title = w.get("display_name") or doi
        out.append(_record(doi or w.get("id", ""), title, w.get("doi") or w.get("id", ""), "OpenAlex",
                           f"{title}\n{abstract}" + (f"\nDOI: {doi}" if doi else ""), "paper",
                           {"authors": authors, "publication_date": w.get("publication_date", ""), "source": "openalex"}))
    return out


# RSS / Atom ----------------------------------------------------------------
def rss(url: str, limit: int) -> list[dict]:
    raw = http_get(url, pace=2.0)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceError(f"フィードを読めません: {exc}") from exc
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//a:entry", ns)
    out = []
    for it in items[:limit]:
        def t(tag):
            el = it.find(tag, ns) if ":" in tag else it.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        link = t("link") or (it.find("a:link", ns).get("href", "") if it.find("a:link", ns) is not None else "")
        title = t("title") or t("a:title")
        summary = _strip_html(t("description") or t("a:summary") or t("a:content"))
        out.append(_record(link or title, title, link, urllib.parse.urlsplit(url).hostname or "RSS",
                           f"{title}\n{summary}", "news", {"publication_date": _date(t("pubDate") or t("a:updated")),
                                                         "source": "rss"}))
    return out


# GDELT ---------------------------------------------------------------------
def gdelt(query: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({"query": query, "mode": "ArtList", "format": "json", "sort": "DateDesc",
                                     "maxrecords": max(1, min(limit, 250)), "timespan": "1w"})
    raw = http_get("https://api.gdeltproject.org/api/v2/doc/doc?" + params, pace=5.5)
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise SourceError("GDELT の応答を読めません") from exc
    return [_record(a.get("url", ""), a.get("title", ""), a.get("url", ""), a.get("domain", "GDELT"), a.get("title", ""),
                    "news", {"publication_date": _date(a.get("seendate", "")), "source": "gdelt"})
            for a in data.get("articles", []) or []]


FETCHERS = {"epo": epo, "uspto": uspto, "openalex": openalex, "rss": rss, "gdelt": gdelt}


def fetch(source: str, query: str, limit: int = 25) -> list[dict]:
    if source not in FETCHERS:
        raise SourceError("知らない取得先です")
    query = (query or "").strip()
    if not query or len(query) > 500:
        raise SourceError("検索語（1〜500 文字）を入れてください")
    return FETCHERS[source](query, max(1, min(int(limit), MAX_RESULTS)))


async def ingest(records: list[dict]) -> dict:
    """取得した書誌を統合ストアへ。種類ごとのコレクション（公報は特許の分野）。既定は機密。"""
    import asyncio

    from app.knowledge import ingest as kingest
    from app.knowledge import store
    from app.workflows import rag

    added, unchanged, refs = 0, 0, []
    for rec in records:
        name, shard, kind = COLLECTIONS[rec["kind"]]
        if await asyncio.to_thread(store.find_collection, name) is None:
            await asyncio.to_thread(store.create_collection, name, shard=shard, kind=kind, config=dict(rag.DEFAULT_CONFIG),
                                    description="公開データの取得（公式 API・フィード）")
        chunks = kingest.paragraph_chunks(rec["body"])
        model, _ = await kingest._embed(chunks, name)
        import hashlib

        result = await asyncio.to_thread(
            store.add_document, name, chunks, source=rec["source"], title=rec["title"], url=rec["url"],
            ext_id=rec["ext_id"], sha256=hashlib.sha256(rec["body"].encode()).hexdigest(), body=rec["body"],
            meta={"biblio": rec["biblio"]}, model=model)
        ref = f"{shard}:{result['doc_id']}"
        if result.get("unchanged"):
            unchanged += 1
        else:
            added += 1
        refs.append({"ref": ref, "new": not result.get("unchanged"), "title": rec["title"], "kind": rec["kind"]})
    return {"added": added, "unchanged": unchanged, "documents": refs}
