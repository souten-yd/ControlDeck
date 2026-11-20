"""ナレッジの作り直し（2026-11-21 利用者「RAG もデータの再構築が必要ではないか。今のデータは使い物になるか」）。

本番のデータを調べると次の問題があった（v0.102 で旧データを取り込んだまま）:
- 旧 RAG では AI の回答（「web回答: …」など）も原文と同じ扱いで入っていた → 推論（model_inference）に直す
- ResearchDataHub から移した公報は埋め込みが無く、書誌・ページ・図のページの画像も無い → 原本の PDF から新しい取り込みで作り直す
- 埋め込みが無い断片・別のモデルで作った断片 → いまのモデルで作り直す（次元の違うベクトルは検索に使われない）

既定は計画の表示だけ（書かない）。--apply で実行する。元の原本・旧ファイルは消さない。

  python -m app.knowledge.rebuild            # 何を直すかを表示
  python -m app.knowledge.rebuild --apply    # 直す（--no-vlm で図表の読み取りを省く）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import numpy as np

from app.knowledge import documents, store
from app.knowledge.migrate import ANSWER_TITLE


def _docs(shard: str) -> list[dict]:
    if not store.shard_path(shard).exists():
        return []
    conn = store.connect(shard)
    try:
        rows = conn.execute("SELECT d.id, d.title, d.basis, d.ai_use, d.meta, c.name AS collection, c.kind AS ckind "
                            "FROM documents d JOIN collections c ON c.id=d.collection_id").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def plan() -> dict:
    from app.workflows import rag

    retag, reembed, reingest = [], [], []
    for shard in store.SHARDS:
        for d in _docs(shard):
            ref = f"{shard}:{d['id']}"
            if d["basis"] == "source_fact" and ANSWER_TITLE.match(d["title"] or ""):
                retag.append(ref)
            meta = json.loads(d["meta"] or "{}")
            if d["ckind"] in ("patent", "generic") and "pdf" not in meta:
                doc = documents.get_document(ref)
                if any(a["role"] == "original" and a.get("mime") == "application/pdf" for a in doc["assets"]):
                    reingest.append(ref)
                    continue
            if d["ai_use"] == "prohibited":
                continue
            model = rag.get_config(d["collection"])["embed_model"]
            conn = store.connect(shard)
            try:
                row = conn.execute("SELECT COUNT(*), SUM(CASE WHEN v.model=? THEN 1 ELSE 0 END) FROM chunks c "
                                   "LEFT JOIN vectors v ON v.chunk_id=c.id WHERE c.doc_id=?", (model, d["id"])).fetchone()
            finally:
                conn.close()
            if row[0] and (row[1] or 0) < row[0]:
                reembed.append(ref)
    return {"retag_ai_answers": retag, "reembed": reembed, "reingest_pdf": reingest}


def _retag(refs: list[str]) -> int:
    for ref in refs:
        shard, doc_id = documents.parse_ref(ref)
        conn = store.connect(shard)
        try:
            conn.execute("UPDATE documents SET basis='model_inference' WHERE id=?", (doc_id,))
            conn.commit()
        finally:
            conn.close()
    return len(refs)


async def _reembed(ref: str) -> int:
    from app.workflows import rag

    shard, doc_id = documents.parse_ref(ref)
    doc = await asyncio.to_thread(documents.get_document, ref)
    cfg = await asyncio.to_thread(rag.get_config, doc["collection"])
    conn = store.connect(shard)
    try:
        rows = conn.execute("SELECT id, text FROM chunks WHERE doc_id=? ORDER BY idx, id", (doc_id,)).fetchall()
    finally:
        conn.close()
    vectors = await rag._embed_batched([r["text"] for r in rows], cfg, "")
    conn = store.connect(shard)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for row, vec in zip(rows, vectors):
            arr = np.asarray(vec, dtype=np.float32)
            conn.execute("INSERT INTO vectors(chunk_id, model, dim, vec) VALUES(?,?,?,?) ON CONFLICT(chunk_id) DO UPDATE "
                         "SET model=excluded.model, dim=excluded.dim, vec=excluded.vec",
                         (row["id"], cfg["embed_model"], int(arr.shape[0]), arr.tobytes()))
        store._bump_vectors(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(rows)


async def _reingest(ref: str, describe_figures: bool) -> str:
    from app.knowledge import ingest

    doc = await asyncio.to_thread(documents.get_document, ref)
    original = next(a for a in doc["assets"] if a["role"] == "original")
    path = store.blob_path(original["sha256"])
    result = await ingest.ingest_pdf(doc["collection"], path, title="", ext_id=doc["ext_id"],
                                     sensitivity=doc["sensitivity"], ai_use=doc["ai_use"], describe_figures=describe_figures)
    new_ref = f"{documents.parse_ref(ref)[0]}:{result['doc_id']}"
    if new_ref != ref:
        await asyncio.to_thread(_repoint, ref, new_ref)
    return new_ref


def _repoint(old: str, new: str) -> None:
    """案件の先行文献・ウォッチのヒットが古い文書を指していたら、作り直した文書へ付け替える。"""
    if not store.shard_path("patents").exists():
        return
    conn = store.connect("patents")
    try:
        for table in ("patent_refs", "patent_watch_hits"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                conn.execute(f"UPDATE {table} SET doc_ref=? WHERE doc_ref=?", (new, old))
        conn.commit()
    finally:
        conn.close()


async def apply(*, describe_figures: bool = True, job=None) -> dict:
    todo = await asyncio.to_thread(plan)
    out = {"retagged": await asyncio.to_thread(_retag, todo["retag_ai_answers"]), "reembedded": 0, "reingested": [],
           "errors": []}
    total = len(todo["reembed"]) + len(todo["reingest_pdf"])
    done = 0
    for ref in todo["reingest_pdf"]:
        if job is not None:
            job.set_progress(f"PDF を作り直しています（{done + 1}/{total}）", done, total)
        try:
            out["reingested"].append(await _reingest(ref, describe_figures))
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で全体を止めない（結果に残す）
            out["errors"].append({"ref": ref, "error": str(exc)[:200]})
        done += 1
    for ref in todo["reembed"]:
        if job is not None:
            job.set_progress(f"埋め込みを作り直しています（{done + 1}/{total}）", done, total)
        try:
            out["reembedded"] += await _reembed(ref)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"ref": ref, "error": str(exc)[:200]})
        done += 1
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="./deck.sh knowledge-rebuild")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-vlm", action="store_true", help="図表の読み取り（画像モデル）を省く")
    args = parser.parse_args(argv)
    if args.apply:
        result = asyncio.run(apply(describe_figures=not args.no_vlm))
    else:
        todo = plan()
        result = {k: {"count": len(v), "examples": v[:5]} for k, v in todo.items()}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
