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


def test_ram_uses_its_own_figure_not_the_vram_envelope():
    """同じモデルでも置き場所で必要量が違う。

    vram の見積りは device_map で段階的に載せるときのGPU側ピークで、RAM配置の
    実態とは別物である。実測: FLUX.2 Klein 4B は VRAM 31.1GB の申告に対し、
    CPU実行の最大RSSが16.3GB（512x512/4歩、2026-09-04）。VRAMの数字をRAMに
    当てると、30GBの機械では host が永久に grant されない。
    """
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 29 * GB,
              "cold_load_peak_bytes": 30 * GB, "headroom_bytes": GB,
              "confidence": "measured"},
        host_bytes=17 * GB,
    )

    status = _grant(_devices(32 * GB, 30 * GB, host_total=30 * GB), request)

    assert status.state == RequestState.GRANTED
    assert status.device_id == "host"


def test_ram_without_its_own_figure_falls_back_to_the_vram_envelope():
    """申告が無ければ従来どおり。黙って小さく見積もらない。"""
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 29 * GB,
              "cold_load_peak_bytes": 30 * GB, "headroom_bytes": GB,
              "confidence": "measured"},
    )

    status = _grant(_devices(32 * GB, 30 * GB, host_total=30 * GB), request)

    assert status.state != RequestState.GRANTED


def test_vram_placement_ignores_the_ram_figure():
    """RAM の数字で VRAM を受理しない。小さい方を使うと OOM する。"""
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 29 * GB,
              "cold_load_peak_bytes": 30 * GB, "headroom_bytes": GB,
              "confidence": "measured"},
        host_bytes=17 * GB,
    )

    status = _grant(_devices(24 * GB, 0, host_total=8 * GB), request)

    # gpu0 は 31GB 要るのに 24GB しかない。host は 8GB で 17GB に足りない。
    # RAM の 17GB を VRAM に当ててしまうと、ここが gpu0 で通ってしまう。
    assert status.state != RequestState.GRANTED


