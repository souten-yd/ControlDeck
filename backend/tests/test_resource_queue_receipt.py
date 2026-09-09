from __future__ import annotations

import asyncio

import pytest

from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.probes import ProviderRegistry
from app.resources.schema import RequestState
from tests.test_resource_broker import request
from tests.test_resource_exclusive_step_aside import ResidentLLM


class DrainingResident(ResidentLLM):
    def __init__(self, *, releases: bool = True):
        super().__init__(40, releases=releases)
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()

    async def step_aside(self, device_id: str) -> tuple[bool, str, int]:
        self.entered.set()
        await self.finish.wait()
        return await super().step_aside(device_id)


@pytest.mark.parametrize("cancel", [False, True])
def test_queue_returns_owned_receipt_before_provider_drain_finishes(cancel: bool):
    async def scenario() -> None:
        resident = DrainingResident()
        broker = ResourceBroker(fake_devices(100), ProviderRegistry([resident]))
        try:
            receipt = await asyncio.wait_for(
                broker.submit(request("addon:a", "queued", 80)), timeout=0.3,
            )
            await asyncio.wait_for(resident.entered.wait(), timeout=0.3)
            assert receipt.state == RequestState.WAITING
            assert receipt.owner == "addon:a" and receipt.job_id == "queued"
            assert receipt.request_id and receipt.lease_id is None
            assert not resident.finish.is_set()
            assert broker.leases.current() == []
            assert (await broker.keep_waiting(receipt.request_id)).state == RequestState.WAITING
            if cancel:
                assert (await broker.cancel_request(receipt.request_id)).state == RequestState.CANCELED
            resident.finish.set()
            await asyncio.wait_for(broker._room_task, timeout=0.3)
            final = await broker.request_status(receipt.request_id)
            assert final.state == (RequestState.CANCELED if cancel else RequestState.GRANTED)
            assert len(broker.leases.current()) == (0 if cancel else 1)
        finally:
            resident.finish.set()
            if broker._room_task is not None:
                await asyncio.wait_for(broker._room_task, timeout=0.3)

    asyncio.run(scenario())


@pytest.mark.parametrize("releases", [False, True])
def test_fail_fast_preserves_provider_drain_verdict(releases: bool):
    async def scenario() -> None:
        resident = DrainingResident(releases=releases)
        broker = ResourceBroker(fake_devices(100), ProviderRegistry([resident]))
        pending = asyncio.create_task(broker.submit(
            request("addon:a", "fast", 80, on_insufficient="fail_fast"),
        ))
        try:
            await asyncio.wait_for(resident.entered.wait(), timeout=0.3)
            assert not pending.done()
            resident.finish.set()
            final = await asyncio.wait_for(pending, timeout=0.3)
            assert final.state == (RequestState.GRANTED if releases else RequestState.REJECTED)
        finally:
            resident.finish.set()
            await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


def test_acquire_still_waits_for_grant_after_fast_queue_receipt():
    async def scenario() -> None:
        resident = DrainingResident()
        broker = ResourceBroker(fake_devices(100), ProviderRegistry([resident]))
        pending = asyncio.create_task(broker.acquire(request("addon:a", "acquire", 80)))
        try:
            await asyncio.wait_for(resident.entered.wait(), timeout=0.3)
            assert not pending.done()
            resident.finish.set()
            assert (await asyncio.wait_for(pending, timeout=0.3)).state == RequestState.GRANTED
        finally:
            resident.finish.set()
            await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())
