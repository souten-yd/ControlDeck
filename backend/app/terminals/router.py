from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.models import User
from app.security.deps import authenticate_websocket, require_permission
from app.terminals.manager import manager, tmux_available

logger = logging.getLogger("control_deck.terminals")

router = APIRouter(prefix="/terminals", tags=["terminals"])


@router.get("")
def list_terminals(user: User = Depends(require_permission("terminal.use"))):
    return {"tmux": tmux_available(), "sessions": manager.list_sessions()}


@router.post("", status_code=201)
def create_terminal(
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    try:
        session = manager.create_session()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    audit.record(
        db, "terminal.start", user=user, resource_type="terminal",
        resource_id=session["id"], request=request,
    )
    return session


@router.delete("/{session_id}")
def delete_terminal(
    session_id: str,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    try:
        manager.kill_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="セッションが見つかりません") from exc
    audit.record(
        db, "terminal.kill", user=user, resource_type="terminal",
        resource_id=session_id, request=request,
    )
    return {"ok": True}


@router.websocket("/{session_id}/connect")
async def terminal_ws(websocket: WebSocket, session_id: str, rows: int = 24, cols: int = 80):
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "terminal.use")
        if user is None:
            return
    finally:
        db.close()

    try:
        conn = manager.open_connection(session_id, rows, cols)
    except KeyError:
        await websocket.close(code=4404)
        return
    except OSError as e:
        logger.warning("terminal open failed: %s", e)
        await websocket.close(code=4500)
        return

    await websocket.accept()
    if conn.initial:
        await websocket.send_bytes(conn.initial)
    await websocket.send_text(json.dumps({"type": "history_reset"}))
    if conn.replay:
        await websocket.send_bytes(conn.replay)

    async def pump_output() -> None:
        await conn.read_loop(websocket.send_bytes)
        # PTY 側 EOF（tmux detach / シェル終了）
        try:
            await websocket.close()
        except RuntimeError:
            pass

    output_task = asyncio.create_task(pump_output())
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                conn.write(data)
            elif (text := msg.get("text")) is not None:
                # 制御メッセージ（リサイズ）は JSON テキストで受ける
                try:
                    ctrl = json.loads(text)
                    if ctrl.get("type") == "resize":
                        conn.resize(int(ctrl["rows"]), int(ctrl["cols"]))
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
        conn.close()
