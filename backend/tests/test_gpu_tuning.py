"""R9700 のコア周波数・電圧オフセット（疑似 sysfs。実機には書かない）。

根拠: 2026-11-18 利用者の依頼。対象の選別、符号と単位、片方だけ未対応、失敗時の復元、他の設定に書かないことを確かめる。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.monitoring import gpu_tuning as gt
from tests.conftest import CSRF_HEADERS

OD = """OD_SCLK_OFFSET:
{core}Mhz
OD_MCLK:
0: 97Mhz
1: 1259MHz
OD_RANGE:
SCLK_OFFSET:    -500Mhz       1000Mhz
MCLK:      97Mhz       1500Mhz
VDDGFX_OFFSET:    -200mv          0mv
OD_VDDGFX_OFFSET:
{voltage}mV
"""
IGPU = "OD_SCLK:\n0:        600Mhz\n1:       2200Mhz\nOD_RANGE:\nSCLK:     600Mhz       2200Mhz\n"


def load_helper():
    path = Path(__file__).parents[2] / "helper" / "control-deck-hw-helper.py"
    spec = importlib.util.spec_from_file_location("control_deck_hw_helper_od", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGpu:
    """ドライバの代わり: s / vo は下書き、c で確定。送られたコマンドを全部覚える。"""

    def __init__(self, root: Path, voltage_supported: bool = True):
        self.commands: list[str] = []
        self.core, self.voltage = 0, 0
        self.draft = {}
        self.fail_on: str | None = None
        self.voltage_supported = voltage_supported
        self.sys = root / "sys"
        self.device = self.sys / "pci0000:00" / "0000:03:00.0"
        self.device.mkdir(parents=True)
        for name, value in (("vendor", "0x1002"), ("device", "0x7551"), ("class", "0x030000"),
                            ("subsystem_vendor", "0x1eae"), ("subsystem_device", "0x9801")):
            (self.device / name).write_text(value + "\n")
        self.node = self.device / "pp_od_clk_voltage"
        self.sync()
        igpu = self.sys / "pci0000:00" / "0000:71:00.0"
        igpu.mkdir()
        for name, value in (("vendor", "0x1002"), ("device", "0x13c0"), ("class", "0x030000")):
            (igpu / name).write_text(value + "\n")
        (igpu / "pp_od_clk_voltage").write_text(IGPU)
        self.igpu = igpu
        self.pci = root / "pci"
        self.pci.mkdir()
        (self.pci / "0000:03:00.0").symlink_to(self.device)
        (self.pci / "0000:71:00.0").symlink_to(igpu)

    def text(self) -> str:
        text = OD.format(core=self.core, voltage=self.voltage)
        if not self.voltage_supported:
            text = text.replace("VDDGFX_OFFSET:    -200mv          0mv\n", "").replace(f"OD_VDDGFX_OFFSET:\n{self.voltage}mV\n", "")
        return text

    def sync(self) -> None:
        self.node.write_text(self.text())

    def write(self, _node: Path, command: str) -> None:
        self.commands.append(command)
        if self.fail_on and command.startswith(self.fail_on):
            self.fail_on = None
            raise OSError(22, "Invalid argument")
        kind, *rest = command.split()
        if kind == "s":
            self.draft["core"] = int(rest[0])
        elif kind == "vo":
            self.draft["voltage"] = int(rest[0])
        elif kind == "c":
            self.core = self.draft.get("core", self.core)
            self.voltage = self.draft.get("voltage", self.voltage)
            self.draft = {}
        else:
            raise AssertionError(f"送ってはいけないコマンド: {command}")
        self.sync()


@pytest.fixture
def gpu(tmp_path, monkeypatch):
    fake = FakeGpu(tmp_path)
    helper = load_helper()
    helper.PCI_ROOT = fake.pci.resolve()
    helper.SYS_DEVICES = fake.sys.resolve()
    helper.od_write = fake.write
    monkeypatch.setattr(gt, "PCI_ROOT", fake.pci)
    monkeypatch.setattr(gt, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gt, "helper_ready", lambda: True)
    boot = tmp_path / "boot"
    boot.write_text("boot-1\n")
    monkeypatch.setattr(gt, "BOOT_ID", boot)

    def run(bdf, core, voltage):
        helper.control_gpu_od(bdf, "keep" if core is None else str(core), "keep" if voltage is None else str(voltage))

    def wrapped(bdf, core, voltage):
        try:
            run(bdf, core, voltage)
        except SystemExit as exc:
            raise gt.GpuTuningError("failed", "書き込みに失敗") from exc

    monkeypatch.setattr(gt, "_run_helper", wrapped)
    fake.helper = helper
    fake.boot = boot
    return fake


def test_only_the_r9700_is_selected_and_negative_offsets_are_parsed(gpu):
    state = gt.status()
    assert state["bdf"] == "0000:03:00.0" and state["xfx"] is True
    assert state["items"]["core"] == {"supported": True, "unit": "MHz", "current": 0, "min": -500, "max": 1000}
    assert state["items"]["voltage"] == {"supported": True, "unit": "mV", "current": 0, "min": -200, "max": 0}
    with pytest.raises(SystemExit):
        gpu.helper.control_gpu_od("0000:71:00.0", "-100", "keep")      # 内蔵 GPU には helper も書かない
    assert (gpu.igpu / "pp_od_clk_voltage").read_text() == IGPU and gpu.commands == []


def test_core_and_voltage_change_independently_and_restore_only_what_changed(gpu):
    gt.apply(-100, None)
    assert gpu.commands == ["s -100", "c"] and (gpu.core, gpu.voltage) == (-100, 0)
    gt.apply(-200, None)
    assert gt.status()["previous"] == {"core": 0}                      # 段階的に試しても最初の値へ戻れる
    gpu.commands.clear()
    state = gt.apply(None, -25)
    assert gpu.commands == ["vo -25", "c"] and state["items"]["voltage"]["current"] == -25
    assert state["previous"] == {"core": 0, "voltage": 0}
    gpu.commands.clear()
    state = gt.restore()
    assert gpu.commands == ["s 0", "vo 0", "c"] and (gpu.core, gpu.voltage) == (0, 0) and state["previous"] == {}
    with pytest.raises(gt.GpuTuningError) as caught:
        gt.restore()
    assert caught.value.code == "nothing"


def test_the_preset_applies_both_offsets_and_never_sends_a_reset_or_other_settings(gpu):
    gt.apply(gt.PRESET["core"], gt.PRESET["voltage"])
    assert gpu.commands == ["s -500", "vo -25", "c"] and (gpu.core, gpu.voltage) == (-500, -25)
    assert not any(command.split()[0] in ("r", "m", "vc", "p", "f") for command in gpu.commands)
    assert sorted(p.name for p in gpu.device.iterdir() if p.is_file()) == [
        "class", "device", "pp_od_clk_voltage", "subsystem_device", "subsystem_vendor", "vendor"]


def test_out_of_range_and_unsupported_are_reported_separately(gpu, tmp_path):
    with pytest.raises(gt.GpuTuningError) as caught:
        gt.apply(-501, None)
    assert caught.value.code == "range" and "-500" in caught.value.message and gpu.commands == []
    with pytest.raises(gt.GpuTuningError) as caught:
        gt.apply(None, 5)
    assert caught.value.code == "range"
    gpu.voltage_supported = False
    gpu.sync()
    state = gt.status()
    assert state["items"]["core"]["supported"] and not state["items"]["voltage"]["supported"]
    with pytest.raises(gt.GpuTuningError) as caught:
        gt.apply(-100, -25)
    assert caught.value.code == "unsupported" and gpu.commands == []    # 片方だけ書かない
    assert gt.apply(-100, None)["items"]["core"]["current"] == -100     # 対応している側は使える


def test_a_failed_write_restores_the_values_before_it(gpu, capsys):
    gt.apply(-100, None)
    gpu.commands.clear()
    gpu.fail_on = "vo"
    with pytest.raises(gt.GpuTuningError) as caught:
        gt.apply(-300, -25)
    assert (gpu.core, gpu.voltage) == (-100, 0)
    assert gpu.commands[-3:] == ["s -100", "vo 0", "c"]
    assert "最後に確認できた値" in caught.value.message and "-100" in caught.value.message
    detail = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert detail["code"] == "failed" and "戻しました" in detail["error"]


def test_only_the_changed_item_is_restored(gpu):
    gt.apply(None, -25)
    gpu.commands.clear()
    gt.restore()
    assert gpu.commands == ["vo 0", "c"]


def test_a_read_failure_is_not_reported_as_missing_support(gpu, monkeypatch):
    def broken(_node):
        raise gt.GpuTuningError("read", "GPU の設定を読み取れませんでした")

    monkeypatch.setattr(gt, "_read_od", broken)
    state = gt.status()
    assert state["available"] is False and "読み取れません" in state["message"]


def test_helper_ready_needs_the_passwordless_rule(tmp_path, monkeypatch):
    """管理者は sudo -l コマンドが成功するので、それでは未導入を見分けられない（2026-11-18 実機）。"""
    import subprocess

    helper = tmp_path / "helper"
    helper.write_text("gpu-od")
    monkeypatch.setattr(gt, "HELPER", helper)
    outputs = ["    (ALL : ALL) ALL\n", f"    (root) NOPASSWD: {helper} gpu-od *\n"]
    for expected, output in zip((False, True), outputs):
        monkeypatch.setattr(gt, "_sudo_cache", (-100.0, False))
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, output, ""))
        assert gt.helper_ready() is expected


def _boot(gpu, name: str) -> None:
    """再起動: 値は 0 に戻り、起動 ID が変わる。"""
    gpu.boot.write_text(name + "\n")
    gpu.core, gpu.voltage = 0, 0
    gpu.sync()
    gpu.commands.clear()


@pytest.fixture
def booted(gpu, monkeypatch):
    monkeypatch.setattr(gt, "_uptime", lambda: 300.0)
    return gpu


def test_boot_apply_runs_once_per_boot_and_leaves_manual_changes_alone(booted):
    gt.apply(-200, -25)
    assert gt.set_boot(True)["boot"]["core"] == -200
    gt.boot_tick()
    assert booted.commands[-1] == "c" and len(booted.commands) == 3    # 今の起動では書き直さない
    gt._update_boot(state="confirmed")
    _boot(booted, "boot-2")
    gt.boot_tick()
    assert booted.commands == ["s -200", "vo -25", "c"] and (booted.core, booted.voltage) == (-200, -25)
    assert gt.status()["previous"] == {"core": 0, "voltage": 0}        # 自動適用の後も元へ戻せる
    gt.restore()
    booted.commands.clear()
    gt.boot_tick()
    assert booted.commands == [] and booted.core == 0                  # 手で戻したら同じ起動では再適用しない


def test_boot_apply_waits_after_startup(booted, monkeypatch):
    gt.apply(-200, None)
    gt.set_boot(True)
    _boot(booted, "boot-2")
    monkeypatch.setattr(gt, "_uptime", lambda: 10.0)
    gt.boot_tick()
    assert booted.commands == []


def test_a_crash_after_boot_apply_disables_it(booted):
    gt.apply(-200, -25)
    gt.set_boot(True)
    _boot(booted, "boot-2")
    gt.boot_tick()
    assert gt.status()["boot"]["state"] == "applied"
    _boot(booted, "boot-3")                                            # 安定を確かめる前に止まった
    gt.boot_tick()
    state = gt.status()["boot"]
    assert state["enabled"] is False and "無効" in state["message"] and booted.commands == []


def test_a_stable_boot_is_confirmed(booted, monkeypatch):
    gt.apply(-200, None)
    gt.set_boot(True)
    _boot(booted, "boot-2")
    gt.boot_tick()
    monkeypatch.setattr(gt.time, "time", lambda: 10**12)
    gt.boot_tick()
    assert gt.status()["boot"]["state"] == "confirmed"
    _boot(booted, "boot-3")
    gt.boot_tick()
    assert booted.core == -200


def test_boot_apply_undoes_itself_if_the_power_limit_changes(booted, monkeypatch):
    gt.apply(-200, None)
    gt.set_boot(True)
    _boot(booted, "boot-2")
    values = iter([("210", "m"), ("180", "m")])
    monkeypatch.setattr(gt, "_guarded_values", lambda _device: next(values))
    gt.boot_tick()
    state = gt.status()["boot"]
    assert booted.core == 0 and state["state"] == "failed" and "電力制限" in state["message"]


def test_boot_needs_an_applied_value(booted):
    with pytest.raises(gt.GpuTuningError):
        gt.set_boot(True)


def test_previous_values_do_not_survive_a_reboot(gpu):
    gt.apply(-100, None)
    gpu.boot.write_text("boot-2\n")
    gpu.core = 0
    gpu.sync()
    assert gt.status()["previous"] == {}


def test_api_requires_an_admin_and_maps_errors(admin_client, gpu):
    assert admin_client.get("/api/v1/system/gpu-tuning").json()["bdf"] == "0000:03:00.0"
    bad = admin_client.post("/api/v1/system/gpu-tuning/apply", headers=CSRF_HEADERS, json={"core_offset_mhz": -999})
    assert bad.status_code == 422 and "範囲" in bad.json()["detail"]
    good = admin_client.post("/api/v1/system/gpu-tuning/apply", headers=CSRF_HEADERS, json={"core_offset_mhz": -100})
    assert good.status_code == 200 and good.json()["items"]["core"]["current"] == -100
    assert admin_client.post("/api/v1/system/gpu-tuning/restore", headers=CSRF_HEADERS).json()["items"]["core"]["current"] == 0
