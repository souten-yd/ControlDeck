"""AMD GPU のファン制御（v2: PID＋電力の先読み。温度マップは 2026-11-18 に撤去）。

外部のファン制御ソフトは使わない。root の処理は固定の helper だけで、利用者の入力からパスやコマンドを
作らない。ファームウェア（PMFW）の 5 点カーブを、制御の出力で「上限温度までは一定・上限を 1°C 超えたら最大」
に書き換える。ファームウェアは hotspot でカーブを読むので、アプリが止まっても上限での最大回転は残る。

制御の中身は ``fan_core``（純粋な計算）、自動調整は ``fan_tune``。
2026-11-17 に 4 方式・7 プリセット・入れ子の設定から作り直した（docs/design-amd-smart-fan.md）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import data_dir
from app.models_mgmt.amd_gpu import HELPER
from app.monitoring import fan_core, fan_log, fan_pid

logger = logging.getLogger("control_deck.fan_control")

DRM_ROOT = Path("/sys/class/drm")
SYS_ROOT = Path("/sys/devices")
BDF_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
SENSOR_RE = re.compile(r"^temp[1-9][0-9]*$")
STATE_FILE = "amd-fan-control.json"
_MODEL_FILE = "amd-fan-model.json"
INTERVAL = 3.0
# 既定の上限温度（センサーの種類ごと）。ハードウェアの crit−5 / emergency−10 を超えない。
# hotspot は 95°C を超えたら最大、普段は 90°C 以下（マージン 5°C）（2026-11-17 利用者の指示）
DEFAULT_LIMIT_C = {"junction": 95, "mem": 90, "edge": 85}
# 全力で一定の回転のまま落ち着いた状態から、熱モデルを少しずつ更新する間隔（外気温・埃で熱抵抗が変わる）
PASSIVE_SECONDS = 300.0


class FanControlError(RuntimeError):
    pass


class FanSettings(BaseModel):
    """利用者が決めるのはこれだけ。PID の効き具合は自動調整が埋める（手でも直せる）。"""
    # 旧版（4 方式・プリセット・温度マップ）の項目は読み捨てる。_load が上限温度などを引き継ぐ
    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    bdf: str | None = None
    # センサーごとの上限温度。これを超えたら最大回転。制御の目標は「上限 − マージン」
    limits_c: dict[str, int] = Field(default_factory=dict, max_length=16)
    margin_c: float = Field(default=5.0, ge=2.0, le=15.0, allow_inf_nan=False)
    min_percent: int = Field(default=20, ge=15, le=70)
    # 応答のなめらかさ（秒）。大きいほど 0%↔100% の往復負荷で回転が動かない
    smoothing_s: float = Field(default=60.0, ge=20.0, le=300.0, allow_inf_nan=False)
    # 既定は R9700 の実測（2026-11-18、fan_core.pid_gains）。自動調整が GPU ごとに決め直す
    pid_kp: float = Field(default=2.5, ge=0.3, le=10.0, allow_inf_nan=False)
    pid_ti: float = Field(default=80.0, ge=10.0, le=600.0, allow_inf_nan=False)
    # 効き具合の既定値の版。1: Ti 50 秒（v0.68）、2: Ti 80 秒（2026-11-18、2 段の模型で選び直した）
    gains_version: int = 2
    # PC 起動直後: 保存した設定で制御する / GPU 標準の自動制御のまま
    startup_mode: Literal["apply_saved", "firmware"] = "apply_saved"

    @field_validator("bdf")
    @classmethod
    def valid_bdf(cls, value: str | None) -> str | None:
        if value is not None and not BDF_RE.fullmatch(value):
            raise ValueError("PCI BDFが不正です")
        return value

    @field_validator("limits_c")
    @classmethod
    def valid_limits(cls, values: dict[str, int]) -> dict[str, int]:
        if any(not SENSOR_RE.fullmatch(k) or isinstance(v, bool) or not 50 <= v <= 100
               for k, v in values.items()):
            raise ValueError("上限温度はtempN形式、50〜100°Cの整数にしてください")
        return values



_lock = threading.RLock()
_active_bdf: str | None = None
_last_curve: str | None = None
_applied_percent = 0
_sensor_ids: set[str] = set()
_zero_rpm_checks = 0
_state = "disabled"
_error = ""
_last_sample: dict = {}
_pending_reset_bdf: str | None = None
_last_reset_retry = 0.0
_reset_retry_failures = 0
MAX_RESET_RETRIES = 3
# SMU 14（RDNA4、R9700 など）: fan_curve のリセットを SMU が拒み、以後の OD 書き込みが再起動まで全部失敗する。
# このような GPU では決してリセットせず、最後のカーブ（上限で最大）を保持する。再起動で PMFW 標準に戻る。
NO_RESTORE_SMU_MAJOR = {14}
HOLD_NOTICE = "このGPUは標準の自動制御へ戻すとファン設定が壊れるため、最後のファンカーブを保持しています。PCを再起動すると標準の自動制御に戻ります"
_no_restore_bdfs: set[str] = set()
REBOOT_HINT = "GPUのファン設定を標準に戻せません。PCを再起動すると標準の自動制御に戻ります"
_last_tick_at: float | None = None
_controller = fan_core.Controller()
_model = fan_pid.ThermalModel()
_model_bdf: str | None = None
_tracker = fan_pid.SteadyTracker()
_last_model_save = 0.0
_saved_integral = 0.0
_last_passive = 0.0
_last_log_prune = -1e9
HISTORY_SECONDS = 3600
_history: deque = deque(maxlen=int(HISTORY_SECONDS / INTERVAL))


def _setting_path() -> Path:
    return data_dir() / STATE_FILE


def _migrate(document: dict) -> dict:
    """旧版の設定から引き継ぐのは、有効/無効・GPU・最低回転数・起動時の扱い・温度追従のマージンだけ。

    * 旧版の targets_c は「目標」だった（R9700: hotspot 90°C）。新しい版の limits_c は「上限」なので、
      そのまま移すと目標が 5°C 下がる。引き継がず既定（hotspot 上限 95・目標 90°C、2026-11-17 利用者の指示）にする
    * PID の効き具合も引き継がない（旧版の Kp は弱すぎた。R9700 で 1.33 → 2.5、2026-11-18 実測）
    """
    if "limits_c" in document or not any(k in document for k in ("targets_c", "control_mode", "preset_id")):
        return document
    old_mode = document.get("control_mode")
    return {
        "enabled": document.get("enabled", False), "bdf": document.get("bdf"),
        "margin_c": float(document.get("temperature_margin_c", 5.0)) if old_mode == "target" else 5.0,
        "min_percent": max(15, int(document.get("min_percent", 20))),
        "startup_mode": document.get("startup_mode", "apply_saved"),
    }


def _upgrade_gains(document: dict) -> dict:
    """v0.68 で保存した設定の Ti が当時の既定値（50 秒）のままなら、今の既定（80 秒）にする。
    手で変えた値はそのまま。マージンなどを画面で保存すると、既定値の Ti も一緒に書かれていた。"""
    if "limits_c" in document and "gains_version" not in document:
        document = {**document, "gains_version": 2}
        if float(document.get("pid_ti", 50.0)) == 50.0:
            document["pid_ti"] = 80.0
    return document


def _load() -> FanSettings:
    path = _setting_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("設定がオブジェクトではありません")
        return FanSettings.model_validate(_upgrade_gains(_migrate(document)))
    except FileNotFoundError:
        return FanSettings()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
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


def _sensor_kind(label: str) -> str:
    name = label.casefold()
    if any(k in name for k in ("mem", "vram")):
        return "mem"
    if any(k in name for k in ("junction", "hotspot")):
        return "junction"
    return "edge"


def _smu_major(device: Path) -> int | None:
    return _read_int(device / "ip_discovery/die/0/MP1/0/major")


def _restore_supported(bdf: str | None) -> bool:
    return bool(bdf) and bdf not in _no_restore_bdfs


def _release(bdf: str) -> bool:
    """安全なら PMFW 標準へ戻す。False: カーブを保持した。"""
    if not _restore_supported(bdf):
        logger.warning("GPU %s は標準制御へ戻せないため最後のファンカーブを保持します", bdf)
        return False
    _helper("reset", bdf)
    return True


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
            # 壊れた値を 0 度に化けさせない
            current = round(raw / 1000, 1) if raw is not None and -20_000 <= raw <= 125_000 else None
            upper = min(
                [100] + [int(x / 1000) - margin for x, margin in ((critical, 5), (emergency, 10)) if x is not None]
            )
            kind = _sensor_kind(label)
            sensors.append({
                "id": sid, "label": label, "value_c": current, "kind": kind,
                "crit_c": critical / 1000 if critical is not None else None,
                "emergency_c": emergency / 1000 if emergency is not None else None,
                "max_limit_c": upper,
                "default_limit_c": min(DEFAULT_LIMIT_C.get(kind, 85), upper),
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
        if _smu_major(device) in NO_RESTORE_SMU_MAJOR:
            _no_restore_bdfs.add(device.name)
        power = _read_int(hwmon / "power1_average")
        if power is None:
            power = _read_int(hwmon / "power1_input")
        power_cap = _read_int(hwmon / "power1_cap")
        found[device.name] = {
            "bdf": device.name, "vram_bytes": _read_int(device / "mem_info_vram_total") or 0,
            "sensors": sensors, "fan_rpm": _read_int(hwmon / "fan1_input"),
            "curve_supported": curve_range is not None,
            "power_w": round(power / 1_000_000, 1) if power is not None and power >= 0 else None,
            "power_cap_w": round(power_cap / 1_000_000, 1) if power_cap is not None and power_cap > 0 else None,
            "restore_supported": device.name not in _no_restore_bdfs,
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
    """センサーに上限温度と目標を付けて返す。"""
    if not device["curve_supported"]:
        raise FanControlError("GPUドライバが安全な5点fan_curve/OD_RANGEを公開していません")
    if not HELPER.is_file():
        raise FanControlError("ControlDeck特権helperが未導入です（./deck.sh serviceで登録）")
    sensors = device["sensors"]
    if not sensors or any(s["value_c"] is None or s["max_limit_c"] < 50 for s in sensors):
        raise FanControlError("温度センサーの一部が取得できないため制御を開始できません")
    if set(settings.limits_c) - {s["id"] for s in sensors}:
        raise FanControlError("保存済み温度センサーが消失しました")
    for sensor in sensors:
        limit = settings.limits_c.get(sensor["id"], sensor["default_limit_c"])
        if not 50 <= limit <= sensor["max_limit_c"]:
            raise FanControlError(f"{sensor['label']}の上限温度は50〜{sensor['max_limit_c']}°Cにしてください")
        if limit - settings.margin_c < 40:
            raise FanControlError(f"{sensor['label']}の上限温度からマージンを引いた目標が低すぎます")
        sensor["limit_c"] = limit
        sensor["target_c"] = round(limit - settings.margin_c, 1)
        sensor["headroom_c"] = round(limit - sensor["value_c"], 1)
    return sensors


def _helper(action: str, bdf: str, curve: str | None = None) -> None:
    if action not in ("apply", "reset"):
        raise FanControlError("不正なGPUファン操作です")
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


def _model_path() -> Path:
    return data_dir() / _MODEL_FILE


def load_model(bdf: str) -> fan_pid.ThermalModel:
    try:
        document = json.loads(_model_path().read_text(encoding="utf-8"))
        return fan_pid.ThermalModel.from_json(document.get(bdf, {}))
    except (OSError, ValueError, AttributeError):
        return fan_pid.ThermalModel()


def save_model(bdf: str, model: fan_pid.ThermalModel) -> None:
    path = _model_path()
    try:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                document = {}
        except (OSError, ValueError):
            document = {}
        document[bdf] = model.to_json()
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(path)
    except OSError:
        logger.warning("ファンの熱モデルを保存できません")


def refit_from_log() -> dict:
    """診断の記録（直近 2 日）から熱モデルと PID の効き具合を作り直して保存する（数秒。GPU は使わない）。

    自動調整（30〜90 分）の代わり。上乗せ（積分）は 0 から学び直す（熱モデルが今の外気温に合ったため）。
    """
    global _model, _model_bdf
    with _lock:
        settings = _load()
        device = _select(_devices(), settings.bdf or _active_bdf)
        sensors = _validate(settings, device)
        sensor_ids = {s["kind"]: s["id"] for s in sensors if s["kind"] in fan_log.KIND_COLUMN}
        bdf = device["bdf"]
        model = fan_pid.ThermalModel.from_json(load_model(bdf).to_json())
        rows = fan_log.read(since=time.time() - fan_log.KEEP_DAYS * 86400)
        changes = fan_log.refit(rows, model, sensor_ids, device["power_cap_w"])   # ValueError は呼び出し側へ
        kp, ti = fan_core.pid_gains(model)
        model.integral_percent = 0.0
        save_model(bdf, model)
        if _model_bdf == bdf:
            _model = model
            _controller.integral_loaded = False
        _persist(settings.model_copy(update={"pid_kp": kp, "pid_ti": ti}))
        logger.info("ファン制御を記録から作り直しました: %s Kp=%s Ti=%s", changes, kp, ti)
        return {"changes": changes, "pid_kp": kp, "pid_ti": ti, "minutes": len(rows)}


def log_summary(hours: float = 24.0) -> dict:
    return fan_log.summary(fan_log.read(since=time.time() - hours * 3600))


def core_settings(settings: FanSettings, sensors: list[dict], maximum: float) -> fan_core.CoreSettings:
    return fan_core.CoreSettings(
        limits_c={s["id"]: float(s["limit_c"]) for s in sensors},
        margin_c=settings.margin_c, min_percent=float(settings.min_percent), max_percent=float(maximum),
        smoothing_s=settings.smoothing_s, kp=settings.pid_kp, ti=settings.pid_ti,
    )


def _refine_model(now: float, device: dict) -> bool:
    """全力で回転も温度も 2 分落ち着いていたら、そのときの熱抵抗を熱モデルへ少しだけ混ぜる。
    アイドル温度（外気温の基準）は fan_pid.update_idle が休むたびに測り直す。"""
    if not (_model.idle_c and _model.idle_power_w is not None
            and fan_pid.loaded(device["power_w"], device["power_cap_w"], _model.idle_power_w)
            and _tracker.power_steady(now, 120.0) and _tracker.stable(now, 120.0)):
        return False
    fans = [v for t, v in _tracker.fan if now - t <= 120]
    if not fans or max(fans) - min(fans) > 3:
        return False
    temps = _tracker.temp_means(now, 30.0)
    heat = (_tracker.mean(_tracker.power, now, 60.0) or 0.0) - _model.idle_power_w
    if heat <= 10:
        return False
    r = {sid: max(0.0, (temps[sid] - _model.idle_c[sid]) / heat) for sid in temps if sid in _model.idle_c}
    _model.add_point(sum(fans) / len(fans), heat, r, weight=0.2)
    return True


def _failure(exc: Exception, bdf: str | None) -> None:
    global _state, _error, _active_bdf, _last_curve, _sensor_ids, _pending_reset_bdf, _last_tick_at
    logger.error("GPUファン制御を停止します: %s", exc)
    reset_error = ""
    held = False
    if bdf:
        try:
            held = not _release(bdf)
        except FanControlError as reset_exc:
            reset_error = f" / 自動復帰失敗: {reset_exc}"
            logger.exception("GPU自動ファン制御への復帰に失敗")
    try:
        current = _load()
        if current.enabled:
            _persist(current.model_copy(update={"enabled": False}))
    except FanControlError:
        logger.exception("GPUファン設定無効化の永続化に失敗")
    _state, _error = "fault", str(exc) + reset_error + (f" / {HOLD_NOTICE}" if held else "")
    _pending_reset_bdf = bdf if reset_error else None
    _active_bdf, _last_curve, _sensor_ids, _last_tick_at = None, None, set(), None


def _retry_reset(now: float) -> None:
    global _pending_reset_bdf, _last_reset_retry, _reset_retry_failures, _error
    if not (_pending_reset_bdf and now - _last_reset_retry >= 10 and _reset_retry_failures < MAX_RESET_RETRIES):
        return
    _last_reset_retry = now
    try:
        _helper("reset", _pending_reset_bdf)
        logger.warning("GPU標準ファン制御への復帰リトライに成功しました")
        _pending_reset_bdf = None
        _reset_retry_failures = 0
        _error += " / 標準自動制御への復帰済み"
    except FanControlError:
        _reset_retry_failures += 1
        if _reset_retry_failures >= MAX_RESET_RETRIES:
            logger.error("GPU標準ファン制御へ戻せないため自動リトライを止めます: %s", _pending_reset_bdf)
            if REBOOT_HINT not in _error:
                _error = f"{_error} / {REBOOT_HINT}" if _error else REBOOT_HINT


def tick() -> None:
    global _active_bdf, _last_curve, _applied_percent, _state, _error, _last_sample
    global _sensor_ids, _zero_rpm_checks, _last_tick_at, _model, _model_bdf, _tracker, _last_model_save
    global _saved_integral, _last_log_prune
    global _last_passive
    from app.monitoring import fan_tune

    with _lock:
        try:
            settings = _load()
        except Exception as exc:  # 保存した設定が壊れていても安全側へ
            _failure(exc, _active_bdf)
            return
        now = time.monotonic()
        if _last_tick_at is not None and (now < _last_tick_at or now - _last_tick_at > 4 * INTERVAL):
            # 一時停止・スリープの後は平滑の途中の値が古い。今の回転数から始め直す
            _controller.reset(float(_applied_percent) if _applied_percent else None)
            logger.warning("GPU温度の継続観測が途絶えたため制御の内部状態を初期化")
        _last_tick_at = now
        _retry_reset(now)
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
            if _model_bdf != bdf:
                _model, _model_bdf = load_model(bdf), bdf
                _saved_integral = _model.integral_percent
                _tracker = fan_pid.SteadyTracker()
                _controller.reset()
            maximum = device["_range"][3]
            core = core_settings(settings, sensors, maximum)
            _tracker.add(now, sensors, device["power_w"], _applied_percent or settings.min_percent)
            if fan_pid.update_idle(_model, _tracker, now, device["power_w"], device["power_cap_w"]) \
                    and now - _last_model_save >= 300:
                save_model(bdf, _model)
                _last_model_save = now
            step = _controller.step(sensors, device["power_w"], INTERVAL, core, _model)
            if abs(_model.integral_percent - _saved_integral) >= 1.0 and now - _last_model_save >= 300:
                save_model(bdf, _model)
                _saved_integral, _last_model_save = _model.integral_percent, now
            # 自動調整の最中は、調整役が回転数を決める（安全の判定は step と同じく生の温度）
            held = fan_tune.hold_percent(now, device, sensors, core, _model, _tracker, step)
            wanted = step.percent if held is None or step.emergency else held
            if held is None and now - _last_passive >= PASSIVE_SECONDS and _refine_model(now, device):
                save_model(bdf, _model)
                _last_passive = _last_model_save = now
            percent = int(math.ceil(min(maximum, max(settings.min_percent, wanted)) - 1e-9))
            hotspot = next((s for s in sensors if s["kind"] == "junction"), max(sensors, key=lambda s: s["value_c"]))
            curve = fan_pid.ceiling_curve(device["_range"], percent, float(hotspot["limit_c"]))
            hot = max(item["value_c"] for item in sensors)
            rpm = device["fan_rpm"]
            _zero_rpm_checks = _zero_rpm_checks + 1 if rpm == 0 and hot >= 60 and percent >= 50 else 0
            if _zero_rpm_checks >= 2:
                raise FanControlError("高温時にファンRPMが0のままです。制御を停止しました")
            if curve != _last_curve:
                _helper("apply", bdf, curve)
                _last_curve = curve
            _applied_percent = percent
            _active_bdf, _sensor_ids = bdf, ids
            _state, _error = "controlling", ""
            _last_sample = {
                "bdf": bdf, "sensors": sensors, "fan_rpm": rpm, "fan_percent": percent,
                "power_w": device["power_w"], "control": step.as_dict(),
                "governing_sensor": step.governing, "observed_at": time.time(), "curve": _last_curve,
            }
            by_kind = {s["kind"]: s for s in sensors}
            _history.append({
                "t": round(time.time(), 1),
                "temps": {k: v["value_c"] for k, v in by_kind.items()},
                "smoothed_temps": {k: round(step.pv_c.get(v["id"], v["value_c"]), 1) for k, v in by_kind.items()},
                "limits": {k: v["limit_c"] for k, v in by_kind.items()},
                "targets": {k: v["target_c"] for k, v in by_kind.items()},
                "fan_percent": percent, "rpm": rpm, "power_w": device["power_w"],
                "feedforward_percent": round(step.feedforward, 1),
                "emergency": step.emergency, "tuning": held is not None,
            })
            # 診断用の記録（1 分ごとのまとめ、直近 2 日）。逸脱の検知と、記録からの作り直しに使う
            fan_log.add(time.time(), temps={k: v["value_c"] for k, v in by_kind.items()},
                        targets={k: v["target_c"] for k, v in by_kind.items()}, power_w=device["power_w"],
                        rpm=rpm, percent=percent, ff=step.feedforward, integral=step.i_term,
                        error_c=step.error_c, emergency=step.emergency, tuning=held is not None)
            if now - _last_log_prune >= 3600:
                fan_log.prune()
                _last_log_prune = now
        except Exception as exc:  # noqa: BLE001 - sysfs や想定外の失敗でも安全側へ
            _failure(exc, bdf)


def configure(settings: FanSettings) -> dict:
    global _active_bdf, _last_curve, _sensor_ids, _state, _error, _zero_rpm_checks, _pending_reset_bdf
    global _last_tick_at, _reset_retry_failures, _applied_percent
    with _lock:
        prior = _load()
        if settings.enabled:
            if _pending_reset_bdf is not None or _state == "fault" and prior.enabled:
                raise FanControlError("自動制御への復帰待ちです。復帰してから再有効化してください")
            device = _select(_devices(), settings.bdf)
            sensors = _validate(settings, device)
            # 上限温度を画面で決めていないセンサーは既定値で保存する（後から既定が変わっても動かない）
            settings = settings.model_copy(update={
                "bdf": device["bdf"], "limits_c": {s["id"]: int(s["limit_c"]) for s in sensors}})
            previous_bdf = _active_bdf or prior.bdf
            if prior.enabled and previous_bdf and previous_bdf != device["bdf"]:
                _release(previous_bdf)
                _active_bdf = None
                _last_curve = None
            _pending_reset_bdf = None
            _state, _error = "disabled", ""
            _persist(settings)
            _sensor_ids, _zero_rpm_checks = set(), 0
            # 方式を切り替えても回転数は今の値から続ける（段差を作らない）
            _controller.reset(float(_applied_percent) if prior.enabled and _applied_percent else None)
            _last_tick_at = None
            tick()
            if _state == "fault":
                raise FanControlError(_error)
        else:
            targets = {item for item in (_pending_reset_bdf, _active_bdf, prior.bdf if prior.enabled else None)
                       if item}
            held = False
            for target in sorted(targets):
                try:
                    held = (not _release(target)) or held
                except FanControlError as exc:
                    _pending_reset_bdf = target
                    _state, _error = "fault", f"自動制御へ復帰できません: {exc} / {REBOOT_HINT}"
                    raise FanControlError(_error) from exc
            _persist(settings)
            _active_bdf, _last_curve, _sensor_ids = None, None, set()
            _state, _error, _pending_reset_bdf = "disabled", HOLD_NOTICE if held else "", None
            _reset_retry_failures = 0
            _last_tick_at = None
            _applied_percent = 0
        return status()


def status() -> dict:
    from app.monitoring import fan_tune

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
        model = _model if _model_bdf else (load_model(settings.bdf) if settings.bdf else None)
        return {
            "settings": settings.model_dump(), "devices": devices,
            "helper_installed": HELPER.is_file(),
            "state": _state if settings.enabled or _state == "fault" else "disabled",
            "error": _error, "pending_auto_restore": _pending_reset_bdf is not None,
            "active": _last_sample if settings.enabled else None,
            "poll_interval_seconds": INTERVAL,
            "model": {
                "usable": model.usable, "idle_c": model.idle_c, "idle_power_w": model.idle_power_w,
                "tau_s": model.tau_s, "points": len(model.points),
                "hotspot_jump_c_per_w": model.hotspot_jump_c_per_w,
                "integral_percent": round(model.integral_percent, 1),
            } if model is not None else None,
            "autotune": fan_tune.snapshot(),
            "warning": "上限温度は保証値ではありません。CPU・マザーボードのファンは制御しません。",
        }


def history(since: float = 0.0) -> dict:
    """毎回記録する（軽い）。画面は表示中だけ取りに来る。"""
    with _lock:
        return {"interval_seconds": INTERVAL, "samples": [item for item in _history if item["t"] > since]}


BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
FIRMWARE_STARTUP_NOTICE = "PC起動時はGPU標準の自動制御を使う設定のため、ファン制御を無効にして起動しました"


def _boot_marker() -> Path:
    return data_dir() / "amd-fan-control.boot"


def startup() -> None:
    """PC 起動後に初めて ControlDeck が動いたときだけ startup_mode に従う。同じ起動中の再起動は制御を続ける。"""
    global _state, _error
    with _lock:
        try:
            boot_id = BOOT_ID_PATH.read_text(encoding="ascii").strip()
        except OSError:
            return
        marker = _boot_marker()
        try:
            previous = marker.read_text(encoding="ascii").strip()
        except OSError:
            previous = ""
        try:
            marker.write_text(boot_id, encoding="ascii")
        except OSError:
            logger.warning("ファン制御の起動記録を保存できません")
        if previous == boot_id:
            return
        try:
            settings = _load()
        except FanControlError:
            return
        if settings.enabled and settings.startup_mode == "firmware":
            _persist(settings.model_copy(update={"enabled": False}))
            _state, _error = "disabled", FIRMWARE_STARTUP_NOTICE
            logger.info(FIRMWARE_STARTUP_NOTICE)


async def run() -> None:
    await asyncio.to_thread(startup)
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001 - 監視をウェブサーバーから切り離す
            logger.exception("GPU fan controller tick failed")
        await asyncio.sleep(INTERVAL)


def shutdown() -> None:
    """正常終了では、戻せる GPU は標準の自動制御へ戻す。"""
    from app.monitoring import fan_tune

    fan_tune.stop_load()
    with _lock:
        settings = _load()
        target = _active_bdf or (settings.bdf if settings.enabled else None)
        if target:
            try:
                _release(target)
            except FanControlError:
                logger.exception("停止時にファン自動制御へ復帰できませんでした")
