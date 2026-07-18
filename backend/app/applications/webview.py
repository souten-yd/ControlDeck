"""アプリ内 Web ビュー用の同一オリジン reverse proxy。

Tailscale Serve 等の HTTPS 経由で Control Deck を開いている場合、iframe で
`http://host:port` を直接埋め込むと混在コンテンツとしてブロックされ黒画面になる。
`/appview/{app_id}/...` を同一オリジンで提供してこれを回避する。

絶対パス（/static 等）で配信するアプリにも対応するため、main.py 側の
referer フォールバック（/appview/{id}/ 由来のリクエストを proxy へ 307）と併用する。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.database import get_db
from app.models import ManagedApplication, User
from app.security.deps import require_permission
from app.security.sessions import SESSION_COOKIE

router = APIRouter(prefix="/appview", tags=["appview"])

_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def _upstream_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lower = key.lower()
        if lower in _HOP_HEADERS or lower == "cookie":
            continue
        headers[key] = value
    # Control Deck のセッション cookie はアプリへ渡さない（セッション窃取防止）
    cookies = [c.strip() for c in request.headers.get("cookie", "").split(";") if c.strip()]
    forwarded = [c for c in cookies if not c.startswith(f"{SESSION_COOKIE}=")]
    if forwarded:
        headers["cookie"] = "; ".join(forwarded)
    return headers


@router.api_route("/{app_id}/{path:path}", methods=_METHODS)
async def appview_proxy(
    app_id: int, path: str, request: Request,
    user: User = Depends(require_permission("apps.view")), db=Depends(get_db),
):
    row = db.get(ManagedApplication, app_id)
    port = int(row.web_port) if row is not None and row.web_port else None
    if port is None:
        raise HTTPException(status_code=404, detail="Webポート未設定のアプリです")
    url = f"http://127.0.0.1:{port}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    body = await request.body()
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=_upstream_headers(request), content=body),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"アプリへ接続できません: {exc}") from exc

    headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in _HOP_HEADERS:
            continue
        # アプリ内リダイレクトは proxy prefix を維持する
        if lower == "location" and value.startswith("/"):
            value = f"/appview/{app_id}{value}"
        headers[key] = value

    async def stream():
        try:
            # aiter_raw は content-encoding を保ったまま転送する（ヘッダーと整合）
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=headers)
