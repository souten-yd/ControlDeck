from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable

from app.resources.schema import DeviceSnapshot


@dataclass
class ResourceDevice:
    id: str
    name: str
    total_bytes: int
    observed_used_bytes: int = 0
    compatible: bool = True
    resident_keys: set[str] = field(default_factory=set)


class DeviceCollection:
    """Thread-safe device facts; lease/provider reservations stay outside it."""

    def __init__(self, devices: Iterable[ResourceDevice] = ()):
        self._lock = threading.RLock()
        self._devices: dict[str, ResourceDevice] = {}
        self.replace(devices)

    def replace(self, devices: Iterable[ResourceDevice]) -> None:
        values = list(devices)
        if len({item.id for item in values}) != len(values):
            raise ValueError("device IDが重複しています")
        if any(not item.id or item.total_bytes < 0 or item.observed_used_bytes < 0 for item in values):
            raise ValueError("device情報が不正です")
        with self._lock:
            self._devices = {item.id: item for item in values}

    def get(self, device_id: str) -> ResourceDevice | None:
        with self._lock:
            item = self._devices.get(device_id)
            if item is None:
                return None
            return ResourceDevice(
                id=item.id,
                name=item.name,
                total_bytes=item.total_bytes,
                observed_used_bytes=item.observed_used_bytes,
                compatible=item.compatible,
                resident_keys=set(item.resident_keys),
            )

    def list(self) -> list[ResourceDevice]:
        with self._lock:
            return [ResourceDevice(
                id=item.id,
                name=item.name,
                total_bytes=item.total_bytes,
                observed_used_bytes=item.observed_used_bytes,
                compatible=item.compatible,
                resident_keys=set(item.resident_keys),
            ) for item in sorted(self._devices.values(), key=lambda value: value.id)]

    def update_observation(
        self,
        device_id: str,
        *,
        observed_used_bytes: int,
        resident_keys: Iterable[str] | None = None,
    ) -> None:
        if observed_used_bytes < 0:
            raise ValueError("observed VRAMが不正です")
        with self._lock:
            item = self._devices.get(device_id)
            if item is None:
                raise KeyError(device_id)
            item.observed_used_bytes = observed_used_bytes
            if resident_keys is not None:
                item.resident_keys = set(resident_keys)

    def snapshots(
        self,
        *,
        fixed_reservations: dict[str, int] | None = None,
        lease_reservations: dict[str, int] | None = None,
    ) -> list[DeviceSnapshot]:
        fixed = fixed_reservations or {}
        leased = lease_reservations or {}
        result: list[DeviceSnapshot] = []
        for item in self.list():
            fixed_bytes = max(0, fixed.get(item.id, 0))
            lease_bytes = max(0, leased.get(item.id, 0))
            # Observed usage may include provider and lease allocations, so admission uses
            # the larger of observed usage or explicit reservations instead of double counting.
            admitted_used = max(item.observed_used_bytes, fixed_bytes + lease_bytes)
            result.append(DeviceSnapshot(
                id=item.id,
                name=item.name,
                total_bytes=item.total_bytes,
                observed_used_bytes=item.observed_used_bytes,
                fixed_reserved_bytes=fixed_bytes,
                lease_reserved_bytes=lease_bytes,
                admitted_free_bytes=max(0, item.total_bytes - admitted_used),
                compatible=item.compatible,
                resident_keys=sorted(item.resident_keys),
            ))
        return result


def fake_devices(*totals: int) -> DeviceCollection:
    return DeviceCollection(
        ResourceDevice(id=f"gpu{index}", name=f"Fake GPU {index}", total_bytes=total)
        for index, total in enumerate(totals)
    )


def observed_system_devices() -> DeviceCollection:
    """Read the existing monitor snapshot without introducing a second hardware probe."""
    from app.monitoring.collector import collector

    gpu = (collector.latest or {}).get("gpu") or {}
    total = gpu.get("vram_total_bytes")
    if not isinstance(total, int) or total <= 0:
        return DeviceCollection()
    used = gpu.get("vram_used_bytes")
    return DeviceCollection([ResourceDevice(
        id="gpu0",
        name=str(gpu.get("name") or "GPU 0"),
        total_bytes=total,
        observed_used_bytes=used if isinstance(used, int) and used >= 0 else 0,
    )])
