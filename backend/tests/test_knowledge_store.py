"""統合ナレッジストア（app.knowledge）と旧データの取り込み。

目的:
- 旧 rag.py の欠陥を直したまま戻らないこと: 2,000 チャンクでの黙った切り詰め、2 文字の日本語語が
  全文検索に当たらない（trigram）、検索のたびに全件を読み 1 件ずつ計算する。
- 旧 data/rag/*.db と ResearchDataHub の library.sqlite3 を、元を書き換えずに取り込めること。
  何度流しても同じ結果で、消したコレクションは復活しないこと。
- 分野（一般・特許・規格）を別ファイルに分けても、呼び出し側は名前だけで検索できること。
- クラウド LLM 向けの横断検索で、会話に紐づかない機密文書を返さないこと（アイデアの流出防止）。

根拠: 2026-11-19 利用者の決定「PatentBuddy・ResearchDataHub を ControlDeck に統合し、データを回収する。
DB は分けるが ControlDeck からは意識しない。守りたいのはアイデアの流出」。docs/PLAN-knowledge-platform.md。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3

import numpy as np
import pytest

from app.config import data_dir


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh_store():
    import shutil

    from app.knowledge import store

    for sub in ("knowledge", "rag"):
        shutil.rmtree(data_dir() / sub, ignore_errors=True)
    store._schema_ready.clear()
    yield
    store._schema_ready.clear()


def _fake_embed(dim=4):
    calls = []

    async def fake(texts, base_url, model, api_key):
        calls.append(len(texts))
        out = []
        for text in texts:
            vec = np.zeros(dim, dtype=np.float32)
            vec[hash(text[:6]) % dim] = 1.0
            out.append(vec)
        return out

    return fake, calls


def test_no_silent_truncation_and_batched_embedding(monkeypatch):
    from app.workflows import rag

    fake, calls = _fake_embed()
    monkeypatch.setattr(rag, "embed", fake)
    text = "\n\n".join(f"段落{i}の本文です。" for i in range(2500))
    result = run(rag.add_document("bulk", text, "big.txt", config_override={"strategy": "paragraph", "size": 20}))
    assert result["added_chunks"] == result["total_chunks"] >= 2500
    assert max(calls) <= rag.EMBED_BATCH


def test_two_character_japanese_terms_hit_fulltext(monkeypatch):
    from app.workflows import rag

    fake, _ = _fake_embed()
    monkeypatch.setattr(rag, "embed", fake)
    run(rag.add_document("ja", "定格電圧は二百ボルトとする。\n\n筐体は樹脂製。", "spec",
                         config_override={"strategy": "paragraph", "size": 30}))
    found = run(rag.search("ja", "電圧", 3, mode_override="fulltext"))
    assert found["count"] >= 1 and "電圧" in found["matches"][0]["text"]
    # 文章の質問でも 2-gram の OR で拾う
    found = run(rag.search("ja", "電圧の定格はいくつですか", 3, mode_override="fulltext"))
    assert "電圧" in found["matches"][0]["text"]


def test_vector_cache_follows_writes_and_deletes(monkeypatch):
    from app.knowledge import store

    store.create_collection("vec")
    a = store.add_document("vec", [store.NewChunk("alpha", vector=np.array([1, 0, 0], np.float32))], model="m")
    store.add_document("vec", [store.NewChunk("beta", vector=np.array([0, 1, 0], np.float32))], model="m")
    hit = store.search(["vec"], "zzz", query_vector=np.array([1, 0, 0], np.float32), model="m", mode="vector")
    assert hit[0]["text"] == "alpha"
    store.delete_document("vec", a["doc_id"])
    hit = store.search(["vec"], "zzz", query_vector=np.array([1, 0, 0], np.float32), model="m", mode="vector")
    assert [h["text"] for h in hit] == ["beta"]


def test_same_ext_id_and_hash_writes_nothing():
    from app.knowledge import store

    store.create_collection("idem")
    first = store.add_document("idem", [store.NewChunk("本文")], ext_id="x", sha256="a" * 64)
    again = store.add_document("idem", [store.NewChunk("本文")], ext_id="x", sha256="a" * 64)
    assert again["unchanged"] and again["doc_id"] == first["doc_id"]
    changed = store.add_document("idem", [store.NewChunk("改訂")], ext_id="x", sha256="b" * 64)
    assert not changed["unchanged"]
    assert [d["ext_id"] for d in store.list_documents("idem")] == ["x"]


def _legacy_db(name: str, chunks: list[tuple[str, list[float]]], config: dict | None = None):
    path = data_dir() / "rag" / f"{name}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, source TEXT, added_at REAL, chunk_count INTEGER, meta TEXT)")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT, parent TEXT, "
                 "embedding BLOB, dim INTEGER, idx INTEGER)")
    conn.execute("INSERT INTO meta VALUES ('config', ?)", (json.dumps(config or {"embed_model": "old-model"}),))
    conn.execute("INSERT INTO documents VALUES (1, 'web: example', 1700000000.0, ?, '{}')", (len(chunks),))
    for i, (text, vec) in enumerate(chunks):
        arr = np.array(vec, dtype=np.float32)
        conn.execute("INSERT INTO chunks (doc_id, text, parent, embedding, dim, idx) VALUES (1, ?, NULL, ?, ?, ?)",
                     (text, arr.tobytes(), len(arr), i))
    conn.commit()
    conn.close()
    return path


def test_legacy_collection_is_imported_on_first_use_without_touching_source(monkeypatch):
    from app.workflows import rag

    path = _legacy_db("chat-0123456789abcdef", [("りんごの話", [1, 0, 0]), ("車の話", [0, 0, 1])])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    listed = {c["collection"]: c for c in rag.list_collections()}
    assert listed["chat-0123456789abcdef"]["kind"] == "legacy"

    async def query_vec(texts, base_url, model, api_key):
        return [np.array([1, 0, 0], dtype=np.float32) for _ in texts]

    monkeypatch.setattr(rag, "embed", query_vec)
    # 設定のモデル名（old-model）と問い合わせのモデル名が違っても、次元が同じ旧ベクトルを使う
    monkeypatch.setattr(rag, "_managed_embed_defaults", lambda: {"embed_model": "managed-alias"})
    found = run(rag.search("chat-0123456789abcdef", "りんご", 1, mode_override="vector"))
    assert found["matches"][0]["text"] == "りんごの話"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    rag.delete_collection("chat-0123456789abcdef")
    assert not rag.collection_exists("chat-0123456789abcdef")  # 旧ファイルから復活しない
    assert path.exists()


def test_migrate_cli_plan_writes_nothing_then_apply_is_idempotent():
    from app.knowledge import migrate, store

    _legacy_db("notes", [("メモ", [1, 0])])
    plan = migrate.plan_all(None)
    assert plan["legacy_totals"] == {"collections": 1, "documents": 1, "chunks": 1}
    assert not store.shard_path("general").exists()
    migrate.apply_all(None)
    again = migrate.apply_all(None)
    assert again["legacy_rag"][0]["skipped"] == "imported"
    assert store.stats()["general"]["documents"] == 1


def _rdh_db(tmp_path, *, with_case=False):
    path = tmp_path / "library.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE collections(id INTEGER PRIMARY KEY, name TEXT, kind TEXT, description TEXT, settings TEXT, created REAL, updated REAL);
    CREATE TABLE documents(id INTEGER PRIMARY KEY, collection_id INTEGER, ext_id TEXT, title TEXT, url TEXT, source TEXT,
      doc_date TEXT, meta TEXT, body TEXT, hash TEXT, created REAL, updated REAL);
    CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, collection_id INTEGER, ord INTEGER, section TEXT, text TEXT);
    CREATE TABLE vectors(chunk_id INTEGER, collection_id INTEGER, model TEXT, dim INTEGER, vec BLOB);
    CREATE TABLE source_pdfs(sha256 TEXT, content BLOB, created REAL);
    CREATE TABLE document_pdfs(doc_id INTEGER, source_sha256 TEXT, extraction_sha256 TEXT, extraction TEXT, created REAL);
    CREATE TABLE pdf_image_blobs(sha256 TEXT, content BLOB);
    CREATE TABLE document_pdf_images(doc_id INTEGER, image_id INTEGER, sha256 TEXT);
    CREATE TABLE document_analyses(id TEXT, doc_id INTEGER, source_revision TEXT, kind TEXT, content TEXT, created REAL);
    CREATE TABLE patents(id INTEGER PRIMARY KEY);
    CREATE TABLE patent_claims(id INTEGER PRIMARY KEY);
    """)
    pdf = b"%PDF-1.4 test"
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    pdf_sha, png_sha = hashlib.sha256(pdf).hexdigest(), hashlib.sha256(png).hexdigest()
    conn.execute("INSERT INTO collections VALUES (1, 'pdf-imports', 'generic', '', '{}', 0, 0)")
    conn.execute("INSERT INTO documents VALUES (1, 1, 'pdf:abc', '公報.pdf', '', ?, '', '{}', '請求項1 電圧変換装置', 'h1', 0, 0)",
                 (f"sha256:{pdf_sha}",))
    conn.execute("INSERT INTO chunks VALUES (1, 1, 1, 0, 'claims', '請求項1 電圧変換装置')")
    conn.execute("INSERT INTO source_pdfs VALUES (?, ?, 0)", (pdf_sha, pdf))
    conn.execute("INSERT INTO document_pdfs VALUES (1, ?, 'e1', '{\"pages\":[]}', 0)", (pdf_sha,))
    conn.execute("INSERT INTO pdf_image_blobs VALUES (?, ?)", (png_sha, png))
    conn.execute("INSERT INTO document_pdf_images VALUES (1, 0, ?)", (png_sha,))
    conn.execute("INSERT INTO document_analyses VALUES ('a1', 1, 'rev', 'summary', '要約', 0)")
    if with_case:
        conn.execute("INSERT INTO patents VALUES (1)")
    conn.commit()
    conn.close()
    return path, pdf_sha


