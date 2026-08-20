from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum

from app.resources.schema import ResourceRequest, WaitReason


class YieldLevel(IntEnum):
    NONE = 0
    DRAIN = 1
    SHRINK = 2
    UNLOAD = 3
    STOP = 4


@dataclass(frozen=True)
class ProviderReservation:
    provider_id: str
    device_id: str
    owner: str
    reserved_bytes: int
    residency_key: str | None = None
    yield_level: YieldLevel = YieldLevel.NONE
    draining: bool = False

    @property
    def yieldable(self) -> bool:
        return self.yield_level > YieldLevel.NONE


@dataclass(frozen=True)
class ProbeResult:
    accepting: bool
    reason: WaitReason | None = None
    retry_after_sec: float | None = None

    def __post_init__(self) -> None:
        if self.accepting and self.reason is not None:
            raise ValueError("accepting probeにwait reasonは指定できません")
        if not self.accepting and self.reason is None:
            raise ValueError("拒否probeにはwait reasonが必要です")


class ResourceProvider(ABC):
    id: str

    @abstractmethod
    def reservations(self) -> list[ProviderReservation]:
        raise NotImplementedError

    async def probe(self, request: ResourceRequest, device_id: str) -> ProbeResult:
        return ProbeResult(accepting=True)

    async def request_yield(
        self,
        device_id: str,
        level: YieldLevel,
        request: ResourceRequest | None = None,
    ) -> bool:
        return False

    def yield_wait_reason(self) -> WaitReason | None:
        return None


class StaticReservationProvider(ResourceProvider):
    def __init__(self, provider_id: str, values: list[ProviderReservation]):
        self.id = provider_id
        if any(item.provider_id != provider_id for item in values):
            raise ValueError("provider reservationのIDが一致しません")
        self._values = list(values)

    def reservations(self) -> list[ProviderReservation]:
        return list(self._values)

    def replace(self, values: list[ProviderReservation]) -> None:
        if any(item.provider_id != self.id for item in values):
            raise ValueError("provider reservationのIDが一致しません")
        self._values = list(values)
