"""ファン制御の記録（診断用）と、その記録からの作り直し。

2026-11-18 利用者の依頼「温度を診断に記録しておけないか。PID 制御が逸脱した際に毎回長時間調整するのが面倒」
「全部記録すると容量がパンクするので、AI に読み込める程度の容量に」。

* 記録: 制御中の 3 秒ごとの値を 1 分ごとにまとめ（平均・最高・最低）、data/diagnostics/fan-YYYYMMDD.csv へ
  1 行で書く。1 行約 85 バイト・1 日最大 1440 行（約 120 KB）、直近 KEEP_DAYS 日だけ残す（合計 約 250 KB）。
  書けなくても制御は止めない。
* 逸脱: 「どれかのセンサーが目標を DEVIATION_C 超えたまま DEVIATION_SECONDS 続いた」は 3 秒ごとの値で
  その場で数え、回数（dev）と超えていた秒数（over_s）だけを 1 分の行に残す。緊急の最大回転は emerg。
  自己診断（ファン制御）と診断の報告がここから読む。
* 作り直し: 記録の中の「全力で回転も温度も落ち着いていた 3 分」から熱抵抗（熱モデルの点）を、
  「休みで落ち着いていた 3 分」からアイドルの温度（外気温の基準）を測り直す。
  自動調整（30〜90 分、GPU を占有）を回さずに、ふだんの使い方の記録だけで数秒で済む。
"""
from __future__ import annotations

import csv
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from app.config import data_dir

logger = logging.getLogger("control_deck.fan_log")

KEEP_DAYS = 2
# 1 分の行の列。温度は種類で持つ（hot=hotspot、mem=VRAM、edge=GPU 本体）。lo/hi はその 1 分の最低・最高
FIELDS = ("t", "n", "p", "p_lo", "p_hi", "edge", "hot", "hot_hi", "mem", "mem_hi", "rpm", "rpm_hi",
          "pct", "pct_lo", "pct_hi", "ff", "i", "err_hi", "over_s", "dev", "emerg", "tune", "floor", "rsv")
KIND_COLUMN = {"edge": "edge", "junction": "hot", "mem": "mem"}
DEVIATION_C = 2.0
DEVIATION_SECONDS = 60.0
# 作り直しに使う落ち着いた区間: 連続 3 分、各分の回転の幅 3 ポイント・電力の幅 15%・温度の変化 2°C 以内
STEADY_MINUTES = 3
STEADY_FAN_SPAN = 3.0
STEADY_TEMP_SPAN = 2.0
_lock = threading.Lock()


def _root() -> Path:
    path = data_dir() / "diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file(day: datetime) -> Path:
    return _root() / f"fan-{day:%Y%m%d}.csv"


def _write(row: dict) -> None:
    try:
        with _lock:
            path = _file(datetime.fromtimestamp(row["t"]))
            new = not path.exists()
            if not new:
                _upgrade_header(path)
            with path.open("a", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
                if new:
                    writer.writeheader()
                writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS})
    except OSError:
        logger.debug("ファン制御の記録を書けません", exc_info=True)


def _upgrade_header(path: Path) -> None:
    """見出しが古い（列が少ない）ファイルを、今の見出しで書き直す（足した列がずれないように）。"""
    with path.open(encoding="utf-8", newline="") as stream:
        header = stream.readline().strip()
    if header == ",".join(FIELDS):
        return
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") or "" for k in FIELDS})
    tmp.replace(path)


