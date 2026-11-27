"""Nexarch の作業フォルダの選択（ルート・一覧・作成）と検証。

files API の許可ルート（files.allowed_roots）とは別に、`commander.workdir_roots`
（既定 /data1tb・/data2tb）だけを作業フォルダのルートにする。どの操作も
Path.resolve 相当（realpath）で実体を解決してからルートの中か確かめるので、
シンボリックリンクで外へ出られない。さらに files の拒否の場所（~/.ssh・data_dir の
DB と secret.key など）と、ControlDeck の data_dir の中は見せない・作らない。
"""
from __future__ import annotations

from pathlib import Path

from app.config import data_dir, get_config
from app.files import service as files
from app.security.paths import is_within, normalize


def roots() -> list[Path]:
    """実在するルートだけ（実体のパス）。"""
    out: list[Path] = []
    for raw in get_config().commander.workdir_roots:
        p = normalize(raw)
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def _blocked(path: Path) -> bool:
    """data_dir の中（丸ごと）と files の拒否の場所。"""
    data = normalize(str(data_dir()))
    if path == data or is_within(path, data):
        return True
    return any(path == d or is_within(path, d) for d in files._deny_roots())  # noqa: SLF001


def _check(path: str, *, must_exist: bool) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise PermissionError("絶対パスで指定してください")
    if must_exist:
        resolved = normalize(str(p))
        if not resolved.exists():
            raise FileNotFoundError(f"存在しません: {path}")
    else:
        if p.name in ("", ".", ".."):
            raise PermissionError("不正なフォルダ名です")
        resolved = normalize(str(p.parent)) / p.name
    if not any(is_within(resolved, r) for r in roots()):
        raise PermissionError(f"作業フォルダのルートの外です: {resolved}")
    if _blocked(resolved):
        raise PermissionError(f"アクセスが拒否されています: {resolved}")
    return resolved


def resolve_dir(path: str) -> Path:
    """既存のディレクトリを検証して実体のパスで返す。"""
    resolved = _check(path, must_exist=True)
    if not resolved.is_dir():
        raise NotADirectoryError(f"ディレクトリではありません: {path}")
    return resolved


def list_roots() -> list[str]:
    return [str(r) for r in roots()]


def list_dirs(path: str) -> dict:
    """ディレクトリだけを返す（ファイルは返さない）。拒否の場所・外を指すリンクは一覧にも出さない。"""
    d = resolve_dir(path)
    entries = []
    for child in d.iterdir():
        try:
            if not child.is_dir():
                continue
            real = normalize(str(child))
            if not any(is_within(real, r) for r in roots()) or _blocked(real):
                continue
            entries.append({
                "name": child.name, "path": str(child), "is_dir": True,
                "hidden": child.name.startswith("."),
            })
        except OSError:
            continue
    entries.sort(key=lambda e: e["name"].lower())
    return {"path": str(d), "entries": entries}


def make_directory(path: str) -> str:
    resolved = _check(path, must_exist=False)
    resolved.mkdir(parents=False, exist_ok=False)
    return str(resolved)
