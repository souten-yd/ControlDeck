"""Intelligent map (fan-curve) control: fast attack in firmware, slow release here.

The GPU firmware reads its fan curve by the HOTSPOT temperature and reacts
within a second, far faster than a 3 s software loop. So the hotspot is
handled by the curve itself:

* the curve is designed from the thermal model so that the fan speed at the
  hotspot target equals what sustained full load needs to settle there,
  rising linearly from 30 °C below the target (hotspot spikes of LLM bursts
  are followed proportionally, not ignored);
* the software holds the curve's flat level at the highest recent demand and
  releases it slowly (LLM load alternates 0–100 %), so the fan does not
  oscillate with every token batch;
* slow sensors (VRAM, edge) are not seen by the firmware curve; their demand
  comes from the remaining headroom and is folded into the held level;
* a slow trim raises the curve when a sensor stays above its target, so the
  limits hold even if the model under-estimates (and decays when cool).

No sysfs access here.
"""
from __future__ import annotations

from dataclasses import dataclass
from app.monitoring import fan_pid
from app.monitoring.fan_strategy import margin_ramp

# Hotspot temperature at which the map starts to rise (below the target).
RISE_START_BELOW_TARGET_C = 30.0
# Share of the target speed at 10 °C below the target (mid point).
MID_SHARE = 0.45
# Above the target the firmware ramps to 100 % within this many °C.
FULL_ABOVE_TARGET_C = 5.0
# Fan % without a thermal model at the hotspot target.
DEFAULT_TARGET_PERCENT = 60.0
# Extra margin on the model's sustained-load speed (percentage points).
MODEL_MARGIN_PERCENT = 3.0
# Trim: integrates the excess over a target (%/°C/s), decays slowly when cool.
TRIM_GAIN = 0.02
TRIM_DECAY_PER_S = 0.003
TRIM_MAX = 30.0


@dataclass
class MapDesign:
    temps: list[int]
    percents: list[int]

    def at(self, temp: float) -> float:
        pts = list(zip(self.temps, self.percents))
        if temp <= pts[0][0]:
            return float(pts[0][1])
        for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
            if temp <= t1:
                return p0 + (p1 - p0) * (temp - t0) / max(1, t1 - t0)
        return float(pts[-1][1])

    def curve(self, floor: float) -> str:
        """Firmware curve with the flat part raised to the held floor."""
        last = len(self.percents) - 1
        values = [p if i == last else max(p, int(round(floor))) for i, p in enumerate(self.percents)]
        for i in range(1, len(values)):
            values[i] = max(values[i], values[i - 1])
        return ",".join(f"{t}:{p}" for t, p in zip(self.temps, values, strict=True))


def target_speed(model: fan_pid.ThermalModel, hotspot_id: str | None, target_c: float,
                 minimum: float, maximum: float) -> float:
    """Fan % at which sustained full load settles at the hotspot target."""
    if not model.usable or hotspot_id is None or hotspot_id not in model.idle_c:
        return DEFAULT_TARGET_PERCENT
    heat = max(p["power_w"] for p in model.points)
    allowed = target_c - 1.0 - model.idle_c[hotspot_id]
    if allowed <= 0:
        return maximum
    return min(maximum, model.fan_for(hotspot_id, allowed / heat, minimum, maximum) + MODEL_MARGIN_PERCENT)


def design(ranges: tuple[int, int, int, int], minimum: int, target_c: float,
           at_target: float, trim: float, offset_c: float = 0.0) -> MapDesign:
    """offset_c shifts the whole map up (PID mode uses +3 °C so that the PID,
    not the map, decides the speed around the target)."""
    target_c = target_c + offset_c
    tmin, tmax, pmin, pmax = ranges
    start = max(tmin, 25)
    full = int(min(tmax, round(target_c + FULL_ABOVE_TARGET_C)))
    top = int(round(min(full - 1, target_c)))
    rise = int(max(start + 2, round(target_c - RISE_START_BELOW_TARGET_C)))
    mid = int(max(rise + 1, min(top - 1, round(target_c - 10))))
    floor = max(pmin, minimum)
    at_top = min(pmax, max(floor, at_target + trim))
    at_mid = floor + MID_SHARE * (at_top - floor)
    temps = [start, rise, mid, top, full]
    percents = [floor, floor, int(round(at_mid)), int(round(at_top)), pmax]
    return MapDesign(temps, percents)


class MapController:
    def __init__(self) -> None:
        self.held: float | None = None
        self.peak_at = 0.0
        self.trim = 0.0
        self.pv = fan_pid.PVFilter(tau_s=9.0)
        self.raw: dict[str, list[float]] = {}

    def update(self, sensors: list[dict], *, ranges: tuple[int, int, int, int],
               model: fan_pid.ThermalModel, minimum: int, maximum: int, now: float, dt: float,
               hold_seconds: float, release_percent_per_min: float) -> tuple[int, str, dict]:
        hot = next((s for s in sensors if s.get("kind") == "junction"), None)
        hot_target = float(hot["target_c"]) if hot else 90.0
        # Slow trim from the smoothed excess over any target.
        excess = max(self.pv.update(s["id"], float(s["value_c"]), dt) - float(s["target_c"])
                     for s in sensors)
        if excess > 0:
            self.trim = min(TRIM_MAX, self.trim + TRIM_GAIN * excess * dt)
        elif excess < -5:
            self.trim = max(0.0, self.trim - TRIM_DECAY_PER_S * dt)
        at_target = target_speed(model, hot["id"] if hot else None, hot_target, minimum, maximum)
        shape = design(ranges, minimum, hot_target, at_target, self.trim)
        # Demand now: the hotspot through the map on its peak of the last 3
        # readings (spikes are followed; the firmware also reacts to them
        # within a second), slow sensors by their headroom.
        demand = float(minimum)
        if hot:
            window = self.raw.setdefault(hot["id"], [])
            window.append(float(hot["value_c"]))
            del window[:-3]
            demand = max(demand, shape.at(max(window)))  # attack on the peak
        slow = [s for s in sensors if s is not hot]
        if slow:
            demand = max(demand, margin_ramp(min(float(s["headroom_c"]) for s in slow), minimum, maximum))
        # Fast attack, hold, then a slow continuous release.
        if self.held is None or demand >= self.held:
            self.held, self.peak_at = demand, now
        elif now - self.peak_at >= hold_seconds:
            self.held = max(demand, self.held - release_percent_per_min * dt / 60)
        floor = int(round(min(maximum, self.held)))
        info = {"held_percent": round(self.held, 1), "demand_percent": round(demand, 1),
                "trim_percent": round(self.trim, 2), "at_target_percent": round(at_target, 1),
                "curve_temps": shape.temps, "curve_percents": shape.percents,
                "excess_c": round(excess, 2)}
        return floor, shape.curve(floor), info

