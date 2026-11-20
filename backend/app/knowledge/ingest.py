"""文書の取り込み（PDF・テキスト）。分割・埋め込み・原本と図の保存・図表の説明を 1 か所にまとめる。

- 一般・特許はページ（テキストは段落）単位、規格は条項単位（「4.2.1 見出し」「附属書 A」）で分割する。
- 埋め込みは ai_use が local_only / allowed のときだけ（規格の利用判定。埋め込みも AI 処理に数える）。
  埋め込みが使えない（モデル停止など）ときは全文検索だけで続け、結果に書く。
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path

from app.config import data_dir

from app.knowledge import pdf_ingest, store

logger = logging.getLogger("control_deck.knowledge.ingest")

CLAUSE_HEAD = re.compile(
    r"^[ \t]*((?:\d{1,2}(?:\.\d{1,2}){0,4})|(?:附属書|Annex)\s*[A-ZＡ-Ｚ](?:\.\d{1,2})*)[ \t　]+(\S[^\n]{0,70})$", re.M)
MAX_CLAUSE_CHARS = 2400
PAGE_MARK = re.compile(r"〔p\.(\d+)〕\n")


def clause_chunks(text: str) -> list[store.NewChunk]:
    """規格の本文を条項見出しで分ける。長い条項は段落で分け、同じ条項番号を持たせる。"""
    marks = [(m.start(), int(m.group(1))) for m in PAGE_MARK.finditer(text)]
    starts = [m[0] for m in marks]

    def page_at(offset: int) -> int | None:
        i = bisect.bisect_right(starts, offset) - 1
        return marks[i][1] if i >= 0 else None

    heads = [m for m in CLAUSE_HEAD.finditer(text) if not re.fullmatch(r"(19|20)\d{2}", m.group(1))]
    pieces: list[tuple[int, str, str, str]] = []
    if not heads:
        return paragraph_chunks(text)
    if heads[0].start() > 0 and text[: heads[0].start()].strip():
        pieces.append((0, "", "前付け", text[: heads[0].start()]))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        pieces.append((m.start(), m.group(1).replace("Annex", "附属書"), m.group(2).strip(), text[m.start():end]))
    out = []
    for offset, clause, heading, body in pieces:
        body = PAGE_MARK.sub("", body).strip()
        if not body:
            continue
        locator = {"clause": clause, "heading": heading, "page": page_at(offset)}
        parts = [body] if len(body) <= MAX_CLAUSE_CHARS else _split_paragraphs(body, MAX_CLAUSE_CHARS)
        out += [store.NewChunk(text=p if j == 0 else f"{clause} {heading}（続き）\n{p}", locator=dict(locator))
                for j, p in enumerate(parts)]
    return out


def _split_paragraphs(text: str, size: int) -> list[str]:
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(buf) + len(para) > size and buf:
            out.append(buf.strip())
            buf = ""
        while len(para) > size:
            out.append(para[:size])
            para = para[size - 200:]
        buf += para + "\n\n"
    if buf.strip():
        out.append(buf.strip())
    return out


def paragraph_chunks(text: str, size: int = 1200) -> list[store.NewChunk]:
    """ページの印（〔p.N〕）があればページを locator に持たせて段落で分ける。"""
    out = []
    segments = PAGE_MARK.split(text)
    if len(segments) == 1:
        return [store.NewChunk(text=p) for p in _split_paragraphs(text, size) if p.strip()]
    # split の結果: [前, page, 本文, page, 本文, ...]
    for i in range(1, len(segments), 2):
        page = int(segments[i])
        for part in _split_paragraphs(segments[i + 1], size):
            if part.strip():
                out.append(store.NewChunk(text=part, locator={"page": page}))
    return out


async def _embed(chunks: list[store.NewChunk], collection: str) -> tuple[str, bool]:
    from app.workflows import rag

    cfg = await asyncio.to_thread(rag.get_config, collection)
    try:
        vectors = await rag._embed_batched([c.text for c in chunks], cfg, "")
    except Exception as exc:  # noqa: BLE001 - 埋め込みが止まっていても全文検索で使える
        logger.warning("埋め込みを使えないため全文検索だけで登録します: %s", exc)
        return cfg["embed_model"], False
    for chunk, vec in zip(chunks, vectors):
        chunk.vector = vec
    return cfg["embed_model"], True


def _progress(job, message: str, done: int, total: int) -> None:
    if job is not None:
        job.set_progress(message, done, total)


async def ingest_pdf(collection: str, path: Path, *, title: str = "", sensitivity: str | None = None,
                     ai_use: str = "allowed", clauses: bool = False, describe_figures: bool = True,
                     store_fulltext: bool = True, meta: dict | None = None, ext_id: str | None = None,
                     identifiers: list | None = None, job=None, force: bool = False) -> dict:
    _progress(job, "PDF を読んでいます", 0, 5)
    data = await asyncio.to_thread(Path(path).read_bytes)
    extracted = await pdf_ingest.extract(Path(path))
    ref = await asyncio.to_thread(store.require_collection, collection)
    figures: list[tuple[int, bytes]] = []
    described: dict[int, str] = {}
    if store_fulltext:
        _progress(job, "図・表のページを画像にしています", 1, 5)
        for page in extracted.figure_pages:
            png = await pdf_ingest.render_page(Path(path), page)
            if png:
                figures.append((page, png))
        if describe_figures and ai_use in ("local_only", "allowed"):
            for i, (page, png) in enumerate(figures):
                _progress(job, f"図表を読み取っています（{i + 1}/{len(figures)}）", 2, 5)
                text = await pdf_ingest.describe_page(png, job)
                if text:
                    described[page] = text
    body = extracted.text if store_fulltext else ""
    if not title and body:
        # 公報なら「番号 発明の名称」を題名にする（ファイル名より分かりやすい）
        from app.knowledge import biblio

        head = biblio.head_identifiers(body)
        name = biblio.invention_title(body)
        title = " ".join(x for x in ((head[0].raw if head else ""), name) if x)
    if store_fulltext:
        chunks = clause_chunks(body) if clauses else paragraph_chunks(body)
        for page, text in described.items():
            chunks.append(store.NewChunk(text=f"（p.{page} の図表の説明・モデルの推論で未確認）\n{text}",
                                         locator={"page": page, "kind": "figure", "basis": "model_inference"}))
    else:
        chunks = [store.NewChunk(text=title or Path(path).name, locator={"kind": "metadata"})]
    meta = dict(meta or {})
    if store_fulltext and not clauses and ref.kind in ("patent", "generic") and body:
        meta["biblio"] = await _patent_biblio(Path(path), body)
        name = meta["biblio"].get("title") or ""
        if name and name not in title:
            title = f"{title} {name}".strip()
    model, embedded = "", False
    if ai_use in ("local_only", "allowed") and store_fulltext:
        _progress(job, "埋め込みを作っています", 3, 5)
        model, embedded = await _embed(chunks, collection)
    _progress(job, "保存しています", 4, 5)
    info = {"page_count": extracted.page_count, "ocr_pages": [p.number for p in extracted.pages if p.method == "ocr"],
            "figure_pages": [p for p, _ in figures], "figures_described": sorted(described),
            "embedded": embedded, "source_sha256": extracted.sha256, "fulltext_stored": store_fulltext}
    result = await asyncio.to_thread(
        store.add_document, collection, chunks, source=Path(path).name, title=title or Path(path).stem,
        ext_id=ext_id or f"pdf:{extracted.sha256}", sha256=extracted.sha256, body=body or None,
        sensitivity=sensitivity, meta={**meta, "pdf": info}, model=model, ai_use=ai_use,
        identifiers=identifiers, force=force)
    if result.get("unchanged"):
        _progress(job, "同じ PDF が登録済みです", 5, 5)
        return {**result, "pdf": info}
    if store_fulltext:
        await asyncio.to_thread(_save_assets, ref.shard, result["doc_id"], data, extracted, figures, described)
    _progress(job, "完了", 5, 5)
    return {**result, "pdf": info}


async def _patent_biblio(path: Path, body: str) -> dict:
    """公報の書誌。1 ページ目の書誌が画像で文字が無い公報は、1 ページ目だけ OCR して読む（本文は文字のまま）。"""
    from app.knowledge import biblio

    fields = biblio.patent_fields(body)
    fields["source"] = "rule_extract"
    if biblio.has_front_page(body) or not shutil.which("tesseract"):
        return fields
    png = await pdf_ingest.render_page(path, 1, dpi=200)
    if png is None:
        return fields
    tmp_root = data_dir() / "tmp" / "pdf"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
        image = Path(tmp) / "front.png"
        image.write_bytes(png)
        code, out, _ = await pdf_ingest._run("tesseract", str(image), "-", "-l", "jpn+eng", timeout=180)
    if code != 0:
        return fields
    ocr = biblio.patent_fields(out.decode("utf-8", "replace") + "\n" + body)
    merged = {k: (fields[k] or ocr.get(k)) for k in fields if k != "source"}
    merged["source"] = "ocr_extract" if merged != {k: fields[k] for k in merged} else "rule_extract"
    return merged


def _save_assets(shard: str, doc_id: int, data: bytes, extracted, figures, described) -> None:
    assets = [(store.put_blob(shard, data, "application/pdf", role="original"), "original", 0, {})]
    for page, png in figures:
        assets.append((store.put_blob(shard, png, "image/png", role="page"), "page", page, {"page": page}))
    for i, image in enumerate(extracted.images):
        assets.append((store.put_blob(shard, image["png"], "image/png", role="figure"), "figure", i,
                       {"page": image["page"], "source": "pdf_embedded"}))
    conn = store.connect(shard)
    try:
        conn.executemany("INSERT INTO document_assets(doc_id, sha256, role, ord, meta) VALUES(?,?,?,?,?)",
                         [(doc_id, sha, role, ord_, json.dumps(meta)) for sha, role, ord_, meta in assets])
        conn.executemany(
            "INSERT INTO annotations(doc_id, kind, basis, review_status, content, source_revision, created) "
            "VALUES(?,?,?,?,?,?,strftime('%s','now'))",
            [(doc_id, f"figure_description:p{page}", "model_inference", "unreviewed", text, extracted.sha256)
             for page, text in described.items()])
        conn.commit()
    finally:
        conn.close()


_CLASS_LINE = re.compile(r"^\s*([A-HＡ-Ｈ][0-9０-９]{2}[A-ZＡ-Ｚ](?:\s*[0-9０-９]{1,4}\s*/\s*[0-9０-９]{1,6})?|[0-9A-ZＡ-Ｚ][0-9A-Za-z./@-]{1,20})\s*[\t ]\s*(\S.*)$")


async def ingest_classification(collection: str, text: str, *, source: str = "") -> dict:
    """分類コード表（1 行 1 コード「コード<TAB>説明」）を取り込む。行ごとに断片にし、コードで探せるようにする。
    辞書は利用者が各サイトの利用条件に従って手で入手したもの（自動取得しない）。"""
    chunks = []
    for line in text.splitlines():
        m = _CLASS_LINE.match(line)
        if not m:
            continue
        code = re.sub(r"\s+", " ", m.group(1)).strip()
        chunks.append(store.NewChunk(text=f"{code}\t{m.group(2).strip()}", locator={"code": code}))
    if not chunks:
        raise store.KnowledgeError("分類コード表の行がありません（1 行 1 コード「コード<TAB>説明」）")
    model, _ = await _embed(chunks, collection)
    return await asyncio.to_thread(store.add_document, collection, chunks, source=source or "分類コード表",
                                   title=source or "分類コード表", body=text, model=model, identifiers=[])


async def ingest_text(collection: str, text: str, *, title: str = "", source: str = "", clauses: bool = False,
                      sensitivity: str | None = None, ai_use: str = "allowed", store_fulltext: bool = True,
                      meta: dict | None = None, ext_id: str | None = None, identifiers: list | None = None) -> dict:
    if store_fulltext:
        chunks = clause_chunks(text) if clauses else paragraph_chunks(text)
    else:
        chunks = [store.NewChunk(text=title or source, locator={"kind": "metadata"})]
    model = ""
    if ai_use in ("local_only", "allowed") and store_fulltext:
        model, _ = await _embed(chunks, collection)
    return await asyncio.to_thread(
        store.add_document, collection, chunks, source=source or title, title=title, ext_id=ext_id,
        body=text if store_fulltext else None, sensitivity=sensitivity, meta=meta, model=model, ai_use=ai_use,
        identifiers=identifiers)
