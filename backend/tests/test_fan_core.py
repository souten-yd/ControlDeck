"""ファン制御 v2 の本体（fan_core）のテスト。

目的: 2 方式（PID＋電力の先読み / 温度マップ）が、利用者の要件どおりに動くことを模擬で確かめる。
  - 休みから急に全力: 上限を超えず、最大回転に跳ねない（マージンの範囲で止まる）
  - 0%↔100% の往復負荷: 回転が往復を追いかけない
  - 上限を超えたら即座に最大回転
  - 自動調整は上限を超えない値のうち、回転が最も低いものを選ぶ
根拠: 2026-11-17 利用者の指示。熱モデルは R9700 の実測（data/amd-fan-model.json、210 W 上限）。
"""
from __future__ import annotations

import pytest

from app.monitoring import fan_core as core
from app.monitoring.fan_pid import ThermalModel

SENSORS = [{"id": "temp1", "label": "edge", "kind": "edge"},
           {"id": "temp2", "label": "junction", "kind": "junction"},
           {"id": "temp3", "label": "mem", "kind": "mem"}]
LIMITS = {"temp1": 85.0, "temp2": 90.0, "temp3": 90.0}


def r9700() -> ThermalModel:
    return ThermalModel.from_json({
        "idle_c": {"temp1": 32.0, "temp2": 36.0, "temp3": 30.2}, "idle_power_w": 7.9,
        "points": [{"fan": 50, "power_w": 198.7, "r": {"temp1": 0.166, "temp2": 0.272, "temp3": 0.242}},
                   {"fan": 60, "power_w": 196.1, "r": {"temp1": 0.117, "temp2": 0.214, "temp3": 0.194}},
                   {"fan": 70, "power_w": 197.4, "r": {"temp1": 0.086, "temp2": 0.181, "temp3": 0.162}}],
        "tau_s": 49.8, "gain_c_per_percent": -1.1, "hotspot_jump_c_per_w": 0.13,
    })


def settings(mode: str, smoothing: float = 90.0) -> core.CoreSettings:
    return core.CoreSettings(mode=mode, limits_c=dict(LIMITS), margin_c=5.0, min_percent=20.0,
                             smoothing_s=smoothing, kp=1.33, ti=49.8)


@pytest.mark.parametrize("mode", ["pid", "map"])
def test_sudden_full_load_stays_inside_the_margin(mode):
    """休みから急に全力でも、上限を超えず緊急の最大回転に入らない。"""
    model = r9700()
    result = core.simulate(model, settings(mode), SENSORS, core.load_pattern("steady", 210, 8, 600),
                           warm_up=core.load_pattern("idle", 210, 8, 300))
    assert not result["over_limit"] and result["emergency_steps"] == 0
    assert result["fan_max"] < 80
    assert result["headroom_c"] >= 1.0


@pytest.mark.parametrize("mode", ["pid", "map"])
def test_square_load_does_not_drive_the_fan_back_and_forth(mode):
    """10 秒ずつの 0%↔100% を続けても、回転の振れは 10 ポイント以内。"""
    model = r9700()
    result = core.simulate(model, settings(mode, 120.0), SENSORS,
                           core.load_pattern("square", 210, 8, 720, period_s=20.0),
                           warm_up=core.load_pattern("steady", 210, 8, 600))
    assert result["fan_swing"] <= 10.0
    assert not result["over_limit"]


def test_over_the_limit_is_full_speed_until_back_at_the_target():
    ctl = core.Controller()
    s = settings("pid", 300.0)
    hot = [{"id": "temp2", "kind": "junction", "value_c": 80.0}]
    for _ in range(5):
        step = ctl.step(hot, 200, 3.0, s, r9700())
    assert step.percent < 100 and not step.emergency
    step = ctl.step([{**hot[0], "value_c": 90.2}], 200, 3.0, s, r9700())
    assert step.percent == 100 and step.emergency
    # 目標（85）より上にいる間は最大のまま
    for _ in range(10):
        step = ctl.step([{**hot[0], "value_c": 87.0}], 200, 3.0, s, r9700())
    assert step.percent == 100


def test_map_shape_min_far_from_target_and_full_at_the_limit():
    s = settings("map")
    points = core.default_map(r9700(), SENSORS, s, 210)
    assert points[0] == (20.0, 20.0)
    at_target = points[-1][1]
    assert 50 <= at_target <= 70          # 全力で目標に落ち着く回転（熱モデル）＋余裕
    assert core.map_percent(points, 30, s) == 20
    assert core.map_percent(points, 0, s) == pytest.approx(at_target)
    assert core.map_percent(points, -5, s) == pytest.approx(100)   # 上限（目標 + マージン）で最大


def test_tune_picks_the_lowest_fan_that_is_safe_and_calm():
    result = core.tune(r9700(), settings("pid"), SENSORS, 210)
    chosen = next(t for t in result["trials"] if t["smoothing_s"] == result["smoothing_s"])
    assert chosen["safe"] and chosen["swing_ratio"] <= 1.0
    calmer_and_safe = [t for t in result["trials"] if t["safe"] and t["swing_ratio"] <= 1.0]
    assert chosen["fan_mean"] == min(t["fan_mean"] for t in calmer_and_safe)
    assert result["kp"] > 0 and result["ti"] > 0
    assert [p for _, p in result["map_points"]] == sorted(p for _, p in result["map_points"])


def test_without_a_thermal_model_the_pid_still_follows_the_target():
    """熱モデルがまだ無い（先読みなし）でも、PI だけで目標へ向かって回転を上げる。"""
    ctl = core.Controller()
    s = settings("pid", 60.0)
    outs = [ctl.step([{"id": "temp2", "kind": "junction", "value_c": 88.0}], 200, 3.0, s, None).percent
            for _ in range(40)]
    assert outs[-1] > outs[0] + 5 and outs == sorted(outs)