def test_researchdatahub_import_goes_to_patent_shard_with_assets(tmp_path):
    from app.knowledge import migrate, store

    path, pdf_sha = _rdh_db(tmp_path)
    report = migrate.import_rdh(path)
    assert report["documents"] == 1 and report["blobs"] == 3 and report["annotations"] == 1
    ref = store.require_collection("rdh-pdf-imports")
    assert ref.shard == "patents" and ref.sensitivity == "confidential"
    assert store.blob_path(pdf_sha).read_bytes() == b"%PDF-1.4 test"
    conn = store.connect("patents")
    try:
        assert tuple(conn.execute("SELECT basis, review_status FROM annotations").fetchone()) == ("model_inference", "unreviewed")
    finally:
        conn.close()
    assert migrate.import_rdh(path)["skipped"] == "imported"
    hits = store.search(["rdh-pdf-imports"], "電圧", mode="fulltext")
    assert hits and hits[0]["locator"]["section"] == "claims"


def test_researchdatahub_cases_stop_the_import(tmp_path):
    from app.knowledge import migrate, store

    path, _ = _rdh_db(tmp_path, with_case=True)
    with pytest.raises(store.KnowledgeError):
        migrate.import_rdh(path)


def test_shards_are_separate_files_but_search_is_by_name():
    from app.knowledge import store

    store.create_collection("gen", shard="general")
    store.create_collection("pat", shard="patents")
    store.add_document("gen", [store.NewChunk("インバータの効率")])
    store.add_document("pat", [store.NewChunk("インバータ装置の請求項")])
    assert store.shard_path("general").exists() and store.shard_path("patents").exists()
    hits = store.search(["gen", "pat"], "インバータ", mode="fulltext")
    assert {h["collection"] for h in hits} == {"gen", "pat"}
    assert {h["chunk_ref"].split(":")[0] for h in hits} == {"general", "patents"}


