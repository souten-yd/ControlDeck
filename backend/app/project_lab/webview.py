"""Project Run専用の同一origin preview proxy。

上流はDBへ割り当てたlocalhost portに固定し、systemd unitのprocess treeが
そのportをLISTENしている場合だけ接続する。ControlDeckの資格情報は渡さない。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProjectRun, User
from app.project_lab import runs
from app.security.deps import require_permission

router = APIRouter(prefix="/project-view", tags=["project-lab-preview"])

MAX_REQUEST_BYTES = 16 * 1024 * 1024
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
_PRIVATE_REQUEST_HEADERS = {"cookie", "authorization", "proxy-authorization", "x-csrf-token"}
_PRIVATE_RESPONSE_HEADERS = {"set-cookie", "set-cookie2"}


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


@router.api_route("/{run_id}/{path:path}", methods=_METHODS)
async def project_view_proxy(
    run_id: int, path: str, request: Request,
    user: User = Depends(require_permission("project_lab.view")), db: Session = Depends(get_db),
):
    row = _run_target(db, run_id)
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
    url = f"http://127.0.0.1:{row.web_port}/{path}"
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

    headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in _HOP_HEADERS | _PRIVATE_RESPONSE_HEADERS:
            continue
        if lower == "location" and value.startswith("/"):
            value = f"/project-view/{run_id}{value}"
        headers[key] = value
    headers["Cache-Control"] = "no-store"

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=headers)
