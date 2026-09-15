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


def minimum_request(owner: str, job: str, required: int, minimum: int) -> ResourceRequest:
    """「占有したいが、これだけあれば動ける」と申告する要求。"""
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
            "minimum_bytes": minimum,
        },
        "compute_mode": "exclusive-preferred",
        "priority": 0,
        "class": "interactive",
        "max_wait_sec": 300,
        "on_insufficient": "queue",
    })


def test_preferred_with_a_floor_shares_instead_of_waiting_for_the_llm_to_leave():
    """preferred は required ではない。

    「占有できるなら占有したいが、無理なら小さい枠でも動ける」と申告した要求が、
    device に誰か居るというだけで下限の判定にすら進めなかった。minimum_bytes は
    exclusive-preferred からは到達できない死んだ枝だった。

    実測 2026-09-15: LLM が 22.9GiB を持った状態で音楽生成を頼むと
    device_busy_exclusive のまま 5 分 35 秒待ち、LLM が退いてから動き出した。
    空きは 9.9GiB あり、要求は 8.47GiB で動けると申告していた。待つ必要は無い。
    """
    llm = ResidentLLM(70, releases=False)  # 使用中。退かない。
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([llm]))

    async def scenario():
        submitted = await broker.submit(minimum_request("addon:sonic-forge", "music", 80, 20))
        await asyncio.sleep(0.05)
        return await broker.request_status(submitted.request_id)

    settled = asyncio.run(scenario())
    assert settled.state == RequestState.GRANTED, "動ける大きさの空きがあるのに待たせている"
    # 貸すのは空いているぶんだけ。全部載る量を貸したことにしてはいけない。
    assert settled.granted_bytes == 30, settled.granted_bytes
    assert llm.reservations(), "同居できるのだから LLM を降ろす必要は無い"


def test_preferred_without_a_floor_still_waits_for_the_device():
    """下限を言っていない要求は、これまでどおり占有しか受けない。

    小さい枠で動けるとは言っていないので、同居させるとその要求が OOM で落ちる。
    """
    llm = ResidentLLM(70, releases=False)
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([llm]))

    async def scenario():
        submitted = await broker.submit(request("addon:sonic-forge", "music", 20))
        await asyncio.sleep(0.05)
        return await broker.request_status(submitted.request_id)

    settled = asyncio.run(scenario())
    assert settled.state == RequestState.WAITING
    assert settled.reason == WaitReason.DEVICE_BUSY_EXCLUSIVE


def test_a_floor_larger_than_the_free_space_still_waits():
    """動けない大きさで通してはいけない。通せば要求側が OOM で落ちる。"""
    llm = ResidentLLM(90, releases=False)
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([llm]))

    async def scenario():
        submitted = await broker.submit(minimum_request("addon:sonic-forge", "music", 80, 20))
        await asyncio.sleep(0.05)
        return await broker.request_status(submitted.request_id)

    settled = asyncio.run(scenario())
    assert settled.state == RequestState.WAITING, "空き 10 に下限 20 は入らない"
