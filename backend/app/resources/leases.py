from __future__ import annotations

import uuid

from app.resources.schema import ComputeMode, LeaseState, LeaseStatus, ResourceRequest


class LeaseError(RuntimeError):
    pass


class LeaseTable:
    def __init__(self, *, ttl_sec: float = 30.0):
        if ttl_sec <= 0 or ttl_sec > 3600:
            raise ValueError("lease TTLが不正です")
        self.ttl_sec = ttl_sec
        self._leases: dict[str, LeaseStatus] = {}

    @staticmethod
    def _copy(value: LeaseStatus) -> LeaseStatus:
        return LeaseStatus.model_validate(value.model_dump())

    def grant(
        self,
        request_id: str,
        request: ResourceRequest,
        device_id: str,
        now: float,
        *,
        reserved_bytes: int | None = None,
    ) -> LeaseStatus:
        lease = LeaseStatus(
            lease_id=str(uuid.uuid4()),
            request_id=request_id,
            owner=request.owner,
            job_id=request.job_id,
            device_id=device_id,
            reserved_bytes=(request.vram.required_bytes if reserved_bytes is None else reserved_bytes),
            compute_mode=request.compute_mode,
            residency_key=request.residency_key,
            state=LeaseState.GRANTED,
            granted_at=now,
            expires_at=now + self.ttl_sec,
        )
        self._leases[lease.lease_id] = lease
        return self._copy(lease)

    def get(self, lease_id: str) -> LeaseStatus | None:
        value = self._leases.get(lease_id)
        return self._copy(value) if value else None

    def current(self) -> list[LeaseStatus]:
        return [self._copy(item) for item in self._leases.values() if item.state in {LeaseState.GRANTED, LeaseState.ACTIVE}]

    def all(self) -> list[LeaseStatus]:
        return [self._copy(self._leases[key]) for key in sorted(self._leases)]

    def activate(self, lease_id: str, now: float) -> LeaseStatus:
        lease = self._required_current(lease_id)
        lease.state = LeaseState.ACTIVE
        lease.expires_at = now + self.ttl_sec
        return self._copy(lease)

    def renew(self, lease_id: str, now: float) -> LeaseStatus:
        lease = self._required_current(lease_id)
        lease.expires_at = now + self.ttl_sec
        return self._copy(lease)

    def release(self, lease_id: str, state: LeaseState = LeaseState.RELEASED) -> LeaseStatus:
        lease = self._required_current(lease_id)
        if state not in {LeaseState.RELEASED, LeaseState.CANCELED, LeaseState.EXPIRED}:
            raise ValueError("terminal lease stateが不正です")
        lease.state = state
        return self._copy(lease)

    def expire_due(self, now: float) -> list[LeaseStatus]:
        result: list[LeaseStatus] = []
        for lease in self._leases.values():
            if lease.state in {LeaseState.GRANTED, LeaseState.ACTIVE} and lease.expires_at <= now:
                lease.state = LeaseState.EXPIRED
                result.append(self._copy(lease))
        return result

    def cancel_owner(self, owner: str) -> list[LeaseStatus]:
        result: list[LeaseStatus] = []
        for lease in self._leases.values():
            if lease.owner == owner and lease.state in {LeaseState.GRANTED, LeaseState.ACTIVE}:
                lease.state = LeaseState.CANCELED
                result.append(self._copy(lease))
        return result

    def reservations(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for lease in self.current():
            result[lease.device_id] = result.get(lease.device_id, 0) + lease.reserved_bytes
        return result

    def owner_grants(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for lease in self.current():
            result[lease.owner] = result.get(lease.owner, 0) + 1
        return result

    def _required_current(self, lease_id: str) -> LeaseStatus:
        lease = self._leases.get(lease_id)
        if lease is None:
            raise LeaseError("leaseが見つかりません")
        if lease.state not in {LeaseState.GRANTED, LeaseState.ACTIVE}:
            raise LeaseError("leaseはすでに終了しています")
        return lease

    def reset(self) -> None:
        self._leases.clear()
