"""RAG の窓口（v3）。保存と検索は app.knowledge.store（統合ナレッジストア）が受け持つ。

既存の呼び出し（会話別 RAG・ワークフローのノード・Knowledge 画面・Deep Research）の関数と戻り値の形を保つ。
v2 からの変更点:
- 保存先は data_dir/knowledge/{general,patents,standards}.sqlite3。旧 data_dir/rag/{collection}.db は
  コレクションを初めて使うときに取り込む（app.knowledge.migrate）。旧ファイルは消さない。
- 2,000 チャンクで黙って切り詰めていたのをやめ、埋め込みを 64 件ずつ送る。
- ベクトル検索は「毎回全件を読んで 1 件ずつ計算」から RAM 上の行列計算へ。
- 全文検索は日本語 2-gram（2 文字の語も当たる）。
- async の中で SQLite を同期的に呼ばない（asyncio.to_thread）。

検索モード: vector / fulltext / hybrid（RRF）/ graph。parent_child は子で検索し親を文脈にする。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from functools import wraps
from pathlib import Path

import httpx
import numpy as np

from app.knowledge import migrate, store
from app.workflows import chunkers

logger = logging.getLogger("control_deck.rag")

COLLECTION_RE = store.COLLECTION_RE
EMBED_BATCH = 64
# 埋め込みの入力の上限（文字）。llama.cpp の埋め込みは 1 件 512 トークンまで（physical batch）で、
# 日本語 1200 文字（約 820 トークン）は HTTP 500 で断られた（2026-11-21 本番）。検索・回答に使う本文は切らない
EMBED_MAX_CHARS = 600

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


def require_loopback_embed(base_url: str) -> None:
    """埋め込みの宛先はループバックだけ。資料（未公開の発明を含みうる）の本文を外部 URL へ送らない。

    2026-11-21 利用者「知財の、私の方から出したアイデアは、ローカル LLM 以外の LLM へのアクセスを拒否する形に」。
    """
    import ipaddress
    from urllib.parse import urlsplit

    try:
        host = urlsplit(str(base_url or "")).hostname or ""
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise RagError("埋め込みの宛先はこの PC（ループバック）だけ指定できます。外部の URL へは資料を送れません")


def _as_rag_error(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except store.KnowledgeError as exc:
            raise RagError(str(exc)) from exc
    return wrapper


def _check_name(collection: str) -> None:
    if not COLLECTION_RE.match(collection or ""):
        raise RagError(f"不正なコレクション名: {collection}（英数・ハイフン・アンダースコア 1〜64 文字）")


def _ensure(collection: str) -> store.CollectionRef | None:
    _check_name(collection)
    migrate.ensure_legacy_collection(collection)
    return store.find_collection(collection)


def collection_exists(collection: str) -> bool:
    if not COLLECTION_RE.match(collection or ""):
        return False
    return _ensure(collection) is not None


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


def _merged(raw: dict) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in (raw or {}).items() if k in DEFAULT_CONFIG})
    # 埋め込みが既定値のまま（=明示設定なし）なら管理中の推奨モデルへ動的追従
    if (cfg["embed_base_url"] == DEFAULT_CONFIG["embed_base_url"]
            and cfg["embed_model"] == DEFAULT_CONFIG["embed_model"]):
        cfg.update(_managed_embed_defaults())
    return cfg


def get_config(collection: str) -> dict:
    ref = _ensure(collection)
    return _merged(ref.config if ref else {})


@_as_rag_error
def set_config(collection: str, config: dict) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})
    require_loopback_embed(cfg["embed_base_url"])
    if _ensure(collection) is None:
        store.create_collection(collection, config=cfg, description=str(cfg.get("description", "")))
    else:
        store.set_collection_config(collection, cfg)
    return cfg


@_as_rag_error
def create_collection(collection: str, config: dict | None = None) -> dict:
    if collection_exists(collection):
        raise RagError(f"コレクションは既に存在します: {collection}")
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in (config or {}).items() if k in DEFAULT_CONFIG})
    require_loopback_embed(cfg["embed_base_url"])
    kind = "conversation" if collection.startswith("chat-") else "general"
    store.create_collection(collection, kind=kind, config=cfg, description=str(cfg.get("description", "")))
    return {"collection": collection, "config": get_config(collection)}


@_as_rag_error
def copy_collection(source: str, target: str) -> bool:
    """コレクションを丸ごと写す（会話の分岐で資料を引き継ぐ）。"""
    _check_name(source)
    _check_name(target)
    if _ensure(source) is None or collection_exists(target):
        return False
    return store.copy_collection(source, target)


@_as_rag_error
def delete_collection(collection: str) -> None:
    _check_name(collection)
    store.delete_collection(collection)
    migrate.mark_deleted(collection)
    graph = _graph_path(collection)
    if graph.exists():
        graph.unlink()


async def embed(texts: list[str], base_url: str, model: str, api_key: str) -> list[np.ndarray]:
    from app.models_mgmt import llama
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    require_loopback_embed(base_url)   # 保存済みの設定が外部 URL でも送らない
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


async def _embed_batched(texts: list[str], cfg: dict, api_key: str) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    texts = [t[:EMBED_MAX_CHARS] for t in texts]
    for start in range(0, len(texts), EMBED_BATCH):
        out.extend(await embed(texts[start:start + EMBED_BATCH], cfg["embed_base_url"], cfg["embed_model"], api_key))
    if len(out) != len(texts):
        raise RagError(f"埋め込みの件数が合いません（{len(out)} / {len(texts)}）")
    return out


async def add_document(
    collection: str, text: str, source: str, api_key: str = "",
    config_override: dict | None = None, reset: bool = False, basis: str = "source_fact",
    strategy: str | None = None,
) -> dict:
    """文書を取り込む。コレクション設定のチャンク戦略で分割し、埋め込み+全文索引に登録する。

    basis: 原文の事実（source_fact）か、AI の回答（model_inference）か。AI の回答を根拠として扱わないため（計画 §10.2）。"""
    ref = await asyncio.to_thread(_ensure, collection)
    cfg = _merged(ref.config if ref else {})
    if config_override:
        cfg.update({k: v for k, v in config_override.items() if k in DEFAULT_CONFIG})
    if ref is None or config_override:
        await asyncio.to_thread(set_config, collection, cfg)
    # strategy: この文書だけの分け方（コレクションの設定は変えない）。Web の取り込みは見出しで分ける
    # （2026-11-21 本番の評価: 見出しで分けると検索の当たり 19→23/26、答えの正解 20→23/26）
    chunk_objs = chunkers.chunk(text, {**cfg, "strategy": strategy} if strategy in chunkers.STRATEGIES else cfg)
    if not chunk_objs:
        raise RagError("入力テキストが空です")
    # 埋め込みのサーバーが止まっていても本文と全文索引は残す（全文検索は使える）。ベクトルは後で
    # Knowledge →「資料の管理」→「作り直し」の「埋め込み」で付ける（rebuild.plan が欠けたベクトルを拾う）。
    # 以前は接続できないだけで取り込みが丸ごと失敗していた（2026-11-21 ボタンの確認で発見）。
    embed_error = ""
    try:
        embeddings: list = await _embed_batched([c.text for c in chunk_objs], cfg, api_key)
    except (RagError, httpx.HTTPError, OSError) as exc:
        embed_error = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("埋め込みを付けずに取り込みます（後で作り直し）: %s", embed_error)
        embeddings = [None] * len(chunk_objs)
    chunks = [store.NewChunk(text=c.text, parent=c.parent, vector=e) for c, e in zip(chunk_objs, embeddings)]
    try:
        result = await asyncio.to_thread(
            store.add_document, collection, chunks, source=source or "document", title=source or "",
            meta={"strategy": cfg["strategy"]}, model=cfg["embed_model"], replace_collection=reset, body=text,
            basis=basis)
    except store.KnowledgeError as exc:
        raise RagError(str(exc)) from exc
    return {
        "collection": collection, "doc_id": result["doc_id"], "source": source,
        "added_chunks": result["added_chunks"], "total_chunks": result["total_chunks"], "strategy": cfg["strategy"],
        "embedded": not embed_error, "embed_error": embed_error,
    }


@_as_rag_error
def list_documents(collection: str) -> list[dict]:
    if _ensure(collection) is None:
        return []
    return store.list_documents(collection)


@_as_rag_error
def delete_document(collection: str, doc_id: int) -> None:
    if _ensure(collection) is not None:
        store.delete_document(collection, int(doc_id))


@_as_rag_error
def chunk_rows(collection: str) -> list[dict]:
    if _ensure(collection) is None:
        raise RagError(f"コレクションが存在しません: {collection}")
    return store.chunk_rows(collection)


# 検索結果を資料として保存するときに先頭へ入っていた回答の指示文。資料として渡すときは除く
_INSTRUCTION_HEAD = re.compile(r"^以下の検索結果を根拠に[^\n]*\n(?:[^\n]*\n)*?\n")


def strip_instruction(text: str) -> str:
    return _INSTRUCTION_HEAD.sub("", text, count=1)


def collection_size_chars(collection: str) -> int:
    """会話の資料の大きさ（断片の文字数の合計）。モデルへ「資料がどれぐらいあるか」を伝えるのに使う。"""
    if not collection_exists(collection):
        return 0
    return store.collection_text_chars(collection)


def collection_materials(collection: str, max_chars: int) -> str | None:
    """会話の資料をすべて、文書の順・断片の順で 1 つの文にする（探さない）。max_chars を超えるなら None。

    2026-11-20 利用者「毎回会話ごとにデータを調査するのか。資料を全部入れるのもありか。速度は速めに」。
    会話の資料は 45 会話中 41 が 4.9 万文字以内（中央値 3,800 文字）だった。小さい資料は探さず全部入れる:
    埋め込みも並べ替えも要らず、毎回同じ内容・同じ位置なので、モデルが前回の読み込みを使い回せる。"""
    if not collection_exists(collection):
        return None
    rows = store.material_rows(collection)
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
    if not await asyncio.to_thread(collection_exists, collection):
        raise RagError(f"コレクションが存在しません: {collection}")
    cfg = await asyncio.to_thread(get_config, collection)
    mode = mode_override or cfg["search_mode"]
    top_k = max(1, min(int(top_k), 20))
    if mode == "graph":
        # グラフ拡張検索（ベクトル文脈 + グラフの関連事実）
        from app.workflows import rag_graph

        return await rag_graph.graph_search(collection, question, top_k, api_key=api_key)
    query_vector = None
    if mode in ("vector", "hybrid"):
        # GPU の reranker を使うなら、埋め込み（bge-m3）より先に起動して場所を取る（2026-11-20 利用者と合意）。
        # ruri は全部を GPU に載せないと動かないが、bge-m3 は空いている分だけ載せて残りを CPU で動かせる
        await _warm_gpu_reranker(fast=rerank_budget_s is not None)
        query_vector = (await embed([question], cfg["embed_base_url"], cfg["embed_model"], api_key))[0]
    found = await asyncio.to_thread(
        store.search, [collection], question, query_vector=query_vector, model=cfg["embed_model"],
        mode=mode, hybrid_weight=float(cfg.get("hybrid_weight", 0.5)), limit=top_k * 8)
    expanded_terms: list[dict] = []
    widened = await _hub_expand_hits(collection, question, top_k, found)
    if widened is not None:
        found, expanded_terms = widened
    if not found:
        return {"matches": [], "context": "", "count": 0}

    # rerank用に多め（top_k*4）の候補を集め、reranker未登録時は先頭top_kを使う
    matches = []
    seen_parents: set[str] = set()
    for item in found:
        if len(matches) >= top_k * 4:
            break
        parent = item.get("parent")
        # parent_child のときは親を文脈にする（重複親は1回だけ）
        if parent:
            if parent in seen_parents:
                continue
            seen_parents.add(parent)
        matches.append({"score": item["score"], "text": item["text"], "context": parent or item["text"],
                        "source": item["source"], "chunk_ref": item["chunk_ref"], "doc_ref": item["doc_ref"],
                        "locator": item["locator"]})

    reranked = await _maybe_rerank(rerank_query or question, matches, top_k, budget_s=rerank_budget_s)
    if min_rerank_score is not None and any(m.get("rerank_engine") == "llama" for m in reranked):
        # 並べ替えの点数が低いもの（関係の薄いもの）は入れない。挨拶なら何も入らない。
        # Qwen3-Reranker の点数だけで切る。ruri は会話の流れを指す質問（「さっき調べた番組表」）にも低い点を
        # 付けるので、点数では切らず上位の件数で入れる（docs/reranker-evaluation.md）
        reranked = [m for m in reranked if m.get("rerank_score", 0.0) >= min_rerank_score]
    context = "\n\n---\n\n".join(strip_instruction(m["context"]) for m in reranked)
    out = {"matches": reranked, "context": context, "count": len(reranked), "mode": mode,
           "reranked": len(reranked) != len(matches) or any("rerank_score" in m for m in reranked)}
    if expanded_terms:
        out["expanded_terms"] = expanded_terms
    return out


async def _hub_expand_hits(collection: str, question: str, top_k: int, found: list[dict]):
    """ニュースハブの関係・概要で語を広げた問い（OR）でも引き、元の結果へ RRF で融合する。設定で切ってあれば・つながっていなければ・広げる語が無ければ None（元の語だけ）。
    測定と根拠は docs/implementation-status.md「ハブの関係・概要で検索の語を広げる」。"""
    from app.knowledge import hub_expand

    if not await asyncio.to_thread(hub_expand.enabled, "rag"):
        return None
    ex = await hub_expand.aexpand(question, purpose="rag")
    if not ex["terms"]:
        return None
    extra = await asyncio.to_thread(store.search, [collection], hub_expand.or_query(ex["terms"]), mode="fulltext", limit=top_k * 8)
    if not extra:
        return None
    return hub_expand.fuse_hits(found, extra)[:top_k * 8], [{"term": t["term"], "reason": t["reason"]} for t in ex["terms"][:6]]


async def _warm_gpu_reranker(fast: bool) -> None:
    from app.workflows import cpu_reranker

    await cpu_reranker.warm_gpu_reranker()


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
    names = set()
    for item in store.list_collections():
        cfg = _merged(item["config"])
        names.add(item["collection"])
        result.append({
            "collection": item["collection"], "chunks": item["chunks"], "documents": item["documents"],
            "strategy": cfg["strategy"], "search_mode": cfg["search_mode"], "embed_model": cfg["embed_model"],
            "description": item.get("description") or cfg.get("description", ""),
            "shard": item["shard"], "kind": item["kind"], "sensitivity": item["sensitivity"],
        })
    # まだ取り込んでいない旧コレクションも一覧に出す（読み取りだけ。取り込みは初回利用時）
    for plan in migrate.pending_legacy(skip=names):
        cfg = _merged(plan["config"])
        result.append({
            "collection": plan["name"], "chunks": plan["chunks"], "documents": plan["documents"],
            "strategy": cfg["strategy"], "search_mode": cfg["search_mode"], "embed_model": cfg["embed_model"],
            "description": cfg.get("description", ""), "shard": "general", "kind": "legacy",
            "sensitivity": "confidential",
        })
    return sorted(result, key=lambda c: c["collection"])


# ---- GraphRAG の三つ組（コレクションごとの別ファイル） ----


def _graph_path(collection: str) -> Path:
    from app.knowledge import storage

    _check_name(collection)
    return storage.graphs_dir() / f"{collection}.sqlite3"


def graph_db(collection: str) -> sqlite3.Connection:
    path = _graph_path(collection)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path, timeout=10)


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
    if not await asyncio.to_thread(collection_exists, collection):
        await asyncio.to_thread(create_collection, collection, {"embed_base_url": base_url, "embed_model": model})
    else:
        current = await asyncio.to_thread(get_config, collection)
        await asyncio.to_thread(set_config, collection, {**current, "embed_base_url": base_url, "embed_model": model})
    return await add_document(collection, text, source, api_key=api_key, reset=reset)


async def query(collection: str, question: str, top_k: int, base_url: str, model: str, api_key: str) -> dict:
    """旧 rag.query 互換。"""
    if await asyncio.to_thread(collection_exists, collection):
        current = await asyncio.to_thread(get_config, collection)
        await asyncio.to_thread(set_config, collection, {**current, "embed_base_url": base_url, "embed_model": model})
    return await search(collection, question, top_k, api_key=api_key)


def stored_config(collection: str) -> dict:
    """保存されている生の設定（既定値の補完なし）。テストと診断用。"""
    ref = _ensure(collection)
    return json.loads(json.dumps(ref.config)) if ref else {}
