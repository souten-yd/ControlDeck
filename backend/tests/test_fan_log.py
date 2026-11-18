"""ファン制御の診断の記録（fan_log）と、記録からの作り直しのテスト。

目的: 2026-11-18 利用者の依頼「温度を診断に記録して、PID が逸脱したとき長い自動調整をせずに直したい」
「全部記録すると容量がパンクするので、AI に読み込める程度の容量に」を満たすこと。
  - 3 秒ごとの値を 1 分 1 行にまとめる（1 日分でも AI が読める大きさ）。2 日より古い記録は消す
  - 目標 +2°C が 60 秒続いたときだけ逸脱として数える（全力の始まりの一瞬は数えない）。緊急も数える
  - 記録の「全力で落ち着いた 3 分」「休みで落ち着いた 3 分」から、熱モデルとゲインを作り直せる
  - 自己診断は、逸脱や緊急があれば「記録から作り直す」を勧める
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest

from app.monitoring import fan_log
from app.monitoring.fan_pid import ThermalModel

TARGETS = {"junction": 90.0, "mem": 85.0, "edge": 80.0}
IDS = {"edge": "temp1", "junction": "temp2", "mem": "temp3"}


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    monkeypatch.setattr(fan_log, "data_dir", lambda: tmp_path)
    return tmp_path / "diagnostics"


def feed(rec: fan_log.Recorder, start: float, seconds: float, *, hot: float, power: float, pct: float,
         mem: float = 60.0, edge: float = 55.0, emergency: bool = False) -> float:
    t = start
    while t < start + seconds:
        rec.add(t, temps={"junction": hot, "mem": mem, "edge": edge}, targets=TARGETS, power_w=power,
                rpm=pct * 55, percent=pct, ff=pct - 2, integral=2.0, error_c=hot - 90, emergency=emergency,
                tuning=False)
        t += 3.0
    return t


def test_one_row_per_minute_and_a_day_fits_an_ai_context(logdir):
    rows: list[dict] = []
    rec = fan_log.Recorder(write=rows.append)
    start = 1_800_000_000.0 - (1_800_000_000.0 % 60)
    feed(rec, start, 600, hot=88, power=210, pct=50)
    rec.flush()
    assert len(rows) == 10 and all(r["n"] == 20 for r in rows)
    # 実際のファイルで 1 日分（1440 行）の大きさを測る
    for i in range(1440):
        fan_log._write({**rows[i % 10], "t": start + i * 60})
    size = sum(p.stat().st_size for p in logdir.glob("fan-*.csv"))
    assert size < 150_000
    assert len(fan_log.read(since=start)) == 1440


def test_deviation_needs_a_minute_over_target_and_emergencies_are_counted():
    rows: list[dict] = []
    rec = fan_log.Recorder(write=rows.append)
    start = 1_800_000_000.0 - (1_800_000_000.0 % 60)
    t = feed(rec, start, 45, hot=93, power=210, pct=50)        # 45 秒だけ超え → 数えない
    t = feed(rec, t, 30, hot=88, power=210, pct=50)
    t = feed(rec, t, 90, hot=93, power=210, pct=50)            # 90 秒続けて超え → 1 回
    t = feed(rec, t, 9, hot=96, power=210, pct=100, emergency=True)
    feed(rec, t, 60, hot=60, power=8, pct=20)
    rec.flush()
    s = fan_log.summary(rows)
    assert s["deviations"] == 1
    assert 120 <= s["deviation_seconds"] <= 150
    assert s["emergency_minutes"] >= 1 and s["peak_c"]["hot"] == 96


def test_old_logs_are_pruned(logdir):
    fan_log._write({"t": time.time(), "n": 1})
    old = datetime.now() - timedelta(days=fan_log.KEEP_DAYS + 1)
    fan_log._write({"t": old.timestamp(), "n": 1})
    assert len(list(logdir.glob("fan-*.csv"))) == 2
    fan_log.prune()
    assert [p.name for p in logdir.glob("fan-*.csv")] == [f"fan-{datetime.now():%Y%m%d}.csv"]


def _minutes(rec, start, minutes, **kw):
    return feed(rec, start, minutes * 60, **kw)


def test_refit_rebuilds_idle_points_and_gain_from_the_log():
    rows: list[dict] = []
    rec = fan_log.Recorder(write=rows.append)
    t = 1_800_000_000.0 - (1_800_000_000.0 % 60)
    t = _minutes(rec, t, 5, hot=40, mem=35, edge=33, power=8, pct=20)        # 休み（外気温が上がった: 40°C）
    t = _minutes(rec, t, 5, hot=90, mem=66, edge=62, power=210, pct=50)      # 全力・50%
    t = _minutes(rec, t, 5, hot=80, mem=60, edge=56, power=210, pct=70)      # 全力・70%
    rec.flush()
    model = ThermalModel.from_json({"idle_c": {"temp1": 25, "temp2": 29, "temp3": 24}, "idle_power_w": 3.5,
                                    "points": [], "tau_s": 49.8, "gain_c_per_percent": -1.1})
    changes = fan_log.refit(rows, model, IDS, 210)
    assert model.idle_c["temp2"] == pytest.approx(40, abs=0.5)
    assert {p["fan"] for p in changes["points"]} == {50, 70}
    heat = 210 - model.idle_power_w
    p50 = next(p for p in model.points if p["fan"] == 50)
    assert p50["r"]["temp2"] == pytest.approx((90 - 40) / heat, rel=0.02)
    # 50%→70% で 10°C 下がる = 0.5°C/%
    assert model.gain_c_per_percent == pytest.approx(-0.5, rel=0.1)


def test_refit_needs_idle_and_loaded_records():
    rows: list[dict] = []
    rec = fan_log.Recorder(write=rows.append)
    t = 1_800_000_000.0 - (1_800_000_000.0 % 60)
    _minutes(rec, t, 6, hot=90, power=210, pct=50)
    rec.flush()
    with pytest.raises(ValueError, match="休み"):
        fan_log.refit(rows, ThermalModel(), IDS, 210)
    rows2: list[dict] = []
    rec2 = fan_log.Recorder(write=rows2.append)
    _minutes(rec2, t, 6, hot=40, power=8, pct=20)
    rec2.flush()
    with pytest.raises(ValueError, match="全力"):
        fan_log.refit(rows2, ThermalModel(), IDS, 210)


def test_selftest_recommends_refit_after_deviations(monkeypatch):
    from app.diagnostics import selftest
    from app.monitoring import fan_control

    monkeypatch.setattr(fan_control, "_load", lambda: fan_control.FanSettings(enabled=True))
    monkeypatch.setattr(fan_control, "log_summary", lambda hours: {
        "minutes": 600, "deviations": 4, "deviation_seconds": 300, "emergency_minutes": 0,
        "peak_c": {"hot": 93.0, "mem": 66.0}})
    status, text = selftest._fan()
    assert status == "warn" and "記録から作り直す" in text
    monkeypatch.setattr(fan_control, "log_summary", lambda hours: {
        "minutes": 600, "deviations": 1, "deviation_seconds": 70, "emergency_minutes": 0,
        "peak_c": {"hot": 91.0, "mem": 66.0}})
    assert selftest._fan()[0] is True
