"""Intelligent map control: hotspot spikes followed, slow release, trim (no hardware)."""
from __future__ import annotations

from app.monitoring import fan_map, fan_pid

RANGES = (25, 100, 15, 100)


def sensors(hot: float, mem: float = 60.0) -> list[dict]:
    return [{"id": "temp2", "kind": "junction", "label": "junction", "value_c": hot,
             "target_c": 90, "headroom_c": 90 - hot},
            {"id": "temp3", "kind": "mem", "label": "mem", "value_c": mem,
             "target_c": 90, "headroom_c": 90 - mem}]


def model() -> fan_pid.ThermalModel:
    return fan_pid.ThermalModel(
        idle_c={"temp2": 36, "temp3": 32}, idle_power_w=12,
        points=[{"fan": 50, "power_w": 200, "r": {"temp2": 0.27, "temp3": 0.24}},
                {"fan": 70, "power_w": 197, "r": {"temp2": 0.18, "temp3": 0.16}}])


def test_design_is_valid_for_the_firmware():
    shape = fan_map.design(RANGES, 20, 90, 52, 0)
    temps, pcts = shape.temps, shape.percents
    assert temps == sorted(temps) and len(set(temps)) == 5 and temps[-1] == 95
    assert pcts == sorted(pcts) and pcts[-1] == 100 and pcts[3] == 52
    assert shape.curve(35).startswith("25:35,60:35,")


def test_target_speed_from_model_keeps_hotspot_at_target():
    speed = fan_map.target_speed(model(), "temp2", 90, 20, 100)
    assert 50 <= speed <= 56


def test_hotspot_spike_is_followed_and_released_slowly():
    ctl = fan_map.MapController()
    kw = dict(ranges=RANGES, model=model(), minimum=20, maximum=100, dt=3.0,
              hold_seconds=30, release_percent_per_min=20)
    floor, _, _ = ctl.update(sensors(45), now=0, **kw)
    assert floor == 20
    floor, _, info = ctl.update(sensors(76), now=3, **kw)  # LLM burst spike
    assert floor >= 30
    peak = floor
    floors = [ctl.update(sensors(48), now=3 + 3 * i, **kw)[0] for i in range(1, 30)]
    assert floors[5] == peak  # held for 30 s
    assert floors[-1] < peak and min(b - a for a, b in zip(floors, floors[1:])) >= -2  # slow, smooth


def test_trim_raises_curve_when_over_target_and_decays():
    ctl = fan_map.MapController()
    kw = dict(ranges=RANGES, model=model(), minimum=20, maximum=100, dt=3.0,
              hold_seconds=30, release_percent_per_min=20)
    for i in range(40):
        ctl.update(sensors(91.5), now=3 * i, **kw)
    assert ctl.trim > 2
    trimmed = ctl.trim
    for i in range(40, 200):
        ctl.update(sensors(70), now=3 * i, **kw)
    assert ctl.trim < trimmed


def test_slow_sensor_raises_the_floor():
    ctl = fan_map.MapController()
    floor, _, _ = ctl.update(sensors(60, mem=84), ranges=RANGES, model=model(), minimum=20, maximum=100,
                             now=0, dt=3.0, hold_seconds=30, release_percent_per_min=20)
    assert floor > 40


def test_curve_ema_dampens_short_cooling_dip_but_raw_overheat_wins():
    ctl = fan_map.MapController()
    kw = dict(ranges=RANGES, model=model(), minimum=20, maximum=100,
              dt=3.0, hold_seconds=30, release_percent_per_min=20)
    s = sensors(80)
    s[0]["control_c"] = 80.0
    high, _, _ = ctl.update(s, now=0, **kw)
    s = sensors(45)
    s[0]["control_c"] = 76.7
    floor, _, info = ctl.update(s, now=3, **kw)
    assert info["demand_percent"] > 20
    assert floor >= high
    s = sensors(96)
    s[0]["control_c"] = 75.0
    floor, _, info = ctl.update(s, now=6, **kw)
    assert floor == 100 and info["demand_percent"] == 100


def test_curve_raw_overshoot_applies_before_filter_release():
    ctl = fan_map.MapController()
    s = sensors(96)
    s[0]["control_c"] = 45.0
    floor, _, info = ctl.update(s, ranges=RANGES, model=model(), minimum=20, maximum=100,
                                now=0, dt=3.0, hold_seconds=30, release_percent_per_min=20)
    assert floor == 100 and info["demand_percent"] == 100


def test_release_scale_band():
    assert fan_map.release_scale(89.0, 90) == 0.0
    assert fan_map.release_scale(87.0, 90) == 0.0
    assert fan_map.release_scale(84.5, 90) == 0.5
    assert fan_map.release_scale(80.0, 90) == 1.0
    assert fan_map.release_scale(None, 90) == 1.0


def test_held_level_settles_instead_of_cycling_near_target():
    """Sustained load hovering just under the target must not saw-tooth."""
    ctl = fan_map.MapController()
    kw = dict(ranges=RANGES, model=model(), minimum=20, maximum=100, dt=3.0,
              hold_seconds=30, release_percent_per_min=30)
    peak, _, _ = ctl.update(sensors(92), now=0, **kw)   # brief excursion: attack
    t, floors = 3.0, []
    for _ in range(100):                                  # 5 min at 88 C (2 below target)
        floor, _, info = ctl.update(sensors(88), now=t, **kw)
        floors.append(floor)
        t += 3
    assert peak > 60
    assert min(floors) == max(floors) == peak              # held, never released
    assert info["release_scale"] == 0.0
    for _ in range(40):                                   # clearly cool: releases again
        floor, _, _ = ctl.update(sensors(70), now=t, **kw)
        t += 3
    assert floor < peak
