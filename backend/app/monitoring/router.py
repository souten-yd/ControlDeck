from __future__ import annotations

import asyncio
import platform
import socket
import time
from datetime import datetime, timezone

import psutil
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.database import SessionLocal, get_db
from app.models import MetricMinute, User
from app.monitoring.collector import collector
from app.security.deps import authenticate_websocket, require_permission

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/overview")
def overview(user: User = Depends(require_permission("system.view"))):
    latest = collector.latest or {}
    return {
        "metrics": latest,
        "host": {
            "hostname": socket.gethostname(),
            "os": _os_release(),
            "kernel": platform.release(),
            "boot_time": datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat(),
            "uptime_seconds": time.time() - psutil.boot_time(),
            "time": datetime.now().astimezone().isoformat(),
            "timezone": str(datetime.now().astimezone().tzinfo),
        },
    }


def _os_release() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


@router.get("/self-status")
def self_status(user: User = Depends(require_permission("system.view"))):
    """Control Deck 自身の健全性（ウォッチドッグ・内部チェック・自己メンテナンス）。"""
    from app.maintenance.service import INTERVAL, last_run
    from app.maintenance.watchdog import health_checks, watchdog_enabled

    return {
        "watchdog_enabled": watchdog_enabled(),
        "checks": health_checks(),
        "maintenance": {
            "interval_seconds": INTERVAL,
            "last_run_at": last_run["at"],
            "last_results": last_run["results"],
        },
    }


@router.get("/disk")
def disk(user: User = Depends(require_permission("system.view"))):
    parts = []
    for p in psutil.disk_partitions(all=False):
        if p.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay"):
            continue
        try:
            usage = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        parts.append(
            {
                "device": p.device,
                "mountpoint": p.mountpoint,
                "fstype": p.fstype,
                "total": usage.total,
                "used": usage.used,
                "percent": usage.percent,
            }
        )
    return parts


@router.get("/network")
def network(user: User = Depends(require_permission("system.view"))):
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    result = []
    for name, addr_list in addrs.items():
        if name == "lo":
            continue
        ips = [a.address for a in addr_list if a.family == socket.AF_INET]
        st = stats.get(name)
        io = counters.get(name)
        result.append(
            {
                "interface": name,
                "ips": ips,
                "is_up": st.isup if st else False,
                "speed_mbps": st.speed if st else None,
                "bytes_recv": io.bytes_recv if io else 0,
                "bytes_sent": io.bytes_sent if io else 0,
            }
        )
    return result


@router.get("/processes")
def top_processes(
    limit: int = Query(default=15, le=50),
    user: User = Depends(require_permission("system.view")),
):
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info"]):
        try:
            info = p.info
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info["name"],
                    "username": info["username"],
                    "cpu_percent": info["cpu_percent"] or 0.0,
                    "memory_bytes": info["memory_info"].rss if info["memory_info"] else 0,
                }
            )
        except psutil.Error:
            continue
    procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return procs[:limit]


@router.get("/metrics/history")
def metrics_history(
    minutes: int = Query(default=15, ge=1, le=60 * 24 * 30),
    user: User = Depends(require_permission("system.view")),
    db=Depends(get_db),
):
    """15 分以内はインメモリ生データ、それ以上は 1 分平均を返す。"""
    if minutes <= 60:
        cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
        samples = [
            {
                "timestamp": s["timestamp"],
                "cpu_percent": s["cpu"]["percent"],
                "memory_percent": s["memory"]["percent"],
                "gpu_percent": (s.get("gpu") or {}).get("utilization_percent"),
                "vram_percent": _vram_pct(s),
                "net_rx_bps": s["io"]["net_rx_bps"],
                "net_tx_bps": s["io"]["net_tx_bps"],
            }
            for s in collector.history
            if datetime.fromisoformat(s["timestamp"]).timestamp() >= cutoff
        ]
        # モバイル向けに最大 600 点へ間引き
        step = max(1, len(samples) // 600)
        return {"resolution": "raw", "samples": samples[::step]}

    from datetime import timedelta

    cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (
        db.execute(
            select(MetricMinute).where(MetricMinute.timestamp >= cutoff_dt).order_by(MetricMinute.timestamp)
        )
        .scalars()
        .all()
    )
    return {
        "resolution": "minute",
        "samples": [
            {
                "timestamp": r.timestamp.isoformat(),
                "cpu_percent": r.cpu_percent,
                "memory_percent": r.memory_percent,
                "gpu_percent": r.gpu_percent,
                "vram_percent": r.vram_percent,
                "net_rx_bps": r.net_rx_bps,
                "net_tx_bps": r.net_tx_bps,
            }
            for r in rows
        ],
    }


def _vram_pct(s: dict) -> float | None:
    gpu = s.get("gpu")
    if gpu and gpu.get("vram_used_bytes") is not None and gpu.get("vram_total_bytes"):
        return gpu["vram_used_bytes"] / gpu["vram_total_bytes"] * 100
    return None


@router.websocket("/metrics/stream")
async def metrics_stream(websocket: WebSocket):
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "system.view")
        if user is None:
            return
    finally:
        db.close()
    await websocket.accept()
    if collector.latest:
        await websocket.send_json(collector.latest)
    queue = collector.subscribe()
    try:
        while True:
            snapshot = await queue.get()
            await websocket.send_json(snapshot)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        collector.unsubscribe(queue)
