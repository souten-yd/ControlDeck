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
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.audit import service as audit
from app.jobs import service as jobs
from app.models import ChatMessage, Conversation, User
from app.models import utcnow
from app.security.deps import authenticate_websocket, require_permission
from app.workflows.chat_router import _keep_alive, _native_base, _think_for

router = APIRouter(prefix="/chat", tags=["chat-persist"])

# 部分出力のチェックポイント間隔（秒）。毎トークン DB 書き込みはしない
CHECKPOINT_SEC = 1.0


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


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


# ---- 送信（サーバー側生成ジョブ） ----


class SendBody(BaseModel):
    content: str = Field(min_length=1, max_length=32000)
    mode: str = "chat"  # chat / web / academic / deep（全てサーバー側ジョブで処理）
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.2"
    engine: str = "duckduckgo"  # web/deep 用
    searxng_url: str = ""
    system: str = "あなたは Control Deck の AI アシスタントです。日本語で簡潔に答えてください。"
    thinking: str | None = None  # off / auto / on。省略時はruntime共通設定。
    max_output_tokens: int | None = Field(default=None, ge=64, le=131072)


async def _run_chat_job(job: jobs.Job, assistant_id: str, conv_id: str,
                        history: list[dict], params: dict) -> dict:
    """サーバー側で検索/生成し、assistant メッセージへ部分出力を保存する（全モード対応）。"""
    base_url = params["base_url"]
    model = params["model"]
    mode = params.get("mode", "chat")
    thinking_mode = str(params.get("thinking", "off"))
    max_output_tokens = int(params.get("max_output_tokens", 2048))
    from app.models_mgmt.runtime_policy import ensure_gpu_profile
    think = False if thinking_mode == "off" else _think_for(model)
    native = _native_base(base_url) if think is not None else None
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

    # ---- 検索モード（web/academic/deep）: サーバー側で検索し履歴を組み立てる ----
    if mode in ("web", "academic", "deep"):
        try:
            query = history[-1]["content"]
            search_history = await _server_search(job, buf, mode, query, params)
            if search_history is None:  # deep はここで完結（レポートを content に保存済み）
                await asyncio.to_thread(checkpoint, True, "completed")
                await asyncio.to_thread(_maybe_title, conv_id)
                return {"assistant_message_id": assistant_id}
            history = search_history
        except asyncio.CancelledError:
            await asyncio.to_thread(checkpoint, True, "canceled", "キャンセルされました")
            raise
        except Exception as e:
            await asyncio.to_thread(checkpoint, True, "failed", f"{type(e).__name__}: {e}")
            raise

    try:
        await asyncio.to_thread(ensure_gpu_profile)
        if native is not None:
            payload = {"model": model, "messages": history, "stream": True,
                       "think": think, "keep_alive": _keep_alive(),
                       "options": {"num_predict": max_output_tokens}}
            url = native + "/api/chat"
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, json=payload) as r:
                    if r.status_code >= 400:
                        raise RuntimeError(f"LLM エラー {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        msg = json.loads(line).get("message", {})
                        if msg.get("thinking"):
                            buf["thinking"] += msg["thinking"]
                            job.log("thinking", delta=msg["thinking"])
                        if msg.get("content"):
                            buf["content"] += msg["content"]
                            job.log("delta", delta=msg["content"])
                        await maybe_ckpt()
        else:
            payload = {"model": model, "messages": history, "stream": True,
                       "keep_alive": _keep_alive(), "max_tokens": max_output_tokens}
            if thinking_mode == "off":
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            url = base_url.rstrip("/") + "/chat/completions"
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, json=payload,
                                         headers={"Authorization": "Bearer sk-no-key"}) as r:
                    if r.status_code >= 400:
                        raise RuntimeError(f"LLM エラー {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            item = json.loads(data)["choices"][0]["delta"]
                            reasoning = item.get("reasoning_content", "")
                            delta = item.get("content", "")
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        if reasoning:
                            buf["thinking"] += reasoning
                            job.log("thinking", delta=reasoning)
                        if delta:
                            buf["content"] += delta
                            job.log("delta", delta=delta)
                            await maybe_ckpt()
        await asyncio.to_thread(checkpoint, True, "completed")
        # 会話タイトルを最初の user 発話から自動設定
        await asyncio.to_thread(_maybe_title, conv_id)
        return {"assistant_message_id": assistant_id, "chars": len(buf["content"])}
    except asyncio.CancelledError:
        await asyncio.to_thread(checkpoint, True, "canceled", "キャンセルされました")
        raise
    except Exception as e:
        await asyncio.to_thread(checkpoint, True, "failed", f"{type(e).__name__}: {e}")
        raise


async def _server_search(job: jobs.Job, buf: dict, mode: str, query: str, params: dict):
    """Web/学術/Deep 検索をサーバー側で実行。web/academic は LLM 生成用の history を返し、
    deep はレポートを buf["content"] に保存して None を返す（呼び出し側で完結）。"""
    from app.workflows import chat_router as cr
    from app.workflows import external_search as ext

    job.log("delta", delta="")  # ストリーム開始マーカー
    if mode == "academic":
        fed = await ext.federated(query, 8)
        results = fed["results"][:12]
        sources = [{"title": r.get("title", ""), "url": r.get("url", ""), "source": r.get("source", "")} for r in results]
        buf["meta"] = {"mode": "academic", "sources": sources}
        job.log("sources", sources=sources)
        ctx = "\n\n".join(f"[{i+1}] {r.get('title','')}\n{r.get('snippet','')[:400]}\n{r.get('url','')}"
                          for i, r in enumerate(results))
        return [
            {"role": "system", "content":
             "以下の学術検索結果を根拠に日本語で回答してください。主張には [番号] で出典を付けること。\n\n" + ctx},
            {"role": "user", "content": query},
        ]

    # web / deep 共通の Web 検索パラメータ
    sb = cr.SearchBody(query=query, mode=mode, engine=params.get("engine", "duckduckgo"),
                       searxng_url=params.get("searxng_url", ""),
                       base_url=params["base_url"], model=params["model"])
    if mode == "web":
        results = await cr._web_results(sb, query, 8)
        sources = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results]
        buf["meta"] = {"mode": "web", "sources": sources}
        job.log("sources", sources=sources)
        ctx = "\n\n".join(f"[{i+1}] {r['title']}\n{r.get('snippet','')}\n{r['url']}" for i, r in enumerate(results))
        return [
            {"role": "system", "content":
             "以下の検索結果を根拠に日本語で回答してください。主張には [番号] で出典を付けること。"
             "検索結果にない内容は推測と明示すること。\n\n" + ctx},
            {"role": "user", "content": query},
        ]

    # deep: 既存の Deep サーチ（分解→収集→引用付きレポート）をサーバー側で実行
    res = await cr._deep_search(sb)  # {"report","sources"}
    buf["content"] = res["report"]
    buf["meta"] = {"mode": "deep", "sources": res.get("sources", [])}
    job.log("delta", delta=res["report"])
    job.log("sources", sources=res.get("sources", []))
    return None


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
    # 履歴（system + 過去 + 今回）
    past = db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conv_id).order_by(ChatMessage.created_at)
    ).scalars().all()
    history = [{"role": "system", "content": body.system}]
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

    from app.models_mgmt.runtime_policy import get_policy

    chat_defaults = get_policy().chat
    params = {"base_url": body.base_url, "model": body.model, "mode": body.mode,
              "engine": body.engine, "searxng_url": body.searxng_url,
              "thinking": body.thinking or chat_defaults.reasoning,
              "max_output_tokens": body.max_output_tokens or chat_defaults.max_output_tokens}
    label = {"chat": "チャット生成", "web": "Web検索", "academic": "学術検索", "deep": "Deepサーチ"}.get(body.mode, "生成")
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

    # まず現在までの保存済み内容を送る（再接続時の即時復元）
    if saved_content:
        await websocket.send_text(json.dumps({"type": "snapshot", "content": saved_content, "thinking": saved_thinking}))

    job = jobs.get(job_id) if job_id else None
    if job is None:
        # ジョブがメモリに無い = 完了済み or 再起動で消失。DB の最終状態を返す
        await websocket.send_text(json.dumps({"type": "done", "status": saved_status}))
        await websocket.close()
        return

    try:
        idx = 0
        while True:
            new_len = await jobs.wait_events(job, idx)
            for ev in job.events[idx:new_len]:
                if ev.get("message") == "delta":
                    await websocket.send_text(json.dumps({"type": "delta", "content": ev.get("delta", "")}))
                elif ev.get("message") == "thinking":
                    await websocket.send_text(json.dumps({"type": "thinking", "content": ev.get("delta", "")}))
                elif ev.get("message") == "sources":
                    await websocket.send_text(json.dumps({"type": "sources", "sources": ev.get("sources", [])}, ensure_ascii=False))
            idx = new_len
            if job.status not in ("queued", "running") and idx >= len(job.events):
                break
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
