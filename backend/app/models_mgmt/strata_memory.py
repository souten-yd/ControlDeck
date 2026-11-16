"""Read-only, bounded Strata memory estimates and allocation-log observations.

The Flash-Next coefficients follow Terminal 0.1.59 vram_fit.py. They must not
be applied to DeepSeek. File size is disk occupancy, never resident RAM.
"""
from __future__ import annotations

import re
import json
import os
from datetime import datetime
from itertools import islice
from pathlib import Path

import psutil

from app.models_mgmt import strata

GIB = 1024 ** 3
KV_BYTES = {"fp16": 2112, "int8": 1056, "q4_0": 576, "k8v4": 816}


def hardware() -> dict:
    ram = psutil.virtual_memory()
    cards = []
    for device in Path("/sys/class/drm").glob("card[0-9]*/device"):
        try:
            total = int((device / "mem_info_vram_total").read_text())
            used = int((device / "mem_info_vram_used").read_text())
            cards.append({"device": device.parent.name, "total": total, "available": max(0, total - used)})
        except (OSError, ValueError):
            continue
    # Capacities of different devices cannot be summed for a single GPU engine.
    gpu = max(cards, key=lambda card: card["total"], default=None)
    return {"ram_total": ram.total, "ram_available": ram.available, "gpu": gpu}


def _read_small(path: Path, limit: int = 65536) -> str:
    try:
        with path.open("rb") as stream:
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def model_info(cfg: dict) -> dict:
    from app.files import service as files
    disk = None
    path = cfg.get("model_path", "")
    if path:
        try:
            resolved = files.resolve(path)
            shard = re.fullmatch(r"(.+)-(\d{5})-of-(\d{5})\.gguf", resolved.name)
            if shard and 1 <= int(shard[3]) <= 999:
                parts = [files.resolve(str(resolved.with_name(f"{shard[1]}-{index:05d}-of-{shard[3]}.gguf")))
                         for index in range(1, int(shard[3]) + 1)]
                disk = sum(part.stat().st_size for part in parts)
            elif not shard:
                disk = resolved.stat().st_size
        except (OSError, ValueError, PermissionError):
            pass
    pack = strata._root().parents[1] / "models" / "strata" / "packs" / f"{cfg['family']}-{cfg['model']}"
    native = _read_small(pack / "native_experts.txt")
    if not native and cfg["family"] == "qwen":
        native = _read_small(pack.parent / cfg["model"] / "native_experts.txt")
    # A pack for another GGUF with the same quantization must not supply estimates.
    matching = bool(path and Path(path).name in native.split("\n", 1)[0])
    match = re.search(r"n_expert \d+, total (\d+)", native) if matching else None
    return {"disk_bytes": disk, "expert_bytes": int(match[1]) if match else None}


