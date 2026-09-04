from __future__ import annotations

import asyncio

from app.models_mgmt import llama
from app.models_mgmt.resource_provider import LocalLlmCapacityProvider
from app.models_mgmt.runtime_policy import RuntimePolicy
from app.jobs import service as jobs
from app.resources import broker as broker_module
from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.probes import ProviderRegistry
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
    provider = LocalLlmCapacityProvider(fake_devices(100), telemetry)
    provider.reservations()
    return provider






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


