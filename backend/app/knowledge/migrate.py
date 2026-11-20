"""旧データの取り込み（旧 rag/*.db、ResearchDataHub の library.sqlite3）。

- 元のファイルは読み取り専用で開き、書き換え・削除しない。消すのは検証後の別工程（利用者の承認が要る）。
- 何度流しても同じ結果になる（legacy_imports 表で取り込み済み・削除済みを覚える）。
- 旧 rag/*.db はコレクションを初めて使うときにも 1 件だけ取り込む（ensure_legacy_collection）。
  起動時に全件を走査しない。

CLI:
  python -m app.knowledge.migrate               # 何が取り込まれるかを表示（書かない）
  python -m app.knowledge.migrate --apply       # 取り込む
  python -m app.knowledge.migrate --apply --rdh /path/to/library.sqlite3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np

from app.config import data_dir
from app.knowledge import store

LEGACY_DEFAULT_MODEL = "nomic-embed-text"
RDH_DEFAULT = Path("/data1tb/ResearchDataHub/data/library.sqlite3")


def legacy_dir() -> Path:
    return data_dir() / "rag"


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _status(source: str) -> str | None:
    if not store.shard_path("general").exists():
        return None
    conn = store.connect("general")
    try:
        row = conn.execute("SELECT status FROM legacy_imports WHERE source=?", (source,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _mark(source: str, status: str, detail: dict) -> None:
    conn = store.connect("general")
    try:
        conn.execute("INSERT INTO legacy_imports(source, status, detail, updated) VALUES(?,?,?,?) "
                     "ON CONFLICT(source) DO UPDATE SET status=excluded.status, detail=excluded.detail, "
                     "updated=excluded.updated", (source, status, json.dumps(detail, ensure_ascii=False), time.time()))
        conn.commit()
    finally:
        conn.close()


def mark_deleted(name: str) -> None:
    """コレクションを消したら、旧ファイルから復活させない。"""
    if (legacy_dir() / f"{name}.db").exists():
        _mark(f"rag:{name}", "deleted", {})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _legacy_plan(path: Path) -> dict:
    conn = _ro(path)
    try:
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        row = conn.execute("SELECT value FROM meta WHERE key='config'").fetchone()
    finally:
        conn.close()
    config = {}
    if row:
        try:
            config = json.loads(row[0])
        except json.JSONDecodeError:
            config = {}
    return {"name": path.stem, "documents": docs, "chunks": chunks, "config": config}


def import_legacy_collection(path: Path) -> dict:
    name = path.stem
    source = f"rag:{name}"
    if _status(source) in ("imported", "deleted"):
        return {"name": name, "skipped": _status(source)}
    if store.find_collection(name) is not None:
        _mark(source, "imported", {"note": "同名のコレクションが既にあった"})
        return {"name": name, "skipped": "exists"}
    plan = _legacy_plan(path)
    config = plan["config"]
    # 旧 rag.py は既定設定のとき管理中の埋め込みへ追従していたので、記録上のモデル名は当てにならない。
    # 検索は同じ名前が無ければ次元で選ぶ（store.search の allow_other_models）。
    model = str(config.get("embed_model") or LEGACY_DEFAULT_MODEL)
    kind = "conversation" if name.startswith("chat-") else "general"
    store.create_collection(name, shard="general", kind=kind, sensitivity="confidential", config=config,
                            description=str(config.get("description", "")), legacy_source=str(path))
    conn = _ro(path)
    imported_chunks = 0
    try:
        docs = conn.execute("SELECT id, source, added_at, meta FROM documents ORDER BY id").fetchall()
        for doc in docs:
            rows = conn.execute("SELECT text, parent, embedding, dim FROM chunks WHERE doc_id=? ORDER BY idx, id",
                                (doc["id"],)).fetchall()
            chunks = []
            for row in rows:
                vec = None
                if row["embedding"]:
                    arr = np.frombuffer(row["embedding"], dtype=np.float32)
                    if row["dim"] in (None, arr.shape[0]):
                        vec = arr
                chunks.append(store.NewChunk(text=row["text"] or "", parent=row["parent"], vector=vec))
            try:
                meta = json.loads(doc["meta"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            result = store.add_document(name, chunks, source=doc["source"] or "document",
                                        title=doc["source"] or "", meta=meta, model=model,
                                        ext_id=f"legacy:{doc['id']}")
            _set_created(name, result["doc_id"], doc["added_at"])
            imported_chunks += len(chunks)
    finally:
        conn.close()
    detail = {"documents": len(docs), "chunks": imported_chunks, "sha256": _sha256_file(path)}
    _mark(source, "imported", detail)
    return {"name": name, **detail}


def _set_created(name: str, doc_id: int, added_at) -> None:
    if not added_at:
        return
    ref = store.require_collection(name)
    conn = store.connect(ref.shard)
    try:
        conn.execute("UPDATE documents SET created=? WHERE id=?", (float(added_at), doc_id))
        conn.commit()
    finally:
        conn.close()


def pending_legacy(skip: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """まだ取り込んでいない旧コレクションの件数（読み取りだけ）。"""
    out = []
    for path in sorted(legacy_dir().glob("*.db")) if legacy_dir().exists() else []:
        if path.stem in skip or not store.COLLECTION_RE.match(path.stem):
            continue
        if _status(f"rag:{path.stem}") in ("imported", "deleted"):
            continue
        try:
            out.append(_legacy_plan(path))
        except sqlite3.Error:
            continue
    return out


def ensure_legacy_collection(name: str) -> bool:
    """旧 rag/{name}.db があり未取り込みなら取り込む。取り込んだら True。"""
    if not store.COLLECTION_RE.match(name or ""):
        return False
    path = legacy_dir() / f"{name}.db"
    if not path.exists():
        return False
    with _legacy_lock:  # 同じ会話への同時の初回アクセスで二重に取り込まない
        if _status(f"rag:{name}") in ("imported", "deleted") or store.find_collection(name) is not None:
            return False
        import_legacy_collection(path)
    return True


_legacy_lock = threading.Lock()


# ---- ResearchDataHub ----

def _rdh_plan(path: Path) -> dict:
    conn = _ro(path)
    try:
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for t in ("collections", "documents", "chunks", "vectors", "document_pdfs",
                            "document_pdf_images", "document_analyses", "patents", "patent_claims")}
        collections = [dict(r) for r in conn.execute("SELECT id, name, kind FROM collections")]
    finally:
        conn.close()
    return {"path": str(path), "counts": counts, "collections": collections}


def import_rdh(path: Path) -> dict:
    """ResearchDataHub の文献を patents 分野へ取り込む。原本 PDF・図はハッシュ名のファイルへ移す。

    patents/patent_claims（特許案件）は現データでは 0 件。件数があれば止めて利用者に知らせる
    （案件は未公開アイデアそのものなので、別の取り込み設計で扱う）。
    """
    source = f"rdh:{_sha256_file(path)}"
    if _status(source) == "imported":
        return {"skipped": "imported", "source": source}
    plan = _rdh_plan(path)
    if plan["counts"]["patents"] or plan["counts"]["patent_claims"]:
        raise store.KnowledgeError("ResearchDataHub に特許案件があります。案件の取り込みは未対応のため中止しました")
    conn = _ro(path)
    report = {"collections": 0, "documents": 0, "chunks": 0, "blobs": 0, "annotations": 0}
    try:
        for coll in conn.execute("SELECT * FROM collections ORDER BY id").fetchall():
            name = f"rdh-{coll['name']}"[:64]
            if store.find_collection(name) is None:
                store.create_collection(name, shard="patents", kind=coll["kind"] or "generic",
                                        sensitivity="confidential", description=coll["description"] or "",
                                        legacy_source=f"{path}#collection={coll['id']}")
                report["collections"] += 1
            for doc in conn.execute("SELECT * FROM documents WHERE collection_id=? ORDER BY id",
                                    (coll["id"],)).fetchall():
                rows = conn.execute("SELECT ord, section, text FROM chunks WHERE doc_id=? ORDER BY ord",
                                    (doc["id"],)).fetchall()
                chunks = [store.NewChunk(text=r["text"], locator={"section": r["section"], "ord": r["ord"]})
                          for r in rows]
                try:
                    meta = json.loads(doc["meta"] or "{}")
                except json.JSONDecodeError:
                    meta = {}
                meta["researchdatahub"] = {"doc_id": doc["id"], "collection": coll["name"], "doc_date": doc["doc_date"]}
                result = store.add_document(name, chunks, source=doc["source"] or "", title=doc["title"] or "",
                                            url=doc["url"] or "", ext_id=doc["ext_id"], sha256=doc["hash"] or "",
                                            body=doc["body"], meta=meta)
                if result.get("unchanged"):
                    continue
                report["documents"] += 1
                report["chunks"] += len(chunks)
                report["blobs"] += _copy_rdh_assets(conn, int(doc["id"]), name, result["doc_id"])
                report["annotations"] += _copy_rdh_analyses(conn, int(doc["id"]), name, result["doc_id"])
    finally:
        conn.close()
    _mark(source, "imported", report)
    return {"source": source, **report}


def _copy_rdh_assets(conn: sqlite3.Connection, rdh_doc: int, name: str, doc_id: int) -> int:
    ref = store.require_collection(name)
    assets: list[tuple[str, str, int, dict]] = []
    for pdf in conn.execute("SELECT * FROM document_pdfs WHERE doc_id=?", (rdh_doc,)).fetchall():
        original = conn.execute("SELECT content FROM source_pdfs WHERE sha256=?", (pdf["source_sha256"],)).fetchone()
        if original is not None:
            digest = store.put_blob(ref.shard, bytes(original[0]), "application/pdf", role="original")
            if digest != pdf["source_sha256"]:
                raise store.KnowledgeError("原本 PDF のハッシュが一致しません")
            assets.append((digest, "original", 0, {}))
        extraction = (pdf["extraction"] or "").encode("utf-8")
        digest = store.put_blob(ref.shard, extraction, "application/json", role="extraction")
        assets.append((digest, "extraction", 0, {"extraction_sha256": pdf["extraction_sha256"]}))
    for image in conn.execute("SELECT i.image_id, i.sha256, b.content FROM document_pdf_images i "
                              "JOIN pdf_image_blobs b ON b.sha256=i.sha256 WHERE i.doc_id=? ORDER BY i.image_id",
                              (rdh_doc,)).fetchall():
        content = bytes(image["content"])
        mime = "image/png" if content[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if content[:2] == b"\xff\xd8" \
            else "application/octet-stream"
        digest = store.put_blob(ref.shard, content, mime, role="figure")
        if digest != image["sha256"]:
            raise store.KnowledgeError("画像のハッシュが一致しません")
        assets.append((digest, "figure", int(image["image_id"]), {"source": "pdf_embedded_raster"}))
    dst = store.connect(ref.shard)
    try:
        for digest, role, ord_, meta in assets:
            dst.execute("INSERT INTO document_assets(doc_id, sha256, role, ord, meta) VALUES(?,?,?,?,?)",
                        (doc_id, digest, role, ord_, json.dumps(meta)))
        dst.commit()
    finally:
        dst.close()
    return len(assets)


def _copy_rdh_analyses(conn: sqlite3.Connection, rdh_doc: int, name: str, doc_id: int) -> int:
    ref = store.require_collection(name)
    rows = conn.execute("SELECT * FROM document_analyses WHERE doc_id=? ORDER BY created", (rdh_doc,)).fetchall()
    dst = store.connect(ref.shard)
    try:
        for row in rows:
            # PatentBuddy のローカル AI 要約・請求項確認。原文と照合されるまで未確認の推論として扱う。
            dst.execute("INSERT INTO annotations(doc_id, kind, basis, review_status, content, source_revision, created) "
                        "VALUES(?,?,?,?,?,?,?)", (doc_id, row["kind"], "model_inference", "unreviewed",
                                                  row["content"], row["source_revision"], row["created"]))
        dst.commit()
    finally:
        dst.close()
    return len(rows)


def plan_all(rdh: Path | None) -> dict:
    paths = sorted(legacy_dir().glob("*.db")) if legacy_dir().exists() else []
    legacy = [_legacy_plan(p) | {"status": _status(f"rag:{p.stem}")} for p in paths]
    out = {"legacy_rag": legacy,
           "legacy_totals": {"collections": len(legacy), "documents": sum(x["documents"] for x in legacy),
                             "chunks": sum(x["chunks"] for x in legacy)}}
    if rdh is not None and rdh.exists():
        out["researchdatahub"] = _rdh_plan(rdh)
    return out


def apply_all(rdh: Path | None) -> dict:
    paths = sorted(legacy_dir().glob("*.db")) if legacy_dir().exists() else []
    results = [import_legacy_collection(p) for p in paths]
    out = {"legacy_rag": results}
    if rdh is not None and rdh.exists():
        out["researchdatahub"] = import_rdh(rdh)
    out["stats"] = store.stats()
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.knowledge.migrate")
    parser.add_argument("--apply", action="store_true", help="取り込む（指定しなければ計画の表示だけ）")
    parser.add_argument("--rdh", type=Path, default=RDH_DEFAULT, help="ResearchDataHub の library.sqlite3")
    parser.add_argument("--no-rdh", action="store_true")
    args = parser.parse_args(argv)
    rdh = None if args.no_rdh else args.rdh
    result = apply_all(rdh) if args.apply else plan_all(rdh)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