class Recorder:
    """3 秒ごとの値を受け取り、1 分ごとに 1 行にまとめて書く。逸脱はここで 3 秒の粒度で数える。"""

    def __init__(self, write=_write) -> None:
        self.write = write
        self.samples: list[dict] = []
        self.minute: int | None = None
        self.over_since: float | None = None
        self.counted = False
        self.last_t: float | None = None
        self.dev = 0
        self.over_s = 0.0

    def add(self, t: float, *, temps: dict[str, float | None], targets: dict[str, float], power_w: float | None,
            rpm: float | None, percent: float, ff: float, integral: float, error_c: float,
            emergency: bool, tuning: bool, floor: float = 0.0, reserve_c: float = 0.0) -> None:
        minute = int(t // 60)
        if self.minute is not None and minute != self.minute:
            self.flush()
        self.minute = minute
        if self.last_t is not None and t - self.last_t > 30:
            self.over_since, self.counted = None, False      # 記録の切れ目
        over = any(v is not None and k in targets and v > targets[k] + DEVIATION_C for k, v in temps.items())
        if over:
            if self.over_since is None:
                self.over_since = t
            elif self.last_t is not None:
                self.over_s += t - self.last_t
            if not self.counted and t - self.over_since >= DEVIATION_SECONDS:
                self.dev, self.counted = self.dev + 1, True
        else:
            self.over_since, self.counted = None, False
        self.last_t = t
        self.samples.append({"temps": temps, "power_w": power_w, "rpm": rpm, "percent": percent, "ff": ff,
                             "i": integral, "err": error_c, "emergency": emergency, "tuning": tuning,
                             "floor": floor, "rsv": reserve_c})

    def flush(self) -> dict | None:
        if not self.samples or self.minute is None:
            return None
        s = self.samples

        def col(pick):
            return [v for v in (pick(x) for x in s) if v is not None]

        def mean(values, digits=1):
            return round(sum(values) / len(values), digits) if values else None

        row: dict = {"t": (self.minute + 1) * 60, "n": len(s)}
        powers = col(lambda x: x["power_w"])
        row.update(p=mean(powers, 0), p_lo=min(powers, default=None), p_hi=max(powers, default=None))
        for kind, name in KIND_COLUMN.items():
            values = col(lambda x, k=kind: x["temps"].get(k))
            row[name] = mean(values)
            if name != "edge":
                row[f"{name}_hi"] = max(values, default=None)
        rpms = col(lambda x: x["rpm"])
        row.update(rpm=mean(rpms, 0), rpm_hi=max(rpms, default=None))
        pcts = col(lambda x: x["percent"])
        row.update(pct=mean(pcts), pct_lo=min(pcts, default=None), pct_hi=max(pcts, default=None))
        row.update(ff=mean(col(lambda x: x["ff"])), i=round(s[-1]["i"], 1),
                   err_hi=round(max(x["err"] for x in s), 1), over_s=round(self.over_s), dev=self.dev,
                   emerg=sum(1 for x in s if x["emergency"]), tune=int(any(x["tuning"] for x in s)),
                   floor=mean(col(lambda x: x.get("floor")), 0) or None, rsv=mean(col(lambda x: x.get("rsv")), 0) or None)
        for key in ("p_lo", "p_hi", "rpm_hi"):
            if row[key] is not None:
                row[key] = round(row[key])
        self.samples, self.dev, self.over_s = [], 0, 0.0
        self.write(row)
        return row


_recorder = Recorder()


def add(t: float, **values) -> None:
    try:
        _recorder.add(t, **values)
    except Exception:  # noqa: BLE001 - 記録のせいで制御を止めない
        logger.debug("ファン制御の記録をまとめられません", exc_info=True)


def _num(value: str) -> float | None:
    try:
        return float(value) if value != "" else None
    except ValueError:
        return None


def read(since: float = 0.0) -> list[dict]:
    """古い順。数値にして返す（空欄は None）。"""
    rows: list[dict] = []
    for path in sorted(_root().glob("fan-*.csv")):
        try:
            day = datetime.strptime(path.stem.split("-", 1)[1], "%Y%m%d")
        except ValueError:
            continue
        if (day + timedelta(days=1)).timestamp() < since:
            continue
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                for raw in csv.DictReader(stream):
                    row = {k: _num(raw.get(k, "")) for k in FIELDS}
                    if row["t"] is not None and row["t"] >= since:
                        rows.append(row)
        except OSError:
            continue
    return rows


def prune() -> None:
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    for path in _root().glob("fan-*.csv"):
        try:
            if datetime.strptime(path.stem.split("-", 1)[1], "%Y%m%d") < cutoff:
                path.unlink()
        except (ValueError, OSError):
            continue


def summary(rows: list[dict]) -> dict:
    """逸脱の集計（自己診断と報告に出す）。"""
    peak = {name: max((r[f"{name}_hi"] for r in rows if r.get(f"{name}_hi") is not None), default=None)
            for name in ("hot", "mem")}
    emergency_minutes = [r["t"] for r in rows if r.get("emerg")]
    deviation_minutes = [r["t"] for r in rows if r.get("dev")]
    return {"minutes": len(rows), "deviations": int(sum(r.get("dev") or 0 for r in rows)),
            "deviation_seconds": int(sum(r.get("over_s") or 0 for r in rows)),
            "emergency_minutes": len(emergency_minutes), "peak_c": peak,
            "last_deviation": max(deviation_minutes, default=None),
            "last_emergency": max(emergency_minutes, default=None),
            "from": rows[0]["t"] if rows else None, "to": rows[-1]["t"] if rows else None}


def _steady_runs(rows: list[dict], loaded: bool, idle_power_w: float | None, power_cap_w: float | None):
    """落ち着いた連続 STEADY_MINUTES 分を、重ならないように順に返す。(行, 平均電力, 平均回転)"""
    from app.monitoring import fan_pid

    i = 0
    while i + STEADY_MINUTES <= len(rows):
        run = rows[i:i + STEADY_MINUTES]
        ok = all(b["t"] - a["t"] <= 61 for a, b in zip(run, run[1:]))
        ok = ok and all(not r.get("tune") and not r.get("emerg") and r.get("p") is not None
                        and r.get("pct") is not None and (r.get("n") or 0) >= 15 for r in run)
        if ok:
            power = sum(r["p"] for r in run) / len(run)
            if loaded:
                ok = (fan_pid.loaded(power, power_cap_w, idle_power_w)
                      and max(r["p_hi"] for r in run) - min(r["p_lo"] for r in run) <= 0.15 * power
                      and max(r["pct_hi"] for r in run) - min(r["pct_lo"] for r in run) <= STEADY_FAN_SPAN)
            else:
                ok = max(r["p_hi"] for r in run) <= min(60.0, 0.15 * (power_cap_w or 300.0))
            # 温度が落ち着いた（後ろ 2 分の平均の差が小さい）
            ok = ok and all(run[-1].get(c) is not None and run[-2].get(c) is not None
                            and abs(run[-1][c] - run[-2][c]) <= STEADY_TEMP_SPAN for c in KIND_COLUMN.values())
        if ok:
            yield run, power, sum(r["pct"] for r in run) / len(run)
            i += STEADY_MINUTES
        else:
            i += 1


def refit(rows: list[dict], model, sensor_ids: dict[str, str], power_cap_w: float | None) -> dict:
    """記録から熱モデル（アイドルの温度・熱抵抗の点・プロセスゲイン）を作り直す。model をその場で書き換える。

    sensor_ids: 種類 → センサー ID（例 {"junction": "temp2"}）。返り値は何を変えたか（画面と監査へ出す）。
    """
    if len(rows) < STEADY_MINUTES:
        raise ValueError("記録が足りません（制御中の記録が数分以上要ります）")
    idle_runs = list(_steady_runs(rows, False, model.idle_power_w, power_cap_w))
    changes: dict = {"idle_runs": len(idle_runs), "loaded_runs": 0, "points": []}
    if idle_runs:
        recent = idle_runs[-5:]   # 新しい休みを優先（外気温は季節で変わる）
        temps = {}
        for kind, sid in sensor_ids.items():
            column = KIND_COLUMN[kind]
            values = [run[-1][column] for run, _, _ in recent if run[-1].get(column) is not None]
            if values:
                temps[sid] = round(median(values), 2)
        if temps:
            model.idle_c = temps
            model.idle_power_w = round(median([p for _, p, _ in recent]), 1)
            changes["idle_c"] = temps
    if not model.idle_c or model.idle_power_w is None:
        raise ValueError("休みの記録が無いため、アイドルの温度を測れません（数分 GPU を休ませてからやり直してください）")
    buckets: dict[float, list[dict]] = {}
    for run, power, fan in _steady_runs(rows, True, model.idle_power_w, power_cap_w):
        heat = power - model.idle_power_w
        if heat <= 10:
            continue
        r = {}
        for kind, sid in sensor_ids.items():
            value = run[-1].get(KIND_COLUMN[kind])
            if value is not None and sid in model.idle_c:
                r[sid] = max(0.0, (value - model.idle_c[sid]) / heat)
        if r:
            buckets.setdefault(round(fan / 5) * 5, []).append({"power_w": heat, "r": r})
            changes["loaded_runs"] += 1
    if not buckets:
        raise ValueError("全力で落ち着いた区間の記録がありません（3 分以上続く負荷の記録が要ります）")
    for bucket, items in sorted(buckets.items()):
        r = {sid: median([i["r"][sid] for i in items if sid in i["r"]]) for sid in items[-1]["r"]}
        model.add_point(bucket, median([i["power_w"] for i in items]), r, weight=1.0)
        changes["points"].append({"fan": bucket, "count": len(items)})
    # プロセスゲイン（°C/%）: 回転の違う点が 2 つ以上（10 ポイント以上離れて）あれば、その傾きから
    governing = sensor_ids.get("junction") or next(iter(sensor_ids.values()))
    usable = sorted((p for p in model.points if governing in p["r"]), key=lambda p: p["fan"])
    if len(usable) >= 2 and usable[-1]["fan"] - usable[0]["fan"] >= 10:
        a, b = usable[0], usable[-1]
        heat = (a["power_w"] + b["power_w"]) / 2
        gain = (a["r"][governing] - b["r"][governing]) * heat / (b["fan"] - a["fan"])
        if gain > 0:
            model.gain_c_per_percent = round(-gain, 4)
            changes["gain_c_per_percent"] = model.gain_c_per_percent
    model.tuned = {**(model.tuned or {}), "refit_at": round(time.time()), "points": len(model.points)}
    return changes


# ---- 1 秒ごとの記録（熱の模型を当てはめ直すため。直近 RAW_KEEP_HOURS 時間だけ） ----
# 2026-11-18 利用者「熱モデルもプロジェクトに書き込んで最適化に使えるようにし、更新したら新しいものに」。
# 模型の当てはめ（fan_plant.fit）にはダイの速い跳ね（1 秒未満）が見える粒度が要る。1 時間で約 0.25 MB

RAW_FIELDS = ("t", "fan", "power", "junction", "mem", "edge", "rpm")
RAW_KEEP_HOURS = 2


def _raw_file(t: float) -> Path:
    return _root() / f"fanraw-{datetime.fromtimestamp(t):%Y%m%d%H}.csv"


def add_raw(t: float, *, fan: float | None, power: float | None, temps: dict[str, float | None],
            rpm: float | None) -> None:
    try:
        with _lock:
            path = _raw_file(t)
            new = not path.exists()
            with path.open("a", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                if new:
                    writer.writerow(RAW_FIELDS)
                writer.writerow([round(t, 1), "" if fan is None else round(fan, 1), "" if power is None else power,
                                 *("" if temps.get(k) is None else temps[k] for k in ("junction", "mem", "edge")),
                                 "" if rpm is None else rpm])
            if new:
                cutoff = t - RAW_KEEP_HOURS * 3600 - 3600
                for old in _root().glob("fanraw-*.csv"):
                    try:
                        if datetime.strptime(old.stem.split("-", 1)[1], "%Y%m%d%H").timestamp() < cutoff:
                            old.unlink()
                    except (ValueError, OSError):
                        continue
    except OSError:
        logger.debug("ファン制御の 1 秒ごとの記録を書けません", exc_info=True)


def read_raw(since: float = 0.0) -> list[dict]:
    """1 秒ごとの記録（古い順）。fan_plant.fit の形 {t, fan, power, temps: {kind: °C}} で返す。"""
    out: list[dict] = []
    for path in sorted(_root().glob("fanraw-*.csv")):
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                for raw in csv.DictReader(stream):
                    values = {k: _num(raw.get(k, "")) for k in RAW_FIELDS}
                    if values["t"] is None or values["t"] < since or values["fan"] is None or values["power"] is None:
                        continue
                    temps = {k: values[k] for k in ("junction", "mem", "edge")}
                    if any(v is None for v in temps.values()):
                        continue
                    out.append({"t": values["t"], "fan": values["fan"], "power": values["power"], "temps": temps})
        except OSError:
            continue
    return out
