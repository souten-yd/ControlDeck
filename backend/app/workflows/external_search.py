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


async def openalex(query: str, limit: int) -> list[dict]:
    """OpenAlex（2.5億件超・全分野・キー不要）。"""
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}) as client:
        r = await client.get("https://api.openalex.org/works", params={"search": query, "per-page": limit, "mailto": "research@example.com"})
    if r.status_code >= 400:
        raise SearchError(f"OpenAlex エラー {r.status_code}")
    out = []
    for w in r.json().get("results", []):
        # abstract は inverted index なので語順復元
        inv = w.get("abstract_inverted_index") or {}
        abstract = ""
        if inv:
            positions = sorted((pos, word) for word, poss in inv.items() for pos in poss)
            abstract = " ".join(w for _, w in positions)[:1000]
        out.append({
            "title": w.get("title", "") or "",
            "snippet": abstract,
            "url": w.get("doi") or w.get("id", ""),
            "meta": {"year": w.get("publication_year"), "cited_by": w.get("cited_by_count"),
                     "authors": [a.get("author", {}).get("display_name", "") for a in (w.get("authorships") or [])][:8],
                     "oa": (w.get("open_access") or {}).get("oa_url", "")},
        })
    return out


async def semanticscholar(query: str, limit: int) -> list[dict]:
    """Semantic Scholar（キー不要・レート制限あり）。"""
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}) as client:
        r = await client.get("https://api.semanticscholar.org/graph/v1/paper/search",
                             params={"query": query, "limit": limit, "fields": "title,abstract,url,year,authors,citationCount,openAccessPdf"})
    if r.status_code == 429:
        raise SearchError("Semantic Scholar が混雑しています（時間をおいて再試行）")
    if r.status_code >= 400:
        raise SearchError(f"Semantic Scholar エラー {r.status_code}")
    out = []
    for p in r.json().get("data", []) or []:
        out.append({
            "title": p.get("title", "") or "",
            "snippet": (p.get("abstract") or "")[:1000],
            "url": p.get("url", ""),
            "meta": {"year": p.get("year"), "cited_by": p.get("citationCount"),
                     "authors": [a.get("name", "") for a in (p.get("authors") or [])][:8],
                     "pdf": (p.get("openAccessPdf") or {}).get("url", "")},
        })
    return out


async def europepmc(query: str, limit: int) -> list[dict]:
    """Europe PMC（生医学・ライフサイエンス中心・キー不要）。"""
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}) as client:
        r = await client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                             params={"query": query, "format": "json", "pageSize": limit})
    if r.status_code >= 400:
        raise SearchError(f"Europe PMC エラー {r.status_code}")
    out = []
    for it in r.json().get("resultList", {}).get("result", []):
        pmid = it.get("pmid", "")
        out.append({
            "title": it.get("title", "") or "",
            "snippet": (it.get("abstractText") or "")[:1000],
            "url": f"https://europepmc.org/article/{it.get('source','MED')}/{it.get('id','')}",
            "meta": {"year": it.get("pubYear"), "journal": it.get("journalTitle", ""), "authors": it.get("authorString", ""), "pmid": pmid},
        })
    return out


async def doaj(query: str, limit: int) -> list[dict]:
    """DOAJ（オープンアクセス学術誌・キー不要）。"""
    from urllib.parse import quote

    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}) as client:
        r = await client.get(f"https://doaj.org/api/search/articles/{quote(query)}", params={"pageSize": limit})
    if r.status_code >= 400:
        raise SearchError(f"DOAJ エラー {r.status_code}")
    out = []
    for it in r.json().get("results", []):
        b = it.get("bibjson", {})
        link = next((l.get("url", "") for l in b.get("link", []) if l.get("url")), "")
        out.append({
            "title": b.get("title", "") or "",
            "snippet": (b.get("abstract") or "")[:1000],
            "url": link,
            "meta": {"year": b.get("year"), "journal": (b.get("journal") or {}).get("title", ""),
                     "authors": [a.get("name", "") for a in (b.get("author") or [])][:8]},
        })
    return out


async def dblp(query: str, limit: int) -> list[dict]:
    """DBLP（計算機科学の書誌・キー不要）。"""
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}) as client:
        r = await client.get("https://dblp.org/search/publ/api", params={"q": query, "format": "json", "h": limit})
    if r.status_code >= 400:
        raise SearchError(f"DBLP エラー {r.status_code}")
    hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
    out = []
    for h in hits:
        info = h.get("info", {})
        authors = info.get("authors", {}).get("author", [])
        if isinstance(authors, dict):
            authors = [authors]
        out.append({
            "title": info.get("title", "") or "",
            "snippet": f"{info.get('venue','')} {info.get('year','')}",
            "url": info.get("ee") or info.get("url", ""),
            "meta": {"year": info.get("year"), "venue": info.get("venue", ""),
                     "authors": [a.get("text", "") if isinstance(a, dict) else str(a) for a in authors][:8]},
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


SOURCES = {
    "arxiv": arxiv, "crossref": crossref, "openalex": openalex,
    "semanticscholar": semanticscholar, "europepmc": europepmc, "doaj": doaj,
    "dblp": dblp, "patent": patent, "market": market,
}
# 串刺し検索（all）で並列に叩くキー不要の学術ソース
FEDERATED_SOURCES = ["openalex", "crossref", "arxiv", "europepmc", "dblp", "doaj"]


def _norm_title(t: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "", (t or "").lower())[:80]


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


async def federated(query: str, limit_per: int, sources: list[str] | None = None) -> dict:
    """複数ソースを並列に叩き、タイトル/DOI で重複統合する串刺し検索。"""
    import asyncio

    srcs = sources or FEDERATED_SOURCES
    limit_per = max(1, min(int(limit_per), 25))
    results = await asyncio.gather(
        *(search(s, query, limit_per) for s in srcs), return_exceptions=True
    )
    merged: list[dict] = []
    seen: set[str] = set()
    errors: dict[str, str] = {}
    for src, res in zip(srcs, results):
        if isinstance(res, Exception):
            errors[src] = str(res)[:120]
            continue
        for item in res:
            key = _norm_title(item.get("title", "")) or item.get("url", "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append({**item, "source": src})
    # 被引用数があれば多い順、なければ元の順
    merged.sort(key=lambda x: -(x.get("meta", {}).get("cited_by") or 0))
    return {"results": merged, "count": len(merged), "sources": srcs, "errors": errors}
