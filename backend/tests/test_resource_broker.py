from __future__ import annotations

import asyncio

from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.probes import ProviderRegistry
from app.resources.providers import (
    ProviderReservation,
    ResourceProvider,
    StaticReservationProvider,
    YieldLevel,
)
from app.resources.schema import LeaseState, RequestState, ResourceRequest, WaitReason
from app.resources.telemetry import ResourceTelemetry


def request(
    owner: str,
    job: str,
    required: int,
    *,
    mode: str = "exclusive-required",
    device: str = "auto",
    on_insufficient: str = "queue",
    max_wait: float = 300,
) -> ResourceRequest:
    return ResourceRequest.model_validate({
        "owner": owner,
        "job_id": job,
        "device": device,
        "vram": {
            "resident_bytes": required,
            "execution_peak_bytes": required,
            "cold_load_peak_bytes": required,
            "headroom_bytes": 0,
            "confidence": "measured",
        },
        "compute_mode": mode,
        "priority": 0,
        "class": "interactive",
        "max_wait_sec": max_wait,
        "on_insufficient": on_insufficient,
    })


def run(value):
    return asyncio.run(value)


def test_exclusive_waiter_is_granted_after_release_without_occupying_a_lease():
    async def scenario():
        broker = ResourceBroker(fake_devices(100))
        first = await broker.submit(request("addon:a", "a", 60))
        second = await broker.submit(request("addon:b", "b", 40))
        assert first.state == RequestState.GRANTED
        assert second.state == RequestState.WAITING
        assert second.reason == WaitReason.DEVICE_BUSY_EXCLUSIVE
        assert len(broker.leases.current()) == 1
        await broker.release(first.lease_id)
        return await broker.request_status(second.request_id), broker.leases.current()

    second, leases = run(scenario())
    assert second.state == RequestState.GRANTED
    assert len(leases) == 1 and leases[0].job_id == "b"


def test_waiting_cancel_is_immediate_and_never_granted():
    async def scenario():
        broker = ResourceBroker(fake_devices(100))
        first = await broker.submit(request("addon:a", "a", 80))
        second = await broker.submit(request("addon:b", "b", 20))
        canceled = await broker.cancel_request(second.request_id)
        await broker.release(first.lease_id)
        return canceled, await broker.request_status(second.request_id)

    canceled, final = run(scenario())
    assert canceled.state == RequestState.CANCELED
    assert final.state == RequestState.CANCELED


def test_lease_ttl_expiry_wakes_waiter_and_renew_extends_deadline():
    now = [100.0]

    async def scenario():
        broker = ResourceBroker(fake_devices(100), lease_ttl_sec=5, clock=lambda: now[0])
        first = await broker.submit(request("addon:a", "a", 100))
        renewed = await broker.renew(first.lease_id)
        second = await broker.submit(request("addon:b", "b", 100))
        now[0] = 106
        expired = await broker.expire_due()
        return renewed, expired, await broker.request_status(second.request_id), broker.leases.all()

    renewed, expired, second, leases = run(scenario())
    assert renewed.expires_at == 105
    assert expired == {"requests": 0, "leases": 1}
    assert second.state == RequestState.GRANTED
    assert any(item.state == LeaseState.EXPIRED for item in leases)


def test_multi_device_leases_are_independent_and_fixed_device_is_respected():
    async def scenario():
        broker = ResourceBroker(fake_devices(100, 100))
        first = await broker.submit(request("addon:a", "a", 100, device="gpu0"))
        second = await broker.submit(request("addon:a", "b", 100, device="gpu1"))
        third = await broker.submit(request("addon:b", "c", 1, device="gpu0"))
        return first, second, third, await broker.snapshot()

    first, second, third, snapshot = run(scenario())
    assert (first.device_id, second.device_id) == ("gpu0", "gpu1")
    assert third.state == RequestState.WAITING
    assert [item["lease_reserved_bytes"] for item in snapshot["devices"]] == [100, 100]


def test_shared_safe_leases_coexist_but_exclusive_waits():
    async def scenario():
        broker = ResourceBroker(fake_devices(100))
        first = await broker.submit(request("addon:a", "a", 30, mode="shared-safe"))
        second = await broker.submit(request("addon:b", "b", 40, mode="shared-safe"))
        exclusive = await broker.submit(request("addon:c", "c", 20))
        return first, second, exclusive

    first, second, exclusive = run(scenario())
    assert first.state == second.state == RequestState.GRANTED
    assert exclusive.state == RequestState.WAITING
    assert exclusive.reason == WaitReason.DEVICE_BUSY_EXCLUSIVE


