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
    """CPU の reranker が導入済みなら GPU の reranker より優先し、上位の件数で選ぶ（点数の門で切らない）。
    会話（3 秒まで）は速い xsmall を、Knowledge・Workflow は精度の ruri を優先する。ruri を会話で使うときは候補と長さを絞る。
    根拠: 2026-11-20 評価（docs/reranker-evaluation.md）と利用者「軽量・高速なら精度は多少落ちてもよい」。
    ruri は日本語が最良（JQaRA 0.877）だが約 800 文字 32 件で 5.7 秒、xsmall は 0.748 で 0.6 秒。ruri は「さっき調べた番組表」
    のように会話の流れを指す質問に 0.02 しか付けず、門にすると要る資料を落とす。"""
    from app.models_mgmt import llama
    from app.workflows import cpu_reranker, rag

    installed = {"xsmall-reranker", "ruri-reranker"}
    monkeypatch.setattr(cpu_reranker, "ready", lambda model_id=None: bool(installed) if model_id is None else model_id in installed)
    monkeypatch.setattr(llama, "find_role_instance", lambda role: pytest.fail("GPU の reranker を使った"))
    assert cpu_reranker.pick(fast=True) == "xsmall-reranker" and cpu_reranker.pick(fast=False) == "ruri-reranker"
    installed.discard("xsmall-reranker")
    assert cpu_reranker.pick(fast=True) == "ruri-reranker"   # 片方しか無ければそれを使う

    asked = {}

    async def scores(query, documents, model_id, max_tokens, **_):
        asked.update(model=model_id, count=len(documents), max_tokens=max_tokens, query=query)
        return [float(i) for i in range(len(documents))]

    monkeypatch.setattr(cpu_reranker, "score", scores)
    many = [{"text": f"資料{i}", "context": f"資料{i}"} for i in range(16)]
    # 会話: ruri しか無ければ候補 top_k×2。資料は切らずに 1024 トークンまで読む（利用者「短く切らないで」）
    picked = run(rag._maybe_rerank("番組表を調べて\nさっきの番組表を教えて", many, 2, budget_s=3.0))
    assert asked == {"model": "ruri-reranker", "count": 4, "max_tokens": 1024, "query": "番組表を調べて\nさっきの番組表を教えて"}
    assert [m["text"] for m in picked] == ["資料3", "資料2"]
    assert all(m["rerank_engine"] == "ruri-reranker" for m in picked)
    # Knowledge 等（上限なし）: 候補 top_k×4
    run(rag._maybe_rerank("q", many, 2))
    assert (asked["count"], asked["max_tokens"]) == (8, 1024)
    # xsmall があれば会話はそちら。速いので候補を絞らない
    installed.add("xsmall-reranker")
    run(rag._maybe_rerank("q", many, 2, budget_s=3.0))
    assert (asked["model"], asked["count"], asked["max_tokens"]) == ("xsmall-reranker", 8, 1024)

    # rag.search の門: CPU の点では切らない。Qwen（llama）の点なら切る
    async def fake_embed(texts, base_url, model, api_key):
        return [np.array([1.0, 0.5, 0.1], dtype=np.float32) for _ in texts]

    monkeypatch.setattr(rag, "embed", fake_embed)
    rag.delete_collection("gatecol")
    run(rag.build("gatecol", "\n\n".join(f"資料{i}の本文" for i in range(6)), "t", "http://x/v1", "m", "", reset=True))
    seen = {}
    for engine, expected in (("ruri-reranker", 1), ("llama", 0)):
        async def fake_rerank(question, found, top_k, engine=engine, **_):
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

    monkeypatch.setattr(cpu_reranker, "ready", lambda model_id=None: model_id in (None, "xsmall-reranker"))
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


def test_cpu_rerankers_are_installed_and_chosen_in_models(admin_client, monkeypatch, tmp_path):
    """Models の「Embed / Reranker」で CPU の reranker（速い xsmall・高精度 ruri）を導入・選択する。
    根拠: 2026-11-20 利用者「Models タブで導入できるように。どのモデルを使うか選べるように」。
    - 状態を見るだけではフォルダを作らない（本番で、未導入なのに空のフォルダが残っていた）
    - 選択は会話と Knowledge・Workflow で別。選んだ CPU のモデルが未導入なら自動に戻る。gpu・none も選べる
    - アンインストールは使わなくするだけで、取得・変換したファイル（保管庫）は残す。実行環境は共有"""
    from app.workflows import cpu_reranker
    from tests.conftest import CSRF_HEADERS

    monkeypatch.setattr(cpu_reranker, "data_dir", lambda: tmp_path)
    data = admin_client.get("/api/v1/models/retrieval").json()
    assert [m["id"] for m in data["cpu_rerankers"]] == ["xsmall-reranker", "ruri-reranker", "ruri-reranker-gpu"]
    assert not any(m["installed"] for m in data["cpu_rerankers"]) and data["selection"] == {"chat": "auto", "knowledge": "auto"}
    assert not (tmp_path / "archives").exists() and not (tmp_path / "runtimes").exists()   # 見るだけでは作らない
    assert data["in_use"] == {"chat": "gpu", "knowledge": "gpu"}                            # CPU が無ければ GPU
    # 導入済みにする（xsmall は取得したファイル、ruri は変換した int8、実行環境は共有）
    for name, (size, _) in cpu_reranker.MODELS["xsmall-reranker"]["files"].items():
        target = cpu_reranker.source_dir("xsmall-reranker") / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            stream.truncate(size)
    ruri = cpu_reranker.model_path("ruri-reranker")
    ruri.parent.mkdir(parents=True)
    ruri.write_bytes(b"x")
    cpu_reranker.tokenizer_path("ruri-reranker").write_text("{}")
    python = cpu_reranker.python_path()
    python.parent.mkdir(parents=True)
    python.write_text("")
    (cpu_reranker.runtime_root() / "venv-ready").write_text(",".join(cpu_reranker.RUNTIME_PACKAGES))
    data = admin_client.get("/api/v1/models/retrieval").json()
    assert data["in_use"] == {"chat": "xsmall-reranker", "knowledge": "ruri-reranker"}      # 自動
    r = admin_client.put("/api/v1/models/retrieval/selection", json={"chat": "ruri", "knowledge": "none"},
                         headers=CSRF_HEADERS)
    # ruri は GPU 版、無ければ CPU 版
    assert r.status_code == 200 and r.json()["in_use"] == {"chat": "ruri-reranker", "knowledge": "none"}
    assert admin_client.put("/api/v1/models/retrieval/selection", json={"chat": "bogus"},
                            headers=CSRF_HEADERS).status_code == 422
    r = admin_client.post("/api/v1/models/retrieval/cpu/ruri-reranker/uninstall", headers=CSRF_HEADERS)
    assert r.status_code == 200 and not r.json()["installed"] and ruri.is_file()             # 保管庫は残る
    data = admin_client.get("/api/v1/models/retrieval").json()
    assert data["in_use"]["chat"] == "xsmall-reranker" and python.exists()                  # 選んだものが無ければ自動
    admin_client.post("/api/v1/models/retrieval/cpu/xsmall-reranker/uninstall", headers=CSRF_HEADERS)
    assert not python.exists()
    assert admin_client.post("/api/v1/models/retrieval/cpu/nope/install-jobs", headers=CSRF_HEADERS).status_code == 404


def test_large_conversation_materials_are_searched_only_when_the_message_points_at_them():
    """大きい会話の資料は、発言が資料・前に調べたもの・詳しい内容を指すときだけ探す。挨拶・お礼・雑談では探さない。
    根拠: 2026-11-20 利用者「毎回チェックする必要はない。前の会話の文脈で評価するはず。Claude と同じ方法で」。
    Claude も文脈に収まらない資料は、要ると判断したときだけ検索の道具で調べる。"""
    from app.workflows.chat_persist import needs_materials

    for text in ("さっき調べた番組表の内容を教えて", "添付した PDF の 3 ページ目は？", "出典はどこ？",
                 "その記事をもっと詳しく", "Can you check the document again?", "要約して"):
        assert needs_materials(text), text
    for text in ("こんにちは", "ありがとう！", "なるほど、了解です", "今日はいい天気だね", "それでいいよ", "発表の練習をしたい"):
        assert not needs_materials(text), text


