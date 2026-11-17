"""診断の API。管理者（settings.manage）だけ。"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import get_db
from app.diagnostics import journal, recorder, report, selftest, store
from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])
ADMIN = require_permission("settings.manage")


@router.get("/summary")
async def summary(hours: float = Query(24.0, ge=1, le=168), user: User = Depends(ADMIN)):
    """画面の「概要」: 自己診断・再起動・OOM・詰まり・遅い API を一度に返す。"""
    del user
    return await asyncio.to_thread(report.build, hours)


@router.get("/events")
def events(hours: float = Query(24.0, ge=0.1, le=168), kind: str = Query("", max_length=40),
           limit: int = Query(300, ge=1, le=2000), user: User = Depends(ADMIN)):
    del user
    kinds = {k for k in kind.split(",") if k} or None
    return {"events": [report.redact_event(e) for e in store.read(since=time.time() - hours * 3600, kinds=kinds, limit=limit)]}


@router.get("/memory")
def memory(hours: float = Query(6.0, ge=0.1, le=24), user: User = Depends(ADMIN)):
    del user
    return {"history": recorder.memory_history(since=time.time() - hours * 3600), "now": recorder.memory_sample(top=10)}


@router.get("/journal")
async def journal_summary(hours: float = Query(72.0, ge=1, le=336), user: User = Depends(ADMIN)):
    del user
    return await asyncio.to_thread(journal.summary, hours)


@router.post("/selftest")
async def run_selftest(request: Request, user: User = Depends(ADMIN), db=Depends(get_db)):
    result = await asyncio.to_thread(selftest.run)
    audit.record(db, "diagnostics.selftest", user=user, resource_type="system", resource_id="diagnostics",
                 request=request, metadata={"overall": result["overall"]})
    return result


class TestsBody(BaseModel):
    # 空なら全部。ファイルやテスト名（tests/test_x.py::test_y）を空白区切りで
    selection: str = Field(default="", max_length=500)


@router.post("/tests", status_code=201)
async def run_tests(body: TestsBody, request: Request, user: User = Depends(ADMIN), db=Depends(get_db)):
    """backend のテストをジョブで流す（本番のデータには触らない）。数分かかる。"""
    _ensure_selection_ok(body.selection)

    async def run(job: jobs.Job) -> dict:
        job.set_progress("テストを実行中", 0, 1)
        result = await asyncio.to_thread(selftest.run_tests, body.selection)
        job.set_progress("完了", 1, 1)
        return result

    job = jobs.create("diagnostics.tests", "診断: テストの実行", run, owner_user_id=user.id)
    audit.record(db, "diagnostics.tests", user=user, resource_type="system", resource_id="diagnostics",
                 request=request, metadata={"job_id": job.id, "selection": body.selection[:200]})
    return {"job_id": job.id}


@router.get("/report")
async def download_report(hours: float = Query(24.0, ge=1, le=168), format: str = Query("md", pattern="^(md|json)$"),
                          request: Request = None, user: User = Depends(ADMIN), db=Depends(get_db)):
    """AI に渡すためのレポート（秘密は伏せてある）。"""
    data = await asyncio.to_thread(report.build, hours)
    audit.record(db, "diagnostics.report", user=user, resource_type="system", resource_id="diagnostics",
                 request=request, metadata={"hours": hours, "format": format})
    if format == "json":
        return data
    return PlainTextResponse(report.to_markdown(data), media_type="text/markdown; charset=utf-8")


def _ensure_selection_ok(selection: str) -> None:
    if selection and not all(ch.isalnum() or ch in "/_.:- " for ch in selection):
        raise HTTPException(status_code=422, detail="テストの指定に使えない文字があります")
