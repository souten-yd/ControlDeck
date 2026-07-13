"""外部情報検索ソース群（統合ノードから呼ばれる）。

- arxiv:    arXiv 論文（キー不要）
- crossref: Crossref 文献/DOI（キー不要）
- patent:   PatentsView 特許（無料 API キーが必要 = data.uspto.gov で取得）
- market:   SEC EDGAR 全文検索（企業/市場調査・キー不要）

すべて {title, snippet, url, meta} に正規化して返す。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

UA = "ControlDeck/1.0 (research; contact@example.com)"


class SearchError(Exception):
    pass


async def arxiv(query: str, limit: int) -> list[dict]:
    params = {"search_query": f"all:{query}", "start": "0", "max_results": str(limit),
              "sortBy": "relevance", "sortOrder": "descending"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get("https://export.arxiv.org/api/query", params=params)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall("a:entry", ns):
        pdf = next((l.get("href", "") for l in e.findall("a:link", ns) if l.get("title") == "pdf"), "")
        out.append({
            "title": (e.findtext("a:title", "", ns) or "").strip(),
            "snippet": (e.findtext("a:summary", "", ns) or "").strip()[:1000],
            "url": (e.findtext("a:id", "", ns) or "").strip(),
            "meta": {"authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
                     "published": e.findtext("a:published", "", ns), "pdf": pdf},
        })
    return out


async def crossref(query: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}) as client:
        r = await client.get("https://api.crossref.org/works", params={"query": query, "rows": str(limit)})
    if r.status_code >= 400:
        raise SearchError(f"Crossref エラー {r.status_code}")
    out = []
    for it in r.json().get("message", {}).get("items", []):
        out.append({
            "title": (it.get("title") or [""])[0],
            "snippet": (it.get("abstract", "") or "")[:1000],
            "url": it.get("URL", ""),
            "meta": {"authors": [f"{a.get('given','')} {a.get('family','')}".strip() for a in it.get("author", [])],
                     "doi": it.get("DOI", ""), "container": (it.get("container-title") or [""])[0],
                     "published": "-".join(str(x) for x in (it.get("published", {}).get("date-parts", [[None]])[0] or []))},
        })
    return out


async def patent(query: str, limit: int, api_key: str) -> list[dict]:
    if not api_key:
        raise SearchError("特許検索には PatentsView の無料 API キーが必要です（data.uspto.gov で取得し、ノードの API キー欄に設定）")
    import json as _json

    body = {
        "q": {"_text_any": {"patent_title": query}},
        "f": ["patent_id", "patent_title", "patent_abstract", "patent_date", "assignees.assignee_organization"],
        "o": {"size": limit},
    }
    async with httpx.AsyncClient(timeout=30, headers={"X-Api-Key": api_key, "User-Agent": UA}) as client:
        r = await client.post("https://search.patentsview.org/api/v1/patent/", content=_json.dumps(body))
    if r.status_code >= 400:
        raise SearchError(f"PatentsView エラー {r.status_code}: {r.text[:150]}")
    out = []
    for p in r.json().get("patents", []) or []:
        pid = p.get("patent_id", "")
        assignees = ", ".join(a.get("assignee_organization", "") for a in (p.get("assignees") or []) if a.get("assignee_organization"))
        out.append({
            "title": p.get("patent_title", ""),
            "snippet": (p.get("patent_abstract", "") or "")[:1000],
            "url": f"https://patents.google.com/patent/US{pid}",
            "meta": {"patent_id": pid, "date": p.get("patent_date", ""), "assignee": assignees},
        })
    return out


async def market(query: str, limit: int) -> list[dict]:
    """SEC EDGAR 全文検索（企業の開示文書＝市場/競合調査に有用）。"""
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}) as client:
        r = await client.get("https://efts.sec.gov/LATEST/search-index", params={"q": query})
    if r.status_code >= 400:
        raise SearchError(f"SEC EDGAR エラー {r.status_code}")
    hits = r.json().get("hits", {}).get("hits", [])[:limit]
    out = []
    for h in hits:
        src = h.get("_source", {})
        names = "; ".join(src.get("display_names", []) or [])
        adsh = (src.get("_id") or h.get("_id", "")).split(":")[0].replace("-", "")
        cik = (src.get("ciks") or [""])[0]
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}" if cik else ""
        out.append({
            "title": f"{names} — {src.get('file_type','')} ({src.get('file_date','')})",
            "snippet": f"{src.get('file_description','')}",
            "url": url,
            "meta": {"form": src.get("file_type", ""), "date": src.get("file_date", ""), "ciks": src.get("ciks", [])},
        })
    return out


SOURCES = {"arxiv": arxiv, "crossref": crossref, "patent": patent, "market": market}


async def search(source: str, query: str, limit: int, api_key: str = "") -> list[dict]:
    limit = max(1, min(int(limit), 50))
    try:
        if source == "patent":
            return await patent(query, limit, api_key)
        fn = SOURCES.get(source)
        if fn is None:
            raise SearchError(f"不明な検索ソース: {source}")
        return await fn(query, limit)
    except httpx.HTTPError as e:
        raise SearchError(f"{source} 取得失敗: {e}")
    except ET.ParseError as e:
        raise SearchError(f"{source} 応答の解析に失敗: {e}")
