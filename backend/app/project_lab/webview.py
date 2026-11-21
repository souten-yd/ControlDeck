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
import functools
import hmac
import html
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.database import SessionLocal, get_db
from app.models import ProjectRun, User
from app.project_lab import live_channel, live_paths, runs, service
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


MAX_INJECT_BYTES = 8 * 1024 * 1024
LIVE_JS_PATH = Path(__file__).parent / "static" / "live.js"
_HEAD_CLOSE = re.compile(rb"</head\s*>", re.I)
_BODY_CLOSE = re.compile(rb"</body\s*>", re.I)


def _inject_live_script(body: bytes, prefix: str) -> bytes:
    """`</head>`（無ければ `</body>`、無ければ末尾）の前へ live.js の script を 1 本入れる。"""
    tag = f'<script src="{html.escape(prefix, quote=True)}__cd/live.js" defer></script>'.encode("ascii", "replace")
    match = _HEAD_CLOSE.search(body)
    if match is None:
        matches = list(_BODY_CLOSE.finditer(body))
        match = matches[-1] if matches else None
    index = match.start() if match else len(body)
    return body[:index] + tag + body[index:]


def _is_html(upstream: httpx.Response) -> bool:
    return upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/html"


def _is_encoded(upstream: httpx.Response) -> bool:
    return upstream.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}


@functools.lru_cache(maxsize=1)
def _live_js() -> bytes:
    return LIVE_JS_PATH.read_bytes()


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

    async def send(headers: dict[str, str]) -> httpx.Response:
        return await client.send(
            client.build_request(request.method, url, headers=headers, content=body), stream=True,
        )

    request_headers = _upstream_headers(request)
    try:
        upstream = await send(request_headers)
        inject = cors and request.method == "GET" and upstream.status_code == 200 and _is_html(upstream)
        if inject and _is_encoded(upstream):
            # 圧縮された HTML には差し込めない。圧縮なし（identity）で頼み直す
            await upstream.aclose()
            upstream = await send({
                **{k: v for k, v in request_headers.items() if k.lower() != "accept-encoding"},
                "accept-encoding": "identity",
            })
            inject = upstream.status_code == 200 and _is_html(upstream) and not _is_encoded(upstream)
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

    buffered: list[bytes] = []
    iterator = upstream.aiter_raw()   # 1 回しか作れない。差し込み判定で読んだ続きから流す
    if inject:
        declared = upstream.headers.get("content-length", "")
        too_big = declared.isdigit() and int(declared) > MAX_INJECT_BYTES
        total = 0
        if too_big:
            inject = False
        else:
            try:
                async for chunk in iterator:
                    buffered.append(chunk)
                    total += len(chunk)
                    if total > MAX_INJECT_BYTES:
                        inject = False   # 8 MiB を超えたら差し込まず、そのまま流す
                        break
            except httpx.HTTPError as exc:
                await upstream.aclose()
                await client.aclose()
                raise HTTPException(status_code=502, detail="Web applicationから読めません") from exc
            if inject:
                await upstream.aclose()
                await client.aclose()
                injected = _inject_live_script(b"".join(buffered), prefix)
                headers["content-length"] = str(len(injected))
                return Response(content=injected, status_code=upstream.status_code, headers=headers)

    async def stream():
        try:
            for chunk in buffered:
                yield chunk
            async for chunk in iterator:
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=headers)


def _live_context(run_id: int, token: str) -> tuple[Path, dict | None]:
    """live.js・`__cd/live` 用の確認。鍵が合い、run が動いていればよい（上流の起動待ちは問わない。
    ビルド中・再起動中も、ControlDeck が状態を答えるため）。"""
    with SessionLocal() as db:
        row = db.get(ProjectRun, run_id)
        if row is None or not hmac.compare_digest(token.encode(), runs.preview_token(row).encode()):
            raise HTTPException(status_code=404, detail="Web preview runが見つかりません")
        runs.refresh_run(db, row)
        if row.status not in runs.RUNNING_STATES:
            raise HTTPException(status_code=409, detail="Web previewは終了しています")
        project_id = row.project_id
    try:
        project = service.resolve_project(project_id)
    except service.ProjectLabError as exc:
        raise HTTPException(status_code=404, detail="projectが見つかりません") from exc
    return project, live_paths.read_spec(run_id)


