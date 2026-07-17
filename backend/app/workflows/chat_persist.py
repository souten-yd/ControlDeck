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

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.audit import service as audit
from app.jobs import service as jobs
from app.models import ChatMessage, Conversation, User
from app.models import utcnow
from app.schemas.assistant import AssistantPlan, ResearchStep
from app.security.deps import authenticate_websocket, require_permission
from app.workflows.chat_router import _keep_alive, _think_for

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
    mode: str = "chat"  # auto / chat / web / academic / deep / research
    plan: AssistantPlan | None = None
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "llama3.2"
    engine: str = "duckduckgo"  # web/deep 用
    searxng_url: str = ""
    system: str = "あなたは Control Deck の AI アシスタントです。日本語で簡潔に答えてください。"
    thinking: str | None = None  # off / auto / on。省略時はruntime共通設定。
    max_output_tokens: int | None = Field(default=None, ge=64, le=131072)


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
    max_output_tokens = int(params.get("max_output_tokens", 2048))
    from app.models_mgmt.runtime_provider import RuntimeChatRequest, provider_for_base_url
    think = False if thinking_mode == "off" else _think_for(model)
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
                search_history = await _server_research(job, buf, query, params, plan)
            else:
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
        runtime_request = RuntimeChatRequest(
            base_url=base_url, model=model, messages=history,
            max_tokens=max_output_tokens, thinking=think,
            disable_thinking=thinking_mode == "off", keep_alive=_keep_alive(),
        )
        async for chunk in provider.stream_chat(runtime_request, request_id=request_id):
            if chunk.type == "thinking":
                buf["thinking"] += chunk.content
                job.log("thinking", delta=chunk.content)
            elif chunk.type == "content":
                buf["content"] += chunk.content
                job.log("delta", delta=chunk.content)
                await maybe_ckpt()
        await asyncio.to_thread(checkpoint, True, "completed")
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


def _source_key(source: dict) -> str:
    return str(source.get("url") or source.get("title") or "").strip().lower()


async def _server_research(
    job: jobs.Job, buf: dict, query: str, params: dict, plan: AssistantPlan | None,
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

    buf["meta"] = {
        **buf.get("meta", {}), "mode": "research", "sources": sources,
        "iterations": iteration_count,
    }
    job.log("sources", sources=sources)
    progress("summarize", f"{len(sources)}件の出典を要約中", iteration=min(max_iterations, 5))
    context = "\n\n".join(f"[{index}] {item}" for index, item in enumerate(evidence, 1))
    return [
        {"role": "system", "content":
         "以下はWeb・学術検索を組み合わせて収集した根拠です。利用者の依頼へ日本語で要約し、"
         "主要な主張には必ず [番号] を付けてください。根拠にない内容は推測と明記してください。\n\n" + context},
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
    allowed_modes = {"auto", "chat", "web", "academic", "deep", "research"}
    if body.mode not in allowed_modes:
        raise HTTPException(status_code=422, detail="未対応のチャットモードです")
    if body.plan is not None and body.mode not in ("auto", body.plan.mode):
        raise HTTPException(status_code=422, detail="モードと調査計画が一致しません")
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
              "max_output_tokens": body.max_output_tokens or chat_defaults.max_output_tokens,
              "plan": body.plan.model_dump() if body.plan is not None else None}
    label = {"auto": "自動判定", "chat": "チャット生成", "web": "Web検索", "academic": "学術検索",
             "deep": "Deepサーチ", "research": "複合調査"}.get(body.mode, "生成")
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
                        "iteration": ev.get("iteration", 0),
                    }, ensure_ascii=False))
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
