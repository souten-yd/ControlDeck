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
        "_pending_reset_bdf": None, "_last_reset_retry": 0.0, "_reset_retry_failures": 0,
        "_last_boost_at": 0.0, "_cool_since": None, "_last_cool_step": 0.0,
        "_temp_history": {}, "_zero_stable_since": None,
        "_zero_active": False, "_zero_initialized_bdf": None,
        "_last_rise_c_per_min": 0.0, "_applied_floor": 0,
        "_last_tick_at": None, "_no_restore_bdfs": set(),
        "_model_bdf": None, "_pid_out": None, "_last_pid": None,
        "_pid": __import__("app.monitoring.fan_pid", fromlist=["x"]).PIDController(),
        "_tracker": __import__("app.monitoring.fan_pid", fromlist=["x"]).SteadyTracker(),
        "_tune": __import__("app.monitoring.fan_pid", fromlist=["x"]).AutoTune(),
        "_history": __import__("collections").deque(maxlen=1200),
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
    # The learned PI/FF demand decays first; the cooling hold starts only
    # once the wanted floor is below the current one.
    for _ in range(100):
        clock[0] += 3
        fan.tick()
        if fan._cool_since is not None:
            break
    assert fan._cool_since is not None
    assert fan._floor == 100
    for _ in range(9):
        clock[0] += 3
        fan.tick()
    assert fan._floor == 100  # 27 seconds of observed cooldown: still held
    # After the 30 s hold the floor ramps continuously at 30%/min:
    # 1.5% per 3 s poll, never a 5% step.
    seen = []
    for _ in range(20):
        clock[0] += 3
        fan.tick()
        seen.append(fan._floor)
    assert seen[-1] == 70  # 60 s of ramp at 30%/min
    assert all(a - b <= 2 for a, b in zip([100] + seen, seen))

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

    near = [
        {"id": "temp1", "value_c": 82, "headroom_c": 8},
        {"id": "temp3", "value_c": 55, "headroom_c": 30},
    ]
    # Near the target (8°C) a rise of 8°C/min raises early: 4°C expected
    # 30 s ahead -> 73% on the ramp; stable it is 52%.
    assert _predictive_floor(near, 35, 100, 8.0) == 73
    assert _predictive_floor(near, 35, 100, 0.0) == 52
    # Racing at the target still goes to maximum.
    assert _predictive_floor(near, 35, 100, 30.0) == 100
    # Far from the target (12°C) the trend is ignored: normal LLM load.
    far = [{"id": "temp2", "value_c": 78, "headroom_c": 12}]
    assert _predictive_floor(far, 35, 100, 30.0) == _predictive_floor(far, 35, 100, 0.0)


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


def test_zero_rpm_requires_reliable_fan_and_power_readings(gpu):
    fan, device, hwmon, _, calls = gpu
    _add_zero_nodes(device)
    enabled = fan.FanSettings(enabled=True, bdf="0000:03:00.0", allow_zero_rpm=True)
    (hwmon / "fan1_input").unlink()
    with pytest.raises(fan.FanControlError, match="RPM"):
        fan.configure(enabled)
    (hwmon / "fan1_input").write_text("1500")
    (hwmon / "power1_average").unlink()
    with pytest.raises(fan.FanControlError, match="電力"):
        fan.configure(enabled)
    assert not calls



