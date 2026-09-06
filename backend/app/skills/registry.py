"""導入したスキルの出し入れ。

置き場は ControlDeck のデータ配下だけにする。利用者の `~/.claude/skills` や
`~/.config/opencode` へは書かない。そこは利用者自身のもので、こちらが足したり
消したりしてよい場所ではないし、消し忘れると ControlDeck を使っていない
OpenCode の挙動まで変えてしまう。

読ませ方は OpenCode の `skills.paths` を使う。ControlDeck が起動のたびに作る
runtime config に、有効なスキルの置き場を並べる。だから「無効化」は設定から
外すだけで済み、ファイルはそのまま残る（もう一度有効にしても取り直さない）。

    <data>/skills/state.json                  何を入れたか
    <data>/skills/versions/<id>/<version>/    実体
"""

from __future__ import annotations

import json
import fcntl
import functools
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from app.config import data_dir
from app.skills import catalog

_LOCK = threading.RLock()
logger = logging.getLogger(__name__)
P = ParamSpec("P")
T = TypeVar("T")


class SkillError(RuntimeError):
    pass


def skills_root() -> Path:
    raw = data_dir().resolve() / "skills"
    if raw.is_symlink() or raw.resolve() != raw:
        raise SkillError("skill管理先をsymlinkにはできません")
    return raw


def _contained(path: Path) -> Path:
    root = skills_root()
    if path.is_symlink() or path.resolve() != path or not path.is_relative_to(root) or path == root:
        raise SkillError("skillの管理先が不正です")
    return path


