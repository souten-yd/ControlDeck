"""Explicit lexical operators must survive retrieval and document ranking."""
import sqlite3

import pytest

from app.knowledge import store, textproc, documents


def test_fts_explicit_and_natural():
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("CREATE TABLE chunks(id INTEGER PRIMARY KEY,collection_id INTEGER);"
                           "CREATE VIRTUAL TABLE chunks_fts USING fts5(tok,tokenize='unicode61 remove_diacritics 0');")
        texts = ["CPU threads", "CPU CUDA", "ROCm Vulkan", "ROCm only", "定格電圧は200V", "CPU private collection"]
        for i, text in enumerate(texts, 1):
            conn.execute("INSERT INTO chunks VALUES(?,?)", (i, 2 if i == 6 else 1))
            conn.execute("INSERT INTO chunks_fts(rowid,tok) VALUES(?,?)", (i,textproc.index_text(text)))
        for q, expected in [("CPU -CUDA",{1}), ("ROCm -Vulkan",{4}),
                            ('"CPU threads"',{1}), ("CUDA OR Vulkan",{2,3}),
                            ("-CUDA",set()), ('"CPU threads" -CPU',set())]:
            assert set(store._fts_ranks(conn,q,[1],20)) == expected
        assert 5 in store._fts_ranks(conn,"定格電圧はいくつですか",[1],20)


@pytest.mark.parametrize("mode",["fulltext","hybrid","vector"])
def test_explicit_never_uses_vector_or_graph(monkeypatch, tmp_path, mode):
    # Minimal test DB; exercise actual ranking functions, not the configured app DB.
    from types import SimpleNamespace
    import numpy as np
    path = tmp_path / "test.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE chunks(id INTEGER PRIMARY KEY,collection_id INTEGER);"
                           "CREATE VIRTUAL TABLE chunks_fts USING fts5(tok);")
        conn.execute("INSERT INTO chunks VALUES(1,1),(2,1)")
        conn.executemany("INSERT INTO chunks_fts(rowid,tok) VALUES(?,?)",[(1,"cpu"),(2,"cpu cuda")])
    monkeypatch.setattr(store,"find_collection",lambda n: SimpleNamespace(shard="general",id=1))
    monkeypatch.setattr(store,"connect",lambda s: sqlite3.connect(path))
    def forbidden(*a,**kw):
        pytest.fail("explicit query invoked expansion")
    monkeypatch.setattr(store._vectors,"get",forbidden)
    monkeypatch.setattr(documents,"find_by_identifier",forbidden)
    monkeypatch.setattr(documents,"related_documents",forbidden)
    def hydrate(scored, excluded):
        return [dict(doc_ref=f"general:{cid}",chunk_ref=f"general:{cid}",title="CPU",source="test",
                     collection="test",sensitivity="public",ai_use="allowed",text="CPU",locator={},score=score)
                for score,_,cid in scored]
    monkeypatch.setattr(store,"_hydrate",hydrate)
    hits=documents.search_documents("CPU -CUDA",names=["test"],query_vector=np.ones(2),
                                    mode=mode,consumer="local",expand=True)
    assert [h["ref"] for h in hits] == ["general:1"]
