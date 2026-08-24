from __future__ import annotations

import asyncio
import time

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


def test_llama_large_vision_request_includes_mmproj_and_fits_observed_free_vram(
    monkeypatch, tmp_path,
):
    from app.resources.devices import DeviceCollection, ResourceDevice

    model = tmp_path / "model.gguf"
    projector = tmp_path / "mmproj.gguf"
    with model.open("wb") as stream:
        stream.truncate(2 * 1024**3)
    with projector.open("wb") as stream:
        stream.truncate(256 * 1024**2)
    instance = {
        "alias": "vision", "model_path": str(model), "mmproj_path": str(projector),
        "port": 8090,
    }
    monkeypatch.setattr(llama, "get_instance", lambda alias: instance)
    monkeypatch.setattr(llama, "residency_key", lambda item: "llama:vision")
    monkeypatch.setattr(llama, "list_instances", lambda: [{**instance, "loaded": False}])
    total = 3 * 1024**3
    observed = 64 * 1024**2
    devices = DeviceCollection([
        ResourceDevice(id="gpu0", name="GPU", total_bytes=total, observed_used_bytes=observed),
    ])
    request = LlamaCapacityProvider(devices, ResourceTelemetry()).resource_request(
        "vision", "gateway-vision",
    )

    assert request.vram.resident_bytes == 2 * 1024**3 + 256 * 1024**2
    assert request.vram.required_bytes == total - observed
    assert request.vram.required_bytes <= devices.snapshots()[0].admitted_free_bytes


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


def test_managed_provider_suppresses_third_yield_inside_thrash_window(monkeypatch, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    instance = {"alias": "chat", "model_path": str(model), "loaded": True}
    monkeypatch.setattr(llama, "list_instances", lambda: [instance])
    monkeypatch.setattr(llama, "residency_key", lambda item: "llama:model")
    monkeypatch.setattr(llama, "stop_instance", lambda alias: (True, ""))
    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.get_policy",
        lambda: RuntimePolicy(supervision="managed", min_uptime_sec=0),
    )
    monkeypatch.setattr(
        "app.models_mgmt.resource_provider.model_is_on_local_nvme", lambda path: True
    )
    telemetry = ResourceTelemetry()
    telemetry.record_load_measurement(
        "llama:model", process_start_sec=5, model_load_sec=35
    )
    provider = LlamaCapacityProvider(fake_devices(100), telemetry)
    provider.reservations()

    async def scenario():
        return [
            await provider.request_yield(
                "gpu0", YieldLevel.STOP, media_request(str(index), runtime=1000)
            )
            for index in range(3)
        ]

    assert asyncio.run(scenario()) == [True, True, False]
    assert telemetry.snapshot()["counters"]["reason:thrash_window"] == 1


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


def test_gateway_disconnect_cancels_waiting_broker_request(monkeypatch):
    from fastapi import HTTPException

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

    class Disconnected:
        @staticmethod
        async def is_disconnected():
            return True

    async def scenario():
        held = await broker.submit(media_request("held", runtime=1000))
        try:
            await gateway._acquire_gateway_lease("chat", Disconnected())
        except HTTPException as exc:
            assert exc.status_code == 499
        statuses = await broker.request_statuses()
        await broker.release(held.lease_id)
        return statuses

    statuses = asyncio.run(scenario())
    gateway_request = next(item for item in statuses if item.owner == "llm:chat")
    assert gateway_request.state.value == "canceled"


# ── helper モデルの解放 ─────────────────────────────────────────────────
#
# 実機で「GPU が空いているのに画像生成が insufficient_capacity で落ちる」。
# bge-m3 が 1.16GB 載ったままで、画像モデルは 34.2GB のカードに 33.35GB を
# 要る。小さいことと、無害であることは別である。

def test_helpers_are_kept_by_default_and_released_only_when_asked():
    """RAG が常時使うので毎回外すのは無駄な載せ替えになる。ただし、足りないと
    分かったときにまで残すと、実際の作業が動かせない。"""
    from app.models_mgmt.llama import release_reason

    embedding = {"role": "embedding", "port": 8081}
    assert release_reason(embedding) == "not_an_llm_instance"
    # 呼び出し側が「LLM を降ろしても足りなかった」と判断したときだけ広げる
    assert release_reason(embedding, include_helpers=True) == ""

    reranker = {"role": "reranker", "port": 8082}
    assert release_reason(reranker, include_helpers=True) == ""


def test_widening_the_role_filter_does_not_bypass_the_other_guards():
    """役割を広げても、operator の「絶対に降ろすな」と接続中は依然として拒む。
    選択だけ広げて可否判定を素通りさせると、使用中のものまで落ちる。"""
    from app.models_mgmt.llama import release_reason

    assert release_reason(
        {"role": "embedding", "port": 8081, "idle_exclude": True}, include_helpers=True
    ) == "idle_excluded"
    assert release_reason({"role": "embedding", "port": 0}, include_helpers=True) == "unknown_port"
