"""永続チャットの添付（画像=VLM入力 / 文書=会話別RAGコレクション登録）。

chat_persist.py から移しただけ（振る舞いは変えない）。chat_persist が `include_router` で取り込み、
元の名前は chat_persist からも使える（再 export）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Conversation, User
from app.security.deps import require_permission

logger = logging.getLogger("control_deck.chat_persist")

router = APIRouter()   # prefix・tags は取り込む側（chat_persist の router）が付ける


def _register_chat_document(*args, **kwargs):
    """chat_persist の名前で呼ぶ（テストが chat_persist.register_chat_document を差し替えても効くように）。"""
    from app.workflows import chat_persist

    return chat_persist.register_chat_document(*args, **kwargs)


ATTACHMENT_IMAGE_TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_ATTACHMENT_ID_RE = re.compile(r"^[0-9a-f]{16}\.(png|jpg|webp|gif)$")
# 圧縮ファイル（OpenCode のプロジェクトへ展開する。archive_import）
_ARCHIVE_ID_RE = re.compile(r"^[0-9a-f]{16}(\.tar\.gz|\.tar\.bz2|\.tar\.xz|\.tgz|\.tbz2|\.txz|\.tar|\.zip|\.7z|\.rar)$")


def _archive_path(conv_id: str, archive_id: str):
    if not _ARCHIVE_ID_RE.fullmatch(archive_id):
        raise HTTPException(status_code=404, detail="添付が見つかりません")
    return _attachment_root(conv_id) / "archives" / archive_id


def _remove_archive(conv_id: str, archive_id: str) -> None:
    """展開し終えた・取り消した圧縮ファイルを消す（最大 200 MiB を会話が消えるまで残さない）。"""
    path = _archive_path(conv_id, archive_id)
    path.unlink(missing_ok=True)
    path.with_name(archive_id + ".json").unlink(missing_ok=True)


def _sent_archives(conv_id: str, ids: list[str]) -> list[dict]:
    return [{"id": a, "name": _archive_name(conv_id, a)} for a in ids
            if _ARCHIVE_ID_RE.fullmatch(a) and _archive_path(conv_id, a).is_file()]


def _archive_name(conv_id: str, archive_id: str) -> str:
    try:
        return json.loads((_archive_path(conv_id, archive_id).with_name(archive_id + ".json")).read_text())["name"]
    except (OSError, ValueError, KeyError):
        return archive_id


async def _save_archive(conv_id: str, file: UploadFile, name: str) -> dict:
    """圧縮ファイルをディスクへ流し込み（メモリに全部載せない）、中身を確かめる。危なければ消して断る。"""
    from app.workflows import archive_import

    suffix = archive_import.suffix_of(name) or ""
    archive_id = uuid.uuid4().hex[:16] + suffix
    folder = _attachment_dir(conv_id) / "archives"
    folder.mkdir(exist_ok=True)
    target = folder / archive_id

    def write() -> int:
        size = 0
        with target.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > archive_import.MAX_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail=f"圧縮ファイルは {archive_import.MAX_ARCHIVE_BYTES // 2**20} MiB 以内にしてください")
                out.write(chunk)
        return size

    try:
        size = await asyncio.to_thread(write)
        if size == 0:
            raise HTTPException(status_code=422, detail="ファイルが空です")
        info = await asyncio.to_thread(archive_import.inspect, target, name)
    except archive_import.ArchiveError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    await asyncio.to_thread(target.with_name(archive_id + ".json").write_text, json.dumps({"name": name}, ensure_ascii=False))
    return {"kind": "archive", "id": archive_id, "name": name, "files": info["files"], "bytes": info["bytes"]}


def _knowledge_chars(budget) -> int:
    """ナレッジの文書に使える文字数。全文を入れられるよう Deep Research の徹底と同じ量（窓から決まる）。"""
    return int(getattr(budget, "exhaustive_evidence_chars", 0) or getattr(budget, "rag_chars", 8000))


def conversation_collection(conv_id: str) -> str:
    """会話別RAGコレクション名。添付文書・検索資料・レポートをここへ蓄積する。"""
    return f"chat-{conv_id}"


def _attachment_root(conv_id: str):
    """会話の添付の置き場所。chat-uploads の直下だけを許す（会話 ID で外へ出ない）。"""
    from app.config import data_dir

    base = (data_dir() / "chat-uploads").resolve()
    root = (base / conv_id).resolve()
    if root.parent != base:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return root


def _attachment_dir(conv_id: str):
    root = _attachment_root(conv_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _remove_attachments(conv_id: str) -> None:
    """会話を消したら、添付した画像も消す（残すと会話が無いのにディスクに残り続ける）。"""
    import shutil

    try:
        root = _attachment_root(conv_id)
    except HTTPException:
        return
    if root.exists():
        try:
            shutil.rmtree(root)
        except OSError as exc:
            logger.warning("会話 %s の添付を消せませんでした: %s", conv_id, exc)


def _collection_embed_override() -> dict:
    """埋め込みは role=embedding instance（BGE-M3等）があればそれを既定にする。"""
    from app.models_mgmt import llama

    instance = llama.find_role_instance("embedding")
    if instance is None:
        return {}
    return {"embed_base_url": str(instance["base_url"]), "embed_model": str(instance["alias"])}


def _extract_document_text(filename: str, content_type: str, data: bytes) -> str:
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        from app.workflows import pdf_import

        return pdf_import.extract(data)[0]
    return data.decode("utf-8", errors="replace")


def _documents_dir(conv_id: str):
    """添付した PDF の原本・文字・ページ画像の置き場所（会話を消すと一緒に消える）。"""
    return _attachment_root(conv_id) / "documents"


def _document_brief(conv_id: str, limit: int = 1500) -> str:
    from app.workflows import pdf_import

    try:
        return pdf_import.brief(_documents_dir(conv_id), limit)
    except OSError:
        return ""


def _install_documents(conv_id: str, project: Path, always: bool) -> tuple[str, list[dict]]:
    """添付した PDF を OpenCode のプロジェクトへ置き、依頼の前に付ける説明を返す。

    説明は、新しく置いたときと新しいセッションのときだけ付ける（同じセッションは覚えている）。
    1 つ失敗しても他と依頼は止めない。
    """
    from app.workflows import pdf_import

    folder = _documents_dir(conv_id)
    placed: list[dict] = []
    for item in pdf_import.saved(folder):
        try:
            placed.append(pdf_import.install(folder, item, project))
        except OSError as exc:
            logger.warning("PDF をプロジェクトへ置けませんでした: %s", type(exc).__name__)
    if not placed or not (always or any(item["new"] for item in placed)):
        return "", placed
    return pdf_import.prompt_note(placed), placed


async def register_chat_document(conv_id: str, text: str, source: str, *, basis: str = "source_fact") -> dict | None:
    """会話コレクションへ文書を登録する（呼び出し側は失敗を致命にしない）。

    コレクション名はID固定（chat-<id>）だが、表示用descriptionへ会話タイトルを
    反映し、Knowledge画面でチャット名として識別できるようにする。
    """
    from app.workflows import rag

    cleaned = (text or "").strip()
    if len(cleaned) < 80:
        return None

    def _conversation_title() -> str:
        db = SessionLocal()
        try:
            conv = db.get(Conversation, conv_id)
            return str(conv.title) if conv is not None else ""
        finally:
            db.close()

    title = await asyncio.to_thread(_conversation_title)
    description = f"会話「{title}」の資料（自動登録）" if title else "AIチャットの会話資料（自動登録）"
    return await rag.add_document(
        conversation_collection(conv_id), cleaned[:800_000], source,
        config_override={"description": description, **_collection_embed_override()}, basis=basis,
    )


async def _save_pdf(conv_id: str, name: str, data: bytes) -> dict:
    """PDF は原本ごと残す。画像だけの PDF や埋め込みモデルが無いときも、OpenCode はページを画像で読める。"""
    from app.workflows import pdf_import, rag

    import httpx

    try:
        text, pages = await asyncio.to_thread(pdf_import.extract, data)
    except Exception as exc:  # pypdf等の解析失敗
        raise HTTPException(status_code=422, detail=f"文書を読み取れません: {exc}") from exc
    doc_id = await asyncio.to_thread(pdf_import.save, _attachment_dir(conv_id) / "documents", name, data, text, pages)
    chunks = 0
    note = ""
    try:
        result = await _register_chat_document(conv_id, text, name)
        chunks = int((result or {}).get("added_chunks") or 0)
        if result is None:
            note = f"「{name}」は文字がほとんど無いので、OpenCode がページを画像として読みます"
    except (rag.RagError, httpx.HTTPError, OSError):
        note = f"「{name}」を保存しました。検索用の登録はできませんでしたが、OpenCode は読めます"
    return {"kind": "document", "id": doc_id, "name": name, "collection": conversation_collection(conv_id),
            "chunks": chunks, "pages": pages, "note": note}


def _remove_document(conv_id: str, doc_id: str) -> None:
    """送る前に取り消した PDF を、保存した分も検索用の登録も消す。"""
    from app.workflows import pdf_import, rag

    name = pdf_import.remove(_documents_dir(conv_id), doc_id)
    collection = conversation_collection(conv_id)
    if not name or not rag.collection_exists(collection):
        return
    for item in rag.list_documents(collection):
        if item["source"] == name:
            rag.delete_document(collection, item["id"])


@router.post("/conversations/{conv_id}/attachments", status_code=201)
async def upload_attachment(
    conv_id: str,
    file: UploadFile = File(...),
    user: User = Depends(require_permission("workflows.run")),
    db: Session = Depends(get_db),
):
    """📎添付: 画像は保存してVLM入力に、コード/文書/PDFは会話コレクションへRAG登録する。"""
    conv = db.get(Conversation, conv_id)
    if conv is None or (conv.owner_user_id is not None and conv.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    from app.workflows import archive_import

    if archive_import.suffix_of(file.filename or ""):
        # 圧縮ファイル: OpenCode に送るときにプロジェクトへ展開する（2026-11-18 利用者の依頼）
        return await _save_archive(conv_id, file, file.filename or "archive.zip")
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=422, detail="ファイルが空です")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="添付は20MiB以内にしてください")
    content_type = (file.content_type or "").lower()
    name = file.filename or "attachment"
    if content_type in ATTACHMENT_IMAGE_TYPES:
        attachment_id = uuid.uuid4().hex[:16] + ATTACHMENT_IMAGE_TYPES[content_type]
        target = _attachment_dir(conv_id) / attachment_id
        await asyncio.to_thread(target.write_bytes, data)  # event loop を止めない
        return {"kind": "image", "id": attachment_id, "name": name}
    if content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="画像は PNG・JPEG・WebP・GIF を添付してください")
    from app.workflows import rag

    import httpx

    if content_type == "application/pdf" or name.lower().endswith(".pdf"):
        return await _save_pdf(conv_id, name, data)  # 原本を残す
    try:
        # PDF の読み取りは重いことがあるので event loop の外で行う。
        text = await asyncio.to_thread(_extract_document_text, name, content_type, data)
    except Exception as exc:  # pypdf等の解析失敗
        raise HTTPException(status_code=422, detail=f"文書を読み取れません: {exc}") from exc
    try:
        result = await _register_chat_document(conv_id, text, name)
    except rag.RagError as exc:
        raise HTTPException(status_code=422, detail=f"RAG登録に失敗: {exc}") from exc
    except (httpx.HTTPError, OSError) as exc:
        # 読めたが、資料の登録先（埋め込みモデル）に届かない。文書のせいにしない。
        raise HTTPException(
            status_code=503,
            detail="資料の登録先（埋め込みモデル）に接続できませんでした。Models で埋め込みモデルが動いているか確認してください",
        ) from exc
    if result is None:
        raise HTTPException(status_code=422, detail="テキストを抽出できませんでした（80文字未満）")
    return {"kind": "document", "id": name, "name": name,
            "collection": conversation_collection(conv_id),
            "chunks": result.get("added_chunks")}


@router.delete("/conversations/{conv_id}/attachments/{attachment_id}", status_code=204)
async def delete_archive_attachment(
    conv_id: str, attachment_id: str,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """送る前に取り消した圧縮ファイル・PDF を消す（画面の ×）。"""
    conv = db.get(Conversation, conv_id)
    if conv is None or (conv.owner_user_id is not None and conv.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    if re.fullmatch(r"[0-9a-f]{16}", attachment_id):
        await asyncio.to_thread(_remove_document, conv_id, attachment_id)
        return
    await asyncio.to_thread(_remove_archive, conv_id, attachment_id)


@router.get("/conversations/{conv_id}/attachments/{attachment_id}")
def get_attachment(
    conv_id: str, attachment_id: str,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """送った画像を返す（会話の発言に出す）。本人の会話の、形の正しい ID だけ。"""
    if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
        raise HTTPException(status_code=404, detail="添付が見つかりません")
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:  # 発言の一覧と同じ厳しさ
        raise HTTPException(status_code=404, detail="添付が見つかりません")
    path = _attachment_root(conv_id) / attachment_id
    if not path.is_file():
        raise HTTPException(status_code=404, detail="添付が見つかりません")
    media = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}[path.suffix]
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=86400",
                                                         "X-Content-Type-Options": "nosniff"})
