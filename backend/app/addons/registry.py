from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.addons.contract import AddonHealthState, ContributionAvailability
from app.addons.schema import (
    MAX_MANIFEST_BYTES,
    PLUGIN_ID_PATTERN,
    AddonHealthReport,
    AddonManifestV2,
    ParsedManifest,
    parse_manifest,
)
from app.config import data_dir

MANIFEST_NAME = "control-deck-addon.json"
STATE_NAME = "state.json"
MAX_ACTIVITY_PER_ADDON = 100
_LOCK = threading.RLock()
_observations: dict[str, "HealthObservation"] = {}
_activity: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=MAX_ACTIVITY_PER_ADDON))
_revision = 0
_revision_event = threading.Condition(_LOCK)


class AddonRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class HealthObservation:
    report: AddonHealthReport
    checked_at: float
    consecutive_failures: int = 0
    consecutive_healthy: int = 0


def _root() -> Path:
    raw = data_dir() / "addons"
    if raw.is_symlink():
        raise AddonRegistryError("addon rootをsymlinkにはできません")
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = raw.resolve()
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AddonRegistryError("addon rootは実行user所有のdirectoryである必要があります")
    if info.st_mode & 0o022:
        raise AddonRegistryError("addon rootをgroupまたはotherから書込み可能にはできません")
    return root


def _addon_dir(addon_id: str) -> Path:
    if re.fullmatch(PLUGIN_ID_PATTERN, addon_id) is None:
        raise AddonRegistryError("不正なaddon IDです")
    root = _root()
    candidate = root / addon_id
    if candidate.is_symlink():
        raise AddonRegistryError("addon管理先をsymlinkにはできません")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or resolved != candidate:
        raise AddonRegistryError("addon pathが管理directory外です")
    return candidate


def _validate_directory(directory: Path) -> None:
    try:
        info = directory.lstat()
    except FileNotFoundError as exc:
        raise AddonRegistryError("拡張機能が登録されていません") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise AddonRegistryError("addon管理先は実行user所有かつ安全な権限のdirectoryである必要があります")


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _state_path() -> Path:
    return _root() / STATE_NAME


def _read_state() -> dict[str, dict[str, Any]]:
    path = _state_path()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > MAX_MANIFEST_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, dict)}
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _write_state(value: dict[str, dict[str, Any]]) -> None:
    _atomic_json(_state_path(), value)


def _load_path(path: Path) -> ParsedManifest:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise AddonRegistryError("manifestは実行user所有かつ安全な通常fileにしてください")
        if info.st_size > MAX_MANIFEST_BYTES:
            raise AddonRegistryError("manifestは64KiB以下にしてください")
        value = json.loads(path.read_text(encoding="utf-8"))
        parsed = parse_manifest(value)
        if not isinstance(parsed.manifest, AddonManifestV2):
            raise AddonRegistryError("v2 Add-on registryにはapi_version 2 manifestが必要です")
        return parsed
    except FileNotFoundError as exc:
        raise AddonRegistryError("addon manifestがありません") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise AddonRegistryError(f"addon manifestが不正です: {exc}") from exc


def _bump_revision() -> None:
    global _revision
    _revision += 1
    _revision_event.notify_all()


def revision() -> int:
    with _LOCK:
        return _revision


def health_observation(addon_id: str) -> HealthObservation | None:
    with _LOCK:
        status(addon_id)
        return _observations.get(addon_id)


def wait_for_revision(previous: int, timeout: float = 30.0) -> int:
    with _revision_event:
        if _revision == previous:
            _revision_event.wait(timeout=max(0.0, min(timeout, 60.0)))
        return _revision


def install(parsed: ParsedManifest) -> dict[str, Any]:
    if not isinstance(parsed.manifest, AddonManifestV2):
        raise AddonRegistryError("v2 Add-on registryにはapi_version 2 manifestが必要です")
    manifest = parsed.manifest
    with _LOCK:
        directory = _addon_dir(manifest.id)
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise AddonRegistryError("addon管理先が安全なdirectoryではありません")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _validate_directory(directory)
        _atomic_json(directory / MANIFEST_NAME, manifest.model_dump(mode="json"))
        state = _read_state()
        current = state.get(manifest.id, {})
        state[manifest.id] = {
            "enabled": bool(current.get("enabled", False)),
            "granted_capabilities": [
                item for item in current.get("granted_capabilities", []) if item in manifest.host_capabilities
            ],
            "warnings": list(parsed.warnings),
        }
        _write_state(state)
        _bump_revision()
        return status(manifest.id)


def manifests() -> list[tuple[AddonManifestV2, tuple[str, ...]]]:
    result: list[tuple[AddonManifestV2, tuple[str, ...]]] = []
    for directory in sorted(_root().iterdir(), key=lambda item: item.name):
        if directory.name == STATE_NAME or directory.is_symlink() or not directory.is_dir():
            continue
        try:
            _validate_directory(directory)
            parsed = _load_path(directory / MANIFEST_NAME)
            if parsed.manifest.id != directory.name:
                continue
            result.append((parsed.manifest, parsed.warnings))
        except AddonRegistryError:
            continue
    return result


def _state_name(enabled: bool, observation: HealthObservation | None) -> str:
    if not enabled:
        return "installed_disabled"
    if observation is None:
        return "enabling"
    return observation.report.status.value