def test_recommended_presets_safe_and_consistent():
    from app.monitoring.fan_control import FAN_PRESETS, FanSettings

    builtins = {preset["id"]: preset for preset in FAN_PRESETS}
    assert set(builtins) == {"recommended", "llm_r9700", "llm_quiet", "curve_r9700", "pid_r9700", "quiet", "cooling"}
    default = FanSettings()
    assert default.preset_id == "recommended"
    assert default.enabled is False
    assert default.allow_zero_rpm is False
    for preset in FAN_PRESETS:
        assert preset["allow_zero_rpm"] is False
        assert preset["min_percent"] >= 15
        assert preset["cool_hold_seconds"] >= 30
        assert 1 <= preset["cool_rate_percent_per_min"] <= 60
        assert preset["zero_stable_seconds"] >= 600
        assert preset["learning_enabled"] is True
        assert 0 <= preset["ff_gain"] <= 55
        assert 0.5 <= preset["kp"] <= 3.2
    assert builtins["recommended"]["min_percent"] == default.min_percent
    assert builtins["recommended"]["cool_hold_seconds"] == default.cool_hold_seconds


def test_user_tuning_bounds_and_legacy_settings_migration():
    from app.monitoring.fan_control import FanSettings
    from pydantic import ValidationError

    # Persisted pre-PR360 settings should migrate without hardware writes.
    legacy = FanSettings.model_validate({
        "enabled": False, "bdf": "0000:03:00.0",
        "min_percent": 35, "allow_zero_rpm": False, "targets_c": {"temp3": 85},
    })
    assert legacy.preset_id == "recommended"
    assert legacy.cool_hold_seconds == 30
    assert legacy.learning_enabled is True
    for patch in (
        {"cool_hold_seconds": 29},
        {"cool_rate_percent_per_min": 0.5},
        {"cool_rate_percent_per_min": 61},
        {"zero_stable_seconds": 599},
        {"ff_gain": -0.1},
        {"kp": 0.49},
        {"kp": 3.21},
        {"ki": 0.031},
        {"preset_id": "unsafe"},
    ):
        with pytest.raises(ValidationError):
            FanSettings.model_validate({**legacy.model_dump(), **patch})


def test_graphic_custom_settings_persist_without_enabling_gpu(gpu):
    fan, _, _, _, calls = gpu
    edited = fan.FanSettings(
        enabled=False, bdf="0000:03:00.0", preset_id="custom",
        min_percent=42, cool_hold_seconds=360, cool_rate_percent_per_min=2.5,
        zero_stable_seconds=1200,
        ff_gain=32.0, kp=2.25, ki=0.02, learning_enabled=False,
        targets_c={"temp1": 72, "temp2": 85, "temp3": 78},
    )
    result = fan.configure(edited)
    assert result["settings"] == edited.model_dump()
    assert fan._load() == edited
    assert result["presets"][0]["id"] == "recommended"
    assert not calls, "Saving disabled settings must not cause a root helper call"


def test_configured_cooldown_parameters_reach_controller(gpu, monkeypatch):
    fan, _, hwmon, _, _ = gpu
    changed = fan.FanSettings(
        enabled=True, bdf="0000:03:00.0",
        cool_hold_seconds=330, cool_rate_percent_per_min=7.5,
        learning_enabled=False, ff_gain=42.0, kp=2.4, ki=0.02,
        preset_id="custom",
    )
    fan.configure(changed)
    assert fan._policy.ff_gain == 42
    assert fan._policy.kp == 2.4
    assert fan._policy.ki == 0.02
    record = []
    original = fan._smooth_floor

    def observe(wanted, current, now, **kwargs):
        record.append(kwargs)
        return original(wanted, current, now, **kwargs)

    monkeypatch.setattr(fan, "_smooth_floor", observe)
    for sensor_id, temperature in (("temp1", 35), ("temp2", 40), ("temp3", 40)):
        (hwmon / f"{sensor_id}_input").write_text(str(temperature * 1000))
    fan.tick()
    assert record[-1] == {"hold_seconds": 330, "rate_percent_per_min": 7.5}