def test_level_zero_capacity_impossible_rejects_immediately_even_when_queue_requested():
    reservation = ProviderReservation("llm", "gpu0", "llm:external", 80, yield_level=YieldLevel.NONE)
    provider = StaticReservationProvider("llm", [reservation])
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([provider]))
    result = run(broker.submit(request("addon:media", "video", 30)))
    assert result.state == RequestState.REJECTED
    assert result.reason == WaitReason.INSUFFICIENT_CAPACITY
    assert result.actions == []


def test_fail_fast_rejects_temporary_contention_and_fit_scheduling_skips_large_waiter():
    async def scenario():
        broker = ResourceBroker(fake_devices(100))
        active = await broker.submit(request("addon:a", "active", 60, mode="shared-safe"))
        large = await broker.submit(request("addon:b", "large", 70, mode="shared-safe"))
        small = await broker.submit(request("addon:c", "small", 40, mode="shared-safe"))
        fast = await broker.submit(request("addon:d", "fast", 1, on_insufficient="fail_fast"))
        return active, large, small, fast

    active, large, small, fast = run(scenario())
    assert active.state == small.state == RequestState.GRANTED
    assert large.state == RequestState.WAITING
    assert fast.state == RequestState.REJECTED
    assert fast.reason == WaitReason.DEVICE_BUSY_EXCLUSIVE


def test_request_max_wait_and_owner_cancel_reclaim_all_state():
    now = [10.0]

    async def scenario():
        broker = ResourceBroker(fake_devices(100), clock=lambda: now[0])
        active = await broker.submit(request("addon:a", "active", 100))
        waiting = await broker.submit(request("addon:b", "waiting", 100, max_wait=2))
        now[0] = 13
        expired = await broker.expire_due()
        canceled = await broker.cancel_owner("addon:a")
        return active, waiting, expired, canceled, await broker.lease_statuses()

    _active, waiting, expired, canceled, leases = run(scenario())
    assert expired["requests"] == 1
    assert canceled == {"requests": 0, "leases": 1}
    assert run(ResourceBroker(fake_devices(1)).request_statuses()) == []
    assert any(item.state == LeaseState.CANCELED for item in leases)


def test_waiting_owner_cancel_preserves_active_lease_for_ttl_fail_safe():
    async def scenario():
        broker = ResourceBroker(fake_devices(100))
        active = await broker.submit(request("addon:a", "active", 100))
        waiting = await broker.submit(request("addon:a", "waiting", 100))
        canceled = await broker.cancel_waiting_owner("addon:a")
        return active, await broker.request_status(waiting.request_id), canceled, await broker.lease_status(active.lease_id)

    active, waiting, canceled, lease = run(scenario())
    assert active.state == RequestState.GRANTED
    assert waiting.state == RequestState.CANCELED
    assert canceled == {"requests": 1, "leases": 0}
    assert lease.state == LeaseState.GRANTED


def test_yieldable_provider_is_requested_outside_broker_lock_and_waiter_wakes():
    class YieldingProvider(ResourceProvider):
        id = "llm"

        def __init__(self):
            self.loaded = True
            self.calls = 0

        def reservations(self):
            if not self.loaded:
                return []
            return [ProviderReservation(
                "llm", "gpu0", "llm:chat", 80, yield_level=YieldLevel.STOP
            )]

        async def request_yield(self, device_id, level, request=None):
            self.calls += 1
            self.loaded = False
            return True

    async def scenario():
        provider = YieldingProvider()
        broker = ResourceBroker(fake_devices(100), ProviderRegistry([provider]))
        result = await broker.submit(request(
            "addon:media", "video", 100, mode="exclusive-required"
        ).model_copy(update={"estimated_runtime_sec": 200}))
        assert result.state == RequestState.WAITING
        for _ in range(100):
            result = await broker.request_status(result.request_id)
            if result.state == RequestState.GRANTED:
                break
            await asyncio.sleep(0.01)
        return result, provider.calls

    result, calls = run(scenario())
    assert result.state == RequestState.GRANTED
    assert calls == 1


def test_oom_profile_enforces_cooldown_and_raised_reservation_floor():
    now = [10.0]
    telemetry = ResourceTelemetry(clock=lambda: now[0])
    telemetry.record_oom(
        "model:risky", "gpu0", observed_peak_bytes=90, requested_bytes=80
    )

    async def scenario():
        broker = ResourceBroker(
            fake_devices(100), clock=lambda: now[0], telemetry=telemetry
        )
        value = request("addon:media", "retry", 80, mode="shared-safe").model_copy(
            update={"residency_key": "model:risky"}
        )
        waiting = await broker.submit(value)
        now[0] = 71
        await broker.reschedule()
        granted = await broker.request_status(waiting.request_id)
        return waiting, granted, broker.leases.current()

    waiting, granted, leases = run(scenario())
    assert waiting.state == RequestState.WAITING
    assert waiting.reason == WaitReason.DEPENDENCY_PENDING
    assert granted.state == RequestState.GRANTED
    assert leases[0].reserved_bytes == 99
