from __future__ import annotations

import asyncio

import pytest

from app.resources.devices import fake_devices, observed_system_devices
from app.resources.probes import ProviderRegistry
from app.resources.providers import ProbeResult, ProviderReservation, ResourceProvider, StaticReservationProvider, YieldLevel
from app.resources.schema import ResourceRequest, WaitReason


def request() -> ResourceRequest:
    return ResourceRequest.model_validate({
        "owner": "addon:media", "job_id": "job-1", "device": "auto",
        "vram": {
            "resident_bytes": 1, "execution_peak_bytes": 2, "cold_load_peak_bytes": 3,
            "headroom_bytes": 1, "confidence": "measured",
        },
        "compute_mode": "shared-safe", "class": "workflow",
    })


def test_fake_device_collection_tracks_two_devices_without_double_counting_observed_usage():
    devices = fake_devices(100, 200)
    devices.update_observation("gpu0", observed_used_bytes=40, resident_keys=["model:a"])
    snapshots = devices.snapshots(fixed_reservations={"gpu0": 30}, lease_reservations={"gpu0": 20, "gpu1": 50})
    assert [(item.id, item.admitted_free_bytes) for item in snapshots] == [("gpu0", 50), ("gpu1", 150)]
    assert snapshots[0].resident_keys == ["model:a"]
    assert devices.get("gpu1").total_bytes == 200


def test_device_collection_rejects_duplicates_and_unknown_updates():
    with pytest.raises(ValueError, match="重複"):
        fake = fake_devices(10, 20)
        fake.replace([fake.get("gpu0"), fake.get("gpu0")])  # type: ignore[list-item]
    with pytest.raises(KeyError):
        fake_devices(10).update_observation("gpu9", observed_used_bytes=1)


def test_observed_devices_accept_monitor_float_bytes(monkeypatch):
    from app.monitoring.collector import collector

    monkeypatch.setattr(collector, "latest", {"gpu": {
        "name": "Observed GPU",
        "vram_total_bytes": 100.0,
        "vram_used_bytes": 25.0,
    }})
    device = observed_system_devices().get("gpu0")
    assert device is not None
    assert (device.name, device.total_bytes, device.observed_used_bytes) == (
        "Observed GPU", 100, 25
    )


def test_observed_devices_reuse_selected_provider_when_latest_is_unavailable(monkeypatch):
    from app.monitoring.collector import collector

    class Provider:
        @staticmethod
        def sample():
            return {"name": "Fallback GPU", "vram_total_bytes": 200, "vram_used_bytes": 10}

    monkeypatch.setattr(collector, "latest", None)
    monkeypatch.setattr(collector, "gpu", Provider())
    device = observed_system_devices().get("gpu0")
    assert device is not None
    assert (device.name, device.total_bytes, device.observed_used_bytes) == (
        "Fallback GPU", 200, 10
    )


def test_provider_registry_keeps_level_zero_reservations_explicit():
    fixed = ProviderReservation("llm", "gpu0", "llm:external", 60, yield_level=YieldLevel.NONE)
    provider = StaticReservationProvider("llm", [fixed])
    registry = ProviderRegistry([provider])
    assert registry.reservations() == [fixed]
    assert registry.reservations()[0].yieldable is False
    assert asyncio.run(registry.check(request(), "gpu0")).accepting is True


def test_provider_probe_returns_structured_reason_without_ui_text():
    class DrainingProvider(ResourceProvider):
        id = "runtime"
        def reservations(self): return []
        async def probe(self, _request, _device_id):
            return ProbeResult(False, WaitReason.PROVIDER_DRAINING, 2.5)

    result = asyncio.run(ProviderRegistry([DrainingProvider()]).check(request(), "gpu0"))
    assert result == ProbeResult(False, WaitReason.PROVIDER_DRAINING, 2.5)