def test_reset_restores_curve_when_zero_rpm_node_is_unadvertised(tmp_path, monkeypatch):
    """実機R9700: fan_zero_rpm_enable は存在するが中身が空で、書くとEIO。
    旧実装はそこで失敗し、カスタムカーブが残ったまま「復帰待ち」になった。"""
    helper, curve = _helper(tmp_path)
    empty = curve.parent / "fan_zero_rpm_enable"
    empty.write_text("\n")
    writes = []
    original = Path.write_text

    def device(path, text, *args, **kwargs):
        if path == empty:
            raise OSError(5, "Input/output error")
        if path == curve:
            writes.append(text)
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", device)
    helper.control_fan_curve("reset", "0000:03:00.0")
    assert writes == ["r", "c"]


def test_reset_still_fails_when_advertised_zero_rpm_cannot_be_restored(tmp_path, monkeypatch):
    helper, curve = _helper(tmp_path)
    node = curve.parent / "fan_zero_rpm_enable"
    node.write_text("FAN_ZERO_RPM_ENABLE:\n1\nOD_RANGE:\nZERO_RPM_ENABLE: 0 1\n")
    original = Path.write_text

    def device(path, text, *args, **kwargs):
        if path == node:
            raise OSError(5, "Input/output error")
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", device)
    with pytest.raises(SystemExit):
        helper.control_fan_curve("reset", "0000:03:00.0")


def test_floor_can_go_down_to_firmware_minimum_and_low_curve_is_flat():
    """実機R9700: 旧実装は25°C 35% / 42°C 45%を固定で敷き、最低指令を30%に
    下げても約1600rpmのままだった。ファームウェアの下限15%まで下げられ、
    低温側は最低指令のまま平らにする。高温側の安全網は残す。"""
    from app.monitoring import fan_control as fan
    assert fan.FanSettings(min_percent=15).min_percent == 15
    with pytest.raises(ValueError):
        fan.FanSettings(min_percent=14)
    # Measured R9700: the old 60°C→65% point spun 3000+ rpm at a 61°C hotspot.
    assert fan._curve((25, 100, 15, 100), 20) == "25:20,42:20,60:20,77:60,95:100"
    # Device minimum still wins over a lower floor.
    assert fan._curve((25, 100, 25, 100), 15).startswith("25:25,42:25,")


def test_helper_accepts_firmware_minimum_but_not_below(tmp_path):
    helper, curve = _helper(tmp_path)
    helper.control_fan_curve("apply", "0000:03:00.0", "25:20,42:20,60:65,77:90,95:100")
    with pytest.raises(SystemExit):
        helper.control_fan_curve("apply", "0000:03:00.0", "25:10,42:10,60:65,77:90,95:100")


def test_rejected_auto_restore_stops_retrying_and_asks_for_reboot(gpu, monkeypatch):
    """実機R9700: SMUがリセットを拒否すると以後のOD書き込みは再起動まで全て失敗する。
    10秒ごとに叩き続けず、3回で止めて再起動を案内する。"""
    fan, _, hwmon, _, calls = gpu
    clock = [100.0]
    monkeypatch.setattr(fan.time, "monotonic", lambda: clock[0])
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))

    def rejecting(action, bdf, curve=None):
        calls.append((action, bdf, curve))
        if action == "reset":
            raise fan.FanControlError("Input/output error")

    monkeypatch.setattr(fan, "_helper", rejecting)
    (hwmon / "temp3_input").unlink()
    fan.tick()  # fault + first failed reset
    assert fan.status()["pending_auto_restore"] is True
    for _ in range(20):
        clock[0] += 3
        fan.tick()
    resets = [c for c in calls if c[0] == "reset"]
    assert len(resets) == 1 + fan.MAX_RESET_RETRIES
    assert fan.REBOOT_HINT in fan.status()["error"]
    with pytest.raises(fan.FanControlError, match="再起動"):
        fan.configure(fan.FanSettings(enabled=False, bdf="0000:03:00.0"))


def _smu14(device):
    mp1 = device / "ip_discovery" / "die" / "0" / "MP1" / "0"
    mp1.mkdir(parents=True)
    (mp1 / "major").write_text("14\n")


