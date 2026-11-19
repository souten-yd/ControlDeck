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


def test_chat_rerank_gives_up_after_its_budget_and_keeps_search_order(monkeypatch):
    """会話の回答前の再ランクは上限（3 秒）で打ち切り、検索の順のまま返す。打ち切りは自己診断に出る。
    根拠: 2026-11-20 本番。GPU が埋まり reranker が CPU で動き、挨拶 1 つで思考の開始まで 23 秒（再ランク 22.6 秒）待たせた。"""
    import asyncio

    from app.diagnostics import selftest
    from app.workflows import rag

    async def slow(question, matches, top_k):
        await asyncio.sleep(5)
        return list(reversed(matches))[:top_k]

    monkeypatch.setattr(rag, "_rerank", slow)
    monkeypatch.setattr(rag, "rerank_stats", {"calls": 1, "timeouts": 0, "last_seconds": 0.0, "slowest_seconds": 0.0})
    matches = [{"text": str(i), "context": str(i)} for i in range(8)]
    picked = asyncio.run(rag._maybe_rerank("こんにちは", matches, 2, budget_s=0.05))
    assert [m["text"] for m in picked] == ["0", "1"]
    assert rag.rerank_stats["timeouts"] == 1 and rag.rerank_stats["last_seconds"] < 1
    assert selftest._chat_rerank()[0] == "warn"
    # 上限を渡さない呼び出し（Workflow の RAG ノードなど）は今まで通り待つ
    async def quick(question, matches, top_k):
        return list(reversed(matches))[:top_k]

    monkeypatch.setattr(rag, "_rerank", quick)
    assert [m["text"] for m in asyncio.run(rag._maybe_rerank("q", matches, 2))] == ["7", "6"]


def test_small_conversation_materials_are_given_whole_without_search(monkeypatch, tmp_path):
    """会話の資料が小さければ探さずに全部、文書の順で渡す（並べ替えも埋め込みも使わない）。保存時に入っていた
    回答の指示文（「以下の検索結果を根拠に…」）は除く。大きければ None（探す方へ回す）。
    根拠: 2026-11-20 利用者「毎回会話ごとに調査するのか。資料を全部入れるのもありか。速度は速めに」。
    実測で 45 会話中 41 が 4.9 万文字以内。挨拶でも天気の検索資料を探して並べ替え、20 秒以上待たせていた。"""
    import sqlite3

    from app.workflows import rag

    monkeypatch.setattr(rag, "_rag_dir", lambda: tmp_path)
    db = sqlite3.connect(tmp_path / "chat-x.db")
    db.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, source TEXT, added_at REAL, chunk_count INTEGER, meta TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT, parent TEXT, embedding BLOB, dim INTEGER, idx INTEGER);
    """)
    db.execute("INSERT INTO documents VALUES (1, 'web:天気', 0, 2, '{}'), (2, 'upload:memo.txt', 0, 1, '{}')")
    db.executemany("INSERT INTO chunks (doc_id, text, parent, idx) VALUES (?, ?, NULL, ?)", [
        (2, "メモの本文", 0),
        (1, "以下の検索結果を根拠に日本語で回答してください。主張には [R英数字] で出典を付けること。\n\n[R1] 横浜は晴れ", 0),
        (1, "[R2] 最高気温 20 度", 1),
    ])
    db.commit()
    monkeypatch.setattr(rag, "collection_exists", lambda name: True)
    text = rag.collection_materials("chat-x", 10_000)
    assert text.index("横浜は晴れ") < text.index("最高気温") < text.index("メモの本文")   # 文書の順・断片の順
    assert "以下の検索結果を根拠に" not in text and "web:天気" in text
    assert rag.collection_materials("chat-x", 20) is None                                # 大きければ探す方へ
