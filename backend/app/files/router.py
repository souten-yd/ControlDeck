from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.config import get_config
from app.database import get_db
from app.files import archives
from app.files import service as files
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/files", tags=["files"])


def _wrap(fn, *args):
    """サービス層の例外を HTTP エラーへ変換する。"""
    try:
        return fn(*args)
    except files.FileAccessError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (FileExistsError, NotADirectoryError, IsADirectoryError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"ファイル操作に失敗しました: {e.strerror or e}")


@router.get("/roots")
def roots(user: User = Depends(require_permission("files.view"))):
    return [str(r) for r in files.allowed_roots()]


@router.get("/list")
def list_dir(path: str, user: User = Depends(require_permission("files.view"))):
    return _wrap(files.list_dir, path)


@router.get("/info")
def info(path: str, user: User = Depends(require_permission("files.view"))):
    return _wrap(files.file_info, path)


@router.get("/download")
def download(path: str, user: User = Depends(require_permission("files.view"))):
    p: Path = _wrap(files.resolve, path)
    if p.is_dir():
        raise HTTPException(status_code=409, detail="ディレクトリはダウンロードできません")
    media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, filename=p.name, media_type=media_type)


@router.get("/preview")
def preview(path: str, user: User = Depends(require_permission("files.view"))):
    """画像／PDF／音声／動画をRange対応で安全にインライン配信する。"""
    p: Path = _wrap(files.resolve, path)
    if p.is_dir():
        raise HTTPException(status_code=409, detail="ディレクトリはプレビューできません")
    media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if not (media_type.startswith(("image/", "audio/", "video/")) or media_type == "application/pdf"):
        raise HTTPException(status_code=415, detail="このファイル形式はインラインプレビューできません")
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if media_type == "image/svg+xml":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
    return FileResponse(
        p, filename=p.name, media_type=media_type, headers=headers,
        content_disposition_type="inline",
    )


@router.get("/text")
def read_text(path: str, user: User = Depends(require_permission("files.view"))):
    return {"path": path, "content": _wrap(files.read_text, path)}


class WriteTextBody(BaseModel):
    path: str
    content: str


@router.put("/text")
def write_text(
    body: WriteTextBody,
    request: Request,
    user: User = Depends(require_permission("files.edit")),
    db: Session = Depends(get_db),
):
    _wrap(files.write_text, body.path, body.content)
    audit.record(db, "files.write", user=user, resource_type="file", resource_id=body.path, request=request)
    return {"ok": True}


@router.post("/upload")
async def upload(
    file: UploadFile,
    request: Request,
    directory: str = Query(...),
    overwrite: bool = False,
    user: User = Depends(require_permission("files.edit")),
    db: Session = Depends(get_db),
):
    name = Path(file.filename or "upload.bin").name
    try:
        dst = files.resolve(str(Path(directory) / name), must_exist=False)
    except files.FileAccessError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if dst.exists() and not overwrite:
        raise HTTPException(status_code=409, detail=f"既に存在します: {name}（上書きするには overwrite=true）")
    max_bytes = get_config().files.max_upload_size_gb * 1024**3
    written = 0
    try:
        with dst.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="アップロードサイズ上限を超えました")
                f.write(chunk)
    except HTTPException:
        dst.unlink(missing_ok=True)
        raise
    except OSError as e:
        dst.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"書き込みに失敗しました: {e.strerror or e}")
    audit.record(
        db, "files.upload", user=user, resource_type="file", resource_id=str(dst),
        request=request, metadata={"size": written},
    )
    return {"ok": True, "path": str(dst), "size": written}


class UploadCreateBody(BaseModel):
    directory: str
    filename: str
    size: int
    overwrite: bool = False


@router.post("/uploads", status_code=201)
def create_resumable_upload(
    body: UploadCreateBody, request: Request,
    user: User = Depends(require_permission("files.edit")), db: Session = Depends(get_db),
):
    result = _wrap(files.create_upload, body.directory, body.filename, body.size, body.overwrite, user.id)
    audit.record(db, "files.upload_start", user=user, resource_type="file", resource_id=body.filename,
                 request=request, metadata={"upload_id": result["id"], "size": body.size})
    return result


@router.get("/uploads/{upload_id}")
def resumable_upload_status(upload_id: str, user: User = Depends(require_permission("files.edit"))):
    _, _, result = _wrap(files.upload_meta, upload_id, user.id)
    return result


@router.put("/uploads/{upload_id}/chunk")
async def resumable_upload_chunk(
    upload_id: str, request: Request, offset: int = Query(ge=0),
    user: User = Depends(require_permission("files.edit")),
):
    chunk = await request.body()
    if not chunk:
        raise HTTPException(status_code=422, detail="空のチャンクです")
    return _wrap(files.append_upload, upload_id, user.id, offset, chunk)


@router.post("/uploads/{upload_id}/complete")
def finish_resumable_upload(
    upload_id: str, request: Request,
    user: User = Depends(require_permission("files.edit")), db: Session = Depends(get_db),
):
    result = _wrap(files.complete_upload, upload_id, user.id)
    audit.record(db, "files.upload", user=user, resource_type="file", resource_id=result["path"],
                 request=request, metadata={"size": result["size"], "resumable": True})
    return {"ok": True, **result}


