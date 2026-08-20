from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.resources.schema import ResourceRequest
from app.resources.scheduler import Candidate, STARVATION_LIMIT, effective_score, order_candidates
from app.resources.schema import WorkloadClass


def candidate(request_id: str, owner: str, workload_class: WorkloadClass, *, queued_at: float = 100, sequence: int = 1, priority: int = 0, residency_key: str | None = None) -> Candidate:
    return Candidate(request_id, owner, priority, workload_class, queued_at, sequence, residency_key)


def test_resource_request_requires_confidence_and_rejects_ambiguous_devices():
    value = {
        "owner": "addon:media", "job_id": "job-1", "device": "auto",
        "vram": {
            "resident_bytes": 10, "execution_peak_bytes": 20, "cold_load_peak_bytes": 30,
            "headroom_bytes": 4, "confidence": "estimated",
        },
        "compute_mode": "exclusive-required", "class": "interactive", "max_wait_sec": 30,
    }
    parsed = ResourceRequest.model_validate(value)
    assert parsed.vram.required_bytes == 34
    assert parsed.workload_class == WorkloadClass.INTERACTIVE
    with pytest.raises(ValidationError):
        ResourceRequest.model_validate({**value, "vram": {key: item for key, item in value["vram"].items() if key != "confidence"}})
    with pytest.raises(ValidationError):
        ResourceRequest.model_validate({**value, "device": "gpu0", "preferred_devices": ["gpu1"]})


def test_scheduler_invariant_interactive_precedes_background_under_equal_conditions():
    values = [
        candidate("background", "addon:a", WorkloadClass.BACKGROUND, sequence=1),
        candidate("interactive", "addon:b", WorkloadClass.INTERACTIVE, sequence=2),
    ]
    assert order_candidates(values, now=100, owner_grants={}, resident_keys=[])[0].request_id == "interactive"


def test_scheduler_invariant_starved_background_gets_head_evaluation():
    values = [
        candidate("interactive", "addon:a", WorkloadClass.INTERACTIVE, queued_at=100, sequence=2, priority=100),
        candidate("starved", "addon:b", WorkloadClass.BACKGROUND, queued_at=100 - STARVATION_LIMIT, sequence=1, priority=-100),
    ]
    assert order_candidates(values, now=100, owner_grants={}, resident_keys=[])[0].request_id == "starved"


def test_scheduler_invariant_fairness_penalizes_owner_with_existing_grant():
    values = [
        candidate("same-owner", "addon:a", WorkloadClass.BACKGROUND, sequence=1),
        candidate("other-owner", "addon:b", WorkloadClass.BACKGROUND, sequence=2),
    ]
    assert order_candidates(values, now=100, owner_grants={"addon:a": 1}, resident_keys=[])[0].request_id == "other-owner"


def test_scheduler_invariant_residency_bonus_cannot_break_class_or_aging_guarantees():
    resident = candidate("resident-background", "addon:a", WorkloadClass.BACKGROUND, sequence=1, residency_key="hot")
    interactive = candidate("interactive", "addon:b", WorkloadClass.INTERACTIVE, sequence=2)
    assert order_candidates([resident, interactive], now=100, owner_grants={}, resident_keys={"hot"})[0].request_id == "interactive"
    assert effective_score(resident, now=100, owner_grants={}, resident_keys={"hot"}) == pytest.approx(2.0)


def test_scheduler_invariant_score_ties_are_fifo():
    first = candidate("first", "addon:a", WorkloadClass.BACKGROUND, sequence=1)
    second = candidate("second", "addon:b", WorkloadClass.BACKGROUND, sequence=2)
    assert [item.request_id for item in order_candidates([second, first], now=100, owner_grants={}, resident_keys=[])] == ["first", "second"]
