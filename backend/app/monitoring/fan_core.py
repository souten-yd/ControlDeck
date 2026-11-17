"""ファン制御 v2 の本体（sysfs に触らない純粋な計算）。

方式は 2 つだけ（2026-11-17 利用者の指示で整理。旧: マップ・グラフ・PID・温度追従の 4 方式と 7 プリセット）。

* ``pid``: 「上限温度 − マージン」を目標に追従する PI 制御。電力から熱モデルで必要な回転数を先読み
  （フィードフォワード）して足す。
* ``map``: 温度マップ。目標（上限 − マージン）から何度下にいるかで回転数を決める。

どちらにも共通:

* 上限温度の手前に「マージン」を取って制御する。上限そのものを目標にすると、行き過ぎで必ず上限を超える。
* 上限を超えたら即座に最大回転にする（ソフトとファームウェアのカーブの両方）。目標まで下がるまで保持。
* 0%↔100% を往復する負荷に過敏に反応しない: 温度は「上げは速く・下げはゆっくり」の平滑、
  電力は熱の時定数ほどの遅れで平らにし、回転数の上げ下げにも速さの上限を付ける。
  その強さは 1 つの値「なめらかさ（秒）」で決める。
* 最低回転数を守る。目標まで温度を使い切るので、回転は必要最低限になる。

模擬（``Plant``）は学習済みの熱モデルから温度を計算し、自動調整とテストに使う。
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from statistics import median

from app.monitoring.fan_pid import (
    COLD_ERROR_LIMIT_C, HOTSPOT_FF_LAG_S, LOOP_DEAD_TIME_S, ThermalModel, clamp, simc_gains,
)

# 平滑の内訳（なめらかさ S 秒に対する比）。根拠は fan_core の模擬と R9700 実測（docs/design-amd-smart-fan.md）
PV_RISE_SHARE = 1 / 12      # 温度の上げ側の平滑: S=60 で 5 秒（PID の遅れを増やしすぎない）
PV_FALL_SHARE = 1 / 2       # 温度の下げ側: S=60 で 30 秒
POWER_RISE_S = 3.0          # 先読みの電力の上げ側は速く（休みから全力で hotspot が 10 秒台で上がるため）
POWER_FALL_SHARE = 3 / 2    # 先読みの電力の下げ側: S=60 で 90 秒（短い休みで回転を落とさない）
UP_SHARE = 1 / 4            # 回転の上げ: 最低→最大を S/4 秒（S=60 で 15 秒）
DOWN_SHARE = 2.0            # 回転の下げ: 最大→最低を 2S 秒（S=60 で 2 分）。往復負荷では高めを保つ
DEFAULT_SMOOTHING_S = 60.0
EMERGENCY_HOLD_S = 15.0
# 既定のマップ: 目標から何度下か → 目標での回転数に対する割合
MAP_SHAPE = ((20.0, 0.0), (10.0, 0.45), (5.0, 0.75), (0.0, 1.0))
MAP_DEFAULT_AT_TARGET = 60.0
MODEL_EXTRA_PERCENT = 3.0


def sensor_kind(sensor: dict) -> str:
    kind = sensor.get("kind")
    if kind:
        return kind
    label = str(sensor.get("label", "")).lower()
    if "junction" in label or "hotspot" in label:
        return "junction"
    if "mem" in label:
        return "mem"
    return "edge"


@dataclass
class CoreSettings:
    mode: str                         # "pid" | "map"
    limits_c: dict[str, float]        # センサーごとの上限温度
    margin_c: float = 5.0
    min_percent: float = 20.0
    max_percent: float = 100.0
    smoothing_s: float = DEFAULT_SMOOTHING_S
    kp: float = 1.33
    ti: float = 50.0
    # [(目標から何度下か, 回転数%)]。空なら熱モデルか既定値から作る
    map_points: list[tuple[float, float]] = field(default_factory=list)

    def targets(self) -> dict[str, float]:
        return {sid: limit - self.margin_c for sid, limit in self.limits_c.items()}


def default_map(model: ThermalModel | None, sensors: list[dict], settings: CoreSettings,
                full_power_w: float | None) -> list[tuple[float, float]]:
    """目標での回転数 = 電力上限いっぱいの負荷で目標に落ち着く回転数（熱モデル）＋少しの余裕。"""
    at_target = MAP_DEFAULT_AT_TARGET
    if model is not None and model.usable and full_power_w:
        heat = max(1.0, full_power_w - (model.idle_power_w or 0.0))
        need = settings.min_percent
        for sensor in sensors:
            sid = sensor["id"]
            idle = model.idle_c.get(sid)
            if idle is None or sid not in settings.limits_c:
                continue
            allowed = settings.targets()[sid] - idle
            need = max(need, settings.max_percent if allowed <= 0 else
                       model.fan_for(sid, allowed / heat, settings.min_percent, settings.max_percent))
        at_target = need + MODEL_EXTRA_PERCENT
    at_target = clamp(at_target, settings.min_percent, settings.max_percent)
    return [(below, round(settings.min_percent + share * (at_target - settings.min_percent), 1))
            for below, share in MAP_SHAPE]


def map_percent(points: list[tuple[float, float]], below_c: float, settings: CoreSettings) -> float:
    """目標から below_c 度下のときの回転数。目標を超えたら上限で最大になるよう直線で上げる。"""
    pts = sorted(points, key=lambda p: -p[0])  # 遠い（冷たい）順
    if below_c >= pts[0][0]:
        return max(settings.min_percent, pts[0][1])
    if below_c <= 0:
        at_target = pts[-1][1]
        over = min(1.0, -below_c / max(0.5, settings.margin_c))
        return at_target + over * (settings.max_percent - at_target)
    for (b0, p0), (b1, p1) in zip(pts, pts[1:]):
        if b1 <= below_c <= b0:
            share = (b0 - below_c) / max(1e-6, b0 - b1)
            return p0 + share * (p1 - p0)
    return pts[-1][1]


class _Lag:
    def __init__(self) -> None:
        self.value: float | None = None

    def update(self, x: float, dt: float, rise_s: float, fall_s: float) -> float:
        if self.value is None:
            self.value = x
        else:
            tau = rise_s if x >= self.value else fall_s
            self.value += (1.0 - math.exp(-dt / max(0.5, tau))) * (x - self.value)
        return self.value


@dataclass
class Step:
    percent: float
    mode: str
    governing: str
    error_c: float
    feedforward: float
    p_term: float
    i_term: float
    emergency: bool
    pv_c: dict[str, float]

    def as_dict(self) -> dict:
        return {"output_percent": round(self.percent, 1), "mode": self.mode, "governing_sensor": self.governing,
                "error_c": round(self.error_c, 2), "feedforward_percent": round(self.feedforward, 1),
                "p_percent": round(self.p_term, 1), "i_percent": round(self.i_term, 1),
                "emergency": self.emergency, "pv_c": {k: round(v, 1) for k, v in self.pv_c.items()}}


class Controller:
    """3 秒ごとに呼ぶ。返す回転数はなめらかさの上限つき（緊急時を除く）。"""

    def __init__(self) -> None:
        self.raw: dict[str, deque] = {}
        self.pv: dict[str, _Lag] = {}
        self.power = _Lag()
        self.integral = 0.0
        self.out: float | None = None
        self.emergency_until = -1.0
        self.emergency_active = False
        self.t = 0.0

    def reset(self, start_percent: float | None = None) -> None:
        self.__init__()
        self.out = start_percent

    def feedforward(self, model: ThermalModel | None, settings: CoreSettings, sensors: list[dict],
                    heat_w: float) -> float:
        if model is None or not model.usable or heat_w < 1.0:
            return settings.min_percent
        need = settings.min_percent
        targets = settings.targets()
        for sensor in sensors:
            sid = sensor["id"]
            idle = model.idle_c.get(sid)
            if idle is None or sid not in targets:
                continue
            allowed = targets[sid] - idle
            if allowed <= 0:
                return settings.max_percent
            need = max(need, model.fan_for(sid, allowed / heat_w, settings.min_percent, settings.max_percent))
        return need

    def step(self, sensors: list[dict], power_w: float | None, dt: float, settings: CoreSettings,
             model: ThermalModel | None = None) -> Step:
        self.t += dt
        s = max(10.0, settings.smoothing_s)
        targets = settings.targets()
        pv: dict[str, float] = {}
        errors: dict[str, float] = {}
        over_limit = False
        raw_over_target = False
        for sensor in sensors:
            sid = sensor["id"]
            if sid not in targets:
                continue
            value = float(sensor["value_c"])
            window = self.raw.setdefault(sid, deque(maxlen=3))
            window.append(value)
            # 上限の判定は生の値（1 回の跳ねも見逃さない）。ファームウェアのカーブも hotspot で同じことをする
            if value >= settings.limits_c[sid]:
                over_limit = True
            if median(window) > targets[sid]:
                raw_over_target = True
            # マップは温度だけで決めるので、上がる側は遅らせない（遅らせると急な上昇に間に合わない）
            rise_s = 2.0 if settings.mode == "map" else max(2.0, s * PV_RISE_SHARE)
            filtered = self.pv.setdefault(sid, _Lag()).update(median(window), dt, rise_s, s * PV_FALL_SHARE)
            pv[sid] = filtered
            errors[sid] = filtered - targets[sid]
        governing = max(errors, key=errors.get) if errors else ""
        error = errors.get(governing, -99.0)
        if over_limit:
            self.emergency_until = self.t + EMERGENCY_HOLD_S
        # 上限を超えたら最大。生の温度が目標まで戻る（かつ最低保持時間）まで続ける。
        # 平滑した温度で判定すると、跳ねた直後は目標を超えて見えず、すぐ解除してしまう
        emergency = over_limit or (self.emergency_active and (self.t < self.emergency_until or raw_over_target))
        self.emergency_active = emergency

        heat = 0.0
        if power_w is not None:
            idle_power = (model.idle_power_w if model is not None and model.idle_power_w is not None else 0.0)
            smooth_power = self.power.update(float(power_w), dt, POWER_RISE_S, s * POWER_FALL_SHARE)
            heat = max(0.0, smooth_power - idle_power)
        ff = p_term = 0.0
        if settings.mode == "pid":
            ff = self.feedforward(model, settings, sensors, heat)
            e = max(error, -COLD_ERROR_LIMIT_C)
            p_term = settings.kp * e
            if error < -COLD_ERROR_LIMIT_C:
                self.integral -= self.integral * min(1.0, dt / max(1.0, settings.ti))
            else:
                self.integral += settings.kp / max(1.0, settings.ti) * e * dt
            self.integral = clamp(self.integral, -settings.max_percent, settings.max_percent)
            wanted = ff + p_term + self.integral
        else:
            points = settings.map_points or default_map(model, sensors, settings, None)
            wanted = map_percent(points, -error, settings)
        wanted = clamp(wanted, settings.min_percent, settings.max_percent)

        if emergency:
            out = settings.max_percent
        elif self.out is None:
            out = wanted
        else:
            # 上げは速く・下げはゆっくり。往復負荷で回転が上下しない
            span = settings.max_percent - settings.min_percent
            up = span / (s * UP_SHARE) * dt
            down = span / (s * DOWN_SHARE) * dt
            out = min(wanted, self.out + up) if wanted > self.out else max(wanted, self.out - down)
        if settings.mode == "pid":
            # 逆算型のワインドアップ防止: 実際に出した値へ積分を寄せる
            v = ff + p_term + self.integral
            tt = max(dt, 0.5 * settings.ti)
            self.integral = clamp(self.integral + dt / tt * (out - v), -settings.max_percent, settings.max_percent)
        self.out = out
        return Step(out, settings.mode, governing, error, ff, p_term, self.integral, emergency, pv)


# ---- 模擬（熱モデルから温度を計算する） ----

# hotspot は電力が入った瞬間に跳ねる（R9700 実測 2026-11-17: 8→210 W で 3 秒のうちに 36→63 °C、
# そこから約 1 分で 88 °C）。跳ねの分は回転数でほとんど変わらない（ダイから放熱板まで）。
HOTSPOT_JUMP_C_PER_W = 0.13


class Plant:
    """センサーごとの一次遅れ。定常温度 = アイドル温度 + R(回転数) × 発熱。
    hotspot だけは「瞬間の跳ね（回転数によらない）＋ 一次遅れ」。"""

    def __init__(self, model: ThermalModel, jump_c_per_w: float = HOTSPOT_JUMP_C_PER_W,
                 fan_lag_s: float = 4.0) -> None:
        self.model = model
        self.slow = dict(model.idle_c)
        self.temps = dict(model.idle_c)
        self.jump = jump_c_per_w
        self.fan = _Lag()
        self.fan_lag_s = fan_lag_s

    def resistance(self, sid: str, fan: float) -> float:
        pts = [(p["fan"], p["r"][sid]) for p in self.model.points if sid in p["r"]]
        pts.sort()
        if fan <= pts[0][0]:
            (f0, r0), (f1, r1) = pts[0], pts[1]
        elif fan >= pts[-1][0]:
            (f0, r0), (f1, r1) = pts[-2], pts[-1]
        else:
            (f0, r0), (f1, r1) = next((a, b) for a, b in zip(pts, pts[1:]) if a[0] <= fan <= b[0])
        r = r0 + (fan - f0) * (r1 - r0) / max(1e-6, f1 - f0)
        return max(0.2 * min(r0, r1), r)

    def step(self, fan_percent: float, power_w: float, dt: float, kinds: dict[str, str]) -> dict[str, float]:
        fan = self.fan.update(fan_percent, dt, self.fan_lag_s, self.fan_lag_s * 2)
        heat = max(0.0, power_w - (self.model.idle_power_w or 0.0))
        for sid, idle in self.model.idle_c.items():
            r = self.resistance(sid, fan)
            jump = min(self.jump, 0.8 * r) if kinds.get(sid) == "junction" else 0.0
            steady = idle + (r - jump) * heat
            self.slow[sid] += (1.0 - math.exp(-dt / max(1.0, self.model.tau_s))) * (steady - self.slow[sid])
            self.temps[sid] = self.slow[sid] + jump * heat
        return dict(self.temps)


def load_pattern(kind: str, full_w: float, idle_w: float, seconds: float, dt: float = 3.0,
                 period_s: float = 20.0) -> list[float]:
    """'steady': 全力のまま / 'square': 半分の周期ずつ 0%↔100% / 'idle'。"""
    n = int(seconds / dt)
    if kind == "steady":
        return [full_w] * n
    if kind == "idle":
        return [idle_w] * n
    return [full_w if (i * dt) % period_s < period_s / 2 else idle_w for i in range(n)]


def simulate(model: ThermalModel, settings: CoreSettings, sensors: list[dict], powers: list[float],
             dt: float = 3.0, warm_up: list[float] | None = None) -> dict:
    """負荷の列を模擬で流し、回転数・温度の指標を返す（warm_up は記録しない前段）。"""
    plant = Plant(model, model.hotspot_jump_c_per_w or HOTSPOT_JUMP_C_PER_W)
    kinds = {s["id"]: sensor_kind(s) for s in sensors}
    controller = Controller()
    fans: list[float] = []
    peak: dict[str, float] = {}
    emergencies = 0
    for i, power in enumerate((warm_up or []) + powers):
        temps = plant.temps
        step = controller.step([{**s, "value_c": temps[s["id"]]} for s in sensors if s["id"] in temps],
                               power, dt, settings, model)
        plant.step(step.percent, power, dt, kinds)
        if i >= len(warm_up or []):
            fans.append(step.percent)
            emergencies += int(step.emergency)
            for sid, value in plant.temps.items():
                peak[sid] = max(peak.get(sid, -99.0), value)
    tail = fans[len(fans) // 2:] or fans
    return {
        "fan_mean": round(sum(fans) / max(1, len(fans)), 1),
        "fan_swing": round(max(tail) - min(tail), 1) if tail else 0.0,
        "fan_max": round(max(fans), 1) if fans else 0.0,
        "peak_c": {k: round(v, 1) for k, v in peak.items()},
        "over_limit": any(peak.get(sid, 0) >= limit for sid, limit in settings.limits_c.items()),
        "headroom_c": round(min(limit - peak.get(sid, 0) for sid, limit in settings.limits_c.items()), 1),
        "emergency_steps": emergencies,
    }


# 試験の型: (名前, 種類, 周期秒)。0%↔100% の往復は 10 秒・30 秒・60 秒周期の半々
TEST_PATTERNS = (("steady", "steady", 0.0), ("sq20", "square", 20.0), ("sq60", "square", 60.0),
                 ("sq120", "square", 120.0))
SMOOTHING_CANDIDATES = (30.0, 45.0, 60.0, 90.0, 120.0, 180.0)
# 往復負荷での回転の振れの許容（ポイント）。短い往復（10・30 秒ずつ）は抑え、1 分ずつの往復は多少追ってよい
SWING_LIMITS = {"sq20": 10.0, "sq60": 10.0, "sq120": 20.0}


def evaluate(model: ThermalModel, settings: CoreSettings, sensors: list[dict], full_w: float,
             minutes: float = 12.0) -> dict:
    idle_w = model.idle_power_w or 10.0
    out = {}
    warm = load_pattern("steady", full_w, idle_w, 600)  # 全力で温度を落ち着かせてから
    for name, kind, period in TEST_PATTERNS:
        out[name] = simulate(model, settings, sensors,
                             load_pattern(kind, full_w, idle_w, minutes * 60, period_s=period or 20.0),
                             warm_up=warm)
    # 休みから急に全力（急な温度上昇）
    out["step"] = simulate(model, settings, sensors, load_pattern("steady", full_w, idle_w, 600),
                           warm_up=load_pattern("idle", full_w, idle_w, 300))
    return out


def tune(model: ThermalModel, base: CoreSettings, sensors: list[dict], full_w: float) -> dict:
    """熱モデルから PID の効き具合とマップを決め、なめらかさは模擬の試験で選ぶ。

    選び方: 上限を超えず・緊急の最大回転に入らず、往復負荷での回転の振れが SWING_LIMITS 以内のうち、
    平均回転数がいちばん低いもの（最低限の回転）。どれも満たさなければ最も振れの小さいもの。
    """
    result: dict = {}
    if model.gain_c_per_percent:
        kp, ti = simc_gains(model.gain_c_per_percent, model.tau_s, LOOP_DEAD_TIME_S)
    else:
        kp, ti = base.kp, base.ti
    result["kp"], result["ti"] = kp, ti
    candidate = CoreSettings(**{**base.__dict__, "kp": kp, "ti": ti})
    result["map_points"] = default_map(model, sensors, candidate, full_w)
    trials = []
    for smoothing in SMOOTHING_CANDIDATES:
        trial = CoreSettings(**{**candidate.__dict__, "smoothing_s": smoothing,
                                "map_points": result["map_points"]})
        metrics = evaluate(model, trial, sensors, full_w)
        swing = max(metrics[k]["fan_swing"] / limit for k, limit in SWING_LIMITS.items())
        safe = not any(m["over_limit"] or m["emergency_steps"] for m in metrics.values())
        mean = sum(m["fan_mean"] for m in metrics.values()) / len(metrics)
        trials.append({"smoothing_s": smoothing, "swing_ratio": round(swing, 2), "safe": safe,
                       "fan_mean": round(mean, 1), "headroom_c": min(m["headroom_c"] for m in metrics.values()),
                       "swing": {k: metrics[k]["fan_swing"] for k in SWING_LIMITS}})
    good = [t for t in trials if t["safe"] and t["swing_ratio"] <= 1.0]
    best = min(good, key=lambda t: (t["fan_mean"], t["smoothing_s"])) if good else \
        min(trials, key=lambda t: (not t["safe"], t["swing_ratio"]))
    result["smoothing_s"] = best["smoothing_s"]
    result["trials"] = trials
    return result
