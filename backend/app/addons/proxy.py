from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.addons import bridge, health, registry, tokens
from app.auth.policy import totp_required_for
from app.database import SessionLocal, get_db
from app.models import User
from app.security.deps import authenticate_websocket_user, user_permissions
from app.security.rate_limit import api_rate_limiter
from app.security.sessions import SESSION_COOKIE, resolve_session
from app.config import get_config

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
    "x-control-deck-bridge-session", "origin", "referer",
}
_PRIVATE_RESPONSE_HEADERS = {"set-cookie", "set-cookie2"}
_BRIDGE_PROTOCOL_PREFIX = "control-deck-bridge."
_FRAME_COOKIE = "cd_addon_frame"
_FRAME_BRIDGE_HEADER = "x-control-deck-bridge-session"
_FRAME_PREFLIGHT_HEADERS = {"content-type", _FRAME_BRIDGE_HEADER}
_FRAME_STATIC_DESTINATIONS = {"audio", "font", "image", "script", "style", "track", "video"}


def _new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10, read=60), follow_redirects=False)


def _upstream_headers(request: Request, addon_id: str, user_id: int) -> dict[str, str]:
    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in _HOP_HEADERS | _PRIVATE_REQUEST_HEADERS
    }
    token = tokens.issue(addon_id, subject=str(user_id), kind="service", actor_user_id=user_id)
    headers["Authorization"] = f"Bearer {token}"
    headers["X-Control-Deck-Addon-ID"] = addon_id
    return headers


def _service_headers(addon_id: str, user_id: int) -> dict[str, str]:
    token = tokens.issue(addon_id, subject=str(user_id), kind="service", actor_user_id=user_id)
    return {
        "Authorization": f"Bearer {token}",
        "X-Control-Deck-Addon-ID": addon_id,
    }


def _frame_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    requested_headers = {
        header.strip().lower()
        for header in request.headers.get("access-control-request-headers", "").split(",")
        if header.strip()
    }
    if (
        request.method == "OPTIONS"
        and request.headers.get("origin") == "null"
        and request.headers.get("access-control-request-method") in _METHODS
        and _FRAME_BRIDGE_HEADER in requested_headers
        and requested_headers <= _FRAME_PREFLIGHT_HEADERS
    ):
        return None
    resolved = resolve_session(db, request.cookies.get(SESSION_COOKIE, ""))
    if resolved is not None:
        _session, user = resolved
    else:
        addon_id = request.path_params.get("addon_id", "")
        try:
            payload = tokens.verify(
                request.cookies.get(_FRAME_COOKIE, ""),
                addon_id=addon_id,
                kind="frame",
            )
            actor_user_id = payload.get("actor_user_id")
            if (
                not isinstance(actor_user_id, int)
                or isinstance(actor_user_id, bool)
                or payload.get("sub") != str(actor_user_id)
            ):
                raise tokens.AddonTokenError("frame token actorが不正です")
            user = db.get(User, actor_user_id, options=[joinedload(User.role)])
        except (tokens.AddonTokenError, ValueError, TypeError):
            user = None
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="認証が必要です")
        requires_bridge = request.method not in {"GET", "HEAD", "OPTIONS"} or (
            request.headers.get("origin") == "null"
            and request.headers.get("sec-fetch-dest", "") not in _FRAME_STATIC_DESTINATIONS
        )
        if requires_bridge:
            try:
                bridge_user, _permissions = bridge.authenticate_websocket_session(
                    addon_id,
                    request.headers.get(_FRAME_BRIDGE_HEADER, ""),
                    db,
                )
            except bridge.BridgeAccessError as exc:
                raise HTTPException(status_code=403, detail="Bridge sessionが必要です") from exc
            if bridge_user.id != user.id:
                raise HTTPException(status_code=403, detail="Bridge session scopeが一致しません")
    if totp_required_for(user) and not user.totp_enabled:
        raise HTTPException(status_code=403, detail="totp_setup_required")
    # ここから先は DB を使わない。上流の Add-on への往復が終わるまで session を
    # 抱えていると、その間ずっと pool の接続を 1 本占める。frame は 1 画面で
    # 何十本も同時に飛ぶので、それだけで pool が空になり、次の要求が接続待ちで
    # event loop ごと止まる。用が済んだ時点で返す。
    db.close()
    return user


def _frame_cookie(addon_id: str, user_id: int) -> str:
    token = tokens.issue(
        addon_id,
        subject=str(user_id),
        kind="frame",
        actor_user_id=user_id,
    )
    return (
        f"{_FRAME_COOKIE}={token}; Path=/addon-frame/{addon_id}/; "
        f"Max-Age={tokens.TOKEN_TTL_SECONDS}; HttpOnly; Secure; SameSite=None"
    )


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
    user: User | None = Depends(_frame_user),
):
    if user is None:
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "null",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": request.headers["access-control-request-method"],
                "Access-Control-Allow-Headers": request.headers["access-control-request-headers"],
                "Access-Control-Max-Age": "600",
            },
        )
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
    headers["Set-Cookie"] = _frame_cookie(addon_id, user.id)
    if request.headers.get("origin") == "null":
        headers["Access-Control-Allow-Origin"] = "null"
        headers["Access-Control-Allow-Credentials"] = "true"
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
        requested_protocols = list(websocket.scope.get("subprotocols") or [])
        bridge_protocol = next((item for item in requested_protocols if item.startswith(_BRIDGE_PROTOCOL_PREFIX)), None)
        if bridge_protocol is not None:
            peer = websocket.client.host if websocket.client else "unknown"
            allowed, _retry_after = api_rate_limiter.check(
                "websocket", peer, get_config().security.websocket_rate_limit_per_minute,
            )
            if not allowed:
                await websocket.close(code=4429, reason="rate limited")
                return
            if websocket.headers.get("origin") != "null":
                await websocket.close(code=4403)
                return
            try:
                user, permissions = bridge.authenticate_websocket_session(
                    addon_id,
                    bridge_protocol.removeprefix(_BRIDGE_PROTOCOL_PREFIX),
                    db,
                )
            except bridge.BridgeAccessError:
                await websocket.close(code=4403)
                return
            user_id = user.id
        else:
            user = await authenticate_websocket_user(websocket, db, allow_opaque_origin=True)
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
    upstream_protocols = [item for item in requested_protocols if not item.startswith(_BRIDGE_PROTOCOL_PREFIX)]
    try:
        async with _connect_websocket(
            url,
            _service_headers(addon_id, user_id),
            upstream_protocols,
        ) as upstream:
            selected_protocol = getattr(upstream, "subprotocol", None)
            accepted_protocol = selected_protocol if selected_protocol in upstream_protocols else bridge_protocol
            await websocket.accept(subprotocol=accepted_protocol)

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
