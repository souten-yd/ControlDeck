"""永続チャット — 会話・メッセージを DB 保存し、生成をサーバー側ジョブで行う。

根本課題（従来 /chat/stream）: WS ハンドラ内で LLM を直接 stream していたため、
ブラウザを閉じる（WS 切断）と生成タスクが中断し、回答はブラウザにしか無く消えていた。

本実装:
- 送信時に user メッセージ + assistant プレースホルダ + chat.completion ジョブを DB に作成。
- ジョブ（サーバー側）が LLM 生成し、assistant メッセージへ部分出力を随時チェックポイント保存。
- WS はイベント通知のみ。切断してもジョブは継続し、再接続で job_id から購読を再開できる。
- 再度開いた際は履歴 API で復元（generating なら現在の部分回答 + WS 再購読）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.audit import service as audit
from app.jobs import service as jobs
from app.models import ChatMessage, ChatQueuedRequest, ChatReference, Conversation, User
from app.models import utcnow
from app.schemas.assistant import AssistantPlan, ResearchStep
from app.security.deps import authenticate_websocket, require_permission
from app.workflows.chat_router import _keep_alive, resolve_think

logger = logging.getLogger("control_deck.chat_persist")

router = APIRouter(prefix="/chat", tags=["chat-persist"])

# 部分出力のチェックポイント間隔（秒）。毎トークン DB 書き込みはしない
CHECKPOINT_SEC = 1.0
REFERENCE_SYSTEM_GUIDANCE = (
    "この会話の検索資料には R1、RA、R10 のような会話内文献IDがあります。"
    "文献を根拠にする場合は [R1] の形式で引用してください。"
    "利用者が文献IDを指定した場合、別の資料と推測で置き換えないでください。"
)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


# モデル最大コンテキストのキャッシュ（Ollama /api/show は毎回呼ばない）
_CTX_CACHE: dict[str, int] = {}


async def _context_max(base_url: str, model: str) -> int | None:
    """生成統計表示用の最大コンテキスト。llama.cppは/slotsのn_ctx（parallel分割後の実値）、
    Ollamaはモデル個別num_ctx → モデル上限の順で解決する。外部endpointはNone。"""
    import httpx

    from app.models_mgmt import llama, ollama

    normalized = base_url.rstrip("/").removesuffix("/v1").rstrip("/")
    try:
        if normalized == ollama.base_url().rstrip("/"):
            num_ctx = ollama.get_model_config(model).get("num_ctx")
            if num_ctx:
                return int(num_ctx)
            key = f"ollama:{model}"
            if key not in _CTX_CACHE:
                shown = await ollama.show(model)
                if shown.get("context_length"):
                    _CTX_CACHE[key] = int(shown["context_length"])
            return _CTX_CACHE.get(key)
        from urllib.parse import urlsplit

        port = urlsplit(base_url).port
        if port in llama.endpoint_ports():
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"http://127.0.0.1:{port}/slots")
            slots = response.json()
            if isinstance(slots, list) and slots:
                return int(slots[0].get("n_ctx") or 0) or None
    except Exception:
        return None
    return None


async def _prompt_tokens_probe(base_url: str) -> int | None:
    """llama.cppの処理中slotから実測プロンプトトークン数を得る（他runtimeはNone）。"""
    import httpx

    from app.models_mgmt import llama

    try:
        from urllib.parse import urlsplit

        port = urlsplit(base_url).port
        if port not in llama.endpoint_ports():
            return None
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"http://127.0.0.1:{port}/slots")
        for slot in response.json():
            if slot.get("is_processing"):
                return int(slot.get("n_prompt_tokens") or 0) or None
    except Exception:
        return None
    return None


# ---- 会話 CRUD ----


class ConversationOut(BaseModel):
    id: str
    title: str
    updated_at: str


def _conv_out(c: Conversation) -> dict:
    return {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat() if c.updated_at else "",
            "archived": c.archived_at is not None}


@router.post("/conversations", status_code=201)
def create_conversation(user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    conv = Conversation(id=_new_id(), owner_user_id=user.id)
    db.add(conv)
    db.commit()
    return _conv_out(conv)


@router.get("/conversations")
def list_conversations(
    archived: bool = False,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """会話の一覧。既定はアーカイブしていないもの、archived=true でアーカイブしたもの。"""
    condition = Conversation.archived_at.is_not(None) if archived else Conversation.archived_at.is_(None)
    rows = db.execute(
        select(Conversation).where(Conversation.owner_user_id == user.id, condition)
        .order_by(Conversation.updated_at.desc()).limit(100)
    ).scalars().all()
    return [_conv_out(c) for c in rows]


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    # True でアーカイブ、False で戻す（一覧の左右スワイプ）
    archived: bool | None = None


@router.patch("/conversations/{conv_id}")
def update_conversation(
    conv_id: str, body: ConversationUpdate, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    if body.title is None and body.archived is None:
        raise HTTPException(status_code=422, detail="変える項目がありません")
    if body.title is not None:
        conv.title = body.title.strip()
        conv.updated_at = utcnow()
        action = "chat.conversation.rename"
    if body.archived is not None:
        # アーカイブは更新時刻を変えない（戻したときに元の並びへ戻る）
        conv.archived_at = utcnow() if body.archived else None
        action = "chat.conversation.archive" if body.archived else "chat.conversation.unarchive"
    db.commit()
    audit.record(db, action, user=user, resource_type="conversation", resource_id=conv_id, request=request)
    return _conv_out(conv)


class ForkBody(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    # True: そのメッセージまでを含める（「ここから分岐」）。False: その手前まで（「編集して送り直す」）
    include: bool = True


def _ordered_messages(db: Session, conv_id: str) -> list[ChatMessage]:
    return list(db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at)
    ).scalars().all())


def _still_generating(m: ChatMessage) -> bool:
    """本当に生成中か。再起動などでジョブが消えた「generating」は生成中とみなさない
    （止める相手がいないのに消せない・分岐できない、を防ぐ）。"""
    if m.status != "generating":
        return False
    job = jobs.get(m.job_id) if m.job_id else None
    return job is not None and job.status in ("queued", "running")


def _copy_attachments(source_conv: str, target_conv: str, ids: list[str]) -> list[str]:
    """発言に付いた画像を新しい会話の置き場へ写す（元の会話を消しても分岐先に残るように）。"""
    import shutil

    copied = []
    source, target = _attachment_root(source_conv), _attachment_dir(target_conv)
    for attachment_id in ids:
        if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
            continue
        path = source / attachment_id
        if path.is_file():
            shutil.copy2(path, target / attachment_id)
            copied.append(attachment_id)
    return copied


@router.post("/conversations/{conv_id}/fork", status_code=201)
def fork_conversation(
    conv_id: str, body: ForkBody, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """会話を分岐する: 指定したメッセージまで（include=False ならその手前まで）を新しい会話へ写す。

    元の会話はそのまま残す（編集で書き直しても、前の続きは元の会話で読める）。
    OpenCode の回答は、写した先で別の OpenCode セッションになる（同じセッションを 2 つの会話で
    取り合わないように opencode_session を外す）。会話の資料（RAG）は写さない。
    """
    source = _owned_conversation(db, conv_id, user)
    messages = _ordered_messages(db, conv_id)
    index = next((i for i, m in enumerate(messages) if m.id == body.message_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="メッセージが見つかりません")
    picked = messages[: index + 1 if body.include else index]
    if any(_still_generating(m) for m in picked):
        raise HTTPException(status_code=409, detail="生成中のメッセージは分岐できません")
    target = Conversation(id=_new_id(), owner_user_id=user.id,
                          title=f"分岐: {source.title}"[:200] if source.title else "分岐した会話")
    db.add(target)
    db.flush()
    new_ids = {m.id: _new_id() for m in picked}
    for m in picked:
        try:
            meta = json.loads(m.meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        if isinstance(meta, dict):
            for key in ("opencode_session", "server_key"):
                meta.pop(key, None)
            if meta.get("attachments"):
                meta["attachments"] = _copy_attachments(conv_id, target.id, list(meta["attachments"]))
            # 分岐先では許可に答えられない（OpenCode のセッションが別になる）
            for item in meta.get("permissions") or []:
                if isinstance(item, dict) and item.get("status") == "pending":
                    item["status"] = "expired"
            # 圧縮の要約は「どこまでを畳んだか」をメッセージ ID で持つ。分岐先の ID に付け替える
            state = meta.get("compaction")
            if isinstance(state, dict) and state.get("covers_until") in new_ids:
                state["covers_until"] = new_ids[state["covers_until"]]
        status = "interrupted" if m.status == "generating" else m.status
        db.add(ChatMessage(
            id=new_ids[m.id], conversation_id=target.id, role=m.role, content=m.content, thinking=m.thinking,
            status=status, model=m.model, error=m.error, created_at=m.created_at,
            meta_json=json.dumps(meta, ensure_ascii=False),
        ))
    # 編集して送り直すときは、書き直す発言の添付画像も分岐先へ写して、送り直しに載せる
    carry: list[str] = []
    if not body.include:
        try:
            edited_meta = json.loads(messages[index].meta_json or "{}")
        except json.JSONDecodeError:
            edited_meta = {}
        if isinstance(edited_meta, dict) and edited_meta.get("attachments"):
            carry = _copy_attachments(conv_id, target.id, list(edited_meta["attachments"]))
    for ref in db.execute(select(ChatReference).where(ChatReference.conversation_id == conv_id)).scalars().all():
        db.add(ChatReference(
            conversation_id=target.id, sequence=ref.sequence, short_id=ref.short_id,
            canonical_key=ref.canonical_key, kind=ref.kind, title=ref.title, url=ref.url,
            provider=ref.provider, excerpt=ref.excerpt, metadata_json=ref.metadata_json,
        ))
    db.commit()
    # 会話の資料（📎 文書・検索資料）も写す。失敗しても分岐は成り立つので、記録だけ残す
    from app.workflows import rag

    documents = False
    try:
        documents = rag.copy_collection(conversation_collection(conv_id), conversation_collection(target.id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("会話 %s の資料を分岐先へ写せませんでした: %s", conv_id, exc)
    audit.record(db, "chat.conversation.fork", user=user, resource_type="conversation", resource_id=target.id,
                 request=request, metadata={"source": conv_id, "messages": len(picked), "include": body.include})
    return {**_conv_out(target), "attachments": carry, "documents_copied": documents,
            "opencode_context_reset": any(
                (json.loads(m.meta_json or "{}") or {}).get("opencode_session") for m in picked if m.meta_json
            )}


@router.delete("/conversations/{conv_id}/messages/{message_id}", status_code=204)
def delete_message(
    conv_id: str, message_id: str, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """メッセージを消す。自分の発言を消したら、その回答（次の自分の発言までの assistant）も消す。"""
    conv = _owned_conversation(db, conv_id, user)
    messages = _ordered_messages(db, conv_id)
    index = next((i for i, m in enumerate(messages) if m.id == message_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="メッセージが見つかりません")
    doomed = [messages[index]]
    if messages[index].role == "user":
        for m in messages[index + 1:]:
            if m.role == "user":
                break
            doomed.append(m)
    if any(_still_generating(m) for m in doomed):
        raise HTTPException(status_code=409, detail="生成中です。止めてから消してください")
    images: list[str] = []
    for m in doomed:
        try:
            meta = json.loads(m.meta_json or "{}") or {}
            images += list(meta.get("attachments") or [])
            # 展開に失敗して残った圧縮ファイルも消す
            images += [a.get("id") for a in meta.get("archives") or [] if isinstance(a, dict)]
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        db.delete(m)
    conv.updated_at = utcnow()
    db.commit()
    # 消した発言の添付画像も消す
    root = _attachment_root(conv_id)
    for attachment_id in images:
        if isinstance(attachment_id, str) and _ATTACHMENT_ID_RE.fullmatch(attachment_id):
            (root / attachment_id).unlink(missing_ok=True)
        elif isinstance(attachment_id, str) and _ARCHIVE_ID_RE.fullmatch(attachment_id):
            _remove_archive(conv_id, attachment_id)
    audit.record(db, "chat.message.delete", user=user, resource_type="conversation", resource_id=conv_id,
                 request=request, metadata={"messages": len(doomed)})


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(
    conv_id: str, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    db.query(ChatReference).filter(ChatReference.conversation_id == conv_id).delete()
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).delete()
    db.query(ChatQueuedRequest).filter(ChatQueuedRequest.conversation_id == conv_id).delete()
    db.delete(conv)
    db.commit()
    _remove_attachments(conv_id)
    audit.record(db, "chat.conversation.delete", user=user, resource_type="conversation",
                 resource_id=conv_id, request=request)


def _msg_out(m: ChatMessage) -> dict:
    try:
        meta = json.loads(m.meta_json or "{}")
    except json.JSONDecodeError:
        meta = {}
    return {
        "id": m.id, "role": m.role, "content": m.content, "thinking": m.thinking,
        "status": m.status, "job_id": m.job_id, "model": m.model, "error": m.error,
        "meta": meta, "created_at": m.created_at.isoformat() if m.created_at else "",
    }


@router.get("/conversations/{conv_id}/messages")
def list_messages(conv_id: str, user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    rows = db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conv_id)
        .order_by(ChatMessage.created_at)
    ).scalars().all()
    return {"conversation": _conv_out(conv), "messages": [_msg_out(m) for m in rows]}


class ReferenceResolveBody(BaseModel):
    reference_ids: list[str] = Field(min_length=1, max_length=12)


def _owned_conversation(db: Session, conv_id: str, user: User) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return conv


@router.get("/conversations/{conv_id}/references")
def list_references(
    conv_id: str, user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """会話内の文献カタログ。本文はpreviewだけにしてレスポンスを小さく保つ。"""
    from app.workflows.reference_registry import reference_out

    _owned_conversation(db, conv_id, user)
    rows = db.execute(select(ChatReference).where(
        ChatReference.conversation_id == conv_id,
    ).order_by(ChatReference.sequence)).scalars().all()
    return {"references": [reference_out(ref) for ref in rows]}


@router.get("/conversations/{conv_id}/references/{reference_id}")
def get_reference(
    conv_id: str, reference_id: str,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """文献IDを1件解決する、エージェントツール向けの最小API。"""
    from app.workflows.reference_registry import reference_out, resolve_references

    _owned_conversation(db, conv_id, user)
    refs = resolve_references(db, conv_id, [reference_id])
    if not refs:
        raise HTTPException(status_code=404, detail="文献が見つかりません")
    return reference_out(refs[0], include_excerpt=True)


@router.post("/conversations/{conv_id}/references/resolve")
def resolve_reference_tool(
    conv_id: str, body: ReferenceResolveBody,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """複数の短い文献IDを一括解決する。provider固有tool callingは要求しない。"""
    from app.workflows.reference_registry import reference_out, resolve_references

    _owned_conversation(db, conv_id, user)
    refs = resolve_references(db, conv_id, body.reference_ids)
    return {"references": [reference_out(ref, include_excerpt=True) for ref in refs]}


# ---- 送信（サーバー側生成ジョブ） ----


class SendBody(BaseModel):
    content: str = Field(min_length=1, max_length=32000)
    mode: str = "chat"  # auto / chat / web / academic / deep / research
    plan: AssistantPlan | None = None
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.2"
    engine: str = "searxng"  # web/deep 用（SearXNG既定）
    searxng_url: str = ""
    deep_depth: str = "deep"
    deep_sources: list[str] = Field(
        default_factory=lambda: ["web", "academic", "github", "direct", "rag"], max_length=8,
    )
    system: str = "あなたは Control Deck の AI アシスタントです。日本語で簡潔に答えてください。"
    thinking: str | None = None  # off / auto / on。省略時はruntime共通設定。
    # 画像添付（/attachments でアップロード済みのID）。VLM有効モデルで画像入力に使う
    attachments: list[str] = Field(default_factory=list, max_length=8)
    # OpenCodeチャット実行（mode=code）用のCodeDEVプロジェクト名
    code_project: str = Field(default="", max_length=64)
    # 任意フォルダ指定（CodeDEV外はサーバー側でコピー取り込み）
    code_project_path: str = Field(default="", max_length=4096)
    # OpenCode に渡す前に、ControlDeck の調査（Web・論文）で調べて結果を付ける（「調べて反映して」）
    code_research: bool = False
    # OpenCode のエージェント: build（作る・直す）／plan（計画だけ。編集もコマンドの変更もしない）
    code_agent: Literal["build", "plan"] = "build"


# ---- 添付（画像=VLM入力 / 文書=会話別RAGコレクション登録） ----

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
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return data.decode("utf-8", errors="replace")


async def register_chat_document(conv_id: str, text: str, source: str) -> dict | None:
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
        config_override={"description": description, **_collection_embed_override()},
    )


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

    try:
        # PDF の読み取りは重いことがあるので event loop の外で行う。
        text = await asyncio.to_thread(_extract_document_text, name, content_type, data)
    except Exception as exc:  # pypdf等の解析失敗
        raise HTTPException(status_code=422, detail=f"文書を読み取れません: {exc}") from exc
    try:
        result = await register_chat_document(conv_id, text, name)
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
    """送る前に取り消した圧縮ファイルを消す（画面の ×）。"""
    conv = db.get(Conversation, conv_id)
    if conv is None or (conv.owner_user_id is not None and conv.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="会話が見つかりません")
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


class PermissionModeBody(BaseModel):
    mode: str = Field(pattern="^(auto|ask)$")


@router.get("/opencode/permission-mode")
def get_permission_mode(user: User = Depends(require_permission("workflows.run"))):
    """AI アシスタントの OpenCode の権限モード（auto: AI が判断し危ない操作だけ確認 / ask: 毎回確認）。"""
    from app.integrations.opencode import provider

    return {"mode": str(provider.get_settings().get("chat_permission_mode") or "auto")}


@router.put("/opencode/permission-mode")
def set_permission_mode(
    body: PermissionModeBody, request: Request,
    user: User = Depends(require_permission("settings.manage")), db: Session = Depends(get_db),
):
    """権限モードを切り替える。次の操作から効く（動いているサーバーも起こし直さない）。

    AI が聞かずにどこまで実行するかを決める設定なので、設定の管理権限を求める。"""
    from app.integrations.opencode import chat_server, provider

    provider.save_workflow({"chat_permission_mode": body.mode})
    applied = chat_server.apply_mode_to_running(body.mode)
    audit.record(db, "opencode.permission_mode", user=user, resource_type="opencode", resource_id="chat",
                 request=request, metadata={"mode": body.mode, "running_servers": applied})
    return {"mode": body.mode}


class OpenCodePermissionBody(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    decision: str = Field(pattern="^(once|always|reject)$")


def _opencode_meta(conv_id: str, message_id: str, user_id: int) -> dict:
    """許可に答えてよい OpenCode の回答か確かめ、その meta を返す（スレッドで呼ぶ。DB は自前で開く）。"""
    with SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.owner_user_id != user_id:
            raise HTTPException(status_code=404, detail="会話が見つかりません")
        message = db.get(ChatMessage, message_id)
        if message is None or message.conversation_id != conv_id or message.role != "assistant":
            raise HTTPException(status_code=404, detail="メッセージが見つかりません")
        raw = message.meta_json or "{}"
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError:
        meta = {}
    if meta.get("mode") != "code" or not meta.get("server_key") or not meta.get("opencode_session"):
        raise HTTPException(status_code=409, detail="OpenCode の回答ではありません")
    return meta


@router.post("/conversations/{conv_id}/opencode/permission", status_code=204)
async def opencode_permission(
    conv_id: str, body: OpenCodePermissionBody, request: Request,
    user: User = Depends(require_permission("workflows.run")),
):
    """OpenCode が求めた許可に答える（今回だけ・常に・拒否）。"""
    from app.integrations.opencode import chat_server

    meta = await asyncio.to_thread(_opencode_meta, conv_id, body.message_id, user.id)
    pending = {str(p.get("id")): p for p in meta.get("permissions") or [] if p.get("status") == "pending"}
    if body.request_id not in pending:
        raise HTTPException(status_code=409, detail="この許可はもう答えられません")
    target_session = str(pending[body.request_id].get("sessionID") or meta["opencode_session"])
    if not target_session.startswith("ses_"):
        raise HTTPException(status_code=409, detail="この許可はもう答えられません")
    server = await asyncio.to_thread(chat_server.load, str(meta["server_key"]))
    if server is None:
        raise HTTPException(status_code=409, detail="OpenCode のサーバーが止まっています")
    try:
        await chat_server.reply_permission(server, target_session, body.request_id, body.decision)
    except chat_server.ChatServerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    def record() -> None:
        with SessionLocal() as db:
            audit.record(db, "opencode.permission", user=user, resource_type="conversation",
                         resource_id=conv_id, request=request,
                         metadata={"decision": body.decision, "request_id": body.request_id})

    await asyncio.to_thread(record)


class RouteBody(BaseModel):
    content: str = Field(min_length=1, max_length=32000)
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.2"


@router.post("/route")
async def route_message(
    body: RouteBody, user: User = Depends(require_permission("workflows.run")),
):
    """明確な依頼はルール、曖昧な依頼はLLMで構造化判定する。"""
    del user
    from app.workflows.assistant_planner import decide

    from app.models_mgmt.runtime_provider import resolve_target

    base_url, model = resolve_target(body.base_url, body.model)
    return (await decide(body.content, base_url, model)).model_dump()


_KANA = re.compile(r"[\u3040-\u30ff]")


def language_guard(history: list[dict], user_query: str) -> list[dict]:
    """日本語の質問には日本語で答えさせる。

    DeepSeek（DS-XS）は、検索資料や長い文脈に中国語・英語が混ざると、最初の system に「日本語で」とあっても
    中国語で答えることがあった（2026-11-17、トランプ大統領の近況の調査）。指示は文脈の最後ほど効くので、
    生成の直前（最後の発言の後ろ）に短い指示を足す。かなを含まない質問（英語など）には足さない。
    """
    if not _KANA.search(user_query or ""):
        return history
    # 途中の system を受け付けないチャットテンプレートがあるので、最後の利用者の発言へ書き添える（送る分だけ。保存はしない）
    note = "\n\n（回答は必ず日本語で書いてください。資料が中国語や英語でも、本文は日本語にすること）"
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return [*history[:index], {**message, "content": message["content"] + note}, *history[index + 1:]]
    return history


def _timeline_of(message) -> list:
    """発言に記録した届いた順の並び（OpenCode の回答だけ）。無ければ空。"""
    try:
        return list((json.loads(message.meta_json or "{}") or {}).get("timeline") or [])
    except (json.JSONDecodeError, TypeError, AttributeError):
        return []


HANDOFF_MODE_LABEL = {"chat": "チャット", "web": "調査（Web）", "academic": "調査（論文）", "research": "調査",
                      "deep": "調査（Deep Research）", "auto": "チャット"}


def handoff_context(conv_id: str, current_user_id: str | None, limit_chars: int) -> tuple[str, int]:
    """OpenCode へ引き継ぐ、前回の OpenCode の回答より後の会話（チャット・調査の結果と出典）。

    2026-11-18 利用者の依頼「チャットで始めた会話の結果を、同じ会話の中で OpenCode に引き継ぎたい」
    「OpenCode で開発している中で調査を入れ、その結果を開発に反映したい」。OpenCode には毎回その発言しか
    渡していなかったので、間に挟んだチャットや調査を OpenCode は知らなかった。
    量は limit_chars まで（context_budget.handoff_chars）。超えたら古いほうから落とす（新しい結論を残す）。
    返り値は (本文, 引き継いだ発言の数)。引き継ぐものが無ければ ("", 0)。
    """
    db = SessionLocal()
    try:
        rows = db.execute(select(ChatMessage).where(ChatMessage.conversation_id == conv_id)
                          .order_by(ChatMessage.created_at)).scalars().all()
    finally:
        db.close()
    start = 0
    for index, row in enumerate(rows):
        if row.role != "assistant":
            continue
        try:
            meta = json.loads(row.meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        # OpenCode のセッションが一度でも受け取った発言までは引き継がない（止めた・失敗した回も、
        # その依頼は同じセッションが見ている。二重に渡さない）
        if meta.get("mode") == "code" and meta.get("opencode_session") and row.status != "generating":
            start = index + 1
    blocks: list[str] = []
    for row in rows[start:]:
        if row.id == current_user_id or row.status in ("generating", "failed", "canceled"):
            continue
        content = (row.content or "").strip()
        if not content:
            continue
        if row.role == "user":
            blocks.append(f"### 利用者\n{content}")
            continue
        try:
            meta = json.loads(row.meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        mode = str(meta.get("mode") or "chat")
        if mode == "code":
            continue
        lines = [f"### アシスタント（{HANDOFF_MODE_LABEL.get(mode, mode)}）", content]
        sources = [x for x in (meta.get("sources") or []) if isinstance(x, dict) and x.get("url")]
        if sources:
            lines.append("出典:")
            lines += [f"- [{x.get('reference_id', '')}] {x.get('title', '')} {x.get('url', '')}".replace("[] ", "")
                      for x in sources[:40]]
        blocks.append("\n".join(lines))
    if not blocks:
        return "", 0
    head = "## これまでの会話（この会話で OpenCode の前に話した内容・調べた結果。依頼の前提として使ってください）\n\n"
    note = "（古い {} 件は量の上限のため省略）\n\n"
    budget = max(200, limit_chars - len(head) - len(note) - 8)   # 見出しと省略の注記も上限に数える
    kept: list[str] = []
    used = 0
    for block in reversed(blocks):
        cost = len(block) + (2 if kept else 0)            # 区切りの空行
        if used + cost > budget:
            if not kept:   # 最新の 1 つだけでも入れる（末尾を残す）
                kept.append("…（前略）\n" + block[-max(100, budget - 10):])
            break
        kept.append(block)
        used += cost
    kept.reverse()
    omitted = len(blocks) - len(kept)
    return head + (note.format(omitted) if omitted else "") + "\n\n".join(kept), len(kept)


async def _run_chat_job(job: jobs.Job, assistant_id: str, conv_id: str,
                        history: list[dict], params: dict) -> dict:
    """サーバー側で検索/生成し、assistant メッセージへ部分出力を保存する（全モード対応）。"""
    base_url = params["base_url"]
    model = params["model"]
    mode = params.get("mode", "chat")
    thinking_mode = str(params.get("thinking") or "")
    max_output_tokens = int(params["max_output_tokens"])
    from app.models_mgmt.runtime_provider import RuntimeChatRequest, provider_for_base_url
    think_spec = resolve_think(base_url, model, thinking_mode or None)
    think = think_spec.ollama_think
    # 元のユーザー発話（検索モードではhistoryが差し替わるため先に控える）
    user_query = str(history[-1].get("content") or "") if history else ""
    provider = provider_for_base_url(base_url)
    request_id = job.id
    buf = {"content": "", "thinking": "", "last_ckpt": 0.0, "meta": {}}

    def checkpoint(final: bool = False, status: str = "generating", error: str = "") -> None:
        db = SessionLocal()
        try:
            m = db.get(ChatMessage, assistant_id)
            if m is None:
                return
            m.content = buf["content"]
            m.thinking = buf["thinking"]
            m.status = status
            if buf["meta"]:
                m.meta_json = json.dumps(buf["meta"], ensure_ascii=False)
            if error:
                m.error = error[:2000]
            db.commit()
        finally:
            db.close()

    async def maybe_ckpt() -> None:
        now = asyncio.get_event_loop().time()
        if now - buf["last_ckpt"] >= CHECKPOINT_SEC:
            buf["last_ckpt"] = now
            await asyncio.to_thread(checkpoint)

    # ---- OpenCode: プロジェクトごとの OpenCode サーバー（opencode serve）へ発言を送り、
    # 本文・思考・道具の実行・許可の問い合わせをこのチャットへそのまま流す ----
    if mode == "code":
        from app.integrations.opencode import chat_server

        query = str(history[-1].get("content") or "") if history else ""

        def _last_code_session() -> str:
            """この会話の直近の OpenCode セッション ID（継続対話用）。"""
            db = SessionLocal()
            try:
                rows = db.execute(
                    select(ChatMessage).where(
                        ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant",
                    ).order_by(ChatMessage.created_at.desc()).limit(20)
                ).scalars().all()
                for row in rows:
                    try:
                        meta = json.loads(row.meta_json or "{}")
                    except json.JSONDecodeError:
                        continue
                    session = str(meta.get("opencode_session") or "")
                    if session:
                        return session
                return ""
            finally:
                db.close()

        tools: list[dict] = []
        permissions: list[dict] = []
        # 届いた順の並び（思考・本文は文字の位置、道具は ID）。画面は思考を道具の前後で分けて出す
        # （2026-11-18 利用者「思考が全部 1 つの窓に入っている。操作の前後で分かれるはず」）
        timeline: list[dict] = []
        buf["meta"] = {"mode": "code", "tools": tools, "permissions": permissions, "timeline": timeline}

        def mark(kind: str, start: int, length: int) -> None:
            if timeline and timeline[-1].get("k") == kind:
                timeline[-1]["to"] = start + length
            else:
                timeline.append({"k": kind, "from": start, "to": start + length})

        async def on_text(delta: str) -> None:
            mark("x", len(buf["content"]), len(delta))
            buf["content"] += delta
            job.log("delta", delta=delta)
            await maybe_ckpt()

        async def on_reasoning(delta: str) -> None:
            mark("t", len(buf["thinking"]), len(delta))
            buf["thinking"] += delta
            job.log("thinking", delta=delta)
            await maybe_ckpt()

        async def on_status(item: dict) -> None:
            _latest_stats[assistant_id] = {"type": "stats", "phase": item["phase"], "detail": item.get("detail", ""),
                                           "tok_per_sec": item.get("tok_per_sec", 0), "gen_tokens": item.get("gen_tokens", 0),
                                           "prompt_tokens": None, "context_max": None}
            job.log("stats", phase=item["phase"], detail=item.get("detail", ""), tok_per_sec=item.get("tok_per_sec", 0),
                    gen_tokens=item.get("gen_tokens", 0), prompt_tokens=None, context_max=None)

        def _previous_todos() -> dict[str, dict]:
            """前の回答のリストの時刻を引き継ぐ（続きのターンで完了時刻が今に書き換わらないように）。"""
            db = SessionLocal()
            try:
                rows = db.execute(select(ChatMessage).where(
                    ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant", ChatMessage.id != assistant_id,
                ).order_by(ChatMessage.created_at.desc()).limit(5)).scalars().all()
                for row in rows:
                    items = json.loads(row.meta_json or "{}").get("todos")
                    if items:
                        return {str(t.get("content")): dict(t) for t in items if isinstance(t, dict)}
            except ValueError:
                pass
            finally:
                db.close()
            return {}

        todo_state: dict[str, dict] = await asyncio.to_thread(_previous_todos)

        async def on_todos(items: list) -> None:
            """アクションリスト（todowrite）。始まった・終わった時刻を覚えて画面へ出す。"""
            now = time.time()
            out = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("content") or item.get("title") or "").strip()
                if not text:
                    continue
                status = str(item.get("status") or "pending")
                row = todo_state.setdefault(text, {"content": text})
                row["status"] = status
                if status == "in_progress":
                    row.setdefault("started_at", now)
                if status == "completed":
                    row.setdefault("started_at", now)
                    row.setdefault("completed_at", now)
                else:
                    row.pop("completed_at", None)     # やり直しになった
                out.append(dict(row))
            buf["meta"]["todos"] = out
            job.log("todos", todos=out)
            await asyncio.to_thread(checkpoint)

        async def on_tool(tool: dict) -> None:
            if tool.get("name") in ("todowrite", "todo_write"):
                # 道具の行には出さない（画面の上のアクションリストに出す）
                todos = (tool.get("input") or {}).get("todos")
                if isinstance(todos, list):
                    await on_todos(todos)
                return
            entry = next((t for t in tools if t["id"] == tool["id"]), None)
            if entry is None:
                tools.append(dict(tool))
                timeline.append({"k": "u", "id": tool["id"]})
            else:
                entry.update(tool)
            job.log("tool", tool=dict(tool))
            await asyncio.to_thread(checkpoint)

        async def on_permission(item: dict) -> None:
            entry = next((p for p in permissions if p["id"] == item["id"]), None)
            if entry is None:
                permissions.append(dict(item))
            else:
                entry.update(item)
            job.log("permission", permission=dict(entry or item))
            await asyncio.to_thread(checkpoint)

        def expire_pending() -> None:
            for item in permissions:
                if item.get("status") == "pending":
                    item["status"] = "expired"
                    job.log("permission", permission=dict(item))

        try:
            project = await chat_server.resolve_project(params.get("code_project", ""),
                                                        params.get("code_project_path", ""))
            from app.integrations.opencode import provider as opencode_provider
            from app.models_mgmt import local_llm

            # LLM（llama.cpp 等）は ondemand の仕組みを通らないので、先に起こしておく
            settings = await asyncio.to_thread(opencode_provider.get_settings)
            await on_status({"phase": "loading", "detail": "モデルを準備しています"})
            await local_llm.ensure_ready_by_base_url(str(settings["base_url"]))
            job.log("progress", phase="opencode", label="OpenCode を準備しています", iteration=0, details={})
            server = await chat_server.ensure(project, job.owner_user_id)
            session_id = await asyncio.to_thread(_last_code_session)
            if not (session_id and await chat_server.session_exists(server, session_id)):
                session_id = await chat_server.create_session(server, query[:60])
            # 添付した圧縮ファイルをプロジェクトへ展開し、どこに何を置いたかを依頼の前に付ける
            archive_notes: list[str] = []
            for archive_id in params.get("archives") or []:
                from app.workflows import archive_import

                archive_name = await asyncio.to_thread(_archive_name, conv_id, archive_id)
                job.log("progress", phase="archive", label=f"「{archive_name}」をプロジェクトへ展開しています",
                        iteration=0, details={})
                try:
                    imported = await asyncio.to_thread(archive_import.extract_into_project,
                                                       _archive_path(conv_id, archive_id), archive_name, project)
                except archive_import.ArchiveError as exc:
                    raise chat_server.ChatServerError(f"「{archive_name}」を展開できません: {exc}") from exc
                archive_notes.append(archive_import.prompt_note(archive_name, imported))
                await asyncio.to_thread(_remove_archive, conv_id, archive_id)   # 展開したら元は要らない
                buf["meta"].setdefault("imports", []).append(
                    {"name": archive_name, "path": imported["relative"], "files": imported["files"]})
            # 前回の OpenCode の回答より後のチャット・調査を、今回の依頼の前に付けて渡す
            from app.models_mgmt import context_budget

            budget = context_budget.for_model(str(settings["base_url"]), str(settings.get("model") or ""))
            # 調べる回は、引き継ぎと調べた結果で上限を半分ずつ使う（合わせて上限を超えない）
            handoff_limit = budget.handoff_chars // 2 if params.get("code_research") else budget.handoff_chars
            handoff, handoff_count = await asyncio.to_thread(
                handoff_context, conv_id, params.get("user_message_id"), handoff_limit)
            # 「調べて反映して」: ControlDeck の調査で調べ、出典つきの根拠を OpenCode に渡す（同じ 1 回の送信で）
            research_note = ""
            if params.get("code_research"):
                # OpenCode のモデルで調べる（別のモデルを起こさない）。量は引き継ぎの上限の半分まで
                from app.models_mgmt.runtime_provider import resolve_target

                # ゲートウェイ宛なら実エンドポイントへ（鍵の要らない直の口で調査の LLM を呼ぶ）
                code_base, code_model = resolve_target(str(settings["base_url"]), str(settings.get("model") or params["model"]))
                research_params = {**params, "base_url": code_base, "model": code_model}
                research_note = await _research_for_code(job, buf, conv_id, query, research_params,
                                                         budget.handoff_chars // 2)
            # 依頼の前に付けるもの: これまでの会話 → 調べた結果 → 展開した圧縮ファイル → 今回の依頼
            preface = ([handoff] if handoff else []) + ([research_note] if research_note else []) + archive_notes
            if preface:
                query = "\n\n".join(preface) + f"\n\n## 今回の依頼\n{query}"
            if handoff:
                buf["meta"]["handoff"] = {"messages": handoff_count, "chars": len(handoff)}
                job.log("progress", phase="handoff", label=f"この会話の {handoff_count} 件を OpenCode に引き継ぎます",
                        iteration=0, details={"chars": len(handoff)})
            buf["meta"].update({"opencode_session": session_id, "project_path": str(project),
                                "server_key": server.key, "agent": params.get("code_agent", "build"),
                                "permission_mode": await asyncio.to_thread(chat_server.get_mode, server)})
            await asyncio.to_thread(checkpoint)
            result = await chat_server.run_turn(server, session_id, query, on_text=on_text,
                                                on_reasoning=on_reasoning, on_tool=on_tool,
                                                on_permission=on_permission,
                                                agent=params.get("code_agent", "build"), on_status=on_status)
        except asyncio.CancelledError:
            expire_pending()
            await asyncio.to_thread(checkpoint, True, "canceled", "キャンセルされました")
            raise
        except chat_server.ChatServerError as e:
            expire_pending()
            await asyncio.to_thread(checkpoint, True, "failed", str(e))
            raise
        except Exception as e:  # noqa: BLE001 - 想定外でも「生成中」のまま残さない
            logger.exception("OpenCode の発言の処理に失敗しました")
            expire_pending()
            await asyncio.to_thread(checkpoint, True, "failed", f"OpenCode の処理に失敗しました（{type(e).__name__}）")
            raise
        # 答えを待っていた許可は、終わった時点で無効（OpenCode 側でも取り消される）
        expire_pending()
        if result["status"] == "interrupted":
            if any(item.get("status") == "reject" for item in permissions):
                buf["content"] += ("\n\n" if buf["content"] else "") + "（許可されなかったため、ここで止めました）"
            await asyncio.to_thread(checkpoint, True, "canceled", "中断しました")
            return {"assistant_message_id": assistant_id, "opencode_session": buf["meta"]["opencode_session"]}
        if result["status"] == "failed":
            await asyncio.to_thread(checkpoint, True, "failed", result["error"])
            raise chat_server.ChatServerError(result["error"])
        if not buf["content"].strip():
            buf["content"] = "（OpenCode からの本文はありませんでした。上の道具の結果を確認してください）"
        await asyncio.to_thread(checkpoint, True, "completed")
        await asyncio.to_thread(_maybe_title, conv_id)
        return {"assistant_message_id": assistant_id, "opencode_session": buf["meta"]["opencode_session"]}

    # APIからautoが直接送られた場合もサーバー側で判定する。UIが先にroute APIで判定した
    # 場合は、検証済みplanを再利用して余分なLLM呼び出しを避ける。
    plan_data = params.get("plan")
    plan = AssistantPlan.model_validate(plan_data) if plan_data else None
    if mode == "auto" and plan is None:
        from app.workflows.assistant_planner import decide

        plan = await decide(history[-1]["content"], base_url, model)
        mode = plan.mode
    elif mode == "auto" and plan is not None:
        mode = plan.mode
    if plan is not None:
        buf["meta"] = {"mode": mode, "plan": plan.model_dump(), "progress": []}
        job.log("plan", plan=plan.model_dump())

    # ---- 検索モード: サーバー側で検索し履歴を組み立てる ----
    if mode in ("web", "academic", "deep", "research"):
        try:
            query = history[-1]["content"]
            if mode == "research":
                search_history = await _server_research(job, buf, conv_id, query, params, plan)
            else:
                search_history = await _server_search(job, buf, conv_id, mode, query, params, checkpoint)
            if search_history is None:  # deep はここで完結（レポートを content に保存済み）
                # レポートを会話コレクションへ登録し、以後の会話・別質問で再利用できるようにする
                try:
                    await register_chat_document(conv_id, buf["content"], f"{mode}: {query[:60]}")
                except Exception:
                    pass  # 埋め込み未導入等では登録スキップ（本文は保存済み）
                await asyncio.to_thread(checkpoint, True, "completed")
                await asyncio.to_thread(_maybe_title, conv_id)
                return {"assistant_message_id": assistant_id}
            # 検索資料（システム文脈の抜粋）も会話コレクションへ蓄積する
            try:
                await register_chat_document(
                    conv_id, str(search_history[0].get("content") or ""), f"{mode}検索: {query[:60]}",
                )
            except Exception:
                pass
            history = search_history
        except asyncio.CancelledError:
            await asyncio.to_thread(checkpoint, True, "canceled", "キャンセルされました")
            raise
        except Exception as e:
            await asyncio.to_thread(checkpoint, True, "failed", f"{type(e).__name__}: {e}")
            raise

    # 会話コレクション（📎添付・過去の検索資料・レポート）があれば、関連抜粋を文脈に加える
    if mode == "chat":
        try:
            from app.workflows import rag

            collection = conversation_collection(conv_id)
            question = next(
                (str(m.get("content")) for m in reversed(history)
                 if m.get("role") == "user" and isinstance(m.get("content"), str)), "",
            )
            if question and rag.collection_exists(collection):
                from app.models_mgmt import context_budget

                budget = context_budget.for_model(base_url, model)
                found = await rag.search(collection=collection, question=question, top_k=budget.rag_top_k)
                if found.get("context"):
                    history = [{"role": "system", "content":
                                "この会話に添付・収集された資料の関連抜粋です。回答の根拠に使ってください:\n\n"
                                + str(found["context"])[:budget.rag_chars]}, *history]
        except Exception:
            pass  # 資料検索の失敗は通常チャットへフォールバック

    # 画像添付（VLM）: 最後のuser発話をOpenAI互換のcontent配列（text+image_url）へ変換
    attachment_ids = [str(a) for a in (params.get("attachments") or []) if _ATTACHMENT_ID_RE.fullmatch(str(a))]
    if attachment_ids and history and history[-1].get("role") == "user":
        import base64

        mime_by_ext = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
        parts: list[dict] = [{"type": "text", "text": str(history[-1].get("content") or "")}]
        for attachment_id in attachment_ids:
            path = _attachment_dir(conv_id) / attachment_id
            if not path.is_file():
                continue
            encoded = base64.b64encode(path.read_bytes()).decode()
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{mime_by_ext[path.suffix]};base64,{encoded}"}})
        if len(parts) > 1:
            history[-1] = {**history[-1], "content": parts}

    # 窓に応じた閾値を超えるなら、古いやり取りを同じモデルで要約して畳む（Models の共通設定）。
    from app.workflows import chat_compaction

    if mode != "code":
        history, compacted = await chat_compaction.compact(
            history, base_url=base_url, model=model, output_reserve=max_output_tokens, provider=provider)
        if compacted is not None:
            buf["meta"]["compaction"] = compacted
            job.log("compaction", tokens_before=compacted["tokens_before"],
                    tokens_after=compacted["tokens_after"], threshold=compacted["threshold"],
                    folded_messages=compacted["folded_messages"])
            await asyncio.to_thread(checkpoint)
    history = chat_compaction.strip_private(history)
    history = language_guard(history, user_query)

    try:
        runtime_request = RuntimeChatRequest(
            client="assistant",
            base_url=base_url, model=model, messages=history,
            # 会話はモデル側の推奨サンプリングに任せる（一律 0.4 だと思考モデルの推奨 0.6〜1.0 から外れる）。
            temperature=None,
            max_tokens=max_output_tokens, thinking=think, reasoning_effort=think_spec.reasoning_effort,
            disable_thinking=think is False, keep_alive=_keep_alive(),
        )
        # 生成統計（フェーズ / tok/s / コンテキスト使用量）。1チャンク≒1トークンとして
        # 直近4秒窓で速度を算出し、usage chunk到着時に実測値へ置き換える。
        stats = {"phase": "", "gen_tokens": 0, "prompt_tokens": None,
                 "context_max": await _context_max(base_url, model),
                 "start": 0.0, "last_emit": 0.0, "recent": []}
        prompt_probe: asyncio.Task | None = None

        def emit_stats(final: bool = False) -> None:
            now = asyncio.get_event_loop().time()
            window = [t for t in stats["recent"] if now - t <= 4.0]
            elapsed = max(now - stats["start"], 0.25) if stats["start"] else 0.25
            rate = (stats["gen_tokens"] / elapsed) if final else len(window) / min(4.0, elapsed)
            job.log("stats", phase="done" if final else stats["phase"],
                    tok_per_sec=round(rate, 1), gen_tokens=stats["gen_tokens"],
                    prompt_tokens=stats["prompt_tokens"], context_max=stats["context_max"])

        job.log("stats", phase="prefill", detail="モデルの準備とプロンプトの読み込み", tok_per_sec=0, gen_tokens=0,
                prompt_tokens=None, context_max=stats["context_max"])
        async for chunk in provider.stream_chat(runtime_request, request_id=request_id):
            if chunk.type == "usage":
                usage = chunk.usage or {}
                if usage.get("prompt_tokens"):
                    stats["prompt_tokens"] = int(usage["prompt_tokens"])
                if usage.get("completion_tokens"):
                    stats["gen_tokens"] = int(usage["completion_tokens"])
                continue
            if chunk.type == "thinking":
                buf["thinking"] += chunk.content
                job.log("thinking", delta=chunk.content)
                stats["phase"] = "thinking"
            elif chunk.type == "content":
                buf["content"] += chunk.content
                job.log("delta", delta=chunk.content)
                stats["phase"] = "answer"
                await maybe_ckpt()
            now = asyncio.get_event_loop().time()
            if not stats["start"]:
                stats["start"] = now
                # llama.cppは処理中slotから実測プロンプトトークン数を並行取得する
                async def fill_prompt() -> None:
                    stats["prompt_tokens"] = await _prompt_tokens_probe(base_url) or stats["prompt_tokens"]
                prompt_probe = asyncio.get_event_loop().create_task(fill_prompt())
            stats["gen_tokens"] += 1
            stats["recent"].append(now)
            if len(stats["recent"]) > 512:
                del stats["recent"][:256]
            if now - stats["last_emit"] >= 1.0:
                stats["last_emit"] = now
                emit_stats()
        if prompt_probe is not None:
            await prompt_probe
        emit_stats(final=True)
        await asyncio.to_thread(checkpoint, True, "completed")
        # 調査系モードは最終回答と参照文献一覧も会話コレクションへ保存し、後続の
        # 質問・別会話からの再利用（RAG検索）に使えるようにする
        if mode in ("web", "academic", "research"):
            source_hint = user_query[:60] if user_query else mode
            try:
                await register_chat_document(conv_id, buf["content"], f"{mode}回答: {source_hint}")
                sources = (buf.get("meta") or {}).get("sources") or []
                if sources:
                    lines = [
                        f"[{s.get('reference_id', '')}] {s.get('title', '')} — {s.get('url', '')}"
                        for s in sources
                    ]
                    await register_chat_document(conv_id, "\n".join(lines), f"参照文献: {source_hint}")
            except Exception:
                pass  # 埋め込み未導入等では登録スキップ（回答の保存には影響なし）
        # 会話タイトルを最初の user 発話から自動設定
        await asyncio.to_thread(_maybe_title, conv_id)
        return {"assistant_message_id": assistant_id, "chars": len(buf["content"])}
    except asyncio.CancelledError:
        await provider.cancel(request_id)
        await asyncio.to_thread(checkpoint, True, "canceled", "キャンセルされました")
        raise
    except Exception as e:
        await asyncio.to_thread(checkpoint, True, "failed", f"{type(e).__name__}: {e}")
        raise


async def _server_search(
    job: jobs.Job, buf: dict, conv_id: str, mode: str, query: str, params: dict,
    checkpoint_fn=None,
):
    """Web/学術/Deep 検索をサーバー側で実行。web/academic は LLM 生成用の history を返し、
    deep はレポートを buf["content"] に保存して None を返す（呼び出し側で完結）。"""
    from app.workflows import chat_router as cr
    from app.workflows import external_search as ext

    job.log("delta", delta="")  # ストリーム開始マーカー
    if mode == "academic":
        fed = await ext.federated(query, 8)
        results = fed["results"][:12]
        raw_sources = [{
            "title": r.get("title", ""), "url": r.get("url", ""), "source": r.get("source", ""),
            "snippet": r.get("snippet", "") or r.get("abstract", ""), "kind": "paper",
        } for r in results]
        sources = await asyncio.to_thread(_register_conversation_sources, conv_id, raw_sources)
        buf["meta"] = {"mode": "academic", "sources": sources}
        job.log("sources", sources=sources)
        ctx = "\n\n".join(
            f"[{source['reference_id']}] {result.get('title','')}\n"
            f"{str(result.get('snippet','') or result.get('abstract',''))[:400]}\n{result.get('url','')}"
            for source, result in zip(sources, results)
        )
        return [
            {"role": "system", "content":
             "以下の学術検索結果を根拠に日本語で回答してください。主張には [R英数字] で出典を付けること。\n\n" + ctx},
            {"role": "user", "content": query},
        ]

    # web / deep 共通の Web 検索パラメータ
    sb = cr.SearchBody(query=query, mode=mode, engine=params.get("engine", "searxng"),
                       searxng_url=params.get("searxng_url", ""),
                       base_url=params["base_url"], model=params["model"],
                       depth=params.get("deep_depth", "deep"),
                       source_types=params.get("deep_sources") or [],
                       rag_collection=conversation_collection(conv_id),
                       local_project_path=params.get("code_project_path", ""))
    if mode == "web":
        from app.models_mgmt import context_budget

        results = await cr._web_results(sb, query, context_budget.for_model(params["base_url"], params["model"]).search_pages)
        raw_sources = [{
            "title": r.get("title", ""), "url": r.get("url", ""),
            "snippet": r.get("snippet", ""), "source": r.get("source", "web"), "kind": "page",
        } for r in results]
        sources = await asyncio.to_thread(_register_conversation_sources, conv_id, raw_sources)
        buf["meta"] = {"mode": "web", "sources": sources}
        job.log("sources", sources=sources)
        ctx = "\n\n".join(
            f"[{source['reference_id']}] {result['title']}\n{result.get('snippet','')}\n{result['url']}"
            for source, result in zip(sources, results)
        )
        return [
            {"role": "system", "content":
             "以下の検索結果を根拠に日本語で回答してください。主張には [R英数字] で出典を付けること。"
             "検索結果にない内容は推測と明示すること。\n\n" + ctx},
            {"role": "user", "content": query},
        ]

    # deep: 既存の Deep サーチ（分解→収集→引用付きレポート）をサーバー側で実行
    def deep_progress(phase: str, label: str, iteration: int, details: dict) -> None:
        item = {"phase": phase, "label": label, "iteration": iteration, "details": details}
        buf.setdefault("meta", {}).setdefault("progress", []).append(item)
        job.log("progress", **item)
        if checkpoint_fn is not None:
            checkpoint_fn()

    res = await cr._deep_search(sb, progress=deep_progress)
    raw_sources = res.get("sources", [])
    sources = await asyncio.to_thread(_register_conversation_sources, conv_id, raw_sources)
    report = str(res["report"])
    # Deep Searchは内部で一時的な連番を使うため、会話内の永続IDへ置換する。
    for index in range(len(sources), 0, -1):
        report = report.replace(f"[{index}]", f"[{sources[index - 1]['reference_id']}]")
    buf["content"] = report
    buf["meta"] = {
        **buf.get("meta", {}), "mode": "deep", "sources": sources,
        "research": res.get("research", {}),
    }
    job.log("delta", delta=report)
    job.log("sources", sources=sources)
    return None


def _source_key(source: dict) -> str:
    return str(source.get("url") or source.get("title") or "").strip().lower()


def _register_conversation_sources(conv_id: str, sources: list[dict]) -> list[dict]:
    from app.workflows.reference_registry import register_sources

    with SessionLocal() as db:
        return register_sources(db, conv_id, sources)


async def _research_for_code(job: jobs.Job, buf: dict, conv_id: str, query: str, params: dict,
                             limit_chars: int = 60_000) -> str:
    """OpenCode の依頼の前に調べる。調査の計画（assistant_planner.decide）で検索語を決め、Web・論文から根拠を集める。

    2026-11-18 利用者の依頼「『この会話を OpenCode に引き継ぐ』『調べて OpenCode に渡す』をボタンを押さずに」。
    集めた出典は会話の出典として登録し（[R..] で参照できる）、この発言の meta.sources と調べた経過を残す。
    params の base_url / model は OpenCode のモデルを渡す（チャットのモデルを起こすと、1 枚の GPU から
    OpenCode のモデルを追い出す。レビュー指摘 2026-11-18）。量は limit_chars まで。
    失敗しても OpenCode の作業は止めない（調べた結果なしで続ける）。
    """
    from app.workflows.assistant_planner import decide

    code_meta = dict(buf.get("meta") or {})
    try:
        job.log("progress", phase="code_research", label="OpenCode に渡す前に調べています", iteration=0, details={})
        plan = await decide(query, params["base_url"], params["model"])
        if plan.mode not in ("web", "academic", "research", "deep") or not plan.steps:
            plan = None
        messages = await _server_research(job, buf, conv_id, query, params, plan)
        sources = (buf.get("meta") or {}).get("sources") or []
        evidence = str(messages[0].get("content") or "") if messages else ""
        evidence = evidence.split("\n\n", 1)[1] if "\n\n" in evidence else evidence
    except Exception:  # noqa: BLE001 - 調べられなくても依頼は OpenCode へ
        logger.exception("OpenCode に渡す前の調査に失敗しました")
        buf["meta"] = code_meta
        job.log("progress", phase="code_research", label="調べられなかったため、そのまま OpenCode に渡します",
                iteration=0, details={})
        return ""
    # _server_research が mode を research にするので、OpenCode の回答に戻す（出典と調べた経過は残す）
    progress = (buf.get("meta") or {}).get("progress") or []
    buf["meta"] = {**code_meta, "sources": sources, **({"progress": progress} if progress else {})}
    if not evidence.strip():
        return ""
    head = ("## 調べた結果（ControlDeck の調査。Web・論文。出典は [R..]）\n"
            "依頼に関係するところを使い、使った根拠は出典の番号と URL を示してください。\n\n")
    body = evidence[: max(1_000, limit_chars - len(head))]
    return head + body


async def _server_research(
    job: jobs.Job, buf: dict, conv_id: str, query: str, params: dict, plan: AssistantPlan | None,
) -> list[dict]:
    """Web/学術を組み合わせ、不足評価を挟みながら根拠を集める。"""
    from app.workflows import chat_router as cr
    from app.workflows import external_search as ext
    from app.workflows.assistant_planner import evaluate

    if plan is None:
        plan = AssistantPlan(
            mode="research", reason="Webと学術情報を組み合わせる調査",
            steps=[ResearchStep(tool="web", query=query), ResearchStep(tool="academic", query=query)],
            decided_by="fallback",
        )
    from app.models_mgmt import context_budget

    budget = context_budget.for_model(params["base_url"], params["model"])
    max_calls, snippet_chars = budget.investigate_calls, budget.snippet_chars
    max_iterations = min(max(plan.max_iterations, 1), 5)
    pending = list(plan.steps) or [ResearchStep(tool="web", query=query), ResearchStep(tool="academic", query=query)]
    sources: list[dict] = []
    evidence: list[str] = []
    seen_sources: set[str] = set()
    seen_steps: set[tuple[str, str]] = set()
    calls = 0
    iteration_count = 0

    def progress(phase: str, label: str, *, iteration: int) -> None:
        item = {"phase": phase, "label": label, "iteration": iteration}
        buf.setdefault("meta", {}).setdefault("progress", []).append(item)
        job.log("progress", **item)

    for iteration in range(1, max_iterations + 1):
        iteration_count = iteration
        progress("iteration", f"調査 {iteration}/{max_iterations} 回目", iteration=iteration)
        current = pending
        pending = []
        for step in current:
            step_key = (step.tool, step.query.strip().lower())
            if calls >= max_calls or step_key in seen_steps:
                continue
            seen_steps.add(step_key)
            calls += 1
            progress("search", f"{step.tool}: {step.query}", iteration=iteration)
            if step.tool == "academic":
                results = (await ext.federated(step.query, 8))["results"][:10]
            else:
                body = cr.SearchBody(
                    query=step.query, mode="web", engine=params.get("engine", "duckduckgo"),
                    searxng_url=params.get("searxng_url", ""), base_url=params["base_url"], model=params["model"],
                )
                results = await cr._web_results(body, step.query, 8)
            for result in results:
                source = {
                    "title": str(result.get("title") or ""), "url": str(result.get("url") or ""),
                    "source": str(result.get("source") or step.tool),
                    "snippet": str(result.get("snippet") or result.get("abstract") or "")[:snippet_chars],
                    "kind": "paper" if step.tool == "academic" else "page",
                }
                key = _source_key(source)
                if not key or key in seen_sources:
                    continue
                seen_sources.add(key)
                sources.append(source)
                snippet = str(result.get("snippet") or result.get("abstract") or "")[:snippet_chars]
                evidence.append(f"{source['title']}\n{snippet}\n{source['url']}")
        if iteration >= max_iterations or calls >= max_calls:
            break
        progress("evaluate", "収集結果の不足を評価中", iteration=iteration)
        assessment = await evaluate(query, "\n\n".join(evidence), params["base_url"], params["model"])
        if assessment.sufficient:
            progress("sufficient", assessment.reason or "回答に必要な根拠が揃いました", iteration=iteration)
            break
        pending = [step for step in assessment.next_steps if (step.tool, step.query.strip().lower()) not in seen_steps]
        if not pending:
            break

    sources = await asyncio.to_thread(_register_conversation_sources, conv_id, sources)
    buf["meta"] = {
        **buf.get("meta", {}), "mode": "research", "sources": sources,
        "iterations": iteration_count,
    }
    job.log("sources", sources=sources)
    progress("summarize", f"{len(sources)}件の出典を要約中", iteration=min(max_iterations, 5))
    context = "\n\n".join(
        f"[{source['reference_id']}] {item}" for source, item in zip(sources, evidence)
    )
    return [
        {"role": "system", "content":
         "以下はWeb・学術検索を組み合わせて収集した根拠です。利用者の依頼へ日本語で要約し、"
         "主要な主張には必ず [R英数字] を付けてください。根拠にない内容は推測と明記してください。\n\n" + context},
        {"role": "user", "content": query},
    ]


def quick_title(text: str) -> str:
    """最初の発言から、すぐ付ける題名（数式の記号・URL・コードを除いた最初の 1 文、28 字まで）。"""
    body = re.sub(r"```.*?```", " ", text, flags=re.S)
    body = re.sub(r"https?://\S+", " ", body)
    body = re.sub(r"\\[()\[\]]|\$+|[`*#>]", "", body)
    line = next((part.strip() for part in re.split(r"[\n。！？!?]", body) if part.strip()), "")
    line = re.sub(r"\s+", " ", line)
    return (line[:28] + "…" if len(line) > 28 else line) or "新しい会話"


async def _refine_title(conv_id: str, base_url: str, model: str, mode: str = "chat") -> None:
    """最初の回答の後で、会話の内容から短い題名を作る（失敗しても最初の題名のまま）。
    今動いたモデルで作る（別のモデルを GPU へ載せ替えない）。送信待ちがあれば作らない（そちらを先に）。"""
    if mode == "code":
        from app.integrations.opencode import provider as opencode_provider

        settings = await asyncio.to_thread(opencode_provider.get_settings)
        base_url, model = str(settings["base_url"]), str(settings["model"])
    db = SessionLocal()
    try:
        if _queued(db, conv_id):
            return
        conv = db.get(Conversation, conv_id)
        users = db.execute(select(ChatMessage).where(ChatMessage.conversation_id == conv_id, ChatMessage.role == "user")
                           .order_by(ChatMessage.created_at)).scalars().all()
        if conv is None or len(users) != 1 or conv.title != quick_title(users[0].content):
            return
        current, question = conv.title, users[0].content[:1500]
    finally:
        db.close()
    from app.workflows.chat_router import _llm

    try:
        answer = await _llm([{"role": "system", "content": "会話の題名だけを日本語 20 字以内で出力してください。記号や説明は付けません。"},
                             {"role": "user", "content": question}], base_url, model, "", temperature=0.2, max_tokens=48,
                            disable_thinking=True, timeout_seconds=60)
    except Exception:  # noqa: BLE001 - 題名は無くても会話は続く
        logger.debug("会話の題名を作れませんでした", exc_info=True)
        return
    title = quick_title(re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip().strip("「」\"'"))
    if not title or title == "新しい会話":
        return
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conv_id)
        if conv is not None and conv.title == current:   # 利用者が変えていれば触らない
            conv.title = title[:60]
            db.commit()
    finally:
        db.close()


def _maybe_title(conv_id: str) -> None:
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.title != "新しい会話":
            return
        first = db.execute(
            select(ChatMessage).where(ChatMessage.conversation_id == conv_id, ChatMessage.role == "user")
            .order_by(ChatMessage.created_at).limit(1)
        ).scalar_one_or_none()
        if first:
            conv.title = quick_title(first.content)
            db.commit()
    finally:
        db.close()


@router.post("/conversations/{conv_id}/send", status_code=201)
async def send_message(
    conv_id: str, body: SendBody, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    """user メッセージ + assistant プレースホルダ + 生成ジョブを作成する（1 トランザクション）。"""
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return await _start_send(db, conv, body, user.id)


async def _start_send(db: Session, conv: Conversation, body: SendBody, owner_user_id: int | None) -> dict:
    """送信の本体（画面からの送信と、送信待ちの要望の自動送信で共通）。"""
    conv_id = conv.id
    allowed_modes = {"auto", "chat", "web", "academic", "deep", "research", "code"}
    if body.mode not in allowed_modes:
        raise HTTPException(status_code=422, detail="未対応のチャットモードです")
    if body.mode == "code" and db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant",
        ChatMessage.status == "generating",
    ).first() is not None:
        # 同じセッションのイベントを 2 つの発言が取り合わないように
        raise HTTPException(status_code=409, detail="OpenCode が作業中です。終わるか止めてから送ってください")
    if body.mode == "code" and not body.code_project and not body.code_project_path:
        # 作業フォルダの指定が空なら、この会話で最後に OpenCode が動いたプロジェクトで続ける
        # （画面を開き直すと選択が外れていた。2026-11-18 利用者の依頼）
        for row in db.execute(select(ChatMessage).where(
                ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant",
        ).order_by(ChatMessage.created_at.desc()).limit(50)).scalars():
            try:
                previous = json.loads(row.meta_json or "{}").get("project_path")
            except (json.JSONDecodeError, AttributeError):
                previous = None
            if previous:
                # CodeDEV の直下なら名前で開く（フォルダの取り込みの道を通すと、設定によって失敗するか、
                # 丸ごと複製した別のプロジェクトで作業してしまう。レビュー指摘 2026-11-18）。消えていれば戻さない
                from app.integrations.opencode import provider as opencode_provider

                previous_path = Path(str(previous))
                root = opencode_provider.codedev_root().resolve()
                if previous_path.is_dir() and previous_path.resolve().parent == root:
                    body.code_project = previous_path.resolve().name
                elif previous_path.is_dir():
                    body.code_project_path = str(previous_path)
                break
    if body.plan is not None and body.mode not in ("auto", body.plan.mode):
        raise HTTPException(status_code=422, detail="モードと調査計画が一致しません")
    # 履歴（system + 過去 + 今回）
    past = db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at)
    ).scalars().all()
    from app.workflows.reference_registry import (
        build_reference_context, extract_reference_ids, resolve_references,
    )

    history = [{"role": "system", "content": body.system + "\n\n" + REFERENCE_SYSTEM_GUIDANCE}]
    if body.mode != "code" and any('"mode": "code"' in (m.meta_json or "") for m in past if m.role == "assistant"):
        # OpenCode の作業の履歴があると、道具の無いチャットでも呼び出しを文字で書いてしまう（2026-11-18 本番）
        history.append({"role": "system", "content": "この返答では道具やコマンドを実行できません。<tool_call> などの呼び出しを"
                        "書かず、分かる範囲を文章で答えてください。ファイルの確認や変更が必要なら、OpenCode で続けるよう伝えてください。"})
    requested_ids = extract_reference_ids(body.content)
    requested_refs = resolve_references(db, conv_id, requested_ids)
    if requested_refs:
        history.append({
            "role": "system",
            "content": (
                "利用者が指定した会話内文献を以下に展開します。この資料だけを必要に応じて参照し、"
                "回答の根拠には対応する文献IDを付けてください。\n\n"
                + build_reference_context(requested_refs)
            ),
        })
    resolved_ids = {ref.short_id for ref in requested_refs}
    missing_ids = [reference_id for reference_id in requested_ids if reference_id not in resolved_ids]
    if missing_ids:
        history.append({
            "role": "system",
            "content": (
                f"指定された文献ID {', '.join(missing_ids)} はこの会話に存在しません。"
                "内容を推測せず、必要なら利用者へIDの確認を求めてください。"
            ),
        })
    # 自動圧縮済みなら、要約から再開する（要約に含めた古い発話は送らない）。
    from app.workflows import chat_compaction

    summary, resume_at = chat_compaction.resume_point(list(past))
    if summary:
        history.append(chat_compaction.summary_message(summary))
    for m in past[resume_at:]:
        if m.role in ("user", "assistant") and m.content:
            history.append({"role": m.role, "content": m.content, chat_compaction.ID_KEY: m.id})
    history.append({"role": "user", "content": body.content})

    user_msg = ChatMessage(id=_new_id(), conversation_id=conv_id, role="user",
                           content=body.content, status="completed")
    sent_images = [a for a in body.attachments
                   if _ATTACHMENT_ID_RE.fullmatch(a) and (_attachment_root(conv_id) / a).is_file()]
    archive_rows = await asyncio.to_thread(_sent_archives, conv_id, list(body.attachments))
    sent_archives = [row["id"] for row in archive_rows]
    if sent_archives and body.mode != "code":
        raise HTTPException(status_code=422, detail="圧縮ファイルは OpenCode で展開して使います。＋から OpenCode を選んでください")
    user_meta: dict = {}
    if sent_images:
        # 発言の下に画像を出すため（画像そのものは chat-uploads にある）。
        user_meta["attachments"] = sent_images
    if sent_archives:
        user_meta["archives"] = archive_rows
    if user_meta:
        user_msg.meta_json = json.dumps(user_meta, ensure_ascii=False)
    assistant = ChatMessage(id=_new_id(), conversation_id=conv_id, role="assistant",
                            content="", status="generating", model=body.model)
    db.add(user_msg)
    db.add(assistant)
    if conv.title == "新しい会話":
        conv.title = quick_title(body.content)   # 話し始めたらすぐ付ける（回答が終わるまで「新しい会話」のままだった）
    conv.updated_at = utcnow()
    db.commit()
    assistant_id = assistant.id
    try:
        return await _start_job(db, conv_id, body, owner_user_id, history, user_msg.id, assistant_id,
                                sent_archives)
    except BaseException as e:
        # 発言を書いた後で失敗したら「生成中」のまま残さない（残ると、この会話の送信待ちも OpenCode も止まる。
        # レビュー指摘 2026-11-18）
        db.rollback()
        row = db.get(ChatMessage, assistant_id)
        if row is not None and row.status == "generating":
            row.status = "failed"
            row.error = f"生成を始められませんでした（{type(e).__name__}）"
            db.commit()
        raise


async def _start_job(db: Session, conv_id: str, body: SendBody, owner_user_id: int | None, history: list[dict],
                     user_message_id: str, assistant_id: str, sent_archives: list[str]) -> dict:

    from app.models_mgmt.runtime_policy import get_policy, model_output_tokens
    from app.models_mgmt.runtime_provider import resolve_target

    # ゲートウェイ宛なら実エンドポイントへ解決してからjobへ渡す。以降のcontext上限・
    # thinking・容量待ちはすべてモデル個別設定を引くため、解決済みの値が要る。
    body.base_url, body.model = resolve_target(body.base_url, body.model)
    params = {"base_url": body.base_url, "model": body.model, "mode": body.mode,
              "engine": body.engine, "searxng_url": body.searxng_url,
              "deep_depth": body.deep_depth, "deep_sources": body.deep_sources,
              # 未指定ならモデル個別設定へ委ねる（共通設定で一律に縛らない）
              "thinking": body.thinking or "",
              "max_output_tokens": model_output_tokens(body.base_url, body.model),
              "attachments": body.attachments,
              "code_project": body.code_project,
              "code_project_path": body.code_project_path,
              # OpenCode への引き継ぎで、今回の発言そのものを重ねて入れないため
              "user_message_id": user_message_id,
              # OpenCode のプロジェクトへ展開する圧縮ファイル
              "archives": sent_archives,
              "code_research": body.code_research and body.mode == "code",
              "code_agent": body.code_agent if body.mode == "code" else "build",
              "plan": body.plan.model_dump() if body.plan is not None else None}
    label = {"auto": "自動判定", "chat": "チャット生成", "web": "Web検索", "academic": "学術検索",
             "deep": "Deepサーチ", "research": "複合調査", "code": "OpenCode"}.get(body.mode, "生成")
    async def run(j: jobs.Job) -> dict:
        try:
            result = await _run_chat_job(j, assistant_id, conv_id, history, params)
        finally:
            _latest_stats.pop(assistant_id, None)
        # 正常に終わったら、送信待ちの要望を送る（止めた・失敗したときは送らずに保留する）
        _after_turn(conv_id, assistant_id, j)
        # 題名はジョブの外で（送信待ちの自動送信を遅らせない）
        task = asyncio.get_running_loop().create_task(
            _refine_title(conv_id, params["base_url"], params["model"], params.get("mode", "chat")))
        _queue_tasks.add(task)
        task.add_done_callback(_queue_tasks.discard)
        return result

    job = jobs.create(
        "chat.completion", f"{label}: {body.content[:40]}", run,
        owner_user_id=owner_user_id, idempotency_key=assistant_id, priority=10,
    )
    # assistant メッセージに job_id を紐付け（再接続時に購読を再開できる）
    db.query(ChatMessage).filter(ChatMessage.id == assistant_id).update({"job_id": job.id})
    db.commit()
    return {"user_message_id": user_message_id, "assistant_message_id": assistant_id, "job_id": job.id, "status": "generating"}


# ---- 送信待ちの要望（生成中に送ったもの。2026-11-18 利用者の依頼） ----
#
# 画面は生成中の送信をここへ貯める。生成が正常に終わったら、サーバーが古い順に会話へ送る（画面を閉じていても届く）。
# 続けて貯めた要望で送り方（モード・モデルなど）が同じものは、1 つの発言にまとめる（細切れの追加の指示を
# まとめて読ませる）。止めた・失敗したときは送らずに保留し、利用者が「今すぐ送る」で送る。

QUEUE_LIMIT = 20
_latest_stats: dict[str, dict] = {}   # 生成中の回答のいまの段階（開き直したときに出す）
_queue_locks: dict[str, asyncio.Lock] = {}
_queue_tasks: set[asyncio.Task] = set()


def _queue_out(item: ChatQueuedRequest) -> dict:
    try:
        mode = json.loads(item.body_json or "{}").get("mode", "chat")
    except json.JSONDecodeError:
        mode = "chat"
    return {"id": item.id, "content": item.content, "status": item.status, "mode": mode, "error": item.error,
            "message_id": item.message_id, "created_at": item.created_at, "updated_at": item.updated_at}


def _generating_id(db: Session, conv_id: str) -> str | None:
    row = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant", ChatMessage.status == "generating",
    ).first()
    return row.id if row is not None else None


def _queued(db: Session, conv_id: str, statuses: tuple[str, ...] = ("queued",)) -> list[ChatQueuedRequest]:
    return list(db.execute(select(ChatQueuedRequest).where(
        ChatQueuedRequest.conversation_id == conv_id, ChatQueuedRequest.status.in_(statuses),
    ).order_by(ChatQueuedRequest.created_at, ChatQueuedRequest.id)).scalars())


def _same_way(a: dict, b: dict) -> bool:
    """同じ送り方か（本文・添付を除いて比べる）。"""
    skip = {"content", "attachments"}
    return {k: v for k, v in a.items() if k not in skip} == {k: v for k, v in b.items() if k not in skip}


def _groups(items: list[ChatQueuedRequest]) -> list[list[ChatQueuedRequest]]:
    """送る単位に分ける: 送り方が同じ続きを、本文 32000 字・添付 8 個に収まるまでまとめる。"""
    groups: list[list[ChatQueuedRequest]] = []
    for item in items:
        body = json.loads(item.body_json or "{}")
        attachments = len(body.get("attachments", []))
        if groups:
            head = json.loads(groups[-1][0].body_json or "{}")
            length = sum(len(i.content) + 2 for i in groups[-1])
            count = sum(len(json.loads(i.body_json or "{}").get("attachments", [])) for i in groups[-1])
            if _same_way(head, body) and length + len(item.content) <= 32000 and count + attachments <= 8:
                groups[-1].append(item)
                continue
        groups.append([item])
    return groups


async def dispatch_queue(conv_id: str) -> dict | None:
    """生成中でなければ、送信待ちの先頭のまとまりを送る。送れないまとまりは「送れなかった」にして次へ進む
    （先頭が送れないと後ろが永久に残っていた。レビュー指摘）。送ったら send の結果を返す。"""
    lock = _queue_locks.setdefault(conv_id, asyncio.Lock())
    async with lock:
        db = SessionLocal()
        try:
            conv = db.get(Conversation, conv_id)
            if conv is None or _generating_id(db, conv_id):
                return None
            for group in _groups(_queued(db, conv_id)):
                first = json.loads(group[0].body_json or "{}")
                attachments: list[str] = []
                for item in group:
                    for a in json.loads(item.body_json or "{}").get("attachments", []):
                        if a not in attachments:
                            attachments.append(a)
                try:
                    body = SendBody.model_validate({**first, "content": "\n\n".join(i.content for i in group),
                                                    "attachments": attachments})
                    result = await _start_send(db, conv, body, conv.owner_user_id)
                except Exception as e:  # noqa: BLE001 - 送れないまとまりは印を付けて次へ
                    db.rollback()
                    detail = e.detail if isinstance(e, HTTPException) else f"{type(e).__name__}: {e}"
                    for item in group:
                        item.status = "failed"
                        item.error = str(detail)[:500]
                    db.commit()
                    logger.warning("送信待ちの要望を送れませんでした: %s", detail)
                    continue
                for item in group:
                    item.status = "sent"
                    item.message_id = result["user_message_id"]
                    item.error = ""
                db.commit()
                return result
            return None
        finally:
            db.close()


def _turn_finished(db: Session, conv_id: str) -> bool:
    """自動で送ってよい状態か: 生成中が無く、最後の回答が正常に終わっている（止めた・失敗した後は保留）。"""
    if _generating_id(db, conv_id):
        return False
    last = db.execute(select(ChatMessage).where(
        ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant",
    ).order_by(ChatMessage.created_at.desc()).limit(1)).scalar_one_or_none()
    return last is None or last.status == "completed"


def _after_turn(conv_id: str, assistant_id: str, job: jobs.Job | None = None) -> None:
    """生成が終わったとき（ジョブの中から呼ぶ）。正常に終わっていれば、ジョブが片付いて（資源を返し、画面へ
    「終わり」が届いて）から送る。決まった秒数で待つと、混んでいるときに前のジョブと重なっていた（レビュー指摘）。"""
    db = SessionLocal()
    try:
        row = db.get(ChatMessage, assistant_id)
        if row is None or row.status != "completed" or not _queued(db, conv_id):
            return
    finally:
        db.close()

    async def later() -> None:
        deadline = asyncio.get_running_loop().time() + 60
        while job is not None and job.finished_at is None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        try:
            await dispatch_queue(conv_id)
        except Exception:  # noqa: BLE001 - 送れなくても生成の結果には響かせない（保留として残る）
            logger.exception("送信待ちの要望の自動送信に失敗しました")

    task = asyncio.get_running_loop().create_task(later())
    _queue_tasks.add(task)
    task.add_done_callback(_queue_tasks.discard)


@router.get("/conversations/{conv_id}/queue")
def list_queue(conv_id: str, user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    _owned_conversation(db, conv_id, user)
    items = _queued(db, conv_id, ("queued", "failed"))
    generating = _generating_id(db, conv_id)
    return {"items": [_queue_out(i) for i in items], "generating_message_id": generating,
            # 生成中でないのに残っている＝止めた・失敗した後で保留している
            "held": bool(not generating and any(i.status == "queued" for i in items))}


class QueueBody(SendBody):
    # 画面がまだ前の発言を送っている途中（自動判定・ワークフロー）なら、すぐには送らない（前の発言より先に
    # 送られて順番が入れ替わっていた。レビュー指摘）。その発言が終わったときに送る
    hold: bool = False


@router.post("/conversations/{conv_id}/queue", status_code=201)
async def add_queue(conv_id: str, body: QueueBody, user: User = Depends(require_permission("workflows.run")),
                    db: Session = Depends(get_db)):
    _owned_conversation(db, conv_id, user)
    if body.mode not in {"auto", "chat", "web", "academic", "deep", "research", "code"}:
        raise HTTPException(status_code=422, detail="未対応のチャットモードです")
    if len(_queued(db, conv_id, ("queued", "failed"))) >= QUEUE_LIMIT:
        raise HTTPException(status_code=409, detail=f"送信待ちは {QUEUE_LIMIT} 件までです")
    item = ChatQueuedRequest(id=_new_id(), conversation_id=conv_id, content=body.content,
                             body_json=json.dumps(body.model_dump(exclude={"content", "hold"}), ensure_ascii=False))
    db.add(item)
    db.commit()
    out = _queue_out(item)
    # ちょうど生成が終わっていたら、すぐ送る
    dispatched = None if body.hold or _generating_id(db, conv_id) else await dispatch_queue(conv_id)
    return {"item": out, "dispatched": dispatched}


async def _send_if_finished(conv_id: str) -> None:
    """戻した・直した要望を、回答がもう正常に終わっていれば送る（保留のまま残っていた。レビュー指摘）。"""
    db = SessionLocal()
    try:
        ready = _turn_finished(db, conv_id)
    finally:
        db.close()
    if ready:
        await dispatch_queue(conv_id)


class QueueEdit(BaseModel):
    content: str = Field(min_length=1, max_length=32000)


def _queue_item(db: Session, conv_id: str, item_id: str, user: User) -> ChatQueuedRequest:
    _owned_conversation(db, conv_id, user)
    item = db.get(ChatQueuedRequest, item_id)
    if item is None or item.conversation_id != conv_id:
        raise HTTPException(status_code=404, detail="送信待ちの要望が見つかりません")
    return item


@router.patch("/conversations/{conv_id}/queue/{item_id}")
async def edit_queue(conv_id: str, item_id: str, body: QueueEdit,
                     user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    async with _queue_locks.setdefault(conv_id, asyncio.Lock()):   # 送っている最中に書き換えない
        db.expire_all()
        item = _queue_item(db, conv_id, item_id, user)
        if item.status not in ("queued", "failed"):
            raise HTTPException(status_code=409, detail="もう送りました（送った発言は「⋯」から編集して送り直せます）")
        item.content = body.content
        item.status = "queued"
        item.error = ""
        db.commit()
        out = _queue_out(item)
    await _send_if_finished(conv_id)
    return out


@router.delete("/conversations/{conv_id}/queue/{item_id}")
async def delete_queue(conv_id: str, item_id: str,
                       user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    """送信待ちから外す（すぐには消さない。「元に戻す」で戻せる）。"""
    async with _queue_locks.setdefault(conv_id, asyncio.Lock()):
        db.expire_all()
        item = _queue_item(db, conv_id, item_id, user)
        if item.status not in ("queued", "failed"):
            raise HTTPException(status_code=409, detail="もう送りました")
        item.status = "deleted"
        db.commit()
        return _queue_out(item)


@router.post("/conversations/{conv_id}/queue/{item_id}/restore")
async def restore_queue(conv_id: str, item_id: str,
                        user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    async with _queue_locks.setdefault(conv_id, asyncio.Lock()):
        db.expire_all()
        item = _queue_item(db, conv_id, item_id, user)
        if item.status != "deleted":
            raise HTTPException(status_code=409, detail="戻せる状態ではありません")
        item.status = "queued"
        item.error = ""
        db.commit()
        out = _queue_out(item)
    await _send_if_finished(conv_id)
    return out


@router.delete("/conversations/{conv_id}/queue")
async def clear_queue(conv_id: str, user: User = Depends(require_permission("workflows.run")),
                      db: Session = Depends(get_db)):
    async with _queue_locks.setdefault(conv_id, asyncio.Lock()):
        db.expire_all()
        _owned_conversation(db, conv_id, user)
        items = _queued(db, conv_id, ("queued", "failed"))
        for item in items:
            item.status = "deleted"
        db.commit()
        return {"ids": [i.id for i in items]}


@router.post("/conversations/{conv_id}/queue/dispatch")
async def dispatch_queue_now(conv_id: str, user: User = Depends(require_permission("workflows.run")),
                             db: Session = Depends(get_db)):
    """保留している送信待ちを今すぐ送る（止めた・失敗した後）。"""
    _owned_conversation(db, conv_id, user)
    if _generating_id(db, conv_id):
        raise HTTPException(status_code=409, detail="生成中です。終わったら自動で送ります")
    for item in _queued(db, conv_id, ("failed",)):
        item.status = "queued"          # 失敗したものも送り直す
        item.error = ""
    db.commit()
    result = await dispatch_queue(conv_id)
    if result is None:
        raise HTTPException(status_code=409, detail="送れませんでした（送信待ちが空か、送れない内容です）")
    return result


# ---- 生成ストリーム購読（切断してもジョブは継続） ----


@router.websocket("/messages/{message_id}/stream")
async def stream_message(websocket: WebSocket, message_id: str):
    """assistant メッセージの生成をストリームする。job_id から購読を再開できる。"""
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "workflows.run")
        if user is None:
            return
        msg = db.get(ChatMessage, message_id)
        conversation = db.get(Conversation, msg.conversation_id) if msg else None
        if msg is not None and (conversation is None or conversation.owner_user_id != user.id):
            msg = None
        job_id = msg.job_id if msg else None
        saved_content = msg.content if msg else ""
        saved_thinking = msg.thinking if msg else ""
        saved_timeline = _timeline_of(msg) if msg else []
        # 本文がまだ無くても、思考や並びがあれば今の内容を送る（送らないと最初からの出来事を重ねて流し、
        # 同じ思考が 2 つの窓に出る。レビュー指摘 2026-11-18）
        has_saved = bool(saved_content or saved_thinking or saved_timeline)
        saved_status = msg.status if msg else "failed"
        saved_error = (msg.error or "") if msg else ""
    finally:
        db.close()
    await websocket.accept()
    if msg is None:
        await websocket.send_text(json.dumps({"type": "error", "message": "メッセージが見つかりません"}))
        await websocket.close()
        return

    # まず現在までの保存済み内容を送る（再接続時の即時復元）。snapshotを送った場合は
    # それ以前のdeltaを再送しない。最終snapshotでもDB内容へ必ず収束させる。
    if has_saved:
        await websocket.send_text(json.dumps({"type": "snapshot", "content": saved_content, "thinking": saved_thinking,
                                              "timeline": saved_timeline}))
    if message_id in _latest_stats:     # 開き直しても、いまの段階をすぐ出す
        await websocket.send_text(json.dumps(_latest_stats[message_id], ensure_ascii=False))

    job = jobs.get(job_id) if job_id else None
    if job is None:
        # ジョブがメモリに無い = 完了済み or 再起動で消失。DB の最終状態を返す
        await websocket.send_text(json.dumps({"type": "done", "status": saved_status, "error": saved_error},
                                             ensure_ascii=False))
        await websocket.close()
        return

    try:
        cursor = job.event_sequence if has_saved else job.event_offset
        while True:
            await jobs.wait_events(job, cursor)
            events, next_cursor, truncated = job.events_since(cursor)
            if truncated:
                # 購読処理より生成が速くbounded journalを追い越した。DB checkpointを
                # 全文置換snapshotとして送り、古いdeltaの欠落/重複を解消する。
                with SessionLocal() as snapshot_db:
                    current = snapshot_db.get(ChatMessage, message_id)
                    if current is not None:
                        await websocket.send_text(json.dumps({
                            "type": "snapshot", "content": current.content,
                            "thinking": current.thinking or "", "timeline": _timeline_of(current),
                        }, ensure_ascii=False))
                cursor = next_cursor
                continue
            for ev in events:
                if ev.get("message") == "delta":
                    await websocket.send_text(json.dumps({"type": "delta", "content": ev.get("delta", "")}))
                elif ev.get("message") == "thinking":
                    await websocket.send_text(json.dumps({"type": "thinking", "content": ev.get("delta", "")}))
                elif ev.get("message") == "sources":
                    await websocket.send_text(json.dumps({"type": "sources", "sources": ev.get("sources", [])}, ensure_ascii=False))
                elif ev.get("message") == "tool":
                    await websocket.send_text(json.dumps({"type": "tool", "tool": ev.get("tool", {})}, ensure_ascii=False))
                elif ev.get("message") == "permission":
                    await websocket.send_text(json.dumps({"type": "permission", "permission": ev.get("permission", {})},
                                                         ensure_ascii=False))
                elif ev.get("message") == "plan":
                    await websocket.send_text(json.dumps({"type": "plan", "plan": ev.get("plan", {})}, ensure_ascii=False))
                elif ev.get("message") == "progress":
                    await websocket.send_text(json.dumps({
                        "type": "progress", "phase": ev.get("phase", ""), "label": ev.get("label", ""),
                        "iteration": ev.get("iteration", 0), "details": ev.get("details", {}),
                    }, ensure_ascii=False))
                elif ev.get("message") == "compaction":
                    await websocket.send_text(json.dumps({
                        "type": "compaction", "tokens_before": ev.get("tokens_before"),
                        "tokens_after": ev.get("tokens_after"), "threshold": ev.get("threshold"),
                        "folded_messages": ev.get("folded_messages"),
                    }))
                elif ev.get("message") == "stats":
                    await websocket.send_text(json.dumps({
                        "type": "stats", "phase": ev.get("phase", ""),
                        "tok_per_sec": ev.get("tok_per_sec"), "gen_tokens": ev.get("gen_tokens"),
                        "prompt_tokens": ev.get("prompt_tokens"), "context_max": ev.get("context_max"),
                        "detail": ev.get("detail", ""),
                    }))
                elif ev.get("message") == "todos":
                    await websocket.send_text(json.dumps({"type": "todos", "todos": ev.get("todos", [])}, ensure_ascii=False))
            cursor = next_cursor
            if job.status not in ("queued", "running") and cursor >= job.event_sequence:
                break
        # checkpointはジョブ完了より先にcommitされる。最後に全文を再送し、再接続や
        # journal切詰めがあっても表示をDBの正本へ収束させる。
        # 画面へ出す理由は発言に記録したもの（利用者向けの文）を優先する。
        final_error = ""
        with SessionLocal() as final_db:
            final = final_db.get(ChatMessage, message_id)
            if final is not None:
                final_error = final.error or ""
                await websocket.send_text(json.dumps({
                    "type": "snapshot", "content": final.content,
                    "thinking": final.thinking or "", "timeline": _timeline_of(final),
                }, ensure_ascii=False))
        await websocket.send_text(json.dumps(
            {"type": "done", "status": job.status, "error": final_error or job.error}, ensure_ascii=False))
    except WebSocketDisconnect:
        return  # 切断してもジョブは継続（DB に保存され続ける）
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass
