"""ファイルマネージャーのサービス層。

すべてのパスは realpath 正規化 + 許可ルート検証（symlink 脱出防止）を通す。
拒否リスト（~/.ssh 等）は許可ルート内でもブロックする。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from app.config import data_dir, get_config
from app.security.paths import is_within, normalize


class FileAccessError(PermissionError):
    pass


def _deny_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".ssh",
        home / ".gnupg",
        home / ".pki",
        home / ".mozilla",
        home / ".config" / "google-chrome",
        home / ".config" / "chromium",
        data_dir() / "secret.key",
        data_dir() / "control-deck.db",
    ]


def allowed_roots() -> list[Path]:
    return [normalize(r) for r in get_config().files.allowed_roots]


def resolve(path: str, *, must_exist: bool = True) -> Path:
    """パスを検証して返す。新規作成対象は親ディレクトリで検証する。"""
    roots = allowed_roots()
    if not roots:
        raise FileAccessError("ファイルアクセスが設定されていません（files.allowed_roots が空です）")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise FileAccessError("絶対パスで指定してください")
    if must_exist:
        resolved = normalize(str(p))
        if not resolved.exists():
            raise FileNotFoundError(f"存在しません: {path}")
    else:
        # 新規パス: 親を実体解決し、ファイル名を連結（ファイル名に .. を含めない）
        if p.name in ("", ".", ".."):
            raise FileAccessError("不正なファイル名です")
        resolved = normalize(str(p.parent)) / p.name
    if not any(is_within(resolved, root) for root in roots):
        raise FileAccessError(f"許可されたディレクトリの外です: {resolved}")
    for deny in _deny_roots():
        if is_within(resolved, deny) or resolved == deny:
            raise FileAccessError(f"アクセスが拒否されています: {resolved}")
    return resolved


def list_dir(path: str) -> dict:
    d = resolve(path)
    if not d.is_dir():
        raise NotADirectoryError(f"ディレクトリではありません: {path}")
    entries = []
    for child in d.iterdir():
        try:
            st = child.lstat()
            is_dir = child.is_dir()
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": is_dir,
                    "is_symlink": child.is_symlink(),
                    "size": 0 if is_dir else st.st_size,
                    "mtime": st.st_mtime,
                    "hidden": child.name.startswith("."),
                }
            )
        except OSError:
            continue
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"path": str(d), "entries": entries}


def file_info(path: str) -> dict:
    p = resolve(path)
    st = p.stat()
    return {
        "name": p.name,
        "path": str(p),
        "is_dir": p.is_dir(),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "mode": oct(st.st_mode & 0o7777),
    }


TEXT_MAX_BYTES = 5 * 1024 * 1024


def read_text(path: str) -> str:
    p = resolve(path)
    if p.stat().st_size > TEXT_MAX_BYTES:
        raise FileAccessError("ファイルが大きすぎます（テキスト編集は 5MB まで）")
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(path: str, content: str) -> None:
    p = resolve(path, must_exist=False)
    if p.exists() and p.is_dir():
        raise IsADirectoryError(f"ディレクトリです: {path}")
    p.write_text(content, encoding="utf-8")


def make_directory(path: str) -> None:
    p = resolve(path, must_exist=False)
    p.mkdir(parents=False, exist_ok=False)


def rename(path: str, new_name: str) -> str:
    if "/" in new_name or new_name in ("", ".", ".."):
        raise FileAccessError("不正な名前です")
    src = resolve(path)
    dst = resolve(str(src.parent / new_name), must_exist=False)
    if dst.exists():
        raise FileExistsError(f"既に存在します: {new_name}")
    src.rename(dst)
    return str(dst)


def copy(src_path: str, dst_dir: str) -> str:
    src = resolve(src_path)
    dst_parent = resolve(dst_dir)
    if not dst_parent.is_dir():
        raise NotADirectoryError(f"コピー先がディレクトリではありません: {dst_dir}")
    dst = resolve(str(dst_parent / src.name), must_exist=False)
    if dst.exists():
        raise FileExistsError(f"コピー先に既に存在します: {src.name}")
    if src.is_dir():
        if is_within(dst, src):
            raise FileAccessError("フォルダを自身の中へコピーできません")
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)
    return str(dst)


def move(src_path: str, dst_dir: str) -> str:
    src = resolve(src_path)
    dst_parent = resolve(dst_dir)
    if not dst_parent.is_dir():
        raise NotADirectoryError(f"移動先がディレクトリではありません: {dst_dir}")
    dst = resolve(str(dst_parent / src.name), must_exist=False)
    if dst.exists():
        raise FileExistsError(f"移動先に既に存在します: {src.name}")
    if src.is_dir() and is_within(dst, src):
        raise FileAccessError("フォルダを自身の中へ移動できません")
    shutil.move(str(src), str(dst))
    return str(dst)


def delete(path: str) -> None:
    p = resolve(path)
    if any(p == root for root in allowed_roots()):
        raise FileAccessError("許可ルート自体は削除できません")
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()
