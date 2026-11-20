"""規格の登録・一覧・検索（docs/research-patent-standards-rag.md §2.4、docs/standards-license-policy.md）。

- 規格は data_dir/knowledge/standards.sqlite3 のコレクション「standards」に入れる。
- 登録のたびに standards_license.evaluate で、本文を保存してよいか・AI（埋め込み・LLM）に使ってよいかを決める。
  保存が許されなければ番号・題名・版だけ（条項番号で探せるように、題名のメタデータの断片だけ）を持つ。
- 本文は条項見出しで分割する（ingest.clause_chunks）。番号での完全一致を最優先にする（documents.find_by_identifier）。
- 同じ規格番号の新しい版を登録したら、古い版を「旧版」にする（現行の要求として使わない）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.knowledge import biblio, documents, ingest, store
from app.knowledge import standards_license as sl

COLLECTION = "standards"
STATUSES = ("current", "superseded", "withdrawn", "unknown")


def ensure_collection() -> store.CollectionRef:
    ref = store.find_collection(COLLECTION)
    if ref is None:
        from app.workflows import rag

        ref = store.create_collection(COLLECTION, shard="standards", kind="standard", sensitivity="confidential",
                                      config=dict(rag.DEFAULT_CONFIG), description="規格（番号・版・条項）")
    return ref


def decide(designation: str, instrument: str, *, permission_ref: str = "", reviewer: str = "",
           reviewed_on: str = "", storage: str | None = None, ai_use: str | None = None) -> sl.Decision:
    return sl.evaluate(sl.LicenseRecord(designation=designation, instrument=instrument, permission_ref=permission_ref,
                                        reviewer=reviewer, reviewed_on=reviewed_on, storage=storage, ai_use=ai_use))


def _own(designation: str, edition: str) -> list[biblio.Identifier]:
    found = [i for i in biblio.extract(designation) if i.scheme == "standard"]
    if not found:
        raise store.KnowledgeError("規格番号を読み取れません（例: JIS C 61000-4-2:2012、IEC 61000-4-2）")
    head = found[0]
    return [biblio.Identifier("standard", head.value, edition or head.edition, designation)]


async def register(*, designation: str, title: str, edition: str = "", status: str = "current", instrument: str,
                   text: str = "", pdf_path: str = "", permission_ref: str = "", reviewer: str = "",
                   reviewed_on: str = "", granted_storage: str | None = None, granted_ai_use: str | None = None,
                   note: str = "", job=None) -> dict:
    if status not in STATUSES:
        raise store.KnowledgeError("不正な状態です")
    own = _own(designation, edition)
    decision = decide(designation, instrument, permission_ref=permission_ref, reviewer=reviewer,
                      reviewed_on=reviewed_on, storage=granted_storage, ai_use=granted_ai_use)
    store_fulltext = decision.storage == "shared_server"
    ai_use = decision.ai_use
    await asyncio.to_thread(ensure_collection)
    meta = {"standard": {"designation": designation, "title": title, "edition": own[0].edition, "status": status,
                         "note": note[:2000]},
            "license": decision.to_dict(), "license_input": {"instrument": instrument, "permission_ref": permission_ref,
                                                             "reviewer": reviewer, "reviewed_on": reviewed_on,
                                                             "storage": granted_storage, "ai_use": granted_ai_use}}
    ext_id = f"std:{own[0].value}:{own[0].edition or '-'}"
    label = f"{designation} {title}".strip()
    if pdf_path and store_fulltext:
        result = await ingest.ingest_pdf(COLLECTION, Path(pdf_path), title=label, ai_use=ai_use, clauses=True,
                                         meta=meta, ext_id=ext_id, identifiers=own, job=job)
    else:
        result = await ingest.ingest_text(COLLECTION, text if store_fulltext else "", title=label, source=designation,
                                          clauses=True, ai_use=ai_use, store_fulltext=store_fulltext and bool(text.strip()),
                                          meta=meta, ext_id=ext_id, identifiers=own)
    if status == "current":
        await asyncio.to_thread(_supersede_older, own[0], result["doc_id"])
    return {"doc_ref": f"standards:{result['doc_id']}", "license": decision.to_dict(),
            "fulltext_stored": store_fulltext and bool(text.strip() or pdf_path)}


def _supersede_older(ident: biblio.Identifier, keep_doc: int) -> None:
    """同じ規格番号で版が違う「現行」を旧版にする。"""
    conn = store.connect("standards")
    try:
        rows = conn.execute("SELECT DISTINCT d.id, d.meta FROM identifiers i JOIN documents d ON d.id=i.doc_id "
                            "WHERE i.own=1 AND i.scheme='standard' AND i.value=? AND d.id<>?", (ident.value, keep_doc)).fetchall()
        for row in rows:
            meta = json.loads(row["meta"] or "{}")
            std = meta.get("standard", {})
            if std.get("status") == "current" and std.get("edition") != ident.edition:
                std["status"] = "superseded"
                meta["standard"] = std
                conn.execute("UPDATE documents SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), row["id"]))
        conn.commit()
    finally:
        conn.close()


def list_standards() -> list[dict]:
    if store.find_collection(COLLECTION) is None:
        return []
    out = []
    for doc in store.list_documents(COLLECTION):
        full = documents.get_document(f"standards:{doc['id']}")
        std = full["meta"].get("standard", {})
        lic = full["meta"].get("license", {})
        out.append({"ref": full["ref"], "designation": std.get("designation", full["title"]), "title": std.get("title", ""),
                    "edition": std.get("edition", ""), "status": std.get("status", "unknown"),
                    "storage": lic.get("storage", "metadata_only"), "ai_use": full["ai_use"],
                    "needs_review": lic.get("needs_review", True), "reasons": lic.get("reasons", []),
                    "chunks": full["chunk_count"], "pdf": full["meta"].get("pdf", {})})
    return out


def search(question: str, *, include_superseded: bool = False, limit: int = 20) -> list[dict]:
    """規格の中を探す（画面用）。番号の完全一致を先頭に、条項ごとの当たりを返す。旧版は既定で除く。"""
    if store.find_collection(COLLECTION) is None:
        return []
    ranked = documents.search_documents(question, names=[COLLECTION], consumer="human", mode="fulltext",
                                        limit=limit, expand=False)
    out = []
    for doc in ranked:
        full = documents.get_document(doc["ref"])
        std = full["meta"].get("standard", {})
        if std.get("status") == "superseded" and not include_superseded:
            continue
        out.append({"ref": doc["ref"], "designation": std.get("designation", full["title"]), "title": std.get("title", ""),
                    "edition": std.get("edition", ""), "status": std.get("status", "unknown"), "exact": doc["relation"] == "exact",
                    "clauses": [{"clause": h["locator"].get("clause", ""), "heading": h["locator"].get("heading", ""),
                                 "page": h["locator"].get("page"), "snippet": documents.snippet(h["text"], question)}
                                for h in doc["hits"][:5]]})
    return out


def remove(ref: str) -> None:
    shard, doc_id = documents.parse_ref(ref)
    if shard != "standards":
        raise store.KnowledgeError("規格ではありません")
    store.delete_document(COLLECTION, doc_id)
