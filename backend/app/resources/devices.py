from __future__ import annotations

import threading
import asyncio
import logging
from math import isfinite
from dataclasses import dataclass, field, replace
from typing import Iterable

from app.resources.schema import HOST_DEVICE_ID, DeviceSnapshot

logger = logging.getLogger("control_deck.resources.devices")


@dataclass
class ResourceDevice:
    id: str
    name: str
    total_bytes: int
    observed_used_bytes: int = 0
    compatible: bool = True
    resident_keys: set[str] = field(default_factory=set)
    # "gpu" はVRAM。"host" はシステムRAM上で動かす配置で、CPUオフロードに対応した
    # 利用者（Add-on）が明示的に希望したときだけ割り当てる。知らない利用者へ黙って
    # 割り当てるとGPUを使うつもりのままVRAMを二重に取りにいってしまう。
    kind: str = "gpu"


def _copy(item: ResourceDevice) -> ResourceDevice:
    """可変な resident_keys だけ切り離して複製する。

    フィールドを手で並べ直すと、追加したときに写し漏れる（kind がそうなった）。
    """
    return replace(item, resident_keys=set(item.resident_keys))


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
            return _copy(item)

    def list(self) -> list[ResourceDevice]:
        with self._lock:
            return [_copy(item)
                    for item in sorted(self._devices.values(), key=lambda value: value.id)]

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
    if not isinstance(total, (int, float)) or not isfinite(total) or total <= 0:
        # The collector can fail after its GPU sample but before publishing `latest`
        # (for example, an unrelated power/history error). Reuse its already-selected
        # provider so Broker visibility does not disappear with the whole snapshot.
        provider = collector.gpu
        gpu = provider.sample() if provider is not None else None
        gpu = gpu or {}
        total = gpu.get("vram_total_bytes")
    if not isinstance(total, (int, float)) or not isfinite(total) or total <= 0:
        # GPUが読めなくても host は登録する。CPUオフロード可能な処理は動かせる。
        return DeviceCollection(host_device())
    used = gpu.get("vram_used_bytes")
    return DeviceCollection([
        ResourceDevice(
            id="gpu0",
            name=str(gpu.get("name") or "GPU 0"),
            total_bytes=int(total),
            observed_used_bytes=(
                int(used)
                if isinstance(used, (int, float)) and isfinite(used) and used >= 0
                else 0
            ),
        ),
        *host_device(),
    ])


# RAMを最後まで貸し出さない。OSと他プロセスのための余白である。
#
# VRAMは物理的に上限で頭打ちになるが、RAMはswapがあるぶん「入ったことになって
# 全体が遅くなる」という壊れ方をする。余白を置かないと broker は available を
# 使い切る判断を平気でする。実測（2026-09-05、この機械）: total 30.4GiB /
# available 18.3GiB で既に swap を 4.6GB 使っており、llama-server はVRAMとは別に
# ホスト側で 6.9GB を持っていた。ここへ 17.9GiB の匿名確保（画像worker、
# disable_mmap なので回収できない）を足すと、VRAMを守るために逃がしたはずの
# LLMをRAM側で潰す。
HOST_RESERVE_BYTES = 4 * 1024 ** 3


def host_device() -> list[ResourceDevice]:
    """システムRAMを配置先として登録する（CPUオフロード可能な処理の受け皿）。

    画像生成のような計算律速の処理は、VRAMが空いていなければRAMへ載せた方が、
    LLMのKVを追い出して全体を遅くするより得になる。空き容量は psutil の
    available から余白を引いた値を使う（他プロセスの消費も込みで見える値が正）。
    """
    try:
        import psutil

        memory = psutil.virtual_memory()
    except Exception:  # noqa: BLE001 - RAM が読めないだけで資源管理を止めない
        return []
    total = int(memory.total)
    if total <= 0:
        return []
    # 余白は「使用中」として数える。total を偽ると、利用者に見える容量が変わる。
    lendable = max(0, int(memory.available) - HOST_RESERVE_BYTES)
    return [ResourceDevice(
        id=HOST_DEVICE_ID,
        name="System RAM",
        total_bytes=total,
        observed_used_bytes=max(0, total - lendable),
        kind="host",
    )]


async def refresh_loop(devices: DeviceCollection) -> None:
    """Refresh facts from the established monitor; transient probe gaps retain the last facts."""
    while True:
        try:
            observed = (await asyncio.to_thread(observed_system_devices)).list()
            if observed:
                devices.replace(observed)
        except Exception:  # noqa: BLE001 - resource telemetry must not stop ControlDeck
            logger.exception("resource device refresh failed")
        await asyncio.sleep(2)
