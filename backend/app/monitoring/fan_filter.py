"""Asymmetric EMA for software fan-control feedback. Raw safety is independent."""
from __future__ import annotations

import math


class TemperatureEMA:
    def __init__(self) -> None:
        self.values: dict[str, float] = {}
        self.last_at: float | None = None

    def reset(self) -> None:
        self.values.clear()
        self.last_at = None

    def update(self, sensors: list[dict], now: float, rise_tau_s: float,
               fall_tau_s: float) -> dict[str, float]:
        if self.last_at is not None and (now < self.last_at or now - self.last_at > 12.0):
            self.reset()
        dt = 3.0 if self.last_at is None or now == self.last_at else now - self.last_at
        self.last_at = now
        valid = {str(s["id"]) for s in sensors}
        for sid in set(self.values) - valid:
            self.values.pop(sid, None)
        for sensor in sensors:
            sid = str(sensor["id"])
            current = float(sensor["value_c"])
            if not math.isfinite(current):
                raise ValueError("non-finite temperature")
            previous = self.values.get(sid)
            if previous is None:
                self.values[sid] = current
            else:
                tau = rise_tau_s if current >= previous else fall_tau_s
                alpha = 1.0 - math.exp(-dt / tau)
                self.values[sid] = previous + alpha * (current - previous)
        return dict(self.values)
