"""R9700 のコア周波数・電圧オフセット（amdgpu の OverDrive）。

2026-11-18 利用者「電力制限 210 W は有効のまま、ホットスポット温度を下げてファンの回転数を下げたい」。
ドライバが定義する最大周波数・電圧に対する「オフセット」だけを扱う。絶対値には変換しない。
書き込みは固定の helper（gpu-od）だけ。電力・VRAM・ファンの設定には触らず、起動時に自動で適用もしない。
全体のリセット（`r`）は使わない（SMU 14 では以後の OD 書き込みが再起動まで失敗する）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from app.config import data_dir

PCI_ROOT = Path("/sys/bus/pci/devices")
HELPER = Path("/usr/local/libexec/control-deck-hw-helper")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
STATE_FILE = "gpu-tuning.json"
TARGET_DEVICES = {"0x7551"}          # Radeon AI PRO R9700（Navi 48）
XFX_SUBSYSTEM = ("0x1eae", "0x9801")
ITEMS = {"core": ("OD_SCLK_OFFSET", "SCLK_OFFSET", "MHz"), "voltage": ("OD_VDDGFX_OFFSET", "VDDGFX_OFFSET", "mV")}
PRESET = {"id": "xfx-r9700-test", "label": "XFX R9700 試験設定（−500 MHz / −25 mV）", "core": -500, "voltage": -25}
_sudo_cache: tuple[float, bool] = (0.0, False)
_write_lock = threading.Lock()       # 読む・書く・確かめるを 1 組にする
logger = logging.getLogger(__name__)


class GpuTuningError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_od(text: str) -> dict:
    """pp_od_clk_voltage の読み取り。項目ごとに、現在値と範囲が揃っていれば対応とする。"""
    items = {}
    for key, (section, range_name, unit) in ITEMS.items():
        current = re.search(rf"^{section}:\s*\n\s*(-?\d+)\s*m[hv]", text, re.M | re.I)
        limits = re.search(rf"^{range_name}:\s*(-?\d+)\s*m[hv]\w*\s+(-?\d+)\s*m[hv]", text, re.M | re.I)
        items[key] = {"supported": bool(current and limits), "unit": unit,
                      "current": int(current[1]) if current else None,
                      "min": int(limits[1]) if limits else None, "max": int(limits[2]) if limits else None}
    return items


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _read_od(node: Path) -> str:
    """OverDrive の読み取り。無い（未対応）と読めない（一時的な失敗）を分ける。"""
    try:
        return node.read_text(encoding="ascii")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        raise GpuTuningError("read", "GPU の設定を読み取れませんでした。少し待ってからもう一度お試しください") from exc


def find_target() -> Path | None:
    """対象の R9700 だけを PCI アドレスで選ぶ（内蔵 GPU や他の GPU は選ばない）。"""
    for device in sorted(PCI_ROOT.glob("*")):
        if _read(device / "vendor").lower() == "0x1002" and _read(device / "device").lower() in TARGET_DEVICES \
                and _read(device / "class").lower().startswith("0x03"):
            return device
    return None


def _number(path: Path, scale: float = 1.0) -> float | None:
    try:
        return round(float(_read(path)) / scale, 1)
    except ValueError:
        return None


def _live(device: Path) -> dict:
    """いまの実測（設定値とは別物）。"""
    hwmon = next(iter(sorted(device.glob("hwmon/hwmon*"))), None)
    if hwmon is None:
        return {}
    temps = {_read(p): p.with_name(p.name.replace("_label", "_input")) for p in hwmon.glob("temp*_label")}
    power = _number(hwmon / "power1_average", 1e6)
    return {"core_mhz": _number(hwmon / "freq1_input", 1e6), "power_w": power if power is not None else _number(hwmon / "power1_input", 1e6),
            "power_limit_w": _number(hwmon / "power1_cap", 1e6), "hotspot_c": _number(temps["junction"], 1000) if "junction" in temps else None,
            "fan_rpm": _number(hwmon / "fan1_input"), "busy_percent": _number(device / "gpu_busy_percent")}


def _boot_id() -> str:
    return _read(BOOT_ID)


def _load_previous() -> dict:
    try:
        saved = json.loads((data_dir() / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # 再起動するとドライバの値は 0 に戻るので、前の起動の記録は使わない
    return saved.get("previous") or {} if saved.get("boot_id") == _boot_id() else {}


def _save_previous(previous: dict) -> None:
    path = data_dir() / STATE_FILE
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"boot_id": _boot_id(), "previous": previous}), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:      # GPU には適用済み。適用の結果は変えない
        logger.warning("GPU 設定の復元用の値を保存できませんでした: %s", exc)


def helper_ready() -> bool:
    """helper が gpu-od を知っていて、sudo で許可されているか（要 ./deck.sh service）。"""
    global _sudo_cache
    now = time.monotonic()
    if now - _sudo_cache[0] < 30:
        return _sudo_cache[1]
    try:
        known = "gpu-od" in HELPER.read_text(encoding="utf-8", errors="replace")
        # 「sudo -l コマンド」はパスワードが要る許可でも成功するので、NOPASSWD の行を見る
        listed = subprocess.run(["sudo", "-n", "-l"], capture_output=True, text=True, timeout=5, check=False,
                                env={**os.environ, "LC_ALL": "C"}).stdout if known else ""
        allowed = any("NOPASSWD" in line and f"{HELPER} gpu-od" in line for line in listed.splitlines())
    except (OSError, subprocess.SubprocessError):
        allowed = False
    _sudo_cache = (now, allowed)
    return allowed


def status() -> dict:
    device = find_target()
    if device is None:
        return {"available": False, "message": "対象の Radeon AI PRO R9700 が見つかりません", "preset": PRESET}
    node = device / "pp_od_clk_voltage"
    try:
        text = _read_od(node)
    except GpuTuningError as exc:
        return {"available": False, "message": exc.message, "bdf": device.name, "preset": PRESET}
    items = parse_od(text)
    subsystem = (_read(device / "subsystem_vendor").lower(), _read(device / "subsystem_device").lower())
    message = "" if text else "ドライバが OverDrive を公開していません（amdgpu.ppfeaturemask を確認してください）"
    return {"available": bool(text), "message": message, "bdf": device.name, "xfx": subsystem == XFX_SUBSYSTEM,
            "items": items, "previous": _load_previous(), "helper_ready": helper_ready(), "preset": PRESET, "live": _live(device)}


def _run_helper(bdf: str, core: int | None, voltage: int | None) -> None:
    argv = ["sudo", "-n", str(HELPER), "gpu-od", "apply", bdf,
            "keep" if core is None else str(core), "keep" if voltage is None else str(voltage)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False,
                                env={**os.environ, "LC_ALL": "C"})   # sudo の文言で権限不足を見分ける
    except (OSError, subprocess.SubprocessError) as exc:
        raise GpuTuningError("failed", f"書き込みを実行できませんでした: {type(exc).__name__}") from exc
    if result.returncode == 0:
        return
    error = result.stderr.strip()
    if "password" in error.lower() or "not allowed" in error.lower():
        raise GpuTuningError("permission", "権限がありません。サーバーで ./deck.sh service を実行して helper を更新してください")
    try:
        detail = json.loads(error.splitlines()[-1])
    except (ValueError, IndexError):
        detail = {}
    raise GpuTuningError(str(detail.get("code") or "failed"), str(detail.get("error") or "GPU への書き込みに失敗しました"))


def _checked(values: dict, items: dict) -> None:
    for key, value in values.items():
        item = items[key]
        label = "コア周波数" if key == "core" else "コア電圧"
        if not item["supported"]:
            raise GpuTuningError("unsupported", f"この環境のドライバは{label}オフセットに対応していません")
        if not item["min"] <= value <= item["max"]:
            raise GpuTuningError("range", f"{label}オフセットは {item['min']}〜{item['max']} {item['unit']} の範囲で指定してください")


def _apply(values: dict) -> tuple[dict, dict]:
    device = find_target()
    if device is None:
        raise GpuTuningError("unsupported", "対象の Radeon AI PRO R9700 が見つかりません")
    node = device / "pp_od_clk_voltage"
    before = parse_od(_read_od(node))
    _checked(values, before)
    changed = {key: value for key, value in values.items() if before[key]["current"] != value}
    if not changed:
        return before, {}
    try:
        _run_helper(device.name, changed.get("core"), changed.get("voltage"))
    except GpuTuningError as exc:
        try:
            after = parse_od(_read_od(node))
            states = "、".join(f"{ITEMS[k][1]} {after[k]['current']} {after[k]['unit']}" for k in after if after[k]["supported"])
        except GpuTuningError:
            states = "読み取れません"
        raise GpuTuningError(exc.code, f"{exc.message}（最後に確認できた値: {states}）") from exc
    after = parse_od(_read_od(node))
    wrong = [key for key, value in changed.items() if after[key]["current"] != value]
    if wrong:   # helper は成功と言ったのに読み戻しが合わない
        back = {key: before[key]["current"] for key in changed}
        try:
            _run_helper(device.name, back.get("core"), back.get("voltage"))
            now = parse_od(_read_od(node))
            restored = all(now[key]["current"] == value for key, value in back.items())
        except GpuTuningError:
            restored = False
        raise GpuTuningError("verify", "読み戻した値が指定と違うため、適用前の値へ戻しました" if restored
                             else "読み戻した値が指定と違い、適用前の値へ戻せたことも確認できませんでした。GPU の状態を確認してください")
    return before, changed


def apply(core: int | None, voltage: int | None) -> dict:
    values = {key: value for key, value in (("core", core), ("voltage", voltage)) if value is not None}
    if not values:
        raise GpuTuningError("range", "変更する値を指定してください")
    with _write_lock:
        before, changed = _apply(values)
        if changed:
            # 戻す先は、この起動で最初に変える前の値（段階的に試しても元へ戻れるように）
            previous = _load_previous()
            for key in changed:
                previous.setdefault(key, before[key]["current"])
            _save_previous({key: value for key, value in previous.items() if parse_od(_read_od(find_target() / "pp_od_clk_voltage"))[key]["current"] != value})
    return status()


def restore() -> dict:
    """変更した項目だけを、最初の値へ戻す（全体のリセットは使わない）。"""
    with _write_lock:
        previous = _load_previous()
        if not previous:
            raise GpuTuningError("nothing", "戻せる変更はありません")
        _apply({key: int(value) for key, value in previous.items()})
        _save_previous({})
    return status()
