"""Project Run専用の同一origin preview proxy。

上流はDBへ割り当てたlocalhost portに固定し、systemd unitのprocess treeが
そのportをLISTENしている場合だけ接続する。ControlDeckの資格情報は渡さない。

入口は 2 つある。
- `/project-view/{run_id}/...`: Cookie で認証する（前からある入口）。
- `/project-view/r/{run_id}/{token}/...`: URL の中の run ごとの鍵で認証する（previewUrl はこちら）。
  Project Lab は preview を sandbox の iframe（allow-same-origin なし）で開く。中のページの生まれは
  null になり、ControlDeck の API には触れないが、Cookie も送れない。Vite の module script は
  CORS で読まれるので、Cookie の入口では 1 本も読めなかった（2026-11-21 実測）。鍵の入口は
  Cookie を使わず、`Access-Control-Allow-Origin: *` を返す。

WebSocket（開発サーバーの HMR など）は鍵の入口だけで受け、上流へ双方向に中継する。
2026-11-21 実測: Vite の HMR は base（既定は `/`）へ繋ぎに来るので、preview の接頭辞で受けるには
command に `--base {base}` を渡す（run が鍵付きの接頭辞へ置き換える）。`{base}` を含む run は
接頭辞を付けたまま上流へ送る。
"""
from __future__ import annotations

import asyncio
import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.database import SessionLocal, get_db
from app.models import ProjectRun, User
from app.project_lab import runs
from app.security.deps import require_permission

logger = logging.getLogger("control_deck.project_lab.preview")

router = APIRouter(prefix="/project-view", tags=["project-lab-preview"])

MAX_REQUEST_BYTES = 16 * 1024 * 1024
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
_PRIVATE_REQUEST_HEADERS = {"cookie", "authorization", "proxy-authorization", "x-csrf-token"}
_PRIVATE_RESPONSE_HEADERS = {"set-cookie", "set-cookie2"}
# WebSocket の握手で上流へ渡してよい頭。Cookie・認証・Origin・Host は渡さない
# （Host は接続先 127.0.0.1:{port} を websockets が入れる。Vite の Host 検査はこれで通る）。
_WS_FORWARD_HEADERS = {"user-agent", "accept-language"}
# 送り返してよい close code（1005・1006・1015 は送れない）
_SENDABLE_CLOSE = {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011}


def _upstream_headers(request: Request) -> dict[str, str]:
    return {
        key: value for key, value in request.headers.items()
        if key.lower() not in _HOP_HEADERS | _PRIVATE_REQUEST_HEADERS
    }


def _run_target(db: Session, run_id: int) -> ProjectRun:
    row = db.get(ProjectRun, run_id)
    if row is None or row.profile_type != "web" or not row.web_port:
        raise HTTPException(status_code=404, detail="Web preview runが見つかりません")
    runs.refresh_run(db, row)
    if row.status not in runs.RUNNING_STATES:
        raise HTTPException(status_code=409, detail="Web previewは終了しています")
    if not runs.web_preview_ready(row):
        raise HTTPException(status_code=425, detail="Web applicationの起動を待っています")
    return row


def _token_target(db: Session, run_id: int, token: str) -> ProjectRun:
    row = db.get(ProjectRun, run_id)
    if row is None or not hmac.compare_digest(token.encode(), runs.preview_token(row).encode()):
        raise HTTPException(status_code=404, detail="Web preview runが見つかりません")
    return _run_target(db, run_id)


def _upstream_path(row: ProjectRun, path: str) -> str:
    """上流へ送る path（先頭の / なし）。{base} を渡した run は接頭辞を付けたまま送る。"""
    if runs.preview_keeps_base(row):
        return runs.preview_base(row).lstrip("/") + path
    return path


async def _proxy_http(
    row: ProjectRun, upstream_path: str, request: Request, *, prefix: str, cors: bool,
) -> StreamingResponse:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="preview requestが16MiBを超えています")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Lengthが不正です") from exc
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="preview requestが16MiBを超えています")
    url = f"http://127.0.0.1:{row.web_port}/{upstream_path}"
    if request.url.query:
        url += f"?{request.url.query}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(15, read=120), follow_redirects=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=_upstream_headers(request), content=body),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Web applicationへ接続できません") from exc

    keeps_base = runs.preview_keeps_base(row)
    headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in _HOP_HEADERS | _PRIVATE_RESPONSE_HEADERS:
            continue
        if cors and lower.startswith("access-control-"):
            continue
        if lower == "location" and value.startswith("/") and not value.startswith("//"):
            if not (keeps_base and value.startswith(prefix)):
                value = f"{prefix.rstrip('/')}{value}"
        headers[key] = value
    headers["Cache-Control"] = "no-store"
    if cors:
        # 中のページの生まれは null。module script・fetch は CORS で読むので、許さないと 1 本も読めない。
        # 鍵の入口は Cookie を受け取らないので、誰に許しても ControlDeck の資格情報は漏れない。
        headers["Access-Control-Allow-Origin"] = "*"

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=headers)


