import asyncio

import numpy as np
import pytest

from tests.conftest import CSRF_HEADERS, _sandbox


def run(coro):
    return asyncio.run(coro)


def test_chunkers():
    from app.workflows import chunkers

    assert chunkers.chunk("", {"strategy": "fixed", "size": 800}) == []
    assert [c.text for c in chunkers.chunk("short", {"strategy": "fixed", "size": 800})] == ["short"]
    chunks = chunkers.chunk("x" * 2000, {"strategy": "fixed", "size": 800, "overlap": 100})
    assert len(chunks) >= 2
    assert all(len(c.text) <= 800 for c in chunks)
    # parent_child は子が親テキストを保持する
    pc = chunkers.chunk("段落一。文が続く。\n\n段落二。別の内容。", {"strategy": "parent_child", "size": 20, "parent_mode": "paragraph", "parent_size": 100})
    assert pc and all(c.parent for c in pc)


def test_rag_build_and_query(monkeypatch):
    """埋め込みをモックして build → query のコサイン検索を確認する。"""
    from app.workflows import rag

    # 単語ごとに決め打ちのベクトルを返すフェイク埋め込み
    vocab = {
        "りんご": [1.0, 0.0, 0.0],
        "ばなな": [0.0, 1.0, 0.0],
        "car": [0.0, 0.0, 1.0],
    }

    async def fake_embed(texts, base_url, model, api_key):
        out = []
        for t in texts:
            v = np.zeros(3, dtype=np.float32)
            for word, vec in vocab.items():
                if word in t:
                    v += np.array(vec, dtype=np.float32)
            if not v.any():
                v = np.array([0.1, 0.1, 0.1], dtype=np.float32)
            out.append(v)
        return out

    monkeypatch.setattr(rag, "embed", fake_embed)

    rag.delete_collection("testcol")
    run(rag.build("testcol", "りんごは赤い果物\n\nばななは黄色い果物\n\ncar is a vehicle", "t", "http://x/v1", "m", "", reset=True))
    # ベクトル検索で最上位が「りんご」チャンクになること
    result = run(rag.query("testcol", "りんご", top_k=1, base_url="http://x/v1", model="m", api_key=""))
    assert result["count"] == 1
    assert "りんご" in result["matches"][0]["text"]
    assert result["matches"][0]["score"] > 0
    rag.delete_collection("testcol")


def test_rag_invalid_collection():
    from app.workflows.rag import _db

    with pytest.raises(ValueError):
        _db("../evil")


def test_db_query_sqlite(monkeypatch):
    from app.workflows.nodes import node_db_query

    db_path = str(_sandbox / "wf-test.db")
    # テーブル作成 + 挿入
    run(node_db_query({"engine": "sqlite", "path": db_path, "query": "CREATE TABLE IF NOT EXISTS items (id INTEGER, name TEXT)"}, {}))
    run(node_db_query({"engine": "sqlite", "path": db_path, "query": "INSERT INTO items VALUES (:id, :name)", "params": '{"id": 1, "name": "apple"}'}, {}))
    out = run(node_db_query({"engine": "sqlite", "path": db_path, "query": "SELECT name FROM items WHERE id = :id", "params": '{"id": 1}'}, {}))
    assert out["row_count"] == 1
    assert out["rows"][0]["name"] == "apple"


def test_db_query_rejects_non_dml():
    from app.workflows.nodes import NodeError, node_db_query

    with pytest.raises(NodeError):
        run(node_db_query({"engine": "sqlite", "path": str(_sandbox / "x.db"), "query": "VACUUM; rm -rf"}, {}))


def test_db_query_sqlite_path_must_be_in_root():
    from app.workflows.nodes import NodeError, node_db_query

    with pytest.raises(NodeError):
        run(node_db_query({"engine": "sqlite", "path": "/etc/passwd.db", "query": "SELECT 1"}, {}))


def test_workflow_api_accepts_rag_db_nodes(admin_client):
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "rq", "type": "rag.query", "config": {"collection": "docs", "question": "x"}},
            {"id": "db", "type": "db.query", "config": {"engine": "sqlite", "query": "SELECT 1"}},
        ],
        "edges": [{"source": "t", "target": "rq"}, {"source": "rq", "target": "db"}],
    }
    r = admin_client.post("/api/v1/workflows", json={"name": "rag-db", "definition": definition}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    admin_client.delete(f"/api/v1/workflows/{r.json()['id']}", headers=CSRF_HEADERS)
