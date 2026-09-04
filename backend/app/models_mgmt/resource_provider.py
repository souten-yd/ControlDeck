"""Common Resource Broker adapter for the locally managed LLM runtimes.

llama.cpp と Lucebox は同じ1枚のGPUを取り合うので、ブローカーから見ると1つの
provider として扱うのが正しい。ランタイム別の差（KVプールの有無など）は
models_mgmt/local_llm.py が吸収する。
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable

from app.models_mgmt import local_llm
from app.models_mgmt.runtime_policy import get_policy
from app.resources.devices import DeviceCollection
from app.resources.providers import ProviderReservation, ResourceProvider
from app.resources.schema import ResourceRequest
from app.resources.telemetry import ResourceTelemetry




def model_is_on_local_nvme(model_path: str) -> bool:
    """Fail closed unless the model's backing block-device path is NVMe."""
    try:
        stat = os.stat(Path(model_path).resolve())
        device = Path(f"/sys/dev/block/{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}")
        return "nvme" in str(device.resolve()).lower()
    except OSError:
        return False


class LocalLlmCapacityProvider(ResourceProvider):
    """ローカル常駐LLM（llama.cpp / Lucebox）のGPU占有をブローカーへ申告する。"""

    id = "local-llm"

    def __init__(
        self,
        devices: DeviceCollection,
        telemetry: ResourceTelemetry,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._devices = devices
        self._telemetry = telemetry
        self._clock = clock
        self._resident_since: dict[str, float] = {}
        self._condition = asyncio.Condition()
        self._active_requests = 0
        self._draining = False
        self._stopping = False
        # Voice/live consumers may request a short, renewable residency hold so
        # the LLM is not unloaded between conversational turns. Holds are
        # intentionally in-memory and TTL-bound: if the consumer crashes or
        # ControlDeck restarts, they disappear without requiring orphan cleanup.
        self._hold_lock = threading.RLock()
        self._residency_holds: dict[str, tuple[str, str, float]] = {}

    def resource_request(self, alias: str, job_id: str) -> ResourceRequest:
        instance = local_llm.get_instance(alias)
        current = local_llm.find(alias) or instance
        loaded = bool(current.get("loaded"))
        model_bytes = 0
        # Lucebox はターゲット + DFlash ドラフトの2本を載せる。VLM の mmproj と同じく
        # 追加分もVRAM見積りへ入れないと、受け入れ判定が実際より甘くなる。
        for key in ("model_path", "mmproj_path", "draft_path"):
            path = str(instance.get(key) or "")
            if not path:
                continue
            try:
                model_bytes += Path(path).stat().st_size
            except OSError:
                continue
        device = self._devices.get("gpu0")
        headroom = 0 if loaded else 512 * 1024 * 1024
        peak = 0 if loaded else max(model_bytes + 2 * 1024**3, int(model_bytes * 1.75))
        if device is not None:
            peak = min(
                peak,
                max(0, device.total_bytes - device.observed_used_bytes - headroom),
            )
        return ResourceRequest.model_validate({
            "owner": f"llm:{alias}",
            "job_id": job_id,
            "device": "gpu0",
            "vram": {
                "resident_bytes": 0 if loaded else model_bytes,
                "execution_peak_bytes": peak,
                "cold_load_peak_bytes": peak,
                "headroom_bytes": headroom,
                "confidence": "measured" if loaded else "low",
            },
            "compute_mode": "endpoint-managed",
            "priority": 100,
            "class": "interactive",
            "residency_key": local_llm.residency_key({**instance, "runtime": current.get("runtime", "")}),
            "max_wait_sec": 300,
            "on_insufficient": "queue",
        })

    def _cleanup_residency_holds(self) -> None:
        now = self._clock()
        with self._hold_lock:
            for hold_id, (_owner, _key, expires_at) in list(self._residency_holds.items()):
                if expires_at <= now:
                    self._residency_holds.pop(hold_id, None)

    def create_residency_hold(self, residency_key: str, owner: str, *, ttl_seconds: float) -> str:
        ttl = max(10.0, float(ttl_seconds))
        hold_id = f"hold:{uuid.uuid4().hex}"
        with self._hold_lock:
            self._cleanup_residency_holds()
            self._residency_holds[hold_id] = (owner, residency_key, self._clock() + ttl)
        self._telemetry.record("residency.hold.created", reason="consumer_session")
        return hold_id

    def renew_residency_hold(self, hold_id: str, owner: str, *, ttl_seconds: float) -> bool:
        ttl = max(10.0, float(ttl_seconds))
        with self._hold_lock:
            self._cleanup_residency_holds()
            current = self._residency_holds.get(hold_id)
            if current is None or current[0] != owner:
                return False
            self._residency_holds[hold_id] = (current[0], current[1], self._clock() + ttl)
        return True

    def release_residency_hold(self, hold_id: str, owner: str) -> bool:
        with self._hold_lock:
            self._cleanup_residency_holds()
            current = self._residency_holds.get(hold_id)
            if current is None or current[0] != owner:
                return False
            self._residency_holds.pop(hold_id, None)
        self._telemetry.record("residency.hold.released", reason="consumer_session")
        return True

    def has_residency_hold(self, residency_key: str | None = None) -> bool:
        with self._hold_lock:
            self._cleanup_residency_holds()
            if residency_key is None:
                return bool(self._residency_holds)
            return any(key == residency_key for _owner, key, _expires in self._residency_holds.values())

    def reservations(self) -> list[ProviderReservation]:
        running = [item for item in local_llm.list_instances() if item.get("loaded")]
        now = self._clock()
        aliases = {str(item.get("alias") or "llama") for item in running}
        self._resident_since = {
            alias: self._resident_since.get(alias, now) for alias in aliases
        }
        if not running:
            return []
        device = self._devices.get("gpu0")
        observed = device.observed_used_bytes if device is not None else 0
        sizes = []
        for item in running:
            try:
                sizes.append(Path(str(item.get("model_path") or "")).stat().st_size)
            except OSError:
                sizes.append(0)
        total_size = sum(sizes)
        reserved_total = max(observed, total_size)
        values = []
        for item, size in zip(running, sizes, strict=True):
            alias = str(item.get("alias") or "llama")
            share = (
                int(reserved_total * size / total_size)
                if total_size > 0 else int(reserved_total / len(running))
            )
            values.append(ProviderReservation(
                provider_id=self.id,
                device_id="gpu0",
                owner=f"llm:{alias}",
                reserved_bytes=share,
                residency_key=local_llm.residency_key(item),
                draining=self._draining,
            ))
        return values

    async def await_capacity(
        self, alias: str, port: int, needed_tokens: int, *, timeout_seconds: float
    ) -> dict:
        return await local_llm.await_capacity(
            alias, port, needed_tokens, timeout_seconds=timeout_seconds
        )

    async def enter_request(self) -> None:
        async with self._condition:
            if self._draining and not self._stopping:
                self._draining = False
                self._telemetry.record("release.drain_canceled", reason="llm_request")
            while self._stopping:
                await self._condition.wait()
            self._active_requests += 1
            self._condition.notify_all()

    async def leave_request(self) -> None:
        async with self._condition:
            self._active_requests = max(0, self._active_requests - 1)
            self._condition.notify_all()


    async def release_on_request(self, *, include_helpers: bool = False) -> tuple[bool, str, int]:
        """Honour an explicit "my AI turn is over" declaration from a consumer.

        This is the same decision the idle-unload loop makes after 30 minutes,
        triggered by a request instead of by a clock. It reuses the identical
        in-use guards, so ControlDeck chat, an OpenCode session, and another
        add-on cannot have the shared model pulled out from under them.

これは利用者からの明示的な解放要求で、自動退避とは別物:
        those exist to stop involuntary preemption from thrashing, and a
        voluntary hand-back is not preemption.

        The drain below is the guarantee that matters: no running inference is
        ever cut, whether it belongs to ControlDeck chat, an OpenCode session,
        or another add-on. What an explicit release deliberately does not
        honour is the idle loop's 30-minute recency window — see
        llama.release_reason for why keeping it would make the capability
        useless on a single GPU.
        """
        policy = get_policy()
        if self.has_residency_hold():
            return False, "residency_held", 0
        if not policy.gateway_only:
            return False, "release_not_permitted_by_policy", 0
        async with self._condition:
            if self._stopping:
                return False, "already_stopping", 0
            self._draining = True
            deadline = asyncio.get_running_loop().time() + policy.drain_timeout_sec
            while self._active_requests and self._draining:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self._draining = False
                    self._condition.notify_all()
                    return False, "drain_timeout", 0
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    self._draining = False
                    self._condition.notify_all()
                    return False, "drain_timeout", 0
            if not self._draining:
                return False, "in_use", 0
            self._stopping = True
        try:
            from app.models_mgmt import llama

            released, reason, freed = await llama.release_loaded_llms(
                include_helpers=include_helpers
            )
        finally:
            async with self._condition:
                self._stopping = False
                self._draining = False
                self._condition.notify_all()
        if released and freed:
            self._telemetry.record("release.explicit", reason="consumer_request")
        return released, reason, freed


_provider: LocalLlmCapacityProvider | None = None


def provider() -> LocalLlmCapacityProvider:
    global _provider
    if _provider is None:
        from app.resources.broker import broker

        _provider = LocalLlmCapacityProvider(broker.devices, broker.telemetry)
    return _provider
