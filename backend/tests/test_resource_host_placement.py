"""VRAMが空いていればVRAM、無ければRAM（host）へ流す配置。

画像生成のような計算律速の処理は、VRAMを奪ってLLMのKVを追い出すより、
RAMへ載せた方が全体として速い。LLMはRAMでは実用にならないので gpu0 に固定する。
"""
import asyncio

from app.resources.broker import ResourceBroker
from app.resources.devices import DeviceCollection, ResourceDevice
from app.resources.schema import RequestState

GB = 1024 ** 3


def _devices(gpu_total, gpu_used, host_total=32 * GB, host_used=0):
    return DeviceCollection([
        ResourceDevice(id="gpu0", name="GPU", total_bytes=gpu_total,
                       observed_used_bytes=gpu_used),
        ResourceDevice(id="host", name="System RAM", total_bytes=host_total,
                       observed_used_bytes=host_used, kind="host"),
    ])


def _request(**patch):
    body = {
        "owner": "addon:media-forge", "job_id": "job1", "device": "auto",
        "preferred_devices": ["gpu0", "host"],
        "vram": {"resident_bytes": 6 * GB, "execution_peak_bytes": 6 * GB,
                 "cold_load_peak_bytes": 6 * GB, "headroom_bytes": 0,
                 "confidence": "measured"},
        "compute_mode": "shared-safe", "class": "background", "on_insufficient": "fail_fast",
    }
    body.update(patch)
    from app.resources.schema import ResourceRequest

    return ResourceRequest.model_validate(body)


def _grant(devices, request):
    broker = ResourceBroker(devices)
    return asyncio.run(broker.submit(request))


def test_vram_is_used_when_it_fits():
    status = _grant(_devices(32 * GB, 2 * GB), _request())
    assert status.state == RequestState.GRANTED
    assert status.device_id == "gpu0"


def test_falls_back_to_host_when_vram_is_full():
    """LLMがVRAMを埋めていても、画像生成はRAMへ載って共存する。"""
    status = _grant(_devices(32 * GB, 30 * GB), _request())
    assert status.state == RequestState.GRANTED
    assert status.device_id == "host"


def test_preferred_order_wins_over_free_bytes():
    """RAMの方が空きが大きくても、VRAMが空いていればVRAMを選ぶ。"""
    status = _grant(_devices(32 * GB, 2 * GB, host_total=128 * GB), _request())
    assert status.device_id == "gpu0"


def test_host_is_opt_in():
    """host を挙げていない要求へ黙って割り当てない。

    GPUを使うつもりの利用者がRAM配置をもらうと、結局VRAMを二重に取りにいく。
    """
    status = _grant(_devices(32 * GB, 30 * GB), _request(preferred_devices=[]))
    assert status.state != RequestState.GRANTED


def test_llm_pins_itself_to_the_gpu(monkeypatch):
    """LLMはメモリ帯域律速で、RAM配置では実用にならない。gpu0 に固定する。"""
    from app.models_mgmt import local_llm, resource_provider
    from app.resources.telemetry import ResourceTelemetry

    monkeypatch.setattr(local_llm, "get_instance", lambda _alias: {"model_path": "/m/a.gguf"})
    monkeypatch.setattr(local_llm, "find", lambda _alias: {"loaded": True, "runtime": "llama.cpp"})
    provider = resource_provider.LocalLlmCapacityProvider(
        _devices(32 * GB, 2 * GB), ResourceTelemetry())
    request = provider.resource_request("qwen", "job1")
    assert request.device == "gpu0"
    assert "host" not in request.preferred_devices


def test_host_device_is_registered_from_system_ram():
    from app.resources.devices import host_device

    values = host_device()
    assert values and values[0].id == "host" and values[0].kind == "host"
    assert values[0].total_bytes > 0
