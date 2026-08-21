from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

from app.addon_runtime.auth import RuntimePrincipal
from app.addon_runtime.service import RuntimeAuthorityError, principal_user_id
from app.config import data_dir
from app.files import service as files
from app.jobs import service as jobs

GRANT_TTL_SECONDS = 60 * 60
MAX_TRANSFER_BYTES = 1024 * 1024 * 1024


class GrantError(RuntimeError):
    pass


def _root(name: str) -> Path:
    root = data_dir() / name
    if root.is_symlink():
        raise GrantError("Add-on file storageをsymlinkにはできません")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved = root.resolve()
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise GrantError("Add-on file storageのownerまたはmodeが不正です")
    return resolved


def _id(value: str) -> str:
    value = value.removeprefix("grant:")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise GrantError("grant/output IDが不正です") from exc


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise GrantError("grant metadataが不正です")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise GrantError("grant metadataが不正です")
        return value
    except FileNotFoundError as exc:
        raise GrantError("grant/outputが見つかりません") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise GrantError("grant metadataが不正です") from exc


def create(addon_id: str, owner_user_id: int, path: str, kind: str) -> dict[str, Any]:
    if kind not in {"read", "export"}:
        raise GrantError("grant kindが不正です")
    resolved = files.resolve(path)
    if kind == "read" and not resolved.is_file():
        raise GrantError("read grantにはfileを選択してください")
    if kind == "export" and not resolved.is_dir():
        raise GrantError("export grantにはdirectoryを選択してください")
    info = resolved.stat()
    if kind == "read" and info.st_size > MAX_TRANSFER_BYTES:
        raise GrantError("read grantのfile sizeが上限を超えています")
    grant_id = str(uuid.uuid4())
    now = time.time()
    value = {
        "id": grant_id,
        "addon_id": addon_id,
        "owner_user_id": owner_user_id,
        "kind": kind,
        "path": str(resolved),
        "name": resolved.name,
        "size": info.st_size if kind == "read" else None,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "created_at": now,
        "expires_at": now + GRANT_TTL_SECONDS,
    }
    _atomic_json(_root("addon-grants") / f"{grant_id}.json", value)
    return public_metadata(value)


def _subject_user_id(principal: RuntimePrincipal) -> int:
    if principal.subject.startswith("job:"):
        job = jobs.get(principal.subject.removeprefix("job:"))
        if job is None or not jobs.addon_owns(job, principal.addon_id) or job.owner_user_id is None:
            raise GrantError("service tokenのJob ownerを解決できません")
        return job.owner_user_id
    try:
        return principal_user_id(principal)
    except RuntimeAuthorityError as exc:
        raise GrantError("service token subjectをgrant ownerに解決できません") from exc


def load(grant_id: str, principal: RuntimePrincipal, *, kind: str | None = None) -> dict[str, Any]:
    normalized_grant_id = f"grant:{_id(grant_id)}"
    if principal.grant_ids is not None and normalized_grant_id not in principal.grant_ids:
        raise GrantError("grantが見つかりません")
    value = _read(_root("addon-grants") / f"{_id(normalized_grant_id)}.json")
    if value.get("addon_id") != principal.addon_id or value.get("owner_user_id") != _subject_user_id(principal):
        raise GrantError("grantが見つかりません")
    if float(value.get("expires_at", 0)) <= time.time():
        raise GrantError("grantの有効期限が切れています")
    if kind is not None and value.get("kind") != kind:
        raise GrantError("grant kindが一致しません")
    return value


def public_metadata(value: dict[str, Any]) -> dict[str, Any]:
    display_name = "".join(character for character in str(value["name"]) if ord(character) >= 32 and ord(character) != 127)
    return {
        "grant_id": f"grant:{value['id']}",
        "kind": value["kind"],
        "name": display_name or "selected",
        "size": value.get("size"),
        "expires_at": value["expires_at"],
    }


