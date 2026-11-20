"""ControlDeck の統合ナレッジストア（一般・特許・規格）。

ResearchDataHub と PatentBuddy を ControlDeck へ統合するための正本（docs/PLAN-knowledge-platform.md）。

- 分野（shard）ごとに SQLite ファイルを分ける: data_dir/knowledge/{general,patents,standards}.sqlite3。
  呼び出し側はコレクション名だけを渡し、どのファイルにあるかを意識しない。
  後で別のサーバーへ分けられるよう、ファイルをまたぐ JOIN・トランザクションは作らない。
- 全文検索は ResearchDataHub と同じ日本語 2-gram（unicode61 + textproc）。2 文字語（「電圧」等）も当たる。
- ベクトルは vectors 表にモデル名つきで持ち、検索時は RAM 上の正規化済み行列で一括計算する
  （旧 rag.py の「毎回全件を読んで 1 件ずつ計算」を置き換える）。書き込みのたびに vec_gen を上げ、
  キャッシュはそれを見て作り直す。
- 機密区分 sensitivity: confidential（既定）/ public。外部（クラウド）LLM 向けの横断検索では
  confidential を返さない（アイデアの流出防止。policy は search_all を参照）。
- 原本・図などの大きなファイルは blobs/sha256 にハッシュ名で置き、DB には参照と保存先（location）だけを持つ。
- どの保存先（この PC・NAS など）に何を置くかは app.knowledge.storage の設定で決める。

同期関数だけを持つ。async から呼ぶときは asyncio.to_thread を使うこと（AGENTS.md 絶対ルール 9）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from app.knowledge import storage, textproc

SHARDS = ("general", "patents", "standards")
SENSITIVITY = ("confidential", "public")
BASIS = ("source_fact", "model_inference", "user_authored")
COLLECTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collections(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'general',
  sensitivity TEXT NOT NULL DEFAULT 'confidential', config TEXT NOT NULL DEFAULT '{}',
  description TEXT NOT NULL DEFAULT '', legacy_source TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, collection_id INTEGER NOT NULL, ext_id TEXT,
  title TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL DEFAULT '', sensitivity TEXT NOT NULL DEFAULT 'confidential',
  basis TEXT NOT NULL DEFAULT 'source_fact', meta TEXT NOT NULL DEFAULT '{}',
  chunk_count INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE(collection_id, ext_id));
CREATE INDEX IF NOT EXISTS ix_documents_collection ON documents(collection_id, id DESC);
CREATE TABLE IF NOT EXISTS bodies(doc_id INTEGER PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, collection_id INTEGER NOT NULL, idx INTEGER NOT NULL,
  text TEXT NOT NULL, parent TEXT, locator TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS ix_chunks_collection ON chunks(collection_id);
CREATE TABLE IF NOT EXISTS vectors(
  chunk_id INTEGER PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS ix_vectors_model ON vectors(model);
CREATE TABLE IF NOT EXISTS blobs(
  sha256 TEXT PRIMARY KEY, mime TEXT NOT NULL, bytes INTEGER NOT NULL, relpath TEXT NOT NULL, created REAL NOT NULL,
  location TEXT NOT NULL DEFAULT 'local', role TEXT NOT NULL DEFAULT 'original');
CREATE TABLE IF NOT EXISTS document_assets(
  id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, sha256 TEXT NOT NULL, role TEXT NOT NULL,
  ord INTEGER NOT NULL DEFAULT 0, meta TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ix_assets_doc ON document_assets(doc_id);
CREATE TABLE IF NOT EXISTS annotations(
  id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, kind TEXT NOT NULL, basis TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', content TEXT NOT NULL,
  source_revision TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_annotations_doc ON annotations(doc_id);
CREATE TABLE IF NOT EXISTS identifiers(
  id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, scheme TEXT NOT NULL, value TEXT NOT NULL,
  edition TEXT NOT NULL DEFAULT '', own INTEGER NOT NULL, raw TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_identifiers_value ON identifiers(scheme, value, own);
CREATE INDEX IF NOT EXISTS ix_identifiers_doc ON identifiers(doc_id);
CREATE TABLE IF NOT EXISTS legacy_imports(
  source TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
"""


class KnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class CollectionRef:
    shard: str
    id: int
    name: str
    kind: str
    sensitivity: str
    config: dict


def shard_path(shard: str) -> Path:
    if shard not in SHARDS:
        raise KnowledgeError(f"不正な分野: {shard}")
    try:
        return storage.db_path(shard)
    except storage.StorageError as exc:
        raise KnowledgeError(str(exc)) from exc


_schema_ready: set[str] = set()
_schema_lock = threading.Lock()


def connect(shard: str) -> sqlite3.Connection:
    path = shard_path(shard)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    # 表があるかを毎回確かめる（ファイルの置き換え・作り直しで、覚えた状態が古くなるため。inode は使い回される）
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identifiers'").fetchone() is None:
        with _schema_lock:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            _add_missing_columns(conn)
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
                         "tok, tokenize='unicode61 remove_diacritics 0')")
            conn.commit()
    elif str(path) not in _schema_ready:
        _add_missing_columns(conn)   # 後から足した列（このプロセスで最初に開いたとき 1 回）
        conn.commit()
        _schema_ready.add(str(path))
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# 後から足した列（既に作られたファイルにも足す。起動時ではなく、そのファイルを初めて開いたとき 1 回）
_LATE_COLUMNS = {
    "documents": [("ai_use", "TEXT NOT NULL DEFAULT 'allowed'")],
    "blobs": [("location", "TEXT NOT NULL DEFAULT 'local'"), ("role", "TEXT NOT NULL DEFAULT 'original'")],
}
AI_USE = ("prohibited", "local_only", "allowed")


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _LATE_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _now() -> float:
    return time.time()


def _bump_vectors(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES('vec_gen', '1') "
                 "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")


# ---- コレクション ----

def _row_to_ref(shard: str, row: sqlite3.Row) -> CollectionRef:
    try:
        config = json.loads(row["config"] or "{}")
    except json.JSONDecodeError:
        config = {}
    return CollectionRef(shard, int(row["id"]), row["name"], row["kind"], row["sensitivity"], config)


def find_collection(name: str) -> CollectionRef | None:
    if not COLLECTION_RE.match(name or ""):
        raise KnowledgeError(f"不正なコレクション名: {name}（英数・ハイフン・アンダースコア 1〜64 文字）")
    for shard in SHARDS:
        if not shard_path(shard).exists():
            continue
        conn = connect(shard)
        try:
            row = conn.execute("SELECT * FROM collections WHERE name=?", (name,)).fetchone()
        finally:
            conn.close()
        if row is not None:
            return _row_to_ref(shard, row)
    return None


def require_collection(name: str) -> CollectionRef:
    ref = find_collection(name)
    if ref is None:
        raise KnowledgeError(f"コレクションが存在しません: {name}")
    return ref


