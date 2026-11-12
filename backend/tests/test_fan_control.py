"""Hardware-free regression tests for AMD fan controller and privileged allowlist."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError


@pytest.fixture
def gpu(tmp_path, monkeypatch):
    from app.monitoring import fan_control as fan

    sys = tmp_path / "sys" / "devices"
    device = sys / "pci0000:00" / "0000:03:00.0"
    hwmon = device / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    drm = tmp_path / "drm" / "card0"
    drm.mkdir(parents=True)
    (drm / "device").symlink_to(device, target_is_directory=True)
    (device / "vendor").write_text("0x1002\n")
    (device / "mem_info_vram_total").write_text("33554432000\n")
    (hwmon / "name").write_text("amdgpu\n")
    for n, label, value, crit in [
        (1, "edge", 55000, 100000),
        (2, "junction", 70000, 110000),
        (3, "mem", 84000, 100000),
    ]:
        (hwmon / f"temp{n}_label").write_text(label)
        (hwmon / f"temp{n}_input").write_text(str(value))
        (hwmon / f"temp{n}_crit").write_text(str(crit))
    (hwmon / "fan1_input").write_text("1500")
    curve = device / "gpu_od" / "fan_ctrl" / "fan_curve"
    curve.parent.mkdir(parents=True)
    curve.write_text(
        "OD_FAN_CURVE:\n0: 25C 30%\n1: 45C 40%\n2: 60C 60%\n"
        "3: 75C 85%\n4: 95C 100%\nOD_RANGE:\n"
        "FAN_CURVE(hotspot temp): 20C 105C\nFAN_CURVE(fan speed): 20% 100%\n"
    )
    helper = tmp_path / "helper"
    helper.write_text("stub")
    monkeypatch.setattr(fan, "DRM_ROOT", drm.parent)
    monkeypatch.setattr(fan, "SYS_ROOT", sys)
    monkeypatch.setattr(fan, "HELPER", helper)
    monkeypatch.setattr(fan, "data_dir", lambda: tmp_path)
    for key, value in {
        "_active_bdf": None, "_last_curve": None, "_last_write": 0.0,
        "_floor": 35, "_sensor_ids": set(), "_zero_rpm_checks": 0,
        "_state": "disabled", "_error": "", "_last_sample": {},
    }.items():
        monkeypatch.setattr(fan, key, value)
    calls = []
    monkeypatch.setattr(fan, "_helper", lambda action, bdf, curve=None: calls.append((action, bdf, curve)))
    return fan, device, hwmon, curve, calls


def test_multisensor_prefers_vram_even_when_core_is_cool(gpu):
    fan, _, _, _, calls = gpu
    result = fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    assert result["state"] == "controlling"
    assert result["active"]["critical_sensor"] == "temp3"
    assert result["active"]["floor_percent"] == 100
    assert calls[0][0:2] == ("apply", "0000:03:00.0")
    assert calls[0][2].endswith("95:100")
    assert fan._load().enabled is True


def test_missing_sensor_stops_and_resets(gpu):
    fan, _, hwmon, _, calls = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    (hwmon / "temp3_input").unlink()
    fan.tick()
    assert calls[-1][0] == "reset"
    assert fan._load().enabled is False
    assert fan.status()["state"] == "fault"


def test_zero_rpm_under_heat_trips_fault(gpu):
    fan, _, hwmon, _, calls = gpu
    (hwmon / "fan1_input").write_text("0")
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    fan.tick()
    assert fan.status()["state"] == "fault"
    assert fan._load().enabled is False
    assert calls[-1][0] == "reset"


def test_disable_restores_firmware_auto(gpu):
    fan, _, _, _, calls = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    fan.configure(fan.FanSettings(enabled=False, bdf="0000:03:00.0"))
    assert calls[-1] == ("reset", "0000:03:00.0", None)
    assert fan.status()["state"] == "disabled"


def test_unusable_firmware_range_is_not_enabled(gpu):
    fan, _, _, curve, calls = gpu
    curve.write_text(
        "OD_FAN_CURVE:\n0: 0C 0%\n1: 0C 0%\n2: 0C 0%\n"
        "3: 0C 0%\n4: 0C 0%\nOD_RANGE:\n"
        "FAN_CURVE(hotspot temp): 0C 0C\nFAN_CURVE(fan speed): 0% 0%\n"
    )
    assert fan.status()["devices"][0]["curve_supported"] is False
    with pytest.raises(fan.FanControlError):
        fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    assert not calls


def test_targets_cannot_exceed_reported_critical_margin(gpu):
    fan, _, hwmon, _, calls = gpu
    (hwmon / "temp3_crit").write_text("90000")  # max target 85
    with pytest.raises(fan.FanControlError, match="目標値"):
        fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0",
                                      targets_c={"temp3": 90}))
    assert not calls


def test_settings_reject_untrusted_names_and_bdf():
    from app.monitoring.fan_control import FanSettings
    with pytest.raises(ValidationError):
        FanSettings(enabled=True, bdf="../../tmp")
    with pytest.raises(ValidationError):
        FanSettings(targets_c={"../../temp3": 75})
    with pytest.raises(ValidationError):
        FanSettings(min_percent=0)


def _helper(tmp_path):
    src = Path(__file__).parents[2] / "helper" / "control-deck-hw-helper.py"
    spec = importlib.util.spec_from_file_location("control_deck_fan_helper", src)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys = tmp_path / "sys/devices"
    device = sys / "pci0000:00" / "0000:03:00.0"
    device.mkdir(parents=True)
    pci = tmp_path / "sys/bus/pci/devices"
    pci.mkdir(parents=True)
    (pci / "0000:03:00.0").symlink_to(device, target_is_directory=True)
    (device / "vendor").write_text("0x1002\n")
    curve = device / "gpu_od/fan_ctrl/fan_curve"
    curve.parent.mkdir(parents=True)
    curve.write_text(
        "OD_FAN_CURVE:\n0: 25C 30%\n1: 45C 40%\n2: 60C 60%\n"
        "3: 75C 85%\n4: 95C 100%\nOD_RANGE:\n"
        "FAN_CURVE(hotspot temp): 20C 105C\nFAN_CURVE(fan speed): 20% 100%\n"
    )
    module.SYS_DEVICES = sys.resolve()
    module.PCI_ROOT = pci.resolve()
    return module, curve


def test_helper_rejects_unsafe_curve_and_bad_path(tmp_path):
    helper, curve = _helper(tmp_path)
    before = curve.read_text()
    with pytest.raises(SystemExit):
        helper.control_fan_curve("apply", "0000:03:00.0", "25:20,45:20,60:30,75:40,95:50")
    assert curve.read_text() == before
    with pytest.raises(SystemExit):
        helper.control_fan_curve("apply", "../../etc", "25:35,45:45,60:65,75:90,95:100")


def test_helper_commits_five_points_and_reset(tmp_path, monkeypatch):
    helper, curve = _helper(tmp_path)
    writes = []
    original = Path.write_text

    def recording(path, text, *args, **kwargs):
        if path == curve:
            writes.append(text)
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", recording)
    helper.control_fan_curve("apply", "0000:03:00.0", "25:35,45:45,60:65,75:90,95:100")
    assert writes == ["0 25 35", "1 45 45", "2 60 65", "3 75 90", "4 95 100", "c"]
    helper.control_fan_curve("reset", "0000:03:00.0")
    assert writes[-2:] == ["r", "c"]


def test_explicit_restore_retries_pending_auto_failure(gpu, monkeypatch):
    fan, _, hwmon, _, calls = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    (hwmon / "temp3_input").unlink()
    failed = [True]

    def flaky(action, bdf, curve=None):
        calls.append((action, bdf, curve))
        if action == "reset" and failed[0]:
            failed[0] = False
            raise fan.FanControlError("simulated transient reset failure")

    monkeypatch.setattr(fan, "_helper", flaky)
    fan.tick()
    assert fan.status()["state"] == "fault"
    assert fan.status()["pending_auto_restore"] is True
    assert fan._load().enabled is False
    # This must retry the reset, even though enabled=False was persisted at fault.
    restored = fan.configure(fan.FanSettings(enabled=False, bdf="0000:03:00.0"))
    assert calls[-1][0] == "reset"
    assert restored["pending_auto_restore"] is False
    assert restored["state"] == "disabled"


def test_unexpected_temperature_read_error_fails_closed(gpu, monkeypatch):
    fan, _, _, _, calls = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    monkeypatch.setattr(fan, "_devices", lambda: (_ for _ in ()).throw(RuntimeError("hwmon issue")))
    fan.tick()
    assert fan._load().enabled is False
    assert fan._state == "fault"
    assert calls[-1][0] == "reset"
