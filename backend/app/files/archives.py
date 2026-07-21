"""許可ルート内で完結する安全なZIP／tar.gz作成・展開。"""
from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import tarfile
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import get_config
from app.files import service as files
from app.security.paths import is_within

_MAX_ENTRIES = 100_000
_MAX_COMPRESSION_RATIO = 200
_RATIO_GRACE_BYTES = 16 * 1024 * 1024
_FREE_SPACE_RESERVE = 64 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


@dataclass(frozen=True)
class ArchiveResult:
    path: str
    entries: int
    bytes: int
    format: str


@dataclass(frozen=True)
class _SourceEntry:
    path: Path
    name: str
    is_dir: bool
    size: int
    mode: int
    device: int
    inode: int


@dataclass(frozen=True)
class _ExtractEntry:
    source: object
    name: str
    is_dir: bool
    size: int
    mode: int


def _limit_bytes() -> int:
    return get_config().files.max_upload_size_gb * 1024**3


def _ensure_free_space(parent: Path, expected_bytes: int) -> None:
    free = shutil.disk_usage(parent).free
    if expected_bytes > max(0, free - _FREE_SPACE_RESERVE):
        raise files.FileAccessError("アーカイブ操作に必要な空き容量がありません")


def _publish_noreplace(temporary: Path, destination: Path) -> None:
    """Linux renameat2で一時成果物を既存pathへ上書きせず原子的に公開する。"""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise files.FileAccessError("原子的な上書き防止を利用できません")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(_AT_FDCWD, os.fsencode(temporary), _AT_FDCWD, os.fsencode(destination), _RENAME_NOREPLACE) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(f"既に存在します: {destination.name}")
    raise OSError(error, os.strerror(error), str(destination))


def _archive_format(path: Path, requested: str | None = None) -> str:
    value = (requested or "").strip().lower()
    lowered = path.name.lower()
    inferred = "zip" if lowered.endswith(".zip") else "tar.gz" if lowered.endswith((".tar.gz", ".tgz")) else None
    if value:
        selected = "zip" if value == "zip" else "tar.gz" if value in {"tar.gz", "tgz"} else None
        if selected is None or inferred != selected:
            raise files.FileAccessError("指定形式と保存先の拡張子が一致しません")
        return selected
    if inferred is not None:
        return inferred
    raise files.FileAccessError("対応形式は .zip / .tar.gz / .tgz です")


def _source_entries(source: Path) -> tuple[list[_SourceEntry], int]:
    paths: Iterable[Path] = [source]
    if source.is_dir():
        paths = [source, *sorted(source.rglob("*"), key=lambda item: item.as_posix())]
    rows: list[_SourceEntry] = []
    total = 0
    for path in paths:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise files.FileAccessError("シンボリックリンクを含む項目は圧縮できません")
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            raise files.FileAccessError("通常ファイルとディレクトリ以外は圧縮できません")
        name = source.name if path == source else f"{source.name}/{path.relative_to(source).as_posix()}"
        size = st.st_size if stat.S_ISREG(st.st_mode) else 0
        total += size
        rows.append(_SourceEntry(
            path, name, stat.S_ISDIR(st.st_mode), size, st.st_mode & 0o777,
            st.st_dev, st.st_ino,
        ))
        if len(rows) > _MAX_ENTRIES:
            raise files.FileAccessError(f"アーカイブ項目数は最大{_MAX_ENTRIES:,}件です")
        if total > _limit_bytes():
            raise files.FileAccessError("圧縮対象の合計サイズが上限を超えました")
    return rows, total


def _open_source(row: _SourceEntry):
    """検査後にsymlink／別fileへ差し替えられていないsourceを開く。"""
    try:
        if row.path.resolve(strict=True) != row.path:
            raise files.FileAccessError("圧縮対象が検査後に変更されました")
        descriptor = os.open(row.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise files.FileAccessError("圧縮対象を安全に読み取れません") from exc
    current = os.fstat(descriptor)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != row.device
        or current.st_ino != row.inode
        or current.st_size != row.size
    ):
        os.close(descriptor)
        raise files.FileAccessError("圧縮対象が検査後に変更されました")
    return os.fdopen(descriptor, "rb")


