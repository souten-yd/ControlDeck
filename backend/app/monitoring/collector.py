"""メトリクス収集。バックグラウンド asyncio タスクが定期収集し、
最新スナップショット + インメモリ履歴（生データ）+ 1 分平均（SQLite）を保持する。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import psutil

from app.config import get_config
from app.monitoring.gpu import BaseProvider, detect_provider

logger = logging.getLogger("control_deck.monitoring")


class MetricsCollector:
    def __init__(self) -> None:
        cfg = get_config().monitoring
        self.interval = max(1.0, float(cfg.interval_seconds))
        maxlen = int(cfg.raw_retention_hours * 3600 / self.interval)
        self.history: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.latest: dict[str, Any] | None = None
        self.gpu: BaseProvider = BaseProvider()
        self._last_disk = psutil.disk_io_counters()
        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        self._minute_bucket: list[dict[str, Any]] = []
        self._task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()

    # ---- 収集 ----

    def _collect_once(self) -> dict[str, Any]:
        now = time.monotonic()
        dt = max(0.001, now - self._last_time)

        cpu_percent = psutil.cpu_percent(interval=None)
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        load1, load5, load15 = psutil.getloadavg()
        freq = psutil.cpu_freq()
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        disk_io = psutil.disk_io_counters()
        net_io = psutil.net_io_counters()
        disk_read_bps = (disk_io.read_bytes - self._last_disk.read_bytes) / dt if disk_io and self._last_disk else 0
        disk_write_bps = (disk_io.write_bytes - self._last_disk.write_bytes) / dt if disk_io and self._last_disk else 0
        net_rx_bps = (net_io.bytes_recv - self._last_net.bytes_recv) / dt
        net_tx_bps = (net_io.bytes_sent - self._last_net.bytes_sent) / dt
        self._last_disk, self._last_net, self._last_time = disk_io, net_io, now

        cpu_temp = None
        try:
            temps = psutil.sensors_temperatures()
            for key in ("k10temp", "coretemp", "zenpower", "cpu_thermal"):
                if key in temps and temps[key]:
                    cpu_temp = temps[key][0].current
                    break
        except Exception:
            pass

        gpu_sample = None
        try:
            gpu_sample = self.gpu.sample()
        except Exception as e:  # GPU 取得失敗で全体を止めない
            logger.debug("GPU sample failed: %s", e)

        cpu_power = self._read_rapl_watts(dt)
        gpu_power = gpu_sample.get("power_watts") if gpu_sample else None
        total_power = None
        if cpu_power is not None or gpu_power is not None:
            total_power = (cpu_power or 0.0) + (gpu_power or 0.0)

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu": {
                "percent": cpu_percent,
                "per_cpu": per_cpu,
                "load": [load1, load5, load15],
                "freq_mhz": freq.current if freq else None,
                "temperature_c": cpu_temp,
                "cores": len(per_cpu),
            },
            "memory": {
                "total": mem.total,
                "used": mem.used,
                "available": mem.available,
                "percent": mem.percent,
                "cached": getattr(mem, "cached", 0),
                "swap_total": swap.total,
                "swap_used": swap.used,
                "swap_percent": swap.percent,
            },
            "gpu": dict(gpu_sample) if gpu_sample else None,
            "io": {
                "disk_read_bps": disk_read_bps,
                "disk_write_bps": disk_write_bps,
                "net_rx_bps": net_rx_bps,
                "net_tx_bps": net_tx_bps,
            },
            "power": {
                "cpu_watts_estimated": cpu_power,
                "gpu_watts": gpu_power,
                "total_watts_estimated": total_power,
                "is_estimate": True,
            },
            "uptime_seconds": time.time() - psutil.boot_time(),
        }
        return snapshot

    _rapl_last: tuple[float, float] | None = None

    def _read_rapl_watts(self, dt: float) -> float | None:
        try:
            from pathlib import Path

            for zone in sorted(Path("/sys/class/powercap").glob("intel-rapl:*/energy_uj")):
                energy = float(zone.read_text().strip())
                if self._rapl_last is None:
                    self._rapl_last = (energy, time.monotonic())
                    return None
                last_energy, last_t = self._rapl_last
                now = time.monotonic()
                self._rapl_last = (energy, now)
                if energy >= last_energy and now > last_t:
                    return (energy - last_energy) / 1e6 / (now - last_t)
                return None
        except (OSError, ValueError, PermissionError):
            return None
        return None

    # ---- 定期タスク ----

    async def run(self) -> None:
        self.gpu = await asyncio.to_thread(detect_provider)
        psutil.cpu_percent(interval=None)  # 初回サンプル
        last_minute_flush = time.monotonic()
        while True:
            try:
                snapshot = await asyncio.to_thread(self._collect_once)
                self.latest = snapshot
                self.history.append(snapshot)
                self._minute_bucket.append(snapshot)
                if time.monotonic() - last_minute_flush >= 60:
                    last_minute_flush = time.monotonic()
                    await asyncio.to_thread(self._flush_minute)
                dead = []
                for q in self._subscribers:
                    try:
                        q.put_nowait(snapshot)
                    except asyncio.QueueFull:
                        dead.append(q)
                for q in dead:
                    self._subscribers.discard(q)
            except Exception as e:
                logger.warning("metrics collection failed: %s", e)
            from app.maintenance.watchdog import beat

            beat("collector")
            await asyncio.sleep(self.interval)

    def _flush_minute(self) -> None:
        if not self._minute_bucket:
            return
        bucket, self._minute_bucket = self._minute_bucket, []

        def avg(getter) -> float | None:
            vals = [v for v in (getter(s) for s in bucket) if v is not None]
            return sum(vals) / len(vals) if vals else None

        from app.database import SessionLocal
        from app.models import MetricMinute

        gpu_pct = avg(lambda s: (s.get("gpu") or {}).get("utilization_percent"))
        vram_pct = avg(
            lambda s: (
                (s["gpu"]["vram_used_bytes"] / s["gpu"]["vram_total_bytes"] * 100)
                if s.get("gpu") and s["gpu"].get("vram_used_bytes") is not None and s["gpu"].get("vram_total_bytes")
                else None
            )
        )
        db = SessionLocal()
        try:
            db.add(
                MetricMinute(
                    timestamp=datetime.now(timezone.utc),
                    cpu_percent=avg(lambda s: s["cpu"]["percent"]),
                    memory_percent=avg(lambda s: s["memory"]["percent"]),
                    gpu_percent=gpu_pct,
                    vram_percent=vram_pct,
                    disk_read_bps=avg(lambda s: s["io"]["disk_read_bps"]),
                    disk_write_bps=avg(lambda s: s["io"]["disk_write_bps"]),
                    net_rx_bps=avg(lambda s: s["io"]["net_rx_bps"]),
                    net_tx_bps=avg(lambda s: s["io"]["net_tx_bps"]),
                )
            )
            # 保持期間を超えた行を削除
            cutoff = datetime.now(timezone.utc) - timedelta(
                days=get_config().monitoring.minute_retention_days
            )
            from sqlalchemy import delete

            db.execute(delete(MetricMinute).where(MetricMinute.timestamp < cutoff))
            db.commit()
        finally:
            db.close()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)


collector = MetricsCollector()
