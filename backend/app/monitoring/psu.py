"""Corsair PSU（corsair-psu カーネルドライバー）の hwmon 読み取り。

/sys/class/hwmon を Python から直接読む（sensors/liquidctl のサブプロセスは使わない）。
hwmon 番号は再起動やデバイス構成で変わるため、毎回 name=corsairpsu を探索する。

読み取り値は PSU の DC 出力電力（power total）。AC 入力電力の概算は呼び出し側で
効率を使って別途計算する（v_in / liquidctl の推定入力電力は使わない）。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("control_deck.psu")

HWMON_ROOT = Path("/sys/class/hwmon")
DEVICE_NAME = "corsairpsu"
# power total と判定するラベル（正規化して比較）
TOTAL_LABELS = {"powertotal", "total", "totalpower"}
# 異常値ガード（HX1500i は 1500W 定格。センサー誤差込みで十分な余裕）
MAX_POWER_W = 3000.0

# 直近の検出状態（ログのレート制限用）。True=検出, False=未検出, None=未判定
_last_available: bool | None = None


def _norm(label: str) -> str:
    return "".join(label.lower().split())


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _find_device() -> Path | None:
    """name の内容が corsairpsu の hwmon ディレクトリを探す。"""
    try:
        candidates = sorted(HWMON_ROOT.glob("hwmon*"))
    except OSError:
        return None
    for hw in candidates:
        try:
            if hw.joinpath("name").read_text().strip() == DEVICE_NAME:
                return hw
        except OSError:
            continue
    return None


def _labelled(hw: Path, prefix: str) -> dict[str, Path]:
    """{正規化ラベル: 対応する *_input パス} を返す（prefix 例: power/in/curr/temp/fan）。"""
    out: dict[str, Path] = {}
    for label_file in hw.glob(f"{prefix}*_label"):
        input_file = label_file.with_name(label_file.name.replace("_label", "_input"))
        if not input_file.exists():
            continue
        try:
            out[_norm(label_file.read_text())] = input_file
        except OSError:
            continue
    return out


def _resolve_total_power(hw: Path) -> Path | None:
    """power total の *_input パスを解決する。ラベル欠落時は power1_input へフォールバック。"""
    powers = _labelled(hw, "power")
    for label, path in powers.items():
        if label in TOTAL_LABELS:
            return path
    # ラベルが無いカーネル向けの安全なフォールバック（根拠なく最初の power は採らない）
    fallback = hw / "power1_input"
    return fallback if fallback.exists() else None


def read_corsair_psu() -> dict:
    """Corsair PSU の現在値を返す。未接続・読み取り不可時は available=False。"""
    global _last_available
    hw = _find_device()
    if hw is None:
        if _last_available is not False:
            logger.info("Corsair PSU hwmon not available")
        _last_available = False
        return {"available": False, "source": None, "output_power_w": None}

    total_path = _resolve_total_power(hw)
    raw = _read_int(total_path) if total_path else None
    if raw is None:
        if _last_available is not False:
            logger.info("Corsair PSU hwmon read failed at %s", hw)
        _last_available = False
        return {"available": False, "source": None, "output_power_w": None}

    output_w = raw / 1_000_000  # µW → W
    if not (0.0 <= output_w <= MAX_POWER_W):
        # 異常値（負数・極端値）は測定不能扱い
        _last_available = False
        return {"available": False, "source": None, "output_power_w": None}

    if _last_available is not True:
        logger.info("Corsair PSU hwmon detected at %s (power total=%.1fW)", hw, output_w)
    _last_available = True

    result: dict = {
        "available": True,
        "source": "corsair_psu_hwmon",
        "output_power_w": round(output_w, 2),
    }

    # 付随情報（取れる範囲で。失敗は無視）
    powers = _labelled(hw, "power")
    for label, key in (("power+12v", "rail_12v_power_w"), ("power+5v", "rail_5v_power_w"),
                       ("power+3.3v", "rail_3v3_power_w")):
        v = _read_int(powers[label]) if label in powers else None
        result[key] = round(v / 1_000_000, 2) if v is not None else None
    temps = _labelled(hw, "temp")
    result["vrm_temperature_c"] = _milli(temps.get("vrmtemp"))
    result["case_temperature_c"] = _milli(temps.get("casetemp"))
    fans = _labelled(hw, "fan")
    result["fan_rpm"] = _read_int(fans["psufan"]) if "psufan" in fans else None
    return result


def _milli(path: Path | None) -> float | None:
    """temp*_input は m°C。°C へ変換。"""
    if path is None:
        return None
    v = _read_int(path)
    return round(v / 1000, 1) if v is not None else None