def test_cloud_cross_search_hides_confidential_except_bound_conversation():
    from app.knowledge import store

    store.create_collection("chat-mine", kind="conversation")
    store.create_collection("idea-notes")
    store.create_collection("public-gazette", shard="patents", sensitivity="public")
    store.add_document("chat-mine", [store.NewChunk("インバータの会話")])
    store.add_document("idea-notes", [store.NewChunk("インバータの未公開アイデア")])
    store.add_document("public-gazette", [store.NewChunk("インバータの公開公報")])
    local = store.search_all("インバータ", consumer="local", bound=["chat-mine"], mode="fulltext")
    assert {h["collection"] for h in local} == {"chat-mine", "idea-notes", "public-gazette"}
    # 会話の資料はその会話の中（bound）でだけ使う（2026-11-21、別の会話の AI の回答を混ぜない）
    assert "chat-mine" not in {h["collection"] for h in store.search_all("インバータ", consumer="local", mode="fulltext")}
    cloud = store.search_all("インバータ", consumer="cloud", bound=["chat-mine"], mode="fulltext")
    assert {h["collection"] for h in cloud} == {"chat-mine", "public-gazette"}
    with pytest.raises(store.KnowledgeError):
        store.search_all("x", consumer="somewhere")


def test_blob_path_rejects_traversal():
    from app.knowledge import store

    with pytest.raises(store.KnowledgeError):
        store.blob_path("../../etc/passwd")


def test_concurrent_first_use_imports_legacy_once():
    from concurrent.futures import ThreadPoolExecutor

    from app.knowledge import store
    from app.workflows import rag

    _legacy_db("chat-race", [("並行の話", [1, 0])])
    with ThreadPoolExecutor(8) as pool:
        assert all(pool.map(lambda _: rag.collection_exists("chat-race"), range(16)))
    assert store.stats()["general"]["documents"] == 1


def test_failed_copy_leaves_no_empty_target(monkeypatch):
    from app.knowledge import store

    store.create_collection("src")
    store.add_document("src", [store.NewChunk("本文")])
    monkeypatch.setattr(store.textproc, "index_text", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        store.copy_collection("src", "dst")
    assert store.find_collection("dst") is None
