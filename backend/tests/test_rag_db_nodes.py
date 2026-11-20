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

    async def slow(question, matches, top_k, **_):
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


def test_cpu_reranker_is_preferred_and_its_scores_do_not_gate(monkeypatch):
    """CPU の reranker（ruri）が導入済みなら GPU の reranker より優先し、上位の件数で選ぶ（点数の門で切らない）。
    根拠: 2026-11-20 評価（docs/reranker-evaluation.md）。ruri は日本語の並べ替えが最良（JQaRA 0.877、4B は 0.743）だが、
    「さっき調べた番組表」のように会話の流れを指す質問に 0.02 しか付けず、門にすると要る資料を落とす。利用者が採用を決めた。"""
    from app.models_mgmt import llama
    from app.workflows import cpu_reranker, rag

    async def scores(query, documents, **_):
        assert query == "番組表を調べて\nさっきの番組表を教えて"
        return [0.01, 0.02, 0.005, 0.001]

    monkeypatch.setattr(cpu_reranker, "ready", lambda: True)
    monkeypatch.setattr(cpu_reranker, "score", scores)
    monkeypatch.setattr(llama, "find_role_instance", lambda role: pytest.fail("GPU の reranker を使った"))
    matches = [{"text": f"資料{i}", "context": f"資料{i}", "score": 1.0} for i in range(4)]
    picked = run(rag._rerank("番組表を調べて\nさっきの番組表を教えて", matches, 2))
    assert [m["text"] for m in picked] == ["資料1", "資料0"]
    assert all(m["rerank_engine"] == cpu_reranker.ENGINE_ID for m in picked)

    # 会話の資料（3 秒まで）は候補を top_k×2 件・各 256 トークンに絞る（約 800 文字 16 件で 1.65 秒、32 件・384 では 5.7 秒）
    asked = {}

    async def fast_scores(query, documents, max_tokens, **_):
        asked.update(count=len(documents), max_tokens=max_tokens)
        return [float(i) for i in range(len(documents))]

    monkeypatch.setattr(cpu_reranker, "score", fast_scores)
    many = [{"text": f"資料{i}", "context": f"資料{i}"} for i in range(16)]
    picked = run(rag._maybe_rerank("q", many, 2, budget_s=3.0))
    assert asked == {"count": 4, "max_tokens": cpu_reranker.FAST_MAX_TOKENS}
    assert [m["text"] for m in picked] == ["資料3", "資料2"]
    run(rag._maybe_rerank("q", many, 2))
    assert asked == {"count": 16, "max_tokens": cpu_reranker.MAX_TOKENS}

    # rag.search の門: ruri の点では切らない。Qwen（llama）の点なら切る
    async def fake_embed(texts, base_url, model, api_key):
        return [np.array([1.0, 0.5, 0.1], dtype=np.float32) for _ in texts]

    monkeypatch.setattr(rag, "embed", fake_embed)
    rag.delete_collection("gatecol")
    run(rag.build("gatecol", "\n\n".join(f"資料{i}の本文" for i in range(6)), "t", "http://x/v1", "m", "", reset=True))
    seen = {}
    for engine, expected in ((cpu_reranker.ENGINE_ID, 1), ("llama", 0)):
        async def fake_rerank(question, found, top_k, engine=engine):
            seen["question"] = question
            return [{**m, "rerank_score": 0.02, "rerank_engine": engine} for m in found[:top_k]]

        monkeypatch.setattr(rag, "_rerank", fake_rerank)
        result = run(rag.search("gatecol", "さっきの", top_k=1, min_rerank_score=0.3, rerank_query="話題\nさっきの"))
        assert result["count"] == expected and seen["question"] == "話題\nさっきの"
    rag.delete_collection("gatecol")


def test_cpu_reranker_failure_keeps_search_order(monkeypatch):
    """CPU の reranker が失敗しても会話を止めず、検索の順で返す。失敗は自己診断に出る。"""
    from app.diagnostics import selftest
    from app.workflows import cpu_reranker, rag

    async def broken(query, documents, **_):
        cpu_reranker.last_error = "worker が起動しませんでした"
        raise cpu_reranker.CpuRerankerError(cpu_reranker.last_error)

    monkeypatch.setattr(cpu_reranker, "ready", lambda: True)
    monkeypatch.setattr(cpu_reranker, "score", broken)
    monkeypatch.setattr(cpu_reranker, "last_error", "")
    matches = [{"text": str(i), "context": str(i)} for i in range(5)]
    assert [m["text"] for m in run(rag._rerank("q", matches, 2))] == ["0", "1"]
    level, message = selftest._chat_rerank()
    assert level == "warn" and "CPU の reranker" in message


def test_rerank_query_carries_the_previous_topic():
    """会話の資料の並べ替えには、直前の発言（後ろ 300 字）を質問の前に添える。最初の発言はそのまま。
    根拠: 2026-11-20 評価。「さっき調べた番組表の内容を教えて」だけでは ruri が番組表の資料に 0.02、添えると 0.47。"""
    from app.workflows.chat_persist import CHAT_RERANK_TOPIC_CHARS, rerank_query_with_topic

    assert rerank_query_with_topic([]) == ""
    assert rerank_query_with_topic(["こんにちは"]) == "こんにちは"
    long = "あ" * 1000 + "番組表を調べて"
    query = rerank_query_with_topic([long, "さっきの内容を教えて"])
    assert query.endswith("番組表を調べて\nさっきの内容を教えて")
    assert len(query.split("\n")[0]) == CHAT_RERANK_TOPIC_CHARS


def test_cpu_reranker_feature_status_and_actions(monkeypatch, tmp_path):
    """機能追加の「検索（RAG）」に CPU の reranker を出す。導入は非同期ジョブだけ、更新は無い（版固定）。
    アンインストールは実行環境だけを消し、重みと int8 の ONNX（保管庫）は残す。"""
    from app.features import registry
    from app.workflows import cpu_reranker

    monkeypatch.setattr(cpu_reranker, "data_dir", lambda: tmp_path)
    state = registry.status("ruri-reranker")
    assert state["category"] == "retrieval" and not state["installed"] and state["health"] == "not-installed"
    with pytest.raises(registry.FeatureError):
        registry.install("ruri-reranker")
    with pytest.raises(registry.FeatureError):
        registry.update("ruri-reranker")
    onnx = cpu_reranker.onnx_dir()
    onnx.mkdir(parents=True)
    (onnx / "model_int8.onnx").write_bytes(b"x")
    (onnx / "tokenizer.json").write_text("{}")
    python = cpu_reranker.python_path()
    python.parent.mkdir(parents=True)
    python.write_text("")
    (cpu_reranker.runtime_root() / "venv-ready").write_text(",".join(cpu_reranker.RUNTIME_PACKAGES))
    assert registry.status("ruri-reranker")["installed"]
    registry.uninstall("ruri-reranker")
    assert not python.exists() and (onnx / "model_int8.onnx").is_file()
    assert not registry.status("ruri-reranker")["installed"]
