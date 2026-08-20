from __future__ import annotations

import json
import os

from app.resources.telemetry import (
    MAX_PROFILE_AGE_DAYS,
    ResourceTelemetry,
)


def _record(telemetry: ResourceTelemetry, key: str, cost: float) -> None:
    telemetry.record_load_measurement(
        key, process_start_sec=1.0, model_load_sec=cost - 1.0,
    )


def test_load_origin_classification_is_scoped_windowed_and_one_shot(tmp_path):
    now = [1_000.0]
    path = tmp_path / "profiles.json"
    telemetry = ResourceTelemetry(profile_path=path, clock=lambda: now[0])

    _record(telemetry, "llama:a", 80)
    telemetry.record_unload("llama:a")
    now[0] += 899
    _record(telemetry, "llama:a", 8)
    _record(telemetry, "llama:a", 81)
    telemetry.record_unload("llama:b")
    _record(telemetry, "llama:a", 82)
    telemetry.record_unload("llama:a")
    now[0] += 901
    _record(telemetry, "llama:a", 83)

    samples = json.loads(path.read_text())["profiles"]["llama:a"]
    assert [sample["load_kind"] for sample in samples] == [
        "cold", "warm", "cold", "cold", "cold",
    ]


def test_reload_cost_prefers_three_warm_samples_and_never_mixes_cold_outlier(tmp_path):
    now = [2_000.0]
    telemetry = ResourceTelemetry(
        profile_path=tmp_path / "profiles.json", clock=lambda: now[0],
    )
    for cost in (81.0, 82.0, 83.0):
        _record(telemetry, "llama:model", cost)
        now[0] += 1
    for cost in (7.0, 8.0, 9.0):
        telemetry.record_unload("llama:model")
        now[0] += 1
        _record(telemetry, "llama:model", cost)

    estimate = telemetry.reload_cost_p90("llama:model")
    assert estimate is not None
    assert estimate.value_sec == 9.0
    assert estimate.basis == "warm"
    assert estimate.sample_count == estimate.warm_count == 3
    assert estimate.cold_count == 3
    profile = telemetry.snapshot()["load_profiles"][0]
    assert profile["cold_load_cost_sec"] == {"p50": 82.0, "p90": 83.0, "count": 3}
    assert profile["warm_reload_cost_sec"] == {"p50": 8.0, "p90": 9.0, "count": 3}
    assert profile["yield_basis"] == "warm"
    assert profile["yield_threshold_sec"] == 18.0


def test_reload_cost_bootstraps_from_three_cold_samples_and_fails_closed_before_that(tmp_path):
    now = [3_000.0]
    path = tmp_path / "profiles.json"
    telemetry = ResourceTelemetry(profile_path=path, clock=lambda: now[0])
    _record(telemetry, "llama:small", 80)
    _record(telemetry, "llama:small", 81)
    assert telemetry.reload_cost_p90("llama:small") is None

    for cost in (80.0, 81.0, 82.0, 83.0, 84.0):
        _record(telemetry, "llama:bootstrap", cost)
    for cost in (8.0, 9.0):
        telemetry.record_unload("llama:bootstrap")
        now[0] += 1
        _record(telemetry, "llama:bootstrap", cost)
    estimate = telemetry.reload_cost_p90("llama:bootstrap")
    assert estimate is not None
    assert estimate.basis == "cold" and estimate.value_sec == 84.0
    assert estimate.warm_count == 2 and estimate.cold_count == 5


def test_profiles_survive_reconstruction_and_respect_age_maxlen_and_clear(tmp_path):
    now = [4_000_000.0]
    path = tmp_path / "profiles.json"
    first = ResourceTelemetry(
        profile_path=path, max_profile_samples=3, clock=lambda: now[0],
    )
    for cost in (70.0, 71.0, 72.0, 73.0):
        _record(first, "llama:model", cost)
        now[0] += 1
    restored = ResourceTelemetry(
        profile_path=path, max_profile_samples=3, clock=lambda: now[0],
    )
    estimate = restored.reload_cost_p90("llama:model")
    assert estimate is not None and estimate.value_sec == 73.0
    assert restored.snapshot()["load_profiles"][0]["sample_count"] == 3
    restored.clear()
    assert not path.exists()

    old = now[0] - (MAX_PROFILE_AGE_DAYS + 1) * 86_400
    path.write_text(json.dumps({
        "schema_version": 1,
        "profiles": {"llama:old": [{
            "measured_at": old, "process_start_sec": 1, "model_load_sec": 2,
            "cold_load_cost_sec": 3, "load_kind": "cold",
        }]},
    }))
    expired = ResourceTelemetry(profile_path=path, clock=lambda: now[0])
    assert expired.snapshot()["load_profiles"] == []


def test_corrupt_or_future_profile_file_is_ignored_without_startup_failure(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{broken")
    assert ResourceTelemetry(profile_path=path).snapshot()["load_profiles"] == []
    path.write_text(json.dumps({"schema_version": 2, "profiles": {}}))
    assert ResourceTelemetry(profile_path=path).snapshot()["load_profiles"] == []


def test_atomic_replace_failure_preserves_previous_profile_file(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    telemetry = ResourceTelemetry(profile_path=path)
    _record(telemetry, "llama:model", 80)
    previous = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(os, "replace", fail_replace)
    _record(telemetry, "llama:model", 81)
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".profiles.json.tmp-*"))