@router.delete("/uploads/{upload_id}", status_code=204)
def cancel_resumable_upload(
    upload_id: str, request: Request,
    user: User = Depends(require_permission("files.edit")), db: Session = Depends(get_db),
):
    _wrap(files.cancel_upload, upload_id, user.id)
    audit.record(db, "files.upload_cancel", user=user, resource_type="upload", resource_id=upload_id, request=request)


@router.get("/trash")
def list_trash(user: User = Depends(require_permission("files.view"))):
    if not get_config().files.trash_enabled:
        return []
    return _wrap(files.list_trash, user.id)


@router.post("/trash/{item_id}/restore")
def restore_trash(
    item_id: str, request: Request,
    user: User = Depends(require_permission("files.delete")), db: Session = Depends(get_db),
):
    path = _wrap(files.restore_trash, item_id, user.id)
    audit.record(db, "files.restore", user=user, resource_type="file", resource_id=path, request=request)
    return {"ok": True, "path": path}


@router.delete("/trash/{item_id}")
def permanently_delete_trash(
    item_id: str, request: Request,
    user: User = Depends(require_permission("files.delete")), db: Session = Depends(get_db),
):
    _wrap(files.delete_trash, item_id, user.id)
    audit.record(db, "files.delete_permanent", user=user, resource_type="trash", resource_id=item_id, request=request)
    return {"ok": True}


@router.delete("/trash")
def empty_trash(
    request: Request, user: User = Depends(require_permission("files.delete")), db: Session = Depends(get_db),
):
    count = _wrap(files.empty_trash, user.id)
    audit.record(db, "files.trash_empty", user=user, resource_type="trash", request=request, metadata={"count": count})
    return {"ok": True, "count": count}


class PathBody(BaseModel):
    path: str


class SrcDstBody(BaseModel):
    source: str
    destination_dir: str


class RenameBody(BaseModel):
    path: str
    new_name: str


class ArchiveCreateBody(BaseModel):
    source: str
    destination: str
    format: str | None = None


class ArchiveExtractBody(BaseModel):
    archive: str
    destination: str


@router.post("/archive")
def create_archive(
    body: ArchiveCreateBody, request: Request,
    user: User = Depends(require_permission("files.edit")), db: Session = Depends(get_db),
):
    result = _wrap(archives.create, body.source, body.destination, body.format)
    audit.record(
        db, "files.archive_create", user=user, resource_type="file", resource_id=body.source,
        request=request, metadata={"to": result.path, "format": result.format, "entries": result.entries, "bytes": result.bytes},
    )
    return {"ok": True, **result.__dict__}


@router.post("/extract")
def extract_archive(
    body: ArchiveExtractBody, request: Request,
    user: User = Depends(require_permission("files.edit")), db: Session = Depends(get_db),
):
    result = _wrap(archives.extract, body.archive, body.destination)
    audit.record(
        db, "files.archive_extract", user=user, resource_type="file", resource_id=body.archive,
        request=request, metadata={"to": result.path, "format": result.format, "entries": result.entries, "bytes": result.bytes},
    )
    return {"ok": True, **result.__dict__}


@router.post("/directory")
def mkdir(
    body: PathBody,
    request: Request,
    user: User = Depends(require_permission("files.edit")),
    db: Session = Depends(get_db),
):
    _wrap(files.make_directory, body.path)
    audit.record(db, "files.mkdir", user=user, resource_type="file", resource_id=body.path, request=request)
    return {"ok": True}


@router.post("/copy")
def copy_path(
    body: SrcDstBody,
    request: Request,
    user: User = Depends(require_permission("files.edit")),
    db: Session = Depends(get_db),
):
    dst = _wrap(files.copy, body.source, body.destination_dir)
    audit.record(db, "files.copy", user=user, resource_type="file", resource_id=body.source, request=request, metadata={"to": dst})
    return {"ok": True, "path": dst}


@router.post("/move")
def move_path(
    body: SrcDstBody,
    request: Request,
    user: User = Depends(require_permission("files.edit")),
    db: Session = Depends(get_db),
):
    dst = _wrap(files.move, body.source, body.destination_dir)
    audit.record(db, "files.move", user=user, resource_type="file", resource_id=body.source, request=request, metadata={"to": dst})
    return {"ok": True, "path": dst}


@router.patch("/rename")
def rename_path(
    body: RenameBody,
    request: Request,
    user: User = Depends(require_permission("files.edit")),
    db: Session = Depends(get_db),
):
    dst = _wrap(files.rename, body.path, body.new_name)
    audit.record(db, "files.rename", user=user, resource_type="file", resource_id=body.path, request=request, metadata={"to": dst})
    return {"ok": True, "path": dst}


@router.delete("")
def delete_path(
    path: str,
    request: Request,
    permanent: bool = False,
    user: User = Depends(require_permission("files.delete")),
    db: Session = Depends(get_db),
):
    if get_config().files.trash_enabled and not permanent:
        item = _wrap(files.move_to_trash, path, user.id)
        audit.record(db, "files.trash", user=user, resource_type="file", resource_id=path, request=request,
                     metadata={"trash_id": item["id"]})
        return {"ok": True, "trashed": True, "trash_id": item["id"]}
    _wrap(files.delete, path)
    audit.record(db, "files.delete_permanent", user=user, resource_type="file", resource_id=path, request=request)
    return {"ok": True, "trashed": False}
