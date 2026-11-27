"""永続チャットの送信待ちの要望（生成中に送ったもの）。

chat_persist.py から移しただけ（振る舞いは変えない）。chat_persist が `include_router` で取り込み、
元の名前は chat_persist からも使える（再 export）。chat_persist に残る関数は `cp.<名前>` で実行時に参照する
（テストが chat_persist の名前を差し替えても効くように）。chat_persist の途中から import される前提なので、
このモジュールを先に単独で import しないこと（chat_persist 経由で読み込む）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.jobs import service as jobs
from app.models import ChatMessage, ChatQueuedRequest, Conversation, User
from app.security.deps import require_permission
from app.workflows import chat_persist as cp
from app.workflows.chat_attachments import _ATTACHMENT_ID_RE

logger = logging.getLogger("control_deck.chat_persist")

router = APIRouter()   # prefix・tags は取り込む側（chat_persist の router）が付ける

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
    skip = {"content", "attachments", "mode_choice", "research_depth", "agent_choice", "model_choice"}   # 画面の記録は送り方に関係しない
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
                    # 画面の選択は、待っている間に変わることがあるので記録しない（古い選択で上書きしない）
                    body = cp.SendBody.model_validate({**first, "content": "\n\n".join(i.content for i in group),
                                                    "attachments": attachments, "mode_choice": "",
                                                    "research_depth": "", "agent_choice": "", "model_choice": ""})
                    result = await cp._start_send(db, conv, body, conv.owner_user_id)
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
    cp._owned_conversation(db, conv_id, user)
    items = _queued(db, conv_id, ("queued", "failed"))
    generating = _generating_id(db, conv_id)
    return {"items": [_queue_out(i) for i in items], "generating_message_id": generating,
            # 生成中でないのに残っている＝止めた・失敗した後で保留している
            "held": bool(not generating and any(i.status == "queued" for i in items))}


class QueueBody(cp.SendBody):
    # 画面がまだ前の発言を送っている途中（自動判定・ワークフロー）なら、すぐには送らない（前の発言より先に
    # 送られて順番が入れ替わっていた。レビュー指摘）。その発言が終わったときに送る
    hold: bool = False


@router.post("/conversations/{conv_id}/queue", status_code=201)
async def add_queue(conv_id: str, body: QueueBody, user: User = Depends(require_permission("workflows.run")),
                    db: Session = Depends(get_db)):
    cp._owned_conversation(db, conv_id, user)
    if body.mode not in {"auto", "chat", "web", "academic", "deep", "research", "code"}:
        raise HTTPException(status_code=422, detail="未対応のチャットモードです")
    if body.mode == "code" and any(_ATTACHMENT_ID_RE.fullmatch(str(a)) for a in body.attachments):
        problem = await asyncio.to_thread(cp.opencode_image_problem)   # 送るときではなく、入れたときに知らせる
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    if len(_queued(db, conv_id, ("queued", "failed"))) >= QUEUE_LIMIT:
        raise HTTPException(status_code=409, detail=f"送信待ちは {QUEUE_LIMIT} 件までです")
    item = ChatQueuedRequest(id=cp._new_id(), conversation_id=conv_id, content=body.content,
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
    cp._owned_conversation(db, conv_id, user)
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


@router.post("/conversations/{conv_id}/queue/{item_id}/steer")
async def steer_queue(conv_id: str, item_id: str,
                      user: User = Depends(require_permission("workflows.run")), db: Session = Depends(get_db)):
    """送信待ちの要望を、作業中の OpenCode へ今すぐ伝える（今の道具が終わったところで届く）。"""
    from app.integrations.opencode import chat_server

    async with _queue_locks.setdefault(conv_id, asyncio.Lock()):
        db.expire_all()
        item = _queue_item(db, conv_id, item_id, user)
        if item.status != "queued":
            raise HTTPException(status_code=409, detail="もう送りました")
        request_body = json.loads(item.body_json or "{}")
        if request_body.get("attachments"):
            raise HTTPException(status_code=409, detail="添付のある要望は、回答が終わってから送ります")
        running = db.execute(select(ChatMessage).where(
            ChatMessage.conversation_id == conv_id, ChatMessage.role == "assistant", ChatMessage.status == "generating",
        ).order_by(ChatMessage.created_at.desc())).scalars().first()
        steer = cp._steer_targets.get(running.id) if running else None
        if steer is None:
            raise HTTPException(status_code=409, detail="今は伝えられません。回答が終わったら送ります")
        # 動いている回と同じ送り先の要望だけ（別の機能・フォルダ・プランの要望を混ぜない）
        running_meta = json.loads(running.meta_json or "{}")
        folder = Path(str(running_meta.get("project_path") or ""))
        wanted_name, wanted_path = str(request_body.get("code_project") or ""), str(request_body.get("code_project_path") or "")
        same_folder = (not wanted_name or wanted_name == folder.name) and (not wanted_path or cp._same_folder(wanted_path, folder))
        if request_body.get("mode") != "code" or request_body.get("code_research") or not same_folder \
                or str(request_body.get("code_agent") or "build") != str(running_meta.get("agent") or "build"):
            raise HTTPException(status_code=409, detail="この要望は送り先が違うので、回答が終わってから送ります")
        try:
            await steer(item.content)
        except chat_server.ChatServerError as exc:
            raise HTTPException(status_code=409, detail=f"今は伝えられません（{exc}）") from exc
        item.status = "sent"     # 回答の中に「途中で伝えた」として残る
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
        cp._owned_conversation(db, conv_id, user)
        items = _queued(db, conv_id, ("queued", "failed"))
        for item in items:
            item.status = "deleted"
        db.commit()
        return {"ids": [i.id for i in items]}


@router.post("/conversations/{conv_id}/queue/dispatch")
async def dispatch_queue_now(conv_id: str, user: User = Depends(require_permission("workflows.run")),
                             db: Session = Depends(get_db)):
    """保留している送信待ちを今すぐ送る（止めた・失敗した後）。"""
    cp._owned_conversation(db, conv_id, user)
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
