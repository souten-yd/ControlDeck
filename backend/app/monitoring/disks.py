"""非特権ディスクtelemetry。取得不能な項目は例外ではなくN/Aとして返す。"""
from __future__ import annotations

import json
import stat
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

DEV_ROOT = Path("/dev").resolve()
SYS_BLOCK_ROOT = Path("/sys/class/block").resolve()
SYS_DEVICES_ROOT = Path("/sys/devices").resolve()
SMARTCTL_CANDIDATES = (Path("/usr/sbin/smartctl"), Path("/usr/bin/smartctl"))


@dataclass
class DiskTelemetry:
    physical_device: str | None = None
    read_bps: float | None = None
    write_bps: float | None = None
    temperature_c: float | None = None
    temperature_sensor: str | None = None
    smart_status: str = "unavailable"
    smart_available: bool = False


class DiskTelemetryService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_time = time.monotonic()
        self._last_io = psutil.disk_io_counters(perdisk=True) or {}
        self._smart_cache: dict[str, tuple[float, bool, str, float | None]] = {}

    def snapshot(self, devices: list[str]) -> dict[str, dict[str, Any]]:
        resolved = {device: _physical_block_device(device) for device in devices}
        now = time.monotonic()
        current = psutil.disk_io_counters(perdisk=True) or {}
        with self._lock:
            elapsed = max(0.001, now - self._last_time)
            previous = self._last_io
            self._last_time = now
            self._last_io = current

        result: dict[str, dict[str, Any]] = {}
        for original, block in resolved.items():
            telemetry = DiskTelemetry()
            if block is None:
                result[original] = asdict(telemetry)
                continue
            telemetry.physical_device = str(block)
            name = block.name
            before, after = previous.get(name), current.get(name)
            if before is not None and after is not None:
                telemetry.read_bps = max(0.0, (after.read_bytes - before.read_bytes) / elapsed)
                telemetry.write_bps = max(0.0, (after.write_bytes - before.write_bytes) / elapsed)
            telemetry.temperature_c, telemetry.temperature_sensor = _sysfs_temperature(name)
            available, status_value, smart_temp = self._smart_health(block, now)
            telemetry.smart_available = available
            telemetry.smart_status = status_value
            if telemetry.temperature_c is None and smart_temp is not None:
                telemetry.temperature_c = smart_temp
                telemetry.temperature_sensor = "SMART"
            result[original] = asdict(telemetry)
        return result

    def _smart_health(self, block: Path, now: float) -> tuple[bool, str, float | None]:
        key = str(block)
        cached = self._smart_cache.get(key)
        if cached is not None and now - cached[0] < 60:
            return cached[1], cached[2], cached[3]
        value = _read_smartctl(block)
        self._smart_cache[key] = (now, *value)
        return value


def _physical_block_device(device: str) -> Path | None:
    try:
        block = Path(device).resolve(strict=True)
        block.relative_to(DEV_ROOT)
        if not stat.S_ISBLK(block.stat().st_mode):
            return None
        sys_entry = SYS_BLOCK_ROOT / block.name
        sys_entry.resolve(strict=True)
        if (sys_entry / "partition").exists():
            physical_name = sys_entry.resolve().parent.name
            block = (DEV_ROOT / physical_name).resolve(strict=True)
            block.relative_to(DEV_ROOT)
            if not stat.S_ISBLK(block.stat().st_mode):
                return None
        return block
    except (OSError, ValueError):
        return None


def _sysfs_temperature(block_name: str) -> tuple[float | None, str | None]:
    sys_entry = SYS_BLOCK_ROOT / block_name
    try:
        sys_entry.resolve(strict=True)
    except OSError:
        return None, None
    candidates = sorted((sys_entry / "device").glob("hwmon*/temp*_input"))
    readings: list[tuple[int, float, str]] = []
    for path in candidates:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(SYS_DEVICES_ROOT)
            value = float(resolved.read_text(encoding="ascii").strip()) / 1000
            if not -50 <= value <= 200:
                continue
            label_path = path.with_name(path.name.replace("_input", "_label"))
            label = label_path.read_text(encoding="utf-8").strip()[:80] if label_path.is_file() else path.stem
            priority = 0 if label.casefold() == "composite" else 1
            readings.append((priority, value, label))
        except (OSError, ValueError):
            continue
    if not readings:
        return None, None
    _, value, label = min(readings, key=lambda item: (item[0], item[2]))
    return value, label


def _read_smartctl(block: Path) -> tuple[bool, str, float | None]:
    executable = _smartctl_executable()
    if executable is None:
        return False, "unavailable", None
    try:
        completed = subprocess.run(
            [str(executable), "-H", "-A", "-j", str(block)],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin", "LANG": "C", "LC_ALL": "C"},
        )
        if len(completed.stdout) > 1024 * 1024:
            return False, "unavailable", None
        payload = json.loads(completed.stdout)
        passed = payload.get("smart_status", {}).get("passed")
        status_value = "passed" if passed is True else "failed" if passed is False else "unknown"
        temperature = payload.get("temperature", {}).get("current")
        if not isinstance(temperature, (int, float)) or not -50 <= temperature <= 200:
            temperature = None
        return True, status_value, float(temperature) if temperature is not None else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError):
        return False, "unavailable", None


def _smartctl_executable() -> Path | None:
    for candidate in SMARTCTL_CANDIDATES:
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_uid == 0
                and info.st_mode & 0o022 == 0
                and info.st_mode & stat.S_IXUSR
            ):
                return resolved
        except OSError:
            continue
    return None


disk_telemetry = DiskTelemetryService()
