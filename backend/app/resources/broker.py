from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from app.config import data_dir
from app.resources.devices import DeviceCollection, ResourceDevice
from app.resources.leases import LeaseError, LeaseTable
from app.resources.probes import ProviderRegistry
from app.resources.providers import ProviderReservation
from app.resources.scheduler import Candidate, order_candidates
from app.resources.schema import (
    HOST_DEVICE_ID,
    BlockingResource,
    ComputeMode,
    LeaseState,
    LeaseStatus,
    RequestState,
    RequestStatus,
    ResourceRequest,
    WaitReason,
)
from app.resources.telemetry import ResourceTelemetry


class BrokerError(RuntimeError):
    pass


@dataclass
class _RequestRecord:
    request: ResourceRequest
    status: RequestStatus
    sequence: int
    completed: asyncio.Event


@dataclass(frozen=True)
class _Fit:
    device_id: str | None
    reason: WaitReason | None
    blocking: tuple[BlockingResource, ...] = ()
    # 実際に貸す枠。全常駐が入らなくても下限を満たせるなら、空きぶんを貸す。
    granted_bytes: int = 0


class ResourceBroker:
    def __init__(
        self,
        devices: DeviceCollection,
        providers: ProviderRegistry | None = None,
        *,
        lease_ttl_sec: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        telemetry: ResourceTelemetry | None = None,
    ):
        self.devices = devices
        self.providers = providers or ProviderRegistry()
        self.leases = LeaseTable(ttl_sec=lease_ttl_sec)
        self._clock = clock
        self.telemetry = telemetry or ResourceTelemetry()
        self._lock = asyncio.Lock()
        self._requests: dict[str, _RequestRecord] = {}
        self._sequence = 0
        self._revision = 0
        self._changed = asyncio.Condition()

    @property
    def revision(self) -> int:
        return self._revision

    async def submit(self, request: ResourceRequest) -> RequestStatus:
        now = self._clock()
        async with self._lock:
            self._sequence += 1
            request_id = str(uuid.uuid4())
            status = RequestStatus(
                request_id=request_id,
                state=RequestState.WAITING,
                owner=request.owner,
                job_id=request.job_id,
                reason=WaitReason.QUEUE_POSITION,
                queue_position=1,
                actions=["cancel", "lower_priority"],
                requested_at=now,
                deadline_at=now + request.max_wait_sec,
            )
            record = _RequestRecord(request, status, self._sequence, asyncio.Event())
            self._requests[request_id] = record
            if not self._physically_possible(request):
                self._finish_rejected(record, WaitReason.INSUFFICIENT_CAPACITY)
            else:
                await self._schedule_locked(now)
                if request.on_insufficient == "fail_fast" and record.status.state == RequestState.WAITING:
                    self._finish_rejected(record, record.status.reason or WaitReason.INSUFFICIENT_VRAM)
            if record.status.state == RequestState.WAITING:
                self.telemetry.record(
                    "request.waiting",
                    request_id=request_id,
                    reason=(record.status.reason or WaitReason.QUEUE_POSITION).value,
                )
            await self._bump()
            return self._copy_status(record.status)

    async def acquire(self, request: ResourceRequest) -> RequestStatus:
        status = await self.submit(request)
        if status.state != RequestState.WAITING:
            return status
        return await self.wait(status.request_id)

    async def wait(self, request_id: str) -> RequestStatus:
        async with self._lock:
            record = self._required_request(request_id)
            if record.status.state != RequestState.WAITING:
                return self._copy_status(record.status)
            event = record.completed
            timeout = max(0.0, record.status.deadline_at - self._clock())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            await self.expire_due()
        return await self.request_status(request_id)

    async def request_status(self, request_id: str) -> RequestStatus:
        async with self._lock:
            return self._copy_status(self._required_request(request_id).status)

    async def request_statuses(self) -> list[RequestStatus]:
        async with self._lock:
            return [self._copy_status(self._requests[key].status) for key in sorted(self._requests)]

    async def lease_statuses(self) -> list[LeaseStatus]:
        async with self._lock:
            return self.leases.all()

    async def lease_status(self, lease_id: str) -> LeaseStatus:
        async with self._lock:
            result = next((item for item in self.leases.all() if item.lease_id == lease_id), None)
            if result is None:
                raise BrokerError("leaseが見つかりません")
            return result

    async def cancel_request(self, request_id: str) -> RequestStatus:
        async with self._lock:
            record = self._required_request(request_id)
            if record.status.state == RequestState.WAITING:
                record.status.state = RequestState.CANCELED
                record.status.reason = None
                record.status.queue_position = None
                record.status.actions = []
                record.completed.set()
                self.telemetry.record("request.canceled", request_id=request_id)
                await self._schedule_locked(self._clock())
                await self._bump()
            return self._copy_status(record.status)

    async def activate(self, lease_id: str) -> LeaseStatus:
        async with self._lock:
            result = self.leases.activate(lease_id, self._clock())
            self.telemetry.record("lease.active", request_id=result.request_id, lease_id=lease_id, device_id=result.device_id)
            await self._bump()
            return result

    async def renew(self, lease_id: str) -> LeaseStatus:
        async with self._lock:
            result = self.leases.renew(lease_id, self._clock())
            self.telemetry.record("lease.renewed", request_id=result.request_id, lease_id=lease_id, device_id=result.device_id)
            await self._bump()
            return result

    async def release(self, lease_id: str) -> LeaseStatus:
        async with self._lock:
            result = self.leases.release(lease_id)
            self.telemetry.record("lease.released", request_id=result.request_id, lease_id=lease_id, device_id=result.device_id)
            await self._schedule_locked(self._clock())
            await self._bump()
            return result

    async def cancel_owner(self, owner: str) -> dict[str, int]:
        async with self._lock:
            requests = 0
            for record in self._requests.values():
                if record.request.owner == owner and record.status.state == RequestState.WAITING:
                    record.status.state = RequestState.CANCELED
                    record.status.reason = None
                    record.status.queue_position = None
                    record.status.actions = []
                    record.completed.set()
                    self.telemetry.record("request.canceled", request_id=record.status.request_id)
                    requests += 1
            canceled_leases = self.leases.cancel_owner(owner)
            leases = len(canceled_leases)
            for lease in canceled_leases:
                self.telemetry.record("lease.canceled", request_id=lease.request_id, lease_id=lease.lease_id, device_id=lease.device_id)
            await self._schedule_locked(self._clock())
            if requests or leases:
                await self._bump()
            return {"requests": requests, "leases": leases}

    async def cancel_waiting_owner(self, owner: str) -> dict[str, int]:
        """Stop queued work without pretending an active worker released its device."""
        async with self._lock:
            requests = 0
            for record in self._requests.values():
                if record.request.owner == owner and record.status.state == RequestState.WAITING:
                    record.status.state = RequestState.CANCELED
                    record.status.reason = None
                    record.status.queue_position = None
                    record.status.actions = []
                    record.completed.set()
                    self.telemetry.record("request.canceled", request_id=record.status.request_id)
                    requests += 1
            if requests:
                await self._schedule_locked(self._clock())
                await self._bump()
            return {"requests": requests, "leases": 0}

    async def expire_due(self) -> dict[str, int]:
        now = self._clock()
        async with self._lock:
            requests = 0
            for record in self._requests.values():
                if record.status.state == RequestState.WAITING and record.status.deadline_at <= now:
                    record.status.state = RequestState.EXPIRED
                    record.status.reason = None
                    record.status.queue_position = None
                    record.status.actions = []
                    record.completed.set()
                    self.telemetry.record("request.expired", request_id=record.status.request_id)
                    requests += 1
            expired_leases = self.leases.expire_due(now)
            leases = len(expired_leases)
            for lease in expired_leases:
                self.telemetry.record("lease.expired", request_id=lease.request_id, lease_id=lease.lease_id, device_id=lease.device_id)
            if requests or leases:
                await self._schedule_locked(now)
                await self._bump()
            return {"requests": requests, "leases": leases}

    async def snapshot(self) -> dict:
        async with self._lock:
            provider_values = self.providers.reservations()
            fixed = self._reservation_totals(provider_values)
            return {
                "revision": self._revision,
                # granted_at / expires_at は monotonic。壁時計と引き算されると
                # 56 年経過のような値になる（実際に表示された）。どの時計かを
                # 呼び出し側に推測させず、同じ時計の現在値を一緒に返す。
                "now": self._clock(),
                "devices": [item.model_dump(mode="json") for item in self.devices.snapshots(
                    fixed_reservations=fixed,
                    lease_reservations=self.leases.reservations(),
                )],
                "requests": [self._copy_status(item.status).model_dump(mode="json") for item in self._requests.values()],
                "leases": [item.model_dump(mode="json") for item in self.leases.all()],
                "telemetry": self.telemetry.snapshot(),
            }

    async def wait_for_revision(self, previous: int, timeout: float = 30.0) -> int:
        async with self._changed:
            if self._revision == previous:
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=max(0.0, min(timeout, 60.0)))
                except TimeoutError:
                    pass
            return self._revision

    async def reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self.expire_due()
            await self.reschedule()

    async def reschedule(self) -> None:
        async with self._lock:
            before = len(self.leases.current())
            await self._schedule_locked(self._clock())
            if len(self.leases.current()) != before:
                await self._bump()

    async def reset(self) -> None:
        async with self._lock:
            for record in self._requests.values():
                record.completed.set()
            self._requests.clear()
            self.leases.reset()
            self.telemetry.reset()
            self._sequence = 0
            await self._bump()

    async def _schedule_locked(self, now: float) -> None:
        while True:
            waiting = [record for record in self._requests.values() if record.status.state == RequestState.WAITING]
            if not waiting:
                return
            provider_values = self.providers.reservations()
            resident = {
                key for item in provider_values for key in [item.residency_key] if key is not None
            } | {
                key for item in self.leases.current() for key in [item.residency_key] if key is not None
            }
            ordered = order_candidates(
                [Candidate(
                    record.status.request_id,
                    record.request.owner,
                    record.request.priority,
                    record.request.workload_class,
                    record.status.requested_at,
                    record.sequence,
                    record.request.residency_key,
                ) for record in waiting],
                now=now,
                owner_grants=self.leases.owner_grants(),
                resident_keys=resident,
            )
            granted = False
            for position, candidate in enumerate(ordered, start=1):
                record = self._requests[candidate.request_id]
                fit = await self._fit(record.request, provider_values)
                if fit.device_id is not None:
                    lease = self.leases.grant(
                        candidate.request_id,
                        record.request,
                        fit.device_id,
                        now,
                        reserved_bytes=fit.granted_bytes or self._required_bytes(
                            record.request, fit.device_id
                        ),
                    )
                    record.status.state = RequestState.GRANTED
                    record.status.device_id = fit.device_id
                    record.status.lease_id = lease.lease_id
                    record.status.granted_bytes = lease.reserved_bytes
                    record.status.reason = None
                    record.status.queue_position = None
                    record.status.blocking = []
                    record.status.actions = []
                    record.completed.set()
                    self.telemetry.record(
                        "lease.granted",
                        request_id=candidate.request_id,
                        lease_id=lease.lease_id,
                        device_id=fit.device_id,
                    )
                    granted = True
                    break
                record.status.reason = fit.reason or WaitReason.QUEUE_POSITION
                record.status.queue_position = position
                record.status.blocking = list(fit.blocking)
                record.status.actions = ["cancel", "lower_priority"]
            if not granted:
                return

    async def _fit(self, request: ResourceRequest, provider_values: list[ProviderReservation]) -> _Fit:
        current_leases = self.leases.current()
        fixed = self._reservation_totals(provider_values)
        snapshots = {item.id: item for item in self.devices.snapshots(
            fixed_reservations=fixed,
            lease_reservations=self.leases.reservations(),
        )}
        candidates: list[tuple[int, int, int, str]] = []
        last_reason = WaitReason.INSUFFICIENT_VRAM
        last_blocking: tuple[BlockingResource, ...] = ()
        for device in self._eligible_devices(request):
            snapshot = snapshots[device.id]
            if request.residency_key and self.telemetry.oom_retry_after(
                request.residency_key, device.id
            ) > 0:
                last_reason = WaitReason.DEPENDENCY_PENDING
                continue
            leases = [item for item in current_leases if item.device_id == device.id]
            providers = [item for item in provider_values if item.device_id == device.id and item.reserved_bytes > 0]
            exclusive = request.compute_mode in {ComputeMode.EXCLUSIVE_REQUIRED, ComputeMode.EXCLUSIVE_PREFERRED}
            existing_exclusive = any(item.compute_mode in {ComputeMode.EXCLUSIVE_REQUIRED, ComputeMode.EXCLUSIVE_PREFERRED} for item in leases)
            if (exclusive and (leases or providers)) or existing_exclusive:
                last_reason = WaitReason.DEVICE_BUSY_EXCLUSIVE
                last_blocking = self._blocking(leases, providers)
                continue
            wanted = self._required_bytes(request, device.id)
            # 全部載らなくても、下限を満たせるなら空きぶんを貸す。利用者は枠の中で
            # 動く形（重みをRAMに置き、実行するモジュールだけをVRAMへ送る）へ
            # 切り替える。枠を割れば利用者の process だけが OOM で落ちる。
            floor = self._floor_bytes(request, device.id)
            budget = min(wanted, snapshot.admitted_free_bytes)
            if budget < floor:
                last_blocking = self._blocking(leases, providers)
                last_reason = WaitReason.HELD_BY_OTHER_OWNER if providers else WaitReason.INSUFFICIENT_VRAM
                continue
            probe = await self.providers.check(request, device.id)
            if not probe.accepting:
                last_reason = probe.reason or WaitReason.DEPENDENCY_PENDING
                continue
            # preferred_devices は順序を持つ。["gpu0", "host"] は「空いていればVRAM、
            # 無ければRAM」を表す。順序を無視して空き容量で選ぶと、RAMの方が空きが
            # 大きいだけでVRAMが空いていてもRAMへ流れてしまう。
            try:
                rank = request.preferred_devices.index(device.id)
            except ValueError:
                rank = len(request.preferred_devices)
            resident = 1 if request.residency_key and request.residency_key in device.resident_keys else 0
            candidates.append((rank, resident, snapshot.admitted_free_bytes, device.id, budget))
        if not candidates:
            return _Fit(None, last_reason, last_blocking)
        candidates.sort(key=lambda item: (item[0], -item[1], -item[2], item[3]))
        best = candidates[0]
        return _Fit(best[3], None, granted_bytes=best[4])

    def _eligible_devices(self, request: ResourceRequest) -> list[ResourceDevice]:
        values = [item for item in self.devices.list() if item.compatible]
        if request.device != "auto":
            return [item for item in values if item.id == request.device]
        forbidden = set(request.forbidden_devices)
        wanted = set(request.preferred_devices)
        # GPU以外の配置（host = システムRAM）は opt-in。CPUオフロードに対応した
        # 利用者が preferred_devices へ挙げたときだけ候補にする。知らない利用者へ
        # 黙って割り当てると、GPUを使うつもりのまま二重にVRAMを取りにいく。
        return [item for item in values
                if item.id not in forbidden and (item.kind == "gpu" or item.id in wanted)]

    def _physically_possible(self, request: ResourceRequest) -> bool:
        # 誰も明け渡さない前提なので、現在の予約はすべて動かせない量として数える。
        non_yieldable: dict[str, int] = {}
        for item in self.providers.reservations():
            non_yieldable[item.device_id] = non_yieldable.get(item.device_id, 0) + item.reserved_bytes
        return any(
            self._required_bytes(request, item.id)
            <= max(0, item.total_bytes - non_yieldable.get(item.id, 0))
            for item in self._eligible_devices(request)
        )

    def _required_bytes(self, request: ResourceRequest, device_id: str) -> int:
        recommendation = (
            self.telemetry.oom_recommendation(request.residency_key, device_id)
            if request.residency_key else 0
        )
        # 同じモデルでも置き場所で必要量が違う。vram の見積りは device_map で
        # 段階的に載せるときのGPU側ピークで、RAM配置の実態とは別物である。
        # 実測: FLUX.2 Klein 4B は VRAM 31.1GB の申告に対しCPU実行のRSSが16.3GB。
        # VRAMの数字をRAMに当てると、30GBの機械では host が永久に grant されない。
        if device_id == HOST_DEVICE_ID and request.host_bytes is not None:
            return max(request.host_bytes, recommendation)
        return max(request.vram.required_bytes, recommendation)

    def _floor_bytes(self, request: ResourceRequest, device_id: str) -> int:
        """この device で受理してよい最小の枠。

        host（システムRAM）は分割して載せる先ではない。RAM 側は全部を置く前提
        なので、下限は required と同じにする。
        """
        if device_id == HOST_DEVICE_ID:
            return self._required_bytes(request, device_id)
        recommendation = (
            self.telemetry.oom_recommendation(request.residency_key, device_id)
            if request.residency_key else 0
        )
        return max(request.vram.floor_bytes, recommendation)

    @staticmethod
    def _reservation_totals(values: list[ProviderReservation]) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in values:
            result[item.device_id] = result.get(item.device_id, 0) + item.reserved_bytes
        return result

    @staticmethod
    def _blocking(leases: list[LeaseStatus], providers: list[ProviderReservation]) -> tuple[BlockingResource, ...]:
        values = [BlockingResource(owner=item.owner, bytes=item.reserved_bytes) for item in leases]
        values.extend(BlockingResource(
            owner=item.owner,
            bytes=item.reserved_bytes,
        ) for item in providers)
        return tuple(values)

    def _finish_rejected(self, record: _RequestRecord, reason: WaitReason) -> None:
        record.status.state = RequestState.REJECTED
        record.status.reason = reason
        record.status.queue_position = None
        record.status.actions = []
        record.completed.set()
        self.telemetry.record("request.rejected", request_id=record.status.request_id, reason=reason.value)

    def _required_request(self, request_id: str) -> _RequestRecord:
        try:
            return self._requests[request_id]
        except KeyError as exc:
            raise BrokerError("resource requestが見つかりません") from exc

    @staticmethod
    def _copy_status(value: RequestStatus) -> RequestStatus:
        return RequestStatus.model_validate(value.model_dump())

    async def _bump(self) -> None:
        self._revision += 1
        async with self._changed:
            self._changed.notify_all()


def empty_broker() -> ResourceBroker:
    return ResourceBroker(DeviceCollection())


broker = ResourceBroker(
    DeviceCollection(),
    telemetry=ResourceTelemetry(profile_path=data_dir() / "resource-load-profiles.json"),
)


__all__ = ["BrokerError", "LeaseError", "ResourceBroker", "broker"]
