from __future__ import annotations

from app.resources.providers import ProbeResult, ResourceProvider, YieldLevel
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

    async def check(self, request: ResourceRequest, device_id: str) -> ProbeResult:
        for provider_id in sorted(self._providers):
            result = await self._providers[provider_id].probe(request, device_id)
            if not result.accepting:
                return result
        return ProbeResult(accepting=True)

    def values(self) -> list[ResourceProvider]:
        return [self._providers[key] for key in sorted(self._providers)]

    async def request_yield(self, request: ResourceRequest, device_id: str) -> bool:
        yielded = False
        for provider in self.values():
            levels = [
                item.yield_level for item in provider.reservations()
                if item.device_id == device_id and item.yield_level > YieldLevel.NONE
            ]
            if levels and await provider.request_yield(device_id, max(levels), request):
                yielded = True
        return yielded

    def yield_wait_reason(self) -> WaitReason | None:
        for provider in self.values():
            reason = provider.yield_wait_reason()
            if reason is not None:
                return reason
        return None
