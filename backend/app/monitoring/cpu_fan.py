"""CPU ファンの回転数と指令値を、名前で確認できるものだけ読む。

マザーボードのファンは Super-I/O チップ（Nuvoton NCT67xx、ITE IT87 など）が
持っている。そのドライバが入っていない環境では hwmon に何も出ないので、
CPU ファンは本当に読めない。読めないときは黙って N/A にする。

読めるときも、どれが CPU ファンかは番号だけでは決まらない。fan1 が CPU の
ことが多い、程度の慣習でしかなく、当てにすると筐体ファンや水冷ポンプの
回転数を CPU ファンとして出してしまう。名前が CPU だと言っているものだけを
採用する:

* hwmon の ``fanN_label``（ドライバまたは sensors 設定が付ける）
* ``/etc/sensors.d`` 等の設定を反映した ``sensors -j`` の名前

どちらも CPU と言わないなら、番号から推測せず読めないものとして扱う。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HWMON_ROOT = Path("/sys/class/hwmon")
# hwmon が名乗るのはドライバ名ではなくチップ名である（nct6775 ドライバは
# "nct6799" と名乗る）。ドライバ名と突き合わせると実機で一致しないので、
# 系統の接頭辞で見る。ここに無いチップの fan を CPU ファンとは呼ばない。
SUPER_IO_CHIP_PREFIXES = ("nct6", "nct7", "it87", "it86", "f718", "w836", "w837")
CPU_FAN_TOKENS = ("cpu_fan", "cpufan", "cpu fan", "cpu_opt", "cpuopt", "cpu-fan")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None


def _is_super_io(name: str) -> bool:
    return any(name.casefold().startswith(prefix) for prefix in SUPER_IO_CHIP_PREFIXES)


def _is_cpu_fan(label: str) -> bool:
    folded = label.casefold().replace("-", "_").replace(" ", "_")
    return any(token.replace("-", "_").replace(" ", "_") in folded for token in CPU_FAN_TOKENS)


def _from_hwmon() -> tuple[int | None, int | None]:
    """ドライバが自分で名前を付けている場合。"""
    try:
        directories = sorted(HWMON_ROOT.iterdir())
    except OSError:
        return None, None
    for directory in directories:
        name = _read(directory / "name") or ""
        if not _is_super_io(name):
            continue
        for index in range(1, 9):
            label = _read(directory / f"fan{index}_label")
            if not label or not _is_cpu_fan(label):
                continue
            rpm = _read(directory / f"fan{index}_input")
            duty = _read(directory / f"pwm{index}")
            return (
                int(rpm) if rpm and rpm.isdigit() else None,
                round(int(duty) / 255 * 100) if duty and duty.isdigit() else None,
            )
    return None, None


def _from_sensors() -> tuple[int | None, int | None]:
    """`sensors -j` は /etc/sensors.d の命名を反映する。

    ドライバが番号しか出さない板でも、配布や利用者の設定が「CPU Fan」と
    名付けていればここで拾える。設定が無ければ何も返さない。
    """
    try:
        output = subprocess.run(
            ["sensors", "-j"], capture_output=True, text=True, timeout=5
        ).stdout
        document = json.loads(output)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None
    if not isinstance(document, dict):
        return None, None
    for chip, readings in document.items():
        if not isinstance(readings, dict):
            continue
        if not _is_super_io(str(chip)):
            continue
        for label, values in readings.items():
            if not isinstance(values, dict) or not _is_cpu_fan(str(label)):
                continue
            for key, value in values.items():
                if key.endswith("_input") and isinstance(value, (int, float)):
                    return int(value), None
    return None, None


def read_cpu_fan() -> tuple[int | None, int | None]:
    """(rpm, 指令値 %) を返す。名前で CPU だと分かるものが無ければ (None, None)。

    0 rpm と「読めない」は違う。前者はファンが止まっているという事実で、
    後者は何も言えないという意味なので、推測で埋めない。
    """
    rpm, duty = _from_hwmon()
    if rpm is not None or duty is not None:
        return rpm, duty
    return _from_sensors()
