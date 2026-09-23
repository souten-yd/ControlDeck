"""バックグラウンドジョブの参照・キャンセル API（メモリ + DB 永続化）。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.jobs import service as jobs
from app.integrations.opencode import recovery as opencode_recovery
from app.models import User
from app.security.deps import authenticate_websocket, require_permission
from app.websocket_tasks import run_websocket_tasks

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    kind: str = "", limit: int = 30,
    user: User = Depends(require_permission("workflows.run")),
):
    # メモリ（実行中）+ DB（履歴・再起動後も残る）を統合。events は一覧では省く
    items = await jobs.list_any(kind, max(1, min(limit, 100)), user.id)
    await opencode_recovery.reconcile(items)
    for it in items:
        it["event_count"] = int(it.get("event_count") or len(it.get("events", [])))
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
    await opencode_recovery.reconcile([persisted])
    return persisted


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    job = jobs.get(job_id)
    record = job if job is not None else await jobs.get_any(job_id)
    if record is None or not jobs.visible_to(record, user.id):
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    if job is None:
        try:
            canceled = await opencode_recovery.cancel_surviving(record)
        except opencode_recovery.RecoveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:
        canceled = None
    if job is not None and job.kind == "chat.completion":
        from app.models_mgmt.runtime_provider import cancel_request

        await cancel_request(job_id)
    if canceled is None:
        canceled = await jobs.cancel_and_wait(job_id)
    if not canceled:
        raise HTTPException(status_code=409, detail="実行中のジョブではありません")
    await asyncio.to_thread(audit.record, db, "job.cancel", user=user, resource_type="job", resource_id=job_id, request=request)
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
    seen: dict[str, tuple] = {}

    def signature(item: dict) -> tuple:
        return (int(item.get("revision") or 0), item.get("status"), item.get("phase"), item.get("error"))
    try:
        initial = await jobs.list_any(kind, 100, user_id)
        await opencode_recovery.reconcile(initial)
        external = any(item.get("phase") in {"external_running", "external_status_unknown"} for item in initial)
        for item in initial:
            item["events"] = []
            seen[item["id"]] = signature(item)
        await websocket.send_text(json.dumps({"type": "snapshot", "jobs": initial}, ensure_ascii=False))
        revision = jobs.stream_revision()
        while True:
            # sendだけのWSはクライアントcloseを検知できず、Uvicorn終了時にhandlerが
            # 残り続ける。更新通知とASGIのdisconnectを同時に待つ。
            changed_revision: int | None = None
            message: dict[str, object] | None = None

            async def changed() -> None:
                nonlocal changed_revision
                changed_revision = await jobs.wait_global(revision, 2 if external else 25)

            async def incoming() -> None:
                nonlocal message
                message = await websocket.receive()

            await run_websocket_tasks(changed, incoming)
            if message is not None:
                if message.get("type") == "websocket.disconnect":
                    return
                # client messageは不要。受信した場合は次の更新/切断待ちへ戻る。
                if changed_revision is None:
                    continue
            assert changed_revision is not None
            revision = changed_revision
            # token/event ごとの更新を100ms単位で束ね、DB参照とWSフレームを抑える。
            # 最終状態はrevision比較で欠落せず、UI上の遅延も知覚しにくい範囲に留める。
            await asyncio.sleep(0.1)
            revision = jobs.stream_revision()
            current = await jobs.list_any(kind, 100, user_id)
            await opencode_recovery.reconcile(current)
            external = any(item.get("phase") in {"external_running", "external_status_unknown"} for item in current)
            for item in current:
                item_revision = signature(item)
                if seen.get(item["id"]) == item_revision:
                    continue
                seen[item["id"]] = item_revision
                item["events"] = []
                await websocket.send_text(json.dumps({"type": "update", "job": item}, ensure_ascii=False))
    except WebSocketDisconnect:
        return