def create_collection(name: str, *, shard: str = "general", kind: str = "general",
                      sensitivity: str = "confidential", config: dict | None = None,
                      description: str = "", legacy_source: str = "") -> CollectionRef:
    if sensitivity not in SENSITIVITY:
        raise KnowledgeError(f"不正な機密区分: {sensitivity}")
    if find_collection(name) is not None:
        raise KnowledgeError(f"コレクションは既に存在します: {name}")
    now = _now()
    conn = connect(shard)
    try:
        conn.execute(
            "INSERT INTO collections(name, kind, sensitivity, config, description, legacy_source, created, updated) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (name, kind, sensitivity, json.dumps(config or {}, ensure_ascii=False), description, legacy_source, now, now))
        conn.commit()
    finally:
        conn.close()
    return require_collection(name)


COLLECTION_KINDS = ("general", "conversation", "patent", "generic", "standard", "law", "classification")


def set_collection_kind(name: str, kind: str) -> CollectionRef:
    """コレクションの種類（審査基準・法令は law、分類辞書は classification）。AI 支援の引用に使う。"""
    if kind not in COLLECTION_KINDS:
        raise KnowledgeError(f"不正な種類: {kind}")
    ref = require_collection(name)
    conn = connect(ref.shard)
    try:
        conn.execute("UPDATE collections SET kind=?, updated=? WHERE id=?", (kind, _now(), ref.id))
        conn.commit()
    finally:
        conn.close()
    return require_collection(name)


def collections_of_kind(kind: str) -> list[str]:
    return [c["collection"] for c in list_collections() if c["kind"] == kind]


def set_collection_config(name: str, config: dict) -> CollectionRef:
    ref = require_collection(name)
    conn = connect(ref.shard)
    try:
        conn.execute("UPDATE collections SET config=?, updated=? WHERE id=?",
                     (json.dumps(config, ensure_ascii=False), _now(), ref.id))
        conn.commit()
    finally:
        conn.close()
    return require_collection(name)


def delete_collection(name: str) -> bool:
    ref = find_collection(name)
    if ref is None:
        return False
    conn = connect(ref.shard)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE collection_id=?", (ref.id,))]
        _delete_chunk_rows(conn, ids)
        docs = [r[0] for r in conn.execute("SELECT id FROM documents WHERE collection_id=?", (ref.id,))]
        _delete_document_side_rows(conn, docs)
        conn.execute("DELETE FROM documents WHERE collection_id=?", (ref.id,))
        conn.execute("DELETE FROM collections WHERE id=?", (ref.id,))
        conn.commit()
    finally:
        conn.close()
    return True


def has_shared_collections() -> bool:
    """会話以外のコレクションがあるか（会話の道具を渡すかの判定。件数は数えない）。"""
    for shard in SHARDS:
        if not shard_path(shard).exists():
            continue
        conn = connect(shard)
        try:
            if conn.execute("SELECT 1 FROM collections WHERE kind<>'conversation' LIMIT 1").fetchone():
                return True
        finally:
            conn.close()
    return False


def list_collections() -> list[dict]:
    out = []
    for shard in SHARDS:
        if not shard_path(shard).exists():
            continue
        conn = connect(shard)
        try:
            rows = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM documents d WHERE d.collection_id=c.id) AS docs, "
                "(SELECT COUNT(*) FROM chunks k WHERE k.collection_id=c.id) AS chunk_total "
                "FROM collections c ORDER BY c.name").fetchall()
        finally:
            conn.close()
        for row in rows:
            ref = _row_to_ref(shard, row)
            out.append({"collection": ref.name, "shard": shard, "kind": ref.kind,
                        "sensitivity": ref.sensitivity, "config": ref.config,
                        "documents": int(row["docs"]), "chunks": int(row["chunk_total"]),
                        "description": row["description"]})
    return out


# ---- 文書 ----

