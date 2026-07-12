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
    """インライン表示用（画像等）。Content-Disposition を付けない。"""
    p: Path = _wrap(files.resolve, path)
    if p.is_dir():
        raise HTTPException(status_code=409, detail="ディレクトリはプレビューできません")
    media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media_type)


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


class PathBody(BaseModel):
    path: str


class SrcDstBody(BaseModel):
    source: str
    destination_dir: str


class RenameBody(BaseModel):
    path: str
    new_name: str


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
    user: User = Depends(require_permission("files.delete")),
    db: Session = Depends(get_db),
):
    _wrap(files.delete, path)
    audit.record(db, "files.delete", user=user, resource_type="file", resource_id=path, request=request)
    return {"ok": True}
