from __future__ import annotations

import threading
import time
from collections import Counter, deque
from math import isfinite
from typing import Any, Callable


class ResourceTelemetry:
    def __init__(
        self,
        *,
        max_events: int = 500,
        max_profile_samples: int = 50,
        clock: Callable[[], float] = time.time,
    ):
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._clock = clock
        self._counters: Counter[str] = Counter()
        self._max_profile_samples = max(1, max_profile_samples)
        self._load_samples: dict[str, deque[dict[str, float]]] = {}
        self._oom_profiles: dict[tuple[str, str], dict[str, Any]] = {}

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
        """Record an observed cold load; estimates are deliberately not accepted."""
        key = residency_key.strip()[:128]
        values = [process_start_sec, model_load_sec]
        if first_token_latency_sec is not None:
            values.append(first_token_latency_sec)
        if not key or any(not isfinite(value) or value < 0 for value in values):
            raise ValueError("load measurements must be finite observed durations")
        sample = {
            "measured_at": self._clock(),
            "process_start_sec": process_start_sec,
            "model_load_sec": model_load_sec,
            "cold_load_cost_sec": process_start_sec + model_load_sec,
        }
        if first_token_latency_sec is not None:
            sample["first_token_latency_sec"] = first_token_latency_sec
        with self._lock:
            samples = self._load_samples.setdefault(
                key, deque(maxlen=self._max_profile_samples)
            )
            samples.append(sample)
            self._counters["load.measured"] += 1

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
                "observed_peak_bytes": max(
                    observed_peak_bytes, int(previous.get("observed_peak_bytes", 0))
                ),
                "recommended_bytes": max(
                    observed_peak_bytes,
                    requested_bytes,
                    int(previous.get("recommended_bytes", 0)),
                ),
            }
            self._counters["oom.incident"] += 1

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

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
        return ordered[index]

    def _load_profiles(self) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for key in sorted(self._load_samples):
            samples = list(self._load_samples[key])
            costs = [item["cold_load_cost_sec"] for item in samples]
            first_tokens = [
                item["first_token_latency_sec"]
                for item in samples
                if "first_token_latency_sec" in item
            ]
            profile = {
                "residency_key": key,
                "measured_at": samples[-1]["measured_at"],
                "sample_count": len(samples),
                "cold_load_cost_sec": {
                    "p50": self._percentile(costs, 0.50),
                    "p90": self._percentile(costs, 0.90),
                },
            }
            if first_tokens:
                profile["first_token_latency_sec"] = {
                    "sample_count": len(first_tokens),
                    "p50": self._percentile(first_tokens, 0.50),
                    "p90": self._percentile(first_tokens, 0.90),
                }
            profiles.append(profile)
        return profiles

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(sorted(self._counters.items())),
                "recent_events": list(self._events),
                "load_profiles": self._load_profiles(),
                "oom_profiles": [
                    dict(self._oom_profiles[key]) for key in sorted(self._oom_profiles)
                ],
            }

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._counters.clear()
            self._load_samples.clear()
            self._oom_profiles.clear()
