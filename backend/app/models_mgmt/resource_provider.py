"""llama.cpp adapter for the common Resource Broker."""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable

from app.models_mgmt import llama
from app.models_mgmt.runtime_policy import get_policy
from app.resources.devices import DeviceCollection
from app.resources.providers import ProviderReservation, ResourceProvider, YieldLevel
from app.resources.schema import ResourceRequest, WaitReason
from app.resources.telemetry import (
    LoadCostEstimate,
    ResourceTelemetry,
    YIELD_THRASH_FACTOR,
)


THRASH_FACTOR = YIELD_THRASH_FACTOR
THRASH_WINDOW_SEC = 300.0
THRASH_MAX_YIELDS = 2


def model_is_on_local_nvme(model_path: str) -> bool:
    """Fail closed unless the model's backing block-device path is NVMe."""
    try:
        stat = os.stat(Path(model_path).resolve())
        device = Path(f"/sys/dev/block/{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}")
        return "nvme" in str(device.resolve()).lower()
    except OSError:
        return False


class LlamaCapacityProvider(ResourceProvider):
    id = "llama.cpp"

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
        self._yield_history: deque[float] = deque(maxlen=16)
        self._condition = asyncio.Condition()
        self._active_requests = 0
        self._draining = False
        self._stopping = False
        self._last_yield_wait_reason: WaitReason | None = None
        # Voice/live consumers may request a short, renewable residency hold so
        # the LLM is not unloaded between conversational turns. Holds are
        # intentionally in-memory and TTL-bound: if the consumer crashes or
        # ControlDeck restarts, they disappear without requiring orphan cleanup.
        self._hold_lock = threading.RLock()
        self._residency_holds: dict[str, tuple[str, str, float]] = {}

    def resource_request(self, alias: str, job_id: str) -> ResourceRequest:
        instance = llama.get_instance(alias)
        current = next(
            (item for item in llama.list_instances() if str(item.get("alias")) == alias),
            instance,
        )
        loaded = bool(current.get("loaded"))
        model_bytes = 0
        for key in ("model_path", "mmproj_path"):
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
            "residency_key": llama.residency_key(instance),
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

    def _managed(self, instances: list[dict]) -> bool:
        policy = get_policy()
        return (
            policy.supervision == "managed"
            and policy.gateway_only
            and policy.yield_max_level >= int(YieldLevel.STOP)
            and bool(instances)
            and all(model_is_on_local_nvme(str(item.get("model_path") or "")) for item in instances)
        )

    def reservations(self) -> list[ProviderReservation]:
        running = [item for item in llama.list_instances() if item.get("loaded")]
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
        managed = self._managed(running)
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
                residency_key=llama.residency_key(item),
                yield_level=YieldLevel.NONE if self.has_residency_hold(llama.residency_key(item)) else (YieldLevel.STOP if managed else YieldLevel.NONE),
                draining=self._draining,
            ))
        return values

    async def await_capacity(
        self, port: int, needed_tokens: int, *, timeout_seconds: float
    ) -> dict:
        return await llama.await_capacity(
            port, needed_tokens, timeout_seconds=timeout_seconds
        )

    async def enter_request(self) -> None:
        async with self._condition:
            if self._draining and not self._stopping:
                self._draining = False
                self._telemetry.record("yield.drain_canceled", reason="llm_request")
            while self._stopping:
                await self._condition.wait()
            self._active_requests += 1
            self._condition.notify_all()

    async def leave_request(self) -> None:
        async with self._condition:
            self._active_requests = max(0, self._active_requests - 1)
            self._condition.notify_all()

    def _yield_allowed(self, request: ResourceRequest, running: list[dict]) -> bool:
        policy = get_policy()
        now = self._clock()
        if not self._managed(running):
            return False
        if any(self.has_residency_hold(llama.residency_key(item)) for item in running):
            return self._suppress(WaitReason.HELD_BY_OTHER_OWNER, "residency_hold")
        if request.estimated_runtime_sec is None:
            return self._suppress(WaitReason.YIELD_RUNTIME_UNKNOWN, "runtime_unknown")
        costs: list[LoadCostEstimate | None] = [
            self._telemetry.reload_cost_p90(llama.residency_key(item)) for item in running
        ]
        if not self._telemetry.persistent_profiles:
            costs = [
                value or self._legacy_cost(llama.residency_key(item))
                for value, item in zip(costs, running, strict=True)
            ]
        if not costs or any(value is None for value in costs):
            return self._suppress(WaitReason.YIELD_LOAD_COST_UNKNOWN, "load_cost_unknown")
        threshold = max(value.value_sec for value in costs if value is not None) * THRASH_FACTOR
        if request.estimated_runtime_sec <= threshold:
            return self._suppress(WaitReason.YIELD_THRASH_COST, "thrash_cost")
        aliases = [str(item.get("alias") or "llama") for item in running]
        if any(now - self._resident_since.get(alias, now) < policy.min_uptime_sec for alias in aliases):
            return self._suppress(WaitReason.YIELD_MINIMUM_UPTIME, "minimum_uptime")
        while self._yield_history and now - self._yield_history[0] > THRASH_WINDOW_SEC:
            self._yield_history.popleft()
        if len(self._yield_history) >= THRASH_MAX_YIELDS:
            return self._suppress(WaitReason.YIELD_THRASH_WINDOW, "thrash_window")
        self._last_yield_wait_reason = None
        return True

    def _legacy_cost(self, residency_key: str) -> LoadCostEstimate | None:
        value = self._telemetry.cold_load_p90(residency_key)
        if value is None:
            return None
        return LoadCostEstimate(value, "cold", 1, 0, 1)

    def _suppress(self, wait_reason: WaitReason, telemetry_reason: str) -> bool:
        self._last_yield_wait_reason = wait_reason
        self._telemetry.record("yield.suppressed", reason=telemetry_reason)
        return False

    def yield_wait_reason(self) -> WaitReason | None:
        return self._last_yield_wait_reason

    async def release_on_request(self, *, include_helpers: bool = False) -> tuple[bool, str, int]:
        """Honour an explicit "my AI turn is over" declaration from a consumer.

        This is the same decision the idle-unload loop makes after 30 minutes,
        triggered by a request instead of by a clock. It reuses the identical
        in-use guards, so ControlDeck chat, an OpenCode session, and another
        add-on cannot have the shared model pulled out from under them.

        Unlike broker yield this does not consult the thrash/uptime heuristics:
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
        if not (policy.gateway_only and policy.yield_max_level >= int(YieldLevel.UNLOAD)):
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

    async def request_yield(
        self,
        device_id: str,
        level: YieldLevel,
        request: ResourceRequest | None = None,
    ) -> bool:
        running = [item for item in llama.list_instances() if item.get("loaded")]
        if device_id != "gpu0" or request is None or not self._yield_allowed(request, running):
            return False
        policy = get_policy()
        async with self._condition:
            if self._stopping:
                return False
            self._draining = True
            deadline = asyncio.get_running_loop().time() + policy.drain_timeout_sec
            while self._active_requests and self._draining:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self._draining = False
                    self._last_yield_wait_reason = WaitReason.YIELD_DRAIN_TIMEOUT
                    self._telemetry.record("yield.suppressed", reason="drain_timeout")
                    self._condition.notify_all()
                    return False
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    self._draining = False
                    self._last_yield_wait_reason = WaitReason.YIELD_DRAIN_TIMEOUT
                    self._telemetry.record("yield.suppressed", reason="drain_timeout")
                    self._condition.notify_all()
                    return False
            if not self._draining:
                return False
            self._stopping = True
        stopped = True
        try:
            for item in running:
                ok, _detail = await asyncio.to_thread(
                    llama.stop_instance, str(item.get("alias") or "llama")
                )
                stopped = stopped and ok
        finally:
            async with self._condition:
                self._stopping = False
                self._draining = False
                self._condition.notify_all()
        if stopped:
            self._last_yield_wait_reason = None
            self._yield_history.append(self._clock())
            self._telemetry.record("yield.completed", device_id=device_id, reason="process_stop")
        return stopped


_provider: LlamaCapacityProvider | None = None


def provider() -> LlamaCapacityProvider:
    global _provider
    if _provider is None:
        from app.resources.broker import broker

        _provider = LlamaCapacityProvider(broker.devices, broker.telemetry)
    return _provider