def test_smu14_never_resets_on_disable_fault_or_shutdown(gpu):
    """利用者の判断: R9700(SMU 14)ではリセットを一切使わず、最後のカーブを保持する。"""
    fan, device, hwmon, _, calls = gpu
    _smu14(device)
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", min_percent=20))
    assert fan.status()["devices"][0]["restore_supported"] is False

    disabled = fan.configure(fan.FanSettings(enabled=False, bdf="0000:03:00.0"))
    assert disabled["state"] == "disabled"
    assert fan.HOLD_NOTICE in disabled["error"]
    assert disabled["pending_auto_restore"] is False

    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", min_percent=20))
    (hwmon / "temp3_input").unlink()
    fan.tick()
    assert fan.status()["state"] == "fault"
    assert fan.status()["pending_auto_restore"] is False
    fan.shutdown()
    assert [c for c in calls if c[0] == "reset"] == []


def test_non_smu14_still_returns_to_firmware_auto(gpu):
    fan, _, _, _, calls = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    fan.configure(fan.FanSettings(enabled=False, bdf="0000:03:00.0"))
    assert calls[-1][0] == "reset"


def test_helper_refuses_reset_on_smu14_and_skips_revert_on_failed_apply(tmp_path, monkeypatch):
    helper, curve = _helper(tmp_path)
    _smu14(curve.parents[2])
    before = curve.read_text()
    with pytest.raises(SystemExit):
        helper.control_fan_curve("reset", "0000:03:00.0")
    assert curve.read_text() == before
    writes = []
    original = Path.write_text

    def device(path, text, *args, **kwargs):
        if path == curve:
            writes.append(text)
            if text == "c":
                raise OSError(5, "Input/output error")
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", device)
    with pytest.raises(SystemExit):
        helper.control_fan_curve("apply", "0000:03:00.0", "25:20,42:20,60:65,77:90,95:100")
    assert "r" not in writes


@pytest.mark.parametrize("mode, expect_enabled", [("apply_saved", True), ("firmware", False)])
def test_startup_mode_after_pc_boot(gpu, tmp_path, monkeypatch, mode, expect_enabled):
    """PC起動後に保存設定を自動で再適用するか、GPU標準のまま起動するかを選べる。"""
    fan, _, _, _, _ = gpu
    boot = tmp_path / "boot_id"
    monkeypatch.setattr(fan, "BOOT_ID_PATH", boot)
    boot.write_text("boot-a\n")
    fan._persist(fan.FanSettings(enabled=True, bdf="0000:03:00.0", startup_mode=mode))
    fan.startup()  # first start in this boot (no marker yet) counts as after boot
    assert fan._load().enabled is expect_enabled

    # A ControlDeck restart within the same boot never changes the setting.
    fan._persist(fan.FanSettings(enabled=True, bdf="0000:03:00.0", startup_mode=mode))
    fan.startup()
    assert fan._load().enabled is True

    boot.write_text("boot-b\n")
    fan.startup()
    assert fan._load().enabled is expect_enabled
    if not expect_enabled:
        assert fan.status()["error"] == fan.FIRMWARE_STARTUP_NOTICE


def test_legacy_settings_default_to_reapplying_on_boot(gpu):
    fan, _, _, _, _ = gpu
    assert fan.FanSettings().startup_mode == "apply_saved"



def test_cool_floor_follows_user_minimum_and_ramps_without_steps():
    """実機: 冷えていても35%固定で、最低指令15%が効かなかった。"""
    from app.monitoring.fan_strategy import margin_ramp
    cool = [{"id": "temp1", "value_c": 30, "headroom_c": 45}]
    assert fan_module()._desired_floor(cool, 15, 100) == 15
    values = [margin_ramp(m / 2, 15, 100) for m in range(60, 0, -1)]
    assert values[0] == 15 and values[-1] == 100
    assert all(b >= a for a, b in zip(values, values[1:]))
    assert max(b - a for a, b in zip(values, values[1:])) <= 6  # no band-edge jump


