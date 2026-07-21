from __future__ import annotations

import asyncio
from pathlib import Path

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


def _source_path(app: ManagedApplication, source: str) -> Path:
    if source in logs.STREAMS:
        return logs.log_path(app.id, source)
    if source.startswith("file:"):
        from app.files import service as files

        try:
            index = int(source.removeprefix("file:"))
            configured = apps.get_log_files(app)[index]
            path = files.resolve(configured)
        except (ValueError, IndexError, OSError, files.FileAccessError) as error:
            raise HTTPException(status_code=404, detail="追加ログファイルが見つかりません") from error
        if not path.is_file():
            raise HTTPException(status_code=404, detail="追加ログファイルが見つかりません")
        return path
    raise HTTPException(status_code=422, detail="ログソースが不正です")


@router.get("/{app_id}/log-sources")
def log_sources(
    app_id: int,
    user: User = Depends(require_permission("logs.view")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    result = [
        {"id": "stdout", "label": "stdout", "kind": "file", "deletable": True},
        {"id": "stderr", "label": "stderr", "kind": "file", "deletable": True},
    ]
    if app.systemd_unit_name:
        result.append({"id": "journal", "label": "systemd journal", "kind": "journal", "deletable": False})
    for index, configured in enumerate(apps.get_log_files(app)):
        result.append({"id": f"file:{index}", "label": Path(configured).name, "kind": "file", "deletable": True})
    return result


@router.get("/{app_id}/logs")
def get_logs(
    app_id: int,
    stream: str = Query(default="stdout", pattern="^(stdout|stderr)$"),
    source: str | None = Query(default=None, max_length=64),
    lines: int = Query(default=500, ge=1, le=10000),
    user: User = Depends(require_permission("logs.view")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    selected = source or stream
    if selected == "journal":
        try:
            journal = logs.journal_lines(
                app.systemd_unit_name, app.systemd_scope or "user", lines, _sensitive_values(app),
            )
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail="systemd journalを取得できません") from error
        return {"stream": selected, "lines": journal, "size_bytes": None}
    path = _source_path(app, selected)
    return {
        "stream": selected,
        "lines": logs.tail_lines(path, lines, sensitive_values=_sensitive_values(app)),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


@router.get("/{app_id}/logs/download")
def download_logs(
    app_id: int,
    stream: str = Query(default="stdout", pattern="^(stdout|stderr)$"),
    source: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("logs.view")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    selected = source or stream
    if selected == "journal":
        try:
            text = "\n".join(logs.journal_lines(
                app.systemd_unit_name, app.systemd_scope or "user", 2000, _sensitive_values(app),
            )) + "\n"
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail="systemd journalを取得できません") from error
        return StreamingResponse(
            iter([text.encode("utf-8")]), media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="app-{app.id}-journal.log"'},
        )
    path = _source_path(app, selected)
    if not path.exists():
        raise HTTPException(status_code=404, detail="ログファイルがありません")
    return StreamingResponse(
        logs.iter_redacted_file(path, _sensitive_values(app)),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="app-{app.id}-{selected.replace(":", "-")}.log"'},
    )


@router.delete("/{app_id}/logs")
def delete_logs(
    app_id: int,
    request: Request,
    stream: str = Query(default="all", pattern="^(stdout|stderr|all)$"),
    source: str | None = Query(default=None, max_length=64),
    user: User = Depends(require_permission("logs.delete")),
    db: Session = Depends(get_db),
):
    app = _get_app(db, app_id)
    selected = source or stream
    if selected == "journal":
        raise HTTPException(status_code=409, detail="systemd journalはControl Deckから削除できません")
    targets = [logs.log_path(app_id, item) for item in logs.STREAMS] if selected == "all" else [_source_path(app, selected)]
    for path in targets:
        if path.exists():
            path.write_text("")  # 実行中でも安全なように truncate
    audit.record(
        db, "logs.delete", user=user, resource_type="app", resource_id=str(app_id),
        request=request, metadata={"name": app.name, "stream": selected},
    )
    return {"ok": True}


@router.websocket("/{app_id}/logs/stream")
async def stream_logs(websocket: WebSocket, app_id: int, stream: str = "stdout", source: str | None = None):
    selected = source or stream
    if selected == "journal":
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
    try:
        path = _source_path(app, selected)
    except HTTPException:
        await websocket.close(code=4404)
        return
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
