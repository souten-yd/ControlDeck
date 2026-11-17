"""ファン制御 v2（PID / 温度マップ）の、実機を使わないテスト。

目的: 2 方式の設定・旧設定の引き継ぎ・ファームウェアへ書くカーブ・故障時の安全側の動き・helper の許可範囲を守る。
根拠: 2026-11-17 に 4 方式・7 プリセットから作り直した（docs/design-amd-smart-fan.md）。helper・SMU14・起動時の
テストは旧版から引き継いだ（R9700 の実機で踏んだ不具合）。
"""
from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from pydantic import ValidationError


@pytest.fixture
def gpu(tmp_path, monkeypatch):
    from app.monitoring import fan_control as fan, fan_core, fan_pid, fan_tune

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
        "_active_bdf": None, "_last_curve": None, "_applied_percent": 0, "_sensor_ids": set(),
        "_zero_rpm_checks": 0, "_state": "disabled", "_error": "", "_last_sample": {},
        "_pending_reset_bdf": None, "_last_reset_retry": 0.0, "_reset_retry_failures": 0,
        "_last_tick_at": None, "_no_restore_bdfs": set(), "_model_bdf": None,
        "_controller": fan_core.Controller(), "_tracker": fan_pid.SteadyTracker(),
        "_history": __import__("collections").deque(maxlen=1200),
    }.items():
        monkeypatch.setattr(fan, key, value)
    monkeypatch.setattr(fan_tune, "_tune", fan_tune._Tune())
    calls = []
    monkeypatch.setattr(fan, "_helper", lambda action, bdf, curve=None: calls.append((action, bdf, curve)))
    return fan, device, hwmon, curve, calls


def _points(curve: str) -> list[tuple[int, int]]:
    return [tuple(map(int, p.split(":"))) for p in curve.split(",")]


def test_curve_is_flat_to_the_hotspot_limit_then_full(gpu):
    """ファームウェアのカーブ: 上限までは制御の出力で一定、上限 +1°C で最大。アプリが止まっても上限での最大は残る。"""
    fan, _, _, _, calls = gpu
    result = fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", limits_c={"temp2": 92}))
    assert fan.DEFAULT_LIMIT_C["junction"] == 95   # 利用者の指示: hotspot は 95°C 超えで最大、普段は 90°C 以下
    assert result["state"] == "controlling"
    points = _points(calls[-1][2])
    assert points[-1] == (93, 100) and points[-2][0] == 92
    assert len({p for _, p in points[:-1]}) == 1
    # 決めていないセンサーは既定の上限で保存され、目標は上限 − マージン
    saved = fan._load()
    assert saved.limits_c == {"temp1": 85, "temp2": 92, "temp3": 90}
    sensors = {s["id"]: s for s in result["active"]["sensors"]}
    assert sensors["temp3"]["target_c"] == 85.0


def test_over_the_limit_goes_to_full_speed_at_once(gpu):
    """上限を超えたら、なめらかさの上限を待たずに最大回転（どの方式でも）。"""
    fan, _, hwmon, _, calls = gpu
    for mode in ("pid", "map"):
        fan.configure(fan.FanSettings(enabled=False, bdf="0000:03:00.0"))
        (hwmon / "temp3_input").write_text("84000")
        fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", mode=mode, smoothing_s=300))
        assert fan._applied_percent < 100
        (hwmon / "temp3_input").write_text("90500")
        fan.tick()
        assert fan._applied_percent == 100, mode
        assert fan._last_sample["control"]["emergency"] is True


def test_old_settings_are_carried_over(gpu, tmp_path):
    """旧版の設定（本番の curve_r9700・温度追従）から、上限温度・方式・マージン・PID の効き具合を引き継ぐ。"""
    fan, _, _, _, _ = gpu
    old = {"enabled": True, "bdf": "0000:03:00.0", "min_percent": 20, "allow_zero_rpm": False,
           "preset_id": "curve_r9700", "cool_hold_seconds": 30, "targets_c": {"temp1": 85, "temp2": 90, "temp3": 90},
           "control_mode": "curve", "temperature_margin_c": 5.0, "pid_kp": 1.33, "pid_ti": 49.8,
           "backstop_percent": 60, "startup_mode": "apply_saved"}
    (tmp_path / fan.STATE_FILE).write_text(json.dumps(old))
    settings = fan._load()
    assert settings.mode == "map" and settings.limits_c == old["targets_c"] and settings.margin_c == 5.0
    assert settings.pid_kp == 1.33 and settings.enabled is True
    (tmp_path / fan.STATE_FILE).write_text(json.dumps({**old, "control_mode": "target", "temperature_margin_c": 7}))
    assert fan._load().mode == "pid" and fan._load().margin_c == 7.0