def fan_module():
    from app.monitoring import fan_control
    return fan_control


def test_legacy_step_settings_migrate_to_equivalent_ramp(gpu, tmp_path):
    fan, _, _, _, _ = gpu
    (tmp_path / "amd-fan-control.json").write_text(
        '{"enabled": false, "min_percent": 35, "cool_hold_seconds": 300,'
        ' "cool_step_seconds": 90, "cool_step_percent": 3, "preset_id": "custom"}')
    loaded = fan._load()
    assert loaded.cool_rate_percent_per_min == 2.0
    assert loaded.cool_hold_seconds == 300



def test_hotspot_jump_far_from_target_does_not_max_the_fan():
    """実機: LLM開始時のhotspotの跳ね上がり（数秒で約20°C）を毎分80°C超と
    見積もり、余裕50°C以上あるのに100%まで上げていた。"""
    from app.monitoring.fan_control import _predictive_floor
    sensors = [{"id": "temp2", "value_c": 55, "headroom_c": 35}]
    assert _predictive_floor(sensors, 20, 100, 30.0) <= 50  # was 100
    # The trend window is long enough that a one-sample jump is averaged.
    fan = fan_module()
    fan._temp_history = {}
    rate = 0.0
    for t, v in ((0, 31), (3, 31), (6, 31), (9, 31), (12, 31), (15, 31), (18, 31), (21, 31),
                 (24, 31), (27, 31), (30, 31), (33, 51)):
        rate = fan._rise_rate([{"id": "temp2", "value_c": v}], 1000 + t)
    assert rate <= 40


def test_rise_rate_ignores_plateau_after_load_start_jump():
    """実機R9700: hotspotが72→81°Cへ跳ねた直後に横ばいでも、30秒窓の傾きだけで
    「上昇中」と見なし、81°C/90°Cで100%まで上げていた。"""
    fan = fan_module()
    fan._temp_history = {}
    rate = 0.0
    for t, v in ((0, 72), (3, 75), (6, 78), (9, 80), (12, 81), (15, 81), (18, 81),
                 (21, 81), (24, 81), (27, 81), (30, 81), (33, 81)):
        rate = fan._rise_rate([{"id": "temp2", "value_c": v}], 2000 + t)
    assert rate == 0
    # A rise that continues is still detected.
    fan._temp_history = {}
    for t in range(0, 36, 3):
        rate = fan._rise_rate([{"id": "temp2", "value_c": 60 + t / 3}], 3000 + t)
    assert rate > 15


def test_prediction_uses_each_sensors_own_rise():
    """hotspot 80/90°Cで横ばい、mem 66/85°Cが上昇中のとき、memの上昇を
    hotspotの余裕に当てはめて87%まで上げていた。"""
    from app.monitoring.fan_control import _predictive_floor
    sensors = [{"id": "temp2", "value_c": 81, "headroom_c": 9},
               {"id": "temp3", "value_c": 66, "headroom_c": 19}]
    steady = _predictive_floor(sensors, 20, 100, {"temp2": 0.0, "temp3": 0.0})
    assert _predictive_floor(sensors, 20, 100, {"temp2": 0.0, "temp3": 15.0}) == steady
    assert _predictive_floor(sensors, 20, 100, {"temp2": 15.0, "temp3": 0.0}) > steady


def test_curve_backstop_point_can_move_later():
    fan = fan_module()
    assert fan._curve((25, 100, 15, 100), 20, 40, 85) == "25:20,45:20,65:20,85:40,95:100"
    # Default stays on the measured R9700 layout.
    assert fan._curve((25, 100, 15, 100), 20, 60, None) == "25:20,42:20,60:20,77:60,95:100"
    quiet = next(p for p in fan.FAN_PRESETS if p["id"] == "llm_quiet")
    assert quiet["backstop_c"] == 85 and quiet["target_offset_c"] == 5


