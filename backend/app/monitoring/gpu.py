"""GPU メトリクスプロバイダー。AMD (amd-smi → rocm-smi → sysfs) → NVIDIA (nvidia-smi) の順で自動検出。

取得できない項目は None（API 上は N/A）とし、失敗してもシステム全体を止めない。
"""
from __future__ import annotations

import glob
import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("control_deck.gpu")


def _run(argv: list[str], timeout: float = 5.0) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


class GpuSample(dict):
    """keys: name, utilization_percent, vram_used_bytes, vram_total_bytes,
    temperature_c, hotspot_c, power_watts, power_cap_watts, fan_percent, clock_mhz"""


class BaseProvider:
    name = "none"

    def sample(self) -> GpuSample | None:
        return None


class AmdSmiProvider(BaseProvider):
    name = "amd-smi"

    def sample(self) -> GpuSample | None:
        out = _run(["amd-smi", "metric", "--json"])
        if not out:
            return None
        try:
            data = json.loads(out)
            gpu = data[0] if isinstance(data, list) else data
            usage = gpu.get("usage", {})
            vram = gpu.get("mem_usage", {}) or gpu.get("vram", {})
            temp = gpu.get("temperature", {})
            power = gpu.get("power", {})

            def num(d, *keys):
                for k in keys:
                    v = d.get(k)
                    if isinstance(v, dict):
                        v = v.get("value")
                    if isinstance(v, (int, float)):
                        return float(v)
                return None

            vram_used = num(vram, "used_vram", "vram_used")
            vram_total = num(vram, "total_vram", "vram_total")
            return GpuSample(
                name="AMD GPU",
                utilization_percent=num(usage, "gfx_activity", "gfx_usage"),
                vram_used_bytes=vram_used * 1024 * 1024 if vram_used is not None else None,
                vram_total_bytes=vram_total * 1024 * 1024 if vram_total is not None else None,
                temperature_c=num(temp, "edge", "sensor_edge"),
                hotspot_c=num(temp, "hotspot", "sensor_hotspot", "junction"),
                power_watts=num(power, "socket_power", "average_socket_power"),
                power_cap_watts=num(power, "power_cap"),
                fan_percent=None,
                clock_mhz=None,
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            logger.debug("amd-smi parse failed: %s", e)
            return None


class RocmSmiProvider(BaseProvider):
    name = "rocm-smi"

    def sample(self) -> GpuSample | None:
        out = _run(["rocm-smi", "--showuse", "--showmemuse", "--showmeminfo", "vram", "--showtemp", "--showpower", "--json"])
        if not out:
            return None
        try:
            data = json.loads(out)
            card = next(iter(data.values())) if data else {}

            def fnum(*keys):
                for k in keys:
                    v = card.get(k)
                    if v is None:
                        continue
                    try:
                        return float(str(v).replace("%", ""))
                    except ValueError:
                        continue
                return None

            return GpuSample(
                name="AMD GPU",
                utilization_percent=fnum("GPU use (%)"),
                vram_used_bytes=fnum("VRAM Total Used Memory (B)"),
                vram_total_bytes=fnum("VRAM Total Memory (B)"),
                temperature_c=fnum("Temperature (Sensor edge) (C)"),
                hotspot_c=fnum("Temperature (Sensor junction) (C)"),
                power_watts=fnum(
                    "Average Graphics Package Power (W)",
                    "Current Socket Graphics Package Power (W)",
                ),
                power_cap_watts=fnum("Max Graphics Package Power (W)"),
                fan_percent=None,
                clock_mhz=None,
            )
        except (json.JSONDecodeError, StopIteration, TypeError) as e:
            logger.debug("rocm-smi parse failed: %s", e)
            return None


class SysfsAmdProvider(BaseProvider):
    """amdgpu の sysfs 直読み。外部ツール不要のフォールバック。"""

    name = "sysfs-amdgpu"

    def __init__(self) -> None:
        self.device: Path | None = None
        for card in sorted(glob.glob("/sys/class/drm/card[0-9]/device")):
            if (Path(card) / "gpu_busy_percent").exists():
                self.device = Path(card)
                break

    def _read_num(self, rel: str, scale: float = 1.0) -> float | None:
        if self.device is None:
            return None
        try:
            return float((self.device / rel).read_text().strip()) * scale
        except (OSError, ValueError):
            return None

    def _hwmon_num(self, name_prefix: str, scale: float = 1.0) -> float | None:
        if self.device is None:
            return None
        for hw in glob.glob(str(self.device / "hwmon/hwmon*")):
            p = Path(hw) / name_prefix
            if p.exists():
                try:
                    return float(p.read_text().strip()) * scale
                except (OSError, ValueError):
                    return None
        return None

    def sample(self) -> GpuSample | None:
        if self.device is None:
            return None
        return GpuSample(
            name="AMD GPU (sysfs)",
            utilization_percent=self._read_num("gpu_busy_percent"),
            vram_used_bytes=self._read_num("mem_info_vram_used"),
            vram_total_bytes=self._read_num("mem_info_vram_total"),
            temperature_c=self._hwmon_num("temp1_input", 0.001),
            hotspot_c=self._hwmon_num("temp2_input", 0.001),
            power_watts=self._hwmon_num("power1_average", 1e-6),
            power_cap_watts=self._hwmon_num("power1_cap", 1e-6),
            fan_percent=None,
            clock_mhz=None,
        )


class NvidiaSmiProvider(BaseProvider):
    name = "nvidia-smi"

    QUERY = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.gr"

    def sample(self) -> GpuSample | None:
        out = _run(["nvidia-smi", f"--query-gpu={self.QUERY}", "--format=csv,noheader,nounits"])
        if not out:
            return None
        try:
            parts = [p.strip() for p in out.strip().splitlines()[0].split(",")]

            def num(i, scale=1.0):
                try:
                    return float(parts[i]) * scale
                except (ValueError, IndexError):
                    return None

            return GpuSample(
                name=parts[0] if parts else "NVIDIA GPU",
                utilization_percent=num(1),
                vram_used_bytes=num(2, 1024 * 1024),
                vram_total_bytes=num(3, 1024 * 1024),
                temperature_c=num(4),
                hotspot_c=None,
                power_watts=num(5),
                power_cap_watts=num(6),
                fan_percent=num(7),
                clock_mhz=num(8),
            )
        except (IndexError, ValueError) as e:
            logger.debug("nvidia-smi parse failed: %s", e)
            return None


def detect_provider() -> BaseProvider:
    if shutil.which("amd-smi"):
        p = AmdSmiProvider()
        if p.sample() is not None:
            logger.info("GPU provider: amd-smi")
            return p
    if shutil.which("rocm-smi"):
        p = RocmSmiProvider()
        if p.sample() is not None:
            logger.info("GPU provider: rocm-smi")
            return p
    sysfs = SysfsAmdProvider()
    if sysfs.device is not None:
        logger.info("GPU provider: sysfs-amdgpu (%s)", sysfs.device)
        return sysfs
    if shutil.which("nvidia-smi"):
        p = NvidiaSmiProvider()
        if p.sample() is not None:
            logger.info("GPU provider: nvidia-smi")
            return p
    logger.info("GPU provider: none（GPU メトリクスは N/A になります）")
    return BaseProvider()
