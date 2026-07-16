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
    send_lock = asyncio.Lock()

    async def send_bytes(data: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(data)

    async def send_control(payload: dict[str, object]) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(payload, separators=(",", ":")))

    if conn.initial:
        await send_bytes(conn.initial)
    await send_control({"type": "history_reset"})
    if conn.replay:
        await send_bytes(conn.replay)
    await send_control({"type": "history_end"})

    async def pump_output() -> None:
        await conn.read_loop(send_bytes)
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
                        resize_generation = int(ctrl["resizeGeneration"])
                        connection_generation = int(ctrl["connectionGeneration"])
                        if not 0 <= resize_generation <= 2_147_483_647:
                            raise ValueError("invalid resize generation")
                        if not 0 <= connection_generation <= 2_147_483_647:
                            raise ValueError("invalid connection generation")
                        received_at = asyncio.get_running_loop().time() * 1000
                        try:
                            applied_rows, applied_cols = conn.resize(int(ctrl["rows"]), int(ctrl["cols"]))
                            applied_at = asyncio.get_running_loop().time() * 1000
                            ack: dict[str, object] = {
                                "type": "resize_ack",
                                "rows": applied_rows,
                                "cols": applied_cols,
                                "resizeGeneration": resize_generation,
                                "connectionGeneration": connection_generation,
                                "success": True,
                            }
                            if ctrl.get("debug") is True:
                                ack["diagnostics"] = {
                                    "serverReceivedAtMs": received_at,
                                    "winsizeAppliedAtMs": applied_at,
                                    **conn.size_diagnostics(),
                                }
                            await send_control(ack)
                        except OSError as exc:
                            logger.warning("terminal resize failed: %s", exc)
                            await send_control({
                                "type": "resize_ack",
                                "rows": int(ctrl["rows"]),
                                "cols": int(ctrl["cols"]),
                                "resizeGeneration": resize_generation,
                                "connectionGeneration": connection_generation,
                                "success": False,
                            })
                    elif ctrl.get("type") == "size_probe":
                        resize_generation = int(ctrl["resizeGeneration"])
                        connection_generation = int(ctrl["connectionGeneration"])
                        if not 0 <= resize_generation <= 2_147_483_647:
                            raise ValueError("invalid resize generation")
                        if not 0 <= connection_generation <= 2_147_483_647:
                            raise ValueError("invalid connection generation")
                        await send_control({
                            "type": "size_probe_result",
                            "resizeGeneration": resize_generation,
                            "connectionGeneration": connection_generation,
                            "diagnostics": conn.size_diagnostics(),
                        })
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
        conn.close()