def resolved_grant(value: dict[str, Any]) -> Path:
    try:
        resolved = files.resolve(value["path"])
        info = resolved.stat()
    except (FileNotFoundError, OSError, files.FileAccessError) as exc:
        raise GrantError("grant対象を安全に解決できません") from exc
    if (
        str(resolved) != value["path"]
        or info.st_dev != value["device"]
        or info.st_ino != value["inode"]
        or (value["kind"] == "read" and (
            info.st_size != value["size"] or info.st_mtime_ns != value["mtime_ns"]
        ))
    ):
        raise GrantError("grant対象が選択後に変更されました")
    return resolved


def create_output(
    principal: RuntimePrincipal,
    *,
    job_id: str,
    grant_id: str,
    filename: str,
    size: int,
    sha256: str | None,
    content_type: str | None,
) -> dict[str, Any]:
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise GrantError("output filenameが不正です")
    if size < 0 or size > MAX_TRANSFER_BYTES:
        raise GrantError("output sizeが上限を超えています")
    destination = load(grant_id, principal, kind="export")
    resolved_grant(destination)
    output_id = str(uuid.uuid4())
    output_root = _root("addon-outputs")
    part = output_root / f"{output_id}.part"
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    meta = {
        "id": output_id,
        "addon_id": principal.addon_id,
        "owner_user_id": _subject_user_id(principal),
        "job_id": job_id,
        "grant_id": grant_id,
        "filename": filename,
        "size": size,
        "sha256": sha256,
        "content_type": content_type or "application/octet-stream",
        "created_at": time.time(),
        "expires_at": time.time() + GRANT_TTL_SECONDS,
    }
    _atomic_json(output_root / f"{output_id}.json", meta)
    return {"output_id": output_id, "name": filename, "size": size, "received": 0}


def load_output(output_id: str, principal: RuntimePrincipal) -> tuple[dict[str, Any], Path, Path]:
    safe = _id(output_id)
    root = _root("addon-outputs")
    meta_path = root / f"{safe}.json"
    part = root / f"{safe}.part"
    value = _read(meta_path)
    if value.get("addon_id") != principal.addon_id or value.get("owner_user_id") != _subject_user_id(principal):
        raise GrantError("outputが見つかりません")
    if float(value.get("expires_at", 0)) <= time.time():
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise GrantError("output stagingの有効期限が切れています")
    try:
        info = part.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise GrantError("output staging fileが不正です")
    except FileNotFoundError as exc:
        raise GrantError("outputが見つかりません") from exc
    return value, meta_path, part


def commit_output(output_id: str, principal: RuntimePrincipal) -> dict[str, Any]:
    value, meta_path, part = load_output(output_id, principal)
    if part.stat().st_size != value["size"]:
        raise GrantError("output uploadが完了していません")
    expected = value.get("sha256")
    if expected:
        digest = hashlib.sha256()
        with part.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise GrantError("output checksumが一致しません")
    destination_grant = load(value["grant_id"], principal, kind="export")
    destination_dir = resolved_grant(destination_grant)
    directory_fd = os.open(destination_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_info = os.fstat(directory_fd)
        if directory_info.st_dev != destination_grant["device"] or directory_info.st_ino != destination_grant["inode"]:
            raise GrantError("export grant対象が選択後に変更されました")
        try:
            os.stat(value["filename"], dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise GrantError("同名のoutputがすでに存在します")
        os.replace(part, value["filename"], dst_dir_fd=directory_fd)
        info = os.stat(value["filename"], dir_fd=directory_fd, follow_symlinks=False)
    finally:
        os.close(directory_fd)
    destination = destination_dir / value["filename"]
    asset_id = str(uuid.uuid4())
    _atomic_json(_root("addon-assets") / f"{asset_id}.json", {
        "id": asset_id,
        "addon_id": principal.addon_id,
        "owner_user_id": value["owner_user_id"],
        "job_id": value["job_id"],
        "path": str(destination),
        "name": destination.name,
        "size": info.st_size,
        "content_type": value["content_type"],
        "device": info.st_dev,
        "inode": info.st_ino,
        "created_at": time.time(),
    })
    meta_path.unlink(missing_ok=True)
    return {
        "asset_id": f"asset:{asset_id}",
        "job_id": value["job_id"],
        "name": destination.name,
        "size": info.st_size,
        "content_type": value["content_type"],
    }
