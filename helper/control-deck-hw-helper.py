#!/usr/bin/env python3
"""Control Deck minimal privileged helper.

Root権限が必要なAMD GPU sysfs属性と、root所有catalogで固定したsystemd
serviceだけを、固定コマンドと厳格な値検証で操作する。任意path／unit／commandは受け付けない。
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

PCI_ROOT = Path("/sys/bus/pci/devices").resolve()
SYS_DEVICES = Path("/sys/devices").resolve()
BDF_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
SERVICE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
CATALOG_PATH = Path("/etc/control-deck/system-services.json")
SYSTEMCTL = Path("/usr/bin/systemctl")
SYSTEM_ACTIONS = {"start", "stop", "restart"}
MAX_CATALOG_BYTES = 64 * 1024


def fail(message: str) -> "NoReturn":
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(2)


def read_int(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        fail(f"必要なGPU属性を読み取れません: {path.name}: {exc}")


def device_path(bdf: str) -> Path:
    if not BDF_RE.fullmatch(bdf):
        fail("不正なPCI BDFです")
    link = PCI_ROOT / bdf.lower()
    path = link.resolve()
    if not link.is_dir() or SYS_DEVICES not in path.parents or path.name != bdf.lower():
        fail("GPUデバイスが見つかりません")
    if path.joinpath("vendor").read_text(encoding="ascii").strip().lower() != "0x1002":
        fail("AMD GPUではありません")
    return path


def hwmon_path(device: Path) -> Path:
    for item in sorted(device.glob("hwmon/hwmon*")):
        resolved = item.resolve()
        if device not in resolved.parents:
            continue
        try:
            if resolved.joinpath("name").read_text(encoding="ascii").strip() == "amdgpu":
                return resolved
        except OSError:
            continue
    fail("AMD GPU hwmonが見つかりません")


def clock_levels(device: Path, attribute: str) -> list[int]:
    try:
        text = device.joinpath(attribute).read_text(encoding="ascii")
    except OSError as exc:
        fail(f"VRAM周波数レベルを読み取れません: {exc}")
    levels: list[int] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+):\s*(\d+)Mhz\b", line, re.IGNORECASE)
        if match and int(match.group(1)) == len(levels):
            levels.append(int(match.group(2)))
    if not levels:
        fail("VRAM周波数レベルがありません")
    return levels


def apply_amd(bdf: str, watts_text: str, memory_mode: str, memory_level_text: str,
              core_mode: str, core_level_text: str) -> None:
    device = device_path(bdf)
    try:
        watts = int(watts_text)
        memory_level = int(memory_level_text)
        core_level = int(core_level_text)
    except ValueError:
        fail("電力またはDPM levelが整数ではありません")

    if watts > 0:
        hwmon = hwmon_path(device)
        cap = hwmon / "power1_cap"
        minimum = read_int(hwmon / "power1_cap_min")
        maximum = read_int(hwmon / "power1_cap_max")
        requested = watts * 1_000_000
        if requested < minimum or requested > maximum:
            fail(f"電力上限は{minimum // 1_000_000}〜{maximum // 1_000_000}Wの範囲外です")
        cap.write_text(str(requested), encoding="ascii")
        if read_int(cap) != requested:
            fail("電力上限を適用できませんでした")

    perf = device / "power_dpm_force_performance_level"
    mclk = device / "pp_dpm_mclk"
    if memory_mode == "auto" and core_mode == "auto":
        perf.write_text("auto", encoding="ascii")
    else:
        # 以前の手動制約を一旦解除し、今回指定したdomainだけを制限する。
        perf.write_text("auto", encoding="ascii")
        perf.write_text("manual", encoding="ascii")
        if memory_mode in ("minimum", "limit"):
            levels = clock_levels(device, "pp_dpm_mclk")
            selected = 0 if memory_mode == "minimum" else memory_level
            if selected < 0 or selected >= len(levels):
                fail("VRAM周波数levelが実機の範囲外です")
            # 0..selectedを許可し、idle時は最低level、負荷時も選択値を超えない。
            mclk.write_text(" ".join(str(i) for i in range(selected + 1)), encoding="ascii")
        elif memory_mode != "auto":
            fail("不正なVRAM周波数モードです")
        if core_mode == "limit":
            levels = clock_levels(device, "pp_dpm_sclk")
            if core_level < 0 or core_level >= len(levels):
                fail("GPUコア周波数levelが実機の範囲外です")
            device.joinpath("pp_dpm_sclk").write_text(
                " ".join(str(i) for i in range(core_level + 1)), encoding="ascii")
        elif core_mode != "auto":
            fail("不正なGPUコア周波数モードです")

    print(json.dumps({"ok": True, "bdf": bdf.lower(), "watts": watts,
                      "memory_mode": memory_mode, "memory_level": memory_level,
                      "core_mode": core_mode, "core_level": core_level}))


def system_service_catalog() -> dict:
    try:
        descriptor = os.open(CATALOG_PATH, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        fail("system service catalogを読み取れません")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            fail("system service catalogの所有権または権限が不正です")
        if info.st_size > MAX_CATALOG_BYTES:
            fail("system service catalogが大きすぎます")
        data = os.read(descriptor, MAX_CATALOG_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        catalog = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("system service catalogを解析できません")
    if not isinstance(catalog, dict) or catalog.get("version") != 1 or not isinstance(catalog.get("services"), dict):
        fail("system service catalogの形式が不正です")
    if len(catalog["services"]) > 64:
        fail("system service catalogの件数が上限を超えています")
    return catalog["services"]


def control_system_service(action: str, service_id: str) -> None:
    if action not in SYSTEM_ACTIONS or not SERVICE_ID_RE.fullmatch(service_id):
        fail("不正なsystem service操作です")
    definition = system_service_catalog().get(service_id)
    if not isinstance(definition, dict):
        fail("system serviceが許可されていません")
    unit, actions = definition.get("unit"), definition.get("actions")
    if (
        not isinstance(unit, str) or not UNIT_RE.fullmatch(unit)
        or not isinstance(actions, list) or action not in actions
        or len(actions) > 3 or len(set(actions)) != len(actions)
        or any(item not in SYSTEM_ACTIONS for item in actions)
    ):
        fail("system service定義または操作が許可されていません")
    if not SYSTEMCTL.is_file() or SYSTEMCTL.is_symlink():
        fail("固定systemctlを利用できません")
    try:
        if action == "start":
            subprocess.run(
                [str(SYSTEMCTL), "--no-ask-password", "reset-failed", "--", unit],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False,
            )
        result = subprocess.run(
            [str(SYSTEMCTL), "--no-ask-password", action, "--", unit],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail("systemctlを実行できません")
    if result.returncode != 0:
        fail(f"systemctl {action}が失敗しました（終了コード {result.returncode}）")
    print(json.dumps({"ok": True, "service_id": service_id, "action": action}))


def main() -> None:
    if len(sys.argv) == 8 and sys.argv[1] == "apply-amd":
        apply_amd(*sys.argv[2:])
        return
    if len(sys.argv) == 4 and sys.argv[1] == "system-service":
        control_system_service(sys.argv[2], sys.argv[3])
        return
    fail("usage: control-deck-hw-helper apply-amd ... | system-service ACTION ID")


if __name__ == "__main__":
    main()
