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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import data_dir
from app.models_mgmt.amd_gpu import HELPER
from app.monitoring.fan_strategy import AdaptivePolicy

logger = logging.getLogger("control_deck.fan_control")
DRM_ROOT = Path("/sys/class/drm")
SYS_ROOT = Path("/sys/devices")
BDF_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
SENSOR_RE = re.compile(r"^temp[1-9][0-9]*$")
STATE_FILE = "amd-fan-control.json"
INTERVAL = 3.0
# Bursty LLM workloads must not turn into fan-speed oscillations.
COOL_HOLD_SECONDS = 180.0
COOL_STEP_SECONDS = 45.0
COOL_STEP_PERCENT = 5
ZERO_STABLE_SECONDS = 600.0
ZERO_STOP_TEMP_C = 55
ZERO_RISE_LIMIT_C_PER_MIN = 0.5
ZERO_START_RISE_C_PER_MIN = 1.0


class FanControlError(RuntimeError):
    """Safe, user-facing fan control failure."""


class FanSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    bdf: str | None = None
    min_percent: int = Field(default=35, ge=30, le=70)
    allow_zero_rpm: bool = False
    preset_id: Literal["recommended", "quiet", "cooling", "custom"] = "recommended"
    cool_hold_seconds: int = Field(default=180, ge=180, le=600)
    cool_step_seconds: int = Field(default=45, ge=45, le=180)
    cool_step_percent: int = Field(default=5, ge=1, le=5)
    zero_stable_seconds: int = Field(default=600, ge=600, le=1800)
    learning_enabled: bool = True
    ff_gain: float = Field(default=25.0, ge=25.0, le=55.0)
    kp: float = Field(default=1.8, ge=1.8, le=3.2)
    ki: float = Field(default=0.015, ge=0.005, le=0.03)  # Opt-in; native PMFW feature required
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
_pending_reset_bdf: str | None = None
_last_reset_retry = 0.0
# Monotonic state (not based on GPU utilization %).
_last_boost_at = 0.0
_cool_since: float | None = None
_last_cool_step = 0.0
_temp_history: dict[str, list[tuple[float, float]]] = {}
_zero_stable_since: float | None = None
_zero_active = False
_zero_initialized_bdf: str | None = None
_last_rise_c_per_min = 0.0
_applied_floor = 0
_last_tick_at: float | None = None
_policy = AdaptivePolicy()
_last_learning_save = 0.0
_LEARNING_FILE = "amd-fan-learning.json"

# Catalog is immutable and does not auto-enable hardware on selection.
# Sensor goals are resolved per GPU against current critical limits in the UI.
FAN_PRESETS = (
    {"id": "recommended", "label": "推奨・バランス", "description": "LLM常駐向け。常時低速・昇温時即加速・静かな降速",
     "min_percent": 35, "allow_zero_rpm": False, "cool_hold_seconds": 180,
     "cool_step_seconds": 45, "cool_step_percent": 5, "zero_stable_seconds": 600,
     "learning_enabled": True, "ff_gain": 25.0, "kp": 1.8, "ki": 0.015, "target_offset_c": 0},
    {"id": "quiet", "label": "静音・変動抑制", "description": "長めの保持でファン回転数の上下を最小化",
     "min_percent": 35, "allow_zero_rpm": False, "cool_hold_seconds": 300,
     "cool_step_seconds": 90, "cool_step_percent": 3, "zero_stable_seconds": 900,
     "learning_enabled": True, "ff_gain": 25.0, "kp": 1.8, "ki": 0.015, "target_offset_c": 0},
    {"id": "cooling", "label": "冷却優先", "description": "最低回転と冷却余裕を増やした保守的設定",
     "min_percent": 45, "allow_zero_rpm": False, "cool_hold_seconds": 300,
     "cool_step_seconds": 90, "cool_step_percent": 3, "zero_stable_seconds": 900,
     "learning_enabled": True, "ff_gain": 30.0, "kp": 2.0, "ki": 0.015, "target_offset_c": -5},
)



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


def _learning_file() -> Path:
    return data_dir() / _LEARNING_FILE


def _base_gains(settings: FanSettings) -> dict:
    return {"ff_gain": settings.ff_gain, "kp": settings.kp, "ki": settings.ki}


def _new_policy(settings: FanSettings, bdf: str) -> AdaptivePolicy:
    global _policy
    _policy = AdaptivePolicy(ff_gain=settings.ff_gain, kp=settings.kp, ki=settings.ki)
    if settings.learning_enabled:
        _load_learning(bdf, settings)
    return _policy


