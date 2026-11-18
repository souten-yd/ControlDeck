"""ファン制御 v2 の本体（fan_core）のテスト。

目的: PID（＋電力の先読み）が、利用者の要件どおりに動くことを模擬と単体の入力で確かめる。
  - 休みから急に全力: 上限を超えず、緊急の最大回転に入らない
  - 上限を超えたら即座に最大回転。目標まで下がるまで保持
  - 温度の上がりを遅らせずに見る（遅らせた版は実機で 93〜95°C まで行き過ぎた）
  - 上乗せ（積分）は休み中・緊急中に動かさず、熱モデルへ保存して再起動後も引き継ぐ
  - 外気温が 17°C 上がっても（冬に学んだ熱モデルのまま）、上乗せで目標に戻り上限を超えない
根拠: 2026-11-17・18 利用者の指示（温度マップは外し PID だけ、回転の振れは気にせず温度を一定に、
外気温 37°C まで）。熱モデルは R9700 の実測（data/amd-fan-model.json、210 W 上限）。
"""
from __future__ import annotations

import pytest

from app.monitoring import fan_core as core
from app.monitoring.fan_pid import ThermalModel

SENSORS = [{"id": "temp1", "label": "edge", "kind": "edge"},
           {"id": "temp2", "label": "junction", "kind": "junction"},
           {"id": "temp3", "label": "mem", "kind": "mem"}]
LIMITS = {"temp1": 85.0, "temp2": 95.0, "temp3": 90.0}


def r9700(ambient_add: float = 0.0) -> ThermalModel:
    return ThermalModel.from_json({
        "idle_c": {"temp1": 25.1 + ambient_add, "temp2": 29.0 + ambient_add, "temp3": 24.1 + ambient_add},
        "idle_power_w": 3.5,
        "points": [{"fan": 50, "power_w": 198.7, "r": {"temp1": 0.166, "temp2": 0.272, "temp3": 0.242}},
                   {"fan": 60, "power_w": 196.1, "r": {"temp1": 0.117, "temp2": 0.214, "temp3": 0.194}},
                   {"fan": 70, "power_w": 197.4, "r": {"temp1": 0.086, "temp2": 0.181, "temp3": 0.162}}],
        "tau_s": 49.8, "gain_c_per_percent": -1.1, "hotspot_jump_c_per_w": 0.13,
    })


def settings(smoothing: float = 60.0) -> core.CoreSettings:
    return core.CoreSettings(limits_c=dict(LIMITS), margin_c=5.0, min_percent=20.0,
                             smoothing_s=smoothing, kp=2.5, ti=49.8)


def hot(value: float) -> list[dict]:
    return [{"id": "temp2", "kind": "junction", "value_c": value}]


def test_sudden_full_load_stays_inside_the_limit():
    """休みから急に全力でも、上限を超えず緊急の最大回転に入らない。"""
    result = core.simulate(r9700(), settings(), SENSORS, core.load_pattern("steady", 210, 3.5, 600),
                           warm_up=core.load_pattern("idle", 210, 3.5, 300))
    assert not result["over_limit"] and result["emergency_steps"] == 0
    assert result["headroom_c"] >= 1.0


def test_over_the_limit_is_full_speed_until_back_at_the_target():
    ctl = core.Controller()
    s = settings(300.0)
    for _ in range(5):
        step = ctl.step(hot(80.0), 200, 3.0, s, r9700())
    assert step.percent < 100 and not step.emergency
    step = ctl.step(hot(95.2), 200, 3.0, s, r9700())
    assert not step.emergency            # 1 回だけの跳ねでは入らない（hotspot は 1 秒だけ +3〜+7°C 跳ねる）
    step = ctl.step(hot(95.2), 200, 3.0, s, r9700())
    assert step.percent == 100 and step.emergency   # 3 回の中央値が上限 → 入る
    # 目標（90）より上にいる間は最大のまま
    for _ in range(10):
        step = ctl.step(hot(92.0), 200, 3.0, s, r9700())
    assert step.percent == 100


def test_rising_temperature_is_seen_without_delay():
    """上がる温度は遅らせない: 中央値の窓（3 回）がそろえば誤差に出る。下がる側だけゆっくり。"""
    ctl = core.Controller()
    s = settings(120.0)
    for _ in range(5):
        ctl.step(hot(70.0), 200, 3.0, s, r9700())
    for _ in range(4):
        step = ctl.step(hot(93.0), 200, 3.0, s, r9700())
    assert step.error_c > 2.5          # 目標 90 を 3°C 超えたことが 12 秒で誤差に出る
    for _ in range(3):
        step = ctl.step(hot(70.0), 200, 3.0, s, r9700())
    assert step.pv_c["temp2"] > 75.0   # 下がる側はゆっくり


def test_integral_is_kept_while_idle_and_during_an_emergency():
    """上乗せ（先読みの不足分）は、休み中と緊急の最大回転の間に動かさない。"""
    ctl = core.Controller()
    s = settings()
    model = r9700()
    for _ in range(40):
        ctl.step(hot(91.5), 200, 3.0, s, model)
    assert ctl.integral > 0
    for _ in range(30):                        # 休みに入る（下がる側の平滑で、しばらくは目標の近くに見える）
        ctl.step(hot(45.0), 4, 3.0, s, model)
    learned = ctl.integral
    for _ in range(100):                       # 休みが続く（電力が低い）
        ctl.step(hot(45.0), 4, 3.0, s, model)
    assert ctl.integral == pytest.approx(learned)
    ctl.step(hot(96.0), 200, 3.0, s, model)
    ctl.step(hot(96.0), 200, 3.0, s, model)    # ここから緊急の最大回転
    learned = ctl.integral
    for _ in range(5):
        step = ctl.step(hot(96.0), 200, 3.0, s, model)
    assert step.emergency and ctl.integral == pytest.approx(learned)


