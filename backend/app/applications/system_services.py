"""root所有allowlistに限定したsystem scope systemd service境界。"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.applications import systemd as sd
from app.config import get_config

_TRUSTED_CATALOG_PATH = Path("/etc/control-deck/system-services.json")
CATALOG_PATH = _TRUSTED_CATALOG_PATH
HELPER_PATH = Path("/usr/local/libexec/control-deck-hw-helper")
SUDO_PATH = Path("/usr/bin/sudo")
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
_ACTIONS = {"start", "stop", "restart"}
_MAX_CATALOG_BYTES = 64 * 1024


@dataclass(frozen=True)
class InstalledSystemService:
    id: str
    label: str
    unit: str
    actions: tuple[str, ...]


class SystemServiceError(RuntimeError):
    pass


def _read_catalog_bytes(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise SystemServiceError("system service catalogが導入されていません") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CATALOG_BYTES:
            raise SystemServiceError("system service catalogが不正です")
        if path == _TRUSTED_CATALOG_PATH and (info.st_uid != 0 or info.st_mode & 0o022):
            raise SystemServiceError("system service catalogの所有権または権限が不正です")
        data = os.read(descriptor, _MAX_CATALOG_BYTES + 1)
        if len(data) > _MAX_CATALOG_BYTES:
            raise SystemServiceError("system service catalogが大きすぎます")
        return data
    finally:
        os.close(descriptor)


def installed_catalog() -> dict[str, InstalledSystemService]:
    try:
        raw = json.loads(_read_catalog_bytes(CATALOG_PATH))
    except json.JSONDecodeError as exc:
        raise SystemServiceError("system service catalogを解析できません") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("services"), dict):
        raise SystemServiceError("system service catalogの形式が不正です")
    services = raw["services"]
    if len(services) > 64:
        raise SystemServiceError("system service catalogの件数が上限を超えています")
    result: dict[str, InstalledSystemService] = {}
    for service_id, value in services.items():
        if not isinstance(service_id, str) or not _ID_RE.fullmatch(service_id) or not isinstance(value, dict):
            raise SystemServiceError("system service catalogのIDが不正です")
        label, unit, actions = value.get("label"), value.get("unit"), value.get("actions")
        if (
            not isinstance(label, str) or not 1 <= len(label) <= 80
            or not isinstance(unit, str) or not _UNIT_RE.fullmatch(unit)
            or not isinstance(actions, list) or not 1 <= len(actions) <= 3
            or any(not isinstance(action, str) or action not in _ACTIONS for action in actions)
            or len(set(actions)) != len(actions)
        ):
            raise SystemServiceError("system service catalogの定義が不正です")
        result[service_id] = InstalledSystemService(service_id, label, unit, tuple(actions))
    return result


def catalog_for_api() -> list[dict[str, object]]:
    try:
        catalog = installed_catalog()
    except SystemServiceError:
        return []
    return [
        {"id": item.id, "label": item.label, "unit": item.unit, "actions": list(item.actions)}
        for item in sorted(catalog.values(), key=lambda item: (item.label.casefold(), item.id))
    ]


def require_service(service_id: str) -> InstalledSystemService:
    try:
        service = installed_catalog().get(service_id)
    except SystemServiceError as exc:
        raise SystemServiceError("system service catalogを利用できません") from exc
    if service is None:
        raise SystemServiceError("登録済みのsystem serviceを選択してください")
    return service


def query_status(service_id: str) -> dict:
    service = require_service(service_id)
    return sd.query_status(service.unit, user_scope=False)


def control(service_id: str, action: str) -> tuple[bool, str]:
    service = require_service(service_id)
    if action not in service.actions:
        return False, "このsystem serviceでは操作が許可されていません"
    try:
        if HELPER_PATH.is_symlink():
            return False, "特権helperの所有権または権限が不正です"
        helper = HELPER_PATH.resolve(strict=True)
        info = helper.stat()
    except OSError:
        return False, "特権helperが導入されていません（./deck.sh service を実行してください）"
    if helper != HELPER_PATH or not helper.is_file() or info.st_uid != 0 or info.st_mode & 0o022:
        return False, "特権helperの所有権または権限が不正です"
    if not SUDO_PATH.is_file() or SUDO_PATH.is_symlink():
        return False, "固定sudoを利用できません"
    try:
        result = subprocess.run(
            [str(SUDO_PATH), "-n", str(helper), "system-service", action, service.id],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "特権helperを実行できません"
    if result.returncode == 0:
        return True, ""
    message = "system service操作に失敗しました"
    try:
        payload = json.loads(result.stderr)
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            message = payload["error"][:500]
    except json.JSONDecodeError:
        pass
    return False, message


def render_config_catalog() -> dict[str, object]:
    definitions = get_config().applications.system_services
    return {
        "version": 1,
        "services": {
            service_id: {
                "label": definition.label,
                "unit": definition.unit,
                "actions": definition.actions,
            }
            for service_id, definition in sorted(definitions.items())
        },
    }


def main() -> None:
    if sys.argv[1:] != ["--render-config"]:
        raise SystemExit("usage: python -m app.applications.system_services --render-config")
    print(json.dumps(render_config_catalog(), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
