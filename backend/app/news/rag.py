"""取った記事を知識基盤（RAG）の分野 news へ貯め、あとの串刺し検索に使えるようにする。

- コレクション news（kind=news・sensitivity=public）は data_dir/knowledge/news.sqlite3。公開情報なので外部の LLM 向けの横断検索にも出てよい。
  ニュースの DB（data_dir/news/news.sqlite3）とは別。こちらは「意味で探す」ためのチャンクと埋め込みを持つ。
- 1 記事 = 1 文書（ext_id は URL。同じ URL は置き換え。本文が同じなら何もしない）。題・出どころ・日付・チャンネル・話題の語・抜粋を入れる。
  設定「本文も取り込む」を入れたときだけ、記事の本文を既存の安全な取得（research_tools.read_page）で読んで足す。
- 埋め込みは既存の知識基盤の埋め込み（ingest._embed。止まっていれば全文検索だけで登録）。多いのでまとめて、取得の後に少しずつ。
- 古い記事をニュースの DB から消すとき、RAG を残すか一緒に消すかは設定（既定は残す＝アーカイブの串刺し検索）。
- 出来事（kind=event。ControlDeck の中の出来事・利用者宛て）は入れない。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from app.knowledge import context, ingest, store

logger = logging.getLogger("control_deck.news.rag")

COLLECTION = "news"
BATCH = 16
FULLTEXT_PER_RUN = 30        # 1 回の取得で本文を読む記事の数（外へ出る取得なので控えめに）
FULLTEXT_TIMEOUT = 40
FULLTEXT_CHARS = 12_000


def ensure_collection() -> store.CollectionRef:
    ref = store.find_collection(COLLECTION)
    if ref is not None:
        return ref
    return store.create_collection(COLLECTION, shard="news", kind="news", sensitivity="public",
                                   description="ニュースの専門ページで取った記事（公開情報）")


def document_text(item: dict, channel_names: list[str], body: str = "") -> str:
    """1 文書の本文（題・出どころ・日付・URL・チャンネル・話題の語・抜粋・あれば本文）。"""
    lines = [item["title"], f"出どころ: {item['source']}", f"日付: {item['published_at']}", f"URL: {item['url']}"]
    if channel_names:
        lines.append("チャンネル: " + "、".join(channel_names))
    if item.get("topics"):
        lines.append("話題: " + "、".join(item["topics"]))
    if item.get("gist"):
        lines.append(f"一言: {item['gist']}")
    head = "\n".join(lines)
    parts = [head] + ([item["summary"]] if item.get("summary") else []) + ([body] if body else [])
    return "\n\n".join(parts)


def _existing_sha(urls: list[str]) -> dict[str, str]:
    ref = store.find_collection(COLLECTION)
    if ref is None or not urls:
        return {}
    conn = store.connect("news")
    try:
        out = {}
        for start in range(0, len(urls), 500):
            part = urls[start:start + 500]
            marks = ",".join("?" * len(part))
            out.update({r[0]: r[1] for r in conn.execute(
                f"SELECT ext_id, sha256 FROM documents WHERE collection_id=? AND ext_id IN ({marks})", (ref.id, *part))})
        return out
    finally:
        conn.close()


async def _read_body(url: str) -> str:
    from app.security.offline import ExternalResearchBlocked
    from app.workflows import research_tools

    try:
        page = await asyncio.wait_for(research_tools.read_page(url, max_chars=FULLTEXT_CHARS), FULLTEXT_TIMEOUT)
    except ExternalResearchBlocked:
        raise
    except Exception as exc:  # noqa: BLE001 - 1 件読めなくても、題と抜粋だけで登録する
        logger.info("記事の本文を読めません: %s %s", url, exc)
        return ""
    return str(page.get("text") or "") if page.get("ok") else ""


async def ingest_items(items: list[dict], channel_names: dict[str, list[str]], *, fulltext: bool = False) -> dict:
    """記事（{title, source, url, published_at, summary, topics, kind, source_key, gist}）を RAG へ入れる。
    {"added": 件数, "unchanged": 件数, "bodies": 本文を足した件数, "embedded": 埋め込めたか}。失敗は呼び出し側が握る。"""
    items = [i for i in items if i.get("kind") != "event"]
    if not items:
        return {"added": 0, "unchanged": 0, "bodies": 0, "embedded": True}
    await asyncio.to_thread(ensure_collection)
    bodies = tried = 0
    prepared: list[tuple[dict, str, bool]] = []
    for item in items:
        body = ""
        if fulltext and tried < FULLTEXT_PER_RUN:
            tried += 1
            try:
                body = await _read_body(item["url"])
            except Exception:  # noqa: BLE001 - 外へ出せない状態（秘密の会話・オフライン）なら、以後の本文は読まない
                fulltext = False
            bodies += 1 if body else 0
        prepared.append((item, document_text(item, channel_names.get(item["url"], []), body), bool(body)))
    have = await asyncio.to_thread(_existing_sha, [i["url"] for i, _, _ in prepared])
    todo = [(i, t, b) for i, t, b in prepared if have.get(i["url"]) != hashlib.sha256(t.encode("utf-8")).hexdigest()]
    added, embedded = 0, True
    for start in range(0, len(todo), BATCH):
        part = todo[start:start + BATCH]
        chunk_lists = [ingest.paragraph_chunks(text) for _, text, _ in part]
        flat = [c for chunks in chunk_lists for c in chunks]
        model, ok = await ingest._embed(flat, COLLECTION)
        embedded = embedded and ok
        for (item, text, has_body), chunks in zip(part, chunk_lists):
            meta = {"kind": item["kind"], "source_key": item["source_key"], "published_at": item["published_at"],
                    "topics": item.get("topics", []), "channels": channel_names.get(item["url"], []), "fulltext": has_body}
            await asyncio.to_thread(
                store.add_document, COLLECTION, chunks, source=item["source"], title=item["title"], url=item["url"], ext_id=item["url"],
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), body=text, sensitivity="public", meta=meta,
                model=model if ok else "", identifiers=[])
            added += 1
        await asyncio.sleep(0)       # 取得の後に少しずつ（ほかの処理に道を譲る）
    return {"added": added, "unchanged": len(prepared) - len(todo), "bodies": bodies, "embedded": embedded}


def delete_urls(urls: list[str]) -> int:
    """古い記事を消すとき（設定が「一緒に消す」のとき）。消した文書の数。"""
    ref = store.find_collection(COLLECTION)
    if ref is None or not urls:
        return 0
    conn = store.connect("news")
    try:
        ids = []
        for start in range(0, len(urls), 500):
            part = urls[start:start + 500]
            marks = ",".join("?" * len(part))
            ids += [r[0] for r in conn.execute(f"SELECT id FROM documents WHERE collection_id=? AND ext_id IN ({marks})", (ref.id, *part))]
    finally:
        conn.close()
    return sum(1 for doc_id in ids if store.delete_document(COLLECTION, int(doc_id)))


def _meta(doc_ids: list[int]) -> dict[int, dict]:
    conn = store.connect("news")
    try:
        marks = ",".join("?" * len(doc_ids))
        out = {}
        for row in conn.execute(f"SELECT id, meta FROM documents WHERE id IN ({marks})", doc_ids):
            try:
                out[int(row[0])] = json.loads(row[1] or "{}")
            except ValueError:
                out[int(row[0])] = {}
        return out
    finally:
        conn.close()


async def search(question: str, limit: int = 24) -> list[dict]:
    """意味で探す（埋め込みが使えれば全文検索との混合、使えなければ全文検索だけ）。
    文書ごとに 1 件: {"url", "title", "source", "text"（当たった断片）, "score", "meta"}。"""
    ref = await asyncio.to_thread(store.find_collection, COLLECTION)
    if ref is None:
        return []
    vector, model = await context.query_vector(question)
    hits = await asyncio.to_thread(store.search, [COLLECTION], question, query_vector=vector, model=model,
                                   mode="hybrid" if vector is not None else "fulltext", limit=limit * 3)
    seen: dict[str, dict] = {}
    for hit in hits:
        seen.setdefault(hit["doc_ref"], hit)
    picked = list(seen.values())[:limit]
    metas = await asyncio.to_thread(_meta, [int(h["doc_ref"].split(":")[1]) for h in picked]) if picked else {}
    return [{"url": h["url"], "title": h["title"], "source": h["source"], "text": h["text"], "score": h["score"],
             "meta": metas.get(int(h["doc_ref"].split(":")[1]), {}), "semantic": vector is not None} for h in picked]
