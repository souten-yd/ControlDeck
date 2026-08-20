from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Callable


logger = logging.getLogger(__name__)
WARM_WINDOW_SEC = 900.0
MIN_WARM_SAMPLES = 3
MIN_COLD_SAMPLES = 3
MAX_PROFILE_AGE_DAYS = 30
PROFILE_SCHEMA_VERSION = 1
MAX_PROFILE_FILE_BYTES = 2 * 1024 * 1024
YIELD_THRASH_FACTOR = 2.0


@dataclass(frozen=True)
class LoadCostEstimate:
    value_sec: float
    basis: str
    sample_count: int
    warm_count: int
    cold_count: int


class ResourceTelemetry:
    def __init__(
        self,
        *,
        max_events: int = 500,
        max_profile_samples: int = 50,
        clock: Callable[[], float] = time.time,
        profile_path: Path | None = None,
    ):
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._clock = clock
        self._counters: Counter[str] = Counter()
        self._max_profile_samples = max(1, max_profile_samples)
        self._profile_path = profile_path
        self._load_samples: dict[str, deque[dict[str, Any]]] = {}
        self._unloaded_at: dict[str, float] = {}
        self._oom_profiles: dict[tuple[str, str], dict[str, Any]] = {}
        self._load_profiles()

    @property
    def persistent_profiles(self) -> bool:
        return self._profile_path is not None

    def _load_profiles(self) -> None:
        path = self._profile_path
        if path is None or not path.exists():
            return
        try:
            if path.stat().st_size > MAX_PROFILE_FILE_BYTES:
                raise ValueError("profile file exceeds the size limit")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
                raise ValueError("unsupported resource load profile schema")
            profiles = payload.get("profiles")
            if not isinstance(profiles, dict):
                raise ValueError("resource load profiles must be an object")
            cutoff = self._clock() - MAX_PROFILE_AGE_DAYS * 86_400
            loaded: dict[str, deque[dict[str, Any]]] = {}
            for raw_key, raw_samples in profiles.items():
                key = str(raw_key).strip()[:128]
                if not key or not isinstance(raw_samples, list):
                    continue
                samples: deque[dict[str, Any]] = deque(maxlen=self._max_profile_samples)
                for raw in raw_samples[-self._max_profile_samples:]:
                    sample = self._validated_sample(raw)
                    if sample is not None and sample["measured_at"] >= cutoff:
                        samples.append(sample)
                if samples:
                    loaded[key] = samples
            self._load_samples = loaded
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("resource load profilesを読み込めません: %s", exc)
            self._load_samples = {}

    @staticmethod
    def _validated_sample(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        names = ("measured_at", "process_start_sec", "model_load_sec", "cold_load_cost_sec")
        values: dict[str, float] = {}
        for name in names:
            value = raw.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None
            value = float(value)
            if not isfinite(value) or value < 0:
                return None
            values[name] = value
        kind = raw.get("load_kind", "cold")
        if kind not in {"cold", "warm"}:
            return None
        sample: dict[str, Any] = {**values, "load_kind": kind}
        first_token = raw.get("first_token_latency_sec")
        if first_token is not None:
            if not isinstance(first_token, (int, float)) or isinstance(first_token, bool):
                return None
            first_token = float(first_token)
            if not isfinite(first_token) or first_token < 0:
                return None
            sample["first_token_latency_sec"] = first_token
        return sample

    def _payload_locked(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profiles": {
                key: list(samples) for key, samples in sorted(self._load_samples.items())
            },
        }

    def _persist_locked(self) -> None:
        path = self._profile_path
        if path is None:
            return
        try:
            payload = self._payload_locked()
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            while len(encoded) > MAX_PROFILE_FILE_BYTES and self._load_samples:
                oldest = min(
                    self._load_samples,
                    key=lambda key: max(float(item["measured_at"]) for item in self._load_samples[key]),
                )
                self._load_samples.pop(oldest, None)
                payload = self._payload_locked()
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
            try:
                with temporary.open("wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(0o600)
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        except OSError as exc:
            logger.warning("resource load profilesを保存できません: %s", exc)

    def record(
        self,
        event: str,
        *,
        request_id: str = "",
        lease_id: str = "",
        device_id: str = "",
        reason: str = "",
    ) -> None:
        # Resource requirements, user input, token values and arbitrary provider payloads
        # are intentionally excluded from telemetry.
        value = {
            "at": self._clock(),
            "event": event[:64],
            "request_id": request_id[:64],
            "lease_id": lease_id[:64],
            "device_id": device_id[:64],
            "reason": reason[:64],
        }
        with self._lock:
            self._events.appendleft(value)
            self._counters[event] += 1
            if reason:
                self._counters[f"reason:{reason}"] += 1

    def record_load_measurement(
        self,
        residency_key: str,
        *,
        process_start_sec: float,
        model_load_sec: float,
        first_token_latency_sec: float | None = None,
    ) -> None:
        """Record an observed load and classify it from the preceding stop."""
        key = residency_key.strip()[:128]
        values = [process_start_sec, model_load_sec]
        if first_token_latency_sec is not None:
            values.append(first_token_latency_sec)
        if not key or any(not isfinite(value) or value < 0 for value in values):
            raise ValueError("load measurements must be finite observed durations")
        measured_at = self._clock()
        sample: dict[str, Any] = {
            "measured_at": measured_at,
            "process_start_sec": process_start_sec,
            "model_load_sec": model_load_sec,
            "cold_load_cost_sec": process_start_sec + model_load_sec,
        }
        if first_token_latency_sec is not None:
            sample["first_token_latency_sec"] = first_token_latency_sec
        with self._lock:
            unloaded_at = self._unloaded_at.pop(key, None)
            sample["load_kind"] = (
                "warm"
                if unloaded_at is not None and 0 <= measured_at - unloaded_at <= WARM_WINDOW_SEC
                else "cold"
            )
            samples = self._load_samples.setdefault(
                key, deque(maxlen=self._max_profile_samples)
            )
            samples.append(sample)
            self._counters["load.measured"] += 1
            self._counters[f"load.measured.{sample['load_kind']}"] += 1
            self._persist_locked()

    def record_unload(self, residency_key: str) -> None:
        """Mark a stop so a following load can be classified as warm."""
        key = residency_key.strip()[:128]
        if not key:
            raise ValueError("residency key is required")
        with self._lock:
            self._unloaded_at[key] = self._clock()
            self._counters["load.unloaded"] += 1

    def record_oom(
        self,
        residency_key: str,
        device_id: str,
        *,
        observed_peak_bytes: int,
        requested_bytes: int,
    ) -> None:
        """Retain a conservative requirement floor for a later broker adapter retry."""
        key = residency_key.strip()[:128]
        device = device_id.strip()[:64]
        if not key or not device or observed_peak_bytes < 0 or requested_bytes < 0:
            raise ValueError("OOM profile requires non-negative observed byte counts")
        now = self._clock()
        profile_key = (key, device)
        with self._lock:
            previous = self._oom_profiles.get(profile_key, {})
            self._oom_profiles[profile_key] = {
                "residency_key": key,
                "device_id": device,
                "incident_count": int(previous.get("incident_count", 0)) + 1,
                "last_incident_at": now,
                "blocked_until": now + 60,
                "observed_peak_bytes": max(
                    observed_peak_bytes, int(previous.get("observed_peak_bytes", 0))
                ),
                "recommended_bytes": max(
                    int(max(observed_peak_bytes, requested_bytes) * 1.10),
                    int(previous.get("recommended_bytes", 0)),
                ),
            }
            self._counters["oom.incident"] += 1

    def oom_recommendation(self, residency_key: str, device_id: str) -> int:
        key = residency_key.strip()[:128]
        device = device_id.strip()[:64]
        with self._lock:
            profile = self._oom_profiles.get((key, device))
            return int(profile.get("recommended_bytes", 0)) if profile else 0

    def oom_retry_after(self, residency_key: str, device_id: str) -> float:
        key = residency_key.strip()[:128]
        device = device_id.strip()[:64]
        with self._lock:
            profile = self._oom_profiles.get((key, device))
            if not profile:
                return 0.0
            return max(0.0, float(profile.get("blocked_until", 0)) - self._clock())

    def record_first_token(self, residency_key: str, latency_sec: float) -> bool:
        """Complete the newest cold-load sample once; warm requests are ignored."""
        key = residency_key.strip()[:128]
        if not key or not isfinite(latency_sec) or latency_sec < 0:
            raise ValueError("first-token latency must be a finite observed duration")
        with self._lock:
            for sample in reversed(self._load_samples.get(key, ())):
                if "first_token_latency_sec" not in sample:
                    sample["first_token_latency_sec"] = latency_sec
                    self._counters["load.first_token_measured"] += 1
                    return True
        return False

    def cold_load_p90(self, residency_key: str) -> float | None:
        key = residency_key.strip()[:128]
        with self._lock:
            samples = [
                item for item in self._load_samples.get(key, ())
                if item.get("load_kind", "cold") == "cold"
            ]
            if not samples:
                return None
            return self._percentile(
                [item["cold_load_cost_sec"] for item in samples], 0.90
            )

    def reload_cost_p90(self, residency_key: str) -> LoadCostEstimate | None:
        """Return the observed cost basis for a yield, or None if insufficient."""
        key = residency_key.strip()[:128]
        with self._lock:
            samples = list(self._load_samples.get(key, ()))
            warm = [item for item in samples if item.get("load_kind") == "warm"]
            cold = [item for item in samples if item.get("load_kind", "cold") == "cold"]
            selected: list[dict[str, Any]]
            basis: str
            if len(warm) >= MIN_WARM_SAMPLES:
                selected, basis = warm, "warm"
            elif len(cold) >= MIN_COLD_SAMPLES:
                selected, basis = cold, "cold"
            else:
                return None
            return LoadCostEstimate(
                value_sec=self._percentile(
                    [float(item["cold_load_cost_sec"]) for item in selected], 0.90
                ),
                basis=basis,
                sample_count=len(selected),
                warm_count=len(warm),
                cold_count=len(cold),
            )

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
        return ordered[index]

    def _profile_snapshot(self) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for key in sorted(self._load_samples):
            samples = list(self._load_samples[key])
            process_starts = [item["process_start_sec"] for item in samples]
            model_loads = [item["model_load_sec"] for item in samples]
            cold_costs = [
                item["cold_load_cost_sec"] for item in samples
                if item.get("load_kind", "cold") == "cold"
            ]
            warm_costs = [
                item["cold_load_cost_sec"] for item in samples
                if item.get("load_kind") == "warm"
            ]
            first_tokens = [
                item["first_token_latency_sec"]
                for item in samples
                if "first_token_latency_sec" in item
            ]
            profile = {
                "residency_key": key,
                "measured_at": samples[-1]["measured_at"],
                "sample_count": len(samples),
                "process_start_sec": {
                    "p50": self._percentile(process_starts, 0.50),
                    "p90": self._percentile(process_starts, 0.90),
                },
                "model_load_sec": {
                    "p50": self._percentile(model_loads, 0.50),
                    "p90": self._percentile(model_loads, 0.90),
                },
            }
            if self._profile_path is None:
                # Preserve the established shape for explicit ephemeral test harnesses.
                costs = [item["cold_load_cost_sec"] for item in samples]
                profile["cold_load_cost_sec"] = {
                    "p50": self._percentile(costs, 0.50),
                    "p90": self._percentile(costs, 0.90),
                }
            else:
                profile["cold_load_cost_sec"] = self._distribution(cold_costs)
                profile["warm_reload_cost_sec"] = self._distribution(warm_costs)
                estimate = self.reload_cost_p90(key)
                profile["yield_basis"] = estimate.basis if estimate else "insufficient"
                profile["yield_threshold_sec"] = (
                    estimate.value_sec * YIELD_THRASH_FACTOR if estimate else None
                )
            if first_tokens:
                profile["first_token_latency_sec"] = {
                    "sample_count": len(first_tokens),
                    "p50": self._percentile(first_tokens, 0.50),
                    "p90": self._percentile(first_tokens, 0.90),
                }
            profiles.append(profile)
        return profiles

    def _distribution(self, values: list[float]) -> dict[str, float | int | None]:
        return {
            "p50": self._percentile(values, 0.50) if values else None,
            "p90": self._percentile(values, 0.90) if values else None,
            "count": len(values),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(sorted(self._counters.items())),
                "recent_events": list(self._events),
                "load_profiles": self._profile_snapshot(),
                "oom_profiles": [
                    dict(self._oom_profiles[key]) for key in sorted(self._oom_profiles)
                ],
            }

    def reset(self) -> None:
        self.clear()

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._counters.clear()
            self._load_samples.clear()
            self._unloaded_at.clear()
            self._oom_profiles.clear()
            if self._profile_path is not None:
                try:
                    self._profile_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("resource load profilesを削除できません: %s", exc)
