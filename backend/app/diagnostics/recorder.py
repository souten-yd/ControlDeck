"""常駐の記録係: 警告・エラー、event loop の詰まり、遅い要求、メモリの推移。

event loop の詰まりは、loop の外の thread から見張る（中に置くと詰まったとき自分も止まる）。
1 秒以上返ってこなければ、その間の loop の thread の stack を数回撮って、どの関数が
塞いでいたかを残す。既存の watchdog の見張り（12 秒で全 thread を stderr へ）より短い詰まり
（画面の「なんか遅い」の正体）を捕まえるためのもの。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from collections import defaultdict, deque

from app.diagnostics import store

logger = logging.getLogger("control_deck.diagnostics")

STALL_SECONDS = 1.0
SLOW_REQUEST_SECONDS = 2.0
MEMORY_INTERVAL = 30.0

_loop_tick = time.monotonic()
_loop_thread_id: int | None = None
_started = False

# 要求ごとの所要時間（直近 200 件ずつ）
_route_times: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
_memory: deque[dict] = deque(maxlen=2880)  # 30 秒 × 24 時間


# ---- 警告・エラー -------------------------------------------------------
class _EventHandler(logging.Handler):
    """WARNING 以上を記録へ。診断自身のログと、頻出する接続ログは外す。"""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("control_deck.diagnostics"):
            return
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        event = {"level": record.levelname, "logger": record.name, "message": message[:2000]}
        if record.exc_info:
            event["traceback"] = "".join(traceback.format_exception(*record.exc_info))[-6000:]
        store.record("log", **event)


# ---- event loop の詰まり ------------------------------------------------
async def _loop_ticker() -> None:
    global _loop_tick, _loop_thread_id
    _loop_thread_id = threading.get_ident()
    while True:
        _loop_tick = time.monotonic()
        await asyncio.sleep(0.25)


def _app_frames(frame) -> list[str]:
    """stack を「呼ばれた順」で、ControlDeck のコードを中心に短くする。"""
    rows = traceback.extract_stack(frame)
    lines = [f"{row.filename}:{row.lineno} in {row.name}" for row in rows]
    app = [line for line in lines if "/backend/app/" in line]
    tail = lines[-6:]
    return (app[-12:] + ["…"] + tail) if app and app[-1] not in tail else lines[-16:]


def _stall_watcher(tick=lambda: _loop_tick, thread=lambda: _loop_thread_id, stop: threading.Event | None = None) -> None:
    """tick / thread は差し替えられる（テストで別の loop を見張るため）。"""
    samples: list[list[str]] = []
    stalled_since: float | None = None
    while not (stop and stop.is_set()):
        time.sleep(0.25)
        age = time.monotonic() - tick()
        if age >= STALL_SECONDS:
            if stalled_since is None:
                stalled_since = time.time() - age
                samples = []
            frame = sys._current_frames().get(thread() or 0)
            if frame is not None and len(samples) < 8:
                samples.append(_app_frames(frame))
            continue
        if stalled_since is not None:
            seconds = round(time.time() - stalled_since, 2)
            # いちばん多く見えた stack を「塞いでいたもの」とする
            counts: dict[str, int] = defaultdict(int)
            for sample in samples:
                counts["\n".join(sample)] += 1
            top = max(counts, key=counts.get) if counts else ""
            store.record("loop_stall", seconds=seconds, stack=top.split("\n") if top else [],
                         samples=len(samples))
            stalled_since = None


# ---- 遅い要求 ------------------------------------------------------------
def install_middleware(app) -> None:
    @app.middleware("http")
    async def _timing(request, call_next):
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            seconds = time.monotonic() - started
            route = request.scope.get("route")
            key = f"{request.method} {getattr(route, 'path', None) or request.url.path}"
            _route_times[key].append(seconds)
            if seconds >= SLOW_REQUEST_SECONDS and not key.endswith(("/stream", "/events")):
                store.record("slow_request", route=key, seconds=round(seconds, 2), status=status)


def route_stats() -> list[dict]:
    out = []
    for key, values in list(_route_times.items()):
        ordered = sorted(values)
        if not ordered:
            continue
        out.append({"route": key, "count": len(ordered),
                    "p50": round(ordered[len(ordered) // 2], 3),
                    "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
                    "max": round(ordered[-1], 3)})
    return sorted(out, key=lambda row: row["p95"], reverse=True)


# ---- メモリ --------------------------------------------------------------
def _unit_of(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as stream:
            path = stream.read().strip().rsplit(":", 1)[-1]
    except OSError:
        return ""
    parts = [part for part in path.split("/") if part.endswith((".service", ".scope"))]
    return parts[-1] if parts else ""


def _vram() -> list[dict]:
    out = []
    for card in sorted(os.listdir("/sys/class/drm")) if os.path.isdir("/sys/class/drm") else []:
        base = f"/sys/class/drm/{card}/device"
        if not card.startswith("card") or "-" in card or not os.path.exists(f"{base}/mem_info_vram_used"):
            continue
        try:
            used = int(open(f"{base}/mem_info_vram_used").read())
            total = int(open(f"{base}/mem_info_vram_total").read())
        except (OSError, ValueError):
            continue
        out.append({"card": card, "used_gb": round(used / 2**30, 2), "total_gb": round(total / 2**30, 2)})
    return out


def memory_sample(top: int = 8) -> dict:
    import psutil

    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    procs = []
    for proc in psutil.process_iter(["pid", "name", "memory_info", "cmdline"]):
        try:
            rss = proc.info["memory_info"].rss
        except (psutil.Error, AttributeError):
            continue
        procs.append((rss, proc))
    procs.sort(key=lambda row: row[0], reverse=True)
    rows = []
    for rss, proc in procs[:top]:
        cmd = " ".join(proc.info.get("cmdline") or [])[:160]
        rows.append({"pid": proc.info["pid"], "name": proc.info["name"], "rss_gb": round(rss / 2**30, 2),
                     "unit": _unit_of(proc.info["pid"]), "cmd": cmd})
    return {"ts": time.time(), "total_gb": round(vm.total / 2**30, 1),
            "available_gb": round(vm.available / 2**30, 2), "used_percent": vm.percent,
            "swap_used_gb": round(swap.used / 2**30, 2), "vram": _vram(), "top": rows}


async def _memory_loop() -> None:
    low_reported = False
    while True:
        try:
            sample = await asyncio.to_thread(memory_sample)
            _memory.append(sample)
            # 空きが少ないときは、そのときの上位を記録へ（OOM の直前の様子を残す）
            if sample["available_gb"] < max(4.0, sample["total_gb"] * 0.06):
                if not low_reported:
                    store.record("memory_low", available_gb=sample["available_gb"], top=sample["top"],
                                 vram=sample["vram"])
                    low_reported = True
            else:
                low_reported = False
        except Exception:  # noqa: BLE001
            logger.debug("メモリの記録に失敗", exc_info=True)
        await asyncio.sleep(MEMORY_INTERVAL)


def memory_history(since: float = 0.0) -> list[dict]:
    return [{k: v for k, v in row.items() if k != "top"} for row in _memory if row["ts"] >= since]


# ---- 起動 ----------------------------------------------------------------
def start() -> list[asyncio.Task]:
    """lifespan から呼ぶ。2 回目以降は何もしない（TestClient が何度も起動する）。"""
    global _started
    tasks = [asyncio.create_task(_loop_ticker()), asyncio.create_task(_memory_loop())]
    if _started:
        return tasks
    _started = True
    handler = _EventHandler(level=logging.WARNING)
    logging.getLogger().addHandler(handler)
    threading.Thread(target=_stall_watcher, name="diag-stall-watch", daemon=True).start()
    store.prune()
    store.record("process_start", pid=os.getpid())
    return tasks
