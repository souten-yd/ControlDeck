from __future__ import annotations

import asyncio
import time

from app.models_mgmt import llama
from app.models_mgmt.resource_provider import LocalLlmCapacityProvider
from app.models_mgmt.runtime_policy import RuntimePolicy
from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
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
    provider = LocalLlmCapacityProvider(fake_devices(8 * 1024**3), ResourceTelemetry())

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
    request = LocalLlmCapacityProvider(devices, ResourceTelemetry()).resource_request(
        "vision", "gateway-vision",
    )

    assert request.vram.resident_bytes == 2 * 1024**3 + 256 * 1024**2
    assert request.vram.required_bytes == total - observed
    assert request.vram.required_bytes <= devices.snapshots()[0].admitted_free_bytes





def test_gateway_lease_helper_activates_renews_and_releases(monkeypatch):
    from app.models_mgmt import gateway, resource_provider
    from app.resources import broker as broker_module

    broker = ResourceBroker(fake_devices(100))
    adapter = LocalLlmCapacityProvider(broker.devices, broker.telemetry)
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
    adapter = LocalLlmCapacityProvider(broker.devices, broker.telemetry)
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


# ── 自分の接続で自分を縛らない ──────────────────────────────────────────
#
# 実測: AI アシスタントで 1 往復した後、解放が常に clients_connected で拒否
# され、画像生成が insufficient_capacity で落ちていた。HTTP client は応答後も
# 接続を保持するので、接続の有無だけを見ると「1 度でもチャットしたら二度と
# 降ろせない」になる。ControlDeck 自身の要求は release_on_request が drain で
# 待つので、ここで重ねて見る必要がない。見るのは外から来ている人だけである。

def test_our_own_connection_does_not_pin_the_model(monkeypatch):
    import os

    from app.models_mgmt import llama

    class Connection:
        def __init__(self, pid, raddr_port):
            self.status = "ESTABLISHED"
            self.laddr = type("A", (), {"port": 40000})()
            self.raddr = type("A", (), {"port": raddr_port})()
            self.pid = pid

    class FakePsutil:
        CONN_ESTABLISHED = "ESTABLISHED"

        class Error(Exception):
            pass

        @staticmethod
        def net_connections(kind):
            return FakePsutil.connections

        class Process:
            def __init__(self, *a):
                pass

            def children(self, recursive=False):
                return []

    monkeypatch.setitem(__import__("sys").modules, "psutil", FakePsutil)

    FakePsutil.connections = [Connection(os.getpid(), 8097)]
    assert llama._has_connected_clients(8097) is False, "自分の接続で降ろせなくなっている"

    FakePsutil.connections = [Connection(os.getpid() + 99999, 8097)]
    assert llama._has_connected_clients(8097) is True, "外部 client を守れていない"

    # pid が読めない接続は外部として扱う。読めないことを「自分のものだ」と
    # 解釈すると、他人が使っている model を降ろしてしまう。
    FakePsutil.connections = [Connection(None, 8097)]
    assert llama._has_connected_clients(8097) is True


def test_the_server_side_socket_is_not_what_gets_inspected(monkeypatch):
    """laddr.port == port の socket の pid は常に llama 自身で、誰が繋いで
    いるかを何も語らない。見るのは client 側（raddr.port == port）である。"""
    source = (
        __import__("pathlib").Path(__file__).parents[1]
        / "app" / "models_mgmt" / "llama.py"
    ).read_text(encoding="utf-8")
    guard = source[source.index("def _has_connected_clients"):]
    guard = guard[:guard.index("\ndef ")]
    assert "connection.raddr" in guard and "connection.raddr.port == port" in guard
    assert "connection.laddr.port == port" not in guard
