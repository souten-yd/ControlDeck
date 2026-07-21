from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.applications import service as apps
from app.database import SessionLocal, get_db
from app.logs import service as logs
from app.models import ManagedApplication, User
from app.security.crypto import is_secret_key
from app.security.deps import authenticate_websocket, require_permission

router = APIRouter(prefix="/apps", tags=["logs"])


def _get_app(db: Session, app_id: int) -> ManagedApplication:
    app = db.get(ManagedApplication, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="アプリが見つかりません")
    return app


def _sensitive_values(app: ManagedApplication) -> set[str]:
    return {
        value for key, value in apps.get_environment(app).items()
        if is_secret_key(key) and isinstance(value, str) and len(value) >= 4
    }


@router.get("/{app_id}/logs")
def get_logs(
    app_id: int,
    stream: str = Query(default="stdout", pattern="^(stdout|stderr)$"),
    lines: int = Query(default=500, ge=1, le=10000),
    user: User = Depends(require_permission("logs.view")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    path = logs.log_path(app_id, stream)
    return {
        "stream": stream,
        "lines": logs.tail_lines(path, lines, sensitive_values=_sensitive_values(app)),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


@router.get("/{app_id}/logs/download")
def download_logs(
    app_id: int,
    stream: str = Query(default="stdout", pattern="^(stdout|stderr)$"),
    user: User = Depends(require_permission("logs.view")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    path = logs.log_path(app_id, stream)
    if not path.exists():
        raise HTTPException(status_code=404, detail="ログファイルがありません")
    return StreamingResponse(
        logs.iter_redacted_file(path, _sensitive_values(app)),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="app-{app.id}-{stream}.log"'},
    )


@router.delete("/{app_id}/logs")
def delete_logs(
    app_id: int,
    request: Request,
    stream: str = Query(default="all", pattern="^(stdout|stderr|all)$"),
    user: User = Depends(require_permission("logs.delete")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    targets = ["stdout", "stderr"] if stream == "all" else [stream]
    for s in targets:
        path = logs.log_path(app_id, s)
        if path.exists():
            path.write_text("")  # 実行中でも安全なように truncate
    audit.record(
        db, "logs.delete", user=user, resource_type="app", resource_id=str(app_id),
        request=request, metadata={"name": app.name, "stream": stream},
    )
    return {"ok": True}


@router.websocket("/{app_id}/logs/stream")
async def stream_logs(websocket: WebSocket, app_id: int, stream: str = "stdout"):
    if stream not in ("stdout", "stderr"):
        await websocket.close(code=4400)
        return
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "logs.view")
        if user is None:
            return
        app = db.get(ManagedApplication, app_id)
        if app is None:
            await websocket.close(code=4404)
            return
        sensitive_values = _sensitive_values(app)
    finally:
        db.close()

    await websocket.accept()
    path = logs.log_path(app_id, stream)
    # 直近分を初期送信し、以降は追記分を送る
    initial = logs.tail_lines(path, 200, sensitive_values=sensitive_values)
    offset = path.stat().st_size if path.exists() else 0
    line_buffer = logs.RedactedLineBuffer(sensitive_values)
    await websocket.send_json({"type": "initial", "lines": initial})
    try:
        while True:
            await asyncio.sleep(0.5)
            data, offset = await asyncio.to_thread(logs.read_new_bytes, path, offset)
            if data:
                text = line_buffer.feed(data)
                if text:
                    await websocket.send_json({"type": "append", "data": text})
    except (WebSocketDisconnect, RuntimeError):
        pass