def estimate(cfg: dict, machine: dict, info: dict) -> dict:
    ctx = int(cfg["context"].removesuffix("k")) * 1024
    parallel = int(cfg.get("parallel", 1))
    result = {"context_tokens": ctx, "parallel": parallel, "model": info,
              "hardware": machine, "supported": cfg["family"] in ("qwen", "unsloth")}
    if not result["supported"]:
        return {**result, "note": "このモデル系列のKV係数は未対応です"}
    kv_per_token = 13 * KV_BYTES[cfg.get("kv", "int8")]
    resident = cfg.get("kv_resident")
    # Auto is chosen by the engine at launch. Show a range, never silently assume 32K.
    windows = [min(ctx, resident)] if resident else [min(ctx, 4096), ctx]
    sessions = parallel + 1 if parallel >= 2 else 1
    def host_kv(tokens: int, count: int = sessions) -> int:
        return 0 if resident is not None and resident >= tokens else tokens * kv_per_token * count
    ram_kv = host_kv(ctx)
    def gpu_kv(window: int) -> int:
        return int(sessions * (121 * 1024**2 + window * kv_per_token * 1.054
                   + (ctx * 1400 if window < ctx else 0)))
    keep = float(4 if cfg.get("ram_keep_gib") is None else cfg["ram_keep_gib"]) * GIB
    # Expert arena + pinned KV + 4 GiB for dense, runtime and temporary buffers.
    experts = info.get("expert_bytes")
    budget = max(0, machine["ram_available"] - keep - 4 * GIB)
    max_ctx = None if experts is None else max(
        (tokens for tokens in range(4096, 1048577, 1024) if experts + host_kv(tokens) <= budget), default=0)
    gpu = machine.get("gpu")
    # Device budget leaves 6 GiB for dense/VLM/drafter/workspace and 2 GiB
    # minimum expert cache. This is a planning allowance, not a fitted engine value.
    device_budget = max(0, gpu["available"] - 8 * GIB) if gpu else None
    vram_ok = None if device_budget is None else gpu_kv(max(windows)) <= device_budget
    max_parallel = None
    if experts is not None and resident and device_budget is not None:
        max_parallel = 0
        for count in range(1, 9):
            count_sessions = count + 1 if count >= 2 else 1
            if (experts + host_kv(ctx, count_sessions) <= budget
                    and gpu_kv(min(ctx, resident)) / sessions * count_sessions <= device_budget):
                max_parallel = count
    return {**result, "vram_budget_fits": vram_ok, "full_ram_parallel": max_parallel,
            "kv_ram_bytes": ram_kv, "kv_ram_min_bytes": 0 if resident is None else ram_kv, "kv_vram_min_bytes": gpu_kv(min(windows)),
            "kv_vram_max_bytes": gpu_kv(max(windows)), "sessions": sessions,
            "ram_keep_bytes": keep, "full_ram_context_tokens": max_ctx,
            "full_ram_estimate_bytes": None if experts is None else experts + ram_kv + 4 * GIB,
            "note": "概算。現在の空きRAMを基準に全エキスパートをRAMへ保持する目安。KVは会話ごとに独立し、並列時はエンジンの主セッションも加算。KV保持長が自動の場合はRAMの上限とVRAMの範囲を表示。自動時のRAM余裕は概算上4GiBを見込みます。VLM・投機モデル・一時領域で変動し、起動可否や速度は保証しません。GPU自動選択は最大VRAMのカードを仮定。"}


def allocations(text: str, parallel: int) -> list[dict]:
    # Logs are append-only. Ignore all allocations before the latest engine start.
    text = text.rsplit("engine started:", 1)[-1]
    rows = []
    def add(category: str, location: str, amount: float | None, source: str = "起動ログ") -> None:
        rows.append({"category": category, "location": location, "bytes": None if amount is None else int(amount * GIB), "source": source})
    match = re.search(r"K/V in ([\d.]+) GiB of pinned RAM", text)
    add("KVキャッシュ（全セッション）", "RAM", float(match[1]) * (parallel + 1 if parallel >= 2 else 1) if match else None,
        "起動ログの1セッション量 × セッション数")
    match = re.search(r"--batch: (\d+) slot sessions.*?\(([\d.]+) GiB each\)", text)
    add("KV・会話セッション（主セッション含む）", "VRAM", (int(match[1]) + 1) * float(match[2]) if match else None,
        "起動ログのセッション量 × セッション数（丸め誤差あり）")
    match = re.search(r"loaded ([\d.]+) GiB at", text)
    if not match:
        match = re.search(r"allocating ([\d.]+) GiB (?:page-locked|pageable resident) cache complement", text)
    add("モデル本体・エキスパート", "RAM", float(match[1]) if match else None)
    match = re.search(r"expert cache \d+ slots, ([\d.]+) GiB of VRAM", text)
    add("モデル本体・エキスパートキャッシュ", "VRAM", float(match[1]) if match else None)
    match = re.search(r"draft layer loaded, ([\d.]+) MiB of VRAM", text)
    add("投機デコード・補助モデル（MTP等）", "VRAM", float(match[1]) / 1024 if match else None)
    for category in ("VLM・画像エンコーダー", "n-gram・トークン履歴", "モデル本体・Dense／その他バッファ"):
        for location in ("RAM", "VRAM"):
            add(category, location, None)
    add("投機デコード・補助モデル（MTP等）", "RAM", None)
    return rows


