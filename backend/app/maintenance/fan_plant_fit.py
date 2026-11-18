"""ファン制御の熱の模型（2 段）を、本番の 1 秒ごとの記録から当てはめ直す。

2026-11-18 利用者「熱モデルをどんどんブラッシュアップしていると思うが、そのモデルもプロジェクトに書き込んで
ファン制御の最適化に使えるようにし、更新したら新しいものに更新することとして」。

使い方（読むだけ。--write で書く）:
    env CONTROL_DECK_CONFIG=<本番の config> python -m app.maintenance.fan_plant_fit [--hours 2] [--bdf 0000:03:00.0]
        → 今の模型と当てはめ直した模型の誤差（°C）を比べて出す
    ... --write
        → 誤差が小さくなったときだけ、同梱の模型（app/monitoring/plant_models/<name>.json）を版を上げて書き換える。
          書き換えたらテスト（tests/test_fan_two_stage.py）を流してコミットする（模型の差し替え = コードの変更）
記録は fan_log の fanraw-*.csv（直近 2 時間）。休みと負荷の両方が入った 10 分以上が要る。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date

from app.monitoring import fan_log, fan_plant


def run(hours: float, bdf: str | None, write: bool) -> dict:
    samples = fan_log.read_raw(time.time() - hours * 3600)
    if bdf is None:
        from app.monitoring import fan_control

        bdf = fan_control._load().bdf or fan_control._select(fan_control._devices(), None)["bdf"]
    base = fan_plant.load(bdf)
    if base is None:
        raise SystemExit("この GPU の模型がありません（plant_models に同じ PCI device ID のものを置いてください）")
    kinds = {k: k for k in fan_plant.KINDS}
    data = [{"fan": s["fan"], "power": s["power"], "temps": s["temps"]} for s in samples]
    fan = [d["fan"] for d in data]
    power = [d["power"] for d in data]
    before = {k: round(fan_plant._rmse(base.sensors[k], fan, power, [d["temps"][k] for d in data], base.idle_w), 2)
              for k in fan_plant.KINDS} if data else {}
    fitted = fan_plant.fit(data, base, kinds)
    after = fitted.data["fit_rmse"]
    improved = all(after[k] <= before[k] for k in fan_plant.KINDS)
    result = {"samples": len(data), "before_rmse": before, "after_rmse": after, "improved": improved,
              "sensors": fitted.sensors}
    if write and improved:
        name = next((p.stem for p in fan_plant.PLANT_DIR.glob("*.json")
                     if json.loads(p.read_text(encoding="utf-8")).get("match") == base.data.get("match")), None)
        if name:
            fitted.data["fitted_at"] = date.today().isoformat()
            fitted.data["source"] = (f"本番の 1 秒ごとの記録 {len(data)} 秒（{date.today().isoformat()}、"
                                     f"app.maintenance.fan_plant_fit）。前の版の誤差 {before}")
            (fan_plant.PLANT_DIR / f"{name}.json").write_text(
                json.dumps(fitted.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            result["written"] = f"plant_models/{name}.json v{fitted.version}"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--bdf")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.hours, args.bdf, args.write), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
