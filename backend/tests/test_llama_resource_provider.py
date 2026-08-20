from __future__ import annotations

import asyncio

from app.models_mgmt import llama
from app.models_mgmt.resource_provider import LlamaCapacityProvider
from app.models_mgmt.runtime_policy import RuntimePolicy
from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.providers import YieldLevel
from app.resources.schema import ResourceRequest
from app.resources.telemetry import ResourceTelemetry


def media_request(job_id: str = "media", runtime: float = 200) -> ResourceRequest:
    return ResourceRequest.model_validate({
        "owner": "addon:media",
        "job_id": job_id,
        "device": "gpu0",
        "vram": {
            "resident_bytes": 80,
            "execution_peak_bytes": 80,
            "cold_load_peak_bytes": 80,
            "headroom_bytes": 0,
            "confidence": "measured",
        },
        "compute_mode": "exclusive-required",
        "class": "background",
        "estimated_runtime_sec": runtime,
    })


def test_llama_resource_request_reserves_cold_load_but_not_resident_model(
    monkeypatch, tmp_path,
):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 100)
    instance = {"alias": "chat", "model_path": str(model), "port": 8090}
    monkeypatch.setattr(llama, "get_instance", lambda alias: instance)
    monkeypatch.setattr(llama, "residency_key", lambda item: "llama:model")
    monkeypatch.setattr(llama, "list_instances", lambda: [{**instance, "loaded": False}])
    provider = LlamaCapacityProvider(fake_devices(8 * 1024**3), ResourceTelemetry())

    cold = provider.resource_request("chat", "gateway-1")
    assert cold.vram.confidence.value == "low"
    assert cold.vram.required_bytes > model.stat().st_size
    assert cold.compute_mode.value == "endpoint-managed"
    assert cold.residency_key == "llama:model"

    monkeypatch.setattr(llama, "list_instances", lambda: [{**instance, "loaded": True}])
    resident = provider.resource_request("chat", "gateway-2")
    assert resident.vram.required_bytes == 0
    assert resident.vram.confidence.value == "measured"


def test_managed_provider_stops_only_for_measured_profitable_yield(monkeypatch, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 50)
    instance = {"alias": "chat", "model_path": str(model), "loaded": True}
    monkeypatch.setattr(llama, "list_instances", lambda: [instance])
    monkeypatch.setattr(llama, "residency_key", lambda item: "llama:model")
    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.get_policy",
        lambda: RuntimePolicy(
            supervision="managed", min_uptime_sec=0, drain_timeout_sec=1
        ),
    )
    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.model_is_on_local_nvme", lambda path: True
    )
    stopped = []
    monkeypatch.setattr(
        llama, "stop_instance", lambda alias: (stopped.append(alias) or (True, ""))
    )
    telemetry = ResourceTelemetry()
    telemetry.record_load_measurement(
        "llama:model", process_start_sec=5, model_load_sec=35
    )
    provider = LlamaCapacityProvider(fake_devices(100), telemetry)
    assert provider.reservations()[0].yield_level == YieldLevel.STOP

    assert asyncio.run(provider.request_yield(
        "gpu0", YieldLevel.STOP, media_request(runtime=81)
    )) is True
    assert stopped == ["chat"]
    assert telemetry.snapshot()["counters"]["yield.completed"] == 1


def test_managed_provider_thrashing_and_non_nvme_fail_closed(monkeypatch, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    instance = {"alias": "chat", "model_path": str(model), "loaded": True}
    monkeypatch.setattr(llama, "list_instances", lambda: [instance])
    monkeypatch.setattr(llama, "residency_key", lambda item: "llama:model")
    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.get_policy",
        lambda: RuntimePolicy(supervision="managed", min_uptime_sec=0),
    )
    telemetry = ResourceTelemetry()
    telemetry.record_load_measurement(
        "llama:model", process_start_sec=5, model_load_sec=35
    )
    provider = LlamaCapacityProvider(fake_devices(100), telemetry)

    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.model_is_on_local_nvme", lambda path: False
    )
    assert provider.reservations()[0].yield_level == YieldLevel.NONE
    assert asyncio.run(provider.request_yield(
        "gpu0", YieldLevel.STOP, media_request(runtime=1000)
    )) is False

    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.model_is_on_local_nvme", lambda path: True
    )
    provider.reservations()
    assert asyncio.run(provider.request_yield(
        "gpu0", YieldLevel.STOP, media_request(runtime=15)
    )) is False
    assert telemetry.snapshot()["counters"]["reason:thrash_cost"] == 1


def test_gateway_lease_helper_activates_renews_and_releases(monkeypatch):
    from app.models_mgmt import gateway, resource_provider
    from app.resources import broker as broker_module

    broker = ResourceBroker(fake_devices(100))
    adapter = LlamaCapacityProvider(broker.devices, broker.telemetry)
    monkeypatch.setattr(resource_provider, "_provider", adapter)
    monkeypatch.setattr(broker_module, "broker", broker)
    monkeypatch.setattr(
        adapter, "resource_request", lambda alias, job_id: ResourceRequest.model_validate({
            **media_request(job_id).model_dump(by_alias=True),
            "owner": f"llm:{alias}",
            "compute_mode": "endpoint-managed",
        })
    )

    class Request:
        @staticmethod
        async def is_disconnected():
            return False

    async def scenario():
        acquired = await gateway._acquire_gateway_lease("chat", Request())
        adapter_value, lease_id, renew = acquired
        assert broker.leases.current()[0].state.value == "active"
        await gateway._release_gateway_lease(adapter_value, lease_id, renew)
        return broker.leases.all()

    leases = asyncio.run(scenario())
    assert leases[0].state.value == "released"
