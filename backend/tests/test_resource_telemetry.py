from __future__ import annotations

import pytest

from app.resources.telemetry import ResourceTelemetry


def test_load_profiles_use_bounded_observed_samples_and_nearest_rank_p90():
    now = [100.0]
    telemetry = ResourceTelemetry(max_profile_samples=3, clock=lambda: now[0])
    for process, load, first_token in (
        (1.0, 9.0, 0.8),
        (2.0, 18.0, 1.1),
        (3.0, 27.0, None),
        (4.0, 36.0, 1.5),
    ):
        now[0] += 1
        telemetry.record_load_measurement(
            "llama:model-hash",
            process_start_sec=process,
            model_load_sec=load,
            first_token_latency_sec=first_token,
        )

    profile = telemetry.snapshot()["load_profiles"][0]
    assert profile["sample_count"] == 3
    assert profile["measured_at"] == 104.0
    assert profile["process_start_sec"] == {"p50": 3.0, "p90": 4.0}
    assert profile["model_load_sec"] == {"p50": 27.0, "p90": 36.0}
    assert profile["cold_load_cost_sec"] == {"p50": 30.0, "p90": 40.0}
    assert profile["first_token_latency_sec"] == {
        "sample_count": 2,
        "p50": 1.1,
        "p90": 1.5,
    }


def test_load_profiles_reject_estimate_shaped_or_invalid_values():
    telemetry = ResourceTelemetry()
    with pytest.raises(TypeError):
        telemetry.record_load_measurement(  # type: ignore[call-arg]
            "model", process_start_sec=1, model_load_sec=2, confidence="estimated"
        )
    with pytest.raises(ValueError):
        telemetry.record_load_measurement(
            "model", process_start_sec=float("nan"), model_load_sec=2
        )


def test_oom_profile_counts_incidents_and_never_lowers_requirement_floor():
    now = [50.0]
    telemetry = ResourceTelemetry(clock=lambda: now[0])
    telemetry.record_oom(
        "runtime:model", "gpu0", observed_peak_bytes=90, requested_bytes=80
    )
    now[0] = 60.0
    telemetry.record_oom(
        "runtime:model", "gpu0", observed_peak_bytes=70, requested_bytes=75
    )

    snapshot = telemetry.snapshot()
    assert snapshot["counters"]["oom.incident"] == 2
    assert snapshot["oom_profiles"] == [{
        "residency_key": "runtime:model",
        "device_id": "gpu0",
        "incident_count": 2,
        "last_incident_at": 60.0,
        "observed_peak_bytes": 90,
        "recommended_bytes": 90,
    }]


def test_first_token_completes_only_the_latest_pending_load_sample():
    telemetry = ResourceTelemetry()
    telemetry.record_load_measurement(
        "runtime:model", process_start_sec=1, model_load_sec=2
    )
    assert telemetry.record_first_token("runtime:model", 0.75) is True
    assert telemetry.record_first_token("runtime:model", 99) is False
    profile = telemetry.snapshot()["load_profiles"][0]
    assert profile["first_token_latency_sec"] == {
        "sample_count": 1, "p50": 0.75, "p90": 0.75
    }


def test_reset_removes_events_and_profiles():
    telemetry = ResourceTelemetry()
    telemetry.record("request.waiting", reason="queue_position")
    telemetry.record_load_measurement(
        "runtime:model", process_start_sec=1, model_load_sec=2
    )
    telemetry.record_oom(
        "runtime:model", "gpu0", observed_peak_bytes=3, requested_bytes=2
    )
    telemetry.reset()
    assert telemetry.snapshot() == {
        "counters": {}, "recent_events": [], "load_profiles": [], "oom_profiles": []
    }
