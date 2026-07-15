import importlib.util
from pathlib import Path


def _caps():
    return {
        "bdf": "0000:03:00.0",
        "power": {"current_watts": 210, "min_watts": 210, "max_watts": 300, "default_watts": 300},
        "memory": {"supported": True, "levels": [
            {"level": i, "mhz": mhz, "current": i == 0}
            for i, mhz in enumerate((96, 456, 772, 875, 1124, 1258))
        ], "performance_level": "auto"},
        "core": {"supported": True, "levels": [
            {"level": 0, "mhz": 500, "current": False},
            {"level": 1, "mhz": 0, "current": True},
            {"level": 2, "mhz": 2350, "current": False},
        ]},
        "helper_installed": True,
    }


def test_quiet_lowers_mclk_by_exactly_one_level(monkeypatch):
    from app.models_mgmt import amd_gpu, runtime_policy

    monkeypatch.setattr(amd_gpu, "capabilities", _caps)
    policy = runtime_policy.RuntimePolicy(amd_gpu={"enabled": True, "profile": "quiet"})
    result = runtime_policy.normalize_gpu_profile(policy).amd_gpu
    assert result.power_limit_watts == 210
    assert result.memory_clock_mode == "limit"
    assert result.memory_clock_level == 4
    assert _caps()["memory"]["levels"][result.memory_clock_level]["mhz"] == 1124
    assert result.core_clock_mode == "auto"


def test_presets_restore_auto_and_custom_keeps_explicit_clocks(monkeypatch):
    from app.models_mgmt import amd_gpu, runtime_policy

    monkeypatch.setattr(amd_gpu, "capabilities", _caps)
    for name, watts in (("balanced", 255), ("full", 300)):
        policy = runtime_policy.RuntimePolicy(amd_gpu={
            "enabled": True, "profile": name, "power_limit_watts": watts,
            "memory_clock_mode": "limit", "memory_clock_level": 1,
            "core_clock_mode": "limit", "core_clock_level": 0,
        })
        result = runtime_policy.normalize_gpu_profile(policy).amd_gpu
        assert result.memory_clock_mode == "auto"

    custom = runtime_policy.RuntimePolicy(amd_gpu={
        "enabled": True, "profile": "custom", "power_limit_watts": 240,
        "memory_clock_mode": "limit", "memory_clock_level": 1,
        "core_clock_mode": "limit", "core_clock_level": 0,
    })
    result = runtime_policy.normalize_gpu_profile(custom).amd_gpu
    assert result.memory_clock_mode == "limit" and result.memory_clock_level == 1
    assert result.core_clock_mode == "limit" and result.core_clock_level == 0


def test_apply_profile_uses_fixed_helper_argv(tmp_path, monkeypatch):
    from app.models_mgmt import amd_gpu, runtime_policy

    helper = tmp_path / "helper"
    helper.write_text("test")
    monkeypatch.setattr(amd_gpu, "HELPER", helper)
    monkeypatch.setattr(amd_gpu, "capabilities", _caps)
    amd_gpu._applied_fingerprint = ""
    seen = []

    class Result:
        returncode = 0
        stderr = ""

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return Result()

    monkeypatch.setattr(amd_gpu.subprocess, "run", run)
    profile = runtime_policy.AmdGpuSettings(
        enabled=True, profile="custom", power_limit_watts=230,
        core_clock_mode="limit", core_clock_level=0,
    )
    out = amd_gpu.apply_profile(profile, force=True)
    assert out["applied"] is True
    assert seen[0][0] == ["sudo", "-n", str(helper), "apply-amd", "0000:03:00.0",
                           "230", "auto", "0", "limit", "0"]
    assert seen[0][1]["timeout"] == 10


def test_privileged_helper_validates_and_writes_only_gpu_attributes(tmp_path):
    helper_path = Path(__file__).parents[2] / "helper" / "control-deck-hw-helper.py"
    spec = importlib.util.spec_from_file_location("control_deck_hw_helper", helper_path)
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    pci_root = tmp_path / "sys" / "bus" / "pci" / "devices"
    sys_devices = tmp_path / "sys" / "devices"
    device = sys_devices / "pci0000:00" / "0000:03:00.0"
    hwmon = device / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    pci_root.mkdir(parents=True)
    (pci_root / "0000:03:00.0").symlink_to(device, target_is_directory=True)
    (device / "vendor").write_text("0x1002\n")
    (device / "power_dpm_force_performance_level").write_text("auto\n")
    (device / "pp_dpm_mclk").write_text("0: 96Mhz *\n1: 456Mhz\n2: 1258Mhz\n")
    (device / "pp_dpm_sclk").write_text("0: 500Mhz\n1: 2350Mhz *\n")
    (hwmon / "name").write_text("amdgpu\n")
    (hwmon / "power1_cap_min").write_text("210000000\n")
    (hwmon / "power1_cap_max").write_text("300000000\n")
    (hwmon / "power1_cap").write_text("300000000\n")
    helper.PCI_ROOT = pci_root.resolve()
    helper.SYS_DEVICES = sys_devices.resolve()

    helper.apply_amd("0000:03:00.0", "210", "limit", "1", "limit", "0")
    assert (hwmon / "power1_cap").read_text() == "210000000"
    assert (device / "power_dpm_force_performance_level").read_text() == "manual"
    assert (device / "pp_dpm_mclk").read_text() == "0 1"
    assert (device / "pp_dpm_sclk").read_text() == "0"

    helper.apply_amd("0000:03:00.0", "210", "auto", "0", "auto", "0")
    assert (device / "power_dpm_force_performance_level").read_text() == "auto"
