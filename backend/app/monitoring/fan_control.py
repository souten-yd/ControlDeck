"""Firmware-backed AMD fan curves with multiple GPU thermal-sensor targets.

No external fan-control software, root web process, or raw user-supplied paths.
The PMFW always owns the instantaneous fan ramp; this worker only revises
its five-point curve if another on-card sensor (e.g. VRAM) needs more cooling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import data_dir
from app.models_mgmt.amd_gpu import HELPER

logger = logging.getLogger("control_deck.fan_control")
DRM_ROOT = Path("/sys/class/drm")
SYS_ROOT = Path("/sys/devices")
BDF_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
SENSOR_RE = re.compile(r"^temp[1-9][0-9]*$")
STATE_FILE = "amd-fan-control.json"
INTERVAL = 5.0


class FanControlError(RuntimeError):
    """Safe, user-facing fan control failure."""


class FanSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    bdf: str | None = None
    min_percent: int = Field(default=35, ge=30, le=70)
    targets_c: dict[str, int] = Field(default_factory=dict, max_length=16)

    @field_validator("bdf")
    @classmethod
    def valid_bdf(cls, value: str | None) -> str | None:
        if value is not None and not BDF_RE.fullmatch(value):
            raise ValueError("PCI BDFが不正です")
        return value

    @field_validator("targets_c")
    @classmethod
    def valid_targets(cls, values: dict[str, int]) -> dict[str, int]:
        if any(not SENSOR_RE.fullmatch(k) or isinstance(v, bool) or not 45 <= v <= 95
               for k, v in values.items()):
            raise ValueError("センサー目標はtempN形式、45〜95°Cの整数にしてください")
        return values


_lock = threading.RLock()
_active_bdf: str | None = None
_last_curve: str | None = None
_last_write = 0.0
_floor = 35
_sensor_ids: set[str] = set()
_zero_rpm_checks = 0
_state = "disabled"
_error = ""
_last_sample: dict = {}


def _setting_path() -> Path:
    return data_dir() / STATE_FILE


def _load() -> FanSettings:
    path = _setting_path()
    try:
        return FanSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return FanSettings()
    except (OSError, ValueError) as exc:
        raise FanControlError(f"ファン設定ファイルが不正です: {exc}") from exc


def _persist(settings: FanSettings) -> None:
    target = _setting_path()
    temp = target.with_suffix(".tmp")
    try:
        temp.write_text(settings.model_dump_json(), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(target)
    except OSError as exc:
        raise FanControlError(f"ファン設定を保存できません: {exc}") from exc


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _devices() -> list[dict]:
    found: dict[str, dict] = {}
    for link in sorted(DRM_ROOT.glob("card[0-9]*/device")):
        device = link.resolve()
        if not BDF_RE.fullmatch(device.name) or not device.is_relative_to(SYS_ROOT.resolve()):
            continue
        if device.name in found:
            continue
        try:
            if (device / "vendor").read_text(encoding="ascii").strip().lower() != "0x1002":
                continue
        except OSError:
            continue
        hwmons = []
        for hw in sorted(device.glob("hwmon/hwmon*")):
            path = hw.resolve()
            if path.is_relative_to(device):
                try:
                    if (path / "name").read_text(encoding="ascii").strip() == "amdgpu":
                        hwmons.append(path)
                except OSError:
                    pass
        if not hwmons:
            continue
        hwmon = hwmons[0]
        curve = device / "gpu_od/fan_ctrl/fan_curve"
        sensors = []
        for path in sorted(hwmon.glob("temp*_input")):
            sid = path.name.removesuffix("_input")
            if not SENSOR_RE.fullmatch(sid):
                continue
            raw = _read_int(path)
            try:
                label = (hwmon / f"{sid}_label").read_text(encoding="ascii").strip()
            except OSError:
                label = sid
            critical = _read_int(hwmon / f"{sid}_crit")
            emergency = _read_int(hwmon / f"{sid}_emergency")
            # Bad sensors are NOT silently converted to 0 degrees.
            current = round(raw / 1000, 1) if raw is not None and -20_000 <= raw <= 125_000 else None
            upper = min(
                [95] + [int(x / 1000) - margin for x, margin in ((critical, 5), (emergency, 10)) if x is not None]
            )
            name = label.casefold()
            default = 85 if any(k in name for k in ("mem", "vram")) else 90 if any(
                k in name for k in ("junction", "hotspot")) else 75
            sensors.append({
                "id": sid, "label": label, "value_c": current,
                "crit_c": critical / 1000 if critical is not None else None,
                "emergency_c": emergency / 1000 if emergency is not None else None,
                "max_target_c": upper,
                "default_target_c": min(default, upper),
            })
        curve_range = None
        if curve.is_file() and not curve.is_symlink():
            try:
                raw_curve = curve.read_text(encoding="ascii")
                t = re.search(r"FAN_CURVE\(hotspot temp\):\s*(\d+)\s*C\s+(\d+)\s*C", raw_curve, re.I)
                p = re.search(r"FAN_CURVE\(fan speed\):\s*(\d+)\s*%\s+(\d+)\s*%", raw_curve, re.I)
                pts = re.findall(r"^\s*(\d+):\s*\d+\s*C\s+\d+\s*%", raw_curve, re.M)
                if t and p and pts == ["0", "1", "2", "3", "4"]:
                    t0, t1 = map(int, t.groups())
                    p0, p1 = map(int, p.groups())
                    if t1 > t0 + 4 and p0 <= 40 and 90 <= p1 <= 100 and p1 > p0:
                        curve_range = (t0, t1, p0, p1)
            except OSError:
                pass
        found[device.name] = {
            "bdf": device.name, "vram_bytes": _read_int(device / "mem_info_vram_total") or 0,
            "sensors": sensors, "fan_rpm": _read_int(hwmon / "fan1_input"),
            "curve_supported": curve_range is not None,
            "_range": curve_range,
        }
    return sorted(found.values(), key=lambda item: -item["vram_bytes"])


def _select(devices: list[dict], bdf: str | None) -> dict:
    if bdf:
        device = next((d for d in devices if d["bdf"] == bdf), None)
        if device is None:
            raise FanControlError("選択したAMD GPUが検出されません")
        return device
    supported = [d for d in devices if d["curve_supported"]]
    if len(supported) != 1:
        raise FanControlError("ファン制御対象GPUを明示的に選択してください")
    return supported[0]


def _validate(settings: FanSettings, device: dict) -> list[dict]:
    if not device["curve_supported"]:
        raise FanControlError("GPUドライバが安全な5点fan_curve/OD_RANGEを公開していません")
    if not HELPER.is_file():
        raise FanControlError("ControlDeck特権helperが未導入です（./deck.sh serviceで登録）")
    sensors = device["sensors"]
    if not sensors or any(s["value_c"] is None or s["max_target_c"] < 45 for s in sensors):
        raise FanControlError("温度センサーの一部が取得できないため制御を開始できません")
    if set(settings.targets_c) - {s["id"] for s in sensors}:
        raise FanControlError("保存済み温度センサーが消失しました")
    for sensor in sensors:
        goal = settings.targets_c.get(sensor["id"], sensor["default_target_c"])
        if not 45 <= goal <= sensor["max_target_c"]:
            raise FanControlError(f"{sensor['label']}の目標値は45〜{sensor['max_target_c']}°Cにしてください")
        sensor["target_c"] = goal
        sensor["headroom_c"] = round(goal - sensor["value_c"], 1)
    return sensors


def _helper(action: str, bdf: str, curve: str | None = None) -> None:
    argv = ["sudo", "-n", str(HELPER), "fan-curve", action, bdf]
    if curve is not None:
        argv.append(curve)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FanControlError(f"ファン制御helperを実行できません: {exc}") from exc
    if result.returncode:
        raise FanControlError("ファン制御helperが拒否しました: " +
                              (result.stderr.strip()[:240] or f"exit {result.returncode}"))


def _curve(ranges: tuple[int, int, int, int], floor: int) -> str:
    tmin, tmax, pmin, pmax = ranges
    start, end = max(tmin, 25), min(tmax, 95)
    if end - start < 4:
        raise FanControlError("GPUの温度カーブ範囲が狭すぎます")
    anchor = [start + ((end - start) * i) // 4 for i in range(5)]
    base = (35, 45, 65, 90, pmax)
    speeds = [min(pmax, max(pmin, floor, value)) for value in base]
    speeds[-1] = pmax
    return ",".join(f"{temp}:{pwm}" for temp, pwm in zip(anchor, speeds, strict=True))


def _desired_floor(sensors: list[dict], minimum: int, maximum: int) -> int:
    margin = min(sensor["headroom_c"] for sensor in sensors)
    request = 35 if margin >= 25 else 40 if margin >= 20 else 50 if margin >= 15 else (
        60 if margin >= 10 else 75 if margin >= 5 else 90 if margin >= 2 else maximum
    )
    return min(maximum, max(minimum, request))


def _failure(exc: Exception, bdf: str | None) -> None:
    global _state, _error, _active_bdf, _last_curve, _sensor_ids
    logger.error("GPUファン制御を停止します: %s", exc)
    reset_error = ""
    if bdf:
        try:
            _helper("reset", bdf)
        except FanControlError as reset_exc:
            reset_error = f" / 自動復帰失敗: {reset_exc}"
            logger.exception("GPU自動ファン制御への復帰に失敗")
    try:
        current = _load()
        if current.enabled:
            _persist(current.model_copy(update={"enabled": False}))
    except FanControlError:
        logger.exception("GPUファン設定無効化の永続化に失敗")
    _state, _error = "fault", str(exc) + reset_error
    _active_bdf, _last_curve, _sensor_ids = None, None, set()


def tick() -> None:
    global _active_bdf, _last_curve, _last_write, _floor, _state, _error, _last_sample
    global _sensor_ids, _zero_rpm_checks
    with _lock:
        settings = _load()
        if not settings.enabled:
            return
        bdf = settings.bdf or _active_bdf
        try:
            device = _select(_devices(), bdf)
            bdf = device["bdf"]
            sensors = _validate(settings, device)
            ids = {item["id"] for item in sensors}
            if _sensor_ids and ids != _sensor_ids:
                raise FanControlError("制御中に温度センサー構成が変化しました")
            maximum = device["_range"][3]
            wanted = _desired_floor(sensors, settings.min_percent, maximum)
            # Increase immediately, relax slowly to prevent hunting/chatter.
            floor = wanted if _active_bdf != bdf else (
                wanted if wanted >= _floor else max(wanted, _floor - 5)
            )
            curve = _curve(device["_range"], floor)
            hot = max(item["value_c"] for item in sensors)
            rpm = device["fan_rpm"]
            _zero_rpm_checks = _zero_rpm_checks + 1 if rpm == 0 and hot >= 60 and floor >= 70 else 0
            if _zero_rpm_checks >= 2:
                raise FanControlError("高温時にファンRPMが0のままです。制御を停止しました")
            if (curve != _last_curve or _active_bdf != bdf) and (
                _active_bdf != bdf or time.monotonic() - _last_write >= 10
                or floor >= 90
            ):
                _helper("apply", bdf, curve)
                _last_curve, _last_write = curve, time.monotonic()
            _active_bdf, _floor, _sensor_ids = bdf, floor, ids
            _state, _error = "controlling", ""
            _last_sample = {
                "bdf": bdf, "sensors": sensors, "fan_rpm": rpm,
                "floor_percent": floor, "critical_sensor": min(sensors, key=lambda x: x["headroom_c"])["id"],
                "observed_at": time.time(), "curve": _last_curve,
            }
        except (FanControlError, OSError, ValueError) as exc:
            _failure(exc, bdf)


def configure(settings: FanSettings) -> dict:
    global _active_bdf, _last_curve, _floor, _sensor_ids, _state, _error, _zero_rpm_checks
    with _lock:
        prior = _load()
        if settings.enabled:
            device = _select(_devices(), settings.bdf)
            _validate(settings, device)
            if prior.enabled and _active_bdf and _active_bdf != device["bdf"]:
                _helper("reset", _active_bdf)
                _active_bdf = None
                _last_curve = None
            _persist(settings.model_copy(update={"bdf": device["bdf"]}))
            _floor, _sensor_ids, _zero_rpm_checks = settings.min_percent, set(), 0
            tick()
            if _state == "fault":
                raise FanControlError(_error)
        else:
            if prior.enabled or _active_bdf:
                target = _active_bdf or prior.bdf
                if target:
                    _helper("reset", target)
            _persist(settings)
            _active_bdf, _last_curve, _sensor_ids = None, None, set()
            _state, _error = "disabled", ""
        return status()


def status() -> dict:
    with _lock:
        settings = _load()
        devices = _devices()
        for device in devices:
            device.pop("_range", None)
        return {
            "settings": settings.model_dump(), "devices": devices,
            "helper_installed": HELPER.is_file(),
            "state": _state if settings.enabled or _state == "fault" else "disabled",
            "error": _error, "active": _last_sample if settings.enabled else None,
            "poll_interval_seconds": INTERVAL,
            "warning": "温度上限は目標であり保証値ではありません。CPU・マザーボードのファンは制御しません。",
        }


async def run() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001 - isolate monitoring from web server
            logger.exception("GPU fan controller tick failed")
        await asyncio.sleep(INTERVAL)


def shutdown() -> None:
    """Leave device in firmware auto mode on orderly ControlDeck shutdown."""
    with _lock:
        settings = _load()
        target = _active_bdf or (settings.bdf if settings.enabled else None)
        if target:
            try:
                _helper("reset", target)
            except FanControlError:
                logger.exception("停止時にファン自動制御へ復帰できませんでした")
