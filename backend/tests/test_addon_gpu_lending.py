"""Add-on（MediaForge / SonicForge）へ常駐 LLM が GPU を貸す（Models の「Add-on への GPU の譲り方」）。

実機で起きていたこと（2026-11-15）: Strata が GPU を埋めていると、LLM の provider は
llama.cpp しか降ろさないので「0 バイト空けた」と答え、Add-on は RAM へ流れるか待ち続けた
（画像が cpu_offload で 235 秒、GPU なら約 3 秒）。
"""
import asyncio

import pytest

from app.models_mgmt import resource_provider
from app.models_mgmt.runtime_policy import AddonGpuSettings, RuntimePolicy
from app.resources.broker import ResourceBroker
from app.resources.devices import DeviceCollection, ResourceDevice
from app.resources.schema import RequestState, ResourceRequest
from app.resources.telemetry import ResourceTelemetry

GB = 1024 ** 3


def _devices(gpu_used=30 * GB):
    return DeviceCollection([
        ResourceDevice(id="gpu0", name="GPU", total_bytes=32 * GB, observed_used_bytes=gpu_used),
        ResourceDevice(id="host", name="System RAM", total_bytes=96 * GB, observed_used_bytes=0, kind="host"),
    ])


def _request(owner="addon:media-forge", **patch):
    body = {
        "owner": owner, "job_id": "job1", "device": "auto",
        "preferred_devices": ["gpu0", "host"],
        "vram": {"resident_bytes": 20 * GB, "execution_peak_bytes": 20 * GB,
                 "cold_load_peak_bytes": 20 * GB, "headroom_bytes": 0, "confidence": "measured"},
        "host_bytes": 18 * GB,
        "compute_mode": "shared-safe", "class": "background", "on_insufficient": "fail_fast",
    }
    body.update(patch)
    return ResourceRequest.model_validate(body)


def _policy(monkeypatch, mode="auto", **addon):
    policy = RuntimePolicy(selected_runtime="strata", addon_gpu=AddonGpuSettings(mode=mode, **addon))
    monkeypatch.setattr(resource_provider, "get_policy", lambda: policy)
    return policy


@pytest.fixture
def provider(monkeypatch):
    """Strata だけが載っている状態（llama.cpp は載っていない）。"""
    state = {"strata_loaded": True, "stopped": []}
    devices = _devices()

    def instances(runtime="", role=""):
        rows = [{"alias": "qwen-flash-next", "runtime": "strata", "role": "llm",
                 "loaded": state["strata_loaded"], "port": 8096}]
        return [row for row in rows if not runtime or row["runtime"] == runtime]

    async def release_llama(**_kwargs):
        return True, "already_released", 0

    async def release_large():
        if not state["strata_loaded"]:
            return [], ""
        state["strata_loaded"] = False
        state["stopped"].append("qwen-flash-next")
        return [("strata", "qwen-flash-next")], ""

    from app.models_mgmt import large_model_lifecycle, llama

    monkeypatch.setattr(resource_provider.local_llm, "list_instances", instances)
    monkeypatch.setattr(llama, "release_loaded_llms", release_llama)
    monkeypatch.setattr(large_model_lifecycle, "release_for_addon", release_large)
    value = resource_provider.LocalLlmCapacityProvider(devices, ResourceTelemetry())
    monkeypatch.setattr(value, "_reobserve", lambda: devices.replace([
        ResourceDevice(id="gpu0", name="GPU", total_bytes=32 * GB,
                       observed_used_bytes=30 * GB if state["strata_loaded"] else GB),
        ResourceDevice(id="host", name="System RAM", total_bytes=96 * GB, observed_used_bytes=0, kind="host"),
    ]))
    value.state = state
    value.devices = devices
    return value


def test_a_resident_strata_is_released_for_an_addon(monkeypatch, provider):
    """llama.cpp だけを見ていたので、Strata が載っていると 0 バイトで断っていた。"""
    _policy(monkeypatch, "auto")
    released, reason, freed = asyncio.run(provider.step_aside("gpu0", request=_request()))
    assert released and reason == "released" and freed > 0
    assert provider.state["stopped"] == ["qwen-flash-next"]
    assert provider.lending()
    assert provider.lending_state()["lent"] == [{"runtime": "strata", "alias": "qwen-flash-next"}]


def test_llm_first_never_releases(monkeypatch, provider):
    _policy(monkeypatch, "llm_first")
    released, reason, _ = asyncio.run(provider.step_aside("gpu0", request=_request()))
    assert (released, reason) == (False, "llm_first")
    assert provider.state["stopped"] == []
    assert not provider.would_step_aside("gpu0", _request())


