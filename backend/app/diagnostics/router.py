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
    return {"events": report.redact_value(store.read(since=time.time() - hours * 3600, kinds=kinds, limit=limit))}


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
    try:
        selftest.check_selection(body.selection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if selftest.tests_running():
        raise HTTPException(status_code=409, detail="テストはもう実行中です")

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


class ClientTiming(BaseModel):
    """画面側で測った時間（ミリ秒）。携帯の「遅い」を、通信・API・描画に分けて残す。"""
    page: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    marks: dict[str, float] = Field(default_factory=dict, max_length=20)
    api: list[dict] = Field(default_factory=list, max_length=12)
    standalone: bool = False
    connection: str = Field(default="", max_length=20)


@router.post("/client", status_code=204)
def client_timing(body: ClientTiming, user: User = Depends(require_permission("workflows.run"))):
    del user
    marks = {k[:40]: round(float(v), 1) for k, v in body.marks.items() if 0 <= float(v) < 600_000}
    api = []
    for row in body.api[:12]:
        try:
            api.append({"path": str(row.get("path", ""))[:120], "ms": round(float(row.get("ms", 0)), 1),
                        "wait_ms": round(float(row.get("wait_ms", 0)), 1), "kb": round(float(row.get("kb", 0)), 1)})
        except (TypeError, ValueError):
            continue
    store.record("client_timing", page=body.page, marks=marks, api=api, standalone=body.standalone,
                 connection=body.connection, rate_key=f"client|{body.page}")

