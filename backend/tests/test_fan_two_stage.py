"""実機の記録に当てはめた 2 段の熱の模型で、ファン制御がマージン 5°C で緊急の最大回転に入らないことを確かめる。

目的: 2026-11-18 利用者「温度がオーバーした」「模擬で確かめてマージン 5 度でゲイン調整できると良い」。
本番（v0.68）では、負荷が止まると回転を急に下げ、再開で 95°C に届いて緊急の最大回転に入り、その後に上乗せが
膨らんで回り続けた。hotspot の読みは全力中でも 1 秒だけ +3〜+7°C 跳ねる。
fan_core の簡単な模型（瞬間の跳ね＋1 段）は直す前の版でも最高 90.7°C で本番を再現できなかったので、
本番の 1 秒ごとの記録に「放熱板（遅い・回転で冷え方が変わる）＋ダイ（速い）」の模型を当てはめた
（hotspot の誤差 1.3°C。tests/fixtures/fan_r9700_two_stage.json）。この模型と実際の揺れの分布で、直す前の版は
本番と同じく最高 98°C・緊急 5 回を再現し、直した版は 0 回。
  - 記録した実際の電力の流れ・全力と 20〜60 秒の休みの繰り返し・休みから全力を、外気温そのまま／+10°C で流す
  - 上乗せは本番と同じく育った状態から（全力 15 分の慣らし）
  - 緊急の最大回転 0 回、全力中の平均が目標 90°C の ±2.5°C
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from app.monitoring import fan_core as core
from app.monitoring.fan_pid import ThermalModel

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "fan_r9700_two_stage.json").read_text())
IDS = {"junction": "temp2", "mem": "temp3", "edge": "temp1"}
SENSORS = [{"id": "temp1", "kind": "edge"}, {"id": "temp2", "kind": "junction"}, {"id": "temp3", "kind": "mem"}]
LIMITS = {"temp1": 85.0, "temp2": 95.0, "temp3": 90.0}


class TwoStage:
    """放熱板 S（空気への熱抵抗 a·(f/50)^-b、時定数 tau_s）とセンサー T（S＋k·P、時定数 tau_d）。1 秒刻み。"""

    def __init__(self, ambient_add: float) -> None:
        self.p = FIXTURE["sensors"]
        self.fan = 20.0
        self.S = {k: v["amb"] + ambient_add for k, v in self.p.items()}
        self.T = dict(self.S)
        self.add = ambient_add

    def step(self, fan: float, power: float) -> None:
        heat = max(0.0, power - FIXTURE["idle_w"])
        self.fan += (fan - self.fan) * (1 - math.exp(-1.0 / self.p["junction"]["tau_f"]))
        for key, q in self.p.items():
            r = q["a"] * (max(self.fan, 10.0) / 50.0) ** (-q["b"])
            self.S[key] += (q["amb"] + self.add + r * heat - self.S[key]) * (1 - math.exp(-1.0 / q["tau_s"]))
            self.T[key] += (self.S[key] + q["k"] * heat - self.T[key]) * (1 - math.exp(-1.0 / q["tau_d"]))


def _pauses() -> list[float]:
    rnd = random.Random(7)
    out = [12.0] * 300
    for _ in range(14):
        out += [210.0] * rnd.randint(120, 240) + [12.0] * rnd.randint(20, 60)
    return out


def _llm(seed: int) -> list[float]:
    """LLM らしい負荷: 生成（130〜160 W）と読み込み（205〜225 W）を 5〜40 秒で行き来し、ときどき休む。
    2026-11-18 14:26 の本番はこの形で、負荷中に回転を下げすぎて上限に届いた（一定の全力では再現しなかった）。"""
    rnd = random.Random(seed)
    out = [12.0] * 300
    for _ in range(40):
        for _ in range(rnd.randint(2, 8)):
            out += [rnd.uniform(130, 160)] * rnd.randint(10, 40) + [rnd.uniform(205, 225)] * rnd.randint(5, 30)
        out += [12.0] * rnd.randint(5, 120)
    return out


def _run(power: list[float], ambient_add: float, margin: float = 5.0) -> dict:
    # 先読みに使う熱モデルは、記録したときの本番のもの（上乗せは 0 から慣らしで育てる）
    model = ThermalModel.from_json(FIXTURE["controller_model"])
    kp, ti = core.pid_gains(model)
    settings = core.CoreSettings(limits_c=dict(LIMITS), margin_c=margin, min_percent=20.0, smoothing_s=60.0, kp=kp, ti=ti)
    plant = TwoStage(ambient_add)
    ctl = core.Controller()
    rnd = random.Random(3)
    out = 20.0
    for i, pw in enumerate([210.0] * 900 + [12.0] * 120):   # 慣らし（上乗せが育った状態にする）
        plant.step(out, pw)
        if i % 3 == 0:
            out = ctl.step([{**s, "value_c": round(plant.T[k])} for k, s in zip(IDS, [SENSORS[1], SENSORS[2], SENSORS[0]])],
                           pw, 3.0, settings, model).percent
    emergencies = 0
    loaded: list[float] = []
    for i, pw in enumerate(power):
        plant.step(out, pw)
        if i % 3:
            continue
        reading = {IDS[k]: round(v + (rnd.choice(FIXTURE["hotspot_noise_c"]) if k == "junction" and pw > 120
                                      else rnd.gauss(0, 0.3))) for k, v in plant.T.items()}
        step = ctl.step([{**s, "value_c": reading[s["id"]]} for s in SENSORS], pw, 3.0, settings, model)
        out = step.percent
        emergencies += int(step.emergency)
        if pw > 120 and i > 300:
            loaded.append(plant.T["junction"])
    return {"emergencies": emergencies, "mean": sum(loaded) / len(loaded)}


@pytest.mark.parametrize("ambient_add", [0.0, 10.0])
@pytest.mark.parametrize("pattern", ["recorded", "pauses", "step"])
def test_margin_five_holds_without_emergency_on_the_fitted_r9700_model(pattern, ambient_add):
    power = {"recorded": [float(x) for x in FIXTURE["power_w"]], "pauses": _pauses(),
             "step": [12.0] * 300 + [210.0] * 600}[pattern]
    result = _run(power, ambient_add)
    assert result["emergencies"] == 0
    # 目標 90°C を超えない・冷やしすぎない。負荷中は先読みを床にし、電力の跳ね上がりで上乗せするので、外気温が
    # 低いときは目標より冷たく保つ（2026-11-18 利用者「回転数変動は気にしなくて良い。温度変動を緩慢に」）
    assert 80.0 <= result["mean"] <= 92.5



@pytest.mark.parametrize("seed", [1, 2, 3])
def test_llm_like_load_holds_margin_five_without_emergency(seed):
    """軽い局面と重い局面を行き来する負荷で、マージン 5°C・外気温 +4°C でも緊急の最大回転に入らない。
    直す前は 1 時間あまりで 350〜430 回（3 秒ごとの数）入り、模型の最高は 111〜113°C だった。"""
    result = _run(_llm(seed), 4.0)
    assert result["emergencies"] == 0
    assert result["mean"] <= 90.0
