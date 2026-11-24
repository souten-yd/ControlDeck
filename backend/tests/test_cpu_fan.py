"""CPU ファン（マザーボードの Super I/O の hwmon）の読み取りの試験。

目的: nct67xx などのドライバがあれば回転数と PWM(%) を読み、CPU ファンの選び方（ラベル優先・無ければ選んだ番号）、
  回転数 0 の扱い、読めないときの無言、機能と拡張の案内コマンドを確かめる。読み取りだけ（書き込みはしない）。
根拠: 2026-11-24 利用者「CPU ファンの回転数とか PWM の変調率とか見えたりしますか？見えるなら CPU のところに表示を追加して」。
  hwmon は tmp の偽物で、実機の /sys には触れない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.features import system_packages
from app.monitoring import cpu_fan


@pytest.fixture
def root(tmp_path, monkeypatch):
    hw = tmp_path / "hwmon"
    hw.mkdir()
    monkeypatch.setattr(cpu_fan, "HWMON_ROOT", hw)
    monkeypatch.setattr(cpu_fan, "_settings_path", lambda: tmp_path / "cpu-fan.json")
    monkeypatch.setattr(cpu_fan, "_from_sensors", lambda: (None, None))
    return hw


def chip(root: Path, name: str, chip_name: str, fans: dict[int, tuple[int, int | None]], labels: dict[int, str] | None = None):
    d = root / name
    d.mkdir()
    (d / "name").write_text(chip_name)
    for index, (rpm, pwm) in fans.items():
        (d / f"fan{index}_input").write_text(str(rpm))
        if pwm is not None:
            (d / f"pwm{index}").write_text(str(pwm))
    for index, label in (labels or {}).items():
        (d / f"fan{index}_label").write_text(label)
    return d


def test_no_super_io_reads_nothing(root):
    chip(root, "hwmon0", "k10temp", {})
    assert cpu_fan.read_cpu_fan() == (None, None)
    assert cpu_fan.list_fans() == []


def test_default_fan1_without_label(root):
    chip(root, "hwmon1", "nct6799", {1: (1200, 115), 2: (900, 90)})
    assert cpu_fan.read_cpu_fan() == (1200, 45)


def test_label_wins_over_configured_index(root):
    chip(root, "hwmon1", "nct6799", {1: (1200, 115), 2: (900, 255)}, {2: "CPU_FAN1"})
    cpu_fan.save_index(1)
    assert cpu_fan.read_cpu_fan() == (900, 100)


def test_configured_index_is_used(root):
    chip(root, "hwmon1", "nct6799", {1: (1200, 115), 3: (700, 51)})
    cpu_fan.save_index(3)
    assert cpu_fan.read_cpu_fan() == (700, 20)


def test_zero_rpm_is_fact_but_not_a_candidate(root):
    chip(root, "hwmon1", "nct6799", {1: (0, 0), 2: (800, None)})
    assert cpu_fan.read_cpu_fan() == (0, 0)
    assert [f["index"] for f in cpu_fan.list_fans()] == [2]
    assert cpu_fan.list_fans()[0]["pwm_percent"] is None


def test_selected_fan_missing_or_unreadable(root):
    d = chip(root, "hwmon1", "nct6799", {1: (1200, 115)})
    cpu_fan.save_index(5)
    assert cpu_fan.read_cpu_fan() == (None, None)
    cpu_fan.save_index(1)
    (d / "fan1_input").write_text("not a number")
    assert cpu_fan.read_cpu_fan() == (None, 45)


def test_missing_hwmon_root_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(cpu_fan, "HWMON_ROOT", tmp_path / "none")
    monkeypatch.setattr(cpu_fan, "_settings_path", lambda: tmp_path / "cpu-fan.json")
    monkeypatch.setattr(cpu_fan, "_from_sensors", lambda: (None, None))
    assert cpu_fan.read_cpu_fan() == (None, None)


def test_save_index_validates_and_broken_file_falls_back(root, tmp_path):
    for bad in (0, 8, True, "1", None):
        with pytest.raises(ValueError):
            cpu_fan.save_index(bad)
    (tmp_path / "cpu-fan.json").write_text("{broken")
    assert cpu_fan.configured_index() == 1


def test_driver_guide_command(root, monkeypatch):
    assert "sudo modprobe nct6775" in system_packages.install_command("system-nct6775")
    assert "/etc/modules-load.d/nct6775.conf" in system_packages.install_command("system-nct6775")
    chip(root, "hwmon1", "nct6799", {1: (1, 1)})
    assert system_packages.missing_packages("system-nct6775") == []
    assert system_packages.install_command("system-nct6775") == ""
