"""Hardware-free tests for timestamp-aware asymmetric fan temperature EMA."""
from __future__ import annotations

import pytest

from app.monitoring.fan_filter import TemperatureEMA


def sample(value: float) -> list[dict]:
    return [{"id": "temp2", "value_c": value}]


def test_fast_rise_slow_fall_and_no_finite_overshoot():
    ema = TemperatureEMA()
    assert ema.update(sample(45), 100, 2, 30)["temp2"] == 45
    high = ema.update(sample(80), 103, 2, 30)["temp2"]
    assert 72 < high < 80
    low = ema.update(sample(45), 106, 2, 30)["temp2"]
    assert 69 < low < high
    assert low > 45


def test_discontinuity_resets_instead_of_resuming_stale_state():
    ema = TemperatureEMA()
    ema.update(sample(45), 100, 2, 30)
    ema.update(sample(80), 103, 2, 30)
    assert ema.update(sample(45), 120, 2, 30)["temp2"] == 45
    ema.update(sample(80), 124, 2, 30)
    assert ema.update(sample(50), 123, 2, 30)["temp2"] == 50


def test_sensor_removal_drops_its_state():
    ema = TemperatureEMA()
    ema.update([{"id": "temp2", "value_c": 50}, {"id": "temp3", "value_c": 60}], 1, 2, 30)
    result = ema.update(sample(55), 4, 2, 30)
    assert set(result) == {"temp2"}


def test_nonfinite_input_rejected():
    ema = TemperatureEMA()
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError, match="non-finite"):
            ema.update(sample(bad), 4, 2, 30)
