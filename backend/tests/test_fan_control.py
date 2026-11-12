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
    (hwmon / "power1_average").write_text("12000000")
    (hwmon / "power1_cap").write_text("300000000")
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
        "_policy": __import__("app.monitoring.fan_strategy", fromlist=["AdaptivePolicy"]).AdaptivePolicy(),
        "_pending_reset_bdf": None, "_last_reset_retry": 0.0,
        "_last_boost_at": 0.0, "_cool_since": None, "_last_cool_step": 0.0,
        "_temp_history": {}, "_zero_stable_since": None,
        "_zero_active": False, "_zero_initialized_bdf": None,
        "_last_rise_c_per_min": 0.0, "_applied_floor": 0,
        "_last_tick_at": None,
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



def test_llm_bursts_do_not_chatter_on_cooling(gpu, monkeypatch):
    fan, _, hwmon, _, calls = gpu
    clock = [100.0]
    monkeypatch.setattr(fan.time, "monotonic", lambda: clock[0])
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    assert fan._floor == 100

    # LLM goes idle with all temperatures cool: hold the fan, not the load.
    for sensor_id, temp in (("temp1", 40), ("temp2", 46), ("temp3", 40)):
        (hwmon / f"{sensor_id}_input").write_text(str(temp * 1000))
    for _ in range(60):
        clock[0] += 3
        fan.tick()
    assert fan._floor == 100  # 177 seconds of observed cooldown
    clock[0] += 3
    fan.tick()
    assert fan._floor == 95  # first reduction after >=180 seconds
    clock[0] += 3
    fan.tick()
    assert fan._floor == 95  # no 5%-per-poll rapid ramp-down

    # LLM begins another burst: increase instantly, even inside write cooldown.
    (hwmon / "temp3_input").write_text("84000")
    clock[0] += 3
    prev_count = len(calls)
    fan.tick()
    assert fan._floor == 100
    assert len(calls) > prev_count
    assert calls[-1][0] == "apply"


def test_predictive_ramp_uses_temperature_trend_not_utilization():
    from app.monitoring.fan_control import _predictive_floor

    sensors = [
        {"id": "temp1", "value_c": 63, "headroom_c": 12},
        {"id": "temp3", "value_c": 55, "headroom_c": 30},
    ]
    # At +8 C/min, headroom 12C lasts ~1.5min -> early >=90%.
    assert _predictive_floor(sensors, 35, 100, 8.0) >= 90
    # Stable temperature has no predictive boost.
    assert _predictive_floor(sensors, 35, 100, 0.0) == 60


def _add_zero_nodes(device):
    directory = device / "gpu_od/fan_ctrl"
    (directory / "fan_zero_rpm_enable").write_text(
        "OD_FAN_ZERO_RPM_ENABLE:\n0\nOD_RANGE:\nFAN_ZERO_RPM_ENABLE: 0 1\n"
    )
    (directory / "fan_zero_rpm_stop_temperature").write_text(
        "OD_FAN_ZERO_RPM_STOP_TEMP:\n40C\nOD_RANGE:\nFAN_ZERO_RPM_STOP_TEMP: 25C 80C\n"
    )


def test_zero_rpm_only_after_stable_ten_minutes_and_immediate_wake(gpu, monkeypatch):
    fan, device, hwmon, _, calls = gpu
    _add_zero_nodes(device)
    clock = [100.0]
    monkeypatch.setattr(fan.time, "monotonic", lambda: clock[0])
    for sid, temp in (("temp1", 31), ("temp2", 34), ("temp3", 39)):
        (hwmon / f"{sid}_input").write_text(str(temp * 1000))
    (hwmon / "fan1_input").write_text("0")
    cfg = fan.FanSettings(enabled=True, bdf="0000:03:00.0", allow_zero_rpm=True)
    fan.configure(cfg)
    assert fan.status()["devices"][0]["zero_rpm_supported"] is True
    assert calls[0][0] == "zero-deny"
    assert not any(x[0] == "zero-allow" for x in calls)

    for _ in range(199):
        clock[0] += 3
        fan.tick()
    assert not any(x[0] == "zero-allow" for x in calls)
    clock[0] += 3
    fan.tick()
    assert calls[-1][0] == "zero-allow"
    assert fan.status()["active"]["zero_rpm_permitted"] is True

    # Memory heats up even if hotspot is still cool; wake without a hold.
    (hwmon / "temp3_input").write_text("56000")
    clock[0] += 3
    fan.tick()
    assert calls[-1][0] in ("zero-deny", "apply")
    assert any(x[0] == "zero-deny" for x in calls[2:])
    assert fan.status()["active"]["zero_rpm_permitted"] is False


def test_zero_rpm_is_default_off_and_unavailable_without_native_support(gpu):
    fan, device, _, _, calls = gpu
    _add_zero_nodes(device)
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    assert calls[0][0] == "zero-deny"
    assert not any(x[0] == "zero-allow" for x in calls)

    fan.configure(fan.FanSettings(enabled=False, bdf="0000:03:00.0"))
    (device / "gpu_od/fan_ctrl/fan_zero_rpm_stop_temperature").unlink()
    assert fan.status()["devices"][0]["zero_rpm_supported"] is False
    with pytest.raises(fan.FanControlError, match="Zero RPM"):
        fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", allow_zero_rpm=True))


def test_helper_zero_rpm_control_uses_fixed_nodes_and_stop_temp(tmp_path, monkeypatch):
    helper, curve = _helper(tmp_path)
    devdir = curve.parent
    _add_zero_nodes(curve.parents[2])
    enable = devdir / "fan_zero_rpm_enable"
    stop = devdir / "fan_zero_rpm_stop_temperature"
    writes = []
    original = Path.write_text

    def recording(path, value, *args, **kwargs):
        if path in (enable, stop):
            writes.append((path.name, value))
        return original(path, value, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", recording)
    helper.control_zero_rpm("allow", "0000:03:00.0")
    assert writes == [
        ("fan_zero_rpm_stop_temperature", "50"),
        ("fan_zero_rpm_stop_temperature", "c"),
        ("fan_zero_rpm_enable", "1"),
        ("fan_zero_rpm_enable", "c"),
    ]
    helper.control_zero_rpm("deny", "0000:03:00.0")
    assert writes[-2:] == [("fan_zero_rpm_enable", "0"), ("fan_zero_rpm_enable", "c")]


def test_helper_refuses_zero_rpm_when_stop_temperature_is_unsafe(tmp_path):
    helper, curve = _helper(tmp_path)
    _add_zero_nodes(curve.parents[2])
    stop = curve.parent / "fan_zero_rpm_stop_temperature"
    stop.write_text("OD_RANGE:\nFAN_ZERO_RPM_STOP_TEMP: 60C 80C\n")
    before = stop.read_text()
    with pytest.raises(SystemExit):
        helper.control_zero_rpm("allow", "0000:03:00.0")
    assert stop.read_text() == before



def test_stalled_monitor_loop_cannot_count_as_5_minutes_of_cooling(gpu, monkeypatch):
    fan, device, hwmon, _, calls = gpu
    _add_zero_nodes(device)
    clock = [100.0]
    monkeypatch.setattr(fan.time, "monotonic", lambda: clock[0])
    for sid, temp in (("temp1", 31), ("temp2", 34), ("temp3", 39)):
        (hwmon / f"{sid}_input").write_text(str(temp * 1000))
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", allow_zero_rpm=True))
    clock[0] += 600  # e.g. the host was suspended; no intervening reads
    fan.tick()
    assert not any(x[0] == "zero-allow" for x in calls)
    assert fan.status()["active"]["zero_rpm_permitted"] is False
