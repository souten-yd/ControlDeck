from __future__ import annotations

from app.resources.providers import ProbeResult, ResourceProvider
from app.resources.schema import ResourceRequest


class ProviderRegistry:
    def __init__(self, providers: list[ResourceProvider] | None = None):
        self._providers: dict[str, ResourceProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: ResourceProvider) -> None:
        if not provider.id or provider.id in self._providers:
            raise ValueError("provider IDが空または重複しています")
        self._providers[provider.id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def reservations(self):
        return [item for provider in self._providers.values() for item in provider.reservations()]

    def immovable_reservations(self):
        """退けない provider の予約。入らない要求を待たせずに断るために使う。"""
        return [item for provider in self._providers.values()
                if not getattr(provider, "can_step_aside", False)
                for item in provider.reservations()]

    async def check(self, request: ResourceRequest, device_id: str) -> ProbeResult:
        for provider_id in sorted(self._providers):
            result = await self._providers[provider_id].probe(request, device_id)
            if not result.accepting:
                return result
        return ProbeResult(accepting=True)

    async def step_aside(self, device_id: str, needed_bytes: int) -> tuple[bool, str, int]:
        """その device で場所を空けられる provider に、順に一度だけ頼む。

        必要量に届いた時点でやめる。全部に頼んで回ると、要らない分まで降ろす。
        """
        freed = 0
        reasons: list[str] = []
        for provider_id in sorted(self._providers):
            provider = self._providers[provider_id]
            if not any(item.device_id == device_id and item.reserved_bytes > 0
                       for item in provider.reservations()):
                continue
            released, reason, bytes_freed = await provider.step_aside(device_id)
            reasons.append(f"{provider_id}:{reason}")
            if released:
                freed += bytes_freed
                if freed >= needed_bytes:
                    break
        return freed > 0, ",".join(reasons) or "no_provider_here", freed

    def values(self) -> list[ResourceProvider]:
        return [self._providers[key] for key in sorted(self._providers)]

