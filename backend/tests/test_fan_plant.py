"""熱の模型（2 段）をプロジェクトに持ち、最適化に使い、本番の 1 秒ごとの記録から当てはめ直せること。

目的: 2026-11-18 利用者「熱モデルもプロジェクトに書き込んでファン制御の最適化に使えるようにし、更新したら新しい
ものに更新することとして」「ファンの制御周期が遅い気がする」。
"""
from __future__ import annotations

import copy
import csv
import random

from app.monitoring import fan_core as core
from app.monitoring import fan_log, fan_plant
from app.monitoring.fan_pid import ThermalModel


def test_the_r9700_model_is_packaged_and_chosen_by_pci_id(tmp_path, monkeypatch):
    monkeypatch.setattr(fan_plant, "data_dir", lambda: tmp_path)
    plant = fan_plant.load(pci="0x7551")
    assert plant is not None and plant.version >= 1 and set(plant.sensors) == set(fan_plant.KINDS)
    assert fan_plant.load(pci="0x1234") is None
    # 手元で当てはめ直したもの（data）を優先する
    newer = fan_plant.PlantModel({**copy.deepcopy(plant.data), "version": plant.version + 1})
    fan_plant.save_local("0000:03:00.0", newer)
    assert fan_plant.load("0000:03:00.0").version == plant.version + 1


def test_fit_recovers_a_known_plant_from_one_second_samples():
    truth = fan_plant.builtin()
    rnd = random.Random(5)
    twin = fan_plant.TwoStage(truth)
    samples = []
    fan, power = 40.0, 12.0
    for i in range(1500):
        if i % 60 == 0:
            power = rnd.choice([12.0, 140.0, 210.0])
            fan = rnd.choice([30.0, 45.0, 60.0, 75.0])
        twin.step(fan, power)
        samples.append({"fan": fan, "power": power, "temps": dict(twin.T)})
    start = fan_plant.PlantModel(copy.deepcopy(truth.data))
    for q in start.sensors.values():      # ずらしたところから始める
        q["a"] *= 1.3
        q["tau_s"] *= 0.7
        q["k"] *= 0.8
    before = fan_plant._rmse(start.sensors["junction"], [s["fan"] for s in samples], [s["power"] for s in samples],
                             [s["temps"]["junction"] for s in samples], truth.idle_w)
    fitted = fan_plant.fit(samples, start, {k: k for k in fan_plant.KINDS})
    assert fitted.version == start.version + 1
    assert fitted.data["fit_rmse"]["junction"] < before / 3 and fitted.data["fit_rmse"]["junction"] < 1.0


def test_raw_log_round_trip_and_keeps_two_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(fan_log, "_root", lambda: tmp_path)
    t0 = 1_800_000_000.0
    for i in range(3):
        fan_log.add_raw(t0 + i, fan=40.0, power=150.0, temps={"junction": 80, "mem": 70, "edge": 60}, rpm=2000)
    rows = fan_log.read_raw(t0 - 1)
    assert len(rows) == 3 and rows[0]["temps"]["junction"] == 80 and rows[0]["fan"] == 40.0
    fan_log.add_raw(t0 + 4 * 3600, fan=40.0, power=150.0, temps={"junction": 80, "mem": 70, "edge": 60}, rpm=2000)
    assert len(list(tmp_path.glob("fanraw-*.csv"))) == 1           # 2 時間より古いものは消える


def test_minute_log_with_an_old_header_is_upgraded_before_appending(tmp_path, monkeypatch):
    """列（floor・rsv）を足したので、今日の古い見出しのファイルに足すと列がずれていた。書き直してから足す。"""
    monkeypatch.setattr(fan_log, "_root", lambda: tmp_path)
    old = [f for f in fan_log.FIELDS if f not in ("floor", "rsv")]
    t = 1_800_000_060
    path = fan_log._file(fan_log.datetime.fromtimestamp(t))
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(old)
        writer.writerow([t - 60, 20] + [""] * (len(old) - 2))
    fan_log._write({"t": t, "n": 20, "floor": 45.0, "rsv": 3.2})
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["t"] == str(t - 60) and rows[1]["floor"] == "45.0" and rows[1]["rsv"] == "3.2"


def test_tune_optimizes_gains_on_the_plant_and_keeps_the_centre_when_differences_are_noise():
    plant = fan_plant.builtin()
    model = ThermalModel.from_json(plant.data["controller_model"])
    base = core.CoreSettings(limits_c={"temp1": 85.0, "temp2": 95.0, "temp3": 90.0}, margin_c=5.0, min_percent=20.0)
    sensors = [{"id": "temp2", "kind": "junction"}, {"id": "temp3", "kind": "mem"}, {"id": "temp1", "kind": "edge"}]
    found = core.tune(model, base, sensors, 210.0, plant=plant)
    assert found["optimized"]["plant"]["version"] == plant.version
    assert len(found["optimized"]["tried"]) == 9
    assert all(t["emergencies"] == 0 for t in found["optimized"]["tried"] if t["centre"])
    assert (found["kp"], found["ti"]) == core.pid_gains(model)