def test_map_points_must_rise_toward_the_target():
    from app.monitoring.fan_control import FanSettings, MapPoint

    good = FanSettings(map_points=[MapPoint(below_c=0, percent=60), MapPoint(below_c=20, percent=20)])
    assert [p.below_c for p in good.map_points] == [20, 0]   # 冷たい順に並べ直す
    with pytest.raises(ValidationError):
        FanSettings(map_points=[MapPoint(below_c=0, percent=30), MapPoint(below_c=20, percent=50)])
    with pytest.raises(ValidationError):
        FanSettings(margin_c=1)   # マージン 0〜1°C は上限を必ず超える


def test_switching_mode_keeps_the_fan_speed(gpu, monkeypatch):
    """方式を切り替えても回転数は今の値から続く（段差を作らない）。"""
    fan, _, hwmon, _, _ = gpu
    clock = [100.0]
    monkeypatch.setattr(fan, "time", SimpleNamespace(**{**vars(fan.time), "monotonic": lambda: clock[0]}))
    (hwmon / "temp3_input").write_text("86000")
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", mode="map"))
    before = fan._applied_percent
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", mode="pid", smoothing_s=300))
    assert abs(fan._applied_percent - before) <= 3


def test_history_records_limits_targets_and_fan(gpu):
    fan, _, _, _, _ = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0"))
    sample = fan.history()["samples"][-1]
    assert sample["limits"]["mem"] == 90 and sample["targets"]["mem"] == 85.0
    assert sample["fan_percent"] >= 20 and "smoothed_temps" in sample


def test_autotune_requires_control_and_an_llm(gpu, monkeypatch):
    from app.monitoring import fan_tune

    fan, _, _, _, _ = gpu
    with pytest.raises(fan.FanControlError, match="有効"):
        fan_tune.start({"alias": "x", "base_url": "http://127.0.0.1:1/v1"})
    monkeypatch.setattr("app.models_mgmt.local_llm.llm_instances", lambda: [])
    with pytest.raises(RuntimeError, match="LLM"):
        fan_tune.choose_llm(None)


def test_autotune_cancel_during_verify_restores_settings_and_stops_load(gpu, monkeypatch):
    """確認のために仮に保存した値は、中止したら元に戻る。負荷役も止める。"""
    from app.monitoring import fan_tune

    fan, _, _, _, _ = gpu
    fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0", mode="map", smoothing_s=90))
    stopped = []
    monkeypatch.setattr(fan_tune, "stop_load", lambda: stopped.append(1))
    fan_tune.start({"alias": "x", "base_url": "http://127.0.0.1:1/v1"})
    fan_tune._tune.result = {"pid_kp": 2.0, "pid_ti": 40.0, "smoothing_s": 30.0, "margin_c": 5.0,
                             "map_points": [{"below_c": 20, "percent": 20}, {"below_c": 0, "percent": 55}]}
    fan_tune._start_verify(0.0, "pid")
    assert fan._load().mode == "pid" and fan._load().smoothing_s == 30.0
    fan_tune.cancel()
    assert fan._load().mode == "map" and fan._load().smoothing_s == 90.0 and fan._load().enabled
    assert stopped and fan_tune.snapshot()["phase"] == "aborted"


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
    (hwmon / "temp3_input").write_text("92000")  # 上限超え → 最大回転なのに 0 rpm
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


def test_limits_cannot_exceed_reported_critical_margin(gpu):
    """上限温度はハードウェアの crit − 5°C を超えられない。"""
    fan, _, hwmon, _, calls = gpu
    (hwmon / "temp3_crit").write_text("90000")  # 上限は 85 まで
    with pytest.raises(fan.FanControlError, match="上限温度"):
        fan.configure(fan.FanSettings(enabled=True, bdf="0000:03:00.0",
                                      limits_c={"temp3": 90}))
    assert not calls


def test_settings_reject_untrusted_names_and_bdf():
    from app.monitoring.fan_control import FanSettings
    with pytest.raises(ValidationError):
        FanSettings(enabled=True, bdf="../../tmp")
    with pytest.raises(ValidationError):
        FanSettings(limits_c={"../../temp3": 75})
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
    monkeypatch.setattr(fan, "time", SimpleNamespace(**{**vars(fan.time), "monotonic": lambda: clock[0]}))
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


def _add_zero_nodes(device):
    directory = device / "gpu_od/fan_ctrl"
    (directory / "fan_zero_rpm_enable").write_text(
        "OD_FAN_ZERO_RPM_ENABLE:\n0\nOD_RANGE:\nFAN_ZERO_RPM_ENABLE: 0 1\n"
    )
    (directory / "fan_zero_rpm_stop_temperature").write_text(
        "OD_FAN_ZERO_RPM_STOP_TEMP:\n40C\nOD_RANGE:\nFAN_ZERO_RPM_STOP_TEMP: 25C 80C\n"
    )


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
