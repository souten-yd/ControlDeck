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
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.audit import service as audit
from app.jobs import service as jobs
from app.models import ChatMessage, ChatReference, Conversation, User
from app.models import utcnow
from app.schemas.assistant import AssistantPlan, ResearchStep
from app.security.deps import authenticate_websocket, require_permission
from app.workflows.chat_router import _keep_alive, _resolve_think

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
        if any(int(item.get("port", 0)) == port for item in llama.list_instances()):
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
        if not any(int(item.get("port", 0)) == port for item in llama.list_instances()):
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
    return {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat() if c.updated_at else ""}


@router.post("/conversations", status_code=201)
def create_conversation(user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    conv = Conversation(id=_new_id(), owner_user_id=user.id)
    db.add(conv)
    db.commit()
    return _conv_out(conv)


@router.get("/conversations")
def list_conversations(user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Conversation).where(Conversation.owner_user_id == user.id)
        .order_by(Conversation.updated_at.desc()).limit(100)
    ).scalars().all()
    return [_conv_out(c) for c in rows]


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.patch("/conversations/{conv_id}")
def update_conversation(
    conv_id: str, body: ConversationUpdate, request: Request,
    user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db),
):
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    conv.title = body.title.strip()
    conv.updated_at = utcnow()
    db.commit()
    audit.record(db, "chat.conversation.rename", user=user, resource_type="conversation",
                 resource_id=conv_id, request=request)
    return _conv_out(conv)


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
    db.delete(conv)
    db.commit()
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
    system: str = "あなたは Control Deck の AI アシスタントです。日本語で簡潔に答えてください。"
    thinking: str | None = None  # off / auto / on。省略時はruntime共通設定。
    # 画像添付（/attachments でアップロード済みのID）。VLM有効モデルで画像入力に使う
    attachments: list[str] = Field(default_factory=list, max_length=8)
    # OpenCodeチャット実行（mode=code）用のCodeDEVプロジェクト名
    code_project: str = Field(default="", max_length=64)
    # 任意フォルダ指定（CodeDEV外はサーバー側でコピー取り込み）
    code_project_path: str = Field(default="", max_length=4096)


# ---- 添付（画像=VLM入力 / 文書=会話別RAGコレクション登録） ----

ATTACHMENT_IMAGE_TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_ATTACHMENT_ID_RE = re.compile(r"^[0-9a-f]{16}\.(png|jpg|webp|gif)$")


def conversation_collection(conv_id: str) -> str:
    """会話別RAGコレクション名。添付文書・検索資料・レポートをここへ蓄積する。"""
    return f"chat-{conv_id}"