def test_rerank_follows_the_selection(monkeypatch):
    """選んだものを並べ替えに使う: none は並べ替えず検索の順、gpu は CPU が入っていても GPU（llama.cpp）を使う。"""
    from app.models_mgmt import llama
    from app.workflows import cpu_reranker, rag

    async def never(*args, **kwargs):
        pytest.fail("CPU の reranker を使った")

    monkeypatch.setattr(cpu_reranker, "ready", lambda model_id=None: True)
    monkeypatch.setattr(cpu_reranker, "score", never)
    matches = [{"text": str(i), "context": str(i)} for i in range(6)]
    monkeypatch.setattr(cpu_reranker, "selection", lambda: {"chat": "none", "knowledge": "gpu"})
    assert [m["text"] for m in run(rag._rerank("q", matches, 2, fast=True))] == ["0", "1"]
    asked = []
    monkeypatch.setattr(llama, "find_role_instance", lambda role: asked.append(role) or None)
    assert [m["text"] for m in run(rag._rerank("q", matches, 2))] == ["0", "1"] and asked == ["reranker"]



def test_gpu_ruri_is_preferred_and_falls_back_to_cpu(monkeypatch, tmp_path):
    """GPU の ruri（fp16、MIGraphX）が入っていれば、自動では会話にも Knowledge にも使う（精度も速さも最良）。
    使えないとき（VRAM が埋まっている等）は、入っている CPU のものへ回す。
    根拠: 2026-11-20 実測。GPU の ruri は日本語 0.877・標準の長さ 32 件 0.37 秒・VRAM 1.3 GB。CPU の ruri は 8〜12 秒。
    CPU 版と GPU 版は同じ保管庫を使うが、外す印・実行環境はそれぞれ別（片方を外してももう一方は残る）。"""
    from app.workflows import cpu_reranker, rag

    installed = {"ruri-reranker-gpu", "xsmall-reranker", "ruri-reranker"}
    monkeypatch.setattr(cpu_reranker, "ready", lambda model_id=None: bool(installed) if model_id is None else model_id in installed)
    monkeypatch.setattr(cpu_reranker, "selection", lambda: {"chat": "auto", "knowledge": "auto"})
    assert cpu_reranker.pick(True) == "ruri-reranker-gpu" and cpu_reranker.pick(False) == "ruri-reranker-gpu"
    assert cpu_reranker.pick(True, cpu_only=True) == "xsmall-reranker"
    assert cpu_reranker.pick(False, cpu_only=True) == "ruri-reranker"
    used = []

    async def score(query, documents, model_id, max_tokens, **_):
        used.append(model_id)
        if model_id == "ruri-reranker-gpu":
            raise cpu_reranker.CpuRerankerError("hipErrorOutOfMemory")
        return [float(i) for i in range(len(documents))]

    monkeypatch.setattr(cpu_reranker, "score", score)
    matches = [{"text": str(i), "context": str(i)} for i in range(8)]
    picked = run(rag._rerank("q", matches, 2, fast=True))
    assert used == ["ruri-reranker-gpu", "xsmall-reranker"] and picked[0]["rerank_engine"] == "xsmall-reranker"

    # 外す印と実行環境はモデル・装置ごと
    monkeypatch.setattr(cpu_reranker, "data_dir", lambda: tmp_path)
    assert cpu_reranker.runtime_root("gpu") != cpu_reranker.runtime_root("cpu")
    assert cpu_reranker._disabled_marker("ruri-reranker") != cpu_reranker._disabled_marker("ruri-reranker-gpu")
    assert cpu_reranker.model_path("ruri-reranker-gpu").name == "model_fp16.onnx"
    assert cpu_reranker._packages("gpu")[0].startswith("onnxruntime-migraphx")