def test_single_hotspot_spike_is_not_a_rise():
    """3秒1回だけの+8°Cの跳ねを上昇と見なし、予測で84%に上げていた。"""
    fan = fan_module()
    fan._temp_history = {}
    rate = 0.0
    for t in range(0, 45, 3):
        rate = fan._rise_rate([{"id": "temp2", "value_c": 86 if t == 42 else 78}], 4000 + t)
    assert rate == 0


def test_llm_quiet_preset_targets_per_sensor_kind():
    fan = fan_module()
    quiet = next(p for p in fan.FAN_PRESETS if p["id"] == "llm_quiet")
    targets = {label: min(95, base + fan.preset_target_offset(quiet, {"label": label}))
               for label, base in (("edge", 75), ("junction", 90), ("mem", 85))}
    assert targets == {"edge": 85, "junction": 95, "mem": 90}


def test_hotspot_climb_is_not_extrapolated():
    """hotspotは数秒でファンに追従する。86/95°Cへの上昇を先読みして83〜100%にしていた。"""
    from app.monitoring.fan_control import _predictive_floor
    hot = [{"id": "temp2", "label": "junction", "kind": "junction", "value_c": 86, "headroom_c": 9}]
    assert _predictive_floor(hot, 20, 100, {"temp2": 15.0}) == _predictive_floor(hot, 20, 100, {"temp2": 0.0})
    mem = [{"id": "temp3", "label": "mem", "kind": "mem", "value_c": 81, "headroom_c": 9}]
    assert _predictive_floor(mem, 20, 100, {"temp3": 15.0}) > _predictive_floor(mem, 20, 100, {"temp3": 0.0})


def test_pid_mode_writes_map_shaped_curve_raised_to_pid_output(gpu):
    fan, _, hwmon, _, calls = gpu
    (hwmon / "temp3_input").write_text("60000")  # mem cool
    result = fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", control_mode="pid",
                                           min_percent=20, targets_c={"temp2": 90}))
    assert result["state"] == "controlling"
    curve = calls[-1][2]
    points = [tuple(map(int, p.split(":"))) for p in curve.split(",")]
    # Map shape shifted +3 °C: top at target+3, 100 % at target+8.
    assert points[3][0] == 93 and points[-1] == (98, 100)
    assert points[0][1] == points[1][1]  # flat at the PID output below the rise
    assert result["active"]["control_mode"] == "pid" and result["active"]["pid"] is not None
    assert fan.history()["samples"][-1]["targets"]["junction"] == 90


def test_autotune_requires_pid_mode(gpu):
    fan, *_ = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    with pytest.raises(fan.FanControlError):
        fan.autotune("start")


def test_autotune_handover_keeps_the_fan_speed(gpu, monkeypatch):
    """実機R9700: 自動調整の終了時に積分をFF抜きで初期化し、50%→100%へ跳ねていた。"""
    fan, _, hwmon, _, calls = gpu
    (hwmon / "temp3_input").write_text("60000")
    (hwmon / "temp2_input").write_text("88000")
    (hwmon / "power1_average").write_text("210000000")
    model = fan.fan_pid.ThermalModel(
        idle_c={"temp1": 32, "temp2": 36, "temp3": 32}, idle_power_w=12,
        points=[{"fan": 50, "power_w": 198, "r": {"temp1": 0.16, "temp2": 0.27, "temp3": 0.24}},
                {"fan": 70, "power_w": 198, "r": {"temp1": 0.09, "temp2": 0.18, "temp3": 0.16}}])
    monkeypatch.setattr(fan, "_load_model", lambda bdf: model)
    holds = iter([50, 50, None])

    def fake_tune(tune, m, *a, **k):
        value = next(holds)
        if value is None:
            tune.phase = "done"
            m.tuned = {"kp": 0.8, "ti": 50.0}
        else:
            tune.phase = "settling"
        return value

    monkeypatch.setattr(fan.fan_pid, "tune_step", fake_tune)
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", control_mode="pid",
                                  min_percent=20, pid_auto_tune=False,
                                  targets_c={"temp1": 85, "temp2": 90, "temp3": 90}))
    fan.tick()  # hold 50
    fan.tick()  # tune done → handover
    assert abs(fan._pid_out - 50) <= 2
    assert fan._load().pid_kp == 0.8