def test_integral_is_saved_in_the_model_and_restored_after_a_restart():
    model = r9700()
    first = core.Controller()
    for _ in range(40):
        first.step(hot(91.5), 200, 3.0, settings(), model)
    assert model.integral_percent == pytest.approx(first.integral)
    restored = ThermalModel.from_json(model.to_json())
    second = core.Controller()
    second.step(hot(60.0), 4, 3.0, settings(), restored)
    assert second.integral == pytest.approx(first.integral, abs=0.01)   # 保存は小数 2 桁


@pytest.mark.parametrize("pattern,period", [("steady", 0.0), ("square", 60.0), ("square", 120.0)])
def test_hot_summer_with_a_winter_model_stays_near_the_target(pattern, period):
    """外気温が 17°C 上がっても（熱モデルは冬のまま）、上乗せで目標近くに戻り、上限を超えない。"""
    winter, summer = r9700(), r9700(17.0)
    original = core.Plant.__init__

    def hot_room(self, model, *args, **kwargs):
        original(self, summer, *args, **kwargs)

    core.Plant.__init__ = hot_room
    try:
        result = core.simulate(winter, settings(), SENSORS,
                               core.load_pattern(pattern, 210, 3.5, 1800, period_s=period or 20.0),
                               warm_up=core.load_pattern("steady", 210, 3.5, 900))
    finally:
        core.Plant.__init__ = original
    assert result["emergency_steps"] == 0
    assert result["peak_c"]["temp2"] < 94.0


def test_tune_uses_the_strong_simc_gains():
    """自動調整の効き具合: Kp は閉ループの時定数を遅れ時間と同じにした SIMC（R9700 で約 2.5）、
    Ti は熱の時定数の 1.6 倍（約 80 秒。2 段の模型でマージン 5°C・外気温 +10°C まで緊急 0 回だった値）。"""
    result = core.tune(r9700(), settings(), SENSORS, 210)
    assert result["kp"] == pytest.approx(2.5, abs=0.1)
    assert result["ti"] == pytest.approx(79.7, abs=0.5)
    assert "steady" in result["simulated"]


def test_without_a_thermal_model_the_pid_still_follows_the_target():
    """熱モデルがまだ無い（先読みなし）でも、PI だけで目標へ向かって回転を上げる。"""
    ctl = core.Controller()
    s = settings()
    outs = [ctl.step(hot(93.0), 200, 3.0, s, None).percent for _ in range(40)]
    assert outs[-1] > outs[0] + 5 and outs == sorted(outs)


def test_after_an_emergency_the_slow_descent_does_not_inflate_the_integral():
    """緊急の最大回転の後、回転をゆっくり下げている間（冷えて見える）に上乗せを動かさない。
    本番 2026-11-18: 実際の回転数へ寄せていたので 3 → 17.6% に膨らみ、86% で回り続けた。"""
    ctl = core.Controller()
    s = settings()
    model = r9700()
    for _ in range(30):
        ctl.step(hot(90.0), 200, 3.0, s, model)
    before = ctl.integral
    for _ in range(3):
        ctl.step(hot(96.0), 200, 3.0, s, model)          # 緊急
    for _ in range(20):
        step = ctl.step(hot(78.0), 200, 3.0, s, model)   # 最大回転で冷えた。ゆっくり下げている途中
        assert step.percent > step.feedforward + step.p_term + step.i_term
    assert ctl.integral <= before + 0.6        # 膨らまない（下がる側は PI の通常の動き）


def test_a_too_large_integral_comes_back_down_under_load():
    """上乗せが大きすぎて、負荷中でも回転を下げきって冷えすぎているなら、ゆっくり減らす（冷えたまま残さない）。"""
    model = r9700()
    model.integral_percent = 17.6
    ctl = core.Controller()
    s = settings()
    for _ in range(300):
        ctl.step(hot(80.0), 200, 3.0, s, model)
    assert ctl.integral < 12.0


def test_idle_is_judged_from_the_raw_power_not_the_slow_feedforward_power():
    """休み（上乗せを動かさない）は生の電力で判定する。先読みの電力はゆっくり下げてあるので、休みでも高く見える。"""
    ctl = core.Controller()
    s = settings()
    model = r9700()
    for _ in range(30):
        ctl.step(hot(91.5), 200, 3.0, s, model)
    learned = ctl.integral
    for _ in range(10):
        ctl.step(hot(60.0), 8, 3.0, s, model)            # 休みに入った直後
    assert ctl.integral == pytest.approx(learned)


def test_without_a_power_reading_the_integral_still_works():
    """電力が読めないときも、休みとみなして上乗せを止めない（P だけの制御にならない。レビュー指摘）。"""
    ctl = core.Controller()
    s = settings()
    for _ in range(30):
        ctl.step(hot(92.0), None, 3.0, s, r9700())
    assert ctl.integral > 0.5


def test_light_load_at_minimum_fan_does_not_inflate_the_integral():
    """軽い負荷で最低回転・目標よりずっと下のとき、上乗せを上へ寄せない（レビュー指摘: 最低で寄せると +6% 膨らむ）。"""
    model = r9700()
    ctl = core.Controller()
    s = settings()
    for _ in range(200):
        step = ctl.step(hot(60.0), 60, 3.0, s, model)
    assert step.percent == pytest.approx(20.0)
    assert -0.5 <= ctl.integral <= 0.5
