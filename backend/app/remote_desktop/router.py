from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.models import RemoteConnection, User
from app.remote_desktop import guacd, service
from app.security.deps import authenticate_websocket, require_permission

router = APIRouter(prefix="/remote", tags=["remote_desktop"])

DEFAULT_PORTS = {"rdp": 3389, "vnc": 5900, "ssh": 22}


class ConnectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str = Field(pattern="^(rdp|vnc|ssh)$")
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=512)
    # RDP セキュリティ: any/nla/tls/rdp/vmconnect（xrdp=any, Windows=nla）
    security: str = Field(default="", max_length=16)
    params: dict = {}


@router.get("/status")
def status(user: User = Depends(require_permission("remote_desktop.use"))):
    return {"guacd_available": guacd.guacd_available()}


@router.get("/connections")
def list_connections(
    user: User = Depends(require_permission("remote_desktop.use")), db: Session = Depends(get_db)
):
    rows = db.execute(select(RemoteConnection).order_by(RemoteConnection.name)).scalars().all()
    return [service.to_out(c) for c in rows]


@router.post("/connections", status_code=201)
def create_connection(
    body: ConnectionBody,
    request: Request,
    user: User = Depends(require_permission("remote_desktop.use")),
    db: Session = Depends(get_db),
):
    params = dict(body.params)
    if body.security:
        params["security"] = body.security
    conn = RemoteConnection(
        name=body.name, protocol=body.protocol, host=body.host,
        port=body.port or DEFAULT_PORTS[body.protocol], username=body.username,
        params_json=json.dumps(params),
    )
    service.set_secret_params(conn, {"password": body.password})
    db.add(conn)
    db.commit()
    audit.record(db, "remote.create", user=user, resource_type="remote", resource_id=str(conn.id), request=request, metadata={"name": conn.name, "protocol": conn.protocol})
    return service.to_out(conn)


@router.delete("/connections/{connection_id}")
def delete_connection(
    connection_id: int,
    request: Request,
    user: User = Depends(require_permission("remote_desktop.use")),
    db: Session = Depends(get_db),
):
    conn = db.get(RemoteConnection, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="接続が見つかりません")
    db.delete(conn)
    db.commit()
    audit.record(db, "remote.delete", user=user, resource_type="remote", resource_id=str(connection_id), request=request)
    return {"ok": True}


@router.websocket("/connections/{connection_id}/tunnel")
async def tunnel(websocket: WebSocket, connection_id: int, width: int = 1024, height: int = 768, dpi: int = 96):
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "remote_desktop.use")
        if user is None:
            return
        conn = db.get(RemoteConnection, connection_id)
        if conn is None:
            await websocket.close(code=4404)
            return
        protocol = conn.protocol
        params = service.build_guacd_params(conn)
    finally:
        db.close()

    if not guacd.guacd_available():
        await websocket.close(code=4503)  # guacd 未導入
        return

    try:
        reader, writer = await guacd.open_guacd(guacd.GUACD_DEFAULT_HOST, guacd.GUACD_DEFAULT_PORT)
    except (OSError, asyncio.TimeoutError):
        await websocket.close(code=4502)  # guacd へ接続できない
        return

    # guacamole-common-js は WebSocket サブプロトコル "guacamole" を要求するため、
    # accept 時に必ずエコーする（返さないとブラウザが 1006 で即切断する）
    subprotocols = websocket.scope.get("subprotocols") or []
    accept_proto = "guacamole" if "guacamole" in subprotocols else None
    await websocket.accept(subprotocol=accept_proto)
    try:
        await guacd.perform_handshake(reader, writer, protocol, params, width, height, dpi)
    except (OSError, asyncio.TimeoutError, ConnectionError) as e:
        await websocket.send_text(encode_error(str(e)))
        await websocket.close(code=4500)
        writer.close()
        return

    # 双方向パイプ: guacd(TCP) <-> WebSocket(text)
    # guacd の出力は UTF-8 テキスト（Guacamole プロトコル）。任意バイト境界で分割されるため
    # インクリメンタルデコーダで multibyte 文字が途中で壊れないようにする（"Incomplete instruction" 防止）。
    async def guacd_to_ws() -> None:
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            while True:
                data = await reader.read(16384)
                if not data:
                    text = decoder.decode(b"", final=True)
                    if text:
                        await websocket.send_text(text)
                    break
                text = decoder.decode(data)
                if text:
                    await websocket.send_text(text)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass

    async def ws_to_guacd() -> None:
        try:
            while True:
                msg = await websocket.receive_text()
                writer.write(msg.encode("utf-8"))
                await writer.drain()
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass

    t1 = asyncio.create_task(guacd_to_ws())
    t2 = asyncio.create_task(ws_to_guacd())
    try:
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        t1.cancel()
        t2.cancel()
        writer.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass


def encode_error(message: str) -> str:
    from app.remote_desktop.guacd import encode_instruction

    return encode_instruction("error", message[:200], "512").decode()
