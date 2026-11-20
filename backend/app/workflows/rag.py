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

import logging

from app.config import data_dir
from app.workflows import chunkers

logger = logging.getLogger("control_deck.rag")

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


def _managed_embed_defaults() -> dict:
    """管理中の埋め込みモデル（role=embedding instance）を既定として動的解決する。

    レガシー既定（nomic-embed-text@Ollama）はモデル未導入だと機能しないため、
    明示設定されていないコレクションはModel画面で管理する推奨埋め込みへ自動追従する。
    """
    try:
        from app.models_mgmt import llama

        instance = llama.find_role_instance("embedding")
        if instance is not None:
            return {"embed_base_url": str(instance["base_url"]),
                    "embed_model": str(instance["alias"])}
    except Exception:
        pass
    return {}


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
    # 埋め込みが既定値のまま（=明示設定なし）なら管理中の推奨モデルへ動的追従
    if (cfg["embed_base_url"] == DEFAULT_CONFIG["embed_base_url"]
            and cfg["embed_model"] == DEFAULT_CONFIG["embed_model"]):
        cfg.update(_managed_embed_defaults())
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


def copy_collection(source: str, target: str) -> bool:
    """コレクションを丸ごと写す（会話の分岐で資料を引き継ぐ）。sqlite の backup で整合した複写を作る。"""
    if not COLLECTION_RE.match(source) or not COLLECTION_RE.match(target):
        raise RagError("不正なコレクション名")
    src_path, dst_path = _rag_dir() / f"{source}.db", _rag_dir() / f"{target}.db"
    if not src_path.exists() or dst_path.exists():
        return False
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return True


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


# 検索結果を資料として保存するときに先頭へ入っていた回答の指示文。資料として渡すときは除く
_INSTRUCTION_HEAD = re.compile(r"^以下の検索結果を根拠に[^\n]*\n(?:[^\n]*\n)*?\n")


def strip_instruction(text: str) -> str:
    return _INSTRUCTION_HEAD.sub("", text, count=1)


def collection_size_chars(collection: str) -> int:
    """会話の資料の大きさ（断片の文字数の合計）。モデルへ「資料がどれぐらいあるか」を伝えるのに使う。"""
    if not collection_exists(collection):
        return 0
    conn = _db(collection)
    try:
        return int(conn.execute("SELECT COALESCE(SUM(LENGTH(text)), 0) FROM chunks").fetchone()[0])
    finally:
        conn.close()


def collection_materials(collection: str, max_chars: int) -> str | None:
    """会話の資料をすべて、文書の順・断片の順で 1 つの文にする（探さない）。max_chars を超えるなら None。

    2026-11-20 利用者「毎回会話ごとにデータを調査するのか。資料を全部入れるのもありか。速度は速めに」。
    会話の資料は 45 会話中 41 が 4.9 万文字以内（中央値 3,800 文字）だった。小さい資料は探さず全部入れる:
    埋め込みも並べ替えも要らず、毎回同じ内容・同じ位置なので、モデルが前回の読み込みを使い回せる。"""
    if not collection_exists(collection):
        return None
    conn = _db(collection)
    try:
        rows = conn.execute(
            "SELECT c.doc_id, c.text, c.parent, d.source FROM chunks c LEFT JOIN documents d ON d.id = c.doc_id "
            "ORDER BY c.doc_id, c.idx").fetchall()
    finally:
        conn.close()
    parts: list[str] = []
    seen_parents: set[str] = set()
    current = None
    size = 0
    for doc_id, text, parent, source in rows:
        piece = parent or text
        if parent:
            if parent in seen_parents:
                continue
            seen_parents.add(parent)
        piece = strip_instruction(piece).strip()
        if not piece:
            continue
        if doc_id != current:
            current = doc_id
            header = f"\n### 資料 {doc_id}" + (f"（{source}）" if source else "")
            parts.append(header)
            size += len(header)
        parts.append(piece)
        size += len(piece)
        if size > max_chars:
            return None
    return "\n".join(parts).strip() or None


