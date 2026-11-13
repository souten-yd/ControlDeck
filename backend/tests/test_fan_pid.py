"""PID fan control, thermal-model feedforward and auto-tune (no hardware)."""
from __future__ import annotations

import math

from app.monitoring import fan_pid as pid


def plant_r(fan: float) -> float:
    """Synthetic R9700-like thermal resistance (°C/W above idle) vs fan %."""
    return 0.08 + 6.0 / max(fan, 5.0)


class Plant:
    """First-order plant: T → idle + P * R(fan) with time constant tau."""

    def __init__(self, idle: float = 38.0, tau: float = 40.0) -> None:
        self.idle, self.tau, self.t = idle, tau, idle

    def step(self, fan: float, heat_w: float, dt: float) -> float:
        target = self.idle + heat_w * plant_r(fan)
        self.t += (1 - math.exp(-dt / self.tau)) * (target - self.t)
        return self.t


def sensors_of(temp: float) -> list[dict]:
    return [{"id": "temp2", "label": "junction", "kind": "junction", "value_c": temp}]


def test_fan_for_interpolates_and_extrapolates():
    model = pid.ThermalModel(idle_c={"t": 40}, idle_power_w=15,
                             points=[{"fan": 40, "power_w": 200, "r": {"t": 0.25}},
                                     {"fan": 60, "power_w": 200, "r": {"t": 0.18}}])
    assert model.fan_for("t", 0.215, 20, 100) == 50
    assert model.fan_for("t", 0.30, 20, 100) < 40  # below the measured range
    assert 80 < model.fan_for("t", 0.10, 20, 100) < 86  # linear beyond the last point
    assert model.fan_for("t", 0.01, 20, 100) == 100


def test_simc_gains_are_bounded_and_calm():
    kp, ti = pid.simc_gains(-0.25, 40.0)
    assert 0.5 <= kp <= 10 and 15 <= ti <= 600
    assert pid.simc_gains(-0.001, 1.0)[0] == pid.KP_RANGE[1] or pid.simc_gains(-0.001, 1.0)[0] <= 10


def test_pid_raises_when_hot_and_antiwindup_holds_at_max():
    ctl = pid.PIDController()
    model = pid.ThermalModel()
    for _ in range(200):
        result = ctl.update(sensors_of(93), {"temp2": 90}, model=model, power_w=250, kp=3, ti=60,
                            ff_weight=1, minimum=20, maximum=100, dt=3)
    assert result.output > 30
    for _ in range(400):
        ctl.update(sensors_of(94.5), {"temp2": 90}, model=model, power_w=250, kp=10, ti=15,
                   ff_weight=1, minimum=20, maximum=100, dt=3)
    assert ctl.integral <= 100
    # Cooling down: the integrator unwinds quickly instead of holding 100%.
    for _ in range(60):
        result = ctl.update(sensors_of(70), {"temp2": 90}, model=model, power_w=20, kp=3, ti=60,
                            ff_weight=1, minimum=20, maximum=100, dt=3)
    assert result.output == 20


def test_single_noisy_reading_barely_moves_the_output():
    ctl = pid.PIDController()
    model = pid.ThermalModel()
    outs = []
    for i in range(40):
        temp = 88 + (6 if i == 30 else 0)  # one 3 s spike
        outs.append(ctl.update(sensors_of(temp), {"temp2": 90}, model=model, power_w=200, kp=3,
                               ti=90, ff_weight=1, minimum=20, maximum=100, dt=3).output)
    assert max(outs[29:33]) - outs[29] < 3


def test_overshoot_ramps_to_full_speed():
    """目標+2°Cから+5°Cで100%まで線形に上げる（以前は+5°Cで即100%）。"""
    ctl = pid.PIDController()
    kw = dict(model=pid.ThermalModel(), power_w=200, kp=1, ti=600, ff_weight=0, minimum=20, maximum=100, dt=3)
    for _ in range(3):
        result = ctl.update(sensors_of(93.5), {"temp2": 90}, **kw)
    assert result.override and 55 <= result.output <= 65
    for _ in range(3):
        result = ctl.update(sensors_of(96), {"temp2": 90}, **kw)
    assert result.override and result.output == 100


def _run_autotune(plant: Plant, heat: float, target: float):
    model = pid.ThermalModel()
    tracker = pid.SteadyTracker()
    tune = pid.AutoTune()
    now, fan = 0.0, 25.0
    # Idle first (power 15 W).
    for _ in range(80):
        now += 3
        temp = plant.step(fan, 0, 3)
        tracker.add(now, sensors_of(temp), 15.0, fan)
        pid.update_idle(model, tracker, now, 15.0, 300.0)
    assert model.idle_c["temp2"] == round(plant.idle, 2) or abs(model.idle_c["temp2"] - plant.idle) < 0.5
    tune.start(now, model)
    for _ in range(2000):
        now += 3
        temp = plant.step(fan, heat, 3)
        tracker.add(now, sensors_of(temp), 15.0 + heat, fan)
        hold = pid.tune_step(tune, model, tracker, now=now, sensors=sensors_of(temp),
                             targets={"temp2": target}, power_w=15.0 + heat, power_cap_w=300.0,
                             minimum=20, maximum=100)
        if tune.phase in ("done", "aborted"):
            break
        fan = hold if hold is not None else fan
    return model, tune


def test_autotune_staircase_identifies_model_and_gains():
    model, tune = _run_autotune(Plant(tau=40), heat=200, target=90)
    assert tune.phase == "done", tune.message
    assert len(tune.points) >= 3 and tune.points[0]["fan"] == pid.TUNE_START_PERCENT
    assert model.usable
    # Model resistance matches the plant at a measured level.
    point = model.points[-1]
    assert abs(point["r"]["temp2"] - plant_r(point["fan"])) < 0.02
    assert 20 <= model.tau_s <= 70
    assert model.tuned and 0.5 <= model.tuned["kp"] <= 10


def test_feedforward_settles_plant_at_target_after_tuning():
    plant = Plant(tau=40)
    model, tune = _run_autotune(plant, heat=200, target=90)
    assert tune.phase == "done"
    ctl = pid.PIDController()
    kp, ti = model.tuned["kp"], model.tuned["ti"]
    temp, fan, now = plant.t, 50.0, 0.0
    temps = []
    for _ in range(600):
        result = ctl.update(sensors_of(temp), {"temp2": 85}, model=model, power_w=215, kp=kp, ti=ti,
                            ff_weight=1, minimum=20, maximum=100, dt=3)
        fan = result.output
        temp = plant.step(fan, 200, 3)
        temps.append(temp)
    assert abs(sum(temps[-50:]) / 50 - 85) < 1.0
    assert max(temps[100:]) < 88


def test_autotune_aborts_without_enough_points_when_overheating():
    model = pid.ThermalModel(idle_c={"temp2": 40}, idle_power_w=15)
    tune = pid.AutoTune()
    tracker = pid.SteadyTracker()
    tune.start(0, model)
    hold = pid.tune_step(tune, model, tracker, now=3, sensors=sensors_of(93), targets={"temp2": 90},
                         power_w=250, power_cap_w=300, minimum=20, maximum=100)
    assert hold is None and tune.phase == "aborted"


def test_model_json_roundtrip_rejects_garbage():
    model = pid.ThermalModel(idle_c={"a": 40}, idle_power_w=15,
                             points=[{"fan": 50, "power_w": 200, "r": {"a": 0.2}}])
    again = pid.ThermalModel.from_json(model.to_json())
    assert again.points == model.points and again.idle_c == {"a": 40.0}
    assert pid.ThermalModel.from_json({"points": [{"fan": "x"}]}).points == []