def _mutating(function: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with _LOCK:
            root = skills_root()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                return function(*args, **kwargs)
            finally:
                os.close(descriptor)
    return wrapped


def _versions_root() -> Path:
    return _contained(skills_root() / "versions")


def _state_path() -> Path:
    return _contained(skills_root() / "state.json")


def _read_state() -> dict:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillError("skillの導入記録を読み込めません") from exc


def _write_state(state: dict) -> None:
    root = skills_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _state_path()
    temp = _contained(path.with_suffix(".tmp"))
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _entry(skill_id: str) -> catalog.SkillEntry:
    found = catalog.BY_ID.get(skill_id)
    if found is None:
        raise SkillError(f"未知のskillです: {skill_id}")
    return found


def _install_dir(skill_id: str, version: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._+-]{0,95}", version):
        raise SkillError("skillの版数が不正です")
    return _contained(_versions_root() / skill_id / version)


def has_skill_document(root: Path) -> bool:
    """その配下に SKILL.md が 1 つでもあるか。

    取り込みが空振りしても気付けるようにする。OpenCode は黙って無視するので、
    導入したのに何も起きない、という形の失敗になりやすい。
    """
    return any(root.rglob("SKILL.md"))


def _copy_bundled(entry: catalog.SkillEntry, target: Path) -> None:
    source = catalog.bundled_source(entry.id)
    if not source.is_dir():
        raise SkillError(f"同梱skillが見つかりません: {entry.id}")
    shutil.copytree(source, target, dirs_exist_ok=True)


def _clone_git(entry: catalog.SkillEntry, target: Path) -> None:
    """固定した commit だけを取り出す。

    `--depth 1` で ref を直接取る。branch を追いかけると、同じ導入操作が日に
    よって別物を持ってくることになる。取り出すのは宣言した部分だけで、repo
    まるごとは置かない（設定や plugin まで読ませないため）。
    """
    with tempfile.TemporaryDirectory(prefix=".checkout-", dir=target.parent) as work:
        checkout = Path(work) / "repo"
        try:
            subprocess.run(
                ["git", "init", "--quiet", str(checkout)],
                check=True, capture_output=True, timeout=60,
            )
            subprocess.run(
                ["git", "-C", str(checkout), "remote", "add", "origin", entry.repository],
                check=True, capture_output=True, timeout=60,
            )
            subprocess.run(
                ["git", "-C", str(checkout), "fetch", "--quiet", "--depth", "1", "origin", entry.ref],
                check=True, capture_output=True, timeout=600,
            )
            subprocess.run(
                ["git", "-C", str(checkout), "checkout", "--quiet", "FETCH_HEAD"],
                check=True, capture_output=True, timeout=120,
            )
            head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                  check=True, capture_output=True, timeout=30).stdout.decode().strip()
            if head != entry.ref:
                raise SkillError("固定したskill commitと一致しません")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace")[:200]
            raise SkillError(f"skillの取得に失敗しました: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillError("skillの取得が時間内に終わりませんでした") from exc

        target.mkdir(parents=True, exist_ok=True)
        for relative in entry.subpaths:
            source = (checkout / relative).resolve()
            if not source.is_dir() or checkout.resolve() not in source.parents and source != checkout.resolve():
                raise SkillError(f"skillに想定した中身がありません: {relative}")
            if any(path.is_symlink() for path in source.rglob("*")):
                raise SkillError("外部skillにsymlinkは許可していません")
            shutil.copytree(source, target / Path(relative).name, dirs_exist_ok=True)
        license_path = checkout / "LICENSE"
        if entry.adapter:
            if not license_path.is_file() or license_path.is_symlink():
                raise SkillError("外部skillのlicenseがありません")
            shutil.copyfile(license_path, target / "UPSTREAM-LICENSE")


def _prepare_adapter(entry: catalog.SkillEntry, target: Path) -> None:
    if not entry.adapter:
        return
    source = target / "skills"
    if not (source / "blender-director" / "SKILL.md").is_file():
        raise SkillError("上流directorが見つかりません")
    upstream = target / "upstream"
    upstream.mkdir()
    source.replace(upstream / "skills")
    adapter = Path(__file__).parent / "adapters" / entry.adapter
    shutil.copytree(adapter, target / "runtime")
    if not has_skill_document(target / "runtime"):
        raise SkillError("実行用adapterがありません")
    (target / "adapter.json").write_text(json.dumps({"adapter": entry.adapter, "version": entry.version,
        "upstream_ref": entry.ref}), encoding="utf-8")


def _cleanup(path: Path) -> None:
    if path.exists():
        try:
            shutil.rmtree(_contained(path))
        except OSError:
            logger.warning("skillの一時directoryを回収できませんでした", exc_info=True)


@_mutating
def install(skill_id: str) -> dict:
    """導入する。すでに入っていれば、その版を入れ直す（修復を兼ねる）。"""
    entry = _entry(skill_id)
    target = _install_dir(entry.id, entry.version)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _contained(target.with_name(target.name + ".previous"))
    if backup.exists():
        raise SkillError("前回の復旧用directoryが残っています。上書きせず診断してください")
    state = _read_state()
    installed = dict(state.get("installed") or {})
    previous = installed.get(entry.id) or {}
    staging = Path(tempfile.mkdtemp(prefix=f".{entry.version}.staging-", dir=target.parent))
    try:
        if entry.source == "bundled":
            _copy_bundled(entry, staging)
        else:
            _clone_git(entry, staging)
        if not has_skill_document(staging):
            raise SkillError("取得した中に SKILL.md がありません")
        _prepare_adapter(entry, staging)
        # 出来上がってから差し替える。途中で失敗したものを OpenCode に読ませない。
        if target.exists():
            target.replace(backup)
        try:
            staging.replace(target)
            installed[entry.id] = {
                "version": entry.version,
                "enabled": bool(previous.get("enabled", True)),
                "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": entry.source, "ref": entry.ref,
            }
            _write_state({**state, "installed": installed})
        except Exception:
            if target.exists():
                target.replace(staging)
            if backup.exists():
                backup.replace(target)
            raise
        _cleanup(backup)
    finally:
        _cleanup(staging)
    return status(entry.id)


@_mutating
def set_enabled(skill_id: str, enabled: bool) -> dict:
    """有効・無効を切り替える。ファイルは消さない。"""
    entry = _entry(skill_id)
    state = _read_state()
    installed = dict(state.get("installed") or {})
    if entry.id not in installed:
        raise SkillError("導入されていないskillです")
    record = dict(installed[entry.id])
    record["enabled"] = bool(enabled)
    installed[entry.id] = record
    state["installed"] = installed
    _write_state(state)
    return status(entry.id)


@_mutating
def remove(skill_id: str) -> dict:
    """消す。実体も記録も残さない。"""
    entry = _entry(skill_id)
    target = _contained(_versions_root() / entry.id)
    state = _read_state()
    installed = dict(state.get("installed") or {})
    installed.pop(entry.id, None)
    state["installed"] = installed
    _write_state(state)
    if target.exists():
        shutil.rmtree(target)
    return status(entry.id)


def status(skill_id: str) -> dict:
    entry = _entry(skill_id)
    record = (_read_state().get("installed") or {}).get(entry.id) or {}
    installed_version = str(record.get("version") or "")
    path = _install_dir(entry.id, installed_version) if installed_version else None
    present = bool(path and path.is_dir())
    readiness = _readiness(entry, path if present else None)
    return {
        "id": entry.id,
        "name": entry.name,
        "summary": entry.summary,
        "source": entry.source,
        "requires": entry.requires,
        "license": entry.license,
        "repository": entry.repository,
        "available_version": entry.version,
        "installed_version": installed_version if present else "",
        "installed": present,
        "enabled": present and bool(record.get("enabled")),
        "update_available": present and installed_version != entry.version,
        "installed_at": str(record.get("installed_at") or ""),
        "execution": readiness,
        "effective": present and bool(record.get("enabled")) and readiness["state"] == "ready",
    }


def _readiness(entry: catalog.SkillEntry, path: Path | None) -> dict[str, object]:
    from app.skills import execution
    if path and entry.adapter and not (path / "runtime" / "blender-director" / "SKILL.md").is_file():
        return {"state": "update_required", "message": "旧版の実行手順はBlenderMCP向けです。対応版へ更新してください。"}
    return execution.check(entry)


def list_skills() -> list[dict]:
    return [status(entry.id) for entry in catalog.ALL]


def enabled_paths() -> list[str]:
    """OpenCode の `skills.paths` へ並べる置き場。

    実体があって、かつ有効なものだけ。記録だけ残って中身が無いものを渡すと、
    OpenCode は黙って無視するので、こちらで落としておく。
    """
    values: list[str] = []
    for entry in catalog.ALL:
        record = (_read_state().get("installed") or {}).get(entry.id) or {}
        if not record.get("enabled"):
            continue
        version = str(record.get("version") or "")
        if not version:
            continue
        path = _install_dir(entry.id, version)
        if path.is_dir() and _readiness(entry, path)["state"] == "ready":
            values.append(str(_contained(path / "runtime") if entry.adapter else path))
    return values
