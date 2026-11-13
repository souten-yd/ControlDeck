"""Target-temperature PID fan control with a thermal-model feedforward.

No sysfs access here: pure, testable logic used by ``fan_control.tick``.

Control law (cooling plant, so the error is PV - SV, "too hot" is positive):

    u = u_ff + Kp * (e + (1 / Ti) * integral(e dt))

* The "D" slot is a **feedforward**, not a linear extrapolation of the
  temperature: from the measured idle temperature and the heat input
  (board power above idle, lagged by the thermal time constant) the thermal
  model gives the fan % at which each sensor settles exactly at its target.
* PV is filtered (median of 3, then EMA) because the hotspot jumps several
  °C for single readings under LLM load.
* Anti-windup: conditional integration while the output is saturated, and
  the integrator is bounded.
* The model is learned by an explicit auto-tune (staircase from a high fan
  speed down to the target while the GPU is under load) and refined
  passively at steady states during normal operation.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from statistics import median

# Safety above the target: from +2°C the output ramps up to 100% at +5°C.
OVERSHOOT_START_C = 2.0
OVERSHOOT_FULL_C = 5.0
# The hotspot follows power within ~10–20 s (R9700: 50→95°C in 45 s at 20%
# fan), much faster than mem/edge; its feedforward uses this short lag.
HOTSPOT_FF_LAG_S = 12.0
# Steady state for idle / equilibrium detection.
STABLE_SLOPE_C_PER_MIN = 0.5
IDLE_STABLE_SECONDS = 120.0
# Auto-tune staircase.
TUNE_START_PERCENT = 70
TUNE_STEP_PERCENT = 10
TUNE_MIN_DWELL_SECONDS = 90.0
TUNE_MAX_DWELL_SECONDS = 300.0
TUNE_MAX_SECONDS = 45 * 60.0
TUNE_LOAD_LOST_SECONDS = 20.0
# Dead time of the loop: 3 s tick + PV filter lag.
LOOP_DEAD_TIME_S = 9.0
KP_RANGE = (0.5, 10.0)
TI_RANGE = (15.0, 600.0)
FF_TAU_DEFAULT_S = 30.0
# Far below the target there is nothing to correct: the output is already at
# the minimum. Capping the cold-side error keeps the integrator from being
# loaded with +30% at idle (it then over-cooled 6°C under load on the R9700).
# A large cap also cancelled the feedforward at load start (Kp 1.33 x -10°C =
# -13%) and let the hotspot overshoot by 4°C.
COLD_ERROR_LIMIT_C = 4.0
# The hotspot reacts to power faster than the fan-step time constant.
FF_LAG_FACTOR = 0.6


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class ThermalModel:
    """Learned per GPU. Points are equilibria: fan % → ΔT/ΔP per sensor."""
    idle_c: dict[str, float] = field(default_factory=dict)
    idle_power_w: float | None = None
    # [{"fan": %, "power_w": W above idle, "r": {sensor: °C per W}}]
    points: list[dict] = field(default_factory=list)
    tau_s: float = FF_TAU_DEFAULT_S
    gain_c_per_percent: float | None = None
    tuned: dict | None = None

    def to_json(self) -> dict:
        return {"idle_c": self.idle_c, "idle_power_w": self.idle_power_w,
                "points": self.points, "tau_s": self.tau_s,
                "gain_c_per_percent": self.gain_c_per_percent, "tuned": self.tuned}

    @classmethod
    def from_json(cls, data: dict) -> "ThermalModel":
        model = cls()
        if not isinstance(data, dict):
            return model
        try:
            model.idle_c = {str(k): float(v) for k, v in (data.get("idle_c") or {}).items()}
            idle_power = data.get("idle_power_w")
            model.idle_power_w = None if idle_power is None else float(idle_power)
            points = []
            for point in data.get("points") or []:
                fan, power = float(point["fan"]), float(point["power_w"])
                r = {str(k): float(v) for k, v in point["r"].items()}
                if 0 < fan <= 100 and power > 0 and all(math.isfinite(v) and v >= 0 for v in r.values()):
                    points.append({"fan": fan, "power_w": power, "r": r})
            model.points = sorted(points, key=lambda p: p["fan"])[:32]
            model.tau_s = clamp(float(data.get("tau_s") or FF_TAU_DEFAULT_S), 5.0, 900.0)
            gain = data.get("gain_c_per_percent")
            model.gain_c_per_percent = None if gain is None else float(gain)
            model.tuned = data.get("tuned") if isinstance(data.get("tuned"), dict) else None
        except (KeyError, TypeError, ValueError):
            return cls()
        return model

    @property
    def usable(self) -> bool:
        return len(self.points) >= 2 and self.idle_power_w is not None and bool(self.idle_c)

    def add_point(self, fan: float, power_w: float, r: dict[str, float], weight: float = 1.0) -> None:
        """Merge into a 5% bucket (EMA for passive refinement)."""
        bucket = round(fan / 5) * 5
        for point in self.points:
            if abs(point["fan"] - bucket) < 2.5:
                for key, value in r.items():
                    old = point["r"].get(key, value)
                    point["r"][key] = old + weight * (value - old)
                point["power_w"] += weight * (power_w - point["power_w"])
                return
        self.points.append({"fan": float(bucket), "power_w": power_w, "r": dict(r)})
        self.points.sort(key=lambda p: p["fan"])
        del self.points[32:]

    def fan_for(self, sensor_id: str, r_required: float, minimum: float, maximum: float) -> float:
        """Smallest fan % whose thermal resistance does not exceed r_required.
        Resistance falls with fan speed; interpolate, extrapolate linearly."""
        pts = [(p["fan"], p["r"][sensor_id]) for p in self.points if sensor_id in p["r"]]
        if len(pts) < 2:
            return minimum
        # Enforce monotone non-increasing R (noise must not invert the map).
        mono: list[tuple[float, float]] = []
        for fan, r in pts:
            mono.append((fan, min(r, mono[-1][1]) if mono else r))
        if r_required >= mono[0][1]:
            (f0, r0), (f1, r1) = mono[0], mono[1]
            if r0 - r1 <= 1e-9:
                return minimum
            return clamp(f0 - (r_required - r0) * (f1 - f0) / (r0 - r1), minimum, maximum)
        for (f0, r0), (f1, r1) in zip(mono, mono[1:]):
            if r1 <= r_required <= r0:
                if r0 - r1 <= 1e-9:
                    return clamp(f1, minimum, maximum)
                return clamp(f0 + (r0 - r_required) * (f1 - f0) / (r0 - r1), minimum, maximum)
        (f0, r0), (f1, r1) = mono[-2], mono[-1]
        if r0 - r1 <= 1e-9:
            return maximum
        return clamp(f1 + (r1 - r_required) * (f1 - f0) / (r0 - r1), minimum, maximum)


def simc_gains(gain_c_per_percent: float, tau_s: float, dead_time_s: float = LOOP_DEAD_TIME_S) -> tuple[float, float]:
    """SIMC (Skogestad) PI tuning for a first-order-plus-dead-time plant.
    lambda = tau/2: calm without a lasting offset. With lambda = tau the
    R9700 hotspot sat 1–2°C above its target for minutes (Kp 0.77).
    Returns (Kp in %/°C, Ti in s)."""
    k = max(0.02, abs(gain_c_per_percent))
    lam = max(tau_s / 2, dead_time_s)
    kp = tau_s / (k * (lam + dead_time_s))
    ti = min(tau_s, 4 * (lam + dead_time_s))
    return round(clamp(kp, *KP_RANGE), 2), round(clamp(ti, *TI_RANGE), 1)


class PVFilter:
    """Median of the last 3 samples, then an EMA."""

    def __init__(self, tau_s: float = 6.0) -> None:
        self.tau_s = tau_s
        self.raw: dict[str, deque] = {}
        self.value: dict[str, float] = {}

    def update(self, sensor_id: str, value: float, dt: float) -> float:
        window = self.raw.setdefault(sensor_id, deque(maxlen=3))
        window.append(value)
        med = median(window)
        old = self.value.get(sensor_id)
        alpha = 1.0 - math.exp(-dt / self.tau_s)
        self.value[sensor_id] = med if old is None else old + alpha * (med - old)
        return self.value[sensor_id]


class Lag:
    """First-order lag of the heat input (power) by the thermal time constant."""

    def __init__(self) -> None:
        self.value: float | None = None

    def update(self, value: float, dt: float, tau_s: float) -> float:
        if self.value is None:
            self.value = value
        else:
            self.value += (1.0 - math.exp(-dt / max(1.0, tau_s))) * (value - self.value)
        return self.value


@dataclass
class PIDResult:
    output: float
    feedforward: float
    p_term: float
    i_term: float
    error_c: float
    governing: str
    override: bool


class PIDController:
    def __init__(self) -> None:
        self.integral = 0.0  # in % (already multiplied by Kp/Ti)
        self.pv = PVFilter()
        self.power = Lag()
        self.power_fast = Lag()
        self.last_output: float | None = None

    def reset(self) -> None:
        self.__init__()

    def feedforward(self, model: ThermalModel, sensors: list[dict], targets: dict[str, float],
                    power_w: float | None, dt: float, minimum: float, maximum: float,
                    weight: float) -> float:
        if power_w is None or not model.usable:
            return minimum
        slow = self.power.update(power_w, dt, model.tau_s * FF_LAG_FACTOR)
        fast = self.power_fast.update(power_w, dt, min(HOTSPOT_FF_LAG_S, model.tau_s * FF_LAG_FACTOR))
        needed = minimum
        for sensor in sensors:
            sid = sensor["id"]
            kind = sensor.get("kind") or ("junction" if "junction" in str(sensor.get("label", "")).lower()
                                          or "hotspot" in str(sensor.get("label", "")).lower() else "")
            heat = max(0.0, (fast if kind == "junction" else slow) - (model.idle_power_w or 0.0))
            if heat < 1.0:
                continue
            idle = model.idle_c.get(sid)
            if idle is None:
                continue
            allowed = targets[sid] - idle
            if allowed <= 0:
                return maximum
            needed = max(needed, model.fan_for(sid, allowed / heat, minimum, maximum))
        return clamp(minimum + weight * (needed - minimum), minimum, maximum)

    def update(self, sensors: list[dict], targets: dict[str, float], *, model: ThermalModel,
               power_w: float | None, kp: float, ti: float, ff_weight: float,
               minimum: float, maximum: float, dt: float) -> PIDResult:
        errors = {}
        for sensor in sensors:
            pv = self.pv.update(sensor["id"], float(sensor["value_c"]), dt)
            errors[sensor["id"]] = pv - targets[sensor["id"]]
        governing = max(errors, key=errors.get)
        error = max(errors[governing], -COLD_ERROR_LIMIT_C)
        ff = self.feedforward(model, sensors, targets, power_w, dt, minimum, maximum, ff_weight)
        # Unsmoothed (median only) overshoot is a safety floor, not a PID input: a
        # ramp from target+2°C to 100% at +5°C (a jump to 100% at +5°C held
        # the fan high for minutes on the R9700).
        # Median of the last 3 readings: a single 3 s hotspot jump is noise.
        over = max(median(self.pv.raw[s["id"]]) - targets[s["id"]] for s in sensors)
        safety = minimum
        if over >= OVERSHOOT_START_C:
            safety = minimum + (maximum - minimum) * min(1.0, (over - OVERSHOOT_START_C)
                                                       / (OVERSHOOT_FULL_C - OVERSHOOT_START_C))
        p_term = kp * error
        unclamped = ff + p_term + self.integral
        # Conditional integration (anti-windup): do not integrate further into
        # a saturated output.
        step = kp / max(1.0, ti) * error * dt
        # Beyond the cold cap the error is not a real deviation: do not
        # integrate (it drove the integral to -4% while warming up); instead
        # the integral relaxes toward 0 over Ti (stale after a load ends).
        capped = errors[governing] < -COLD_ERROR_LIMIT_C
        if capped:
            self.integral -= self.integral * min(1.0, dt / max(1.0, ti))
        elif not ((unclamped >= maximum and step > 0) or (unclamped <= minimum and step < 0)):
            self.integral = clamp(self.integral + step, -maximum, maximum)
        # Resting at the minimum while cold: a positive integral is stale
        # (it would over-cool the next load); relax it toward 0 over Ti.
        if unclamped <= minimum and error < 0 and self.integral > 0:
            self.integral = max(0.0, self.integral - self.integral * dt / max(1.0, ti) - abs(step))
        output = clamp(ff + p_term + self.integral, minimum, maximum)
        override = safety > output
        output = max(output, safety)
        self.last_output = output
        return PIDResult(round(output, 2), round(ff, 2), round(p_term, 2), round(self.integral, 2),
                         round(error, 2), governing, override)

    def bumpless(self, current_percent: float, ff: float) -> None:
        """Start from the fan speed in use (no jump when switching modes)."""
        self.integral = current_percent - ff


def slope_c_per_min(history: list[tuple[float, float]], now: float, window: float = 60.0) -> float | None:
    """Least-squares slope over the window; None if not enough samples."""
    pts = [(t, v) for t, v in history if now - t <= window]
    if len(pts) < 5 or pts[-1][0] - pts[0][0] < window * 0.6:
        return None
    mt = sum(t for t, _ in pts) / len(pts)
    mv = sum(v for _, v in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    if den <= 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in pts) / den * 60.0


class SteadyTracker:
    """Per-sensor history for idle / equilibrium detection."""

    def __init__(self) -> None:
        self.temps: dict[str, deque] = {}
        self.power: deque = deque(maxlen=200)
        self.fan: deque = deque(maxlen=200)

    def add(self, now: float, sensors: list[dict], power_w: float | None, fan: float) -> None:
        for sensor in sensors:
            self.temps.setdefault(sensor["id"], deque(maxlen=200)).append((now, float(sensor["value_c"])))
        if power_w is not None:
            self.power.append((now, float(power_w)))
        self.fan.append((now, float(fan)))

    def stable(self, now: float, window: float) -> bool:
        for history in self.temps.values():
            slope = slope_c_per_min(list(history), now, window)
            if slope is None or abs(slope) > STABLE_SLOPE_C_PER_MIN:
                return False
        return True

    def mean(self, series: deque, now: float, window: float) -> float | None:
        values = [v for t, v in series if now - t <= window]
        return sum(values) / len(values) if values else None

    def temp_means(self, now: float, window: float) -> dict[str, float]:
        out = {}
        for sid, history in self.temps.items():
            value = self.mean(history, now, window)
            if value is not None:
                out[sid] = value
        return out

    def power_steady(self, now: float, window: float, tolerance: float = 0.15) -> bool:
        values = [v for t, v in self.power if now - t <= window]
        if len(values) < 5:
            return False
        avg = sum(values) / len(values)
        return avg > 0 and max(abs(v - avg) for v in values) <= max(15.0, avg * tolerance)


@dataclass
class AutoTune:
    """Staircase identification under the user's own load.

    phase: waiting_idle → waiting_load → settling (fan held) → done/aborted.
    The fan starts high and steps down until a sensor reaches its target
    (or the minimum fan); each settled level is an equilibrium point.
    """
    phase: str = "off"
    started_at: float = 0.0
    level: float = TUNE_START_PERCENT
    level_since: float = 0.0
    load_lost_since: float | None = None
    step_start: dict | None = None
    taus: list[float] = field(default_factory=list)
    points: list[dict] = field(default_factory=list)
    message: str = ""
    result: dict | None = None

    def snapshot(self, now: float) -> dict:
        running = self.phase not in ("off", "done", "aborted")
        return {"phase": self.phase, "level_percent": self.level if self.phase == "settling" else None,
                "points": [{"fan": p["fan"], "temps": p.get("temps", {}), "settled": p.get("settled")}
                           for p in self.points],
                "message": self.message, "result": self.result,
                "elapsed_s": round(now - self.started_at) if running else None}

    def start(self, now: float, model: ThermalModel) -> None:
        self.__init__()
        self.started_at = now
        self.phase = "waiting_load" if model.idle_c else "waiting_idle"
        self.message = ("負荷（LLMの連続生成など）を待っています" if model.idle_c
                        else "まずアイドル温度を測ります。GPUを使わずに2分ほど待ってください")

    def cancel(self, message: str = "中止しました") -> None:
        self.phase, self.message = "aborted", message


def loaded(power_w: float | None, power_cap_w: float | None, idle_power_w: float | None) -> bool:
    if power_w is None:
        return False
    threshold = max((idle_power_w or 20.0) + 60.0, 0.4 * (power_cap_w or 300.0))
    return power_w >= threshold


def update_idle(model: ThermalModel, tracker: SteadyTracker, now: float,
                power_w: float | None, power_cap_w: float | None) -> bool:
    """Measure the idle (ambient) temperature every time the GPU rests."""
    if power_w is None:
        return False
    limit = min(60.0, 0.15 * (power_cap_w or 300.0))
    recent = [v for t, v in tracker.power if now - t <= IDLE_STABLE_SECONDS]
    if len(recent) < 20 or max(recent) > limit or not tracker.stable(now, IDLE_STABLE_SECONDS):
        return False
    temps = tracker.temp_means(now, 30.0)
    first = not model.idle_c
    for sid, value in temps.items():
        old = model.idle_c.get(sid)
        model.idle_c[sid] = round(value if old is None or first else old + 0.2 * (value - old), 2)
    power = sum(recent) / len(recent)
    model.idle_power_w = round(power if model.idle_power_w is None else
                               model.idle_power_w + 0.2 * (power - model.idle_power_w), 1)
    return True


def tune_step(tune: AutoTune, model: ThermalModel, tracker: SteadyTracker, *, now: float,
              sensors: list[dict], targets: dict[str, float], power_w: float | None,
              power_cap_w: float | None, minimum: float, maximum: float) -> float | None:
    """Advance the auto-tune. Returns the fan % to hold, or None (normal control)."""
    if tune.phase in ("off", "done", "aborted"):
        return None
    if now - tune.started_at > TUNE_MAX_SECONDS:
        return _finish(tune, model, "時間切れのため、ここまでの測定で調整しました")
    if any(float(s["value_c"]) >= targets[s["id"]] + 2 for s in sensors):
        if len(tune.points) >= 2:
            return _finish(tune, model, "目標温度を超えたため、ここまでの測定で調整しました")
        tune.cancel("目標温度を超えたため中止しました（通常制御に戻ります）")
        return None
    if tune.phase == "waiting_idle":
        if model.idle_c:
            tune.phase, tune.message = "waiting_load", "負荷（LLMの連続生成など）を待っています"
        return None
    is_loaded = loaded(power_w, power_cap_w, model.idle_power_w)
    if tune.phase == "waiting_load":
        if is_loaded:
            tune.phase, tune.level, tune.level_since = "settling", float(min(maximum, TUNE_START_PERCENT)), now
            tune.message = f"{tune.level:.0f}%で温度が落ち着くのを待っています"
            tune.step_start = None
            return tune.level
        return None
    # settling
    if not is_loaded:
        tune.load_lost_since = tune.load_lost_since or now
        if now - tune.load_lost_since >= TUNE_LOAD_LOST_SECONDS:
            tune.phase, tune.message = "waiting_load", "負荷が止まりました。再開を待っています（測定済みの点は保持）"
            tune.load_lost_since = None
        return tune.level
    tune.load_lost_since = None
    dwell = now - tune.level_since
    if tune.step_start is not None and "tau_s" not in tune.step_start:
        _track_tau(tune, tracker, now)
    settled = dwell >= TUNE_MIN_DWELL_SECONDS and (
        tracker.stable(now, 60.0) and tracker.power_steady(now, 60.0)
    )
    if not settled and dwell < TUNE_MAX_DWELL_SECONDS:
        return tune.level
    temps = tracker.temp_means(now, 30.0)
    power = tracker.mean(tracker.power, now, 30.0) or 0.0
    heat = power - (model.idle_power_w or 0.0)
    if heat > 10 and model.idle_c:
        r = {sid: max(0.0, (temps[sid] - model.idle_c[sid]) / heat) for sid in temps if sid in model.idle_c}
        tune.points.append({"fan": tune.level, "power_w": round(heat, 1), "r": r,
                            "temps": {k: round(v, 1) for k, v in temps.items()}, "settled": settled})
    if tune.step_start is not None and "tau_s" in tune.step_start:
        tune.taus.append(tune.step_start["tau_s"])
    near = any(temps.get(s["id"], 0) >= targets[s["id"]] - 3 for s in sensors)
    if near or tune.level <= minimum:
        return _finish(tune, model, "調整が完了しました")
    # Next level; record the step start for the time constant.
    governing = max(sensors, key=lambda s: temps.get(s["id"], 0) - targets[s["id"]])["id"]
    tune.step_start = {"t": now, "sensor": governing, "t0": temps.get(governing, 0.0)}
    tune.level = float(max(minimum, tune.level - TUNE_STEP_PERCENT))
    tune.level_since = now
    tune.message = f"{tune.level:.0f}%で温度が落ち着くのを待っています（{len(tune.points)}点測定済み）"
    return tune.level


def _track_tau(tune: AutoTune, tracker: SteadyTracker, now: float) -> None:
    """63% rise time after a fan step (first-order time constant)."""
    start = tune.step_start
    history = tracker.temps.get(start["sensor"])
    if not history:
        return
    start.setdefault("samples", []).append((now - start["t"], history[-1][1]))
    samples = start["samples"]
    if now - start["t"] < 60:
        return
    final = sum(v for _, v in samples[-5:]) / min(5, len(samples))
    rise = final - start["t0"]
    if rise < 1.0:
        return
    for elapsed, value in samples:
        if value - start["t0"] >= 0.63 * rise:
            if tracker.stable(now, 45.0):
                start["tau_s"] = max(5.0, elapsed)
            return


def _finish(tune: AutoTune, model: ThermalModel, message: str) -> None:
    if len(tune.points) < 2:
        tune.cancel("測定点が足りないため調整できませんでした（負荷が短すぎた可能性）")
        return None
    model.points = []
    for point in tune.points:
        model.add_point(point["fan"], point["power_w"], point["r"])
    if tune.taus:
        model.tau_s = round(clamp(median(tune.taus), 5.0, 900.0), 1)
    # Process gain near the target: last two points, governing sensor.
    a, b = sorted(tune.points, key=lambda p: p["fan"])[:2]
    governing = max(b["r"], key=lambda sid: a["r"].get(sid, 0) - b["r"].get(sid, 0))
    heat = (a["power_w"] + b["power_w"]) / 2
    gain = (a["r"][governing] - b["r"][governing]) * heat / max(1.0, b["fan"] - a["fan"])
    model.gain_c_per_percent = round(-abs(gain), 4)
    kp, ti = simc_gains(gain, model.tau_s)
    model.tuned = {"kp": kp, "ti": ti, "tau_s": model.tau_s,
                   "gain_c_per_percent": model.gain_c_per_percent, "points": len(tune.points)}
    tune.result = dict(model.tuned)
    tune.phase, tune.message = "done", message
    return None