def test_install_endpoint_actually_runs_the_job(admin_client, monkeypatch, tmp_path):
    """導入の API はジョブを実際に走らせる。根拠: 2026-11-20 本番 v0.87。同期関数で受けていたためイベントループが無く 500
    （画面に一瞬「エラー」）、ジョブの記録だけが残り「処理中…」が消えなかった。進み具合は LLM の画面ではなく
    Embed / Reranker のカードに出す（ジョブの種類を model. で始めない）。"""
    import time

    from app.jobs import service as jobs
    from app.workflows import cpu_reranker
    from tests.conftest import CSRF_HEADERS

    monkeypatch.setattr(cpu_reranker, "data_dir", lambda: tmp_path)
    ran = []

    async def install(job, model_id):
        ran.append(model_id)
        return {"ok": True}

    monkeypatch.setattr(cpu_reranker, "install", install)
    monkeypatch.setattr(cpu_reranker, "_install_jobs", {})
    r = admin_client.post("/api/v1/models/retrieval/cpu/xsmall-reranker/install-jobs", headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    job = jobs.get(r.json()["job_id"])
    for _ in range(100):
        if job.status not in ("queued", "running"):
            break
        time.sleep(0.02)
    assert job.status == "succeeded" and ran == ["xsmall-reranker"] and job.kind == "retrieval.install"


def test_gpu_preset_uninstall_removes_registration_and_file(admin_client, monkeypatch):
    """GPU の埋め込み・reranker（llama.cpp のプリセット）のアンインストール: 登録を消し、GGUF も消す。"""
    from app.models_mgmt import llama
    from tests.conftest import CSRF_HEADERS

    called = []

    def delete_instance(alias, *, delete_file=False):
        called.append((alias, delete_file))
        return {"gguf_deleted": True, "reason": ""}

    monkeypatch.setattr(llama, "delete_instance", delete_instance)
    r = admin_client.post("/api/v1/models/llama/role-presets/qwen3-reranker-4b/uninstall", headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["gguf_deleted"] and called == [("rerank-qwen3-4b", True)]
    assert admin_client.post("/api/v1/models/llama/role-presets/nope/uninstall", headers=CSRF_HEADERS).status_code == 404


def test_gpu_helpers_are_released_before_an_llm_loads(monkeypatch):
    """LLM を GPU に載せる前に、GPU の補助（GPU の ruri・GPU に載った埋め込み / reranker）を止める。CPU だけのものは止めない。
    根拠: 2026-11-20 利用者と合意（順番に頼らない方法）。補助は Knowledge の検索・添付の保存・Workflow などで LLM より
    先に載ることがあり、載ったまま大きな LLM（Maya 31.5 GB・Radiance 31 GB / 34 GB）を起動すると OOM になり得る。"""
    from app.models_mgmt import large_model_lifecycle as life
    from app.models_mgmt import llama
    from app.workflows import cpu_reranker

    stopped = []

    async def stop_device(device):
        assert device == "gpu"
        return ["ruri-reranker-gpu"]

    monkeypatch.setattr(cpu_reranker, "stop_device", stop_device)
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "embed-bge-m3", "role": "embedding", "loaded": True, "n_gpu_layers": 999},
        {"alias": "embed-cpu", "role": "embedding", "loaded": True, "n_gpu_layers": 0},
        {"alias": "rerank-qwen3-4b", "role": "reranker", "loaded": False},
        {"alias": "chat", "role": "llm", "loaded": True},
    ])
    monkeypatch.setattr(llama, "stop_instance", lambda alias, **kw: (stopped.append(alias), (True, ""))[1])
    released = run(life.release_gpu_helpers())
    assert released == ["ruri-reranker-gpu", "embed-bge-m3"] and stopped == ["embed-bge-m3"]


