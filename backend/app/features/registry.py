from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from app.config import data_dir

FeatureAction = Literal["install", "enable", "disable", "uninstall"]
KNOWN_FEATURES = {"opencode"}


class FeatureError(RuntimeError):
    pass


def _features_root() -> Path:
    root = (data_dir() / "features").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path() -> Path:
    return _features_root() / "state.json"


def _read_state() -> dict:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    path = _state_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _feature_root(feature_id: str) -> Path:
    if feature_id not in KNOWN_FEATURES:
        raise FeatureError(f"未知のfeatureです: {feature_id}")
    root = (_features_root() / feature_id).resolve()
    if not root.is_relative_to(_features_root()):
        raise FeatureError("feature pathがdata directory外です")
    return root


def _managed_executable(feature_id: str) -> Path:
    return _feature_root(feature_id) / "node_modules" / ".bin" / feature_id


def executable(feature_id: str) -> Path | None:
    managed = _managed_executable(feature_id)
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed.resolve()
    saved = str(_read_state().get(feature_id, {}).get("external_executable") or "")
    if saved:
        candidate = Path(saved).expanduser().resolve()
        if candidate.name == feature_id and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    external = shutil.which(feature_id)
    return Path(external).resolve() if external else None


def status(feature_id: str) -> dict:
    if feature_id not in KNOWN_FEATURES:
        raise FeatureError(f"未知のfeatureです: {feature_id}")
    state = _read_state().get(feature_id, {})
    binary = executable(feature_id)
    managed = _managed_executable(feature_id).is_file()
    version = ""
    healthy = False
    error = ""
    if binary is not None:
        try:
            result = subprocess.run(
                [str(binary), "--version"], capture_output=True, text=True, timeout=10, check=False,
            )
            healthy = result.returncode == 0
            lines = (result.stdout or result.stderr).strip().splitlines()
            version = lines[0][:80] if healthy and lines else ""
            if not healthy:
                error = "version確認に失敗しました"
        except (OSError, subprocess.TimeoutExpired):
            error = "実行ファイルを起動できません"
    installed = binary is not None
    return {
        "id": feature_id,
        "name": "OpenCode" if feature_id == "opencode" else feature_id,
        "available": shutil.which("npm") is not None or installed,
        "installed": installed,
        "managed": managed,
        "enabled": bool(state.get("enabled")) and installed and healthy,
        "requested_enabled": bool(state.get("enabled")),
        "version": version,
        "health": "healthy" if healthy else ("error" if installed else "not-installed"),
        "error": error,
        "executable": str(binary) if binary else "",
    }


def list_features() -> list[dict]:
    return [status(feature_id) for feature_id in sorted(KNOWN_FEATURES)]


def is_enabled(feature_id: str) -> bool:
    try:
        return bool(status(feature_id)["enabled"])
    except FeatureError:
        return False


def install(feature_id: str) -> dict:
    root = _feature_root(feature_id)
    npm = shutil.which("npm")
    if npm is None:
        raise FeatureError("npmが必要です")
    root.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npm, "install", "--prefix", str(root), "--no-fund", "--no-audit", "opencode-ai"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    if result.returncode != 0 or not _managed_executable(feature_id).is_file():
        raise FeatureError("OpenCodeの管理導入に失敗しました")
    return status(feature_id)


def enable(feature_id: str) -> dict:
    current = status(feature_id)
    if not current["installed"] or current["health"] != "healthy":
        raise FeatureError("正常なOpenCodeを先に導入してください")
    state = _read_state()
    remembered = "" if current["managed"] else current["executable"]
    state[feature_id] = {
        **state.get(feature_id, {}), "enabled": True,
        "external_executable": remembered,
    }
    _write_state(state)
    return status(feature_id)


def disable(feature_id: str) -> dict:
    if feature_id not in KNOWN_FEATURES:
        raise FeatureError(f"未知のfeatureです: {feature_id}")
    state = _read_state()
    state[feature_id] = {**state.get(feature_id, {}), "enabled": False}
    _write_state(state)
    return status(feature_id)


def uninstall(feature_id: str) -> dict:
    disable(feature_id)
    root = _feature_root(feature_id)
    if root.exists():
        # 管理prefixだけを削除。PATH上の外部OpenCodeと~/.config/~/.local/shareには触れない。
        if not root.is_relative_to(_features_root()):
            raise FeatureError("削除対象がfeature directory外です")
        shutil.rmtree(root)
    return status(feature_id)


def apply(action: FeatureAction, feature_id: str) -> dict:
    operations = {"install": install, "enable": enable, "disable": disable, "uninstall": uninstall}
    return operations[action](feature_id)
