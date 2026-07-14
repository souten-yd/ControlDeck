"""電気代の積算（起動セッション / 日別 / 月別）。

- PSU の DC 出力電力を効率で AC 入力電力へ換算し、台形積分で電力量を積算する。
- 積分間隔は time.monotonic()（実時刻変更に非依存）。日付キーはローカルタイムゾーン。
- メモリ上で高頻度に積算し、SQLite へは低頻度（既定10分）+ 境界/終了時にチェックポイント保存。
- 起動セッションは boot ID 単位。バックエンド再起動では維持、OS 再起動で 0 から。

SSD 書き込み抑制のため、2秒毎のサンプルは DB に書かず、未保存差分方式で日別を UPSERT する。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta

logger = logging.getLogger("control_deck.electricity")

MS_PER_HOUR = 3_600_000  # W·秒 → kWh は /3_600_000
# 異常なサンプル間隔の上限（秒）。既定周期2秒に対し、これを超える区間は積算しない
MAX_SAMPLE_GAP_SEC = 30.0


def read_boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def _local_now() -> datetime:
    """OS/設定のローカルタイムゾーンでの現在時刻（aware）。"""
    return datetime.now().astimezone()


def _split_energy_by_day(prev_input_w: float, cur_input_w: float,
                         start: datetime, end: datetime) -> list[tuple[str, float, float]]:
    """区間 [start, end] の電力量を日付境界で分割し、[(YYYY-MM-DD, kwh, seconds), ...] を返す。

    台形積分で線形補間し、各日ぶんを按分する。start/end は aware(ローカル)。
    """
    total_sec = (end - start).total_seconds()
    if total_sec <= 0:
        return []
    out: list[tuple[str, float, float]] = []
    seg_start = start
    # start の電力を基準に、区間内を線形補間して境界電力を求める
    while seg_start < end:
        day_end = datetime.combine(seg_start.date() + timedelta(days=1),
                                   datetime.min.time(), tzinfo=seg_start.tzinfo)
        seg_end = min(day_end, end)
        f0 = (seg_start - start).total_seconds() / total_sec
        f1 = (seg_end - start).total_seconds() / total_sec
        p0 = prev_input_w + (cur_input_w - prev_input_w) * f0
        p1 = prev_input_w + (cur_input_w - prev_input_w) * f1
        seg_sec = (seg_end - seg_start).total_seconds()
        kwh = ((p0 + p1) / 2) * seg_sec / MS_PER_HOUR
        out.append((seg_start.strftime("%Y-%m-%d"), kwh, seg_sec))
        seg_start = seg_end
    return out


class ElectricityAccumulator:
    """電力サンプルを受けて起動/日別を積算し、低頻度で永続化する。スレッドセーフ。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.boot_id = read_boot_id()
        self.session_energy_kwh = 0.0
        self.session_cost_yen = 0.0
        # 未保存差分方式: 日別の絶対累積(メモリ) と 永続化済み値
        self._today: dict[str, dict] = {}  # date -> {"energy_kwh","cost_yen","seconds","first","last","price"}
        self._persisted: dict[str, dict] = {}
        self._prev_input_w: float | None = None
        self._prev_monotonic: float | None = None
        self._prev_wall: datetime | None = None
        self._last_persist_monotonic = time.monotonic()
        self._last_persisted_at: datetime | None = None
        self._loaded = False

    # ---- 設定取得 ----
    def _cfg(self):
        from app.config import get_config

        return get_config().monitoring.electricity

    # ---- 復元 ----
    def load(self) -> None:
        """DB から起動セッション（同一 boot ID）と当月日別を復元する。"""
        from app.database import SessionLocal
        from app.models import ElectricityDaily, ElectricityState

        db = SessionLocal()
        try:
            st = db.get(ElectricityState, 1)
            if st is not None and st.boot_id == self.boot_id:
                self.session_energy_kwh = st.session_energy_kwh or 0.0
                self.session_cost_yen = st.session_cost_yen or 0.0
                self._prev_input_w = None  # 積分は再接続後の最初のサンプルから
                logger.info("電気代セッションを復元: %.4f kWh (boot %s)", self.session_energy_kwh, self.boot_id[:8])
            elif st is not None:
                logger.info("新しい OS 起動セッション（boot ID 変化）: 起動中電気代を 0 から開始")
            # 当日・当月の日別を復元
            month_prefix = _local_now().strftime("%Y-%m")
            rows = db.query(ElectricityDaily).filter(
                ElectricityDaily.local_date.like(f"{month_prefix}-%")
            ).all()
            for r in rows:
                snap = {"energy_kwh": r.energy_kwh, "cost_yen": r.cost_yen,
                        "seconds": r.sample_duration_sec, "price": r.price_per_kwh_yen,
                        "first": r.first_sample_at, "last": r.last_sample_at}
                self._today[r.local_date] = dict(snap)
                self._persisted[r.local_date] = dict(snap)
        except Exception:
            logger.exception("電気代の復元に失敗（0 から継続）")
        finally:
            db.close()
        self._loaded = True

    # ---- サンプル受信（collector から約2秒毎） ----
    def update(self, output_power_w: float | None) -> None:
        cfg = self._cfg()
        if not cfg.enabled:
            return
        now_mono = time.monotonic()
        now_wall = _local_now()
        with self._lock:
            if output_power_w is None:
                # PSU 取得不能: 区間を積算せず、次の復帰を新しい積分開始点にする
                self._prev_input_w = None
                self._prev_monotonic = None
                self._prev_wall = None
                return
            input_w = output_power_w / cfg.psu_efficiency
            if self._prev_input_w is not None and self._prev_monotonic is not None and self._prev_wall is not None:
                dt = now_mono - self._prev_monotonic
                # 異常間隔（逆行・欠測・サスペンド跨ぎ）は積算しない
                if 0 < dt <= MAX_SAMPLE_GAP_SEC:
                    self._integrate(self._prev_input_w, input_w, self._prev_wall, now_wall, cfg.price_per_kwh_yen)
            self._prev_input_w = input_w
            self._prev_monotonic = now_mono
            self._prev_wall = now_wall
        self._maybe_persist(cfg)

    def _integrate(self, prev_w: float, cur_w: float, start: datetime, end: datetime, price: float) -> None:
        delta_kwh_total = ((prev_w + cur_w) / 2) * (end - start).total_seconds() / MS_PER_HOUR
        self.session_energy_kwh += delta_kwh_total
        self.session_cost_yen += delta_kwh_total * price
        # 日別（日付境界で分割）
        for day, kwh, sec in _split_energy_by_day(prev_w, cur_w, start, end):
            d = self._today.setdefault(day, {"energy_kwh": 0.0, "cost_yen": 0.0, "seconds": 0.0,
                                             "price": price, "first": None, "last": None})
            d["energy_kwh"] += kwh
            d["cost_yen"] += kwh * price  # その時点の単価で加算（過去分は変えない）
            d["seconds"] += sec
            d["price"] = price
            if d["first"] is None:
                d["first"] = start
            d["last"] = end

    # ---- 永続化 ----
    def _maybe_persist(self, cfg) -> None:
        if time.monotonic() - self._last_persist_monotonic >= cfg.persistence_interval_seconds:
            self.persist()

    def persist(self, reason: str = "interval") -> None:
        """メモリの累積を SQLite へチェックポイント保存（未保存差分のみ UPSERT）。"""
        from app.database import SessionLocal
        from app.models import ElectricityDaily, ElectricityState

        with self._lock:
            today_snapshot = {k: dict(v) for k, v in self._today.items()}
            session_e, session_c = self.session_energy_kwh, self.session_cost_yen
            prev_w = self._prev_input_w
            prev_wall = self._prev_wall
        db = SessionLocal()
        try:
            changed = False
            for day, cur in today_snapshot.items():
                prev = self._persisted.get(day)
                if prev and abs(cur["energy_kwh"] - prev["energy_kwh"]) < 1e-12:
                    continue  # 変化なしは書かない（無駄な書き込み抑制）
                row = db.get(ElectricityDaily, day)
                if row is None:
                    row = ElectricityDaily(local_date=day)
                    db.add(row)
                row.energy_kwh = cur["energy_kwh"]
                row.cost_yen = cur["cost_yen"]
                row.price_per_kwh_yen = cur["price"]
                row.sample_duration_sec = cur["seconds"]
                row.first_sample_at = cur["first"]
                row.last_sample_at = cur["last"]
                changed = True
            # 起動セッション状態
            st = db.get(ElectricityState, 1)
            if st is None:
                st = ElectricityState(id=1)
                db.add(st)
            st.boot_id = self.boot_id
            st.session_energy_kwh = session_e
            st.session_cost_yen = session_c
            st.last_input_power_w = prev_w
            st.last_sample_wall_time = prev_wall
            st.last_persisted_at = _local_now()
            db.commit()
            self._last_persisted_at = st.last_persisted_at
            self._persisted = {k: dict(v) for k, v in today_snapshot.items()}
            self._last_persist_monotonic = time.monotonic()
            if changed:
                logger.debug("電気代をチェックポイント保存 (%s)", reason)
        except Exception:
            logger.exception("電気代の保存に失敗（メモリ累積は保持）")
            db.rollback()
        finally:
            db.close()

    # ---- 参照（API 用） ----
    def snapshot(self) -> dict:
        from app.database import SessionLocal
        from app.models import ElectricityDaily

        cfg = self._cfg()
        now = _local_now()
        today_key = now.strftime("%Y-%m-%d")
        month_prefix = now.strftime("%Y-%m")
        with self._lock:
            today = self._today.get(today_key, {"energy_kwh": 0.0, "cost_yen": 0.0})
            session_e, session_c = self.session_energy_kwh, self.session_cost_yen
            # 当月はメモリの日別を合算（未保存分を含む）
            month_e = sum(v["energy_kwh"] for k, v in self._today.items() if k.startswith(month_prefix))
            month_c = sum(v["cost_yen"] for k, v in self._today.items() if k.startswith(month_prefix))
            last_persisted = self._last_persisted_at
        # 当月の過去日（メモリ外＝先月以前の起動）も DB から補完
        db = SessionLocal()
        try:
            rows = db.query(ElectricityDaily).filter(
                ElectricityDaily.local_date.like(f"{month_prefix}-%")
            ).all()
            for r in rows:
                if r.local_date not in self._today:
                    month_e += r.energy_kwh
                    month_c += r.cost_yen
        except Exception:
            pass
        finally:
            db.close()
        return {
            "session_energy_kwh": round(session_e, 6),
            "session_cost_yen": round(session_c, 4),
            "today_energy_kwh": round(today["energy_kwh"], 6),
            "today_cost_yen": round(today["cost_yen"], 4),
            "month_energy_kwh": round(month_e, 6),
            "month_cost_yen": round(month_c, 4),
            "price_per_kwh_yen": cfg.price_per_kwh_yen,
            "psu_efficiency": cfg.psu_efficiency,
            "persistence_interval_seconds": cfg.persistence_interval_seconds,
            "last_persisted_at": last_persisted.isoformat() if last_persisted else None,
        }


# シングルトン（collector と lifespan が共有）
accumulator = ElectricityAccumulator()
