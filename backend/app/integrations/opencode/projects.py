"""作業場所（CodeDEV 直下のフォルダ）の自動作成と表示名。

Nexarch・チーム・AI アシスタントの OpenCode が、毎回フォルダ名を考えずに作業場所を持てるようにする。
フォルダ名は `<kind>-YYYYMMDD-HHMMSS` で変えない（OpenCode の会話の履歴がフォルダのパスに結び付くため）。
利用者に見える名前は `.controldeck/project.json` の `name` だけで、後から書き換えられる
（Project Lab の一覧・詳細がこの name を出す）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

KINDS = ("nexarch", "team", "chat")
MAX_NAME = 80
MAX_MANIFEST_BYTES = 256 * 1024


class ProjectError(ValueError):
    pass


def _root() -> Path:
    # 置き場の決定は provider.codedev_root（config）に一本化する（Project Lab・OpenCode と同じ場所）
    from app.integrations.opencode import provider

    return provider.codedev_root().resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def codedev_root() -> Path:
    return _root()


def default_name(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"新しい作業場所 {now.month}/{now.day} {now:%H:%M}"


def clean_name(name: str | None, fallback: str | None = None) -> str:
    """1〜80 字・制御文字なし。空なら fallback（None なら既定の名前）。"""
    cleaned = (name or "").strip()
    if not cleaned:
        return default_name() if fallback is None else fallback
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in cleaned):
        raise ProjectError("表示名に制御文字（tab・改行など）は使えません")
    if len(cleaned) > MAX_NAME:
        raise ProjectError(f"表示名は {MAX_NAME} 字以内にしてください")
    return cleaned


def resolve_in_codedev(folder_or_path: str) -> Path:
    """CodeDEV 直下のフォルダ名（またはその絶対パス）を realpath で解決し、中にあるか確かめる。"""
    root = _root()
    raw = (folder_or_path or "").strip()
    if not raw or "\x00" in raw:
        raise ProjectError("作業場所が不正です")
    candidate = Path(raw)
    if not candidate.is_absolute():
        if raw.startswith(".") or any(c in raw for c in "/\\"):
            raise ProjectError("作業場所が不正です")
        candidate = root / raw
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ProjectError("作業場所が見つかりません") from exc
    if not resolved.is_dir() or resolved == root or not _inside(resolved, root):
        raise ProjectError("CodeDEV の外の作業場所は扱えません")
    return resolved


def _manifest_path(project: Path) -> Path:
    return project / ".controldeck" / "project.json"


def _load_raw(project: Path) -> dict:
    path = _manifest_path(project)
    try:
        resolved = path.resolve(strict=True)
        if not _inside(resolved, project) or resolved.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProjectError("manifest が project 外を指すか、大きすぎます")
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError("manifest を読めません") from exc
    if not isinstance(raw, dict):
        raise ProjectError("manifest の形が不正です")
    return raw


def _write_raw(project: Path, raw: dict) -> None:
    path = _manifest_path(project)
    if path.parent.is_symlink():
        raise ProjectError("manifest の置き場が不正です")
    path.parent.mkdir(exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def create_auto_project(kind: str, display_name: str = "") -> dict:
    """CodeDEV 直下に `<kind>-YYYYMMDD-HHMMSS` を作る（git init、manifest に表示名と created_by）。"""
    if kind not in KINDS:
        raise ProjectError("作業場所の種類が不正です")
    name = clean_name(display_name)
    root = _root()
    base = f"{kind}-{datetime.now():%Y%m%d-%H%M%S}"
    folder = base
    suffix = 1
    while True:
        try:
            (root / folder).mkdir()
            break
        except FileExistsError:
            suffix += 1
            folder = f"{base}-{suffix}"
    path = (root / folder).resolve()
    if shutil.which("git"):
        try:
            subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    _write_raw(path, {"schemaVersion": 1, "name": name, "description": "", "profiles": [], "created_by": kind})
    return {"folder": folder, "path": str(path), "name": name}


def safe_stub(name: str | None, fallback: str = "team") -> str:
    """フォルダ名の前半。英数・_・- だけ（それ以外は - にまとめる）・30 字まで。残らなければ fallback。"""
    import re

    stub = re.sub(r"[^A-Za-z0-9_]+", "-", (name or "").strip()).strip("-_")[:30].strip("-_")
    return stub or fallback


def create_project_in(parent: Path, name: str, kind: str = "team") -> dict:
    """parent（検証済みの親フォルダ）の下に `<名前を安全にした名前>-YYYYMMDD-HHMMSS` を作る（git init・manifest）。

    create_auto_project と同じ作り方（フォルダ名は変えない・表示名は manifest の name・created_by）。
    CodeDEV の外にも作れる（チームの保存先 team_root）。親の検証は呼び出し側（commander.workdir.resolve_dir）。
    """
    if kind not in KINDS:
        raise ProjectError("作業場所の種類が不正です")
    display = clean_name(name, fallback=default_name())
    base = f"{safe_stub(name, kind)}-{datetime.now():%Y%m%d-%H%M%S}"
    folder = base
    suffix = 1
    while True:
        try:
            (parent / folder).mkdir()
            break
        except FileExistsError:
            suffix += 1
            folder = f"{base}-{suffix}"
    path = (parent / folder).resolve()
    if shutil.which("git"):
        try:
            subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    _write_raw(path, {"schemaVersion": 1, "name": display, "description": "", "profiles": [], "created_by": kind})
    return {"folder": folder, "path": str(path), "name": display}


def set_display_name(folder: str, name: str) -> dict:
    """manifest の name だけ書き換える（他の項目・フォルダ名は変えない）。"""
    project = resolve_in_codedev(folder)
    cleaned = clean_name(name, fallback="")
    if not cleaned:
        raise ProjectError("表示名を入力してください")
    raw = _load_raw(project)
    raw.setdefault("schemaVersion", 1)
    raw.setdefault("description", "")
    raw.setdefault("profiles", [])
    raw["name"] = cleaned
    _write_raw(project, raw)
    return {"folder": project.name, "path": str(project), "name": cleaned}


def display_name(path: str | Path) -> str:
    """manifest の name、無ければフォルダ名。"""
    p = Path(path)
    try:
        name = _load_raw(p.resolve()).get("name")
    except (ProjectError, OSError):
        name = None
    return name.strip() if isinstance(name, str) and name.strip() else p.name


def display_name_if_codedev(path: str | Path) -> str | None:
    """CodeDEV 直下のフォルダなら表示名、それ以外は None。"""
    try:
        resolved = Path(path).resolve()
        if resolved.parent != _root():
            return None
    except OSError:
        return None
    return display_name(resolved)