def status(addon_id: str) -> dict[str, Any]:
    with _LOCK:
        directory = _addon_dir(addon_id)
        _validate_directory(directory)
        parsed = _load_path(directory / MANIFEST_NAME)
        manifest = parsed.manifest
        stored = _read_state().get(addon_id, {})
        enabled = bool(stored.get("enabled", False))
        observation = _observations.get(addon_id)
        warnings = stored.get("warnings", list(parsed.warnings))
        return {
            **manifest.model_dump(mode="json"),
            "installed": True,
            "enabled": enabled,
            "state": _state_name(enabled, observation),
            "granted_capabilities": [
                item for item in stored.get("granted_capabilities", []) if item in manifest.host_capabilities
            ],
            "warnings": warnings if isinstance(warnings, list) else [],
            "health": observation.report.model_dump(mode="json") if observation else None,
            "health_checked_at": observation.checked_at if observation else None,
        }


def list_addons() -> list[dict[str, Any]]:
    return [status(manifest.id) for manifest, _warnings in manifests()]


def set_enabled(addon_id: str, enabled: bool, grants: list[str] | None = None) -> dict[str, Any]:
    with _LOCK:
        current = status(addon_id)
        manifest = _load_path(_addon_dir(addon_id) / MANIFEST_NAME).manifest
        requested = set(manifest.host_capabilities)
        selected = list(manifest.host_capabilities) if grants is None else list(dict.fromkeys(grants))
        if not set(selected).issubset(requested):
            raise AddonRegistryError("manifestが要求していないhost capabilityはgrantできません")
        state = _read_state()
        state[addon_id] = {
            "enabled": enabled,
            "granted_capabilities": selected if enabled else current["granted_capabilities"],
            "warnings": current["warnings"],
        }
        _write_state(state)
        if not enabled:
            _observations.pop(addon_id, None)
        _bump_revision()
        return status(addon_id)


def uninstall(addon_id: str) -> dict[str, Any]:
    with _LOCK:
        current = status(addon_id)
        directory = _addon_dir(addon_id)
        if directory.is_symlink() or not directory.is_dir() or not directory.is_relative_to(_root()):
            raise AddonRegistryError("削除対象がaddon管理directory外です")
        shutil.rmtree(directory)
        state = _read_state()
        state.pop(addon_id, None)
        _write_state(state)
        _observations.pop(addon_id, None)
        _activity.pop(addon_id, None)
        _bump_revision()
        return {**current, "installed": False, "enabled": False, "state": "not_installed"}


def update_health(addon_id: str, report: AddonHealthReport, *, failed: bool = False) -> dict[str, Any]:
    with _LOCK:
        current = status(addon_id)
        if not current["enabled"]:
            raise AddonRegistryError("無効な拡張機能のhealthは更新できません")
        previous = _observations.get(addon_id)
        failures = (previous.consecutive_failures if previous else 0) + 1 if failed else 0
        healthy = (previous.consecutive_healthy if previous else 0) + 1 if report.status == AddonHealthState.HEALTHY else 0
        _observations[addon_id] = HealthObservation(report, time.time(), failures, healthy)
        if previous is None or previous.report != report:
            _bump_revision()
        return status(addon_id)


def _contribution_availability(report: AddonHealthReport | None, kind: str, item_id: str) -> str:
    if report is None:
        return ContributionAvailability.UNAVAILABLE.value
    value = report.contributions.get(f"{kind}:{item_id}")
    if value is None:
        return ContributionAvailability.AVAILABLE.value
    return value.value if isinstance(value, ContributionAvailability) else value.state.value


def effective_for_permissions(permissions: set[str]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    addons: list[dict[str, Any]] = []
    for manifest, _warnings in manifests():
        item = status(manifest.id)
        if not item["enabled"]:
            continue
        granted = set(item["granted_capabilities"])
        if not set(manifest.host_capabilities).issubset(granted):
            continue
        report = _observations.get(manifest.id).report if manifest.id in _observations else None
        addons.append({
            "id": manifest.id,
            "name": manifest.name,
            "state": item["state"],
            "health": report.model_dump(mode="json") if report else None,
        })
        for kind in type(manifest.contributions).model_fields:
            for contribution in getattr(manifest.contributions, kind):
                if contribution.permission not in permissions:
                    continue
                availability = _contribution_availability(report, kind.rstrip("s"), contribution.id)
                navigation = kind == "navigation"
                overall_unavailable = report is None or report.status in {
                    AddonHealthState.UNAVAILABLE, AddonHealthState.SETUP_REQUIRED,
                }
                if not navigation and (availability == ContributionAvailability.UNAVAILABLE.value or overall_unavailable):
                    continue
                groups.setdefault(kind, []).append({
                    "addon_id": manifest.id,
                    **contribution.model_dump(mode="json"),
                    "availability": availability,
                })
    for values in groups.values():
        values.sort(key=lambda value: (value.get("order", 100), value["addon_id"], value["id"]))
    payload = {"revision": revision(), "addons": addons, "contributions": groups}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload["etag"] = f'"{hashlib.sha256(canonical).hexdigest()}"'
    return payload


def record_activity(addon_id: str, method: str, result: str, metadata: dict[str, Any] | None = None) -> None:
    with _LOCK:
        status(addon_id)
        _activity[addon_id].appendleft({
            "at": time.time(),
            "method": method[:128],
            "result": result[:32],
            "metadata": metadata or {},
        })


def activity(addon_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        status(addon_id)
        return list(_activity.get(addon_id, ()))


def reset_runtime_state_for_tests() -> None:
    global _revision
    with _LOCK:
        _observations.clear()
        _activity.clear()
        _revision = 0