def test_a_ram_figure_without_asking_for_ram_is_refused():
    """使われない申告を黙って受け取らない。効かないことに気づけなくなる。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _request(preferred_devices=[], host_bytes=17 * GB)


def test_ram_is_not_lent_to_the_last_byte():
    """OSと他プロセスのための余白を残す。

    VRAMは物理的に上限で頭打ちになるが、RAMはswapがあるぶん「入ったことになって
    全体が遅くなる」という壊れ方をする。実測（2026-09-05）: total 30.4GiB /
    available 18.3GiB で既に swap を 4.6GB 使っており、llama-server はVRAMとは別に
    ホスト側で 6.9GB を持っていた。ここを使い切る判断をさせない。
    """
    from app.resources import devices as device_module

    values = device_module.host_device()

    assert values
    host = values[0]
    lendable = host.total_bytes - host.observed_used_bytes
    assert host.total_bytes > 0
    # total は偽らない。余白は「使用中」として数える。
    assert lendable <= host.total_bytes - device_module.HOST_RESERVE_BYTES


def test_a_machine_with_no_spare_ram_lends_nothing(monkeypatch):
    """余白を割り込んだら貸さない。負の空きを作らない。"""
    import psutil

    from app.resources import devices as device_module

    class Memory:
        total = 8 * GB
        available = 1 * GB

    monkeypatch.setattr(psutil, "virtual_memory", lambda: Memory())
    host = device_module.host_device()[0]

    assert host.total_bytes - host.observed_used_bytes == 0


def test_the_leftover_is_lent_when_the_whole_thing_does_not_fit():
    """全常駐が入らなくても、下限を満たせるなら空きぶんを貸す。

    実測（2026-09-05、FLUX.2 Klein 4B / 1024²）: 全常駐 21.9GiB で 2.98秒、
    枠 8GiB で 6.7秒、枠 7GiB では OOM。残りを貸せないと純CPUの 113秒に落ちる。
    """
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 20 * GB,
              "cold_load_peak_bytes": 20 * GB, "headroom_bytes": GB,
              "minimum_bytes": 8 * GB, "confidence": "measured"},
        host_bytes=18 * GB,
    )

    # LLM が 21.4GiB 載っていて、残りは 10.4GiB。
    status = _grant(_devices(int(31.86 * GB), int(21.42 * GB)), request)

    assert status.state == RequestState.GRANTED
    assert status.device_id == "gpu0"
    assert status.granted_bytes is not None
    assert 8 * GB <= status.granted_bytes < 21 * GB


def test_a_budget_below_the_floor_is_refused():
    """下限を割る枠では動かない。貸しても OOM で落ちるだけである。"""
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 20 * GB,
              "cold_load_peak_bytes": 20 * GB, "headroom_bytes": GB,
              "minimum_bytes": 8 * GB, "confidence": "measured"},
        preferred_devices=[],
    )

    # 空きは 5GiB しかない。
    status = _grant(_devices(int(31.86 * GB), int(26.86 * GB)), request)

    assert status.state != RequestState.GRANTED


def test_a_request_without_a_floor_still_needs_the_whole_thing():
    """下限を申告しない要求の意味は変えない。黙って小さく貸さない。"""
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 20 * GB,
              "cold_load_peak_bytes": 20 * GB, "headroom_bytes": GB,
              "confidence": "measured"},
        preferred_devices=[],
    )

    status = _grant(_devices(int(31.86 * GB), int(21.42 * GB)), request)

    assert status.state != RequestState.GRANTED


def test_a_full_card_lends_the_whole_requirement_not_more():
    """空いていれば全常駐ぶんを貸す。空き全部を予約して他を締め出さない。"""
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 20 * GB,
              "cold_load_peak_bytes": 20 * GB, "headroom_bytes": GB,
              "minimum_bytes": 8 * GB, "confidence": "measured"},
        preferred_devices=[],
    )

    status = _grant(_devices(int(31.86 * GB), 0), request)

    assert status.state == RequestState.GRANTED
    assert status.granted_bytes == 21 * GB


def test_ram_is_never_lent_in_pieces():
    """RAM は分割して載せる先ではない。全部置ける空きが要る。"""
    request = _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 20 * GB,
              "cold_load_peak_bytes": 20 * GB, "headroom_bytes": GB,
              "minimum_bytes": 8 * GB, "confidence": "measured"},
        host_bytes=18 * GB,
    )

    # gpu0 は 3GiB しか空いておらず下限に足りない。host も 10GiB で 18GiB に届かない。
    status = _grant(_devices(int(31.86 * GB), int(28.86 * GB), host_total=int(10 * GB)),
                    request)

    assert status.state != RequestState.GRANTED


class _SteppingProvider:
    """使用中かどうかで退去の可否が変わる provider。"""

    id = "local-llm"
    can_step_aside = True

    def __init__(self, *, in_use: bool, bytes_held: int):
        self.in_use = in_use
        self.bytes_held = bytes_held
        self.asked = 0

    def reservations(self):
        from app.resources.providers import ProviderReservation

        if not self.bytes_held:
            return []
        return [ProviderReservation(provider_id=self.id, device_id="gpu0",
                                    owner="llm:qwen38", reserved_bytes=self.bytes_held,
                                    residency_key="llm:qwen38")]

    async def probe(self, request, device_id):
        from app.resources.providers import ProbeResult

        return ProbeResult(accepting=True)

    async def step_aside(self, device_id):
        self.asked += 1
        if self.in_use:
            return False, "in_use", 0
        freed, self.bytes_held = self.bytes_held, 0
        return True, "released", freed


def _big_request():
    return _request(
        vram={"resident_bytes": 0, "execution_peak_bytes": 20 * GB,
              "cold_load_peak_bytes": 20 * GB, "headroom_bytes": GB,
              "confidence": "measured"},
        preferred_devices=[],
    )


def test_an_idle_llm_steps_aside_when_there_is_nowhere_else():
    """他に置き場所が無いときだけ、使っていない LLM に退いてもらう。"""
    from app.resources.broker import ResourceBroker

    provider = _SteppingProvider(in_use=False, bytes_held=int(21.42 * GB))
    broker = ResourceBroker(_devices(int(31.86 * GB), 0))
    broker.providers.register(provider)

    status = asyncio.run(broker.submit(_big_request()))

    assert provider.asked == 1
    assert status.state == RequestState.GRANTED
    assert status.device_id == "gpu0"


def test_an_llm_in_use_is_never_pulled_out():
    """使用中なら降ろさない。実行中の推論を切らないのは provider の責任である。"""
    from app.resources.broker import ResourceBroker

    provider = _SteppingProvider(in_use=True, bytes_held=int(21.42 * GB))
    broker = ResourceBroker(_devices(int(31.86 * GB), 0))
    broker.providers.register(provider)

    status = asyncio.run(broker.submit(_big_request()))

    assert provider.asked == 1
    assert status.state != RequestState.GRANTED


def test_a_request_that_already_fits_never_asks_anyone_to_move():
    """置ける要求のために誰かを降ろさない。"""
    from app.resources.broker import ResourceBroker

    provider = _SteppingProvider(in_use=False, bytes_held=int(5 * GB))
    broker = ResourceBroker(_devices(int(31.86 * GB), 0))
    broker.providers.register(provider)

    status = asyncio.run(broker.submit(_request()))

    assert provider.asked == 0
    assert status.state == RequestState.GRANTED
