"""実機に当てはめた 2 段の熱の模型（放熱板＋ダイ）。ファン制御の最適化（自動調整の模擬）とテストに使う。

2026-11-18 利用者「熱モデルをどんどんブラッシュアップしていると思うが、そのモデルもプロジェクトに書き込んで
ファン制御の最適化に使えるようにし、更新したら新しいものに更新することとして」。

* 模型は `plant_models/*.json`（版・作った日・誤差つき）。GPU の PCI device ID で選ぶ。
* 本番の 1 秒ごとの記録（fan_log の `fanraw-*.csv`）から当てはめ直せる（`fit`）。
  `python -m app.maintenance.fan_plant_fit --write` は data の `amd-fan-plant.json` へ保存し、本番はこちらを優先する。
  `--export <path>` で書き出した JSON を開発側の `plant_models/` へ入れ、PR で同梱の模型を更新する。

模型（1 秒刻み）: 放熱板 S は 外気温＋空気への熱抵抗 a·(f/50)^-b × 熱 に時定数 tau_s で近づき、センサー T は
S ＋ k × 熱 に時定数 tau_d で近づく。f はファンの回転で、指示に時定数 tau_f で追いつく。熱＝電力 − アイドル電力。
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from app.config import data_dir

PLANT_DIR = Path(__file__).resolve().parent / "plant_models"
LOCAL_FILE = "amd-fan-plant.json"
KINDS = ("junction", "mem", "edge")


@dataclass
class PlantModel:
    data: dict

    @property
    def version(self) -> int:
        return int(self.data.get("version", 0))

    @property
    def sensors(self) -> dict:
        return self.data["sensors"]

    @property
    def idle_w(self) -> float:
        return float(self.data.get("idle_w", 0.0))

    @property
    def noise(self) -> list[float]:
        return [float(x) for x in self.data.get("hotspot_noise_c") or [0.0]]

    def summary(self) -> dict:
        return {"name": self.data.get("name", ""), "version": self.version, "fitted_at": self.data.get("fitted_at", ""),
                "fit_rmse": self.data.get("fit_rmse", {}), "source": self.data.get("source", "")}


class TwoStage:
    """模型の温度を 1 秒ずつ進める。ambient_add は外気温の上乗せ（°C）。"""

    def __init__(self, plant: PlantModel, ambient_add: float = 0.0, start_fan: float = 20.0) -> None:
        self.p = plant.sensors
        self.idle_w = plant.idle_w
        self.fan = start_fan
        # ファンの遅れはセンサーごと（当てはめたときと同じ。VRAM は 18 秒、hotspot は 1 秒）
        self.fans = {k: start_fan for k in self.p}
        self.add = ambient_add
        self.S = {k: v["amb"] + ambient_add for k, v in self.p.items()}
        self.T = dict(self.S)

    def step(self, fan: float, power: float, dt: float = 1.0) -> None:
        heat = max(0.0, power - self.idle_w)
        for key, q in self.p.items():
            self.fans[key] += (fan - self.fans[key]) * (1 - math.exp(-dt / max(0.05, q["tau_f"])))
            r = q["a"] * (max(self.fans[key], 10.0) / 50.0) ** (-q["b"])
            self.S[key] += (q["amb"] + self.add + r * heat - self.S[key]) * (1 - math.exp(-dt / q["tau_s"]))
            self.T[key] += (self.S[key] + q["k"] * heat - self.T[key]) * (1 - math.exp(-dt / max(0.05, q["tau_d"])))
        self.fan = self.fans.get("junction", fan)


def _builtin() -> list[PlantModel]:
    out = []
    for path in sorted(PLANT_DIR.glob("*.json")):
        try:
            out.append(PlantModel(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def pci_device(bdf: str) -> str:
    try:
        return (Path("/sys/bus/pci/devices") / bdf / "device").read_text().strip().lower()
    except OSError:
        return ""


def load(bdf: str | None = None, pci: str | None = None) -> PlantModel | None:
    """この GPU の模型。手元で当てはめ直したもの（data）を優先し、無ければ同梱のもの。"""
    local = data_dir() / LOCAL_FILE
    if bdf:
        try:
            saved = json.loads(local.read_text(encoding="utf-8")).get(bdf)
            if saved:
                return PlantModel(saved)
        except (OSError, ValueError, AttributeError):
            pass
    device = (pci or (pci_device(bdf) if bdf else "")).lower()
    for plant in _builtin():
        if device and str(plant.data.get("match", {}).get("pci_device", "")).lower() == device:
            return plant
    return None


def builtin(name: str = "amd-r9700") -> PlantModel:
    return PlantModel(json.loads((PLANT_DIR / f"{name}.json").read_text(encoding="utf-8")))


def save_local(bdf: str, plant: PlantModel) -> None:
    path = data_dir() / LOCAL_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            document = {}
    except (OSError, ValueError):
        document = {}
    document[bdf] = plant.data
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---- 負荷の型（模擬の試験に使う） ----

def llm_load(seed: int, idle_w: float = 12.0) -> list[float]:
    """LLM らしい負荷（1 秒刻み）: 生成（130〜160 W）と読み込み（205〜225 W）を 5〜40 秒で行き来し、ときどき休む。
    2026-11-18 14:26 の本番はこの形で、負荷中に回転を下げすぎて上限に届いた（一定の全力では再現しなかった）。"""
    rnd = random.Random(seed)
    out = [idle_w] * 300
    for _ in range(40):
        for _ in range(rnd.randint(2, 8)):
            out += [rnd.uniform(130, 160)] * rnd.randint(10, 40) + [rnd.uniform(205, 225)] * rnd.randint(5, 30)
        out += [idle_w] * rnd.randint(5, 120)
    return out


# ---- 当てはめ（1 秒ごとの記録から。numpy だけ） ----

def _simulate_sensor(q: dict, fan: list[float], power: list[float], idle_w: float, start: float) -> list[float]:
    # 放熱板の出発点は、最初の読みからダイの跳ね（k × 熱）を引いたもの（全力中から始まる記録で 20°C ずれていた）
    t = start
    s = start - q["k"] * max(0.0, power[0] - idle_w)
    f = fan[0]
    out = []
    for cmd, pw in zip(fan, power):
        heat = max(0.0, pw - idle_w)
        f += (cmd - f) * (1 - math.exp(-1.0 / max(0.05, q["tau_f"])))
        r = q["a"] * (max(f, 10.0) / 50.0) ** (-q["b"])
        s += (q["amb"] + r * heat - s) * (1 - math.exp(-1.0 / max(1.0, q["tau_s"])))
        t += (s + q["k"] * heat - t) * (1 - math.exp(-1.0 / max(0.05, q["tau_d"])))
        out.append(t)
    return out


BOUNDS = {"amb": (10.0, 50.0), "a": (0.02, 1.0), "b": (0.3, 3.0), "tau_s": (5.0, 300.0), "k": (0.0, 0.5),
          "tau_d": (0.05, 10.0), "tau_f": (0.05, 60.0)}


def _rmse(q: dict, fan: list[float], power: list[float], temp: list[float], idle_w: float) -> float:
    pred = _simulate_sensor(q, fan, power, idle_w, temp[0])
    return math.sqrt(sum((p - t) ** 2 for p, t in zip(pred, temp)) / max(1, len(temp)))


def fit_sensor(start: dict, fan: list[float], power: list[float], temp: list[float], idle_w: float,
               rounds: int = 6) -> tuple[dict, float]:
    """座標ごとの探索（倍率を狭めながら）。scipy を使わずに十分な精度が出る（模型の当てはめは 7 個の数だけ）。"""
    best = {k: float(v) for k, v in start.items()}
    err = _rmse(best, fan, power, temp, idle_w)
    step = 0.4
    for _ in range(rounds):
        improved = True
        while improved:
            improved = False
            for key, (lo, hi) in BOUNDS.items():
                for factor in (1 + step, 1 / (1 + step)):
                    trial = dict(best)
                    trial[key] = min(hi, max(lo, best[key] * factor if key != "amb" else best[key] + (factor - 1) * 10))
                    trial_err = _rmse(trial, fan, power, temp, idle_w)
                    if trial_err < err - 1e-6:
                        best, err, improved = trial, trial_err, True
        step /= 2
    return best, err


def fit(samples: list[dict], base: PlantModel, sensor_ids: dict[str, str]) -> PlantModel:
    """1 秒ごとの記録（{t, fan, power, temps: {id: °C}}）から当てはめ直す。今の模型から始める。"""
    if len(samples) < 600:
        raise ValueError("当てはめには 10 分以上の 1 秒ごとの記録が要ります")
    fan = [float(s["fan"]) for s in samples]
    power = [float(s["power"]) for s in samples]
    sensors, rmse = {}, {}
    for kind, sid in sensor_ids.items():
        temp = [float(s["temps"][sid]) for s in samples]
        sensors[kind], err = fit_sensor(base.sensors[kind], fan, power, temp, base.idle_w)
        rmse[kind] = round(err, 2)
    data = {**base.data, "sensors": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in sensors.items()},
            "fit_rmse": rmse, "version": base.version + 1}
    return PlantModel(data)
