"""ファン制御の熱の模型（2 段）を、本番の 1 秒ごとの記録から当てはめ直す。

使い方（既定は読むだけ）:
    env CONTROL_DECK_CONFIG=<本番の config> python -m app.maintenance.fan_plant_fit [--hours 2] [--bdf 0000:03:00.0]
    ... --write            検証区間で良くなったときだけ data/amd-fan-plant.json へ保存（本番の checkout は書き換えない）
    ... --export out.json  同じ条件で JSON を書き出す。開発側で plant_models/ へ入れてテストし、PR にする

切れ目の無い最長の区間を使い、前 70% で当てはめて後ろ 30% で比べる（同じデータで比べると必ず良く見えるため）。
休み（アイドル近く）と負荷の両方が入っていない記録は使わない。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from app.monitoring import fan_log, fan_plant

MAX_GAP_S = 3.0


def longest_run(samples: list[dict]) -> list[dict]:
    best: list[dict] = []
    current: list[dict] = []
    for sample in samples:
        if current and sample["t"] - current[-1]["t"] > MAX_GAP_S:
            current = []
        current.append(sample)
        if len(current) > len(best):
            best = current
    return best


def _errors(plant: fan_plant.PlantModel, rows: list[dict]) -> dict[str, float]:
    fan, power = [r["fan"] for r in rows], [r["power"] for r in rows]
    return {k: round(fan_plant._rmse(plant.sensors[k], fan, power, [r["temps"][k] for r in rows], plant.idle_w), 2)
            for k in fan_plant.KINDS}


def evaluate(samples: list[dict], base: fan_plant.PlantModel) -> dict:
    run = longest_run(samples)
    if len(run) < 1200:
        raise ValueError("切れ目の無い 20 分以上の記録が要ります")
    idle = sum(1 for r in run if r["power"] < base.idle_w + 30)
    load = sum(1 for r in run if r["power"] >= 120)
    if idle < 120 or load < 300:
        raise ValueError("休み（2 分以上）と負荷（5 分以上）の両方が入った記録が要ります")
    cut = int(len(run) * 0.7)
    train, check = run[:cut], run[cut:]
    fitted = fan_plant.fit(train, base, {k: k for k in fan_plant.KINDS})
    before, after = _errors(base, check), _errors(fitted, check)
    return {"fitted": fitted, "samples": len(run), "before_rmse": before, "after_rmse": after,
            "improved": all(after[k] <= before[k] for k in fan_plant.KINDS)
            and sum(after.values()) < sum(before.values())}


def run(hours: float, bdf: str | None, write: bool, export: str | None) -> dict:
    from app.monitoring import fan_control

    bdf = bdf or fan_control._load().bdf or fan_control._select(fan_control._devices(), None)["bdf"]
    base = fan_plant.load(bdf)
    if base is None:
        raise SystemExit("この GPU の模型がありません")
    found = evaluate(fan_log.read_raw(time.time() - hours * 3600), base)
    fitted = found.pop("fitted")
    fitted.data.update(fitted_at=date.today().isoformat(),
                       source=f"本番の 1 秒ごとの記録 {found['samples']} 秒。検証区間の誤差 {found['before_rmse']} → {found['after_rmse']}")
    if found["improved"] and write:
        fan_plant.save_local(bdf, fitted)
        found["written"] = "data/amd-fan-plant.json"
    if found["improved"] and export:
        Path(export).write_text(json.dumps(fitted.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        found["exported"] = export
    return {**found, "version": fitted.version, "sensors": fitted.sensors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--bdf")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--export")
    args = parser.parse_args()
    print(json.dumps(run(args.hours, args.bdf, args.write, args.export), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
