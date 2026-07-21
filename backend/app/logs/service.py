"""アプリログ（stdout/stderr ファイル）の読み取り。"""
from __future__ import annotations

from pathlib import Path

from app.config import data_dir

STREAMS = ("stdout", "stderr")


def log_path(app_id: int, stream: str) -> Path:
    if stream not in STREAMS:
        raise ValueError(f"不正なストリーム: {stream}")
    root = (data_dir() / "logs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    app_dir = (root / str(app_id)).resolve()
    try:
        app_dir.relative_to(root)
    except ValueError as error:
        raise ValueError("ログパスが許可ルート外です") from error
    app_dir.mkdir(parents=True, exist_ok=True)
    path = (app_dir / f"{stream}.log").resolve()
    try:
        path.relative_to(app_dir)
    except ValueError as error:
        raise ValueError("ログパスがアプリログ領域外です") from error
    return path


def tail_lines(path: Path, max_lines: int, max_bytes: int = 2 * 1024 * 1024) -> list[str]:
    """ファイル末尾から最大 max_lines 行を読む（末尾 max_bytes のみ走査）。"""
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # 途中行を捨てる
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def read_new_data(path: Path, offset: int, max_bytes: int = 256 * 1024) -> tuple[str, int]:
    """offset 以降の追記分を読み、(テキスト, 新 offset) を返す。ローテーション時は末尾へ追従。"""
    if not path.exists():
        return "", 0
    size = path.stat().st_size
    if size < offset:
        offset = 0  # truncate された
    if size == offset:
        return "", offset
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read(max_bytes)
    return data.decode("utf-8", errors="replace"), offset + len(data)
