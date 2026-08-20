from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

app = FastAPI(title="Control Deck Fake Add-on", version="2.0")

_HEALTH_STATES = {"healthy", "degraded", "unavailable", "setup_required"}
_health_state = "healthy"
_video_available = True


class HealthUpdate(BaseModel):
    status: str
    video_available: bool = True


class FakeGpuJobRequest(BaseModel):
    duration_sec: float = Field(default=1.0, ge=0.05, le=300)
    vram_bytes: int = Field(default=1024**3, ge=1, le=128 * 1024**3)


@dataclass
class FakeGpuJob:
    id: str
    duration_sec: float
    vram_bytes: int
    status: str = "queued"
    progress: float = 0.0
    created_at: float = 0.0
    cancel_requested: bool = False

    def public(self) -> dict:
        return {
            "id": self.id,
            "duration_sec": self.duration_sec,
            "vram_bytes": self.vram_bytes,
            "status": self.status,
            "progress": round(self.progress, 3),
            "cancel_requested": self.cancel_requested,
        }


_jobs: dict[str, FakeGpuJob] = {}
_tasks: dict[str, asyncio.Task[None]] = {}


def _availability() -> dict:
    video = "available" if _video_available else {
        "state": "unavailable",
        "reason_code": "worker_not_installed",
        "message": "Video worker is not installed",
        "action": {"kind": "open_route", "route": "/x/fake-addon/settings"},
    }
    return {
        "navigation:workspace": "available",
        "embedded_view:workspace": "available",
        "workflow_executor:fake.generate": "available",
        "workflow_executor:fake.video": video,
    }


def _setup() -> list[dict]:
    if _health_state != "setup_required":
        return []
    return [
        {"id": "service", "label": "Fake service", "state": "ok"},
        {
            "id": "model",
            "label": "Fake model",
            "state": "missing",
            "message": "Install the fake model to continue",
            "action": {"kind": "open_route", "route": "/x/fake-addon/settings"},
        },
    ]


@app.get("/health")
async def health() -> dict:
    return {
        "status": _health_state,
        "contract_version": "2.0",
        "contributions": _availability(),
        "setup": _setup(),
    }


@app.post("/test/health")
async def set_health(update: HealthUpdate) -> dict:
    global _health_state, _video_available
    if update.status not in _HEALTH_STATES:
        raise HTTPException(status_code=422, detail="unsupported fake health state")
    _health_state = update.status
    _video_available = update.video_available
    return await health()


async def _run_gpu_job(job: FakeGpuJob) -> None:
    job.status = "running"
    started = time.monotonic()
    try:
        while True:
            if job.cancel_requested:
                job.status = "canceled"
                return
            elapsed = time.monotonic() - started
            job.progress = min(1.0, elapsed / job.duration_sec)
            if job.progress >= 1.0:
                job.status = "succeeded"
                return
            await asyncio.sleep(min(0.05, job.duration_sec / 10))
    except asyncio.CancelledError:
        job.status = "canceled"
        raise


@app.post("/fake-gpu/jobs", status_code=202)
async def create_gpu_job(request: FakeGpuJobRequest, response: Response) -> dict:
    if len(_jobs) >= 100:
        raise HTTPException(status_code=429, detail="fake job capacity reached")
    job_id = str(uuid.uuid4())
    job = FakeGpuJob(
        id=job_id,
        duration_sec=request.duration_sec,
        vram_bytes=request.vram_bytes,
        created_at=time.time(),
    )
    _jobs[job_id] = job
    _tasks[job_id] = asyncio.create_task(_run_gpu_job(job))
    response.headers["Location"] = f"/fake-gpu/jobs/{job_id}"
    return job.public()


@app.get("/fake-gpu/jobs/{job_id}")
async def get_gpu_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="fake job not found")
    return job.public()


@app.delete("/fake-gpu/jobs/{job_id}")
async def cancel_gpu_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="fake job not found")
    if job.status in {"queued", "running"}:
        job.cancel_requested = True
    return job.public()


@app.post("/commands/generate")
@app.post("/workflow/execute")
async def execute(payload: dict) -> dict:
    return {"ok": True, "echo": payload}


@app.post("/agent/execute")
async def agent_execute(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": f"fake-addon received {len(payload)} fields"}]}


@app.post("/context/inspect")
async def context_inspect(payload: dict) -> dict:
    return {"summary": "fake context", "keys": sorted(payload)}


@app.get("/schemas/workflow-input")
@app.get("/schemas/agent-tool")
async def input_schema() -> dict:
    return {"type": "object", "additionalProperties": True}


@app.get("/schemas/workflow-output")
async def output_schema() -> dict:
    return {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
