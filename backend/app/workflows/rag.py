"""軽量 RAG ストア。

埋め込みは OpenAI 互換 /v1/embeddings（Ollama / vLLM / OpenAI 等）から取得し、
コレクションごとに SQLite（data_dir/rag/{collection}.db）へ保存する。
検索は numpy によるコサイン類似度（依存を最小化するためベクトル DB は使わない）。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import httpx
import numpy as np

from app.config import data_dir

COLLECTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _rag_dir() -> Path:
    d = data_dir() / "rag"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db(collection: str) -> sqlite3.Connection:
    if not COLLECTION_RE.match(collection):
        raise ValueError(f"不正なコレクション名: {collection}")
    conn = sqlite3.connect(_rag_dir() / f"{collection}.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, text TEXT, embedding BLOB, dim INTEGER, source TEXT)"
    )
    return conn


async def embed(texts: list[str], base_url: str, model: str, api_key: str) -> list[np.ndarray]:
    url = base_url.rstrip("/") + "/embeddings"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            url,
            json={"model": model, "input": texts},
            headers={"Authorization": f"Bearer {api_key or 'sk-no-key'}"},
        )
    if r.status_code >= 400:
        raise RuntimeError(f"埋め込み API エラー {r.status_code}: {r.text[:200]}")
    data = r.json()
    return [np.array(item["embedding"], dtype=np.float32) for item in data["data"]]


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks


async def build(collection: str, text: str, source: str, base_url: str, model: str, api_key: str, reset: bool) -> dict:
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("入力テキストが空です")
    if len(chunks) > 500:
        chunks = chunks[:500]
    embeddings = await embed(chunks, base_url, model, api_key)
    conn = _db(collection)
    try:
        if reset:
            conn.execute("DELETE FROM chunks")
        for chunk, emb in zip(chunks, embeddings):
            conn.execute(
                "INSERT INTO chunks (text, embedding, dim, source) VALUES (?, ?, ?, ?)",
                (chunk, emb.tobytes(), len(emb), source),
            )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    return {"collection": collection, "added_chunks": len(chunks), "total_chunks": total}


async def query(collection: str, question: str, top_k: int, base_url: str, model: str, api_key: str) -> dict:
    q_emb = (await embed([question], base_url, model, api_key))[0]
    conn = _db(collection)
    try:
        rows = conn.execute("SELECT text, embedding, dim, source FROM chunks").fetchall()
    finally:
        conn.close()
    if not rows:
        return {"matches": [], "context": "", "count": 0}
    q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-8)
    scored = []
    for text, blob, dim, source in rows:
        emb = np.frombuffer(blob, dtype=np.float32)
        if emb.shape[0] != q_emb.shape[0]:
            continue
        sim = float(np.dot(q_norm, emb / (np.linalg.norm(emb) + 1e-8)))
        scored.append((sim, text, source))
    scored.sort(reverse=True, key=lambda x: x[0])
    top = scored[: max(1, min(top_k, 20))]
    matches = [{"score": round(s, 4), "text": t, "source": src} for s, t, src in top]
    context = "\n\n---\n\n".join(m["text"] for m in matches)
    return {"matches": matches, "context": context, "count": len(matches)}


def list_collections() -> list[dict]:
    result = []
    for f in _rag_dir().glob("*.db"):
        try:
            conn = sqlite3.connect(f)
            n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            conn.close()
            result.append({"collection": f.stem, "chunks": n})
        except sqlite3.Error:
            continue
    return result
