"""Pure thermal-strategy simulations: no GPU hardware or privileged writes."""
from __future__ import annotations

from app.monitoring.fan_strategy import AdaptivePolicy


def sensors(edge=38.0, hotspot=40.0, memory=43.0):
    return [
        {"id": "temp1", "label": "edge", "value_c": edge, "target_c": 75},
        {"id": "temp2", "label": "junction", "value_c": hotspot, "target_c": 90},
        {"id": "temp3", "label": "mem", "value_c": memory, "target_c": 85},
    ]


def step(policy, data=None, now=1000, power=12, cap=300, stop=False):
    return policy.update(
        data or sensors(), power_w=power, power_cap_w=cap,
        min_percent=35, max_percent=100, now=now, dt=5, allow_stop=stop,
    )


def test_zero_rpm_is_never_available_without_explicit_opt_in_or_power_telemetry():
    p = AdaptivePolicy()
    for idx in range(200):
        result = step(p, now=1000 + idx * 5, stop=False)
        assert not result["zero_stop_eligible"]
    p = AdaptivePolicy()
    for idx in range(200):
        result = step(p, now=1000 + idx * 5, stop=True, power=None)
        assert not result["zero_stop_eligible"]


def test_stable_thermal_power_required_for_ten_minutes_then_fast_power_wake():
    p = AdaptivePolicy()
    for idx in range(120):
        result = step(p, now=1000 + idx * 5, stop=True)
        assert not result["zero_stop_eligible"]
    assert step(p, now=1600, stop=True)["zero_stop_eligible"]
    # LLM job begins: 2 consecutive high-power intervals trigger early wake;
    # not raw instantaneous utilization toggling from 0 to 100%.
    step(p, now=1605, power=260, stop=True)
    assert step(p, now=1610, power=260, stop=True)["wake_required"]
    p.exited_stop(1610)
    for idx in range(20):
        assert not step(p, now=1615 + 5 * idx, power=12, stop=True)["zero_stop_eligible"]


def test_positive_temperature_slope_accelerates_without_cooling_slew_limit():
    p = AdaptivePolicy()
    first = step(p, sensors(edge=60, hotspot=65, memory=65), power=20)
    second = step(p, sensors(edge=65, hotspot=73, memory=70), now=1005, power=120)
    assert second["floor_percent"] >= first["floor_percent"]
    assert second["min_predicted_headroom_c"] < first["min_predicted_headroom_c"]


def test_cooldown_does_not_chase_bursty_load():
    p = AdaptivePolicy()
    step(p, sensors(edge=76, hotspot=90, memory=85), power=220)
    high = p.last_demand
    result = step(p, sensors(), now=1005, power=0)
    assert result["floor_percent"] >= high  # no sudden audible fan slowdown
    for idx in range(12):
        result = step(p, sensors(), now=1010 + idx * 5, power=0)
    assert result["floor_percent"] >= 35


def test_integrator_clamps_and_adaptive_gains_remain_in_safe_bounds():
    p = AdaptivePolicy()
    for idx in range(450):
        result = step(p, sensors(edge=90, hotspot=105, memory=95),
                      now=1000 + idx * 5, power=280)
    assert all(0 <= v <= 30 for v in p.integral.values())
    assert 25 <= p.ff_gain <= 55
    assert 1.8 <= p.kp <= 3.2
    assert result["floor_percent"] == 100
    p.load_parameters({"ff_gain": 999, "kp": -999})
    assert p.learned_parameters() == {"ff_gain": 55, "kp": 1.8}


def test_suspend_resets_low_power_qualification_not_just_monotonic_clock():
    p = AdaptivePolicy()
    for idx in range(110):
        step(p, now=1000 + idx * 5, stop=True)
    p.reset_dwell(5000)
    result = step(p, now=5000, stop=True)
    assert not result["zero_stop_eligible"]
    assert result["cooldown_seconds"] == 0



def test_learning_can_be_disabled_without_disabling_safety_floor():
    p = AdaptivePolicy(ff_gain=28.0, kp=2.0, ki=0.01)
    for idx in range(400):
        result = p.update(
            sensors(edge=88, hotspot=101, memory=94), power_w=270,
            power_cap_w=300, min_percent=35, max_percent=100,
            now=1000 + idx * 5, dt=5, learning_enabled=False,
        )
    assert p.ff_gain == 28.0
    assert p.kp == 2.0
    assert result["floor_percent"] == 100
    assert all(v <= 30 for v in p.integral.values())


def test_zero_stop_dwell_cannot_shorten_below_ten_minutes():
    p = AdaptivePolicy()
    for idx in range(120):
        result = p.update(
            sensors(), power_w=10, power_cap_w=300, min_percent=35,
            max_percent=100, now=1000 + idx * 5, dt=5, allow_stop=True,
            stop_dwell_seconds=1200,
        )
    assert not result["zero_stop_eligible"]
    for idx in range(120, 240):
        result = p.update(
            sensors(), power_w=10, power_cap_w=300, min_percent=35,
            max_percent=100, now=1000 + idx * 5, dt=5, allow_stop=True,
            stop_dwell_seconds=1200,
        )
    assert not result["zero_stop_eligible"]
    assert p.update(
        sensors(), power_w=10, power_cap_w=300, min_percent=35,
        max_percent=100, now=2200, dt=5, allow_stop=True,
        stop_dwell_seconds=1200,
    )["zero_stop_eligible"]
