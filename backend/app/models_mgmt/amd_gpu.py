"""AMD dGPUの電力・VRAM周波数能力検出と、最小特権helper経由の適用。"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
HELPER = Path("/usr/local/libexec/control-deck-hw-helper")
AMD_SMI = Path("/usr/bin/amd-smi")
_cache_lock = threading.Lock()
_applied_fingerprint = ""
_applied_at = 0.0


def preflight_argvs(profile: object) -> list[list[str]]:
    """systemd ExecStartPreと通常preflightで共有する検証済み固定argv列。"""
    if not bool(getattr(profile, "enabled", False)):
        return []
    caps = capabilities()
    if caps is None:
        return []
    watts = str(int(getattr(profile, "power_limit_watts", 0)))
    memory_mode = str(getattr(profile, "memory_clock_mode", "auto"))
    memory_level = int(getattr(profile, "memory_clock_level", 0))
    core_mode = str(getattr(profile, "core_clock_mode", "auto"))
    core_level = int(getattr(profile, "core_clock_level", 0))
    if HELPER.is_file():
        return [["sudo", "-n", str(HELPER), "apply-amd", caps["bdf"], watts,
                 memory_mode, str(memory_level), core_mode, str(core_level)]]
    if not AMD_SMI.is_file():
        return []
    prefix = ["sudo", "-n", str(AMD_SMI)]
    commands = [prefix + ["set", "-g", caps["bdf"], "-o", "ppt0", watts],
                prefix + ["set", "-g", caps["bdf"], "-l", "AUTO"]]
    if memory_mode != "auto" or core_mode != "auto":
        commands.append(prefix + ["set", "-g", caps["bdf"], "-l", "MANUAL"])
    if memory_mode in ("minimum", "limit"):
        selected = 0 if memory_mode == "minimum" else memory_level
        commands.append(prefix + ["set", "-g", caps["bdf"], "-c", "mclk",
                                  *(str(i) for i in range(selected + 1))])
    if core_mode == "limit":
        commands.append(prefix + ["set", "-g", caps["bdf"], "-c", "sclk",
                                  *(str(i) for i in range(core_level + 1))])
    return commands


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _clock_levels(device: Path, name: str) -> list[dict]:
    try:
        text = (device / name).read_text(encoding="ascii")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+):\s*(\d+)Mhz\b(.*)$", line, re.IGNORECASE)
        if match:
            out.append({"level": int(match.group(1)), "mhz": int(match.group(2)),
                        "current": "*" in match.group(3)})
    return out


def capabilities() -> dict | None:
    """電力capを持つ最大VRAM AMD dGPUを選択する。非対応環境はNone。"""
    candidates = []
    for raw in sorted(Path("/sys/class/drm").glob("card*/device")):
        try:
            device = raw.resolve()
            if (device / "vendor").read_text(encoding="ascii").strip().lower() != "0x1002":
                continue
        except OSError:
            continue
        for hwmon_link in raw.glob("hwmon/hwmon*"):
            hwmon = hwmon_link.resolve()
            try:
                if (hwmon / "name").read_text(encoding="ascii").strip() != "amdgpu":
                    continue
            except OSError:
                continue
            minimum = _read_int(hwmon / "power1_cap_min")
            maximum = _read_int(hwmon / "power1_cap_max")
            current = _read_int(hwmon / "power1_cap")
            default = _read_int(hwmon / "power1_cap_default")
            if None in (minimum, maximum, current):
                continue
            vram = _read_int(device / "mem_info_vram_total") or 0
            candidates.append((vram, device, minimum, maximum, current, default))
    if not candidates:
        return None
    vram, device, minimum, maximum, current, default = max(candidates, key=lambda item: item[0])
    levels = _clock_levels(device, "pp_dpm_mclk")
    core_levels = _clock_levels(device, "pp_dpm_sclk")
    try:
        perf = (device / "power_dpm_force_performance_level").read_text(encoding="ascii").strip()
    except OSError:
        perf = ""
    return {
        "bdf": device.name, "vram_bytes": vram,
        "power": {"current_watts": round(current / 1_000_000),
                  "min_watts": round(minimum / 1_000_000),
                  "max_watts": round(maximum / 1_000_000),
                  "default_watts": round((default or maximum) / 1_000_000)},
        "memory": {"supported": len(levels) > 1 and (device / "power_dpm_force_performance_level").exists(),
                   "levels": levels, "performance_level": perf},
        "core": {"supported": len(core_levels) > 1 and (device / "power_dpm_force_performance_level").exists(),
                 "levels": core_levels},
        "helper_installed": HELPER.is_file() or AMD_SMI.is_file(),
        "control_backend": "control-deck-helper" if HELPER.is_file() else "amd-smi" if AMD_SMI.is_file() else "unavailable",
    }


def apply_profile(profile: object, *, force: bool = False) -> dict:
    """RuntimePolicyのAMD設定を適用。無効/非対応なら安全にno-op。"""
    global _applied_at, _applied_fingerprint
    enabled = bool(getattr(profile, "enabled", False))
    if not enabled:
        return {"applied": False, "reason": "disabled"}
    caps = capabilities()
    if caps is None:
        return {"applied": False, "reason": "unsupported"}
    watts = int(getattr(profile, "power_limit_watts", 0))
    mode = str(getattr(profile, "memory_clock_mode", "auto"))
    level = int(getattr(profile, "memory_clock_level", 0))
    core_mode = str(getattr(profile, "core_clock_mode", "auto"))
    core_level = int(getattr(profile, "core_clock_level", 0))
    pwr = caps["power"]
    if watts < pwr["min_watts"] or watts > pwr["max_watts"]:
        raise RuntimeError(f"AMD GPU電力上限は{pwr['min_watts']}〜{pwr['max_watts']}Wで指定してください")
    levels = caps["memory"]["levels"]
    if mode == "limit" and not any(item["level"] == level for item in levels):
        raise RuntimeError("保存されたVRAM周波数levelは現在のGPUで利用できません")
    if core_mode == "limit" and not any(item["level"] == core_level for item in caps["core"]["levels"]):
        raise RuntimeError("保存されたGPUコア周波数levelは現在のGPUで利用できません")
    fingerprint = f"{caps['bdf']}:{watts}:{mode}:{level}:{core_mode}:{core_level}"
    now = time.monotonic()
    with _cache_lock:
        if not force and fingerprint == _applied_fingerprint and now - _applied_at < 300:
            return {"applied": True, "cached": True, "fingerprint": fingerprint}
        commands = preflight_argvs(profile)
        if not commands:
            raise RuntimeError("AMD GPU設定helperの対象GPUを解決できません")
        for argv in commands:
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f"AMD GPU設定helperを実行できません: {exc}") from exc
            if result.returncode != 0:
                detail = result.stderr.strip()[:500] or result.stdout.strip()[:500] or "helper returned failure"
                raise RuntimeError(f"AMD GPU設定を適用できません: {detail}")
        _applied_fingerprint = fingerprint
        _applied_at = now
    logger.info("AMD GPU profile applied: bdf=%s power=%dW memory=%s/%d core=%s/%d",
                caps["bdf"], watts, mode, level, core_mode, core_level)
    return {"applied": True, "cached": False, "fingerprint": fingerprint}
