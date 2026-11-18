"""ファン制御 v2 の本体（sysfs に触らない純粋な計算）。

方式は PID だけ（2026-11-18 利用者の指示「いい方式のみ採用」。R9700 実機で温度マップと比べ、
全力が続くときの hotspot のばらつきが ±1.05°C → ±0.5°C、VRAM は ±0.96°C → ±0.0°C だった）。

* 目標 = 上限温度 − マージン（R9700 の hotspot は 95 − 5 = 90°C）。目標との差に比例して回転を決め（P）、
  差が残れば回転を上乗せする（I）。電力から熱モデルで必要な回転数を先読み（フィードフォワード）して足す。
* 上乗せ（積分）は「先読みの不足分」= 外気温・季節・埃のずれを覚えている。休み中も捨てず、
  熱モデルのファイルに保存して再起動後も引き継ぐ（外気温 37°C でも目標に戻るため）。
* 温度の上がる側は遅らせずに見る。下がる側と電力の下がる側はゆっくり（往復負荷で回転を落としすぎない）。
* 上限を超えたら最大回転にする。ソフトは直近 3 回（9 秒）の中央値で判定（hotspot の 1 秒の跳ねでは入らない）、ファームウェアのカーブは上限 +1°C で最大。目標まで下がるまで保持。
* 回転数の振れは気にしない（利用者の指示 2026-11-18: 温度の変動を小さくするほうを優先）。

模擬（``Plant``）は学習済みの熱モデルから温度を計算し、テストに使う。
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from statistics import median

from app.monitoring.fan_pid import (
    COLD_ERROR_LIMIT_C, HIGH_EXPONENT_MAX, HOTSPOT_FF_LAG_S, LOW_EXPONENT_MIN, LOOP_DEAD_TIME_S, ThermalModel, clamp,
    simc_gains,
)

# 平滑の内訳（なめらかさ S 秒に対する比）。根拠は R9700 実測（docs/design-amd-smart-fan.md）
PV_FALL_SHARE = 1 / 2       # 温度の下げ側: S=60 で 30 秒（上げ側は遅らせない: PV_RISE_S）
PV_RISE_S = 2.0             # 上げ側を S/12 秒遅らせたら、hotspot が 93〜95°C まで行き過ぎた（2026-11-18）
POWER_RISE_S = 3.0          # 先読みの電力の上げ側は速く（休みから全力で hotspot が 10 秒台で上がるため）
POWER_FALL_SHARE = 3 / 2    # 先読みの電力の下げ側: S=60 で 90 秒（短い休みで回転を落とさない）
UP_SHARE = 1 / 4            # 回転の上げ: 最低→最大を S/4 秒（S=60 で 15 秒）
DOWN_SHARE = 2.0            # 回転の下げ: 最大→最低を 2S 秒（S=60 で 2 分）
DEFAULT_SMOOTHING_S = 60.0
EMERGENCY_HOLD_S = 15.0
INTEGRAL_RANGE = (-20.0, 50.0)   # 上乗せの範囲（%）。外気温 37°C で先読みより +15% ほど要る見込み
# 目標より下にいるときの比例の効き（上にいるときに対する比）。負荷が止まると hotspot は一瞬で 25°C ほど下がり、
# 等倍だと回転を一気に落とす。再開すると遅れて上がり、行き過ぎる（R9700 本番 2026-11-18: 52%→39% に落ち、
# 再開で 95°C・緊急の最大回転）。休み中の回転は先読み（電力の下げ側はゆっくり）で保つ
COLD_P_SHARE = 0.6   # 実機の記録に当てはめた模型で選んだ（docs/design-amd-smart-fan.md「2 段の模型」）
# 生の電力がアイドル電力＋これより小さければ「休み」（上乗せを動かさない）。アイドル電力をまだ測っていなければ
# IDLE_FALLBACK_W を使う（マルチモニターなどでアイドルが 30 W を超える GPU もある）
IDLE_HEAT_W = 30.0
IDLE_FALLBACK_W = 30.0
# 目標より冷えすぎで、回転を下げている途中でもないときに上乗せを減らす速さ（通常の比）
COLD_DECAY_SHARE = 0.1
# 負荷がかかっている間の回転の床 = 先読み ＋ マイナスの上乗せ（＋ 跳ね上がりの上乗せ）。LLM の負荷は軽い局面（生成）
# と重い局面（読み込み）を行き来する。軽い局面で目標より冷えると、比例の項が回転を先読みより大きく下げ、重い局面が
# 来ると追いつかずに上限へ届いた（R9700 本番 2026-11-18 14:26: 負荷中に 52→35%、10 秒後に緊急）。マイナスの上乗せ
# （先読みの多すぎる分を覚えたもの）は床にも効かせる（効かせないと先読みの多すぎを直せない。レビュー指摘）。
# 床まではすぐ上げる（電力の急変への先読み）
# 床に効かせるマイナスの上乗せの上限（%）
FLOOR_NEGATIVE_I_MAX = 5.0
# （電力の跳ね上がりに一定の % を足す案は採らなかった: 下の「重い局面への余白」があれば緊急は 0 回のまま、足すと
#  一定の全力で目標より冷やしすぎた。模擬 2026-11-18）
# 上限の判定に使う直近の読みの中央値の幅（秒）。周期 3 秒で 3 回分だった
EMERGENCY_WINDOW_S = 9.0
# 重い局面への余白: hotspot は電力が上がると一瞬で（3 秒以内）「ダイの係数 × 電力の差」だけ跳ね、回転では防げない
# （模型 2026-11-18: 154→223 W で 85.8→94.8°C）。直近 PEAK_WINDOW_S 秒の最大電力に戻ったときの跳ねを見積もり、
# その分 hotspot が熱いとみなして制御する（軽い局面のうちに放熱板を冷やしておく）。利用者の案（2026-11-18
# 「ピーク温度に対する現状温度の差で制御できないか」）
PEAK_WINDOW_S = 180.0


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
    limits_c: dict[str, float]        # センサーごとの上限温度
    margin_c: float = 5.0
    min_percent: float = 20.0
    max_percent: float = 100.0
    smoothing_s: float = DEFAULT_SMOOTHING_S
    kp: float = 2.5
    ti: float = 80.0

    def targets(self) -> dict[str, float]:
        return {sid: limit - self.margin_c for sid, limit in self.limits_c.items()}


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
    floor: float = 0.0       # 負荷中の床（先読み＋マイナスの上乗せ）。休み中は 0
    reserve_c: float = 0.0   # 重い局面への余白（hotspot を熱いとみなした分、°C）

    def as_dict(self) -> dict:
        return {"output_percent": round(self.percent, 1), "mode": self.mode, "governing_sensor": self.governing,
                "error_c": round(self.error_c, 2), "feedforward_percent": round(self.feedforward, 1),
                "p_percent": round(self.p_term, 1), "i_percent": round(self.i_term, 1),
                "emergency": self.emergency, "pv_c": {k: round(v, 1) for k, v in self.pv_c.items()},
                "floor_percent": round(self.floor, 1), "reserve_c": round(self.reserve_c, 1)}


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
        self.integral_loaded = False
        self.peaks: deque = deque()
        self.reserve_c = 0.0

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
            window = self.raw.setdefault(sid, deque(maxlen=max(3, round(EMERGENCY_WINDOW_S / max(dt, 0.1)))))
            window.append(value)
            recent = median(window)   # 上限・目標・平滑の 3 つとも同じ値で判断する
            # 上限の判定は直近 3 回（9 秒）の中央値。hotspot は全力が続いていても 1 秒だけ +3〜+7°C 跳ねる
            # （R9700 本番 2026-11-18 の 1 秒ごとの記録: 210 W・回転一定で 87→98→91）。1 回の読みで判定すると、
            # 平均が目標でも跳ねに当たって最大回転に入る。2 回続けて超えれば入る（3 秒ごとなので 6 秒以内。
            # 危険温度 110°C まで余裕がある。ファームウェアのカーブも上限 +1°C で最大にしてある）
            if recent >= settings.limits_c[sid]:
                over_limit = True
            if recent > targets[sid]:
                raw_over_target = True
            filtered = self.pv.setdefault(sid, _Lag()).update(recent, dt, PV_RISE_S, s * PV_FALL_SHARE)
            pv[sid] = filtered
            errors[sid] = filtered - targets[sid]
        # 重い局面への余白（hotspot だけ。負荷中だけ）
        jump_c_per_w = (model.hotspot_jump_c_per_w if model is not None and model.hotspot_jump_c_per_w else HOTSPOT_JUMP_C_PER_W)
        if power_w is not None:
            self.peaks.append((self.t, float(power_w)))
            while self.peaks and self.peaks[0][0] < self.t - PEAK_WINDOW_S:
                self.peaks.popleft()
        reserve = 0.0
        if power_w is not None and self.peaks:
            idle_guess = model.idle_power_w if model is not None and model.idle_power_w is not None else IDLE_FALLBACK_W
            if float(power_w) - idle_guess >= IDLE_HEAT_W:
                peak_w = max(w for _, w in self.peaks)
                reserve = max(0.0, jump_c_per_w * (peak_w - float(power_w)))
                for sensor in sensors:
                    if sensor_kind(sensor) == "junction" and sensor["id"] in errors:
                        errors[sensor["id"]] += reserve
        self.reserve_c = reserve
        governing = max(errors, key=errors.get) if errors else ""
        error = errors.get(governing, -99.0)
        if over_limit:
            self.emergency_until = self.t + EMERGENCY_HOLD_S
        # 上限を超えたら最大。生の温度が目標まで戻る（かつ最低保持時間）まで続ける。
        # 平滑した温度で判定すると、跳ねた直後は目標を超えて見えず、すぐ解除してしまう
        emergency = over_limit or (self.emergency_active and (self.t < self.emergency_until or raw_over_target))
        self.emergency_active = emergency

        heat = 0.0
        known_idle = model is not None and model.idle_power_w is not None
        idle_power = model.idle_power_w if known_idle else IDLE_FALLBACK_W
        if power_w is not None:
            smooth_power = self.power.update(float(power_w), dt, POWER_RISE_S, s * POWER_FALL_SHARE)
            heat = max(0.0, smooth_power - (idle_power if known_idle else 0.0))
        if not self.integral_loaded:
            # 上乗せは熱モデルに保存してある（再起動・設定の変更をまたいで引き継ぐ）
            self.integral = clamp(model.integral_percent, *INTEGRAL_RANGE) if model is not None else 0.0
            self.integral_loaded = True
        ff = self.feedforward(model, settings, sensors, heat)
        e = max(error, -COLD_ERROR_LIMIT_C)
        p_term = settings.kp * (e if e > 0 else e * COLD_P_SHARE)
        # 上乗せ（先読みの不足分）の動かし方:
        #  ・GPU が休んでいる（生の電力が低い）・緊急の最大回転の間は動かさない。休むたびに捨てると次の全力のたびに
        #    同じだけ行き過ぎる（R9700 実測 2026-11-18: 先読み 46% に対し必要 51%、捨てる版は 93〜95°C）
        #  ・目標より COLD_ERROR_LIMIT_C 以上冷えているときは、ほぼ動かさない。温まる途中（休みから全力）でも、
        #    回転をゆっくり下げている途中（自分の高い回転で冷えた）でも冷えて見えるので、そこで減らすと次に行き過ぎる
        #    （模擬 2026-11-18: −9〜−20% まで下がって 95°C 超えを繰り返した）。下げている途中でなければ
        #    COLD_DECAY_SHARE の速さでだけ減らし、季節が変わって要らなくなった上乗せを時間をかけて戻す
        # 電力が読めないときは休みとみなさない（上乗せが止まったままになる）
        idle = power_w is not None and float(power_w) - idle_power < IDLE_HEAT_W
        frozen = idle or emergency
        rate = settings.kp / max(1.0, settings.ti) * e * dt
        if not frozen and error < -COLD_ERROR_LIMIT_C:
            rate *= COLD_DECAY_SHARE
        if not frozen and rate < 0 and self.integral <= 0 and ff + p_term + self.integral <= settings.min_percent:
            rate = 0.0   # 最低回転で止まっているのに、上乗せをマイナスへ膨らませない（次の負荷で遅れる）
        if not frozen and rate < 0 and self.out is not None \
                and self.out > max(ff + p_term + self.integral, settings.min_percent) + 0.5:
            # 冷えているのが自分の回転のせいなら減らさない: 回転をゆっくり下げている途中（緊急の最大回転の後など）。
            # 減らすと上乗せが −20% まで張り付き、「冷える → 減らす → 上限 → 最大回転」を繰り返した（模擬 2026-11-18）
            rate = 0.0
        if not frozen:
            self.integral = clamp(self.integral + rate, *INTEGRAL_RANGE)
        wanted = clamp(ff + p_term + self.integral, settings.min_percent, settings.max_percent)
        loaded = power_w is not None and not idle
        floor = clamp(ff + max(min(self.integral, 0.0), -FLOOR_NEGATIVE_I_MAX), settings.min_percent, settings.max_percent) if loaded else 0.0
        wanted = max(wanted, floor)

        if emergency:
            out = settings.max_percent
        elif self.out is None:
            out = wanted
        else:
            # 上げは速く・下げはゆっくり
            span = settings.max_percent - settings.min_percent
            up = span / (s * UP_SHARE) * dt
            down = span / (s * DOWN_SHARE) * dt
            out = min(wanted, self.out + up) if wanted > self.out else max(wanted, self.out - down)
            out = max(out, floor)   # 床まではすぐ上げる（休み中の床は 0）
        v = ff + p_term + self.integral
        if not frozen and out < v:
            # 逆算型のワインドアップ防止: 上げが追いつかない・最大で頭打ちのときだけ、実際に出した値へ寄せる。
            # 下げをゆっくりにしている間（out > v）に寄せると、上乗せが膨らむ（本番 2026-11-18: 緊急の最大回転の
            # 後に 3 → 17.6%）
            tt = max(dt, 0.5 * settings.ti)
            self.integral = clamp(self.integral + dt / tt * (out - v), *INTEGRAL_RANGE)
        if model is not None:
            model.integral_percent = self.integral
        self.out = out
        return Step(out, "pid", governing, error, ff, p_term, self.integral, emergency, pv, floor, self.reserve_c)


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
        if fan < pts[0][0] or fan > pts[-1][0]:
            # 範囲の外はべき乗（fan_for と同じ延ばし方。直線では低い回転の放熱の悪さを小さく見る）
            r_edge, f_edge = (r0, f0) if fan < pts[0][0] else (r1, f1)
            b = math.log(max(r0, 1e-9) / max(r1, 1e-9)) / math.log(max(f1, 1e-6) / max(f0, 1e-6)) if f1 > f0 and r1 < r0 else 0.0
            b = max(b, LOW_EXPONENT_MIN) if fan < pts[0][0] else min(b, HIGH_EXPONENT_MAX)
            return r_edge * (max(fan, 5.0) / f_edge) ** (-b) if b > 0 else r_edge
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


def pid_gains(model: ThermalModel) -> tuple[float, float]:
    """熱モデルから PID の効き具合（Kp %/°C, Ti 秒）。SIMC で閉ループの時定数を遅れ時間と同じにする（強め）。

    閉ループの時定数を熱の時定数の半分にした旧版（R9700 で Kp 1.33）は弱く、往復負荷で hotspot が
    93〜95°C まで行き過ぎた。遅れ時間と同じ（Kp 2.5）で hotspot は全力中 90.0±0.5°C（実測 2026-11-18）。
    Ti は熱の時定数の 1.6 倍（R9700 で 80 秒）。実機の 1 秒ごとの記録に当てはめた 2 段の模型で、マージン 5°C・
    外気温 +10°C まで、実際の電力の流れ・休みを挟む負荷・休みから全力のどれでも緊急の最大回転 0 回だった組み合わせ
    （Ti 50 秒では 5〜10 回。模擬 2026-11-18）。
    """
    if not model.gain_c_per_percent:
        return CoreSettings.kp, CoreSettings.ti
    kp, _ = simc_gains(model.gain_c_per_percent, model.tau_s, LOOP_DEAD_TIME_S, lam=LOOP_DEAD_TIME_S)
    return kp, round(min(600.0, max(15.0, model.tau_s * 1.6)), 1)


def optimize_gains(plant, model: ThermalModel, base: CoreSettings) -> dict:
    """実機に当てはめた 2 段の模型（fan_plant）で PID の効き具合を選ぶ。熱モデルからの値（pid_gains）を中心に
    Kp・Ti を 0.8〜1.25 倍で試し、LLM らしい負荷・休みから全力・外気温 +10°C で緊急の最大回転が 0 回のものの
    うち、負荷中の温度の揺れが一番小さいもの。どれも 0 回にならなければ回数が一番少ないもの。"""
    from app.monitoring import fan_plant

    kp0, ti0 = pid_gains(model)
    loads = [(fan_plant.llm_load(1), 4.0, 3.0), (fan_plant.llm_load(2), 4.0, 3.0),
             ([plant.idle_w + 8] * 300 + [210.0] * 600, 10.0, 0.0)]
    tried = []
    for kf in (0.8, 1.0, 1.25):
        for tf in (0.8, 1.0, 1.25):
            trial = CoreSettings(**{**base.__dict__, "kp": round(kp0 * kf, 2), "ti": round(ti0 * tf, 1)})
            results = [simulate_plant(plant, model, trial, power, dt=3.0, ambient_add=amb, ambient_ramp=ramp, seed=i + 1)
                       for i, (power, amb, ramp) in enumerate(loads)]
            tried.append({"kp": trial.kp, "ti": trial.ti, "emergencies": sum(r["emergencies"] for r in results),
                          "spread_c": round(max(r["spread_c"] for r in results), 2),
                          "peak_c": max(r["peak_c"] for r in results),
                          "centre": kf == 1.0 and tf == 1.0})
    # 揺れの差が 0.25°C 未満なら差が無いとみなし、熱モデルからの値（中心）を選ぶ（模擬の揺らぎで選ばない）
    best = min(tried, key=lambda t: (t["emergencies"], round(t["spread_c"] * 4) / 4, not t["centre"]))
    return {"kp": best["kp"], "ti": best["ti"], "tried": tried, "plant": plant.summary()}


def tune(model: ThermalModel, base: CoreSettings, sensors: list[dict], full_w: float, plant=None) -> dict:
    """熱モデルから PID の効き具合を決め、模擬の試験の結果を添える。実機に当てはめた 2 段の模型（plant）があれば、
    それで候補を試して選ぶ（2026-11-18 利用者「熱モデルをプロジェクトに書き込んでファン制御の最適化に使えるように」）。
    無ければ熱モデルからの値のまま（簡単な模型の hotspot は実機の約 1 分かけた上昇を再現しないため、選択には使わない）。"""
    kp, ti = pid_gains(model)
    if plant is not None:
        chosen = optimize_gains(plant, model, base)
        kp, ti = chosen["kp"], chosen["ti"]
    trial = CoreSettings(**{**base.__dict__, "kp": kp, "ti": ti})
    metrics = evaluate(model, trial, sensors, full_w)
    out = {"kp": kp, "ti": ti, "smoothing_s": base.smoothing_s,
           "simulated": {name: {"peak_c": m["peak_c"], "fan_mean": m["fan_mean"],
                                "emergency_steps": m["emergency_steps"]} for name, m in metrics.items()}}
    if plant is not None:
        out["optimized"] = chosen
    return out


# ---- 実機に当てはめた 2 段の模型で制御を模擬する（自動調整の最適化・テスト） ----

def simulate_plant(plant, model: ThermalModel, settings: CoreSettings, power: list[float], *,
                   dt: float = 1.0, ambient_add: float = 0.0, ambient_ramp: float = 0.0, seed: int = 3,
                   warm_up: bool = True, sensor_ids: dict[str, str] | None = None) -> dict:
    """plant は fan_plant.PlantModel。power は 1 秒刻み。制御は dt 秒ごと（本番の周期）。hotspot の読みには
    実機の記録の 1 秒の跳ね（+3〜+7°C）を足す。返り値: 緊急の最大回転の回数（制御の回数）・最高・負荷中の平均と揺れ。"""
    import random

    from app.monitoring.fan_plant import TwoStage

    ids = sensor_ids or plant.data.get("sensor_ids") or {"junction": "temp2", "mem": "temp3", "edge": "temp1"}
    sensors = [{"id": ids[kind], "kind": kind} for kind in ("junction", "mem", "edge")]
    rnd = random.Random(seed)
    noise = plant.noise
    twin = TwoStage(plant, ambient_add)
    ctl = Controller()
    out = settings.min_percent
    every = max(1, round(dt))

    def read(pw: float) -> list[dict]:
        values = {kind: twin.T[kind] + (rnd.choice(noise) if kind == "junction" and pw > 120 else rnd.gauss(0, 0.3))
                  for kind in ("junction", "mem", "edge")}
        return [{**s, "value_c": round(values[s["kind"]])} for s in sensors]

    if warm_up:   # 全力で温めてから休む（上乗せが育った本番に近い状態）
        for i, pw in enumerate([210.0] * 900 + [plant.idle_w + 8] * 120):
            twin.step(out, pw)
            if i % every == 0:
                out = ctl.step(read(pw), pw, float(every), settings, model).percent
    emergencies, peak, loaded = 0, 0.0, []
    n = max(1, len(power))
    for i, pw in enumerate(power):
        twin.add = ambient_add + ambient_ramp * i / n
        twin.step(out, pw)
        if i % every:
            continue
        step = ctl.step(read(pw), pw, float(every), settings, model)
        out = step.percent
        emergencies += int(step.emergency)
        peak = max(peak, twin.T["junction"])
        if pw > 120 and i > 300:
            loaded.append(twin.T["junction"])
    mean = sum(loaded) / len(loaded) if loaded else 0.0
    spread = (sum((x - mean) ** 2 for x in loaded) / len(loaded)) ** 0.5 if loaded else 0.0
    return {"emergencies": emergencies, "peak_c": round(peak, 1), "mean_c": round(mean, 1), "spread_c": round(spread, 2)}
