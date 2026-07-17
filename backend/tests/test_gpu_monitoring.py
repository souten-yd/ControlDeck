from __future__ import annotations

from pathlib import Path

from app.monitoring import gpu


def _fake_card(root: Path, name: str, *, vram: int, busy: int = 3) -> Path:
    device = root / name / "device"
    hwmon = device / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    (device / "gpu_busy_percent").write_text(str(busy))
    (device / "mem_info_vram_used").write_text(str(vram // 2))
    (device / "mem_info_vram_total").write_text(str(vram))
    (hwmon / "temp1_input").write_text("42000")
    (hwmon / "power1_average").write_text("18000000")
    (hwmon / "fan1_input").write_text("1280")
    return device


def test_sysfs_provider_selects_gpu_with_largest_vram(tmp_path, monkeypatch):
    igpu = _fake_card(tmp_path, "card1", vram=512 * 1024**2)
    dgpu = _fake_card(tmp_path, "card2", vram=32 * 1024**3, busy=17)
    monkeypatch.setattr(gpu.glob, "glob", lambda pattern: [str(igpu), str(dgpu)] if "card[0-9]" in pattern else [str(dgpu / "hwmon" / "hwmon0")])

    provider = gpu.SysfsAmdProvider()
    sample = provider.sample()

    assert provider.device == dgpu
    assert sample is not None
    assert sample["utilization_percent"] == 17
    assert sample["vram_total_bytes"] == 32 * 1024**3
    assert sample["temperature_c"] == 42
    assert sample["power_watts"] == 18
    assert sample["fan_rpm"] == 1280


def test_detect_provider_prefers_complete_sysfs_without_cli(tmp_path, monkeypatch):
    device = _fake_card(tmp_path, "card1", vram=16 * 1024**3)
    monkeypatch.setattr(gpu.glob, "glob", lambda pattern: [str(device)] if "card[0-9]" in pattern else [str(device / "hwmon" / "hwmon0")])
    monkeypatch.setattr(gpu.shutil, "which", lambda name: (_ for _ in ()).throw(AssertionError(f"CLI probe must not run: {name}")))

    provider = gpu.detect_provider()

    assert isinstance(provider, gpu.SysfsAmdProvider)


def test_detect_provider_falls_back_when_sysfs_is_incomplete(tmp_path, monkeypatch):
    device = tmp_path / "card1" / "device"
    device.mkdir(parents=True)
    (device / "gpu_busy_percent").write_text("1")
    monkeypatch.setattr(gpu.glob, "glob", lambda pattern: [str(device)] if "card[0-9]" in pattern else [])
    monkeypatch.setattr(gpu.shutil, "which", lambda name: "/usr/bin/amd-smi" if name == "amd-smi" else None)
    monkeypatch.setattr(gpu.AmdSmiProvider, "sample", lambda self: gpu.GpuSample(name="AMD", utilization_percent=1))

    provider = gpu.detect_provider()

    assert isinstance(provider, gpu.AmdSmiProvider)
