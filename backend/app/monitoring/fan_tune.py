"""ファン制御の自動調整（2 方式の両方の値を決める）。

GPU に自分で全力の負荷（電力上限いっぱい）をかけて、次の順に進む（2026-11-17 利用者の指示）。

1. ``idle``: 負荷なしで温度が落ち着くのを待ち、アイドルの温度と電力を測る
2. ``jump``: 全力にした直後の hotspot の跳ね（°C/W）を測る
3. ``stairs``: 全力のまま回転数を 70% から 10% ずつ下げ、各段で温度が落ち着いた点を熱モデルにする。
   目標（上限 − マージン）の手前で止める。ここで PID の効き具合（SIMC）が決まる
4. ``simulate``: 熱モデルの模擬で、なめらかさと温度マップを選ぶ（fan_core.tune）
5. ``verify``: 決めた値で実際に 0%↔100% の往復（10 秒ずつ・30 秒ずつ）と全力をかけ、上限に近づかないか、
   緊急の最大回転に入らないかを確かめる。近づいたらマージンを 1°C 広げてやり直す（2 回まで）

負荷は実際に使う LLM に、毎回同じ文言で長文を書かせる（行列計算だけの負荷とは温度の分布が違う）。
0% にするときは生成の接続を切る。
自動調整中も、上限を超えたときの最大回転は通常どおり効く。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from app.monitoring import fan_core, fan_pid

logger = logging.getLogger("control_deck.fan_tune")

IDLE_HOLD_PERCENT = 40
IDLE_MAX_SECONDS = 300.0
JUMP_WAIT_SECONDS = 6.0
STAIRS_START_PERCENT = 70
VERIFY_PATTERNS = (("sq20", 10.0, 10.0, 120.0), ("sq60", 30.0, 30.0, 180.0), ("steady", 0.0, 0.0, 120.0))
VERIFY_HEADROOM_C = 1.0       # 上限まで最低これだけ残す
MAX_MARGIN_RAISES = 2
# 階段（最大 6 段 × 5 分）と確認（2 方式 × 7 分）に足りるよう、十分に取る
TOTAL_MAX_SECONDS = 120 * 60.0


@dataclass
class _Tune:
    phase: str = "off"            # off / idle / jump / stairs / verify / done / aborted
    message: str = ""
    started_at: float = 0.0
    phase_since: float = 0.0
    hold: float | None = None
    stairs: fan_pid.AutoTune = field(default_factory=fan_pid.AutoTune)
    jump_before: float | None = None
    result: dict | None = None
    verify_mode: str = ""
    verify_index: int = 0
    verify_since: float = 0.0
    verify_log: list = field(default_factory=list)
    verify_peak: dict = field(default_factory=dict)
    verify_emergency: bool = False
    margin_raises: int = 0
    verify_fan: list = field(default_factory=list)
    # 始める前の設定。確認のために仮に保存した値を、中止したら元へ戻す
    backup: dict | None = None


_tune = _Tune()
_load_proc: subprocess.Popen | None = None
_load_paused = False


# ---- 負荷役（LLM に長文を書かせる） ----
# 行列計算だけの負荷は、LLM と温度の分布が違う（R9700 実測 2026-11-17: bf16 の行列積 210 W で hotspot 88・
# VRAM 62°C、LLM の連続生成では VRAM がもっと熱くなる）。実際に使う LLM で、毎回同じ文言で書かせる。
TUNE_PROMPT = (
    "日本の四季の移ろいと、各地方の年中行事・食文化・気候の違いについて、北海道から沖縄まで順に、"
    "それぞれ具体的な例を挙げながら、できるだけ長く詳しく書き続けてください。要約や締めくくりは不要です。"
)
TUNE_MAX_TOKENS = 4096


class _LLMLoad:
    """別スレッドで生成を流し続ける。pause 中は接続を切って生成を止める。"""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url, self.model = base_url.rstrip("/"), model
        self.paused = False
        self.stopped = False
        self.failures = 0
        self.tokens = 0
        self.thread = threading.Thread(target=self._run, name="fan-tune-load", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        import httpx

        body = {"model": self.model, "messages": [{"role": "user", "content": TUNE_PROMPT}],
                "max_tokens": TUNE_MAX_TOKENS, "temperature": 0.8, "stream": True}
        while not self.stopped:
            if self.paused:
                time.sleep(0.2)
                continue
            try:
                with httpx.stream("POST", f"{self.base_url}/chat/completions", json=body,
                                  timeout=httpx.Timeout(30.0, read=120.0)) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if self.stopped or self.paused:
                            break        # 接続を切ると生成が止まる
                        if line.startswith("data:"):
                            self.tokens += 1
                self.failures = 0
            except Exception:  # noqa: BLE001 - 連続で失敗したら調整を止める（alive で見る）
                self.failures += 1
                time.sleep(2.0)

    def alive(self) -> bool:
        return self.thread.is_alive() and self.failures < 3


_load: _LLMLoad | None = None
_llm: dict | None = None


def choose_llm(alias: str | None) -> dict:
    """自動調整に使う LLM。指定が無ければ登録済みの最初の LLM。"""
    from app.models_mgmt import local_llm

    items = local_llm.llm_instances()
    if alias:
        items = [i for i in items if i.get("alias") == alias]
    if not items:
        raise RuntimeError("自動調整に使う LLM が登録されていません")
    item = items[0]
    return {"alias": str(item["alias"]), "base_url": str(item["base_url"])}


def start_load() -> None:
    global _load
    if _load is not None and _load.alive():
        _load.paused = False
        return
    if _llm is None:
        raise RuntimeError("自動調整に使う LLM が決まっていません")
    _load = _LLMLoad(_llm["base_url"], _llm["alias"])


def pause_load() -> None:
    if _load is not None:
        _load.paused = True


def resume_load() -> None:
    if _load is not None:
        _load.paused = False


def stop_load() -> None:
    global _load
    if _load is not None:
        _load.stopped = True
    _load = None


def _load_alive() -> bool:
    return _load is not None and _load.alive()


# ---- 操作 ----

def start(llm: dict) -> dict:
    """llm: choose_llm の結果。LLM は呼び出し側（API）が起動を確かめてから渡す。"""
    global _llm
    from app.monitoring import fan_control

    with fan_control._lock:
        settings = fan_control._load()
        if not settings.enabled or fan_control._state != "controlling":
            raise fan_control.FanControlError("ファン制御を有効にしてから自動調整を始めてください")
        if _tune.phase in ("idle", "jump", "stairs", "verify"):
            raise fan_control.FanControlError("自動調整は実行中です")
        _llm = llm
        now = time.monotonic()
        _tune.__init__()
        _tune.backup = settings.model_dump()
        _tune.started_at = _tune.phase_since = now
        _tune.phase, _tune.hold = "idle", float(IDLE_HOLD_PERCENT)
        _tune.message = f"アイドルの温度を測っています（{llm['alias']} で調整します。GPU を使わないでください。最大 5 分）"
        logger.info("ファン自動調整を開始")
        return snapshot()


def cancel(message: str = "中止しました") -> dict:
    stop_load()
    if _tune.phase == "verify" and _tune.backup is not None:
        from app.monitoring import fan_control

        try:
            current = fan_control._load()
            fan_control._persist(fan_control.FanSettings.model_validate(
                {**_tune.backup, "enabled": current.enabled}))
        except Exception:  # noqa: BLE001
            logger.exception("自動調整の前の設定へ戻せません")
    if _tune.phase not in ("off", "done", "aborted"):
        _tune.phase, _tune.message, _tune.hold = "aborted", message, None
        logger.info("ファン自動調整: %s", message)
    return snapshot()


def snapshot() -> dict:
    running = _tune.phase not in ("off", "done", "aborted")
    return {"phase": _tune.phase, "message": _tune.message, "running": running,
            "elapsed_s": round(time.monotonic() - _tune.started_at) if running else None,
            "hold_percent": _tune.hold if running else None, "result": _tune.result,
            "llm": (_llm or {}).get("alias") if running else None,
            "tokens": _load.tokens if running and _load is not None else None}


def _set(phase: str, message: str, now: float, hold: float | None) -> None:
    _tune.phase, _tune.message, _tune.phase_since, _tune.hold = phase, message, now, hold
    logger.info("ファン自動調整: %s %s", phase, message)


def hold_percent(now: float, device: dict, sensors: list[dict], core: fan_core.CoreSettings,
                 model: fan_pid.ThermalModel, tracker: fan_pid.SteadyTracker,
                 step: fan_core.Step) -> float | None:
    """毎回の制御から呼ぶ。自動調整中に保持する回転数（None なら通常の制御）。"""
    if _tune.phase in ("off", "done", "aborted"):
        return None
    try:
        return _advance(now, device, sensors, core, model, tracker, step)
    except Exception as exc:  # noqa: BLE001 - 調整の失敗で制御を止めない
        logger.exception("ファン自動調整が失敗")
        cancel(f"失敗したため中止しました: {exc}")
        return None


def _advance(now, device, sensors, core, model, tracker, step) -> float | None:
    from app.monitoring import fan_control

    if now - _tune.started_at > TOTAL_MAX_SECONDS:
        cancel("時間切れのため中止しました（それまでの値は変えていません）")
        return None
    if step.emergency and _tune.phase in ("idle", "jump", "stairs"):
        cancel("上限温度を超えたため中止しました。マージンを広げてからやり直してください")
        return None
    power = device["power_w"]
    targets = core.targets()
    hot = next((s for s in sensors if s["kind"] == "junction"), None)
    if _tune.phase == "idle":
        stop_load()
        recent = [v for t, v in tracker.power if now - t <= 90]
        quiet = len(recent) >= 20 and max(recent) <= min(60.0, 0.15 * (device["power_cap_w"] or 300))
        if (quiet and tracker.stable(now, 90.0)) or now - _tune.phase_since >= IDLE_MAX_SECONDS:
            temps = tracker.temp_means(now, 30.0)
            if temps and recent:
                model.idle_c = {k: round(v, 2) for k, v in temps.items()}
                model.idle_power_w = round(sum(recent) / len(recent), 1)
            _tune.jump_before = float(hot["value_c"]) if hot else None
            start_load()
            _set("jump", "LLM に書かせ始め、直後の温度の跳ねを測っています", now, float(STAIRS_START_PERCENT))
        return _tune.hold
    if not _load_alive():
        cancel("LLM の生成が続けられなくなったため中止しました（LLM が止まった・応答しない）")
        return None
    if _tune.phase == "jump":
        if now - _tune.phase_since >= JUMP_WAIT_SECONDS and power:
            heat = power - (model.idle_power_w or 0.0)
            if hot is not None and _tune.jump_before is not None and heat > 30:
                model.hotspot_jump_c_per_w = round(max(0.0, float(hot["value_c"]) - _tune.jump_before) / heat, 4)
            _tune.stairs = fan_pid.AutoTune()
            _tune.stairs.start(now, model)
            _set("stairs", f"全力のまま {STAIRS_START_PERCENT}% から回転数を下げて熱の特性を測っています",
                 now, float(STAIRS_START_PERCENT))
        return _tune.hold
    if _tune.phase == "stairs":
        held = fan_pid.tune_step(_tune.stairs, model, tracker, now=now, sensors=sensors, targets=targets,
                                 power_w=power, power_cap_w=device["power_cap_w"],
                                 minimum=core.min_percent, maximum=core.max_percent)
        if _tune.stairs.phase == "aborted":
            cancel("熱の特性を測れませんでした: " + _tune.stairs.message)
            return None
        if _tune.stairs.phase != "done":
            _tune.message = f"熱の特性を測っています: {_tune.stairs.message}"
            _tune.hold = held if held is not None else _tune.hold
            return _tune.hold
        fan_control.save_model(device["bdf"], model)
        found = fan_core.tune(model, core, sensors, device["power_cap_w"] or power or 200.0)
        _tune.result = {"pid_kp": found["kp"], "pid_ti": found["ti"], "smoothing_s": found["smoothing_s"],
                        "map_points": [{"below_c": b, "percent": p} for b, p in found["map_points"]],
                        "margin_c": core.margin_c, "simulated": found["trials"]}
        _start_verify(now, "pid")
        return None
    if _tune.phase == "verify":
        return _verify(now, sensors, core, step, device)
    return None


def _start_verify(now: float, mode: str) -> None:
    _tune.verify_mode, _tune.verify_index, _tune.verify_since = mode, 0, now
    _tune.verify_peak, _tune.verify_emergency, _tune.verify_fan = {}, False, []
    label = "PID" if mode == "pid" else "温度マップ"
    _set("verify", f"{label}で 0%↔100% の往復を試しています（10 秒ずつ）", now, None)
    # 試すのは決めた値。tick は保存された設定で動くので、ここで仮に保存する
    _apply_result(mode, final=False)


def _apply_result(mode: str, *, final: bool) -> None:
    from app.monitoring import fan_control

    result = _tune.result or {}
    settings = fan_control._load()
    update = {"pid_kp": result["pid_kp"], "pid_ti": result["pid_ti"], "smoothing_s": result["smoothing_s"],
              "map_points": [fan_control.MapPoint(**p) for p in result["map_points"]],
              "margin_c": result["margin_c"]}
    if not final:
        update["mode"] = mode
    fan_control._persist(settings.model_copy(update=update))


def _verify(now, sensors, core, step, device) -> float | None:
    name, on_s, off_s, length = VERIFY_PATTERNS[_tune.verify_index]
    elapsed = now - _tune.verify_since
    if on_s and (elapsed % (on_s + off_s)) >= on_s:
        pause_load()
    else:
        resume_load()
    for sensor in sensors:
        _tune.verify_peak[sensor["id"]] = max(_tune.verify_peak.get(sensor["id"], -99.0), float(sensor["value_c"]))
    _tune.verify_emergency = _tune.verify_emergency or step.emergency
    if elapsed >= length / 2:
        _tune.verify_fan.append(step.percent)
    if elapsed < length:
        return None
    swing = (max(_tune.verify_fan) - min(_tune.verify_fan)) if _tune.verify_fan else 0.0
    headroom = min(core.limits_c[sid] - peak for sid, peak in _tune.verify_peak.items() if sid in core.limits_c)
    _tune.verify_log.append({"mode": _tune.verify_mode, "pattern": name, "headroom_c": round(headroom, 1),
                             "fan_swing": round(swing, 1), "emergency": _tune.verify_emergency,
                             "margin_c": core.margin_c})
    if _tune.verify_emergency or headroom < VERIFY_HEADROOM_C:
        if _tune.margin_raises >= MAX_MARGIN_RAISES:
            cancel("マージンを広げても上限に近づくため中止しました。上限温度とマージンを見直してください")
            return None
        _tune.margin_raises += 1
        _tune.result["margin_c"] = round(core.margin_c + 1.0, 1)
        _start_verify(now, _tune.verify_mode)
        _tune.message += f"（上限に近づいたため、マージンを {_tune.result['margin_c']}°C にしてやり直し）"
        return None
    _tune.verify_index += 1
    _tune.verify_since, _tune.verify_peak, _tune.verify_emergency, _tune.verify_fan = now, {}, False, []
    if _tune.verify_index < len(VERIFY_PATTERNS):
        label = "PID" if _tune.verify_mode == "pid" else "温度マップ"
        nxt = VERIFY_PATTERNS[_tune.verify_index]
        _tune.message = (f"{label}で 0%↔100% の往復を試しています（{nxt[1]:.0f} 秒ずつ）" if nxt[1]
                         else f"{label}で全力を続けています")
        return None
    if _tune.verify_mode == "pid":
        _start_verify(now, "map")
        return None
    _finish(now)
    return None


def _finish(now: float) -> None:
    from app.monitoring import fan_control

    stop_load()
    _tune.result["verified"] = _tune.verify_log
    original_mode = (_tune.backup or {}).get("mode", "pid")
    settings = fan_control._load()
    fan_control._persist(settings.model_copy(update={"mode": original_mode}))
    _apply_result(original_mode, final=True)
    _set("done", "調整が完了しました。PID の効き具合・なめらかさ・温度マップを保存しました", now, None)
    _tune.phase = "done"