def _copy_source(row: _SourceEntry, output) -> None:
    remaining = row.size
    with _open_source(row) as source:
        while remaining:
            chunk = source.read(min(_COPY_CHUNK, remaining))
            if not chunk:
                raise files.FileAccessError("圧縮対象のsizeが検査後に変わりました")
            output.write(chunk)
            remaining -= len(chunk)


def create(source_path: str, destination_path: str, archive_format: str | None = None) -> ArchiveResult:
    source = files.resolve(source_path)
    if not source.is_file() and not source.is_dir():
        raise files.FileAccessError("通常ファイルまたはディレクトリを指定してください")
    destination = files.resolve(destination_path, must_exist=False)
    if destination.exists():
        raise FileExistsError(f"既に存在します: {destination.name}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"保存先フォルダが存在しません: {destination.parent}")
    if source.is_dir() and is_within(destination, source):
        raise files.FileAccessError("圧縮ファイルを圧縮対象フォルダ内へ作成できません")
    fmt = _archive_format(destination, archive_format)
    rows, total = _source_entries(source)
    _ensure_free_space(destination.parent, total + len(rows) * 512 + 1024 * 1024)
    temporary = destination.parent / f".control-deck-archive-{uuid.uuid4().hex}.tmp"
    try:
        if fmt == "zip":
            with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for row in rows:
                    if row.is_dir:
                        info = zipfile.ZipInfo(row.name.rstrip("/") + "/")
                        info.external_attr = ((stat.S_IFDIR | row.mode) & 0xFFFF) << 16
                        archive.writestr(info, b"")
                    else:
                        info = zipfile.ZipInfo(row.name)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = ((stat.S_IFREG | row.mode) & 0xFFFF) << 16
                        with archive.open(info, "w") as output:
                            _copy_source(row, output)
        else:
            with tarfile.open(temporary, "x:gz", compresslevel=6) as archive:
                for row in rows:
                    info = tarfile.TarInfo(row.name)
                    info.type = tarfile.DIRTYPE if row.is_dir else tarfile.REGTYPE
                    info.size = 0 if row.is_dir else row.size
                    info.mode = row.mode
                    info.uid = info.gid = 0
                    if row.is_dir:
                        archive.addfile(info)
                    else:
                        with _open_source(row) as source_file:
                            archive.addfile(info, source_file)
        _publish_noreplace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return ArchiveResult(str(destination), len(rows), total, fmt)


def _safe_member_name(raw_name: str) -> str:
    normalized = raw_name.replace("\\", "/")
    if "\x00" in normalized:
        raise files.FileAccessError("アーカイブに不正なパスが含まれています")
    path = PurePosixPath(normalized)
    parts = [part for part in path.parts if part not in ("", ".")]
    if path.is_absolute() or not parts or any(part == ".." for part in parts):
        raise files.FileAccessError("アーカイブに許可されないパスが含まれています")
    name = "/".join(parts)
    if len(name.encode("utf-8")) > 4096:
        raise files.FileAccessError("アーカイブ内のパスが長すぎます")
    return name


def _validate_totals(rows: list[_ExtractEntry], compressed_size: int) -> int:
    if len(rows) > _MAX_ENTRIES:
        raise files.FileAccessError(f"アーカイブ項目数は最大{_MAX_ENTRIES:,}件です")
    names: set[str] = set()
    total = 0
    for row in rows:
        if row.name in names:
            raise files.FileAccessError("アーカイブに重複パスが含まれています")
        names.add(row.name)
        total += row.size
        if total > _limit_bytes():
            raise files.FileAccessError("展開後の合計サイズが上限を超えました")
    kinds = {row.name: row.is_dir for row in rows}
    for name in names:
        parts = PurePosixPath(name).parts
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            if ancestor in kinds and not kinds[ancestor]:
                raise files.FileAccessError("アーカイブのファイル階層が競合しています")
    if compressed_size > 0 and total > _RATIO_GRACE_BYTES and total > compressed_size * _MAX_COMPRESSION_RATIO:
        raise files.FileAccessError("圧縮率が高すぎるため展開を拒否しました")
    return total


def _zip_entries(archive: zipfile.ZipFile) -> list[_ExtractEntry]:
    rows: list[_ExtractEntry] = []
    for member in archive.infolist():
        name = _safe_member_name(member.filename)
        mode = (member.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        if kind == stat.S_IFLNK:
            raise files.FileAccessError("シンボリックリンクを含むZIPは展開できません")
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise files.FileAccessError("特殊ファイルを含むZIPは展開できません")
        rows.append(_ExtractEntry(member, name, member.is_dir(), 0 if member.is_dir() else member.file_size, mode & 0o777))
    return rows


def _tar_entries(archive: tarfile.TarFile) -> list[_ExtractEntry]:
    rows: list[_ExtractEntry] = []
    for member in archive.getmembers():
        name = _safe_member_name(member.name)
        if not (member.isfile() or member.isdir()):
            raise files.FileAccessError("リンクまたは特殊ファイルを含むtarは展開できません")
        rows.append(_ExtractEntry(member, name, member.isdir(), 0 if member.isdir() else member.size, member.mode & 0o777))
    return rows


def _copy_bounded(source, destination: Path, declared_size: int) -> None:
    written = 0
    with destination.open("xb") as output:
        while chunk := source.read(min(_COPY_CHUNK, declared_size - written + 1)):
            written += len(chunk)
            if written > declared_size:
                raise files.FileAccessError("アーカイブ項目の実サイズが宣言値を超えました")
            output.write(chunk)
    if written != declared_size:
        raise files.FileAccessError("アーカイブ項目のサイズが宣言値と一致しません")


def _extract_rows(rows: list[_ExtractEntry], temporary: Path, opener) -> None:
    directory_modes: dict[Path, int] = {}
    for row in sorted(rows, key=lambda item: (item.name.count("/"), item.name)):
        target = temporary.joinpath(*PurePosixPath(row.name).parts)
        if row.is_dir:
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory_modes[target] = row.mode or 0o755
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = opener(row.source)
        if source is None:
            raise files.FileAccessError("アーカイブ項目を読み取れません")
        with source:
            _copy_bounded(source, target, row.size)
        target.chmod(row.mode or 0o644)
    # 子項目作成後にarchive由来modeを反映する。暗黙作成した親は通常directoryへする。
    directories = [path for path in temporary.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        directory.chmod(directory_modes.get(directory, 0o755))


def extract(archive_path: str, destination_path: str) -> ArchiveResult:
    source = files.resolve(archive_path)
    if not source.is_file():
        raise files.FileAccessError("アーカイブファイルを指定してください")
    destination = files.resolve(destination_path, must_exist=False)
    if destination.exists():
        raise FileExistsError(f"既に存在します: {destination.name}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"展開先フォルダが存在しません: {destination.parent}")
    fmt = _archive_format(source)
    temporary = destination.parent / f".control-deck-extract-{uuid.uuid4().hex}.tmp"
    temporary.mkdir(mode=0o700)
    try:
        if fmt == "zip":
            with zipfile.ZipFile(source, "r") as archive:
                rows = _zip_entries(archive)
                total = _validate_totals(rows, source.stat().st_size)
                _ensure_free_space(destination.parent, total)
                _extract_rows(rows, temporary, archive.open)
        else:
            with tarfile.open(source, "r:gz") as archive:
                rows = _tar_entries(archive)
                total = _validate_totals(rows, source.stat().st_size)
                _ensure_free_space(destination.parent, total)
                _extract_rows(rows, temporary, archive.extractfile)
        _publish_noreplace(temporary, destination)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, RuntimeError) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise files.FileAccessError("アーカイブが壊れているか対応形式ではありません") from exc
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return ArchiveResult(str(destination), len(rows), total, fmt)