@router.api_route("/r/{run_id}/{token}/{path:path}", methods=_METHODS)
async def project_view_token_proxy(
    run_id: int, token: str, path: str, request: Request, db: Session = Depends(get_db),
):
    row = _token_target(db, run_id, token)
    return await _proxy_http(row, _upstream_path(row, path), request, prefix=runs.preview_base(row), cors=True)


@router.websocket("/r/{run_id}/{token}/{path:path}")
async def project_view_websocket(websocket: WebSocket, run_id: int, token: str, path: str):
    """開発サーバーの WebSocket（Vite の HMR など）を上流へ中継する。

    - 認証は URL の鍵（HTTP と同じ）。生まれが null の iframe は Cookie を送れない
    - サブプロトコル（Vite は `vite-hmr`。無いと Vite は握手に応じない）は上流が選んだものを返す
    - 文字も binary もそのまま。片側が閉じたら、もう片方も同じ code で閉じる
    - ping/pong は区間ごとに uvicorn と websockets が返すので、中継しない
    """
    db = SessionLocal()
    try:
        try:
            row = _token_target(db, run_id, token)
        except HTTPException:
            await websocket.close(code=4404)
            return
        port = row.web_port
        upstream_path = _upstream_path(row, path)
    finally:
        db.close()

    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

    url = f"ws://127.0.0.1:{port}/{upstream_path}"
    query = websocket.scope.get("query_string", b"").decode("latin-1")
    if query:
        url += f"?{query}"
    offered = list(websocket.scope.get("subprotocols") or [])
    forward = [(key, value) for key, value in websocket.headers.items() if key.lower() in _WS_FORWARD_HEADERS]
    try:
        upstream = await connect(
            url, subprotocols=offered or None, additional_headers=forward, max_size=MAX_REQUEST_BYTES,
            open_timeout=10, compression=None, user_agent_header=None, proxy=None,
        )
    except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as exc:
        logger.info("preview WebSocket の上流へ繋げません (run %s, %s)", run_id, type(exc).__name__)
        await websocket.close(code=1011)
        return
    await websocket.accept(subprotocol=upstream.subprotocol)

    async def client_to_upstream() -> tuple[int, str]:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return int(message.get("code") or 1000), str(message.get("reason") or "")
            data = message.get("text") if message.get("text") is not None else message.get("bytes")
            if data is None:
                continue
            if len(data) > MAX_REQUEST_BYTES:
                return 1009, "message too big"
            await upstream.send(data)

    async def upstream_to_client() -> tuple[int, str]:
        try:
            async for data in upstream:
                if isinstance(data, bytes):
                    await websocket.send_bytes(data)
                else:
                    await websocket.send_text(data)
        except ConnectionClosed:
            pass
        return int(upstream.close_code or 1000), str(upstream.close_reason or "")

    tasks = {asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finished = done.pop()
        for task in pending:
            task.cancel()
        try:
            code, reason = finished.result()
        except (WebSocketDisconnect, ConnectionClosed, RuntimeError):
            code, reason = 1000, ""
        code = code if code in _SENDABLE_CLOSE or 3000 <= code <= 4999 else 1000
        reason = reason[:120]
        await upstream.close(code=code, reason=reason)
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=code, reason=reason)
            except RuntimeError:
                pass
    finally:
        for task in tasks:
            task.cancel()
        await upstream.close()


@router.api_route("/{run_id}/{path:path}", methods=_METHODS)
async def project_view_proxy(
    run_id: int, path: str, request: Request,
    user: User = Depends(require_permission("project_lab.view")), db: Session = Depends(get_db),
):
    row = _run_target(db, run_id)
    return await _proxy_http(row, _upstream_path(row, path), request, prefix=f"/project-view/{run_id}/", cors=False)
