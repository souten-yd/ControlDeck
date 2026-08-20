from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Mapping

from app.resources.schema import WorkloadClass

AGING_PERIOD = 30.0
AGING_CAP = 4.0
AGING_GAIN = 5.0
FAIRNESS_COST = 3.0
RESIDENCY_BONUS = 2.0
STARVATION_LIMIT = 600.0

CLASS_WEIGHT: dict[WorkloadClass, float] = {
    WorkloadClass.INTERACTIVE: 8.0,
    WorkloadClass.AGENT_INTERACTIVE: 6.0,
    WorkloadClass.WORKFLOW: 3.0,
    WorkloadClass.BACKGROUND: 0.0,
    WorkloadClass.BATCH: 0.0,
    WorkloadClass.MAINTENANCE: -2.0,
}


@dataclass(frozen=True)
class Candidate:
    request_id: str
    owner: str
    priority: int
    workload_class: WorkloadClass
    queued_at: float
    sequence: int
    residency_key: str | None = None


def effective_score(
    candidate: Candidate,
    *,
    now: float,
    owner_grants: Mapping[str, int],
    resident_keys: Collection[str],
) -> float:
    age = max(0.0, now - candidate.queued_at)
    age_units = min(age / AGING_PERIOD, AGING_CAP)
    return (
        candidate.priority
        + CLASS_WEIGHT[candidate.workload_class]
        + AGING_GAIN * age_units
        - FAIRNESS_COST * max(0, owner_grants.get(candidate.owner, 0))
        + (RESIDENCY_BONUS if candidate.residency_key in resident_keys and candidate.residency_key is not None else 0.0)
    )


def order_candidates(
    candidates: Collection[Candidate],
    *,
    now: float,
    owner_grants: Mapping[str, int],
    resident_keys: Collection[str],
) -> list[Candidate]:
    def key(candidate: Candidate) -> tuple[float, float, int]:
        age = max(0.0, now - candidate.queued_at)
        # Starved work is guaranteed a head evaluation before ordinary scoring.
        starved = 1.0 if age >= STARVATION_LIMIT else 0.0
        score = effective_score(candidate, now=now, owner_grants=owner_grants, resident_keys=resident_keys)
        return (-starved, -score, candidate.sequence)

    return sorted(candidates, key=key)