async def search(
    collection: str, question: str, top_k: int, api_key: str = "",
    mode_override: str | None = None, rerank_budget_s: float | None = None,
    min_rerank_score: float | None = None, rerank_query: str | None = None,
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
            # GPU の reranker を使うなら、埋め込み（bge-m3）より先に起動して場所を取る（2026-11-20 利用者と合意）。
            # ruri は全部を GPU に載せないと動かないが、bge-m3 は空いている分だけ載せて残りを CPU で動かせる
            await _warm_gpu_reranker(fast=rerank_budget_s is not None)
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

    reranked = await _maybe_rerank(rerank_query or question, matches, top_k, budget_s=rerank_budget_s)
    if min_rerank_score is not None and any(m.get("rerank_engine") == "llama" for m in reranked):
        # 並べ替えの点数が低いもの（関係の薄いもの）は入れない。挨拶なら何も入らない。
        # Qwen3-Reranker の点数だけで切る。ruri は会話の流れを指す質問（「さっき調べた番組表」）にも低い点を
        # 付けるので、点数では切らず上位の件数で入れる（docs/reranker-evaluation.md）
        reranked = [m for m in reranked if m.get("rerank_score", 0.0) >= min_rerank_score]
    context = "\n\n---\n\n".join(strip_instruction(m["context"]) for m in reranked)
    return {"matches": reranked, "context": context, "count": len(reranked), "mode": mode,
            "reranked": len(reranked) != len(matches) or any("rerank_score" in m for m in reranked)}


async def _warm_gpu_reranker(fast: bool) -> None:
    from app.workflows import cpu_reranker

    model_id = cpu_reranker.choose(fast)
    if model_id in cpu_reranker.MODELS and cpu_reranker.MODELS[model_id]["device"] == "gpu" \
            and not cpu_reranker.running(model_id):
        try:
            await cpu_reranker.warm(model_id)
        except cpu_reranker.CpuRerankerError as exc:
            logger.info("GPU の reranker を先に起動できませんでした（並べ替えは CPU の控えで行います）: %s", exc)


# 再ランクの記録（自己診断）。2026-11-20 本番: GPU を他のモデルが使い切っていると reranker が CPU で動き、
# AI アシスタントの挨拶 1 つで再ランクに 22.6 秒かかり、思考が始まるまで 23 秒待たせていた
rerank_stats = {"calls": 0, "timeouts": 0, "last_seconds": 0.0, "slowest_seconds": 0.0}


async def _maybe_rerank(question: str, matches: list[dict], top_k: int, *, budget_s: float | None = None) -> list[dict]:
    """role=reranker のllama instance（Qwen3-Reranker等）が登録済みなら /v1/rerank で
    上位top_kを選び直す。未登録・停止起動失敗・APIエラー時は先頭top_kへフォールバック。

    budget_s を渡すと、起動待ちも含めてその秒数で打ち切り、検索の順のまま返す（会話を待たせない）。"""
    if budget_s is None:
        return await _rerank(question, matches, top_k)
    started = time.monotonic()
    try:
        return await asyncio.wait_for(_rerank(question, matches, top_k, fast=True), timeout=budget_s)
    except TimeoutError:
        rerank_stats["timeouts"] += 1
        logger.warning("再ランクが %.1f 秒で終わらないため検索の順で続けます（%d 件）", budget_s, len(matches))
        return matches[:top_k]
    finally:
        elapsed = time.monotonic() - started
        rerank_stats["last_seconds"] = round(elapsed, 2)
        rerank_stats["slowest_seconds"] = max(rerank_stats["slowest_seconds"], round(elapsed, 2))


async def _rerank(question: str, matches: list[dict], top_k: int, *, fast: bool = False) -> list[dict]:
    """CPU の reranker（導入済みなら優先）か、role=reranker の llama instance で並べ替える。

    2026-11-20 利用者の判断: reranker は GPU に置かず CPU で。軽量・高速を優先（精度は多少落ちてもよい）。
    fast（会話の資料。3 秒まで）は速いモデル（xsmall）を、それ以外（Knowledge・Workflow）は精度の高いモデル（ruri）を
    優先する（cpu_reranker.pick）。資料は切らずに 1024 トークンまで読む。ruri を会話で使うときは候補を top_k×2 件に
    絞る（標準の長さ 32 件で 8 秒かかるため）。CPU が未導入なら従来の GPU の reranker。"""
    from app.models_mgmt import llama
    from app.workflows import cpu_reranker

    if len(matches) <= top_k:
        return matches[:top_k]
    model_id = cpu_reranker.choose(fast)   # Models の「Embed / Reranker」で選んだもの（既定は自動）
    if model_id == "none":
        return matches[:top_k]
    if model_id != "gpu":
        rerank_stats["calls"] += 1
        pool_factor, max_tokens = cpu_reranker.limits(model_id, fast)
        pool = matches[:top_k * pool_factor]
        try:
            scores = await cpu_reranker.score(question, [m["text"] for m in pool], model_id=model_id,
                                              max_tokens=max_tokens)
        except (cpu_reranker.CpuRerankerError, ValueError):
            # GPU の ruri が使えない（VRAM が埋まっている等）ときは、入っている CPU のものへ回す
            fallback = cpu_reranker.pick(fast, cpu_only=True) if cpu_reranker.MODELS[model_id]["device"] == "gpu" else None
            if fallback is None:
                return matches[:top_k]
            model_id = fallback
            pool_factor, max_tokens = cpu_reranker.limits(model_id, fast)
            pool = matches[:top_k * pool_factor]
            try:
                scores = await cpu_reranker.score(question, [m["text"] for m in pool], model_id=model_id,
                                                  max_tokens=max_tokens)
            except (cpu_reranker.CpuRerankerError, ValueError):
                return matches[:top_k]
        order = sorted(range(len(pool)), key=lambda i: -scores[i])[:top_k]
        return [{**pool[i], "rerank_score": round(scores[i], 5), "rerank_engine": model_id} for i in order]
    if llama.find_role_instance("reranker") is None:
        return matches[:top_k]
    rerank_stats["calls"] += 1
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
                picked.append({**matches[index], "rerank_score": round(float(item.get("relevance_score", 0.0)), 5),
                               "rerank_engine": "llama"})
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
    from app.models_mgmt import gateway, llama
    from app.models_mgmt.runtime_policy import ensure_gpu_profile
    from app.models_mgmt.runtime_provider import resolve_target

    base_url, model = resolve_target(base_url, model)
    # 解決先が停止中なら起動を待つ（ゲートウェイ経由と同じオンデマンド起動）。
    await llama.ensure_ready_by_base_url(base_url)
    try:
        await asyncio.to_thread(ensure_gpu_profile, base_url=base_url)
    except RuntimeError as exc:
        raise RagError(str(exc)) from exc
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": temperature, "stream": False, "max_tokens": 2048},
            headers={"Authorization": f"Bearer {gateway.internal_api_key(base_url, api_key)}", **gateway.client_headers(base_url, gateway.ASSISTANT_CLIENT)},
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
