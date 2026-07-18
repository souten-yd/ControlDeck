"""GraphRAG（軽量）。

LLM でチャンクから (主語, 関係, 目的語) のトリプルを抽出してグラフ化し、
クエリの起点エンティティから隣接ノードを辿って関連事実を文脈に加える。

- グラフは各コレクション DB の triples テーブルに保存
- 抽出は OpenAI 互換 Chat Completions（Ollama 等）を使用
- graph_search: 起点エンティティ一致 → 1〜2ホップ近傍のトリプルを文脈化
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3

import httpx

from app.workflows import rag

EXTRACT_SYSTEM = (
    "あなたは知識グラフ抽出器です。与えられたテキストから事実の三つ組を抽出します。"
    '必ず JSON のみで {"triples":[{"s":"主語","p":"関係","o":"目的語"}]} の形式で返答してください。'
    "固有名詞・専門用語・数値関係を優先し、最大12件。"
)


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS triples ("
        "id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, chunk_id INTEGER,"
        " UNIQUE(subject, predicate, object))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tri_s ON triples(subject)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tri_o ON triples(object)")


async def _extract(text: str, base_url: str, model: str, api_key: str) -> list[dict]:
    from app.models_mgmt.runtime_provider import response_format_candidates
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    try:
        await asyncio.to_thread(ensure_gpu_profile, base_url=base_url)
    except RuntimeError as exc:
        raise rag.RagError(str(exc)) from exc
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": text[:4000]},
        ],
        "temperature": 0.0,
        "stream": False,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    try:
        r: httpx.Response | None = None
        for candidate in response_format_candidates(payload["response_format"]):
            attempt = dict(payload)
            if candidate is None:
                attempt.pop("response_format", None)
            else:
                attempt["response_format"] = candidate
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    base_url.rstrip("/") + "/chat/completions",
                    json=attempt,
                    headers={"Authorization": f"Bearer {api_key or 'sk-no-key'}"},
                )
            if r.status_code < 400 or r.status_code not in {400, 404, 415, 422, 501}:
                break
        if r is None or r.status_code >= 400:
            return []
        content = r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError):
        return []
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    triples = data.get("triples", []) if isinstance(data, dict) else []
    out = []
    for t in triples:
        if isinstance(t, dict) and t.get("s") and t.get("p") and t.get("o"):
            out.append({"s": str(t["s"])[:200], "p": str(t["p"])[:120], "o": str(t["o"])[:200]})
    return out


async def build_graph(collection: str, base_url: str, model: str, api_key: str,
                      max_chunks: int = 200, on_progress=None) -> dict:
    """コレクションの全チャンク（親子なら親を優先）からトリプルを抽出してグラフ構築する。"""
    # 抽出LLM（llama.cpp instance）が停止中ならオンデマンド起動する
    from app.models_mgmt import llama

    await llama.ensure_ready_by_base_url(base_url)
    if not rag.collection_exists(collection):
        raise rag.RagError(f"コレクションが存在しません: {collection}")
    conn = rag._db(collection)
    try:
        _ensure_tables(conn)
        # 親があれば親、なければチャンクを対象に重複排除
        rows = conn.execute("SELECT id, text, parent FROM chunks").fetchall()
        seen: set[str] = set()
        units: list[tuple[int, str]] = []
        for cid, text, parent in rows:
            unit = parent or text
            if unit in seen:
                continue
            seen.add(unit)
            units.append((cid, unit))
        units = units[:max_chunks]
        conn.execute("DELETE FROM triples")
        total = 0
        for index, (cid, unit) in enumerate(units):
            if on_progress is not None:
                on_progress(index, len(units))
            triples = await _extract(unit, base_url, model, api_key)
            for t in triples:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO triples (subject, predicate, object, chunk_id) VALUES (?, ?, ?, ?)",
                        (t["s"], t["p"], t["o"], cid),
                    )
                    total += 1
                except sqlite3.Error:
                    pass
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
        entities = conn.execute(
            "SELECT COUNT(*) FROM (SELECT subject FROM triples UNION SELECT object FROM triples)"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"collection": collection, "triples": count, "entities": entities, "processed_units": len(units)}


def graph_stats(collection: str, sample: int = 30) -> dict:
    conn = rag._db(collection)
    try:
        _ensure_tables(conn)
        count = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
        entities = conn.execute(
            "SELECT COUNT(*) FROM (SELECT subject FROM triples UNION SELECT object FROM triples)"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT subject, predicate, object FROM triples LIMIT ?", (sample,)
        ).fetchall()
    finally:
        conn.close()
    return {
        "triples": count,
        "entities": entities,
        "sample": [{"s": s, "p": p, "o": o} for s, p, o in rows],
    }


def _terms(question: str) -> list[str]:
    return [t for t in re.findall(r"[\w一-龠ぁ-んァ-ヶー]+", question) if len(t) >= 2][:8]


def graph_facts(collection: str, question: str, hops: int = 1, limit: int = 30) -> list[dict]:
    """クエリ語に一致するエンティティを起点に近傍トリプルを収集する。"""
    conn = rag._db(collection)
    try:
        _ensure_tables(conn)
        terms = _terms(question)
        if not terms:
            return []
        like = " OR ".join(["subject LIKE ? OR object LIKE ?"] * len(terms))
        params: list[str] = []
        for t in terms:
            params += [f"%{t}%", f"%{t}%"]
        seeds = conn.execute(
            f"SELECT subject, predicate, object FROM triples WHERE {like} LIMIT ?",
            (*params, limit),
        ).fetchall()
        facts = [{"s": s, "p": p, "o": o} for s, p, o in seeds]
        # 1ホップ拡張: 起点エンティティに接続する他のトリプル
        entities = set()
        for s, _, o in seeds:
            entities.add(s)
            entities.add(o)
        if hops >= 1 and entities and len(facts) < limit:
            ents = list(entities)[:20]
            ql = " OR ".join(["subject = ? OR object = ?"] * len(ents))
            p2: list[str] = []
            for e in ents:
                p2 += [e, e]
            more = conn.execute(
                f"SELECT subject, predicate, object FROM triples WHERE {ql} LIMIT ?",
                (*p2, limit - len(facts)),
            ).fetchall()
            for s, p, o in more:
                fact = {"s": s, "p": p, "o": o}
                if fact not in facts:
                    facts.append(fact)
    finally:
        conn.close()
    return facts[:limit]


async def graph_search(collection: str, question: str, top_k: int, api_key: str = "") -> dict:
    """ベクトル検索の文脈にグラフの関連事実を加えて返す。"""
    base = await rag.search(collection, question, top_k, api_key=api_key, mode_override="hybrid")
    facts = graph_facts(collection, question)
    fact_lines = [f"- {f['s']} —{f['p']}→ {f['o']}" for f in facts]
    graph_context = ("関連する事実:\n" + "\n".join(fact_lines)) if fact_lines else ""
    combined = (graph_context + "\n\n---\n\n" + base["context"]) if graph_context else base["context"]
    return {
        "matches": base["matches"],
        "facts": facts,
        "context": combined,
        "count": base["count"],
        "mode": "graph",
    }
