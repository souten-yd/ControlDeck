from __future__ import annotations

import asyncio

from app.models_mgmt import llama
from app.models_mgmt.resource_provider import LlamaCapacityProvider
from app.models_mgmt.runtime_policy import RuntimePolicy
from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.probes import ProviderRegistry
from app.resources.providers import YieldLevel
from app.resources.schema import ResourceRequest, RequestState, WaitReason
from app.resources.telemetry import ResourceTelemetry


def _request(runtime: float | None) -> ResourceRequest:
    return ResourceRequest.model_validate({
        "owner": "addon:test", "job_id": "job", "device": "gpu0",
        "vram": {
            "resident_bytes": 100, "execution_peak_bytes": 100,
            "cold_load_peak_bytes": 100, "headroom_bytes": 0,
            "confidence": "measured",
        },
        "compute_mode": "exclusive-required", "class": "background",
        "estimated_runtime_sec": runtime,
    })


def _warm_profile(telemetry: ResourceTelemetry, key: str = "llama:model") -> None:
    for cost in (81.0, 82.0, 83.0):
        telemetry.record_load_measurement(
            key, process_start_sec=1, model_load_sec=cost - 1,
        )
    for cost in (7.0, 8.0, 9.0):
        telemetry.record_unload(key)
        telemetry.record_load_measurement(
            key, process_start_sec=1, model_load_sec=cost - 1,
        )


def _provider(monkeypatch, tmp_path, telemetry: ResourceTelemetry):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    instance = {"alias": "chat", "model_path": str(model), "loaded": True}
    monkeypatch.setattr(llama, "list_instances", lambda: [instance])
    monkeypatch.setattr(llama, "residency_key", lambda _item: "llama:model")
    monkeypatch.setattr(llama, "stop_instance", lambda _alias: (True, ""))
    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.get_policy",
        lambda: RuntimePolicy(supervision="managed", min_uptime_sec=0),
    )
    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.model_is_on_local_nvme", lambda _path: True,
    )
    provider = LlamaCapacityProvider(fake_devices(100), telemetry)
    provider.reservations()
    return provider


def test_managed_yield_uses_warm_18_second_threshold_not_cold_166(monkeypatch, tmp_path):
    telemetry = ResourceTelemetry(profile_path=tmp_path / "profiles.json")
    _warm_profile(telemetry)
    provider = _provider(monkeypatch, tmp_path, telemetry)
    assert asyncio.run(provider.request_yield("gpu0", YieldLevel.STOP, _request(20))) is True


def test_managed_yield_reports_threshold_and_runtime_suppression(monkeypatch, tmp_path):
    telemetry = ResourceTelemetry(profile_path=tmp_path / "profiles.json")
    _warm_profile(telemetry)
    provider = _provider(monkeypatch, tmp_path, telemetry)
    assert asyncio.run(provider.request_yield("gpu0", YieldLevel.STOP, _request(18))) is False
    assert provider.yield_wait_reason() == WaitReason.YIELD_THRASH_COST
    assert asyncio.run(provider.request_yield("gpu0", YieldLevel.STOP, _request(None))) is False
    assert provider.yield_wait_reason() == WaitReason.YIELD_RUNTIME_UNKNOWN


def test_insufficient_samples_fail_closed_and_persisted_profile_restores_decision(monkeypatch, tmp_path):
    path = tmp_path / "profiles.json"
    telemetry = ResourceTelemetry(profile_path=path)
    for cost in (80.0, 81.0):
        telemetry.record_load_measurement(
            "llama:model", process_start_sec=1, model_load_sec=cost - 1,
        )
    provider = _provider(monkeypatch, tmp_path, telemetry)
    assert asyncio.run(provider.request_yield("gpu0", YieldLevel.STOP, _request(1000))) is False
    assert provider.yield_wait_reason() == WaitReason.YIELD_LOAD_COST_UNKNOWN

    telemetry.record_load_measurement(
        "llama:model", process_start_sec=1, model_load_sec=81,
    )
    restored = ResourceTelemetry(profile_path=path)
    restored_provider = _provider(monkeypatch, tmp_path, restored)
    assert asyncio.run(restored_provider.request_yield(
        "gpu0", YieldLevel.STOP, _request(1000),
    )) is True


def test_broker_exposes_yield_suppression_as_wait_reason(monkeypatch, tmp_path):
    telemetry = ResourceTelemetry(profile_path=tmp_path / "profiles.json")
    _warm_profile(telemetry)
    provider = _provider(monkeypatch, tmp_path, telemetry)
    broker = ResourceBroker(
        fake_devices(100), ProviderRegistry([provider]), telemetry=telemetry,
    )

    async def scenario():
        status = await broker.submit(_request(None))
        for _ in range(100):
            status = await broker.request_status(status.request_id)
            if status.reason == WaitReason.YIELD_RUNTIME_UNKNOWN:
                break
            await asyncio.sleep(0.01)
        return status

    status = asyncio.run(scenario())
    assert status.state == RequestState.WAITING
    assert status.reason == WaitReason.YIELD_RUNTIME_UNKNOWN
