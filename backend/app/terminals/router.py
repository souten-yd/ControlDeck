from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.models import User
from app.security.deps import authenticate_websocket, require_permission
from app.terminals.manager import manager, tmux_available
from app.terminals.stream import JournalEntry, TerminalClientStream, TerminalStreamRegistry

logger = logging.getLogger("control_deck.terminals")

router = APIRouter(prefix="/terminals", tags=["terminals"])
streams = TerminalStreamRegistry(manager)
CLIENT_INSTANCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
MAX_INPUT_CHUNK_BYTES = 16 * 1024


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
async def delete_terminal(
    session_id: str,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    try:
        streams.close_session(session_id)
        manager.kill_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="セッションが見つかりません") from exc
    audit.record(
        db, "terminal.kill", user=user, resource_type="terminal",
        resource_id=session_id, request=request,
    )
    return {"ok": True}


@router.websocket("/{session_id}/connect")
async def terminal_ws(
    websocket: WebSocket,
    session_id: str,
    rows: int = 24,
    cols: int = 80,
    client_instance_id: str = Query("", alias="clientInstanceId"),
    connection_generation: int = Query(1, alias="connectionGeneration"),
    attach_mode: str = Query("initial", alias="attachMode"),
    last_sequence: int = Query(0, alias="lastSequence"),
):
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "terminal.use")
        if user is None:
            return
    finally:
        db.close()

    if not CLIENT_INSTANCE_RE.fullmatch(client_instance_id) or attach_mode not in {"initial", "resume"}:
        await websocket.close(code=4400)
        return
    if not 1 <= connection_generation <= 2_147_483_647 or not 0 <= last_sequence <= 9_007_199_254_740_991:
        await websocket.close(code=4400)
        return

    try:
        stream, created, output_queue = streams.acquire(
            session_id, client_instance_id, connection_generation, rows, cols,
        )
    except KeyError:
        await websocket.close(code=4404)
        return
    except ValueError:
        await websocket.close(code=4409)
        return
    except OSError as e:
        logger.warning("terminal open failed: %s", e)
        await websocket.close(code=4500)
        return

    conn = stream.connection

    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send_bytes(data: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(data)

    async def send_control(payload: dict[str, object]) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(payload, separators=(",", ":")))

    async def send_output(entry: JournalEntry) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps({
                "type": "output",
                "sequence": entry.sequence,
                "connectionGeneration": connection_generation,
            }, separators=(",", ":")))
            await websocket.send_bytes(entry.data)

    async def send_snapshot(replay: bytes, baseline: int, created_stream: bool) -> None:
        await send_control({
            "type": "history_reset",
            "connectionGeneration": connection_generation,
        })
        if replay:
            await send_bytes(replay)
        # 新規attachではcapture完了後にreaderが得たbyteをsnapshotの後へ連結する。
        if created_stream:
            entries = stream.journal.after(0, baseline) or []
            for entry in entries:
                await send_output(entry)
        await send_control({
            "type": "history_end",
            "connectionGeneration": connection_generation,
            "sequence": baseline,
        })

    skip_through = 0
    resumed = attach_mode == "resume" and not created
    if resumed:
        resume_through = stream.journal.latest_sequence
        delta = stream.journal.after(last_sequence, resume_through)
        if delta is not None:
            await send_control({
                "type": "resume_ready",
                "connectionGeneration": connection_generation,
                "fromSequence": last_sequence,
                "throughSequence": resume_through,
            })
            for entry in delta:
                await send_output(entry)
            await send_control({
                "type": "resume_end",
                "connectionGeneration": connection_generation,
                "sequence": resume_through,
            })
            skip_through = resume_through
        else:
            await send_control({
                "type": "resume_reset_required",
                "connectionGeneration": connection_generation,
                "oldestSequence": stream.journal.oldest_sequence,
                "latestSequence": stream.journal.latest_sequence,
            })
            replay = conn.capture_replay()
            skip_through = stream.journal.latest_sequence
            await send_snapshot(replay, skip_through, False)
    else:
        if attach_mode == "resume":
            await send_control({
                "type": "resume_reset_required",
                "connectionGeneration": connection_generation,
                "oldestSequence": stream.journal.oldest_sequence,
                "latestSequence": stream.journal.latest_sequence,
            })
        if conn.initial:
            await send_bytes(conn.initial)
        baseline = stream.journal.latest_sequence
        await send_snapshot(conn.replay, baseline, True)
        skip_through = baseline

    async def pump_output() -> None:
        while True:
            entry = await output_queue.get()
            if entry is None:
                try:
                    await websocket.close()
                except RuntimeError:
                    pass
                return
            if entry.sequence <= skip_through:
                continue
            await send_output(entry)

    output_task = asyncio.create_task(pump_output())
    pending_input: dict[str, int | bool] | None = None
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                if connection_generation != stream.connection_generation:
                    break
                if pending_input is None:
                    # 旧frontendの通常キー入力との互換経路。
                    await asyncio.to_thread(conn.write, data)
                    continue
                metadata = pending_input
                pending_input = None
                if len(data) != metadata["byteLength"]:
                    await websocket.close(code=4400, reason="input byte length mismatch")
                    break
                input_sequence = int(metadata["inputSequence"])
                paste_id = int(metadata["pasteId"])
                chunk_index = int(metadata["chunkIndex"])
                existing = stream.input_ack(input_sequence)
                if existing is not None:
                    if (existing.paste_id != paste_id or existing.chunk_index != chunk_index
                            or existing.written_bytes != len(data)):
                        await websocket.close(code=4400, reason="input sequence conflict")
                        break
                    written = existing.written_bytes
                else:
                    if not stream.validate_new_input_sequence(input_sequence):
                        await websocket.close(code=4400, reason="input sequence out of order")
                        break
                    try:
                        written = await asyncio.to_thread(conn.write, data)
                    except OSError as exc:
                        logger.warning("terminal input write failed: %s", exc)
                        await send_control({
                            "type": "input_error", "inputSequence": input_sequence,
                            "pasteId": paste_id, "chunkIndex": chunk_index,
                            "connectionGeneration": connection_generation,
                            "reason": "pty-write-failed",
                        })
                        continue
                    stream.record_input_ack(input_sequence, paste_id, chunk_index, written)
                await send_control({
                    "type": "input_ack", "inputSequence": input_sequence,
                    "pasteId": paste_id, "chunkIndex": chunk_index,
                    "writtenBytes": written, "connectionGeneration": connection_generation,
                })
            elif (text := msg.get("text")) is not None:
                # 制御メッセージ（リサイズ）は JSON テキストで受ける
                try:
                    ctrl = json.loads(text)
                    if ctrl.get("type") == "input":
                        if pending_input is not None:
                            raise ValueError("input control without binary frame")
                        input_sequence = int(ctrl["inputSequence"])
                        paste_id = int(ctrl["pasteId"])
                        chunk_index = int(ctrl["chunkIndex"])
                        byte_length = int(ctrl["byteLength"])
                        message_connection_generation = int(ctrl["connectionGeneration"])
                        if not 1 <= input_sequence <= 9_007_199_254_740_991:
                            raise ValueError("invalid input sequence")
                        if not 1 <= paste_id <= 2_147_483_647 or not 0 <= chunk_index <= 2_147_483_647:
                            raise ValueError("invalid paste metadata")
                        if not 1 <= byte_length <= MAX_INPUT_CHUNK_BYTES:
                            raise ValueError("invalid input byte length")
                        if message_connection_generation != stream.connection_generation:
                            raise ValueError("stale connection generation")
                        if not isinstance(ctrl.get("final"), bool):
                            raise ValueError("invalid final flag")
                        pending_input = {
                            "inputSequence": input_sequence, "pasteId": paste_id,
                            "chunkIndex": chunk_index, "byteLength": byte_length,
                            "final": ctrl["final"],
                        }
                    elif ctrl.get("type") == "resize":
                        resize_generation = int(ctrl["resizeGeneration"])
                        message_connection_generation = int(ctrl["connectionGeneration"])
                        if not 0 <= resize_generation <= 2_147_483_647:
                            raise ValueError("invalid resize generation")
                        if not 0 <= message_connection_generation <= 2_147_483_647:
                            raise ValueError("invalid connection generation")
                        if message_connection_generation != stream.connection_generation:
                            raise ValueError("stale connection generation")
                        received_at = asyncio.get_running_loop().time() * 1000
                        try:
                            applied_rows, applied_cols = conn.resize(int(ctrl["rows"]), int(ctrl["cols"]))
                            applied_at = asyncio.get_running_loop().time() * 1000
                            ack: dict[str, object] = {
                                "type": "resize_ack",
                                "rows": applied_rows,
                                "cols": applied_cols,
                                "resizeGeneration": resize_generation,
                                "connectionGeneration": message_connection_generation,
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
                                "connectionGeneration": message_connection_generation,
                                "success": False,
                            })
                    elif ctrl.get("type") == "size_probe":
                        resize_generation = int(ctrl["resizeGeneration"])
                        message_connection_generation = int(ctrl["connectionGeneration"])
                        if not 0 <= resize_generation <= 2_147_483_647:
                            raise ValueError("invalid resize generation")
                        if not 0 <= message_connection_generation <= 2_147_483_647:
                            raise ValueError("invalid connection generation")
                        if message_connection_generation != stream.connection_generation:
                            raise ValueError("stale connection generation")
                        await send_control({
                            "type": "size_probe_result",
                            "resizeGeneration": resize_generation,
                            "connectionGeneration": message_connection_generation,
                            "diagnostics": conn.size_diagnostics(),
                        })
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    await websocket.close(code=4400, reason="malformed terminal control")
                    break
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
        streams.release(stream, output_queue)
