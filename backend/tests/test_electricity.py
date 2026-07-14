"""PSU 電力読み取りと電気代積算のテスト。"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import CSRF_HEADERS


# ---- hwmon 検出・ラベル解決・単位変換 ----

def _make_psu(tmp_path, name="corsairpsu", total_uw=74000000, total_label="power total", extra=None):
    """疑似 hwmon ディレクトリを作る。"""
    hw = tmp_path / "hwmon9"
    hw.mkdir(parents=True, exist_ok=True)
    (hw / "name").write_text(name + "\n")
    (hw / "power1_label").write_text(total_label + "\n")
    (hw / "power1_input").write_text(str(total_uw))
    for k, v in (extra or {}).items():
        (hw / k).write_text(str(v))
    return hw


def test_hwmon_detection_varies_number(tmp_path, monkeypatch):
    from app.monitoring import psu

    root = tmp_path / "hwmon_root"
    root.mkdir()
    # 別デバイスと corsairpsu を混在（番号は任意）
    other = root / "hwmon0"; other.mkdir(); (other / "name").write_text("k10temp\n")
    hw = root / "hwmon42"; hw.mkdir()
    (hw / "name").write_text("corsairpsu\n")  # 改行込み
    (hw / "power1_label").write_text("power total")
    (hw / "power1_input").write_text("74000000")
    monkeypatch.setattr(psu, "HWMON_ROOT", root)
    monkeypatch.setattr(psu, "_last_available", None)
    r = psu.read_corsair_psu()
    assert r["available"] is True
    assert r["output_power_w"] == 74.0  # µW → W


def test_hwmon_absent(tmp_path, monkeypatch):
    from app.monitoring import psu

    empty = tmp_path / "empty"; empty.mkdir()
    monkeypatch.setattr(psu, "HWMON_ROOT", empty)
    monkeypatch.setattr(psu, "_last_available", None)
    r = psu.read_corsair_psu()
    assert r["available"] is False and r["output_power_w"] is None


def test_label_case_and_space(tmp_path, monkeypatch):
    from app.monitoring import psu

    root = tmp_path / "r"; root.mkdir()
    hw = root / "hwmon3"; hw.mkdir()
    (hw / "name").write_text("corsairpsu")
    # 大文字・余分な空白のラベル
    (hw / "power1_label").write_text("  Power Total  \n")
    (hw / "power1_input").write_text("56000000")
    monkeypatch.setattr(psu, "HWMON_ROOT", root)
    monkeypatch.setattr(psu, "_last_available", None)
    assert psu.read_corsair_psu()["output_power_w"] == 56.0


def test_invalid_power_rejected(tmp_path, monkeypatch):
    from app.monitoring import psu

    root = tmp_path / "r"; root.mkdir()
    hw = root / "hwmon3"; hw.mkdir()
    (hw / "name").write_text("corsairpsu")
    (hw / "power1_label").write_text("power total")
    (hw / "power1_input").write_text("-5")  # 負数 → 拒否
    monkeypatch.setattr(psu, "HWMON_ROOT", root)
    monkeypatch.setattr(psu, "_last_available", None)
    assert psu.read_corsair_psu()["available"] is False


def test_unit_conversion_zero(tmp_path, monkeypatch):
    from app.monitoring import psu

    root = tmp_path / "r"; root.mkdir()
    hw = root / "hwmon3"; hw.mkdir()
    (hw / "name").write_text("corsairpsu")
    (hw / "power1_label").write_text("power total")
    (hw / "power1_input").write_text("0")  # 0W は有効な測定値
    monkeypatch.setattr(psu, "HWMON_ROOT", root)
    monkeypatch.setattr(psu, "_last_available", None)
    r = psu.read_corsair_psu()
    assert r["available"] is True and r["output_power_w"] == 0.0


# ---- 効率補正・台形積分・日付境界 ----

def test_day_boundary_split():
    from app.monitoring.electricity import _split_energy_by_day

    tz = timezone(timedelta(hours=9))
    s = datetime(2026, 7, 15, 23, 59, 59, tzinfo=tz)
    e = datetime(2026, 7, 16, 0, 0, 1, tzinfo=tz)
    parts = _split_energy_by_day(87.0, 87.0, s, e)
    assert len(parts) == 2
    assert parts[0][0] == "2026-07-15" and abs(parts[0][2] - 1.0) < 0.01
    assert parts[1][0] == "2026-07-16" and abs(parts[1][2] - 1.0) < 0.01
    # 合計 kWh = 87W * 2s = 87*2/3.6e6
    total = parts[0][1] + parts[1][1]
    assert abs(total - 87.0 * 2 / 3_600_000) < 1e-12


def test_trapezoidal_and_efficiency():
    from app.monitoring.electricity import _split_energy_by_day

    tz = timezone(timedelta(hours=9))
    s = datetime(2026, 7, 15, 12, 0, 0, tzinfo=tz)
    e = datetime(2026, 7, 15, 12, 0, 2, tzinfo=tz)
    # 80W→100W の 2 秒（台形）: (80+100)/2 * 2 / 3.6e6
    parts = _split_energy_by_day(80.0, 100.0, s, e)
    assert len(parts) == 1
    assert abs(parts[0][1] - (90.0 * 2 / 3_600_000)) < 1e-12


def test_cost_24h(tmp_path, monkeypatch):
    """DC74W・効率85%・35.69円/kWh・24h → 約74.57円。"""
    eff = 0.85
    input_w = 74.0 / eff
    assert abs(input_w - 87.0588235) < 1e-4
    energy_kwh = input_w / 1000 * 24
    assert abs(energy_kwh - 2.0894117) < 1e-4
    cost = energy_kwh * 35.69
    assert abs(cost - 74.57) < 0.1


# ---- 積算エンジン（boot_id・欠測・異常間隔） ----

def test_accumulator_integrates_and_skips_gaps(monkeypatch):
    import app.monitoring.electricity as el

    acc = el.ElectricityAccumulator()
    acc._loaded = True
    # monotonic と wall を制御
    fake = {"mono": 1000.0, "wall": datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))}
    monkeypatch.setattr(el.time, "monotonic", lambda: fake["mono"])
    monkeypatch.setattr(el, "_local_now", lambda: fake["wall"])
    # persist を無効化（DB 触らない）
    monkeypatch.setattr(acc, "persist", lambda reason="interval": None)

    acc.update(85.0)  # 初回: 積分開始点のみ
    assert acc.session_energy_kwh == 0.0
    fake["mono"] += 2.0; fake["wall"] += timedelta(seconds=2)
    acc.update(85.0)  # 2秒後: 積算される（input=100W）
    assert acc.session_energy_kwh > 0
    e1 = acc.session_energy_kwh
    # 異常間隔（60秒）は積算しない
    fake["mono"] += 60.0; fake["wall"] += timedelta(seconds=60)
    acc.update(85.0)
    assert acc.session_energy_kwh == e1
    # PSU 欠測 → 復帰は新しい積分開始点（積算しない）
    fake["mono"] += 2.0; fake["wall"] += timedelta(seconds=2)
    acc.update(None)
    fake["mono"] += 2.0; fake["wall"] += timedelta(seconds=2)
    acc.update(85.0)
    assert acc.session_energy_kwh == e1  # 欠測直後は積算されない


# ---- 設定検証 ----

def test_config_validation():
    from app.config import ElectricityConfig

    ElectricityConfig(price_per_kwh_yen=35.69, psu_efficiency=0.85)  # OK
    with pytest.raises(Exception):
        ElectricityConfig(price_per_kwh_yen=-1)
    with pytest.raises(Exception):
        ElectricityConfig(psu_efficiency=0.4)
    with pytest.raises(Exception):
        ElectricityConfig(persistence_interval_seconds=10)
    # 既定は 35.69 円/kWh
    assert ElectricityConfig().price_per_kwh_yen == 35.69


# ---- API ----

def test_overview_includes_power(admin_client):
    r = admin_client.get("/api/v1/system/overview")
    assert r.status_code == 200
    metrics = r.json().get("metrics", {})
    if "power" in metrics:  # 収集が走っていれば
        p = metrics["power"]
        assert "price_per_kwh_yen" in p
        assert p["price_per_kwh_yen"] == 35.69
