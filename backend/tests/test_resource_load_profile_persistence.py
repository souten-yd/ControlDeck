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