def process_usage(pid: int | None) -> list[dict]:
    """Observed process usage is separate from component allocation logs.

    A process can contain GGUF, KV and draft buffers; never split its RSS among
    those components. DRM client IDs avoid counting duplicated descriptors twice.
    """
    if not pid:
        return []
    try:
        parent = psutil.Process(pid)
        processes = [parent, *parent.children(recursive=True)]
    except (psutil.Error, OSError):
        return []
    from app.applications.gpu_usage import parse_fdinfo
    rows = []
    clients = set()
    for process in processes:
        try:
            command = process.cmdline()
            binary = Path(command[0]).name if command else ""
            category = "VLM・画像エンコーダー" if any("strata-vision" in part for part in command) else "モデルエンジン（GGUF・KV・投機を含む）" if binary == "strata" else "サーバー・補助プロセス"
            ram = process.memory_info().rss
            device = None
            for fd in islice(Path(f"/proc/{process.pid}/fdinfo").glob("*"), 512):
                sample = parse_fdinfo(_read_small(fd, 32768), fallback_id=f"{process.pid}-{fd.name}")
                if sample is None or sample.client_id in clients:
                    continue
                clients.add(sample.client_id)
                if sample.vram_bytes is not None:
                    device = (device or 0) + sample.vram_bytes
            rows.append({"category": category, "ram_bytes": ram, "vram_bytes": device})
        except (psutil.Error, OSError):
            continue
    return rows


def owner_pid(alias: str) -> int | None:
    """Read only the owned host identity; never expose its private control token."""
    root = (strata._home() / ".local/state/strata/runtime").resolve()
    profile = f"launch-cd-{alias}"
    path = (root / f"{profile}.json").resolve()
    if not path.is_relative_to(root):
        return None
    try:
        state = json.loads(_read_small(path, 16384))
        if not isinstance(state, dict) or state.get("profile") != profile:
            return None
        pid = state.get("host_pid")
        if type(pid) is not int or pid <= 0:
            return None
        process = psutil.Process(pid)
        command = process.cmdline()
        if process.uids().real != os.getuid() or profile not in command or "strata_control.runtime" not in command:
            return None
        return pid
    except (ValueError, psutil.Error, OSError):
        return None


def observed(alias: str, cfg: dict) -> dict:
    from app.models_mgmt import strata_gateway
    item = strata_gateway.get_instance(alias)
    active = item["runtime_status"] in {"READY", "STARTING", "LOADING"}
    pid = owner_pid(alias) if active else None
    text = ""
    if active and pid:
        root = (strata._home() / ".local/state/strata/launch").resolve()
        path = (root / f"cd-{alias}.engine.log").resolve()
        if not path.is_relative_to(root):
            return {"active": active, "status": item["runtime_status"], "hardware": hardware(), "rows": [], "note": "ログを読み込めません"}
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 2 * 1024**2))
                text = stream.read().decode("utf-8", errors="replace")
            if pid:
                starts = re.findall(r"\[strata\] (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) engine started:", text)
                if not starts or datetime.strptime(starts[-1], "%Y-%m-%d %H:%M:%S").timestamp() < psutil.Process(pid).create_time() - 5:
                    text = ""
        except (OSError, ValueError, psutil.Error):
            text = ""
    return {"active": active, "status": item["runtime_status"], "hardware": hardware(),
            "rows": allocations(text, cfg.get("parallel", 1)) if active else [],
            "processes": process_usage(pid) if active and pid else [],
            "note": "起動ログは確保量、プロセスは現在のRSSとDRMのVRAM量です。RSSは共有ページを含むため合算不可。プロセスと内訳も重複するため合算不可。不明な内訳は合計に含めません。GGUFのディスク容量とRAM常駐量は別です。"}
