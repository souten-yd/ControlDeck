"""Concurrent gateway calls must remeasure after a shared model starts."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.models_mgmt import gateway, local_llm, resource_provider
from app.resources import broker as broker_module
from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.schema import ResourceRequest


class Request:
    async def json(self) -> dict:
        return {"model": "chat", "messages": [{"role": "user", "content": "hello"}]}

    async def is_disconnected(self) -> bool:
        return False


def setup_gateway(monkeypatch):
    broker = ResourceBroker(fake_devices(100))
    adapter = resource_provider.LocalLlmCapacityProvider(broker.devices, broker.telemetry)
    monkeypatch.setattr(resource_provider, "_provider", adapter)
    monkeypatch.setattr(broker_module, "broker", broker)
    monkeypatch.setattr(gateway, "_authorize", lambda request: None)
    monkeypatch.setattr(gateway, "resolve_instance", lambda model: {"alias": "chat", "port": 8090})
    monkeypatch.setattr(gateway, "_record_gateway_oom", lambda *args: None)

    async def admit(*args):
        return {"accepting": True}

    monkeypatch.setattr(gateway, "_admit", admit)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
    monkeypatch.setattr(gateway.httpx, "AsyncClient",
                        lambda **kwargs: real_client(**{**kwargs, "transport": transport}))
    return broker, adapter


def test_overlapping_cold_calls_remeasure_after_shared_start(monkeypatch):
    broker, adapter = setup_gateway(monkeypatch)
    loaded = False
    estimates = []

    def requirement(alias: str, job_id: str) -> ResourceRequest:
        estimates.append(loaded)
        size = 0 if loaded else 80
        return ResourceRequest.model_validate({
            "owner": f"llm:{alias}", "job_id": job_id, "device": "gpu0",
            "vram": {"resident_bytes": size, "execution_peak_bytes": size,
                     "cold_load_peak_bytes": size, "headroom_bytes": 0, "confidence": "measured"},
            "compute_mode": "endpoint-managed", "residency_key": "llama:chat",
        })

    monkeypatch.setattr(adapter, "resource_request", requirement)

    async def scenario():
        nonlocal loaded
        starting, finish_start = asyncio.Event(), asyncio.Event()

        async def ready(alias, timeout_seconds):
            nonlocal loaded
            starting.set()
            await finish_start.wait()
            loaded = True
            broker.devices.update_observation("gpu0", observed_used_bytes=70)
            return True

        monkeypatch.setattr(local_llm, "ensure_ready", ready)
        first = asyncio.create_task(gateway.gateway_chat(Request()))
        await starting.wait()
        second = asyncio.create_task(gateway.gateway_chat(Request()))
        await asyncio.sleep(0.02)
        finish_start.set()
        try:
            responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
            assert [response.status_code for response in responses] == [200, 200]
            assert estimates == [False, True]
            assert adapter._active_requests == 0
            assert broker.leases.current() == []
        finally:
            for task in (first, second):
                task.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize('interrupt', ['disconnect', 'cancel'])
def test_interrupted_startup_waiter_does_not_hold_the_model_lock(interrupt):
    from fastapi import HTTPException

    class Disconnected(Request):
        async def is_disconnected(self) -> bool:
            return True

    async def scenario():
        entered = asyncio.Event()

        async def waiter():
            async with gateway._model_startup('waiting-model', Disconnected()):
                entered.set()

        async with gateway._model_startup('waiting-model', Request()):
            task = asyncio.create_task(waiter())
            # An unrelated model must not queue behind this startup.
            async with gateway._model_startup('other-model', Request()):
                assert not entered.is_set()
            if interrupt == 'cancel':
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(HTTPException) as error:
                    await asyncio.wait_for(task, timeout=1)
                assert error.value.status_code == 499
        async with gateway._model_startup('waiting-model', Request()):
            assert not entered.is_set()

    asyncio.run(scenario())


def test_failed_model_start_releases_lease_and_allows_the_next_call(monkeypatch):
    broker, adapter = setup_gateway(monkeypatch)
    attempts = 0
    monkeypatch.setattr(adapter, 'resource_request', lambda alias, job_id: ResourceRequest.model_validate({
        'owner': f'llm:{alias}', 'job_id': job_id, 'device': 'gpu0',
        'vram': {'resident_bytes': 80, 'execution_peak_bytes': 80,
                 'cold_load_peak_bytes': 80, 'headroom_bytes': 0, 'confidence': 'measured'},
        'compute_mode': 'endpoint-managed',
    }))

    async def ready(alias, timeout_seconds):
        nonlocal attempts
        attempts += 1
        return attempts > 1

    monkeypatch.setattr(local_llm, 'ensure_ready', ready)

    async def scenario():
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await gateway.gateway_chat(Request())
        assert error.value.status_code == 503
        assert adapter._active_requests == 0
        assert broker.leases.current() == []
        assert (await gateway.gateway_chat(Request())).status_code == 200
        assert adapter._active_requests == 0
        assert broker.leases.current() == []

    asyncio.run(scenario())
