"""Control Deck backup tar.gzの限定・安全展開。"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath

_ROOT = "control-deck-backup"
_MAX_ENTRIES = 100_000
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024 * 1024
_MAX_RATIO = 200
_RATIO_FLOOR = 16 * 1024 * 1024


def _parts(name: str) -> tuple[str, ...]:
    if not name or "\x00" in name or "\\" in name or len(name.encode("utf-8")) > 4096:
        raise ValueError("backup archive pathが不正です")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("backup archive pathがroot外を指しています")
    if not path.parts or path.parts[0] != _ROOT:
        raise ValueError("backup archive rootが不正です")
    return path.parts


def extract_backup(archive: Path, destination: Path) -> None:
    archive_path = archive.expanduser().resolve(strict=True)
    destination_path = destination.expanduser().resolve(strict=True)
    if not destination_path.is_dir() or destination_path.is_symlink():
        raise ValueError("backup展開先は既存の通常directoryにしてください")
    archive_size = archive_path.stat().st_size
    seen: set[tuple[str, ...]] = set()
    total = 0
    with tarfile.open(archive_path, mode="r:gz") as source:
        members = source.getmembers()
        if not members or len(members) > _MAX_ENTRIES:
            raise ValueError("backup archiveの項目数が不正です")
        for member in members:
            parts = _parts(member.name.rstrip("/"))
            if parts in seen:
                raise ValueError("backup archiveに重複pathがあります")
            seen.add(parts)
            if not (member.isdir() or member.isreg()):
                raise ValueError("backup archiveのsymlink／hardlink／特殊fileは展開できません")
            if member.size < 0:
                raise ValueError("backup archiveのfile sizeが不正です")
            total += member.size
            if total > _MAX_TOTAL_BYTES:
                raise ValueError("backup archiveの展開sizeが上限を超えています")
        if total > _RATIO_FLOOR and total > max(archive_size, 1) * _MAX_RATIO:
            raise ValueError("backup archiveの圧縮率が安全上限を超えています")

        for member in members:
            parts = _parts(member.name.rstrip("/"))
            target = destination_path.joinpath(*parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                if target.is_symlink() or not target.is_dir():
                    raise ValueError("backup archiveのdirectory pathが競合しています")
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            extracted = source.extractfile(member)
            if extracted is None:
                raise ValueError("backup archiveの通常fileを読み込めません")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(target, flags, 0o600)
            try:
                with os.fdopen(fd, "wb", closefd=False) as output:
                    shutil.copyfileobj(extracted, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if os.fstat(fd).st_size != member.size or not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("backup archiveの展開sizeが宣言値と一致しません")
            finally:
                os.close(fd)


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 2:
        print("usage: backup_archive <archive> <destination>", file=sys.stderr)
        return 2
    try:
        extract_backup(Path(args[0]), Path(args[1]))
    except Exception as exc:
        print(f"backup archive extraction failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