def _delete_chunk_rows(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    if not chunk_ids:
        return
    for start in range(0, len(chunk_ids), 500):
        part = chunk_ids[start:start + 500]
        marks = ",".join("?" * len(part))
        conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", part)
        removed = conn.execute(f"DELETE FROM vectors WHERE chunk_id IN ({marks})", part).rowcount
        conn.execute(f"DELETE FROM chunks WHERE id IN ({marks})", part)
        if removed:
            _bump_vectors(conn)


def _delete_document_side_rows(conn: sqlite3.Connection, doc_ids: list[int]) -> None:
    for start in range(0, len(doc_ids), 500):
        part = doc_ids[start:start + 500]
        marks = ",".join("?" * len(part))
        for table in ("bodies", "document_assets", "annotations", "identifiers"):
            conn.execute(f"DELETE FROM {table} WHERE doc_id IN ({marks})", part)


@dataclass
class NewChunk:
    text: str
    parent: str | None = None
    locator: dict | None = None
    vector: np.ndarray | None = None


def add_document(name: str, chunks: list[NewChunk], *, source: str = "", title: str = "",
                 url: str = "", ext_id: str | None = None, sha256: str = "", body: str | None = None,
                 sensitivity: str | None = None, basis: str = "source_fact", meta: dict | None = None,
                 model: str = "", replace_collection: bool = False, ai_use: str = "allowed",
                 identifiers: list | None = None) -> dict:
    """1 文書を 1 トランザクションで登録する。件数の上限で切り詰めない。

    同じ ext_id の文書があれば置き換える（冪等な再投入）。sha256 も同じなら何も書かない。
    """
    if basis not in BASIS:
        raise KnowledgeError(f"不正な basis: {basis}")
    if ai_use not in AI_USE:
        raise KnowledgeError(f"不正な ai_use: {ai_use}")
    ref = require_collection(name)
    from app.knowledge import biblio

    own = identifiers if identifiers is not None else biblio.own_identifiers(title, ext_id or "", source)
    if identifiers is None and not own and body:
        own = biblio.head_identifiers(body)
    refs = biblio.references(body if body is not None else "\n".join(c.text for c in chunks), own)
    level = sensitivity or ref.sensitivity
    if level not in SENSITIVITY:
        raise KnowledgeError(f"不正な機密区分: {level}")
    now = _now()
    conn = connect(ref.shard)
    try:
        conn.execute("BEGIN IMMEDIATE")
        carried: list = []
        if replace_collection:
            ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE collection_id=?", (ref.id,))]
            _delete_chunk_rows(conn, ids)
            docs = [r[0] for r in conn.execute("SELECT id FROM documents WHERE collection_id=?", (ref.id,))]
            _delete_document_side_rows(conn, docs)
            conn.execute("DELETE FROM documents WHERE collection_id=?", (ref.id,))
        if ext_id is not None:
            old = conn.execute("SELECT id, sha256 FROM documents WHERE collection_id=? AND ext_id=?",
                               (ref.id, ext_id)).fetchone()
            if old is not None and sha256 and old["sha256"] == sha256:
                conn.rollback()
                return {"doc_id": int(old["id"]), "unchanged": True, "added_chunks": 0}
            if old is not None:
                # AI の分析・人のレビューは新しい版へ持ち越す（版が違うので stale と表示される。古い承認は復活させない）
                carried = conn.execute("SELECT kind, basis, review_status, content, source_revision, created "
                                       "FROM annotations WHERE doc_id=?", (old["id"],)).fetchall()
                ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE doc_id=?", (old["id"],))]
                _delete_chunk_rows(conn, ids)
                _delete_document_side_rows(conn, [int(old["id"])])
                conn.execute("DELETE FROM documents WHERE id=?", (old["id"],))
        doc_id = conn.execute(
            "INSERT INTO documents(collection_id, ext_id, title, source, url, sha256, sensitivity, basis, meta, "
            "chunk_count, created, updated, ai_use) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref.id, ext_id, title, source, url, sha256, level, basis,
             json.dumps(meta or {}, ensure_ascii=False), len(chunks), now, now, ai_use)).lastrowid
        conn.executemany("INSERT INTO identifiers(doc_id, scheme, value, edition, own, raw) VALUES(?,?,?,?,?,?)",
                         [(doc_id, i.scheme, i.value, i.edition, 1, i.raw[:120]) for i in own]
                         + [(doc_id, i.scheme, i.value, i.edition, 0, i.raw[:120]) for i in refs])
        if body is not None:
            conn.execute("INSERT INTO bodies(doc_id, text) VALUES(?,?)", (doc_id, body))
        for note in carried:
            conn.execute("INSERT INTO annotations(doc_id, kind, basis, review_status, content, source_revision, created) "
                         "VALUES(?,?,?,?,?,?,?)", (doc_id, *tuple(note)))
        wrote_vectors = False
        for idx, chunk in enumerate(chunks):
            cid = conn.execute(
                "INSERT INTO chunks(doc_id, collection_id, idx, text, parent, locator) VALUES(?,?,?,?,?,?)",
                (doc_id, ref.id, idx, chunk.text, chunk.parent,
                 json.dumps(chunk.locator or {}, ensure_ascii=False))).lastrowid
            conn.execute("INSERT INTO chunks_fts(rowid, tok) VALUES(?,?)", (cid, textproc.index_text(chunk.text)))
            if chunk.vector is not None:
                vec = np.asarray(chunk.vector, dtype=np.float32)
                conn.execute("INSERT INTO vectors(chunk_id, model, dim, vec) VALUES(?,?,?,?)",
                             (cid, model, int(vec.shape[0]), vec.tobytes()))
                wrote_vectors = True
        if wrote_vectors:
            _bump_vectors(conn)
        conn.execute("UPDATE collections SET updated=? WHERE id=?", (now, ref.id))
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM chunks WHERE collection_id=?", (ref.id,)).fetchone()[0]
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"doc_id": int(doc_id), "unchanged": False, "added_chunks": len(chunks), "total_chunks": int(total)}


