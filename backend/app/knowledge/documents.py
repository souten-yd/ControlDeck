"""文書を単位にした検索・全文・骨格・書誌の関係（docs/research-patent-standards-rag.md §2.1〜2.3）。

- search_documents: 断片の検索結果を文書ごとに集め、文書を並べる。上位の文書から書誌の関係で 1 hop だけ広げる。
- document_text: 文書の全文（保存した本文。無ければ断片を元の順につなぐ）。
- document_outline: 全文が入らないときの骨格（特許: 要約・請求項、規格: 適用範囲・引用規格）と当たった節（元の順）。
- related_documents: 識別子（公報番号・規格番号・DOI）で結んだ確定した関係（引用する・引用される・別の版）。
- allowed_for: 答えを作るモデル（local / cloud / human）に渡してよい文書か（機密区分と規格の AI 利用）。
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from app.knowledge import store, textproc

CONSUMERS = ("human", "local", "cloud")
MAX_RELATED = 6


def parse_ref(ref: str) -> tuple[str, int]:
    shard, _, number = (ref or "").partition(":")
    if shard not in store.SHARDS or not number.isdigit():
        raise store.KnowledgeError("文書の参照が不正です")
    return shard, int(number)


def allowed_for(doc: dict, consumer: str, bound: Iterable[str] = ()) -> bool:
    """human は画面での閲覧（規格の本文の表示は保存の許可で決まる）。local / cloud は LLM へ渡す場合。"""
    if consumer == "human":
        return True
    ai_use = doc.get("ai_use", "allowed")
    if consumer == "local":
        return ai_use in ("local_only", "allowed")
    if ai_use != "allowed":
        return False
    return doc.get("sensitivity") == "public" or doc.get("collection") in set(bound)


def get_document(ref: str) -> dict:
    shard, doc_id = parse_ref(ref)
    if not store.shard_path(shard).exists():
        raise store.KnowledgeError("文書がありません")
    conn = store.connect(shard)
    try:
        row = conn.execute("SELECT d.*, c.name AS collection, c.kind AS collection_kind FROM documents d "
                           "JOIN collections c ON c.id=d.collection_id WHERE d.id=?", (doc_id,)).fetchone()
        if row is None:
            raise store.KnowledgeError("文書がありません")
        idents = [dict(r) for r in conn.execute(
            "SELECT scheme, value, edition, own, raw FROM identifiers WHERE doc_id=? ORDER BY own DESC, id", (doc_id,))]
        assets = [dict(r) for r in conn.execute(
            "SELECT a.sha256, a.role, a.ord, a.meta, b.mime, b.bytes FROM document_assets a "
            "LEFT JOIN blobs b ON b.sha256=a.sha256 WHERE a.doc_id=? ORDER BY a.role, a.ord", (doc_id,))]
        notes = [dict(r) for r in conn.execute(
            "SELECT id, kind, basis, review_status, content, source_revision, created FROM annotations WHERE doc_id=? ORDER BY id",
            (doc_id,))]
    finally:
        conn.close()
    meta = json.loads(row["meta"] or "{}")
    for asset in assets:
        asset["meta"] = json.loads(asset["meta"] or "{}")
    return {"ref": ref, "shard": shard, "id": doc_id, "collection": row["collection"],
            "collection_kind": row["collection_kind"], "title": row["title"] or row["source"], "source": row["source"],
            "url": row["url"], "ext_id": row["ext_id"], "sensitivity": row["sensitivity"], "basis": row["basis"],
            "ai_use": row["ai_use"], "meta": meta, "chunk_count": row["chunk_count"], "created": row["created"],
            "identifiers": [i for i in idents if i["own"]], "references": [i for i in idents if not i["own"]],
            "assets": assets, "annotations": notes}


def _chunks(shard: str, doc_id: int) -> list[dict]:
    conn = store.connect(shard)
    try:
        rows = conn.execute("SELECT id, idx, text, parent, locator FROM chunks WHERE doc_id=? ORDER BY idx, id",
                            (doc_id,)).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "idx": r["idx"], "text": r["text"], "parent": r["parent"],
             "locator": json.loads(r["locator"] or "{}")} for r in rows]


def document_text(ref: str) -> str:
    shard, doc_id = parse_ref(ref)
    conn = store.connect(shard)
    try:
        row = conn.execute("SELECT text FROM bodies WHERE doc_id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if row is not None and row[0].strip():
        return row[0]
    parts, seen = [], set()
    for chunk in _chunks(shard, doc_id):
        if chunk["locator"].get("kind") == "metadata":   # 番号・題名だけの断片は本文ではない
            continue
        piece = chunk["parent"] or chunk["text"]
        if piece in seen:
            continue
        seen.add(piece)
        parts.append(piece)
    return "\n\n".join(parts)


_CLAIMS_HEAD = re.compile(r"【特許請求の範囲】|【請求項\s*[1１]】|^\s*(?:What is claimed is|Claims)\s*:?\s*$", re.M | re.I)
_ABSTRACT_HEAD = re.compile(r"【要約】|^\s*Abstract\s*$", re.M | re.I)
_ABSTRACT_END = re.compile(r"【選択図】|【特許請求の範囲】|【発明の詳細な説明】|【技術分野】|【書誌事項】")
_SECTION_END = re.compile(r"\n【(?!請求項)[^】]{1,12}】")


def _section(text: str, head: re.Pattern, limit: int) -> str:
    m = head.search(text)
    if not m:
        return ""
    start = m.start()
    tail = text[m.end():]
    end = _ABSTRACT_END.search(tail) if head is _ABSTRACT_HEAD else _SECTION_END.search(tail)
    if head is _CLAIMS_HEAD:
        end = re.search(r"\n【(?!請求項)(?!特許請求)[^】]{1,12}】", tail)
    return text[start:m.end() + (end.start() if end else len(tail))][:limit].strip()


def document_outline(ref: str, hit_chunk_ids: Iterable[int] = (), limit: int = 12000) -> str:
    """全文が入らないときの骨格と、当たった箇所（元の順・親の見出し付き）。"""
    shard, doc_id = parse_ref(ref)
    doc = get_document(ref)
    text = document_text(ref)
    head = [f"題名: {doc['title']}"]
    own = ", ".join(f"{i['raw'] or i['value']}" for i in doc["identifiers"][:5])
    if own:
        head.append(f"識別子: {own}")
    parts = ["\n".join(head)]
    kind = doc["collection_kind"]
    if kind in ("patent", "generic") or _CLAIMS_HEAD.search(text):
        abstract = _section(text, _ABSTRACT_HEAD, 1500)
        claims = _section(text, _CLAIMS_HEAD, limit // 2)
        if abstract:
            parts.append(abstract)
        if claims:
            parts.append(claims)
    chunks = _chunks(shard, doc_id)
    if kind == "standard":
        # 適用範囲（1）と引用規格（2）は必ず入れる
        for chunk in chunks:
            clause = str(chunk["locator"].get("clause", ""))
            if clause in ("1", "2") or clause.startswith(("1.", "2.")):
                parts.append(chunk["text"])
    hits = set(int(i) for i in hit_chunk_ids)
    picked = [c for c in chunks if c["id"] in hits]
    for chunk in picked:   # 元の順
        heading = chunk["locator"].get("heading") or ""
        page = chunk["locator"].get("page")
        label = " ".join(x for x in (f"[{chunk['locator'].get('clause')}]" if chunk["locator"].get("clause") else "",
                                     heading, f"(p.{page})" if page else "") if x)
        piece = chunk["parent"] or chunk["text"]
        parts.append((label + "\n" if label else "") + piece)
    out, size = [], 0
    for part in parts:
        if part in out:
            continue
        if size + len(part) > limit:
            out.append(part[: max(0, limit - size)])
            break
        out.append(part)
        size += len(part)
    return "\n\n…\n\n".join(p for p in out if p)


def related_documents(ref: str, limit: int = MAX_RELATED) -> list[dict]:
    """識別子で結んだ関係。cites=この文書が参照する文書、cited_by=この文書を参照する文書、version=同じ番号の別の版。"""
    shard, doc_id = parse_ref(ref)
    conn = store.connect(shard)
    try:
        mine = [dict(r) for r in conn.execute("SELECT scheme, value, edition, own FROM identifiers WHERE doc_id=?", (doc_id,))]
    finally:
        conn.close()
    own = [(i["scheme"], i["value"]) for i in mine if i["own"]]
    refs = [(i["scheme"], i["value"]) for i in mine if not i["own"]]
    out: list[dict] = []
    seen = {ref}
    for other in store.SHARDS:
        if not store.shard_path(other).exists():
            continue
        conn = store.connect(other)
        try:
            def query(pairs, own_flag):
                rows = []
                for start in range(0, len(pairs), 200):
                    part = pairs[start:start + 200]
                    cond = " OR ".join(["(scheme=? AND value=?)"] * len(part))
                    flat = [x for pair in part for x in pair]
                    rows += conn.execute(
                        f"SELECT DISTINCT i.doc_id, i.value, d.title, d.sensitivity, d.ai_use, c.name AS collection "
                        f"FROM identifiers i JOIN documents d ON d.id=i.doc_id JOIN collections c ON c.id=d.collection_id "
                        f"WHERE i.own=? AND ({cond}) LIMIT ?", (own_flag, *flat, limit * 4)).fetchall()
                return rows
            for relation, pairs, flag in (("cites", refs, 1), ("cited_by", own, 0), ("version", own, 1)):
                if not pairs:
                    continue
                for row in query(pairs, flag):
                    target = f"{other}:{row['doc_id']}"
                    if target in seen:
                        continue
                    seen.add(target)
                    out.append({"ref": target, "relation": relation, "via": row["value"], "title": row["title"],
                                "collection": row["collection"], "sensitivity": row["sensitivity"],
                                "ai_use": row["ai_use"]})
        finally:
            conn.close()
    order = {"cites": 0, "version": 1, "cited_by": 2}
    out.sort(key=lambda r: order[r["relation"]])
    return out[:limit]


def find_by_identifier(text: str) -> list[str]:
    """番号での完全一致（規格番号・公報番号）。"""
    from app.knowledge import biblio

    pairs = [(i.scheme, i.value) for i in biblio.extract(text)]
    out = []
    for shard in store.SHARDS:
        if not pairs or not store.shard_path(shard).exists():
            continue
        conn = store.connect(shard)
        try:
            cond = " OR ".join(["(scheme=? AND value=?)"] * len(pairs))
            flat = [x for pair in pairs for x in pair]
            out += [f"{shard}:{r[0]}" for r in conn.execute(
                f"SELECT DISTINCT doc_id FROM identifiers WHERE own=1 AND ({cond})", flat)]
        finally:
            conn.close()
    return out


def search_documents(question: str, *, names: Iterable[str] | None = None, consumer: str = "human",
                     bound: Iterable[str] = (), query_vector=None, model: str = "", mode: str = "hybrid",
                     limit: int = 10, expand: bool = True) -> list[dict]:
    """断片を探して文書ごとに集め、文書の順位を返す。番号の完全一致は先頭。1 hop の関係を足す。"""
    if consumer not in CONSUMERS:
        raise store.KnowledgeError("consumer は human / local / cloud")
    bound = list(bound)
    # 会話の資料は、その会話の中（bound）でだけ使う。別の会話の AI の回答や雑談（天気など）を混ぜない
    names = list(names) if names is not None else [c["collection"] for c in store.list_collections()
                                                    if c["kind"] != "conversation" or c["collection"] in bound]
    hits = store.search(names, question, query_vector=query_vector, model=model, mode=mode, limit=limit * 10)
    docs: dict[str, dict] = {}
    for hit in hits:
        if not allowed_for(hit, consumer, bound):
            continue
        entry = docs.setdefault(hit["doc_ref"], {
            "ref": hit["doc_ref"], "title": hit["title"] or hit["source"], "collection": hit["collection"],
            "sensitivity": hit["sensitivity"], "ai_use": hit["ai_use"], "score": 0.0, "best": 0.0,
            "hits": [], "relation": "match"})
        entry["hits"].append({"chunk_id": int(hit["chunk_ref"].split(":")[1]), "text": hit["text"],
                              "locator": hit["locator"], "score": hit["score"]})
        entry["best"] = max(entry["best"], hit["score"])
    for entry in docs.values():
        scores = sorted((h["score"] for h in entry["hits"]), reverse=True)
        entry["score"] = round(scores[0] + 0.2 * sum(scores[1:4]), 5)
    exact_all = find_by_identifier(question)
    for ref in exact_all:   # 番号が完全に一致した文書は、本文の当たりがあっても先頭へ
        if ref in docs:
            docs[ref]["relation"] = "exact"
            docs[ref]["score"] = 10.0 + docs[ref]["score"]
    exact = [r for r in exact_all if r not in docs]
    ranked = sorted(docs.values(), key=lambda d: -d["score"])
    for ref in reversed(exact):
        try:
            doc = get_document(ref)
        except store.KnowledgeError:
            continue
        if names and doc["collection"] not in names:
            continue
        if not allowed_for(doc, consumer, bound):
            continue
        ranked.insert(0, {"ref": ref, "title": doc["title"], "collection": doc["collection"],
                          "sensitivity": doc["sensitivity"], "ai_use": doc["ai_use"], "score": 1.0, "best": 1.0,
                          "hits": [], "relation": "exact"})
    ranked = ranked[:limit]
    if expand:
        have = {d["ref"] for d in ranked}
        extra = []
        for doc in ranked[:3]:
            for rel in related_documents(doc["ref"], limit=3):
                if rel["ref"] in have or not allowed_for(rel, consumer, bound):
                    continue
                have.add(rel["ref"])
                extra.append({"ref": rel["ref"], "title": rel["title"], "collection": rel["collection"],
                              "sensitivity": rel["sensitivity"], "ai_use": rel["ai_use"],
                              "score": round(doc["score"] * 0.5, 5), "best": 0.0, "hits": [],
                              "relation": rel["relation"], "via": rel["via"], "from": doc["ref"]})
        ranked += extra[:MAX_RELATED]
    return ranked


def snippet(text: str, question: str, width: int = 180) -> str:
    return textproc.snippet(text, textproc.query_terms(question), width)
