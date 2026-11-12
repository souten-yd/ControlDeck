"""Conservative adaptive feedforward + PI temperature policy, no sysfs writes.

The firmware curve is the primary temperature safety envelope. This policy can
only RAISE its low-speed floor. It uses smoothed board power, predicts positive
temperature slopes, and clamps a bounded PI integrator (anti-windup).
No active tuning sweeps or workloads are ever generated for identification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, isfinite
from typing import Sequence

INTERVAL_SECONDS = 5
COOL_DOWN_HOLD_SECONDS = 90
FAN_STOP_DWELL_SECONDS = 600
FAN_STOP_REARM_SECONDS = 600
# Sensor types have different conservative stopping thresholds.
STOP_EDGE_C = 48
STOP_OTHER_C = 50
STOP_MEMORY_C = 55


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class AdaptivePolicy:
    """In-memory online learner. Persist bounded gains only, never live integrators."""
    ff_gain: float = 25.0
    kp: float = 1.8
    ki: float = 0.015
    integral: dict[str, float] = field(default_factory=dict)
    previous_c: dict[str, float] = field(default_factory=dict)
    derivative: dict[str, float] = field(default_factory=dict)
    power_ema_w: float | None = None
    last_demand: float = 35
    last_rise_at: float = 0
    cooling_since: float | None = None
    last_stop_exit: float = -1_000_000
    stop_active: bool = False
    warm_seconds: float = 0
    cool_seconds: float = 0
    observations: int = 0

    def _stop_threshold(self, label: str, target: float) -> float:
        low = label.lower()
        if "mem" in low or "vram" in low:
            return min(target - 20.0, STOP_MEMORY_C)
        if "hotspot" in low or "junction" in low:
            return min(target - 20.0, STOP_OTHER_C)
        return min(target - 20.0, STOP_EDGE_C)

    def update(
        self,
        sensors: Sequence[dict],
        *,
        power_w: float | None,
        power_cap_w: float | None,
        min_percent: int,
        max_percent: int,
        now: float,
        dt: float = INTERVAL_SECONDS,
        allow_stop: bool = False,
    ) -> dict:
        if not sensors:
            raise ValueError("thermal sensors unavailable")
        dt = clamp(dt, 1.0, 20.0)
        if power_w is not None and (not isfinite(power_w) or power_w < 0):
            power_w = None
        if power_cap_w is not None and (not isfinite(power_cap_w) or power_cap_w < 20):
            power_cap_w = None
        if power_w is not None:
            # Quick to rise, slow to decay: LLM prefill/decode load may toggle 0/100%.
            tau = 20 if self.power_ema_w is None or power_w > self.power_ema_w else 120
            alpha = 1.0 - exp(-dt / tau)
            self.power_ema_w = power_w if self.power_ema_w is None else (
                (1 - alpha) * self.power_ema_w + alpha * power_w
            )
        else:
            self.power_ema_w = None  # unknown must never qualify for stop
        ratio = (
            clamp(self.power_ema_w / power_cap_w, 0, 1)
            if self.power_ema_w is not None and power_cap_w is not None else 0
        )
        feedforward = self.ff_gain * ratio
        demands: list[float] = []
        margins: list[float] = []
        stop_cold = True
        fast_rise = False
        for sensor in sensors:
            key = sensor["id"]
            temp = float(sensor["value_c"])
            target = float(sensor["target_c"])
            if not isfinite(temp) or temp < -20 or temp > 125:
                raise ValueError(f"invalid temperature sensor {key}")
            previous = self.previous_c.get(key, temp)
            d = clamp((temp - previous) / dt, -0.5, 0.5)
            old_d = self.derivative.get(key, 0.0)
            d_smooth = 0.35 * d + 0.65 * old_d
            self.derivative[key] = d_smooth
            self.previous_c[key] = temp
            predicted = temp + 20 * max(0.0, d_smooth)
            margin = target - predicted
            margins.append(margin)
            # 20°C ahead of the limit the PI begins quietly correcting.
            error = predicted - (target - 20)
            integral = self.integral.get(key, 0.0)
            requested = min_percent + feedforward + self.kp * max(error, 0) + integral
            # Conditional integration is the anti-windup: do not accumulate
            # when at the upper actuator bound or when below the deadband.
            if error <= 0:
                integral = max(0, integral - 0.15 * dt)
            elif requested < max_percent - 1:
                integral = clamp(integral + self.ki * error * dt, 0, 30)
            else:
                integral = max(0, integral - 0.1 * dt)
            self.integral[key] = integral
            demands.append(min_percent + feedforward + self.kp * max(error, 0) + integral)
            if temp > self._stop_threshold(str(sensor["label"]), target) or d_smooth > 0.009:
                stop_cold = False
            fast_rise |= d_smooth > 0.045
        margin_min = min(margins)
        # Independent safety floor: adaptive parameters never reduce this.
        safety = (
            max_percent if margin_min <= 2 else 90 if margin_min <= 5 else
            75 if margin_min <= 10 else 60 if margin_min <= 15 else
            50 if margin_min <= 20 else min_percent
        )
        wanted = clamp(max(safety, *demands), min_percent, max_percent)

        # Normal operation is passive system identification (no test load).
        # Tuning is slow, bounded, and asymmetric. Bad thermal performance
        # increases response, stable cool operation relaxes very slowly.
        if power_w is not None and power_cap_w is not None and ratio >= 0.3:
            if margin_min < 10 and fast_rise:
                self.warm_seconds += dt
                self.cool_seconds = 0
            elif margin_min > 25 and not fast_rise:
                self.cool_seconds += dt
                self.warm_seconds = 0
            else:
                self.warm_seconds = self.cool_seconds = 0
            if self.warm_seconds >= 60:
                self.ff_gain = clamp(self.ff_gain + 1, 25, 55)
                self.kp = clamp(self.kp + 0.05, 1.8, 3.2)
                self.warm_seconds = 0
            if self.cool_seconds >= 900:
                self.ff_gain = clamp(self.ff_gain - 0.25, 25, 55)
                self.cool_seconds = 0
        else:
            self.warm_seconds = self.cool_seconds = 0

        if wanted > self.last_demand:
            self.last_rise_at = now
            demand = wanted # hot side never slew-rate limited
        elif now - self.last_rise_at < COOL_DOWN_HOLD_SECONDS:
            demand = self.last_demand
        else:
            # Slow and quantized ramp-down to avoid audible fan hunting.
            demand = max(wanted, self.last_demand - 2)
        self.last_demand = clamp(demand, min_percent, max_percent)
        self.observations += 1

        # Fan stop is permissive, never a forced 0%-PWM command. 10-minute
        # quiet qualification + 10-minute re-arm after a hot wake-up.
        # Power and critical temps must be known. Not based on GPU busy %.
        quiet_power = (
            self.power_ema_w is not None and power_cap_w is not None
            and self.power_ema_w <= min(40.0, power_cap_w * 0.12)
        )
        cool = stop_cold and quiet_power and margin_min >= 20
        stop_permission = False
        if allow_stop and cool and not fast_rise and now - self.last_stop_exit >= FAN_STOP_REARM_SECONDS:
            if self.cooling_since is None:
                self.cooling_since = now
            stop_permission = now - self.cooling_since >= FAN_STOP_DWELL_SECONDS
        else:
            self.cooling_since = None

        # A running stop uses a wide wake margin; short isolated usage spikes
        # do not cause PWM oscillation. Temperature rise always wins.
        risk = (
            fast_rise or margin_min <= 17 or any(
                float(s["value_c"]) >= self._stop_threshold(str(s["label"]), float(s["target_c"])) + 5
                for s in sensors
            ) or (
                self.power_ema_w is not None and power_cap_w is not None
                and self.power_ema_w >= min(75.0, power_cap_w * 0.28)
            )
        )
        return {
            "floor_percent": round(self.last_demand),
            "min_predicted_headroom_c": round(margin_min, 1),
            "power_ema_w": round(self.power_ema_w, 1) if self.power_ema_w is not None else None,
            "zero_stop_eligible": bool(stop_permission),
            "wake_required": bool(risk or not allow_stop),
            "cooldown_seconds": int(now - self.cooling_since) if self.cooling_since is not None else 0,
            "learning": {"ff_gain": round(self.ff_gain, 2), "kp": round(self.kp, 2),
                         "ki": self.ki, "observations": self.observations},
        }

    def reset_dwell(self, now: float) -> None:
        """Suspend/worker gaps are not evidence of continuous cooling."""
        self.cooling_since = None
        self.previous_c.clear()
        self.derivative.clear()
        self.power_ema_w = None
        self.last_rise_at = now
        self.warm_seconds = self.cool_seconds = 0

    def exited_stop(self, now: float) -> None:
        self.last_stop_exit = now
        self.cooling_since = None
        self.stop_active = False
        self.last_rise_at = now

    def learned_parameters(self) -> dict:
        return {"ff_gain": round(clamp(self.ff_gain, 25, 55), 2),
                "kp": round(clamp(self.kp, 1.8, 3.2), 3)}

    def load_parameters(self, values: dict) -> None:
        if not isinstance(values, dict):
            return
        try:
            v1, v2 = float(values["ff_gain"]), float(values["kp"])
            if isfinite(v1) and isfinite(v2):
                self.ff_gain = clamp(v1, 25, 55)
                self.kp = clamp(v2, 1.8, 3.2)
        except (ValueError, TypeError, KeyError):
            pass
