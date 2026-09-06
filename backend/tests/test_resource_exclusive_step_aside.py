"""device を占有したい要求が、常駐物へ退去を頼めることを守る。

音楽生成は GPU を単独で使う。LLM が載っているだけで待たされ、退いてくれと
言うことすらできないと、利用者側は exclusive を諦めて shared-safe に落とす
しかない。そうすると LLM と場所を奪い合い、実際に OOM で落ちていた。

退くかどうかを決めるのは provider 側である。ここで守るのは「頼みが届くこと」と
「同居して構わない要求の扱いは変わらないこと」の2つ。
"""
from __future__ import annotations

import asyncio

from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from app.resources.probes import ProviderRegistry
from app.resources.providers import ProviderReservation, ResourceProvider
from app.resources.schema import RequestState, ResourceRequest, WaitReason


def request(owner: str, job: str, required: int, *, mode: str = "exclusive-preferred") -> ResourceRequest:
    return ResourceRequest.model_validate({
        "owner": owner,
        "job_id": job,
        "device": "auto",
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
        "max_wait_sec": 300,
        "on_insufficient": "queue",
    })


class ResidentLLM(ResourceProvider):
    """lease を持たずに場所を抱える常駐物。LLM の立ち位置を模す。"""

    can_step_aside = True

    def __init__(self, reserved: int, *, releases: bool):
        self.id = "llm"
        self._reserved = reserved
        self._releases = releases
        self.step_aside_calls = 0

    def reservations(self) -> list[ProviderReservation]:
        if self._reserved <= 0:
            return []
        return [ProviderReservation("llm", "gpu0", "llm:llama", self._reserved)]

    async def step_aside(self, device_id: str) -> tuple[bool, str, int]:
        self.step_aside_calls += 1
        if not self._releases:
            # 使用中の LLM は退かない。走っている推論を切らないため。
            return False, "in_use", 0
        freed, self._reserved = self._reserved, 0
        return True, "released", freed


def test_exclusive_request_asks_the_resident_llm_to_step_aside():
    """空きは足りているのに exclusive というだけで塞がれていた経路。"""
    llm = ResidentLLM(40, releases=True)
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([llm]))

    async def scenario():
        first = await broker.submit(request("addon:sonic-forge", "music", 30))
        # 退去は lock の外の task で走る。終わるのを待ってから再スケジュールを見る。
        for _ in range(50):
            await asyncio.sleep(0.01)
            if llm.step_aside_calls:
                break
        await asyncio.sleep(0.05)
        return first, await broker.request_status(first.request_id)

    _first, settled = asyncio.run(scenario())
    # 直前まで LLM が 40 を抱えており、exclusive なので空きバイトに関係なく塞がれる。
    # 退去を頼めて初めて通る。頼まずに通ったのなら、それは占有できていない。
    assert llm.step_aside_calls == 1, "退いてくれと頼めていない"
    assert settled.state == RequestState.GRANTED, "退いた後も通らないなら意味がない"
    assert llm.reservations() == [], "LLM が場所を抱えたままになっている"


def test_exclusive_request_keeps_waiting_when_the_llm_is_in_use():
    """退くかどうかは provider が決める。使用中なら待つ。"""
    llm = ResidentLLM(40, releases=False)
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([llm]))

    async def scenario():
        first = await broker.submit(request("addon:sonic-forge", "music", 30))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if llm.step_aside_calls:
                break
        return await broker.request_status(first.request_id)

    settled = asyncio.run(scenario())
    assert llm.step_aside_calls >= 1
    assert settled.state == RequestState.WAITING, "使用中の LLM を取り上げてはいけない"


def test_shared_requests_are_unaffected_and_do_not_evict_the_llm():
    """同居して構わない要求の扱いは変えない。ASR/TTS はここを通る。"""
    llm = ResidentLLM(40, releases=True)
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([llm]))

    async def scenario():
        granted = await broker.submit(request("addon:sonic-forge", "asr", 20, mode="shared-safe"))
        await asyncio.sleep(0.05)
        return granted

    granted = asyncio.run(scenario())
    assert granted.state == RequestState.GRANTED, "空きに収まるなら同居のまま通る"
    assert llm.step_aside_calls == 0, "同居できる要求で LLM を降ろしてはいけない"
