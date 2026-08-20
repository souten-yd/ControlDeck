from __future__ import annotations

import asyncio

from app.models_mgmt import llama
from app.models_mgmt.resource_provider import LlamaCapacityProvider
from app.models_mgmt.runtime_policy import RuntimePolicy
from app.jobs import service as jobs
from app.resources import broker as broker_module
from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.probes import ProviderRegistry
from app.resources.providers import YieldLevel
from app.resources.schema import RequestStatus, ResourceRequest, RequestState, WaitReason
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
        await broker.expire_due()
        status = await broker.request_status(status.request_id)
        return status

    status = asyncio.run(scenario())
    assert status.state == RequestState.WAITING
    assert status.reason == WaitReason.YIELD_RUNTIME_UNKNOWN


def test_successful_llama_stop_marks_the_next_load_warm(monkeypatch, tmp_path):
    telemetry = ResourceTelemetry(profile_path=tmp_path / "profiles.json")
    instance = {"alias": "chat", "model_path": str(tmp_path / "model.gguf"), "loaded": True}
    monkeypatch.setattr(broker_module.broker, "telemetry", telemetry)
    monkeypatch.setattr(llama, "get_config", lambda: {"selected_alias": "chat"})
    monkeypatch.setattr(llama, "get_instance", lambda _alias: instance)
    monkeypatch.setattr(llama, "list_instances", lambda: [instance])
    monkeypatch.setattr(llama, "residency_key", lambda _item: "llama:model")
    monkeypatch.setattr("app.applications.systemd.stop", lambda _unit: (True, ""))

    assert llama.stop_instance("chat") == (True, "")
    telemetry.record_load_measurement(
        "llama:model", process_start_sec=1, model_load_sec=7,
    )
    profile = telemetry.snapshot()["load_profiles"][0]
    assert profile["warm_reload_cost_sec"]["count"] == 1
    assert profile["cold_load_cost_sec"]["count"] == 0


def test_waiting_job_mirrors_broker_suppression_reason(monkeypatch):
    class BrokerStub:
        def __init__(self):
            self.revision = 0
            self.reason = WaitReason.INSUFFICIENT_VRAM
            self.changed = asyncio.Event()
            self.done = asyncio.Event()

        async def wait(self, _request_id):
            await self.done.wait()
            return RequestStatus(
                request_id="request", state=RequestState.CANCELED,
                owner="addon:test", job_id="job", reason=self.reason,
                requested_at=1, deadline_at=2,
            )

        async def wait_for_revision(self, previous, _timeout):
            while self.revision <= previous:
                await self.changed.wait()
                self.changed.clear()
            return self.revision

        async def request_status(self, _request_id):
            return RequestStatus(
                request_id="request", state=RequestState.WAITING,
                owner="addon:test", job_id="job", reason=self.reason,
                requested_at=1, deadline_at=2,
            )

    monkeypatch.setattr(jobs, "_db_write", lambda *_args, **_kwargs: None)

    async def scenario():
        broker = BrokerStub()
        job = jobs.Job(id="job", kind="test", title="test")
        task = asyncio.create_task(jobs._wait_for_resource_updates(job, broker, "request"))
        await asyncio.sleep(0)
        broker.reason = WaitReason.YIELD_RUNTIME_UNKNOWN
        broker.revision += 1
        broker.changed.set()
        for _ in range(100):
            if job.wait_reason == WaitReason.YIELD_RUNTIME_UNKNOWN.value:
                break
            await asyncio.sleep(0.01)
        broker.done.set()
        broker.revision += 1
        broker.changed.set()
        await task
        return job.wait_reason

    assert asyncio.run(scenario()) == WaitReason.YIELD_RUNTIME_UNKNOWN.value
