from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import threading
from pathlib import Path

from pydantic import ValidationError

from app.config import data_dir
from app.plugins.schema import PLUGIN_ID_PATTERN, PluginManifest

MAX_MANIFEST_BYTES = 64 * 1024
MANIFEST_NAME = "control-deck-plugin.json"
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


class PluginError(RuntimeError):
    pass


def _root() -> Path:
    raw = data_dir() / "plugins"
    if raw.is_symlink():
        raise PluginError("plugin rootをsymlinkにはできません")
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = raw.resolve()
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PluginError("plugin rootは実行user所有のdirectoryである必要があります")
    if info.st_mode & 0o022:
        raise PluginError("plugin rootをgroupまたはotherから書込み可能にはできません")
    return root


def _state_path() -> Path:
    return _root() / "state.json"


def _atomic_json(path: Path, value: object, mode: int = 0o600) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_state() -> dict[str, bool]:
    path = _state_path()
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or info.st_size > MAX_MANIFEST_BYTES):
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, bool)} \
            if isinstance(raw, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _plugin_dir(plugin_id: str) -> Path:
    if re.fullmatch(PLUGIN_ID_PATTERN, plugin_id) is None:
        raise PluginError("不正なplugin IDです")
    root = _root()
    candidate = root / plugin_id
    if candidate.is_symlink():
        raise PluginError("plugin管理先をsymlinkにはできません")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or resolved != candidate:
        raise PluginError("plugin pathが管理directory外です")
    return candidate


def _validate_directory(directory: Path) -> None:
    try:
        info = directory.lstat()
    except FileNotFoundError as exc:
        raise PluginError("pluginが登録されていません") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise PluginError("plugin管理先は実行user所有かつ安全な権限のdirectoryである必要があります")


def _load_path(path: Path, *, managed: bool = True) -> PluginManifest:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PluginError("manifestは実行user所有の通常fileである必要があります")
        forbidden_write_bits = 0o022 if managed else 0o002
        if info.st_mode & forbidden_write_bits:
            scope = "groupまたはother" if managed else "other"
            raise PluginError(f"manifestを{scope}から書込み可能にはできません")
        if info.st_size > MAX_MANIFEST_BYTES:
            raise PluginError("manifestは64KiB以下にしてください")
        return PluginManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PluginError("plugin manifestがありません") from exc
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise PluginError(f"plugin manifestが不正です: {exc}") from exc


def validate_file(source: Path) -> PluginManifest:
    return _load_path(source.expanduser().resolve(strict=True), managed=False)


def install(manifest: PluginManifest) -> dict:
    with _LOCK:
        directory = _plugin_dir(manifest.id)
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise PluginError("plugin管理先が安全なdirectoryではありません")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _validate_directory(directory)
        _atomic_json(directory / MANIFEST_NAME, manifest.model_dump(mode="json"))
        return status(manifest.id)


def install_file(source: Path) -> dict:
    return install(validate_file(source))


def manifests() -> list[PluginManifest]:
    result: list[PluginManifest] = []
    for directory in sorted(_root().iterdir(), key=lambda item: item.name):
        if directory.name == "state.json" or directory.is_symlink() or not directory.is_dir():
            continue
        try:
            _validate_directory(directory)
            manifest = _load_path(directory / MANIFEST_NAME)
            if manifest.id != directory.name:
                continue
            result.append(manifest)
        except PluginError:
            continue
    return result


def status(plugin_id: str) -> dict:
    directory = _plugin_dir(plugin_id)
    _validate_directory(directory)
    manifest = _load_path(directory / MANIFEST_NAME)
    enabled = bool(_read_state().get(plugin_id, False))
    return {**manifest.model_dump(mode="json"), "installed": True, "enabled": enabled}


def list_plugins() -> list[dict]:
    state = _read_state()
    return [
        {**item.model_dump(mode="json"), "installed": True, "enabled": bool(state.get(item.id, False))}
        for item in manifests()
    ]


def set_enabled(plugin_id: str, enabled: bool) -> dict:
    with _LOCK:
        status(plugin_id)
        state = _read_state()
        state[plugin_id] = enabled
        _atomic_json(_state_path(), state)
        return status(plugin_id)


def uninstall(plugin_id: str) -> dict:
    with _LOCK:
        current = status(plugin_id)
        directory = _plugin_dir(plugin_id)
        if directory.is_symlink() or not directory.is_dir() or not directory.is_relative_to(_root()):
            raise PluginError("削除対象がplugin管理directory外です")
        shutil.rmtree(directory)
        state = _read_state()
        state.pop(plugin_id, None)
        _atomic_json(_state_path(), state)
        return {**current, "installed": False, "enabled": False}


def enabled_navigation() -> list[dict]:
    try:
        return [
            {"id": item["id"], **item["navigation"]}
            for item in list_plugins() if item["enabled"] and "navigation" in item["capabilities"]
        ]
    except PluginError as exc:
        logger.warning("plugin navigationを読み込めません: %s", exc)
        return []
