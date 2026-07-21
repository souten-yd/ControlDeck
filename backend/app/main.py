from __future__ import annotations

import asyncio
import logging
import os
import re as _re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap import init_db, seed_roles
from app.config import REPO_ROOT, get_config
from app.database import SessionLocal
from app.monitoring.collector import collector
from app.security.rate_limit import api_rate_limiter

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
        from app.bootstrap import remove_retired_repair_app

        removed = remove_retired_repair_app(db)
        if removed:
            logger.info("旧Claude修復コンソールを%d件撤去しました", removed)
    finally:
        db.close()
    # 電気代の起動セッション/日別を復元（同一 boot ID なら累積を引き継ぐ）
    from app.monitoring.electricity import accumulator

    accumulator.load()
    # 前回実行中のまま残ったジョブを interrupted にマーク（メモリは再起動で消える）
    from app.jobs import service as jobs_service

    jobs_service.recover_on_startup()
    from app.alerts.engine import alert_loop
    from app.maintenance.service import maintenance_loop
    from app.maintenance.watchdog import notify_ready, watchdog_loop
    from app.workflows.engine import pause_recovery_loop, scheduler_loop, system_event_loop
    from app.workflows.business_events import delivery_loop as business_event_delivery_loop
    from app.models_mgmt.ollama import idle_unload_loop as ollama_idle_unload_loop
    from app.models_mgmt.llama import idle_unload_loop as llama_idle_unload_loop
    from app.applications.health import health_check_loop

    tasks = [
        asyncio.create_task(collector.run()),
        asyncio.create_task(scheduler_loop()),
        asyncio.create_task(pause_recovery_loop()),
        asyncio.create_task(system_event_loop()),
        asyncio.create_task(business_event_delivery_loop()),
        asyncio.create_task(maintenance_loop()),
        asyncio.create_task(watchdog_loop()),
        asyncio.create_task(alert_loop()),
        asyncio.create_task(ollama_idle_unload_loop()),
        asyncio.create_task(llama_idle_unload_loop()),
        asyncio.create_task(health_check_loop()),
    ]
    # SearXNG は基本停止・検索時に自動起動。自動起動分はアイドルで自動停止する
    from app.workflows import searxng

    tasks.append(asyncio.create_task(searxng.idle_stop_loop()))
    notify_ready()
    logger.info("Control Deck 起動完了")
    yield
    from app.maintenance.watchdog import sd_notify

    sd_notify("STOPPING=1")
    # 電気代を即時保存（正常終了時。最大 persistence_interval 分の損失を防ぐ）
    from app.monitoring.electricity import accumulator

    await asyncio.to_thread(accumulator.persist, "shutdown")
    await searxng.lifecycle_stop()  # SearXNG も一緒に停止
    import contextlib

    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Ubuntu Control Deck", lifespan=lifespan, docs_url=None, redoc_url=None)


def _download_request(path: str) -> bool:
    segments = {segment for segment in path.split("/") if segment}
    return "download" in segments or "artifacts" in segments