def test_curve_mode_writes_a_valid_map(gpu):
    fan, _, hwmon, _, calls = gpu
    (hwmon / "temp3_input").write_text("60000")
    result = fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", control_mode="curve",
                                           min_percent=20, targets_c={"temp2": 90, "temp3": 90}))
    assert result["state"] == "controlling"
    points = [tuple(map(int, p.split(":"))) for p in calls[-1][2].split(",")]
    assert [t for t, _ in points] == sorted(t for t, _ in points) and points[-1][1] == 100
    assert result["active"]["map"]["curve_temps"][3] == 90


def test_mode_presets_carry_mode_and_targets():
    fan = fan_module()
    for preset_id, mode in (("curve_r9700", "curve"), ("pid_r9700", "pid")):
        preset = next(p for p in fan.FAN_PRESETS if p["id"] == preset_id)
        assert preset["control_mode"] == mode
        targets = {label: min(95, base + fan.preset_target_offset(preset, {"label": label}))
                   for label, base in (("edge", 75), ("junction", 90), ("mem", 85))}
        assert targets == {"edge": 85, "junction": 90, "mem": 90}


@pytest.mark.parametrize("field,value", [("temp_rise_tau_seconds", 0), ("temp_rise_tau_seconds", 16),
                                         ("temp_fall_tau_seconds", 1), ("temp_fall_tau_seconds", 121),
                                         ("temp_fall_tau_seconds", float("nan"))])
def test_temperature_ema_settings_reject_unsafe_values(field, value):
    from app.monitoring.fan_control import FanSettings
    with pytest.raises(ValidationError):
        FanSettings.model_validate({field: value})


def test_fan_history_reports_raw_and_smoothed_separately(gpu, monkeypatch):
    fan, _, hwmon, _, _ = gpu
    clock = [100.0]
    monkeypatch.setattr(fan.time, "monotonic", lambda: clock[0])
    for sid in ("temp1", "temp2", "temp3"):
        (hwmon / f"{sid}_input").write_text("45000")
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", control_mode="curve",
                                  temp_rise_tau_seconds=2, temp_fall_tau_seconds=30))
    (hwmon / "temp2_input").write_text("80000")
    clock[0] += 3
    fan.tick()
    hot = fan.history()["samples"][-1]
    assert hot["temps"]["junction"] == 80
    assert 70 < hot["smoothed_temps"]["junction"] < 80
    (hwmon / "temp2_input").write_text("45000")
    clock[0] += 3
    fan.tick()
    cool = fan.history()["samples"][-1]
    assert cool["temps"]["junction"] == 45
    assert cool["smoothed_temps"]["junction"] > 65
    assert fan.status()["active"]["smoothed_temps_c"]["temp2"] > 65


def test_legacy_map_raw_overtemperature_wins_over_temperature_ema(gpu, monkeypatch):
    fan, _, hwmon, _, _ = gpu
    clock = [100.0]
    monkeypatch.setattr(fan.time, "monotonic", lambda: clock[0])
    for sid in ("temp1", "temp2", "temp3"):
        (hwmon / f"{sid}_input").write_text("45000")
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", control_mode="map",
                                  min_percent=20, targets_c={"temp2": 90}))
    (hwmon / "temp2_input").write_text("97000")
    clock[0] += 3
    fan.tick()
    assert fan._floor == 100
    assert fan.status()["active"]["smoothed_temps_c"]["temp2"] < 97