def test_gpu_reranker_starts_before_embedding_and_checks_vram(monkeypatch, tmp_path):
    """資料の検索では GPU の ruri を埋め込みより先に起動する（ruri は全部を GPU に載せないと動かないが、bge-m3 は空きに
    合わせて残りを CPU で動かせる）。GPU の ruri は起動前に VRAM の空きを確かめ、足りなければ試さない（CPU の控えへ）。
    使われなくなったら GPU は 3 分、CPU は 10 分で止める。常駐なら止めない。xsmall は CPU に合わせた公式の int8 を使う。"""
    from app.workflows import cpu_reranker, rag

    order = []

    async def warm(model_id):
        order.append(("warm", model_id))

    async def embed(texts, *args, **kwargs):
        order.append(("embed", texts[0]))
        return [np.array([1.0, 0.0, 0.0], dtype=np.float32)]

    monkeypatch.setattr(cpu_reranker, "choose", lambda fast: "ruri-reranker-gpu")
    monkeypatch.setattr(cpu_reranker, "running", lambda model_id: False)
    monkeypatch.setattr(cpu_reranker, "warm", warm)
    monkeypatch.setattr(rag, "embed", embed)
    run(rag._warm_gpu_reranker(fast=True))
    run(rag.embed(["q"], "", "", ""))
    assert order == [("warm", "ruri-reranker-gpu"), ("embed", "q")]

    monkeypatch.setattr(cpu_reranker, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(cpu_reranker, "gpu_free_bytes", lambda: 900 * 1024 ** 2)
    with pytest.raises(cpu_reranker.CpuRerankerError, match="VRAM"):
        run(cpu_reranker._Worker("ruri-reranker-gpu")._start())
    assert cpu_reranker.GPU_IDLE_STOP_S == 180 and cpu_reranker.IDLE_STOP_S == 600
    assert not cpu_reranker.resident("ruri-reranker-gpu")
    cpu_reranker.set_resident("ruri-reranker-gpu", True)
    assert cpu_reranker.resident("ruri-reranker-gpu") and cpu_reranker.status("ruri-reranker-gpu")["resident"]
    name = cpu_reranker.MODELS["xsmall-reranker"]["model_file"]
    assert name.startswith("onnx/model_qint8_") and cpu_reranker.MODELS["xsmall-reranker"]["files"][name][0] == 37367189



def test_cpu_llama_helpers_never_touch_the_gpu(monkeypatch, tmp_path):
    """n_gpu_layers 0（CPU）の llama.cpp には --device none を付ける。付けないと大きな計算を自動で GPU へ回す
    （2026-11-20 実測: bge-m3 は -ngl 0 だけだと 32 件 1.2 秒で GPU を使っていた。GPU を使わせないと 20.9 秒）。"""
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_help_text_sync", lambda: "--fit-target")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    for layers, expect in ((0, True), (999, False)):
        monkeypatch.setattr(llama, "get_instance", lambda alias, layers=layers: {
            **llama.DEFAULT_INSTANCE, "alias": "e", "role": "embedding", "model_path": "/m.gguf", "n_gpu_layers": layers})
        assert ('"--device" "none"' in llama._unit_content("e", margin_mib=0)) is expect


def test_embedding_start_brings_up_the_gpu_reranker_first(monkeypatch):
    """GPU の埋め込みを載せる前に、GPU の reranker（ruri）を先に載せる。LLM を載せる前は両方を止める。
    根拠: 2026-11-20 利用者と合意。ruri は全部を GPU に載せないと動かず、bge-m3 は空きに合わせて残りを CPU で動かせる。
    埋め込みだけを使う場面（添付の保存・調査結果の保存）でも先に場所を取る。補助は使うときだけ載せる（LLM の後に必ず載せはしない）。"""
    from app.models_mgmt import large_model_lifecycle, llama
    from app.workflows import cpu_reranker

    calls = []

    async def warm():
        calls.append("reranker")
        return "ruri-reranker-gpu"

    async def release():
        calls.append("release")
        return []

    async def unhealthy(alias):
        return {"ok": False}

    async def nothing(alias):
        return None

    monkeypatch.setattr(cpu_reranker, "warm_gpu_reranker", warm)
    monkeypatch.setattr(large_model_lifecycle, "release_gpu_helpers", release)
    monkeypatch.setattr(llama, "health", unhealthy)
    monkeypatch.setattr(llama, "mark_used_by_base_url", lambda url: None)
    monkeypatch.setattr(llama, "_reclaim_gpu_before_load", nothing)
    monkeypatch.setattr(llama, "start_instance", lambda alias: (calls.append(f"start:{alias}"), (False, "test"))[1])
    for alias, role, layers in (("embed", "embedding", 999), ("embed-cpu", "embedding", 0), ("chat", "llm", 999)):
        monkeypatch.setattr(llama, "get_instance", lambda a, role=role, layers=layers: {"alias": a, "role": role, "port": 1, "n_gpu_layers": layers})
        assert run(llama.ensure_ready(alias)) is False
    assert calls == ["reranker", "start:embed", "start:embed-cpu", "release", "start:chat"]
