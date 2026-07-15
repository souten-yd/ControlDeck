"""バックグラウンドジョブの参照・キャンセル API（メモリ + DB 永続化）。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.jobs import service as jobs
from app.models import User
from app.security.deps import authenticate_websocket, require_permission

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    kind: str = "", limit: int = 30,
    user: User = Depends(require_permission("workflows.run")),
):
    # メモリ（実行中）+ DB（履歴・再起動後も残る）を統合。events は一覧では省く
    items = await jobs.list_any(kind, max(1, min(limit, 100)), user.id)
    for it in items:
        it["event_count"] = len(it.get("events", []))
        it["events"] = []
    return items


@router.get("/{job_id}")
async def get_job(
    job_id: str, events_from: int = 0,
    user: User = Depends(require_permission("workflows.run")),
):
    job = jobs.get(job_id)
    if job is not None:
        if not jobs.visible_to(job, user.id):
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")
        return job.to_dict(with_events_from=max(0, events_from))
    # メモリに無ければ DB から（再起動後の履歴。interrupted 等も見える）
    persisted = await jobs.get_any(job_id)
    if persisted is None or not jobs.visible_to(persisted, user.id):
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return persisted


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    job = jobs.get(job_id)
    if job is None or not jobs.visible_to(job, user.id):
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    if job.kind == "chat.completion":
        from app.models_mgmt.runtime_provider import cancel_request

        await cancel_request(job_id)
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=409, detail="実行中のジョブではありません")
    audit.record(db, "job.cancel", user=user, resource_type="job", resource_id=job_id, request=request)
    return {"ok": True}


@router.websocket("/stream")
async def stream_jobs(websocket: WebSocket, kind: str = ""):
    """所有者本人/system jobのsnapshotと更新だけを通知する全体stream。"""
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "workflows.run")
        if user is None:
            return
        user_id = user.id
    finally:
        db.close()
    await websocket.accept()
    seen: dict[str, int] = {}
    try:
        initial = await jobs.list_any(kind, 100, user_id)
        for item in initial:
            item["events"] = []
            seen[item["id"]] = int(item.get("revision") or 0)
        await websocket.send_text(json.dumps({"type": "snapshot", "jobs": initial}, ensure_ascii=False))
        revision = jobs.stream_revision()
        while True:
            # sendだけのWSはクライアントcloseを検知できず、Uvicorn終了時にhandlerが
            # 残り続ける。更新通知とASGIのdisconnectを同時に待つ。
            changed = asyncio.create_task(jobs.wait_global(revision))
            incoming = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                (changed, incoming), return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if incoming in done:
                message = incoming.result()
                if message.get("type") == "websocket.disconnect":
                    return
                # client messageは不要。受信した場合は次の更新/切断待ちへ戻る。
                if changed not in done:
                    continue
            revision = changed.result()
            # token/event ごとの更新を100ms単位で束ね、DB参照とWSフレームを抑える。
            # 最終状態はrevision比較で欠落せず、UI上の遅延も知覚しにくい範囲に留める。
            await asyncio.sleep(0.1)
            revision = jobs.stream_revision()
            current = await jobs.list_any(kind, 100, user_id)
            for item in current:
                item_revision = int(item.get("revision") or 0)
                if seen.get(item["id"]) == item_revision:
                    continue
                seen[item["id"]] = item_revision
                item["events"] = []
                await websocket.send_text(json.dumps({"type": "update", "job": item}, ensure_ascii=False))
    except WebSocketDisconnect:
        return
