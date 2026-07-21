from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_disk_telemetry_snapshot_rates_temperature_and_smart(monkeypatch):
    from app.monitoring import disks

    counters = iter(
        [
            {"nvme0n1": SimpleNamespace(read_bytes=100, write_bytes=200)},
            {"nvme0n1": SimpleNamespace(read_bytes=500, write_bytes=800)},
        ]
    )
    times = iter([100.0, 102.0])
    monkeypatch.setattr(disks.psutil, "disk_io_counters", lambda perdisk: next(counters))
    monkeypatch.setattr(disks.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(disks, "_physical_block_device", lambda _device: Path("/dev/nvme0n1"))
    monkeypatch.setattr(disks, "_sysfs_temperature", lambda _name: (41.5, "Composite"))
    monkeypatch.setattr(disks, "_read_smartctl", lambda _block: (True, "passed", 43.0))

    service = disks.DiskTelemetryService()
    value = service.snapshot(["/dev/nvme0n1p1"])["/dev/nvme0n1p1"]
    assert value == {
        "physical_device": "/dev/nvme0n1",
        "read_bps": pytest.approx(200),
        "write_bps": pytest.approx(300),
        "temperature_c": pytest.approx(41.5),
        "temperature_sensor": "Composite",
        "smart_status": "passed",
        "smart_available": True,
    }


def test_sysfs_temperature_prefers_composite_and_rejects_out_of_range(tmp_path, monkeypatch):
    from app.monitoring import disks

    device = tmp_path / "nvme0n1" / "device" / "hwmon0"
    device.mkdir(parents=True)
    (device / "temp1_input").write_text("42000\n", encoding="ascii")
    (device / "temp1_label").write_text("Composite\n", encoding="utf-8")
    (device / "temp2_input").write_text("350000\n", encoding="ascii")
    (device / "temp2_label").write_text("Invalid\n", encoding="utf-8")
    (device / "temp3_input").write_text("51000\n", encoding="ascii")
    (device / "temp3_label").write_text("Sensor 1\n", encoding="utf-8")
    monkeypatch.setattr(disks, "SYS_BLOCK_ROOT", tmp_path)
    monkeypatch.setattr(disks, "SYS_DEVICES_ROOT", tmp_path)

    assert disks._sysfs_temperature("nvme0n1") == (42.0, "Composite")


def test_smartctl_uses_fixed_argv_and_parses_json(monkeypatch):
    from app.monitoring import disks

    captured = {}

    def run(argv, **kwargs):
        captured.update({"argv": argv, **kwargs})
        return SimpleNamespace(
            stdout='{"smart_status":{"passed":true},"temperature":{"current":39}}',
            returncode=0,
        )

    monkeypatch.setattr(disks, "_smartctl_executable", lambda: Path("/usr/sbin/smartctl"))
    monkeypatch.setattr(disks.subprocess, "run", run)

    assert disks._read_smartctl(Path("/dev/nvme0n1")) == (True, "passed", 39.0)
    assert captured["argv"] == ["/usr/sbin/smartctl", "-H", "-A", "-j", "/dev/nvme0n1"]
    assert captured["timeout"] == 8
    assert captured["check"] is False


def test_physical_block_device_rejects_non_device_path():
    from app.monitoring.disks import _physical_block_device

    assert _physical_block_device("/etc/passwd") is None
    assert _physical_block_device("relative/path") is None


def test_disk_api_returns_extended_telemetry(admin_client, monkeypatch):
    from app.monitoring import disks, router

    partition = SimpleNamespace(device="/dev/test1", mountpoint="/data", fstype="ext4")
    usage = SimpleNamespace(total=1000, used=250, percent=25.0)
    monkeypatch.setattr(router.psutil, "disk_partitions", lambda all: [partition])
    monkeypatch.setattr(router.psutil, "disk_usage", lambda _mountpoint: usage)
    monkeypatch.setattr(router.psutil, "cpu_times_percent", lambda interval: SimpleNamespace(iowait=1.25))
    monkeypatch.setattr(
        disks.disk_telemetry,
        "snapshot",
        lambda _devices: {
            "/dev/test1": {
                "physical_device": "/dev/test",
                "read_bps": 10.0,
                "write_bps": 20.0,
                "temperature_c": None,
                "temperature_sensor": None,
                "smart_status": "unavailable",
                "smart_available": False,
            }
        },
    )

    response = admin_client.get("/api/v1/system/disk")
    assert response.status_code == 200
    assert response.json() == [
        {
            "device": "/dev/test1",
            "mountpoint": "/data",
            "fstype": "ext4",
            "total": 1000,
            "used": 250,
            "percent": 25.0,
            "io_wait_percent": 1.25,
            "physical_device": "/dev/test",
            "read_bps": 10.0,
            "write_bps": 20.0,
            "temperature_c": None,
            "temperature_sensor": None,
            "smart_status": "unavailable",
            "smart_available": False,
        }
    ]