def _load_learning(bdf: str, settings: FanSettings) -> None:
    try:
        document = json.loads(_learning_file().read_text(encoding="utf-8"))
        if document.get("bdf") == bdf and document.get("base_gains") == _base_gains(settings):
            _policy.load_parameters(document.get("parameters", {}))
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def _save_learning(bdf: str, now: float, settings: FanSettings) -> None:
    global _last_learning_save
    if now - _last_learning_save < 1800:
        return
    _last_learning_save = now
    path = _learning_file()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"bdf": bdf, "base_gains": _base_gains(settings),
                                   "parameters": _policy.learned_parameters()}), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    except OSError:
        logger.warning("ファン制御学習値を保存できません", exc_info=True)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _zero_capable(directory: Path) -> bool:
    """Fail closed unless native OD ranges support conservative zero-RPM settings."""
    enable = directory / "fan_zero_rpm_enable"
    stop = directory / "fan_zero_rpm_stop_temperature"
    if not all(node.is_file() and not node.is_symlink() for node in (enable, stop)):
        return False
    try:
        contents = stop.read_text(encoding="ascii")
        enable_info = enable.read_text(encoding="ascii")
    except OSError:
        return False
    if not re.search(r"(?m)^\s*(?:FAN_)?ZERO_RPM_ENABLE\s*:\s*0\s+1\s*$",
                     enable_info.split("OD_RANGE:", 1)[-1] if "OD_RANGE:" in enable_info else ""):
        return False
    if "OD_RANGE:" not in contents:
        return False
    match = re.search(
        r"(?im)^\s*(?:FAN_)?ZERO_RPM_STOP_(?:TEMP|TEMPERATURE)\s*:\s*(\d+)\s*C?\s+(\d+)\s*C?\s*$",
        contents.split("OD_RANGE:", 1)[1],
    )
    if match is None:
        return False
    lower, upper = map(int, match.groups())
    return 25 <= lower <= ZERO_STOP_TEMP_C and max(50, lower) <= upper and lower < upper


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
        power = _read_int(hwmon / "power1_average")
        if power is None:
            power = _read_int(hwmon / "power1_input")
        power_cap = _read_int(hwmon / "power1_cap")
        found[device.name] = {
            "bdf": device.name, "vram_bytes": _read_int(device / "mem_info_vram_total") or 0,
            "sensors": sensors, "fan_rpm": _read_int(hwmon / "fan1_input"),
            "curve_supported": curve_range is not None,
            "zero_rpm_supported": curve_range is not None and _zero_capable(device / "gpu_od/fan_ctrl"),
            "power_w": round(power / 1_000_000, 1) if power is not None and power >= 0 else None,
            "power_cap_w": round(power_cap / 1_000_000, 1) if power_cap is not None and power_cap > 0 else None,
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
    if settings.allow_zero_rpm and not device["zero_rpm_supported"]:
        raise FanControlError("このGPUは安全なPMFW Zero RPM設定に対応していません")
    if settings.allow_zero_rpm and (
        device["fan_rpm"] is None or device["power_w"] is None or device["power_cap_w"] is None
    ):
        raise FanControlError("Zero RPMにはRPM・GPU電力・電力上限の取得が必要です")
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
    if action in ("zero-allow", "zero-deny"):
        if curve is not None:
            raise FanControlError("Zero RPMにfan_curve引数は指定できません")
        argv = ["sudo", "-n", str(HELPER), "fan-zero-rpm", action.removeprefix("zero-"), bdf]
    elif action in ("apply", "reset"):
        argv = ["sudo", "-n", str(HELPER), "fan-curve", action, bdf]
        if curve is not None:
            argv.append(curve)
    else:
        raise FanControlError("不正なGPUファン操作です")
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


def _rise_rate(sensors: list[dict], now: float) -> float:
    """20-second thermal trend; do not react to GPU utilization spikes."""
    maximum = 0.0
    for sensor in sensors:
        sensor_id = sensor["id"]
        entries = _temp_history.setdefault(sensor_id, [])
        if entries and now < entries[-1][0]:
            entries.clear()
        entries.append((now, float(sensor["value_c"])))
        entries[:] = [(t, v) for t, v in entries if now - t <= 45]
        baseline = [item for item in entries if now - item[0] >= 15]
        if baseline:
            old_time, old_value = baseline[-1]
            rate = (float(sensor["value_c"]) - old_value) * 60 / max(1, now - old_time)
            maximum = max(maximum, rate)
    return maximum


def _predictive_floor(sensors: list[dict], minimum: int, maximum: int,
                      rise_c_per_min: float) -> int:
    """Raise early if extrapolated time to a target is small."""
    floor = _desired_floor(sensors, minimum, maximum)
    headroom = min(s["headroom_c"] for s in sensors)
    if headroom <= 0:
        return maximum
    if rise_c_per_min > 1.5:
        minutes_to_target = headroom / rise_c_per_min
        if minutes_to_target <= 1:
            floor = maximum
        elif minutes_to_target <= 2:
            floor = max(floor, 90)
        elif minutes_to_target <= 4:
            floor = max(floor, 75)
    return min(maximum, floor)


def _smooth_floor(wanted: int, current: int, now: float, *,
                  hold_seconds: int = COOL_HOLD_SECONDS,
                  step_seconds: int = COOL_STEP_SECONDS,
                  step_percent: int = COOL_STEP_PERCENT) -> int:
    """Immediate spin-up, 3-minute hold then 5% steps no faster than 45 s."""
    global _cool_since, _last_cool_step, _last_boost_at
    if wanted >= current:
        _cool_since = None
        if wanted > current:
            _last_boost_at = now
        return wanted
    if _cool_since is None:
        _cool_since = now
        return current
    if now - _cool_since < hold_seconds:
        return current
    if now - _last_cool_step < step_seconds:
        return current
    _last_cool_step = now
    return max(wanted, current - step_percent)


def _stop_entry_safe(sensors: list[dict], rise: float) -> bool:
    if rise > ZERO_RISE_LIMIT_C_PER_MIN:
        return False
    for sensor in sensors:
        label = sensor["label"].casefold()
        cap = 50 if any(word in label for word in ("mem", "vram")) else (
            45 if any(word in label for word in ("junction", "hotspot")) else 42
        )
        if sensor["value_c"] > cap or sensor["headroom_c"] < 25:
            return False
    return True


def _stop_must_end(sensors: list[dict], rise: float) -> bool:
    # Exit early; hardware's 40C native stop limit remains an independent brake.
    if rise >= ZERO_START_RISE_C_PER_MIN:
        return True
    for sensor in sensors:
        label = sensor["label"].casefold()
        cap = 55 if any(word in label for word in ("mem", "vram")) else (
            49 if any(word in label for word in ("junction", "hotspot")) else 46
        )
        if sensor["value_c"] >= cap or sensor["headroom_c"] < 20:
            return True
    return False


def _failure(exc: Exception, bdf: str | None) -> None:
    global _state, _error, _active_bdf, _last_curve, _sensor_ids, _pending_reset_bdf
    global _zero_active, _zero_initialized_bdf, _temp_history, _zero_stable_since
    global _last_tick_at
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
    _pending_reset_bdf = bdf if reset_error else None
    _active_bdf, _last_curve, _sensor_ids = None, None, set()
    _zero_active, _zero_initialized_bdf = False, None
    _zero_stable_since, _temp_history, _last_tick_at = None, {}, None


def tick() -> None:
    global _active_bdf, _last_curve, _last_write, _floor, _state, _error, _last_sample
    global _sensor_ids, _zero_rpm_checks, _pending_reset_bdf, _last_reset_retry
    global _zero_active, _zero_initialized_bdf, _zero_stable_since, _temp_history
    global _last_rise_c_per_min, _applied_floor, _cool_since, _last_cool_step, _policy, _last_tick_at
    with _lock:
        try:
            settings = _load()
        except Exception as exc:  # fail safe even for invalid persisted settings
            _failure(exc, _active_bdf)
            return
        now = time.monotonic()
        # Monotonic elapsed time is NOT observed cooling time. After suspend,
        # stalled watchdog or process pause, reset all quiet/stop dwell clocks.
        if _last_tick_at is not None and (
            now < _last_tick_at or now - _last_tick_at > 4 * INTERVAL
        ):
            _temp_history = {}
            _cool_since = None
            _zero_stable_since = None
            _policy.reset_dwell(now)
            logger.warning("GPU温度の継続観測が途絶えたため低速化/停止の待機判定をリセット")
        _last_tick_at = now
        if _pending_reset_bdf and now - _last_reset_retry >= 10:
            _last_reset_retry = now
            try:
                _helper("reset", _pending_reset_bdf)
                logger.warning("GPU標準ファン制御への復帰リトライに成功しました")
                _pending_reset_bdf = None
                _error += " / 標準自動制御への復帰済み"
            except FanControlError:
                logger.warning("GPU標準ファン制御の復帰待ち: %s", _pending_reset_bdf)
        if not settings.enabled or _state == "fault":
            return
        bdf = settings.bdf or _active_bdf
        try:
            device = _select(_devices(), bdf)
            bdf = device["bdf"]
            sensors = _validate(settings, device)
            ids = {item["id"] for item in sensors}
            if _sensor_ids and ids != _sensor_ids:
                raise FanControlError("制御中に温度センサー構成が変化しました")
            if _active_bdf != bdf:
                _temp_history, _cool_since, _zero_stable_since = {}, None, None
                _new_policy(settings, bdf)
            maximum = device["_range"][3]
            rise = _rise_rate(sensors, now)
            _last_rise_c_per_min = round(rise, 2)
            strategy = _policy.update(
                sensors,
                power_w=device["power_w"], power_cap_w=device["power_cap_w"],
                min_percent=settings.min_percent, max_percent=maximum,
                now=now, dt=INTERVAL, allow_stop=settings.allow_zero_rpm,
                learning_enabled=settings.learning_enabled,
                stop_dwell_seconds=settings.zero_stable_seconds,
            )
            # The independent safety floor wins over learned PI/FF output.
            wanted = max(_predictive_floor(sensors, settings.min_percent, maximum, rise),
                         strategy["floor_percent"])
            floor = wanted if _active_bdf != bdf else _smooth_floor(
                wanted, _floor, now, hold_seconds=settings.cool_hold_seconds,
                step_seconds=settings.cool_step_seconds, step_percent=settings.cool_step_percent,
            )
            curve = _curve(device["_range"], floor)
            hot = max(item["value_c"] for item in sensors)
            rpm = device["fan_rpm"]

            # Native zero-RPM mode is explicitly disabled on each controller
            # startup so the normal default has an always-spinning idle fan.
            if _zero_initialized_bdf != bdf:
                if device["zero_rpm_supported"]:
                    _helper("zero-deny", bdf)
                _zero_initialized_bdf = bdf
                _zero_active = False
                _last_curve = None
            if _zero_active and (
                not settings.allow_zero_rpm or _stop_must_end(sensors, rise)
                or strategy["wake_required"]
            ):
                # Sensor- or temperature-rise-driven wake has no cool-down wait.
                _helper("zero-deny", bdf)
                _zero_active = False
                _zero_stable_since = None
                _policy.exited_stop(now)
                _last_curve = None  # Firmware may have switched modes.
                _zero_rpm_checks = 0

            # Zero-RPM is never treated as stalled when firmware was explicitly
            # permitted to stop. Once re-enabled, a hot zero RPM still faults.
            _zero_rpm_checks = (
                _zero_rpm_checks + 1
                if not _zero_active and rpm == 0 and hot >= 60 and floor >= 70
                else 0
            )
            if _zero_rpm_checks >= 2:
                raise FanControlError("高温時にファンRPMが0のままです。制御を停止しました")

            # Every increase is applied immediately (not delayed by the
            # slow-down write budget); decreases are spaced apart.
            increasing = floor > _applied_floor
            if not _zero_active and curve != _last_curve and (increasing or now - _last_write >= 10):
                _helper("apply", bdf, curve)
                _last_curve, _last_write = curve, now
                _applied_floor = floor

            if settings.allow_zero_rpm and device["zero_rpm_supported"] and not _zero_active:
                eligible = _stop_entry_safe(sensors, rise)
                _zero_stable_since = (
                    now if eligible and _zero_stable_since is None
                    else _zero_stable_since if eligible else None
                )
                if (_zero_stable_since is not None and now - _zero_stable_since >= settings.zero_stable_seconds
                        and strategy["zero_stop_eligible"]):
                    # PMFW auto owns the stopped state. Do not force PWM=0.
                    _helper("reset", bdf)
                    _helper("zero-allow", bdf)
                    _zero_active = True
                    _last_curve = None
                    _applied_floor = 0
            elif not _zero_active:
                _zero_stable_since = None

            if not _zero_active:
                if settings.learning_enabled:
                    _save_learning(bdf, now, settings)
            _active_bdf, _floor, _sensor_ids = bdf, floor, ids
            _state, _error = ("fan-stop" if _zero_active else "controlling"), ""
            _last_sample = {
                "bdf": bdf, "sensors": sensors, "fan_rpm": rpm,
                "floor_percent": None if _zero_active else floor,
                "critical_sensor": min(sensors, key=lambda x: x["headroom_c"])["id"],
                "rise_c_per_min": _last_rise_c_per_min,
                "zero_rpm_permitted": _zero_active, "cool_hold_seconds": settings.cool_hold_seconds,
                "power_ema_w": strategy["power_ema_w"],
                "min_predicted_headroom_c": strategy["min_predicted_headroom_c"],
                "learning": strategy["learning"],
                "observed_at": time.time(), "curve": _last_curve,
            }
        except Exception as exc:  # noqa: BLE001 - fail safe on unexpected sysfs/runtime errors
            _failure(exc, bdf)


def configure(settings: FanSettings) -> dict:
    global _active_bdf, _last_curve, _floor, _sensor_ids, _state, _error, _zero_rpm_checks, _pending_reset_bdf
    global _zero_active, _zero_initialized_bdf, _temp_history, _zero_stable_since, _applied_floor
    global _cool_since, _last_cool_step, _last_tick_at, _policy
    with _lock:
        prior = _load()
        if settings.enabled:
            if _pending_reset_bdf is not None or _state == "fault" and prior.enabled:
                raise FanControlError("自動制御への復帰待ちです。復帰してから再有効化してください")
            device = _select(_devices(), settings.bdf)
            _validate(settings, device)
            previous_bdf = _active_bdf or prior.bdf
            if prior.enabled and previous_bdf and previous_bdf != device["bdf"]:
                _helper("reset", previous_bdf)
                _active_bdf = None
                _last_curve = None
            _pending_reset_bdf = None
            _state, _error = "disabled", ""
            _persist(settings.model_copy(update={"bdf": device["bdf"]}))
            _floor, _sensor_ids, _zero_rpm_checks = settings.min_percent, set(), 0
            _cool_since, _last_cool_step, _zero_stable_since = None, 0.0, None
            _temp_history = {}
            _last_tick_at = None
            _applied_floor = 0
            _new_policy(settings, device["bdf"])
            tick()
            if _state == "fault":
                raise FanControlError(_error)
        else:
            # A failed prior auto-restore is latched even after enabled=False was
            # persisted; explicit "return to auto" must retry, not clear the latch.
            targets = {item for item in (
                _pending_reset_bdf,
                _active_bdf,
                prior.bdf if prior.enabled else None,
            ) if item}
            for target in sorted(targets):
                try:
                    _helper("reset", target)
                except FanControlError as exc:
                    _pending_reset_bdf = target
                    _state, _error = "fault", f"自動制御へ復帰できません: {exc}"
                    raise
            _persist(settings)
            _active_bdf, _last_curve, _sensor_ids = None, None, set()
            _state, _error, _pending_reset_bdf = "disabled", "", None
            _zero_active, _zero_initialized_bdf = False, None
            _temp_history, _zero_stable_since, _cool_since, _last_tick_at = {}, None, None, None
            _policy = AdaptivePolicy()
        return status()


def status() -> dict:
    with _lock:
        settings = _load()
        devices = _devices()
        for device in devices:
            limits = device.pop("_range", None)
            device["curve_limits"] = (
                {"temp_min_c": limits[0], "temp_max_c": limits[1],
                 "pwm_min_percent": limits[2], "pwm_max_percent": limits[3]}
                if limits is not None else None
            )
        return {
            "settings": settings.model_dump(), "devices": devices,
            "helper_installed": HELPER.is_file(),
            "state": _state if settings.enabled or _state == "fault" else "disabled",
            "error": _error, "pending_auto_restore": _pending_reset_bdf is not None,
            "control_policy": "adaptive_ff_pi_antiwindup_fast_rise_slow_fall",
            "learned_parameters": _policy.learned_parameters(),
            "presets": list(FAN_PRESETS),
            "cool_hold_seconds": COOL_HOLD_SECONDS,
            "zero_stable_seconds": settings.zero_stable_seconds,
            "effective_cool_hold_seconds": settings.cool_hold_seconds,
            "effective_cool_step_seconds": settings.cool_step_seconds,
            "active": _last_sample if settings.enabled else None,
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