def list_documents(name: str) -> list[dict]:
    ref = require_collection(name)
    conn = connect(ref.shard)
    try:
        rows = conn.execute(
            "SELECT id, ext_id, title, source, url, sensitivity, basis, meta, chunk_count, created "
            "FROM documents WHERE collection_id=? ORDER BY created DESC, id DESC", (ref.id,)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        out.append({"id": int(row["id"]), "ext_id": row["ext_id"], "title": row["title"],
                    "source": row["source"], "url": row["url"], "sensitivity": row["sensitivity"],
                    "basis": row["basis"], "added_at": row["created"], "chunk_count": int(row["chunk_count"]),
                    "strategy": meta.get("strategy")})
    return out


def delete_document(name: str, doc_id: int) -> bool:
    ref = require_collection(name)
    conn = connect(ref.shard)
    try:
        row = conn.execute("SELECT id FROM documents WHERE id=? AND collection_id=?", (doc_id, ref.id)).fetchone()
        if row is None:
            return False
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
        _delete_chunk_rows(conn, ids)
        _delete_document_side_rows(conn, [doc_id])
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    return True


def copy_collection(source: str, target: str) -> bool:
    """会話の分岐用。文書・チャンク・ベクトルを写す（原本ファイルは共有）。"""
    src = find_collection(source)
    if src is None or find_collection(target) is not None:
        return False
    create_collection(target, shard=src.shard, kind=src.kind, sensitivity=src.sensitivity, config=src.config)
    dst = require_collection(target)
    conn = connect(src.shard)
    try:
        conn.execute("BEGIN IMMEDIATE")
        doc_map: dict[int, int] = {}
        for doc in conn.execute("SELECT * FROM documents WHERE collection_id=? ORDER BY id", (src.id,)).fetchall():
            new_id = conn.execute(
                "INSERT INTO documents(collection_id, ext_id, title, source, url, sha256, sensitivity, basis, meta, "
                "chunk_count, created, updated, ai_use) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (dst.id, doc["ext_id"], doc["title"], doc["source"], doc["url"], doc["sha256"], doc["sensitivity"],
                 doc["basis"], doc["meta"], doc["chunk_count"], doc["created"], _now(), doc["ai_use"])).lastrowid
            doc_map[int(doc["id"])] = int(new_id)
            for table, cols in (("bodies", "text"), ("document_assets", "sha256, role, ord, meta"),
                                ("annotations", "kind, basis, review_status, content, source_revision, created"),
                                ("identifiers", "scheme, value, edition, own, raw")):
                conn.execute(f"INSERT INTO {table}(doc_id, {cols}) SELECT ?, {cols} FROM {table} WHERE doc_id=?",
                             (new_id, doc["id"]))
        wrote_vectors = False
        for chunk in conn.execute("SELECT * FROM chunks WHERE collection_id=? ORDER BY id", (src.id,)).fetchall():
            cid = conn.execute(
                "INSERT INTO chunks(doc_id, collection_id, idx, text, parent, locator) VALUES(?,?,?,?,?,?)",
                (doc_map[int(chunk["doc_id"])], dst.id, chunk["idx"], chunk["text"], chunk["parent"],
                 chunk["locator"])).lastrowid
            conn.execute("INSERT INTO chunks_fts(rowid, tok) VALUES(?,?)", (cid, textproc.index_text(chunk["text"])))
            vec = conn.execute("SELECT model, dim, vec FROM vectors WHERE chunk_id=?", (chunk["id"],)).fetchone()
            if vec is not None:
                conn.execute("INSERT INTO vectors(chunk_id, model, dim, vec) VALUES(?,?,?,?)",
                             (cid, vec["model"], vec["dim"], vec["vec"]))
                wrote_vectors = True
        if wrote_vectors:
            _bump_vectors(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        conn.close()
        delete_collection(target)  # 途中で失敗したら空の写し先を残さない
        raise
    conn.close()
    return True


def chunk_rows(name: str) -> list[dict]:
    """GraphRAG などの後処理用。id・text・parent を順に返す。"""
    ref = require_collection(name)
    conn = connect(ref.shard)
    try:
        rows = conn.execute("SELECT id, text, parent FROM chunks WHERE collection_id=? ORDER BY id",
                            (ref.id,)).fetchall()
    finally:
        conn.close()
    return [{"id": int(r["id"]), "text": r["text"], "parent": r["parent"]} for r in rows]


def collection_text_chars(name: str) -> int:
    """断片の文字数の合計（会話の資料の大きさ）。"""
    ref = require_collection(name)
    conn = connect(ref.shard)
    try:
        return int(conn.execute("SELECT COALESCE(SUM(LENGTH(text)), 0) FROM chunks WHERE collection_id=?",
                                (ref.id,)).fetchone()[0])
    finally:
        conn.close()


def material_rows(name: str) -> list[tuple[int, str, str | None, str]]:
    """(doc_id, text, parent, source) を文書の順・断片の順で返す（資料をすべて入れるとき用）。"""
    ref = require_collection(name)
    conn = connect(ref.shard)
    try:
        rows = conn.execute(
            "SELECT c.doc_id, c.text, c.parent, d.source FROM chunks c LEFT JOIN documents d ON d.id=c.doc_id "
            "WHERE c.collection_id=? ORDER BY c.doc_id, c.idx", (ref.id,)).fetchall()
    finally:
        conn.close()
    return [(int(r[0]), r[1], r[2], r[3] or "") for r in rows]


# ---- 原本・図（ハッシュ名のファイル） ----

def _write_blob_file(directory: Path, digest: str, content: bytes) -> None:
    path = directory / digest[:2] / digest
    if path.exists() and path.stat().st_size == len(content):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with tmp.open("wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def put_blob(shard: str, content: bytes, mime: str, role: str = "original") -> str:
    """役割（original / figure / extraction …）に応じた保存先へ置く。

    設定の保存先が使えない（NAS が外れている）ときは「この PC」に置き、location に記録する。
    後で ./deck.sh knowledge-storage relocate で設定どおりの場所へ移す。
    """
    digest = hashlib.sha256(content).hexdigest()
    wanted = storage.blob_target_for(role)
    location = wanted
    try:
        directory = storage.blob_dir(wanted)
    except storage.StorageUnavailable:
        location, directory = storage.LOCAL, storage.blob_dir(storage.LOCAL)
    _write_blob_file(directory, digest, content)
    conn = connect(shard)
    try:
        conn.execute("INSERT OR IGNORE INTO blobs(sha256, mime, bytes, relpath, created, location, role) "
                     "VALUES(?,?,?,?,?,?,?)", (digest, mime, len(content), f"{digest[:2]}/{digest}", _now(), location, role))
        conn.commit()
    finally:
        conn.close()
    return digest


def blob_info(sha256: str) -> tuple[str, sqlite3.Row] | None:
    if not SHA256_RE.match(sha256 or ""):
        raise KnowledgeError("不正なハッシュ")
    for shard in SHARDS:
        if not shard_path(shard).exists():
            continue
        conn = connect(shard)
        try:
            row = conn.execute("SELECT * FROM blobs WHERE sha256=?", (sha256,)).fetchone()
        finally:
            conn.close()
        if row is not None:
            return shard, row
    return None


def blob_path(sha256: str) -> Path:
    """読み出し用のパス。記録された保存先が使えなければバックアップ先の写しを探す。"""
    info = blob_info(sha256)
    if info is None:
        raise KnowledgeError("ファイルがありません")
    _, row = info
    candidates = [row["location"], *[t for t in storage.backup_targets() if t != row["location"]]]
    for name in candidates:
        try:
            directory = storage.blob_dir(name).resolve()
        except storage.StorageError:
            continue
        path = (directory / sha256[:2] / sha256).resolve()
        if directory not in path.parents:
            raise KnowledgeError("不正なパス")
        if path.exists():
            return path
    raise KnowledgeError("ファイルの保存先を使えません（NAS が外れていないか確認）")


# ---- 検索 ----

class _VectorCache:
    """(shard, model) ごとの正規化済み行列。vec_gen が変わったら読み直す。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str, int], tuple[int, np.ndarray, np.ndarray, np.ndarray]] = {}

    def get(self, shard: str, model: str, dim: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        conn = connect(shard)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='vec_gen'").fetchone()
            gen = int(row[0]) if row else 0
            key = (str(shard_path(shard)), model, dim)
            with self._lock:
                hit = self._items.get(key)
                if hit is not None and hit[0] == gen:
                    return hit[1], hit[2], hit[3]
            if model:
                rows = conn.execute(
                    "SELECT v.chunk_id, c.collection_id, v.vec FROM vectors v JOIN chunks c ON c.id=v.chunk_id "
                    "WHERE v.model=? AND v.dim=?", (model, dim)).fetchall()
            else:  # 次元だけで選ぶ（旧 rag.py と同じ。モデル名を記録していない旧データ用）
                rows = conn.execute(
                    "SELECT v.chunk_id, c.collection_id, v.vec FROM vectors v JOIN chunks c ON c.id=v.chunk_id "
                    "WHERE v.dim=?", (dim,)).fetchall()
        finally:
            conn.close()
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        colls = np.fromiter((r[1] for r in rows), dtype=np.int64, count=len(rows))
        if rows:
            mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), dim).copy()
            mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
        else:
            mat = np.zeros((0, dim), dtype=np.float32)
        with self._lock:
            self._items[key] = (gen, ids, colls, mat)
        return ids, colls, mat


_vectors = _VectorCache()


def _fts_ranks(conn: sqlite3.Connection, question: str, collection_ids: list[int], limit: int) -> list[int]:
    marks = ",".join("?" * len(collection_ids))
    sql = ("SELECT c.id FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
           f"WHERE chunks_fts MATCH ? AND c.collection_id IN ({marks}) ORDER BY bm25(chunks_fts) LIMIT ?")
    found: list[int] = []
    strict = textproc.parse_query(question)
    try:
        if strict:
            found = [r[0] for r in conn.execute(sql, (strict, *collection_ids, limit))]
        if len(found) < limit // 4:
            # 文章の質問はフレーズが一致しにくいので、2-gram の OR で補う（ResearchDataHub の bm25_loose）
            loose = textproc.or_query(question)
            if loose:
                seen = set(found)
                for (cid,) in conn.execute(sql, (loose, *collection_ids, limit)):
                    if cid not in seen:
                        found.append(cid)
                        seen.add(cid)
    except sqlite3.OperationalError:
        return found
    return found[:limit]


def search(names: Iterable[str], question: str, *, query_vector: np.ndarray | None = None, model: str = "",
           mode: str = "hybrid", hybrid_weight: float = 0.5, limit: int = 80,
           exclude_confidential: bool = False, allow_other_models: bool = True) -> list[dict]:
    """コレクション（複数・分野をまたいでよい）を検索し、RRF で並べた候補を返す。"""
    by_shard: dict[str, list[CollectionRef]] = {}
    for name in names:
        ref = find_collection(name)
        if ref is not None:
            by_shard.setdefault(ref.shard, []).append(ref)
    k = 60
    weight = min(max(float(hybrid_weight), 0.0), 1.0)
    scored: list[tuple[float, str, int]] = []
    for shard, refs in by_shard.items():
        coll_ids = [r.id for r in refs]
        scores: dict[int, float] = {}
        if mode in ("vector", "hybrid") and query_vector is not None:
            q = np.asarray(query_vector, dtype=np.float32)
            wanted = np.array(coll_ids, dtype=np.int64)
            ids, colls, mat = _vectors.get(shard, model, int(q.shape[0]))
            mask = np.isin(colls, wanted) if len(ids) else np.zeros(0, dtype=bool)
            if not mask.any() and model and allow_other_models:
                ids, colls, mat = _vectors.get(shard, "", int(q.shape[0]))
                mask = np.isin(colls, wanted) if len(ids) else np.zeros(0, dtype=bool)
            if len(ids) and mask.any():
                # 行列は写さない（100k×1024 で 400MB）。全行を計算し、対象外を -inf にする
                sims = mat @ (q / (np.linalg.norm(q) + 1e-8))
                if not mask.all():
                    sims = np.where(mask, sims, -np.inf)
                top = min(limit * 2, int(mask.sum()))
                order = np.argpartition(-sims, top - 1)[:top]
                order = order[np.argsort(-sims[order])]
                w = 1.0 if mode == "vector" else weight
                for rank, pos in enumerate(order):
                    cid = int(ids[pos])
                    scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
        if mode in ("fulltext", "hybrid"):
            conn = connect(shard)
            try:
                ranked = _fts_ranks(conn, question, coll_ids, limit * 2)
            finally:
                conn.close()
            w = 1.0 if mode == "fulltext" else 1.0 - weight
            for rank, cid in enumerate(ranked):
                scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
        scored.extend((score, shard, cid) for cid, score in scores.items())
    scored.sort(key=lambda item: -item[0])
    return _hydrate(scored[:limit * 2], exclude_confidential)[:limit]


def _hydrate(scored: list[tuple[float, str, int]], exclude_confidential: bool) -> list[dict]:
    by_shard: dict[str, list[int]] = {}
    for _, shard, cid in scored:
        by_shard.setdefault(shard, []).append(cid)
    rows: dict[tuple[str, int], sqlite3.Row] = {}
    for shard, ids in by_shard.items():
        conn = connect(shard)
        try:
            for start in range(0, len(ids), 500):
                part = ids[start:start + 500]
                marks = ",".join("?" * len(part))
                for row in conn.execute(
                        "SELECT c.id, c.text, c.parent, c.locator, d.id AS doc_id, d.title, d.source, d.url, "
                        "d.sensitivity, d.basis, d.ai_use, col.name AS collection FROM chunks c "
                        "JOIN documents d ON d.id=c.doc_id JOIN collections col ON col.id=c.collection_id "
                        f"WHERE c.id IN ({marks})", part):
                    rows[(shard, int(row["id"]))] = row
        finally:
            conn.close()
    out = []
    for score, shard, cid in scored:
        row = rows.get((shard, cid))
        if row is None:
            continue
        if exclude_confidential and row["sensitivity"] != "public":
            continue
        try:
            locator = json.loads(row["locator"] or "{}")
        except json.JSONDecodeError:
            locator = {}
        out.append({"score": round(score, 5), "text": row["text"], "parent": row["parent"],
                    "chunk_ref": f"{shard}:{cid}", "doc_ref": f"{shard}:{row['doc_id']}",
                    "collection": row["collection"], "title": row["title"], "source": row["source"],
                    "url": row["url"], "sensitivity": row["sensitivity"], "basis": row["basis"], "ai_use": row["ai_use"],
                    "locator": locator})
    return out


def search_all(question: str, *, consumer: str, bound: Iterable[str] = (), **kwargs) -> list[dict]:
    """全分野の横断検索。consumer=cloud のときは、会話に紐づくコレクション（bound）以外の
    confidential な文書を返さない。consumer は呼び出し側が答えを作る LLM の種類で決める。"""
    if consumer not in ("local", "cloud"):
        raise KnowledgeError("consumer は local か cloud")
    names = [c["collection"] for c in list_collections()]
    bound_set = set(bound)
    if consumer == "local":
        return search(names, question, **kwargs)
    others = [n for n in names if n not in bound_set]
    mine = [n for n in names if n in bound_set]
    merged = search(mine, question, **kwargs) + search(others, question, exclude_confidential=True, **kwargs)
    merged.sort(key=lambda m: -m["score"])
    return merged[:int(kwargs.get("limit", 80))]


def stats() -> dict:
    out = {}
    for shard in SHARDS:
        path = shard_path(shard)
        if not path.exists():
            out[shard] = {"exists": False}
            continue
        conn = connect(shard)
        try:
            out[shard] = {"exists": True, "bytes": path.stat().st_size, "target": storage.db_target(shard),
                          **{t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                             for t in ("collections", "documents", "chunks", "vectors", "blobs")},
                          "blob_bytes_by_location": {r[0]: int(r[1]) for r in conn.execute(
                              "SELECT location, SUM(bytes) FROM blobs GROUP BY location")}}
        finally:
            conn.close()
    return out