def _attachment_dir(conv_id: str):
    from app.config import data_dir

    root = (data_dir() / "chat-uploads" / conv_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


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
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=422, detail="ファイルが空です")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="添付は20MiB以内にしてください")
    content_type = (file.content_type or "").lower()
    name = file.filename or "attachment"
    if content_type in ATTACHMENT_IMAGE_TYPES:
        attachment_id = uuid.uuid4().hex[:16] + ATTACHMENT_IMAGE_TYPES[content_type]
        (_attachment_dir(conv_id) / attachment_id).write_bytes(data)
        return {"kind": "image", "id": attachment_id, "name": name}
    from app.workflows import rag

    try:
        text = _extract_document_text(name, content_type, data)
        result = await register_chat_document(conv_id, text, name)
    except rag.RagError as exc:
        raise HTTPException(status_code=422, detail=f"RAG登録に失敗: {exc}") from exc
    except Exception as exc:  # pypdf等の解析失敗
        raise HTTPException(status_code=422, detail=f"文書を読み取れません: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=422, detail="テキストを抽出できませんでした（80文字未満）")
    return {"kind": "document", "id": name, "name": name,
            "collection": conversation_collection(conv_id),
            "chunks": result.get("added_chunks")}


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

    return (await decide(body.content, body.base_url, body.model)).model_dump()


async def _run_chat_job(job: jobs.Job, assistant_id: str, conv_id: str,
                        history: list[dict], params: dict) -> dict:
    """サーバー側で検索/生成し、assistant メッセージへ部分出力を保存する（全モード対応）。"""
    base_url = params["base_url"]
    model = params["model"]
    mode = params.get("mode", "chat")
    thinking_mode = str(params.get("thinking", "off"))
    max_output_tokens = int(params["max_output_tokens"])
    from app.models_mgmt.runtime_provider import RuntimeChatRequest, provider_for_base_url
    think = _resolve_think(thinking_mode, model)
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

    # ---- OpenCodeチャット実行: TUIへ遷移せずheadlessで実行し、本文をストリームする ----
    if mode == "code":
        from app.integrations.opencode import provider as opencode_provider

        query = str(history[-1].get("content") or "") if history else ""

        def _last_code_session() -> str:
            """この会話の直近のopencodeセッションID（継続対話用）。"""
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

        previous_session = await asyncio.to_thread(_last_code_session)

        async def on_text(text: str) -> None:
            buf["content"] += text + "\n\n"
            job.log("delta", delta=text + "\n\n")
            await maybe_ckpt()

        # ツール実行等のイベントを間引いて進捗表示（作業中に無反応にならないように）
        event_state = {"last": 0.0}

        async def on_event(event_type: str, events: int) -> None:
            now = asyncio.get_event_loop().time()
            if now - event_state["last"] < 1.5:
                return
            event_state["last"] = now
            label = {"tool_use": "ツール実行", "tool_result": "ツール結果", "step-start": "ステップ開始",
                     "step-finish": "ステップ完了", "message": "応答生成", "text": "応答生成"}.get(event_type, event_type or "処理中")
            job.log("progress", phase="opencode", label=f"{label}（{events}イベント）", iteration=events, details={})
            job.log("stats", phase="code", tok_per_sec=0.0, gen_tokens=events,
                    prompt_tokens=None, context_max=None)

        try:
            result = await opencode_provider.run_chat(
                job, instruction=query, project_name=params.get("code_project", ""),
                project_path=params.get("code_project_path", ""),
                session_id=previous_session, on_text=on_text, on_event=on_event,
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(checkpoint, True, "canceled", "キャンセルされました")
            raise
        except opencode_provider.CodeAgentError as e:
            await asyncio.to_thread(checkpoint, True, "failed", str(e))
            raise
        buf["meta"] = {"mode": "code", "opencode_session": result.get("session_id", ""),
                       "project_path": result.get("project_path", "")}
        if not buf["content"].strip():
            buf["content"] = result.get("output") or "（OpenCodeからの出力はありませんでした）"
        await asyncio.to_thread(checkpoint, True, "completed")
        await asyncio.to_thread(_maybe_title, conv_id)
        return {"assistant_message_id": assistant_id, "opencode_session": result.get("session_id", "")}

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
                found = await rag.search(collection=collection, question=question, top_k=4)
                if found.get("context"):
                    history = [{"role": "system", "content":
                                "この会話に添付・収集された資料の関連抜粋です。回答の根拠に使ってください:\n\n"
                                + str(found["context"])[:12_000]}, *history]
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

    try:
        runtime_request = RuntimeChatRequest(
            base_url=base_url, model=model, messages=history,
            max_tokens=max_output_tokens, thinking=think,
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
    sb = cr.SearchBody(query=query, mode=mode, engine=params.get("engine", "duckduckgo"),
                       searxng_url=params.get("searxng_url", ""),
                       base_url=params["base_url"], model=params["model"])
    if mode == "web":
        results = await cr._web_results(sb, query, 8)
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
            if calls >= 8 or step_key in seen_steps:
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
                    "snippet": str(result.get("snippet") or result.get("abstract") or "")[:700],
                    "kind": "paper" if step.tool == "academic" else "page",
                }
                key = _source_key(source)
                if not key or key in seen_sources:
                    continue
                seen_sources.add(key)
                sources.append(source)
                snippet = str(result.get("snippet") or result.get("abstract") or "")[:700]
                evidence.append(f"{source['title']}\n{snippet}\n{source['url']}")
        if iteration >= max_iterations or calls >= 8:
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
            conv.title = first.content[:40]
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
    allowed_modes = {"auto", "chat", "web", "academic", "deep", "research", "code"}
    if body.mode not in allowed_modes:
        raise HTTPException(status_code=422, detail="未対応のチャットモードです")
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
    for m in past:
        if m.role in ("user", "assistant") and m.content:
            history.append({"role": m.role, "content": m.content})
    history.append({"role": "user", "content": body.content})

    user_msg = ChatMessage(id=_new_id(), conversation_id=conv_id, role="user",
                           content=body.content, status="completed")
    assistant = ChatMessage(id=_new_id(), conversation_id=conv_id, role="assistant",
                            content="", status="generating", model=body.model)
    db.add(user_msg)
    db.add(assistant)
    conv.updated_at = utcnow()
    db.commit()
    assistant_id = assistant.id

    from app.models_mgmt.runtime_policy import get_policy, model_output_tokens

    chat_defaults = get_policy().chat
    params = {"base_url": body.base_url, "model": body.model, "mode": body.mode,
              "engine": body.engine, "searxng_url": body.searxng_url,
              "thinking": body.thinking or chat_defaults.reasoning,
              "max_output_tokens": model_output_tokens(body.base_url, body.model),
              "attachments": body.attachments,
              "code_project": body.code_project,
              "code_project_path": body.code_project_path,
              "plan": body.plan.model_dump() if body.plan is not None else None}
    label = {"auto": "自動判定", "chat": "チャット生成", "web": "Web検索", "academic": "学術検索",
             "deep": "Deepサーチ", "research": "複合調査", "code": "OpenCode"}.get(body.mode, "生成")
    job = jobs.create(
        "chat.completion", f"{label}: {body.content[:40]}",
        lambda j: _run_chat_job(j, assistant_id, conv_id, history, params),
        owner_user_id=user.id, idempotency_key=assistant_id, priority=10,
    )
    # assistant メッセージに job_id を紐付け（再接続時に購読を再開できる）
    db.query(ChatMessage).filter(ChatMessage.id == assistant_id).update({"job_id": job.id})
    db.commit()
    return {"user_message_id": user_msg.id, "assistant_message_id": assistant_id, "job_id": job.id, "status": "generating"}


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
        saved_status = msg.status if msg else "failed"
    finally:
        db.close()
    await websocket.accept()
    if msg is None:
        await websocket.send_text(json.dumps({"type": "error", "message": "メッセージが見つかりません"}))
        await websocket.close()
        return

    # まず現在までの保存済み内容を送る（再接続時の即時復元）。snapshotを送った場合は
    # それ以前のdeltaを再送しない。最終snapshotでもDB内容へ必ず収束させる。
    if saved_content:
        await websocket.send_text(json.dumps({"type": "snapshot", "content": saved_content, "thinking": saved_thinking}))

    job = jobs.get(job_id) if job_id else None
    if job is None:
        # ジョブがメモリに無い = 完了済み or 再起動で消失。DB の最終状態を返す
        await websocket.send_text(json.dumps({"type": "done", "status": saved_status}))
        await websocket.close()
        return

    try:
        cursor = job.event_sequence if saved_content else job.event_offset
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
                            "thinking": current.thinking or "",
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
                elif ev.get("message") == "plan":
                    await websocket.send_text(json.dumps({"type": "plan", "plan": ev.get("plan", {})}, ensure_ascii=False))
                elif ev.get("message") == "progress":
                    await websocket.send_text(json.dumps({
                        "type": "progress", "phase": ev.get("phase", ""), "label": ev.get("label", ""),
                        "iteration": ev.get("iteration", 0), "details": ev.get("details", {}),
                    }, ensure_ascii=False))
                elif ev.get("message") == "stats":
                    await websocket.send_text(json.dumps({
                        "type": "stats", "phase": ev.get("phase", ""),
                        "tok_per_sec": ev.get("tok_per_sec"), "gen_tokens": ev.get("gen_tokens"),
                        "prompt_tokens": ev.get("prompt_tokens"), "context_max": ev.get("context_max"),
                    }))
            cursor = next_cursor
            if job.status not in ("queued", "running") and cursor >= job.event_sequence:
                break
        # checkpointはジョブ完了より先にcommitされる。最後に全文を再送し、再接続や
        # journal切詰めがあっても表示をDBの正本へ収束させる。
        with SessionLocal() as final_db:
            final = final_db.get(ChatMessage, message_id)
            if final is not None:
                await websocket.send_text(json.dumps({
                    "type": "snapshot", "content": final.content,
                    "thinking": final.thinking or "",
                }, ensure_ascii=False))
        await websocket.send_text(json.dumps({"type": "done", "status": job.status, "error": job.error}))
    except WebSocketDisconnect:
        return  # 切断してもジョブは継続（DB に保存され続ける）
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass
