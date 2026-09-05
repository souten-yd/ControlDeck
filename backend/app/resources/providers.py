from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.resources.schema import ResourceRequest, WaitReason


@dataclass(frozen=True)
class ProviderReservation:
    provider_id: str
    device_id: str
    owner: str
    reserved_bytes: int
    residency_key: str | None = None
    draining: bool = False



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
    # 場所を空けられる provider か。空けられないものの予約は「動かせない量」と
    # して数え、入らない要求は待たせずに断る。
    can_step_aside: bool = False

    @abstractmethod
    def reservations(self) -> list[ProviderReservation]:
        raise NotImplementedError

    async def probe(self, request: ResourceRequest, device_id: str) -> ProbeResult:
        return ProbeResult(accepting=True)

    async def step_aside(self, device_id: str) -> tuple[bool, str, int]:
        """使っていないなら退く。使っているなら断る。

        broker が「どの device にも置けない」と判じる直前に一度だけ呼ぶ。
        廃止した yield 機構とは別物である。yield は優先度の高い者のために
        追い出すもので、実行中の推論を巻き込んだ。ここは
        (a) 使用中なら退かない (b) 実行中の処理は待つ
        (c) 引き金は優先度ではなく「他に置き場所が無い」こと、である。

        返り値は (退いたか, 理由, 空けたバイト数)。
        """
        return False, "step_aside_not_supported", 0


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
