"""RAG ストア v2。

コレクションごとに SQLite（data_dir/rag/{collection}.db）へ保存する。
- meta:       コレクション設定（埋め込み model/url, チャンク戦略, ハイブリッド重み 等）
- documents:  取り込んだ文書（source, 追加日時, チャンク数）
- chunks:     子チャンク（embedding + parent テキスト）。検索対象
- chunks_fts: FTS5 全文索引（キーワード/ハイブリッド検索用）

検索モード: vector（コサイン類似）/ fulltext（FTS5 BM25）/ hybrid（RRF 融合）。
parent_child 戦略のときは子で検索し、親テキストを文脈として返す（重複親は統合）。
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path

import httpx
import numpy as np

from app.config import data_dir
from app.workflows import chunkers

COLLECTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

DEFAULT_CONFIG = {
    "embed_base_url": "http://127.0.0.1:11434/v1",
    "embed_model": "nomic-embed-text",
    "strategy": "recursive",
    "size": 800,
    "overlap": 100,
    "parent_mode": "paragraph",
    "parent_size": 2000,
    "search_mode": "hybrid",  # vector / fulltext / hybrid
    "hybrid_weight": 0.5,  # 0=全文寄り 1=ベクトル寄り
    "description": "",
}


class RagError(ValueError):
    pass


def _rag_dir() -> Path:
    d = data_dir() / "rag"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fts5_ok(conn: sqlite3.Connection) -> bool:
    # trigram トークナイザで日本語(CJK)も部分一致検索できるようにする
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "text, content='chunks', content_rowid='id', tokenize='trigram')"
        )
        return True
    except sqlite3.OperationalError:
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id')")
            return True
        except sqlite3.OperationalError:
            return False


def _db(collection: str) -> sqlite3.Connection:
    if not COLLECTION_RE.match(collection):
        raise RagError(f"不正なコレクション名: {collection}（英数・ハイフン・アンダースコア 1〜64 文字）")
    conn = sqlite3.connect(_rag_dir() / f"{collection}.db")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS documents ("
        "id INTEGER PRIMARY KEY, source TEXT, added_at REAL, chunk_count INTEGER, meta TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks ("
        "id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT, parent TEXT, "
        "embedding BLOB, dim INTEGER, idx INTEGER)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id)")
    _fts5_ok(conn)
    return conn


def collection_exists(collection: str) -> bool:
    return (_rag_dir() / f"{collection}.db").exists()


def get_config(collection: str) -> dict:
    conn = _db(collection)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='config'").fetchone()
    finally:
        conn.close()
    cfg = dict(DEFAULT_CONFIG)
    if row:
        try:
            cfg.update(json.loads(row[0]))
        except json.JSONDecodeError:
            pass
    return cfg


def set_config(collection: str, config: dict) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})
    conn = _db(collection)
    try:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('config', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(cfg),),
        )
        conn.commit()
    finally:
        conn.close()
    return cfg


def create_collection(collection: str, config: dict | None = None) -> dict:
    if collection_exists(collection):
        raise RagError(f"コレクションは既に存在します: {collection}")
    set_config(collection, config or {})
    return {"collection": collection, "config": get_config(collection)}


def delete_collection(collection: str) -> None:
    if not COLLECTION_RE.match(collection):
        raise RagError("不正なコレクション名")
    p = _rag_dir() / f"{collection}.db"
    if p.exists():
        p.unlink()


async def embed(texts: list[str], base_url: str, model: str, api_key: str) -> list[np.ndarray]:
    from app.models_mgmt import llama
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    try:
        await asyncio.to_thread(ensure_gpu_profile, base_url=base_url)
    except RuntimeError as exc:
        raise RagError(str(exc)) from exc
    # llama.cppの埋め込みinstance（BGE-M3等）は停止していればオンデマンド起動する
    await llama.ensure_ready_by_base_url(base_url)
    url = base_url.rstrip("/") + "/embeddings"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            url,
            json={"model": model, "input": texts},
            headers={"Authorization": f"Bearer {api_key or 'sk-no-key'}"},
        )
    if r.status_code >= 400:
        raise RagError(f"埋め込み API エラー {r.status_code}: {r.text[:200]}")
    data = r.json()
    return [np.array(item["embedding"], dtype=np.float32) for item in data["data"]]


async def add_document(
    collection: str, text: str, source: str, api_key: str = "",
    config_override: dict | None = None, reset: bool = False,
) -> dict:
    """文書を取り込む。コレクション設定のチャンク戦略で分割し、埋め込み+FTS登録する。"""
    cfg = get_config(collection)
    if config_override:
        cfg.update({k: v for k, v in config_override.items() if k in DEFAULT_CONFIG})
        set_config(collection, cfg)
    chunk_objs = chunkers.chunk(text, cfg)
    if not chunk_objs:
        raise RagError("入力テキストが空です")
    if len(chunk_objs) > 2000:
        chunk_objs = chunk_objs[:2000]
    embeddings = await embed(
        [c.text for c in chunk_objs], cfg["embed_base_url"], cfg["embed_model"], api_key
    )
    conn = _db(collection)
    try:
        if reset:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            try:
                conn.execute("DELETE FROM chunks_fts")
            except sqlite3.OperationalError:
                pass
        cur = conn.execute(
            "INSERT INTO documents (source, added_at, chunk_count, meta) VALUES (?, ?, ?, ?)",
            (source or "document", time.time(), len(chunk_objs), json.dumps({"strategy": cfg["strategy"]})),
        )
        doc_id = cur.lastrowid
        has_fts = _fts5_ok(conn)
        for i, (c, emb) in enumerate(zip(chunk_objs, embeddings)):
            rid = conn.execute(
                "INSERT INTO chunks (doc_id, text, parent, embedding, dim, idx) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, c.text, c.parent, emb.tobytes(), len(emb), i),
            ).lastrowid
            if has_fts:
                conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (rid, c.text))
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    return {
        "collection": collection, "doc_id": doc_id, "source": source,
        "added_chunks": len(chunk_objs), "total_chunks": total, "strategy": cfg["strategy"],
    }


def list_documents(collection: str) -> list[dict]:
    conn = _db(collection)
    try:
        rows = conn.execute(
            "SELECT id, source, added_at, chunk_count, meta FROM documents ORDER BY added_at DESC"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for i, source, added_at, cc, meta in rows:
        try:
            m = json.loads(meta or "{}")
        except json.JSONDecodeError:
            m = {}
        out.append({"id": i, "source": source, "added_at": added_at, "chunk_count": cc, "strategy": m.get("strategy")})
    return out


def delete_document(collection: str, doc_id: int) -> None:
    conn = _db(collection)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        try:
            for rid in ids:
                conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (rid,))
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def _fts_query(question: str) -> str:
    # trigram: 3文字以上の語を "..." 句として OR 結合（部分一致）。3文字未満は捨てる。
    terms = [t for t in re.findall(r"[\w一-龠ぁ-んァ-ヶー]+", question) if len(t) >= 3]
    if not terms:
        return '"' + question.strip()[:64].replace('"', "") + '"'
    return " OR ".join(f'"{t}"' for t in terms[:32])


async def search(
    collection: str, question: str, top_k: int, api_key: str = "",
    mode_override: str | None = None,
) -> dict:
    if not collection_exists(collection):
        raise RagError(f"コレクションが存在しません: {collection}")
    cfg = get_config(collection)
    mode = mode_override or cfg["search_mode"]
    top_k = max(1, min(int(top_k), 20))
    if mode == "graph":
        # グラフ拡張検索（ベクトル文脈 + グラフの関連事実）
        from app.workflows import rag_graph

        return await rag_graph.graph_search(collection, question, top_k, api_key=api_key)
    conn = _db(collection)
    try:
        rows = conn.execute("SELECT id, text, parent, embedding, dim FROM chunks").fetchall()
        if not rows:
            return {"matches": [], "context": "", "count": 0}

        # ベクトルスコア
        vec_rank: dict[int, float] = {}
        if mode in ("vector", "hybrid"):
            q_emb = (await embed([question], cfg["embed_base_url"], cfg["embed_model"], api_key))[0]
            q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-8)
            sims = []
            for cid, text, parent, blob, dim in rows:
                emb = np.frombuffer(blob, dtype=np.float32)
                if emb.shape[0] != q_emb.shape[0]:
                    continue
                sim = float(np.dot(q_norm, emb / (np.linalg.norm(emb) + 1e-8)))
                sims.append((cid, sim))
            sims.sort(key=lambda x: -x[1])
            for rank, (cid, _) in enumerate(sims):
                vec_rank[cid] = rank

        # 全文スコア（FTS5 BM25）
        fts_rank: dict[int, float] = {}
        if mode in ("fulltext", "hybrid"):
            try:
                frows = conn.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 100",
                    (_fts_query(question),),
                ).fetchall()
                for rank, (rid,) in enumerate(frows):
                    fts_rank[rid] = rank
            except sqlite3.OperationalError:
                # FTS 未対応環境: LIKE フォールバック
                terms = re.findall(r"[\w一-龠ぁ-んァ-ヶ]+", question)[:5]
                if terms:
                    like = " OR ".join(["text LIKE ?"] * len(terms))
                    frows = conn.execute(
                        f"SELECT id FROM chunks WHERE {like} LIMIT 100",
                        tuple(f"%{t}%" for t in terms),
                    ).fetchall()
                    for rank, (rid,) in enumerate(frows):
                        fts_rank[rid] = rank

        text_by_id = {cid: (text, parent) for cid, text, parent, _, _ in rows}

        # スコア融合（Reciprocal Rank Fusion）
        K = 60
        w = float(cfg.get("hybrid_weight", 0.5))
        scores: dict[int, float] = {}
        if mode == "vector":
            for cid, rank in vec_rank.items():
                scores[cid] = 1.0 / (K + rank)
        elif mode == "fulltext":
            for cid, rank in fts_rank.items():
                scores[cid] = 1.0 / (K + rank)
        else:  # hybrid RRF
            for cid, rank in vec_rank.items():
                scores[cid] = scores.get(cid, 0) + w / (K + rank)
            for cid, rank in fts_rank.items():
                scores[cid] = scores.get(cid, 0) + (1 - w) / (K + rank)

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        # rerank用に多め（top_k*4）の候補を集め、reranker未登録時は先頭top_kを使う
        matches = []
        seen_parents: set[str] = set()
        for cid, sc in ranked:
            if len(matches) >= top_k * 4:
                break
            text, parent = text_by_id[cid]
            # parent_child のときは親を文脈にする（重複親は1回だけ）
            context_text = parent or text
            if parent:
                if parent in seen_parents:
                    continue
                seen_parents.add(parent)
            matches.append({"score": round(sc, 5), "text": text, "context": context_text})
    finally:
        conn.close()

    reranked = await _maybe_rerank(question, matches, top_k)
    context = "\n\n---\n\n".join(m["context"] for m in reranked)
    return {"matches": reranked, "context": context, "count": len(reranked), "mode": mode,
            "reranked": len(reranked) != len(matches) or any("rerank_score" in m for m in reranked)}


async def _maybe_rerank(question: str, matches: list[dict], top_k: int) -> list[dict]:
    """role=reranker のllama instance（Qwen3-Reranker等）が登録済みなら /v1/rerank で
    上位top_kを選び直す。未登録・停止起動失敗・APIエラー時は先頭top_kへフォールバック。"""
    from app.models_mgmt import llama

    if len(matches) <= top_k or llama.find_role_instance("reranker") is None:
        return matches[:top_k]
    base = await llama.ensure_role_ready("reranker")
    if base is None:
        return matches[:top_k]
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(base.rstrip("/") + "/rerank", json={
                "model": "reranker", "query": question,
                "documents": [m["text"] for m in matches], "top_n": top_k,
            })
        if response.status_code >= 400:
            return matches[:top_k]
        results = response.json().get("results", [])
        picked = []
        for item in results[:top_k]:
            index = int(item.get("index", -1))
            if 0 <= index < len(matches):
                picked.append({**matches[index], "rerank_score": round(float(item.get("relevance_score", 0.0)), 5)})
        return picked or matches[:top_k]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return matches[:top_k]


def list_collections() -> list[dict]:
    result = []
    for f in sorted(_rag_dir().glob("*.db")):
        try:
            conn = sqlite3.connect(f)
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            row = conn.execute("SELECT value FROM meta WHERE key='config'").fetchone()
            conn.close()
            cfg = dict(DEFAULT_CONFIG)
            if row:
                try:
                    cfg.update(json.loads(row[0]))
                except json.JSONDecodeError:
                    pass
            result.append({
                "collection": f.stem, "chunks": chunks, "documents": docs,
                "strategy": cfg["strategy"], "search_mode": cfg["search_mode"],
                "embed_model": cfg["embed_model"], "description": cfg.get("description", ""),
            })
        except sqlite3.Error:
            continue
    return result


# ---- 検索強化: HyDE / マルチクエリ（RAG-Fusion） ----


async def _llm_complete(prompt: str, base_url: str, model: str, api_key: str, temperature: float = 0.3) -> str:
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    try:
        await asyncio.to_thread(ensure_gpu_profile, base_url=base_url)
    except RuntimeError as exc:
        raise RagError(str(exc)) from exc
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": temperature, "stream": False, "max_tokens": 2048},
            headers={"Authorization": f"Bearer {api_key or 'sk-no-key'}"},
        )
    if r.status_code >= 400:
        raise RagError(f"LLM エラー {r.status_code}: {r.text[:150]}")
    return r.json()["choices"][0]["message"]["content"]


async def search_enhanced(
    collection: str, question: str, top_k: int, api_key: str = "", mode_override: str | None = None,
    hyde: bool = False, multi_query: int = 0, llm_base_url: str = "", llm_model: str = "",
) -> dict:
    """HyDE / マルチクエリで検索を強化する。LLM を用いる（llm_base_url/model 必須）。"""
    queries = [question]
    if (hyde or multi_query) and llm_base_url and llm_model:
        try:
            if hyde:
                # 仮想的な回答文を生成し、それを埋め込みクエリにする（HyDE）
                hypo = await _llm_complete(
                    f"次の質問に対する理想的な回答の本文を、事実に基づき簡潔に書いてください。\n質問: {question}",
                    llm_base_url, llm_model, api_key)
                queries.append(hypo[:1000])
            if multi_query and multi_query > 0:
                # 質問を複数の観点に分解（RAG-Fusion）
                raw = await _llm_complete(
                    f"次の質問を、検索に有効な別々の観点の検索クエリ {multi_query} 個に言い換えてください。"
                    f"1行に1クエリ、番号なしで出力。\n質問: {question}",
                    llm_base_url, llm_model, api_key)
                queries += [ln.strip("・-•* \t") for ln in raw.splitlines() if ln.strip()][:multi_query]
        except RagError:
            pass  # 強化失敗時は元クエリのみで続行

    if len(queries) == 1:
        return await search(collection, question, top_k, api_key=api_key, mode_override=mode_override)

    # 各クエリで検索し RRF で融合
    fused: dict[str, float] = {}
    payload: dict[str, dict] = {}
    for q in queries:
        res = await search(collection, q, top_k, api_key=api_key, mode_override=mode_override)
        for rank, m in enumerate(res["matches"]):
            key = m["context"]
            fused[key] = fused.get(key, 0.0) + 1.0 / (60 + rank)
            payload.setdefault(key, m)
    ranked = sorted(fused.items(), key=lambda x: -x[1])[:top_k]
    matches = [{**payload[k], "score": round(sc, 5)} for k, sc in ranked]
    return {
        "matches": matches,
        "context": "\n\n---\n\n".join(m["context"] for m in matches),
        "count": len(matches),
        "mode": (mode_override or "hybrid") + ("+hyde" if hyde else "") + (f"+mq{multi_query}" if multi_query else ""),
        "queries": queries,
    }


# ---- 後方互換 API（既存ノードが使用） ----


async def build(collection: str, text: str, source: str, base_url: str, model: str, api_key: str, reset: bool) -> dict:
    """旧 rag.build 互換。base_url/model を設定へ反映してから取り込む。"""
    if not collection_exists(collection):
        create_collection(collection, {"embed_base_url": base_url, "embed_model": model})
    else:
        set_config(collection, {**get_config(collection), "embed_base_url": base_url, "embed_model": model})
    return await add_document(collection, text, source, api_key=api_key, reset=reset)


async def query(collection: str, question: str, top_k: int, base_url: str, model: str, api_key: str) -> dict:
    """旧 rag.query 互換。"""
    if collection_exists(collection):
        set_config(collection, {**get_config(collection), "embed_base_url": base_url, "embed_model": model})
    return await search(collection, question, top_k, api_key=api_key)
