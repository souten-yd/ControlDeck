"""特許の分析（ResearchDataHub v0.3 の集計と、PatentBuddy の 10 種のグラフの試作を本体に統合したもの）。

書誌（meta["biblio"]、`knowledge/biblio.py` の形）から、年別の推移・出願人・IPC・出願人×IPC（バブル）・出願人×年（ヒートマップ）・
請求項の数・国と種別・類似マップ（文書ベクトルを 2 次元に縮めたもの）を集計する。案件を指定すれば、案件の請求項に近い公報も返す。

- 書誌が読めていない公報は数えず、どれだけ読めているか（coverage）を必ず返す（画面で「n 件中 m 件の書誌から」と出す）。
- 公報の選び方によって偏る（取り込んだ公報だけの集計で、業界全体の傾向ではない）ことを note で返す。
- 読み取りだけ。書き込みも外部への送信もしない。
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import numpy as np

from app.knowledge import store

MAX_DOCS = 5000
MAX_MAP = 300
TOP = 15


def _documents(collection: str | None = None) -> list[dict]:
    if not store.shard_path("patents").exists():
        return []
    conn = store.connect("patents")
    try:
        sql = ("SELECT d.id, d.title, d.meta, c.name AS collection FROM documents d JOIN collections c ON c.id=d.collection_id "
               "WHERE c.kind IN ('patent', 'generic')")
        args: list = []
        if collection:
            sql += " AND c.name=?"
            args.append(collection)
        rows = conn.execute(sql + " ORDER BY d.id DESC LIMIT ?", (*args, MAX_DOCS)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        out.append({"ref": f"patents:{row['id']}", "id": int(row["id"]), "title": row["title"],
                    "collection": row["collection"], "biblio": meta.get("biblio") or {}})
    return out


def _year(b: dict) -> str:
    for key in ("publication_date", "filing_date", "priority_date"):
        value = str(b.get(key) or "")
        if re.match(r"(19|20)\d{2}", value):
            return value[:4]
    return ""


def _applicant(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def _subclass(code: str) -> str:
    return re.sub(r"\s+", "", code)[:4].upper()


def overview(collection: str | None = None) -> dict:
    docs = _documents(collection)
    years, applicants, subclasses, countries, kinds = Counter(), Counter(), Counter(), Counter(), Counter()
    claims: list[int] = []
    pair, app_year = Counter(), Counter()
    for d in docs:
        b = d["biblio"]
        y = _year(b)
        apps = [_applicant(a) for a in b.get("applicants") or [] if a]
        subs = sorted({_subclass(c) for c in b.get("ipc") or [] if c})
        if y:
            years[y] += 1
        for a in apps:
            applicants[a] += 1
            if y:
                app_year[(a, y)] += 1
            for s in subs:
                pair[(a, s)] += 1
        for s in subs:
            subclasses[s] += 1
        if b.get("country"):
            countries[str(b["country"])] += 1
        if b.get("kind"):
            kinds[str(b["kind"])] += 1
        if isinstance(b.get("claims_count"), int):
            claims.append(int(b["claims_count"]))
    top_apps = [a for a, _ in applicants.most_common(8)]
    top_subs = [s for s, _ in subclasses.most_common(8)]
    buckets = [(1, 5), (6, 10), (11, 15), (16, 20), (21, 30), (31, 999)]
    return {
        "documents": len(docs),
        "coverage": {"year": sum(years.values()), "applicant": sum(1 for d in docs if d["biblio"].get("applicants")),
                     "ipc": sum(1 for d in docs if d["biblio"].get("ipc")), "claims": len(claims)},
        "by_year": [{"key": k, "count": years[k]} for k in sorted(years)],
        "applicants": [{"key": k, "count": n} for k, n in applicants.most_common(TOP)],
        "ipc_subclasses": [{"key": k, "count": n} for k, n in subclasses.most_common(TOP)],
        "countries": [{"key": k, "count": n} for k, n in countries.most_common()],
        "kinds": [{"key": k, "count": n} for k, n in kinds.most_common()],
        "applicant_ipc": [{"applicant": a, "ipc": s, "count": pair[(a, s)]} for a in top_apps for s in top_subs if pair[(a, s)]],
        "applicant_year": [{"applicant": a, "year": y, "count": n} for (a, y), n in sorted(app_year.items()) if a in top_apps],
        "claims_histogram": [{"key": f"{lo}–{hi}" if hi < 999 else f"{lo}–", "count": sum(1 for c in claims if lo <= c <= hi)}
                             for lo, hi in buckets],
        "note": "取り込んだ公報だけの集計です。選び方によって偏るので、業界全体の傾向とは限りません。書誌が読めていない公報は数えていません。",
    }


def similarity_map(collection: str | None = None) -> dict:
    """公報ごとの平均ベクトルを主成分で 2 次元にする（近いほど内容が似ている）。埋め込みが無い公報は出さない。"""
    docs = {d["id"]: d for d in _documents(collection)[:MAX_MAP * 3]}
    if not docs:
        return {"points": [], "model": "", "explained": []}
    conn = store.connect("patents")
    try:
        marks = ",".join("?" * len(docs))
        rows = conn.execute(f"SELECT c.doc_id, v.model, v.dim, v.vec FROM vectors v JOIN chunks c ON c.id=v.chunk_id "
                            f"WHERE c.doc_id IN ({marks})", tuple(docs)).fetchall()
    finally:
        conn.close()
    by_model: dict[tuple[str, int], dict[int, list[np.ndarray]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_model[(row["model"], row["dim"])][row["doc_id"]].append(np.frombuffer(row["vec"], dtype=np.float32))
    if not by_model:
        return {"points": [], "model": "", "explained": []}
    (model, _), groups = max(by_model.items(), key=lambda kv: len(kv[1]))
    ids = list(groups)[:MAX_MAP]
    if len(ids) < 3:
        return {"points": [], "model": model, "explained": []}
    mat = np.stack([np.mean(groups[i], axis=0) for i in ids])
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
    centered = mat - mat.mean(axis=0)
    _, s, vt = np.linalg.svd(centered, full_matrices=False)
    xy = centered @ vt[:2].T
    var = (s ** 2) / max(float((s ** 2).sum()), 1e-12)
    points = []
    for i, doc_id in enumerate(ids):
        b = docs[doc_id]["biblio"]
        points.append({"ref": f"patents:{doc_id}", "title": docs[doc_id]["title"], "x": round(float(xy[i, 0]), 5),
                       "y": round(float(xy[i, 1]), 5), "applicant": (b.get("applicants") or [""])[0],
                       "ipc": _subclass((b.get("ipc") or [""])[0]) if b.get("ipc") else "", "year": _year(b)})
    return {"points": points, "model": model, "explained": [round(float(v), 4) for v in var[:2]]}


def set_biblio(ref: str, fields: dict) -> dict:
    """書誌を手で直す（source=manual）。OCR の読み違いを直すとき。"""
    from app.knowledge import documents

    shard, doc_id = documents.parse_ref(ref)
    allowed = {"title", "applicants", "inventors", "ipc", "filing_date", "publication_date", "priority_date",
               "country", "kind", "claims_count"}
    clean: dict = {}
    for key, value in fields.items():
        if key not in allowed:
            raise store.KnowledgeError(f"書誌の項目ではありません: {key}")
        if key in ("applicants", "inventors", "ipc"):
            clean[key] = [str(v).strip()[:80] for v in (value or []) if str(v).strip()][:20]
        elif key == "claims_count":
            clean[key] = int(value) if value not in (None, "") else None
        elif key.endswith("_date"):
            value = str(value or "")
            if value and not re.fullmatch(r"(19|20)\d{2}-\d{2}-\d{2}", value):
                raise store.KnowledgeError("日付は YYYY-MM-DD で入れてください")
            clean[key] = value
        else:
            clean[key] = str(value or "")[:120]
    conn = store.connect(shard)
    try:
        row = conn.execute("SELECT meta FROM documents WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            raise store.KnowledgeError("文書がありません")
        meta = json.loads(row[0] or "{}")
        meta["biblio"] = {**(meta.get("biblio") or {}), **clean, "source": "manual"}
        conn.execute("UPDATE documents SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), doc_id))
        conn.commit()
    finally:
        conn.close()
    return meta["biblio"]


async def refresh_biblio(job=None) -> dict:
    """書誌の無い公報（原本 PDF があるもの）を読み直す。v0.102 より前に取り込んだ公報のため。"""
    import asyncio

    from app.knowledge import documents, ingest

    targets = [d for d in await asyncio.to_thread(_documents) if not d["biblio"]]
    done = 0
    for i, d in enumerate(targets):
        if job is not None:
            job.set_progress(f"書誌を読んでいます（{i + 1}/{len(targets)}）", i, len(targets))
        doc = await asyncio.to_thread(documents.get_document, d["ref"])
        original = next((a for a in doc["assets"] if a["role"] == "original"), None)
        body = await asyncio.to_thread(documents.document_text, d["ref"])
        if original is not None:
            fields = await ingest._patent_biblio(store.blob_path(original["sha256"]), body)
        else:
            from app.knowledge import biblio

            fields = {**biblio.patent_fields(body), "source": "rule_extract"}
        await asyncio.to_thread(_write_biblio, d["id"], fields)
        done += 1
    return {"updated": done}


def _write_biblio(doc_id: int, fields: dict) -> None:
    conn = store.connect("patents")
    try:
        row = conn.execute("SELECT meta, title FROM documents WHERE id=?", (doc_id,)).fetchone()
        meta = json.loads(row[0] or "{}")
        meta["biblio"] = fields
        title = row[1] or ""
        if fields.get("title") and fields["title"] not in title:
            title = f"{title} {fields['title']}".strip()
        conn.execute("UPDATE documents SET meta=?, title=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), title, doc_id))
        conn.commit()
    finally:
        conn.close()