def test_auto_does_not_wait_for_a_busy_llm(monkeypatch, provider):
    """auto は推論中の LLM を待たない（OpenCode のサブエージェントを止めない）。"""
    _policy(monkeypatch, "auto")
    provider._active_requests = 1
    assert not provider.would_step_aside("gpu0", _request())
    released, reason, _ = asyncio.run(provider.step_aside("gpu0", request=_request()))
    assert (released, reason) == (False, "in_use")


def test_swap_waits_for_the_running_inference_and_holds_new_ones(monkeypatch, provider):
    """swap: 走っている推論は終わるまで待ち、その間に来た要求で取り消さない。"""
    _policy(monkeypatch, "swap")

    async def scenario():
        await provider.enter_request()                    # 推論中の 1 本
        assert provider.would_step_aside("gpu0", _request())
        lend = asyncio.create_task(provider.step_aside("gpu0", request=_request()))
        await asyncio.sleep(0.05)
        newcomer = asyncio.create_task(provider.enter_request())   # 新しく来た要求
        await asyncio.sleep(0.05)
        assert not newcomer.done(), "新しい要求は drain を取り消さずに待つ"
        await provider.leave_request()                    # 推論が終わる
        released, _, _ = await asyncio.wait_for(lend, 2)
        assert released and provider.lending()
        await asyncio.sleep(0.05)
        assert not newcomer.done(), "貸している間は LLM を起こさない"
        await provider.return_gpu(restore=False)
        await asyncio.wait_for(newcomer, 3)
        await provider.leave_request()

    asyncio.run(scenario())


def test_an_llm_request_does_not_release_the_large_runtime(monkeypatch, provider):
    """別の LLM のために大きなランタイムを降ろすのは prepare_switch の仕事。"""
    _policy(monkeypatch, "auto")
    _, _, freed = asyncio.run(provider.step_aside("gpu0", request=_request(owner="llm:qwen38")))
    assert provider.state["stopped"] == []
    assert not provider.lending()
    assert freed == 0


def test_broker_lends_the_gpu_before_placing_on_ram(monkeypatch, provider):
    """RAM が空いていても、LLM が暇なら先に GPU を借りる。"""
    _policy(monkeypatch, "auto")
    broker = ResourceBroker(provider.devices)
    broker.providers.register(provider)
    status = asyncio.run(broker.submit(_request()))
    assert status.state == RequestState.GRANTED
    assert status.device_id == "gpu0"
    assert provider.state["stopped"] == ["qwen-flash-next"]


def test_broker_places_on_ram_when_the_llm_keeps_the_gpu(monkeypatch, provider):
    _policy(monkeypatch, "llm_first")
    broker = ResourceBroker(provider.devices)
    broker.providers.register(provider)
    status = asyncio.run(broker.submit(_request()))
    assert status.state == RequestState.GRANTED
    assert status.device_id == "host"
    assert provider.state["stopped"] == []


def test_the_gpu_comes_back_after_the_addon_is_done(monkeypatch, provider):
    _policy(monkeypatch, "auto", restore_delay_sec=0)
    restored = []

    async def fake_restore(alias):
        restored.append(alias)

    monkeypatch.setattr(provider, "_restore", fake_restore)

    class _Broker:
        active = True

        async def owner_active(self, prefix, device_id):
            return self.active

    fake = _Broker()
    import app.resources.broker as broker_module

    monkeypatch.setattr(broker_module, "broker", fake)

    async def scenario():
        await provider.step_aside("gpu0", request=_request())
        await provider.lending_tick()
        assert provider.lending(), "生成中は返さない"
        fake.active = False
        await provider.lending_tick()                     # 静かになった時刻を記録
        await provider.lending_tick()
        assert not provider.lending()

    asyncio.run(scenario())
    assert restored == ["qwen-flash-next"]


def test_a_waiting_llm_request_gets_the_gpu_back_without_the_delay(monkeypatch, provider):
    _policy(monkeypatch, "auto", restore_delay_sec=600)
    restored = []

    async def fake_restore(alias):
        restored.append(alias)

    monkeypatch.setattr(provider, "_restore", fake_restore)

    class _Broker:
        async def owner_active(self, prefix, device_id):
            return False

    import app.resources.broker as broker_module

    monkeypatch.setattr(broker_module, "broker", _Broker())

    async def scenario():
        await provider.step_aside("gpu0", request=_request())
        waiter = asyncio.create_task(provider.enter_request())
        await asyncio.sleep(0.05)
        await provider.lending_tick()
        await asyncio.wait_for(waiter, 3)
        await provider.leave_request()

    asyncio.run(scenario())
    assert restored == [], "待っている要求が自分で載せる"


def test_lending_expires(monkeypatch, provider):
    _policy(monkeypatch, "auto", max_lend_sec=60)
    now = [1000.0]
    provider._clock = lambda: now[0]
    asyncio.run(provider.step_aside("gpu0", request=_request()))
    assert provider.lending()
    now[0] += 61
    assert not provider.lending()
