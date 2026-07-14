"""バックグラウンドジョブの参照・キャンセル API（メモリ + DB 永続化）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    kind: str = "", limit: int = 30,
    user: User = Depends(require_permission("workflows.run")),
):
    # メモリ（実行中）+ DB（履歴・再起動後も残る）を統合。events は一覧では省く
    items = await jobs.list_any(kind, min(limit, 100))
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
        return job.to_dict(with_events_from=max(0, events_from))
    # メモリに無ければ DB から（再起動後の履歴。interrupted 等も見える）
    persisted = await jobs.get_any(job_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return persisted


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require_permission("workflows.edit"))):
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=409, detail="実行中のジョブではありません")
    return {"ok": True}
