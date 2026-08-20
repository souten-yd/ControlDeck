from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from app.addons import health, registry, tokens
from app.database import SessionLocal
from app.models import User
from app.security.deps import authenticate_websocket_user, get_current_user, user_permissions

router = APIRouter(prefix="/addon-frame", tags=["addon-frame"])

MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
_PRIVATE_REQUEST_HEADERS = {
    "cookie", "authorization", "proxy-authorization", "x-csrf-token", "x-requested-with",
    "origin", "referer",
}
_PRIVATE_RESPONSE_HEADERS = {"set-cookie", "set-cookie2"}


def _new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10, read=60), follow_redirects=False)


def _upstream_headers(request: Request, addon_id: str, user_id: int) -> dict[str, str]:
    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in _HOP_HEADERS | _PRIVATE_REQUEST_HEADERS
    }
    headers["Authorization"] = f"Bearer {tokens.issue(addon_id, subject=str(user_id), kind='service')}"
    headers["X-Control-Deck-Addon-ID"] = addon_id
    return headers


def _service_headers(addon_id: str, user_id: int) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tokens.issue(addon_id, subject=str(user_id), kind='service')}",
        "X-Control-Deck-Addon-ID": addon_id,
    }


def _connect_websocket(url: str, headers: dict[str, str], subprotocols: list[str]):
    return websockets.connect(
        url,
        additional_headers=headers,
        subprotocols=subprotocols or None,
        proxy=None,
        open_timeout=10,
        close_timeout=5,
        max_size=16 * 1024 * 1024,
    )


def _authorized_runtime(addon_id: str, permissions: set[str]) -> str:
    try:
        current = registry.status(addon_id)
    except registry.AddonRegistryError as exc:
        raise HTTPException(status_code=404, detail="拡張機能が登録されていません") from exc
    if not current["enabled"]:
        raise HTTPException(status_code=409, detail="拡張機能は無効です")
    effective = registry.effective_for_permissions(permissions)
    views = effective.get("contributions", {}).get("embedded_views", [])
    if not any(item["addon_id"] == addon_id for item in views):
        has_declared_view = bool(current.get("contributions", {}).get("embedded_views"))
        raise HTTPException(
            status_code=409 if has_declared_view else 404,
            detail="利用可能な埋め込み画面がありません",
        )
    return health.approved_health_url(current["runtime"]["base_url"], "/").rstrip("/")


def _content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
        if parsed < 0:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Content-Lengthが不正です") from exc


@router.api_route("/{addon_id}/{path:path}", methods=_METHODS)
async def addon_frame_proxy(
    addon_id: str,
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    base_url = _authorized_runtime(addon_id, user_permissions(user))
    declared_length = _content_length(request)
    if declared_length is not None and declared_length > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="拡張機能へのrequestが16MiBを超えています")
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="拡張機能へのrequestが16MiBを超えています")
    url = f"{base_url}/{path.lstrip('/')}"
    if request.url.query:
        url += f"?{request.url.query}"
    client = _new_http_client()
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                url,
                headers=_upstream_headers(request, addon_id, user.id),
                content=body,
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        registry.record_activity(addon_id, "addon-frame.http", "upstream_error")
        raise HTTPException(status_code=502, detail="拡張機能serviceへ接続できません") from exc

    content_type = upstream.headers.get("content-type", "")
    streaming = content_type.lower().startswith("text/event-stream")
    upstream_length = upstream.headers.get("content-length")
    if not streaming and upstream_length:
        try:
            too_large = int(upstream_length) > MAX_RESPONSE_BYTES
        except ValueError:
            too_large = False
        if too_large:
            await upstream.aclose()
            await client.aclose()
            registry.record_activity(addon_id, "addon-frame.http", "response_too_large")
            raise HTTPException(status_code=502, detail="拡張機能のresponseが32MiBを超えています")

    headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in _HOP_HEADERS | _PRIVATE_RESPONSE_HEADERS:
            continue
        if lower == "location" and value.startswith("/"):
            value = f"/addon-frame/{addon_id}{value}"
        headers[key] = value
    headers["Cache-Control"] = "no-store"
    headers["Content-Security-Policy"] = "sandbox allow-scripts allow-forms allow-popups allow-downloads"
    if streaming:
        headers["X-Accel-Buffering"] = "no"

    if not streaming:
        transferred = 0
        chunks: list[bytes] = []
        try:
            async for chunk in upstream.aiter_raw():
                transferred += len(chunk)
                if transferred > MAX_RESPONSE_BYTES:
                    registry.record_activity(addon_id, "addon-frame.http", "response_too_large")
                    raise HTTPException(status_code=502, detail="拡張機能のresponseが32MiBを超えています")
                chunks.append(chunk)
        except httpx.HTTPError as exc:
            registry.record_activity(addon_id, "addon-frame.http", "upstream_error")
            raise HTTPException(status_code=502, detail="拡張機能のresponseを受信できません") from exc
        finally:
            await upstream.aclose()
            await client.aclose()
        registry.record_activity(
            addon_id,
            "addon-frame.http",
            "success",
            {"status_code": upstream.status_code, "byte_count": transferred},
        )
        return Response(content=b"".join(chunks), status_code=upstream.status_code, headers=headers)

    async def stream() -> AsyncIterator[bytes]:
        transferred = 0
        result = "success"
        try:
            async for chunk in upstream.aiter_raw():
                transferred += len(chunk)
                yield chunk
        except httpx.HTTPError:
            result = "upstream_error"
        finally:
            await upstream.aclose()
            await client.aclose()
            registry.record_activity(
                addon_id,
                "addon-frame.http",
                result,
                {"status_code": upstream.status_code, "byte_count": transferred},
            )

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=headers)


@router.websocket("/{addon_id}/{path:path}")
async def addon_frame_websocket(websocket: WebSocket, addon_id: str, path: str):
    db = SessionLocal()
    try:
        user = await authenticate_websocket_user(websocket, db)
        if user is None:
            return
        user_id = user.id
        permissions = user_permissions(user)
    finally:
        db.close()
    try:
        base_url = _authorized_runtime(addon_id, permissions)
    except HTTPException as exc:
        await websocket.close(code={403: 4403, 404: 4404, 409: 4409}.get(exc.status_code, 4500))
        return
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    upstream_path = f"/{path.lstrip('/')}"
    url = urlunsplit((scheme, parsed.netloc, upstream_path, websocket.url.query, ""))
    requested_protocols = list(websocket.scope.get("subprotocols") or [])
    try:
        async with _connect_websocket(
            url,
            _service_headers(addon_id, user_id),
            requested_protocols,
        ) as upstream:
            selected_protocol = getattr(upstream, "subprotocol", None)
            await websocket.accept(subprotocol=selected_protocol if selected_protocol in requested_protocols else None)

            async def browser_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_browser() -> None:
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)

            first = asyncio.create_task(browser_to_upstream())
            second = asyncio.create_task(upstream_to_browser())
            done, pending = await asyncio.wait((first, second), return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
            registry.record_activity(addon_id, "addon-frame.websocket", "success")
    except (OSError, TimeoutError, websockets.WebSocketException) as exc:
        registry.record_activity(addon_id, "addon-frame.websocket", "upstream_error")
        try:
            await websocket.close(code=4502, reason="拡張機能のWebSocketへ接続できません")
        except RuntimeError:
            pass
    except (WebSocketDisconnect, RuntimeError):
        registry.record_activity(addon_id, "addon-frame.websocket", "closed")