@app.middleware("http")
async def api_rate_limit(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/v1/") and path not in ("/api/v1/health", "/api/v1/meta"):
        cfg = get_config().security
        download = request.method == "GET" and _download_request(path)
        scope = "download" if download else "api"
        limit = cfg.download_rate_limit_per_minute if download else cfg.api_rate_limit_per_minute
        peer = request.client.host if request.client else "unknown"
        allowed, retry_after = api_rate_limiter.check(scope, peer, limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "リクエストが多すぎます。しばらく待ってから再試行してください"},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    # Cookie セッションのため、状態変更 API はカスタムヘッダーを必須にする
    # （/hooks/ は外部 Webhook 用: セッションを使わずトークンで保護されるため除外）
    if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not request.url.path.startswith(f"{API}/hooks/") and request.headers.get("x-requested-with") != "ControlDeck":
            return JSONResponse(status_code=403, content={"detail": "CSRF チェックに失敗しました"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # appview / Project Lab成果物は認証済み同一origin iframeで表示するためDENYにしない。
    project_artifact = request.url.path.startswith(f"{API}/project-lab/projects/") and "/artifacts/" in request.url.path
    if request.url.path.startswith(("/appview/", "/project-view/")) or project_artifact:
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    else:
        response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# アプリ内Webビュー: /appview/{id}/ を起点に読み込まれたページの絶対パス参照
# （/static 等）を、referer を手掛かりに proxy へ戻す。Control Deck 自身の
# API/資産(/api/v1, /assets)とproxy自身は対象外。
_APPVIEW_REFERER_RE = _re.compile(r"/appview/(\d+)/")
_PROJECT_VIEW_REFERER_RE = _re.compile(r"/project-view/(\d+)/")


@app.middleware("http")
async def appview_referer_fallback(request: Request, call_next):
    path = request.url.path
    if not path.startswith(("/appview/", "/project-view/", "/api/v1/", "/assets/")):
        referer = request.headers.get("referer", "")
        match = _APPVIEW_REFERER_RE.search(referer)
        project_match = _PROJECT_VIEW_REFERER_RE.search(referer)
        if match or project_match:
            target = (f"/appview/{match.group(1)}{path}" if match
                      else f"/project-view/{project_match.group(1)}{path}")
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(target, status_code=307)
    return await call_next(request)


from app.applications.router import router as apps_router  # noqa: E402
from app.audit.router import router as audit_router  # noqa: E402
from app.auth.router import router as auth_router  # noqa: E402
from app.access.router import router as access_router  # noqa: E402
from app.files.router import router as files_router  # noqa: E402
from app.logs.router import router as logs_router  # noqa: E402
from app.monitoring.router import router as system_router  # noqa: E402
from app.power.router import router as power_router  # noqa: E402
from app.terminals.router import router as terminals_router  # noqa: E402
from app.terminals.automation_router import router as terminal_automation_router  # noqa: E402
from app.workflows.router import router as workflows_router  # noqa: E402
from app.workflows.runner_router import router as workflow_runner_router  # noqa: E402
from app.application_builder.router import router as application_builder_router  # noqa: E402
from app.project_lab.router import router as project_lab_router  # noqa: E402
from app.alerts.router import router as alerts_router  # noqa: E402
from app.remote_desktop.router import router as remote_router  # noqa: E402
from app.gitrepos.router import router as gitrepos_router  # noqa: E402
from app.workflows.knowledge_router import router as knowledge_router  # noqa: E402
from app.workflows.chat_router import router as chat_router  # noqa: E402
from app.workflows.samplebook import router as samplebook_router  # noqa: E402
from app.workflows.hooks_router import router as hooks_router  # noqa: E402
from app.workflows.chat_persist import router as chat_persist_router  # noqa: E402
from app.workflows.asr import router as chat_asr_router  # noqa: E402
from app.jobs.router import router as jobs_router  # noqa: E402
from app.models_mgmt.router import router as models_router  # noqa: E402
from app.features.router import router as features_router  # noqa: E402
from app.features.registry import is_enabled as feature_enabled  # noqa: E402
from app.plugins.router import router as plugins_router  # noqa: E402

API = "/api/v1"
app.include_router(auth_router, prefix=API)
app.include_router(access_router, prefix=API)
app.include_router(apps_router, prefix=API)
# アプリ内Webビュー proxy（同一オリジン・iframe用のためAPI prefixなし）
from app.applications.webview import router as appview_router  # noqa: E402
from app.project_lab.webview import router as project_view_router  # noqa: E402

app.include_router(appview_router)
app.include_router(project_view_router)
app.include_router(logs_router, prefix=API)
app.include_router(system_router, prefix=API)
app.include_router(power_router, prefix=API)
app.include_router(audit_router, prefix=API)
app.include_router(files_router, prefix=API)
app.include_router(terminals_router, prefix=API)
app.include_router(terminal_automation_router, prefix=API)
app.include_router(samplebook_router, prefix=API)  # /workflows/samples は /workflows/{id} より先に登録
app.include_router(workflow_runner_router, prefix=API)
app.include_router(application_builder_router, prefix=API)
app.include_router(project_lab_router, prefix=API)
app.include_router(workflows_router, prefix=API)
app.include_router(alerts_router, prefix=API)
app.include_router(remote_router, prefix=API)
app.include_router(gitrepos_router, prefix=API)
app.include_router(knowledge_router, prefix=API)
app.include_router(chat_router, prefix=API)
app.include_router(chat_persist_router, prefix=API)
app.include_router(chat_asr_router, prefix=API)
app.include_router(hooks_router, prefix=API)
app.include_router(jobs_router, prefix=API)
app.include_router(models_router, prefix=API)
app.include_router(features_router, prefix=API)
app.include_router(plugins_router, prefix=API)
if feature_enabled("opencode"):
    from app.integrations.opencode.router import router as opencode_router

    app.include_router(opencode_router, prefix=API)


@app.get("/api/v1/meta")
def meta():
    """ログイン画面用の公開メタ情報（秘密情報を含めない）。"""
    ui = get_config().ui
    from app.features.registry import list_features
    from app.plugins.registry import enabled_navigation

    return {
        "app_name": ui.app_name,
        "accent_color": ui.accent_color,
        "default_theme": ui.default_theme,
        "metric_refresh_seconds": ui.metric_refresh_seconds,
        "enabled_features": [item["id"] for item in list_features() if item["enabled"]],
        "plugin_navigation": enabled_navigation(),
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
        # 未登録APIをSPAへfallbackするとoptional featureの不存在を隠して200になる。
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if full_path.rstrip("/") == "opencode" and not feature_enabled("opencode"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(DIST.resolve()):
            return FileResponse(candidate)
        # index.html はキャッシュさせない（デプロイ後に旧チャンク参照が残るのを防ぐ）
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
else:

    @app.get("/", include_in_schema=False)
    def no_frontend():
        return JSONResponse(
            {"detail": "フロントエンドが未ビルドです。scripts/setup.sh を実行してください"}
        )
