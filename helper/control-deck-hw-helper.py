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



def fan_curve_path(device: Path) -> Path:
    """Only the GPU's exact amdgpu OD node; never accept a path from the caller."""
    curve = device / "gpu_od" / "fan_ctrl" / "fan_curve"
    resolved = curve.resolve()
    if not curve.is_file() or curve.is_symlink() or resolved != curve:
        fail("amdgpu fan_curveが利用できません（kernel/OverDrive対応を確認してください）")
    return curve


def fan_curve_range(text: str) -> tuple[int, int, int, int, int]:
    """Parse firmware-advertised limits; zero/invalid ranges MUST NOT be used."""
    temps = re.search(r"FAN_CURVE\(hotspot temp\):\s*(\d+)\s*C\s+(\d+)\s*C", text, re.I)
    speeds = re.search(r"FAN_CURVE\(fan speed\):\s*(\d+)\s*%\s+(\d+)\s*%", text, re.I)
    points = re.findall(r"^\s*(\d+):\s*\d+\s*C\s+\d+\s*%", text, re.M)
    if not temps or not speeds or points != [str(i) for i in range(len(points))] or len(points) != 5:
        fail("fan_curveのOD_RANGE/5-point形式を検証できません")
    t0, t1 = map(int, temps.groups())
    p0, p1 = map(int, speeds.groups())
    if t0 >= t1 or t1 - t0 < 5 or p0 >= p1 or p1 < 90 or p0 > 40 or p1 > 100:
        fail("fan_curveの温度/PWM範囲が安全に利用できません")
    return t0, t1, p0, p1, len(points)


def control_fan_curve(action: str, bdf: str, curve_text: str = "") -> None:
    if action not in ("apply", "reset"):
        fail("不正なfan control操作です")
    device = device_path(bdf)
    curve = fan_curve_path(device)
    if action == "reset":
        # Restore the fan curve FIRST: it is what actually drives the fan, and
        # it must not depend on optional nodes. Then restore native zero-RPM
        # overrides, but only where the firmware advertises them (OD_RANGE).
        # R9700 exposes an empty fan_zero_rpm_enable whose writes fail with
        # EIO; treating that as a reset failure left the custom curve active.
        try:
            curve.write_text("r", encoding="ascii")
            curve.write_text("c", encoding="ascii")
        except OSError as exc:
            fail(f"ファン自動制御への復帰に失敗しました: {exc}")
        for name in ("fan_zero_rpm_enable", "fan_zero_rpm_stop_temperature"):
            node = curve.parent / name
            if not node.is_file():
                continue
            if node.is_symlink() or node.resolve() != node:
                fail("不正なzero RPM node")
            try:
                advertised = "OD_RANGE:" in node.read_text(encoding="ascii")
            except OSError:
                advertised = False
            if not advertised:
                continue  # unsupported by firmware: nothing was ever written
            try:
                node.write_text("r", encoding="ascii")
                node.write_text("c", encoding="ascii")
            except OSError as exc:
                fail(f"Zero RPM設定の自動復帰に失敗しました: {exc}")
        print(json.dumps({"ok": True, "mode": "auto", "bdf": bdf.lower()}))
        return

    try:
        current = curve.read_text(encoding="ascii")
    except OSError as exc:
        fail(f"fan_curveを読み取れません: {exc}")
    tmin, tmax, pmin, pmax, count = fan_curve_range(current)
    if not re.fullmatch(r"(?:\d{1,3}:\d{1,3},){4}\d{1,3}:\d{1,3}", curve_text):
        fail("fan_curve指定形式が不正です")
    points = [tuple(map(int, item.split(":"))) for item in curve_text.split(",")]
    if len(points) != count or any(
        not (tmin <= t <= tmax and max(30, pmin) <= p <= pmax)
        for t, p in points
    ) or any(
        points[i][0] <= points[i - 1][0] or points[i][1] < points[i - 1][1]
        for i in range(1, count)
    ) or points[-1][1] != pmax:
        fail("fan_curveが機器上限/単調増加/最大PWMの安全条件を満たしません")
    try:
        for i, (temperature, pwm) in enumerate(points):
            curve.write_text(f"{i} {temperature} {pwm}", encoding="ascii")
        curve.write_text("c", encoding="ascii")
    except OSError as exc:
        # An incomplete curve must never remain staged.
        try:
            curve.write_text("r", encoding="ascii")
            curve.write_text("c", encoding="ascii")
        except OSError:
            pass
        fail(f"fan_curveの適用に失敗しました（auto復帰を試行）: {exc}")
    print(json.dumps({"ok": True, "mode": "curve", "bdf": bdf.lower()}))



