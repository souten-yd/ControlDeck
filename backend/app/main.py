from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap import init_db, seed_roles
from app.config import REPO_ROOT, get_config
from app.database import SessionLocal
from app.monitoring.collector import collector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("control_deck")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.geteuid() == 0:
        raise RuntimeError("Control Deck を root で起動してはいけません（docs/security-model.md 参照）")
    cfg = get_config()
    if cfg.server.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "サーバーが %s で待ち受けます。外部公開せず Tailscale/WireGuard/リバースプロキシ+HTTPS を推奨します",
            cfg.server.host,
        )
    init_db()
    db = SessionLocal()
    try:
        seed_roles(db)
        from app.bootstrap import seed_repair_app

        seed_repair_app(db)
    finally:
        db.close()
    from app.alerts.engine import alert_loop
    from app.maintenance.service import maintenance_loop
    from app.maintenance.watchdog import notify_ready, watchdog_loop
    from app.workflows.engine import scheduler_loop
    from app.models_mgmt.ollama import idle_unload_loop

    tasks = [
        asyncio.create_task(collector.run()),
        asyncio.create_task(scheduler_loop()),
        asyncio.create_task(maintenance_loop()),
        asyncio.create_task(watchdog_loop()),
        asyncio.create_task(alert_loop()),
        asyncio.create_task(idle_unload_loop()),
    ]
    notify_ready()
    logger.info("Control Deck 起動完了")
    yield
    from app.maintenance.watchdog import sd_notify

    sd_notify("STOPPING=1")
    import contextlib

    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Ubuntu Control Deck", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    # Cookie セッションのため、状態変更 API はカスタムヘッダーを必須にする
    if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get("x-requested-with") != "ControlDeck":
            return JSONResponse(status_code=403, content={"detail": "CSRF チェックに失敗しました"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


from app.applications.router import router as apps_router  # noqa: E402
from app.audit.router import router as audit_router  # noqa: E402
from app.auth.router import router as auth_router  # noqa: E402
from app.files.router import router as files_router  # noqa: E402
from app.logs.router import router as logs_router  # noqa: E402
from app.monitoring.router import router as system_router  # noqa: E402
from app.power.router import router as power_router  # noqa: E402
from app.terminals.router import router as terminals_router  # noqa: E402
from app.workflows.router import router as workflows_router  # noqa: E402
from app.alerts.router import router as alerts_router  # noqa: E402
from app.remote_desktop.router import router as remote_router  # noqa: E402
from app.gitrepos.router import router as gitrepos_router  # noqa: E402
from app.workflows.knowledge_router import router as knowledge_router  # noqa: E402
from app.models_mgmt.router import router as models_router  # noqa: E402

API = "/api/v1"
app.include_router(auth_router, prefix=API)
app.include_router(apps_router, prefix=API)
app.include_router(logs_router, prefix=API)
app.include_router(system_router, prefix=API)
app.include_router(power_router, prefix=API)
app.include_router(audit_router, prefix=API)
app.include_router(files_router, prefix=API)
app.include_router(terminals_router, prefix=API)
app.include_router(workflows_router, prefix=API)
app.include_router(alerts_router, prefix=API)
app.include_router(remote_router, prefix=API)
app.include_router(gitrepos_router, prefix=API)
app.include_router(knowledge_router, prefix=API)
app.include_router(models_router, prefix=API)


@app.get("/api/v1/meta")
def meta():
    """ログイン画面用の公開メタ情報（秘密情報を含めない）。"""
    ui = get_config().ui
    return {
        "app_name": ui.app_name,
        "accent_color": ui.accent_color,
        "default_theme": ui.default_theme,
        "metric_refresh_seconds": ui.metric_refresh_seconds,
    }


@app.get("/api/v1/health")
def health():
    return {"ok": True}


# ---- フロントエンド配信（frontend/dist、SPA fallback）----

DIST = REPO_ROOT / "frontend" / "dist"

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file() and str(candidate).startswith(str(DIST)):
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def no_frontend():
        return JSONResponse(
            {"detail": "フロントエンドが未ビルドです。scripts/setup.sh を実行してください"}
        )
