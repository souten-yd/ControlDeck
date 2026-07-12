"""パス検証。realpath 正規化 + 許可ルート配下チェック（symlink 脱出防止）。"""
from __future__ import annotations

import os
from pathlib import Path


def normalize(path: str) -> Path:
    """~ 展開 + realpath 正規化（symlink 解決）。"""
    return Path(os.path.realpath(Path(path).expanduser()))


def is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([path, root]) == str(root)
    except ValueError:
        return False


def ensure_within_roots(path: str, roots: list[str]) -> Path:
    """path を正規化し、いずれかの許可ルート配下であることを検証して返す。"""
    resolved = normalize(path)
    for root in roots:
        if is_within(resolved, normalize(str(root))):
            return resolved
    raise PermissionError(f"許可されたディレクトリの外です: {resolved}")