def zero_rpm_nodes(device: Path) -> tuple[Path, Path, int]:
    """Only hardware-advertised native zero-RPM controls, with safe stop temp."""
    directory = device / "gpu_od" / "fan_ctrl"
    enable = directory / "fan_zero_rpm_enable"
    stop = directory / "fan_zero_rpm_stop_temperature"
    for node in (enable, stop):
        if not node.is_file() or node.is_symlink() or node.resolve() != node:
            fail("GPU native zero RPM設定はサポートされていません")
    try:
        content = stop.read_text(encoding="ascii")
        on_info = enable.read_text(encoding="ascii")
    except OSError as exc:
        fail(f"Zero RPMの安全温度範囲を読み取れません: {exc}")
    if not re.search(r"(?m)^\s*(?:FAN_)?ZERO_RPM_ENABLE\s*:\s*0\s+1\s*$",
                     on_info.split("OD_RANGE:", 1)[-1] if "OD_RANGE:" in on_info else ""):
        fail("Zero RPM切替をファームウェアが許可していません")
    # Some AMD drivers prefix the name with FAN_, others do not.
    match = re.search(
        r"(?im)^\s*(?:FAN_)?ZERO_RPM_STOP_(?:TEMP|TEMPERATURE)\s*:\s*(\d+)\s*C?\s+(\d+)\s*C?\s*$",
        content.split("OD_RANGE:", 1)[-1] if "OD_RANGE:" in content else "",
    )
    if not match:
        fail("Zero RPM停止温度のOD_RANGEが取得できません")
    minimum, maximum = map(int, match.groups())
    # R9700/RDNA4では下限が50°C以上のGPUもある。
    # 固定40°Cは非対応になるため実機OD_RANGEから50〜55°Cを選ぶ。
    stop_value = max(50, minimum)
    if minimum < 25 or stop_value > 55 or maximum < stop_value or minimum >= maximum:
        fail("安全な55°C以下のファン停止温度を設定できません")
    return enable, stop, stop_value


def control_zero_rpm(action: str, bdf: str) -> None:
    """Enable/disable ONLY native PMFW stop feature, not raw PWM=0."""
    if action not in ("allow", "deny"):
        fail("不正なZero RPM操作です")
    device = device_path(bdf)
    if action == "allow":
        enable, stop, stop_temp = zero_rpm_nodes(device)
    else:
        # On a fault, disabling zero RPM must not depend on the stop-temp
        # node still existing or advertising the same OD_RANGE.
        enable = device / "gpu_od/fan_ctrl/fan_zero_rpm_enable"
        if not enable.is_file() or enable.is_symlink() or enable.resolve() != enable:
            fail("Zero RPM無効化の制御ノードが利用できません")
    try:
        if action == "allow":
            # Install a conservative firmware fallback before enabling stop.
            stop.write_text(str(stop_temp), encoding="ascii")
            stop.write_text("c", encoding="ascii")
            enable.write_text("1", encoding="ascii")
            enable.write_text("c", encoding="ascii")
        else:
            enable.write_text("0", encoding="ascii")
            enable.write_text("c", encoding="ascii")
    except OSError as exc:
        if action == "allow":
            try:
                enable.write_text("0", encoding="ascii")
                enable.write_text("c", encoding="ascii")
            except OSError:
                pass
        fail(f"Zero RPM {action}に失敗しました: {exc}")
    print(json.dumps({"ok": True, "action": action, "bdf": bdf.lower()}))


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
    if len(sys.argv) == 4 and sys.argv[1] == "fan-zero-rpm":
        control_zero_rpm(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) == 5 and sys.argv[1] == "fan-curve" and sys.argv[2] == "apply":
        control_fan_curve("apply", sys.argv[3], sys.argv[4])
        return
    if len(sys.argv) == 4 and sys.argv[1] == "fan-curve" and sys.argv[2] == "reset":
        control_fan_curve("reset", sys.argv[3])
        return
    fail("usage: control-deck-hw-helper apply-amd ... | fan-curve apply|reset ... | fan-zero-rpm allow|deny BDF | system-service ACTION ID")


if __name__ == "__main__":
    main()
