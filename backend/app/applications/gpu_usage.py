"""管理アプリのprocess treeに限定したDRM fdinfo GPU使用量。

外部GPU CLIを監視周期ごとに起動せず、同じユーザーから読める固定
``/proc/<pid>/fdinfo`` だけを有界走査する。取得不能な項目はNoneへ縮退する。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

_MAX_FDS_PER_PROCESS = 512
_MAX_FDINFO_BYTES = 32 * 1024
_MIN_SAMPLE_INTERVAL = 0.25
_STALE_SECONDS = 60.0
_lock = Lock()


@dataclass(frozen=True)
class GpuClientSample:
    client_id: str
    engine_ns: int | None
    vram_bytes: int | None


@dataclass
class _PreviousSample:
    sampled_at: float
    engine_ns: int
    percent: float | None


_previous: dict[str, _PreviousSample] = {}


def _number(value: str) -> int | None:
    parts = value.split()
    try:
        number = int(parts[0])
    except (IndexError, ValueError):
        return None
    unit = parts[1] if len(parts) > 1 else "B"
    scale = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}.get(unit)
    return number * scale if scale is not None else None


def _duration_ns(value: str) -> int | None:
    parts = value.split()
    try:
        number = int(parts[0])
    except (IndexError, ValueError):
        return None
    unit = parts[1] if len(parts) > 1 else "ns"
    scale = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}.get(unit)
    return number * scale if scale is not None else None


def parse_fdinfo(text: str, *, fallback_id: str) -> GpuClientSample | None:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.startswith("drm-"):
            values[key] = value.strip()
    if "drm-driver" not in values and not any(
        key.startswith(("drm-engine-", "drm-memory-", "drm-resident-", "drm-total-"))
        for key in values
    ):
        return None
    engine_values = [
        parsed
        for key, value in values.items()
        if key.startswith("drm-engine-") and (parsed := _duration_ns(value)) is not None
    ]
    vram = None
    for key in ("drm-resident-vram", "drm-memory-vram", "drm-total-vram"):
        if key in values:
            vram = _number(values[key])
            if vram is not None:
                break
    identity = ":".join(filter(None, (
        values.get("drm-driver", "drm"), values.get("drm-pdev", ""),
        values.get("drm-client-id", fallback_id),
    )))
    return GpuClientSample(
        client_id=identity,
        engine_ns=sum(engine_values) if engine_values else None,
        vram_bytes=vram,
    )


def _read_bounded(path: Path) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        return os.read(descriptor, _MAX_FDINFO_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    finally:
        os.close(descriptor)


def collect(
    pids: set[int], *, proc_root: Path = Path("/proc"), sampled_at: float | None = None,
    scope_id: str | None = None,
) -> tuple[float | None, int | None]:
    """(GPU %, VRAM bytes)を返す。GPU clientがなければ0、未公開項目はNone。"""
    now = time.monotonic() if sampled_at is None else sampled_at
    clients: dict[str, GpuClientSample] = {}
    for pid in sorted(pid for pid in pids if pid > 0):
        directory = proc_root / str(pid) / "fdinfo"
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)[:_MAX_FDS_PER_PROCESS]
        except OSError:
            continue
        for entry in entries:
            text = _read_bounded(entry)
            if text is None:
                continue
            # drm-client-idがある通常ケースは親子process間で継承された同じ
            # file descriptionを一度だけ数える。ID非公開driverではpid/fdをfallbackにする。
            sample = parse_fdinfo(text, fallback_id=f"{pid}-{entry.name}")
            if sample is not None:
                clients[sample.client_id] = sample

    if not clients:
        with _lock:
            for key, previous in list(_previous.items()):
                if now - previous.sampled_at > _STALE_SECONDS:
                    _previous.pop(key, None)
        return 0.0, 0

    vram_values = [sample.vram_bytes for sample in clients.values() if sample.vram_bytes is not None]
    percentages: list[float] = []
    with _lock:
        for client_id, sample in clients.items():
            key = f"{scope_id or 'pids=' + ','.join(map(str, sorted(pids)))}:{client_id}"
            if sample.engine_ns is None:
                continue
            previous = _previous.get(key)
            percent = previous.percent if previous is not None else None
            if (
                previous is not None
                and now - previous.sampled_at >= _MIN_SAMPLE_INTERVAL
                and sample.engine_ns >= previous.engine_ns
            ):
                elapsed_ns = (now - previous.sampled_at) * 1_000_000_000
                percent = min(100.0, max(0.0, (sample.engine_ns - previous.engine_ns) / elapsed_ns * 100.0))
                _previous[key] = _PreviousSample(now, sample.engine_ns, percent)
            elif previous is None:
                _previous[key] = _PreviousSample(now, sample.engine_ns, None)
            if percent is not None:
                percentages.append(percent)
        for key, previous in list(_previous.items()):
            if now - previous.sampled_at > _STALE_SECONDS:
                _previous.pop(key, None)
    gpu_percent = min(100.0, sum(percentages)) if percentages else None
    return gpu_percent, sum(vram_values) if vram_values else None


def clear_cache() -> None:
    with _lock:
        _previous.clear()
