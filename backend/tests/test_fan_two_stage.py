"""実機に当てはめた 2 段の熱の模型（app/monitoring/plant_models/amd-r9700.json）で、ファン制御が緊急の最大回転に
入らないことを確かめる。

目的: 2026-11-18 利用者「温度がオーバーした」「模擬で確かめてマージン 5 度でゲイン調整できると良い」
「またファンがオーバースピードした。運転中も室温が微妙に上がるのと、電力負荷が急変した際はフィードフォワードを」
「熱モデルもプロジェクトに書き込んでファン制御の最適化に使えるように」。
  - 記録した実際の電力の流れ・全力と休みの繰り返し・休みから全力を、外気温そのまま／+10°C で流す
  - LLM らしい負荷（軽い局面と重い局面を行き来する。14:26 の本番はこの形で、負荷中に回転を下げすぎた）
  - 上乗せは本番と同じく育った状態から（全力 15 分の慣らし）。制御は本番と同じ 3 秒ごと
"""
from __future__ import annotations

import copy
import random

import pytest

from app.monitoring import fan_core as core
from app.monitoring import fan_plant
from app.monitoring.fan_pid import ThermalModel

PLANT = fan_plant.builtin()
LIMITS = {"temp1": 85.0, "temp2": 95.0, "temp3": 90.0}


def _settings(margin: float) -> tuple[core.CoreSettings, ThermalModel]:
    # 先読みに使う熱モデルは、記録したときの本番のもの（50〜70% だけ測った点）
    model = ThermalModel.from_json(PLANT.data["controller_model"])
    kp, ti = core.pid_gains(model)
    return core.CoreSettings(limits_c=dict(LIMITS), margin_c=margin, min_percent=20.0, smoothing_s=60.0, kp=kp, ti=ti), model


def _pauses(lo: int = 20, hi: int = 60) -> list[float]:
    rnd = random.Random(7)
    out = [12.0] * 300
    for _ in range(14):
        out += [210.0] * rnd.randint(120, 240) + [12.0] * rnd.randint(lo, hi)
    return out


def _run(power: list[float], ambient_add: float, margin: float = 5.0, plant=PLANT, ramp: float = 0.0, seed: int = 3) -> dict:
    settings, model = _settings(margin)
    return core.simulate_plant(plant, model, settings, power, dt=3.0, ambient_add=ambient_add, ambient_ramp=ramp, seed=seed)


@pytest.mark.parametrize("ambient_add", [0.0, 10.0])
@pytest.mark.parametrize("pattern", ["recorded", "pauses", "long_pauses", "step"])
def test_margin_five_holds_without_emergency_on_the_fitted_r9700_model(pattern, ambient_add):
    power = {"recorded": [float(x) for x in PLANT.data["sample_power_w"]], "pauses": _pauses(),
             "long_pauses": _pauses(60, 180), "step": [12.0] * 300 + [210.0] * 600}[pattern]
    result = _run(power, ambient_add)
    assert result["emergencies"] == 0
    assert result["mean_c"] <= 92.5
    # 一定の全力では目標（90°C）の近くまで戻る（先読みが多すぎても上乗せがマイナス側で直す。レビュー指摘 2026-11-18）。
    # 休みを挟むと再開のたびに床と跳ね上がりの上乗せで先に冷やすので、目標より冷ため
    assert result["mean_c"] >= (87.5 if pattern == "step" else 83.0)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_llm_like_load_holds_margin_five_without_emergency(seed):
    """軽い局面と重い局面を行き来する負荷で、マージン 5°C・外気温 +4°C から運転中に +3°C でも緊急の最大回転に
    入らない。直す前は 1 時間あまりで 350〜430 回（3 秒ごとの数）入り、模型の最高は 111〜113°C だった。"""
    result = _run(fan_plant.llm_load(seed), 4.0, ramp=3.0, seed=seed)
    assert result["emergencies"] == 0
    assert result["peak_c"] < 95.0 and result["mean_c"] <= 90.0


def test_a_hotter_gpu_holds_margin_six_under_llm_load():
    """模型より熱くなりやすい GPU（ダイ ×1.3・放熱 ×1.15）でも、マージン 6°C なら入らない（本番の今の設定）。"""
    hot = fan_plant.PlantModel(copy.deepcopy(PLANT.data))
    for q in hot.sensors.values():
        q["k"] *= 1.3
        q["a"] *= 1.15
    for seed in (1, 2, 3):
        assert _run(fan_plant.llm_load(seed), 4.0, margin=6.0, plant=hot, ramp=3.0, seed=seed)["emergencies"] == 0
