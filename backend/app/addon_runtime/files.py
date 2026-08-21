from __future__ import annotations

import os
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.addon_runtime import grants
from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addon_runtime.service import audit_runtime, host_job

router = APIRouter(prefix="/{addon_id}")
ReadAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability("files.pick"))]
WriteAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability("files.export"))]


class OutputCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=128)
    grant_id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0, le=grants.MAX_TRANSFER_BYTES)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_type: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("output filenameに制御文字は使用できません")
        return value


def _grant_error(exc: grants.GrantError, status_code: int = 404) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("/grants/{grant_id}")
def grant_metadata(grant_id: str, request: Request, principal: ReadAuth):
    try:
        result = grants.public_metadata(grants.load(grant_id, principal, kind="read"))
    except grants.GrantError as exc:
        raise _grant_error(exc) from exc
    audit_runtime(request, principal, "addon.runtime.file.grant.read", "addon_grant", grant_id, {"content": False})
    return result


@router.get("/grants/{grant_id}/content")
def grant_content(grant_id: str, request: Request, principal: ReadAuth):
    try:
        value = grants.load(grant_id, principal, kind="read")
        path = grants.resolved_grant(value)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if info.st_dev != value["device"] or info.st_ino != value["inode"] or info.st_size != value["size"]:
            os.close(descriptor)
            raise grants.GrantError("grant対象が選択後に変更されました")
    except grants.GrantError as exc:
        raise _grant_error(exc) from exc

    async def content() -> AsyncIterator[bytes]:
        with os.fdopen(descriptor, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk

    audit_runtime(request, principal, "addon.runtime.file.grant.read", "addon_grant", grant_id, {
        "content": True, "size": value["size"],
    })
    return StreamingResponse(
        content(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(value["size"]),
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/files/outputs", status_code=201)
def create_output(body: OutputCreate, request: Request, principal: WriteAuth):
    host_job(principal, body.job_id)
    try:
        result = grants.create_output(principal, **body.model_dump())
    except grants.GrantError as exc:
        raise _grant_error(exc, 422) from exc
    audit_runtime(request, principal, "addon.runtime.file.output.create", "addon_output", result["output_id"], {
        "job_id": body.job_id, "size": body.size,
    })
    return result


@router.put("/files/outputs/{output_id}/content")
async def upload_output(output_id: str, request: Request, principal: WriteAuth):
    try:
        value, _meta_path, part = grants.load_output(output_id, principal)
    except grants.GrantError as exc:
        raise _grant_error(exc) from exc
    received = 0
    try:
        descriptor = os.open(part, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "wb") as stream:
            async for chunk in request.stream():
                received += len(chunk)
                if received > value["size"] or received > grants.MAX_TRANSFER_BYTES:
                    raise HTTPException(status_code=413, detail="output contentが宣言sizeを超えています")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        part.unlink(missing_ok=True)
        descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        raise
    if received != value["size"]:
        raise HTTPException(status_code=409, detail="output content sizeが宣言値と一致しません")
    audit_runtime(request, principal, "addon.runtime.file.output.upload", "addon_output", output_id, {
        "job_id": value["job_id"], "size": received,
    })
    return {"output_id": output_id, "received": received}


@router.post("/files/outputs/{output_id}/commit")
def commit_output(output_id: str, request: Request, principal: WriteAuth):
    try:
        result = grants.commit_output(output_id, principal)
    except grants.GrantError as exc:
        raise _grant_error(exc, 409) from exc
    audit_runtime(request, principal, "addon.runtime.file.output.commit", "addon_output", output_id, {
        "job_id": result["job_id"], "asset_id": result["asset_id"], "size": result["size"],
    })
    return result
