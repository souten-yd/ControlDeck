"""バックグラウンドジョブの参照・キャンセル API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    kind: str = "", limit: int = 30,
    user: User = Depends(require_permission("workflows.run")),
):
    return [j.to_dict(with_events_from=len(j.events)) | {"events": []} for j in jobs.list_jobs(kind, min(limit, 100))]


@router.get("/{job_id}")
def get_job(
    job_id: str, events_from: int = 0,
    user: User = Depends(require_permission("workflows.run")),
):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません（サーバー再起動で履歴は消えます）")
    return job.to_dict(with_events_from=max(0, events_from))


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require_permission("workflows.edit"))):
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=409, detail="実行中のジョブではありません")
    return {"ok": True}
