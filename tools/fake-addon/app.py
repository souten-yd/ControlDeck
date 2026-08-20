from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Control Deck Fake Add-on", version="2.0")

_HEALTH_STATES = {"healthy", "degraded", "unavailable", "setup_required"}
_health_state = "healthy"
_video_available = True

_WORKSPACE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fake workspace</title><style>
:root{font-family:system-ui,sans-serif}body{margin:0;padding:24px;background:var(--bg,#fafafa);color:var(--text,#18181b)}
.card{max-width:640px;border:1px solid var(--border,#ddd);border-radius:14px;padding:20px;background:var(--surface,#fff)}
button{min-height:44px;margin:6px 6px 0 0;padding:0 14px;border:0;border-radius:10px;background:var(--accent,#2563eb);color:white;font-weight:600}
code{font-size:12px;color:var(--muted,#71717a)}
</style></head><body><main class="card"><h1>Fake embedded workspace</h1><p id="bridge-state">Connecting to host…</p>
<p><code id="route-state">/</code> · <code id="theme-state">unknown</code></p>
<button id="details">Open details route</button><button id="notify">Show notification</button><button id="busy">Toggle unsaved</button><button id="pick-file">Pick file</button><button id="pick-project">Pick project</button>
<p><code id="picker-state">No selection</code></p>
<p id="ws-state">WebSocket: connecting</p></main><script>
let port=null,nonce="",sequence=0,busy=false;document.documentElement.dataset.loadId=String(performance.timeOrigin);
function applyTheme(theme){for(const key of ["bg","surface","text","border","muted","accent"])document.documentElement.style.setProperty(`--${key}`,theme[key]);document.documentElement.dataset.scheme=theme.color_scheme;document.getElementById("theme-state").textContent=theme.color_scheme;}
function call(method,params={}){return new Promise((resolve,reject)=>{const id=`fake-${++sequence}`;const listener=(event)=>{const msg=event.data;if(msg?.type!=="response"||msg.id!==id)return;port.removeEventListener("message",listener);msg.ok?resolve(msg.result):reject(msg.error)};port.addEventListener("message",listener);port.postMessage({id,method,params,session_nonce:nonce})})}
window.addEventListener("message",event=>{if(event.source!==parent||event.data?.type!=="control-deck-host.connected"||!event.ports[0])return;port=event.ports[0];nonce=event.data.session_nonce;applyTheme(event.data.theme);port.onmessage=event=>{const msg=event.data;if(msg?.type!=="event")return;if(msg.event==="theme.changed")applyTheme(msg.data);if(msg.event==="route.changed")document.getElementById("route-state").textContent=msg.data.path;if(msg.event==="locale.changed")document.documentElement.dataset.locale=msg.data.locale;if(msg.event==="safe_area.changed")document.documentElement.dataset.safeArea=JSON.stringify(msg.data);if(msg.event==="session.updated")nonce=msg.data.session_nonce;if(msg.event==="disable.pending"){document.documentElement.dataset.disablePending="true";document.getElementById("bridge-state").textContent="Host is disabling this Add-on…";}};port.start();document.getElementById("bridge-state").textContent="Host Bridge ready";call("host.title.set",{title:"Fake workspace"});const frameRoot=location.pathname.split("/").slice(0,3).join("/");const ws=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}${frameRoot}/ws`,[`control-deck-bridge.${nonce}`]);ws.onmessage=event=>{document.getElementById("ws-state").textContent=`WebSocket: ${event.data}`};});
window.addEventListener("keydown",event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="k"&&port){event.preventDefault();port.postMessage({type:"shortcut",shortcut:"command_palette",session_nonce:nonce});}});
document.getElementById("details").onclick=()=>call("host.route.sync",{path:"/details"}).then(()=>document.getElementById("route-state").textContent="/details");
document.getElementById("notify").onclick=()=>call("host.notification.show",{title:"Fake Add-on",message:"Bridge notification",dedupe_key:"fake-notification"});
document.getElementById("busy").onclick=()=>{busy=!busy;call("host.busy.set",{busy})};
document.getElementById("pick-file").onclick=()=>call("host.file.pick",{mode:"file",title:"Fake Add-onへ渡すファイル"}).then(value=>document.getElementById("picker-state").textContent=`File grant: ${value.grant_id} (${value.name})`).catch(error=>document.getElementById("picker-state").textContent=error.code||String(error));
document.getElementById("pick-project").onclick=()=>call("host.project.pick",{title:"Fake Add-onへ渡すプロジェクト"}).then(value=>document.getElementById("picker-state").textContent=`Project: ${value.project_id} (${value.name})`).catch(error=>document.getElementById("picker-state").textContent=error.code||String(error));
window.parent.postMessage({type:"control-deck-addon.connect",bridge_version:"1.0"},"*");
</script></body></html>"""


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
        "embedded_view:silent": "available",
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


@app.get("/", response_class=HTMLResponse)
@app.get("/details", response_class=HTMLResponse)
async def workspace() -> str:
    return _WORKSPACE_HTML


@app.get("/silent", response_class=HTMLResponse)
async def silent_workspace() -> str:
    return "<!doctype html><html><body><p>Intentionally silent Bridge harness</p></body></html>"


@app.post("/test/health")
async def set_health(update: HealthUpdate) -> dict:
    global _health_state, _video_available
    if update.status not in _HEALTH_STATES:
        raise HTTPException(status_code=422, detail="unsupported fake health state")
    _health_state = update.status
    _video_available = update.video_available
    return await health()


@app.websocket("/ws")
async def echo_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready"})
    try:
        while True:
            message = await websocket.receive_text()
            await websocket.send_text(message)
    except WebSocketDisconnect:
        return


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
