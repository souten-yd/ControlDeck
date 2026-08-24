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


# ── いま GPU に載っているもの ───────────────────────────────────────────
#
# ホームからは VRAM の総量しか見えず、何が使っているのかが分からなかった。
# LLM だけ別扱いにすると、add-on が載せた画像・動画・音声のモデルが数字に
# 混ざったまま見えない。lease で表現できるので、個別 add-on の語彙は要らない。

def test_residents_lists_runtime_and_lease_holders_together(admin_client, monkeypatch):
    from app.resources import router as resources_router

    async def runtime_items():
        return [{
            "id": "llama:qwen", "label": "qwen", "source": "runtime",
            "owner": "llama.cpp", "role": "llm", "bytes": 0,
            "since_sec": None, "state": "active",
        }]

    async def snapshot():
        return {
            "devices": [{"id": "gpu0", "name": "test", "total_bytes": 34_000_000_000,
                         "observed_used_bytes": 18_000_000_000}],
            # granted_at は monotonic。壁時計と引き算すると 56 年経過のような
            # 値になる（実際に「496491.9時間」と表示された）。同じ時計の現在値
            # を snapshot が持つ。
            "now": 1_000.0,
            "leases": [
                {"lease_id": "lease-1", "owner": "addon:media-forge", "job_id": "job-1",
                 "device_id": "gpu0", "reserved_bytes": 18_000_000_000,
                 "state": "active", "granted_at": 970.0},
                {"lease_id": "lease-2", "owner": "addon:other", "job_id": "job-2",
                 "device_id": "gpu0", "reserved_bytes": 1_000,
                 "state": "released", "granted_at": 999.0},
            ],
        }

    monkeypatch.setattr(resources_router, "_runtime_residents", runtime_items)
    monkeypatch.setattr(resources_router.resource_broker, "snapshot", snapshot)

    body = admin_client.get("/api/v1/resources/residents").json()
    by_id = {item["id"]: item for item in body["items"]}

    assert "llama:qwen" in by_id, "runtime 側が出ていない"
    assert "lease-1" in by_id, "lease 側が出ていない"
    assert "lease-2" not in by_id, "解放済みの lease を載っているものとして出している"

    holder = by_id["lease-1"]
    assert holder["source"] == "addon"
    # 表示名は持ち主が名乗る。ControlDeck が add-on ごとの語彙を持たない。
    assert holder["label"] == "media-forge"
    assert holder["bytes"] == 18_000_000_000
    assert holder["since_sec"] == 30.0, "経過時間が別の時計で計算されている"
    assert body["devices"][0]["total_bytes"] == 34_000_000_000


def test_residents_survives_one_runtime_being_unreadable(admin_client, monkeypatch):
    """片方が読めなくても、もう片方は出す。全部黙るより悪いことはない。"""
    from app.models_mgmt import ollama
    from app.resources import router as resources_router

    async def broken():
        raise RuntimeError("ollamaが応答しない")

    monkeypatch.setattr(ollama, "running_models", broken)
    monkeypatch.setattr("app.models_mgmt.llama.list_instances",
                        lambda: [{"alias": "qwen", "loaded": True, "role": "llm"}])

    async def snapshot():
        return {"devices": [], "leases": []}

    monkeypatch.setattr(resources_router.resource_broker, "snapshot", snapshot)
    body = admin_client.get("/api/v1/resources/residents").json()
    assert [item["label"] for item in body["items"]] == ["qwen"]


def test_elapsed_time_is_omitted_when_the_clock_is_unknown(admin_client, monkeypatch):
    """どの時計かを推測して引き算すると、56 年経過のような値が画面に出る。
    分からないなら黙る。"""
    from app.resources import router as resources_router

    async def runtime_items():
        return []

    async def snapshot():
        return {"devices": [], "leases": [
            {"lease_id": "l", "owner": "addon:x", "job_id": "j", "device_id": "gpu0",
             "reserved_bytes": 1, "state": "active", "granted_at": 12345.0},
        ]}

    monkeypatch.setattr(resources_router, "_runtime_residents", runtime_items)
    monkeypatch.setattr(resources_router.resource_broker, "snapshot", snapshot)
    body = admin_client.get("/api/v1/resources/residents").json()
    assert body["items"][0]["since_sec"] is None


def test_the_broker_snapshot_publishes_its_own_clock():
    """granted_at と同じ時計の現在値が無いと、経過時間は出しようがない。"""
    import asyncio

    from app.resources.broker import broker

    snapshot = asyncio.run(broker.snapshot())
    assert "now" in snapshot and isinstance(snapshot["now"], (int, float))