def _run_alive(run_id: int) -> bool:
    with SessionLocal() as db:
        row = db.get(ProjectRun, run_id)
        if row is None:
            return False
        runs.refresh_run(db, row)
        return row.status in runs.RUNNING_STATES


def _wait_preview_ready(run_id: int, timeout: float = 30.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            row = db.get(ProjectRun, run_id)
            if row is None:
                return False
            if runs.web_preview_ready(row):
                return True
        time.sleep(0.3)
    return False


@router.get("/r/{run_id}/{token}/__cd/live.js")
async def project_view_live_js(run_id: int, token: str):
    """画面へ差し込む live.js。上流へは送らず、ControlDeck が答える。"""
    await asyncio.to_thread(_live_context, run_id, token)
    body = await asyncio.to_thread(_live_js)
    return Response(content=body, media_type="application/javascript", headers={
        "Cache-Control": "no-store", "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
    })


LIVE_POLL_SECONDS = 0.3
LIVE_ALIVE_CHECK_SECONDS = 10.0


@router.websocket("/r/{run_id}/{token}/__cd/live")
async def project_view_live_websocket(websocket: WebSocket, run_id: int, token: str):
    """画面（live.js）との口。上流へは中継しない。

    - 見張り役の状態（`.controldeck/live/<run_id>.jsonl`）の新しい行を `status` として送る。
      mode が restart で command が起き直したら（preview が繋がるのを待って）`reload` も送る
    - AI の知らせ（`.controldeck/live/<run_id>.notes` の新しい行）を `note` として送る
    - 画面から来た記録を `.controldeck/feedback/<run_id>.jsonl` へ追記する（大きさ・頻度・総量に上限）
    """
    try:
        project, spec = await asyncio.to_thread(_live_context, run_id, token)
    except HTTPException:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    mode = (spec or {}).get("mode")
    state_path = live_paths.state_file(project, run_id)
    notes_path = live_paths.notes_file(project, run_id)

    async def send(payload: dict) -> None:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    def status_payload(state: dict) -> dict:
        tail = state.get("error_tail")
        return {
            "type": "status", "phase": state.get("phase"), "changed": state.get("changed") or [],
            "exit_code": state.get("exit_code"),
            "error_tail": service.redact_text(tail) if isinstance(tail, str) else "",
        }

    async def sender() -> None:
        # 見張りが無い run（native・watch なし）は状態を送らない
        watching = spec is not None
        state_offset = 0
        if watching:
            lines, state_offset = await asyncio.to_thread(live_channel.read_new_lines, state_path, 0)
            last = next((st for st in map(live_channel.parse_state, reversed(lines)) if st), None)
            if last:
                await send(status_payload(last))
        try:
            notes_offset = (await asyncio.to_thread(notes_path.stat)).st_size
        except OSError:
            notes_offset = 0
        since_check = 0.0
        while True:
            await asyncio.sleep(LIVE_POLL_SECONDS)
            since_check += LIVE_POLL_SECONDS
            if watching:
                lines, state_offset = await asyncio.to_thread(live_channel.read_new_lines, state_path, state_offset)
                for line in lines:
                    state = live_channel.parse_state(line)
                    if state is None:
                        continue
                    await send(status_payload(state))
                    if mode == "restart" and state.get("phase") == "running":
                        if await asyncio.to_thread(_wait_preview_ready, run_id):
                            await send({"type": "reload"})
            lines, notes_offset = await asyncio.to_thread(live_channel.read_new_lines, notes_path, notes_offset)
            for line in lines:
                await send({"type": "note", "text": line.strip()[:live_channel.MAX_NOTE_CHARS]})
            if since_check >= LIVE_ALIVE_CHECK_SECONDS:
                since_check = 0.0
                if not await asyncio.to_thread(_run_alive, run_id):
                    await websocket.close(code=1001)
                    return

    async def receiver() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None:
                continue
            reason = await asyncio.to_thread(live_channel.accept_record, project, run_id, text)
            if reason:
                await send({"type": "dropped", "reason": reason})

    tasks = {asyncio.create_task(sender()), asyncio.create_task(receiver())}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except Exception:  # noqa: BLE001
        logger.exception("live WebSocket が失敗しました (run %s)", run_id)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError, Exception):  # noqa: BLE001
                pass
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except RuntimeError:
                pass


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
