from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import MetricHour, MetricMinute
from app.monitoring.collector import MetricsCollector


def _clean_metrics() -> None:
    db = SessionLocal()
    try:
        db.execute(delete(MetricHour))
        db.execute(delete(MetricMinute))
        db.commit()
    finally:
        db.close()


def test_hourly_metrics_are_recomputed_idempotently(client):
    _clean_metrics()
    start = datetime(2026, 1, 2, 3, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        db.add_all(
            [
                MetricMinute(
                    timestamp=start + timedelta(minutes=2),
                    cpu_percent=10,
                    memory_percent=40,
                    gpu_percent=None,
                    net_rx_bps=100,
                ),
                MetricMinute(
                    timestamp=start + timedelta(minutes=48),
                    cpu_percent=30,
                    memory_percent=60,
                    gpu_percent=80,
                    net_rx_bps=300,
                ),
            ]
        )
        db.flush()
        MetricsCollector._update_hour(db, start + timedelta(minutes=48))
        db.flush()

        hour = db.execute(select(MetricHour)).scalar_one()
        assert hour.timestamp.replace(tzinfo=timezone.utc) == start
        assert hour.minute_count == 2
        assert hour.cpu_percent == pytest.approx(20)
        assert hour.memory_percent == pytest.approx(50)
        assert hour.gpu_percent == pytest.approx(80)
        assert hour.net_rx_bps == pytest.approx(200)

        db.add(MetricMinute(timestamp=start + timedelta(minutes=55), cpu_percent=50))
        db.flush()
        MetricsCollector._update_hour(db, start + timedelta(minutes=55))
        db.commit()

        hours = db.execute(select(MetricHour)).scalars().all()
        assert len(hours) == 1
        assert hours[0].minute_count == 3
        assert hours[0].cpu_percent == pytest.approx(30)
    finally:
        db.close()
        _clean_metrics()


def test_metrics_history_selects_minute_and_hour_resolution(admin_client):
    _clean_metrics()
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(MetricMinute(timestamp=now - timedelta(hours=30), cpu_percent=21))
        db.add(MetricHour(timestamp=now - timedelta(days=35), minute_count=60, cpu_percent=42))
        db.commit()
    finally:
        db.close()

    try:
        minute = admin_client.get("/api/v1/system/metrics/history?minutes=2880")
        assert minute.status_code == 200
        assert minute.json()["resolution"] == "minute"
        assert [sample["cpu_percent"] for sample in minute.json()["samples"]] == [21]

        hour = admin_client.get("/api/v1/system/metrics/history?minutes=51840")
        assert hour.status_code == 200
        assert hour.json()["resolution"] == "hour"
        assert [sample["cpu_percent"] for sample in hour.json()["samples"]] == [42]

        too_long = admin_client.get("/api/v1/system/metrics/history?minutes=525601")
        assert too_long.status_code == 422
    finally:
        _clean_metrics()


def test_metrics_history_keeps_raw_resolution_for_full_day(admin_client, monkeypatch):
    from app.monitoring import router

    now = datetime.now(timezone.utc)
    sample = {
        "timestamp": (now - timedelta(hours=23)).isoformat(),
        "cpu": {"percent": 12},
        "memory": {"percent": 34},
        "gpu": None,
        "io": {"net_rx_bps": 56, "net_tx_bps": 78},
    }
    monkeypatch.setattr(router.collector, "history", [sample])

    response = admin_client.get("/api/v1/system/metrics/history?minutes=1440")
    assert response.status_code == 200
    assert response.json() == {
        "resolution": "raw",
        "samples": [
            {
                "timestamp": sample["timestamp"],
                "cpu_percent": 12,
                "memory_percent": 34,
                "gpu_percent": None,
                "vram_percent": None,
                "net_rx_bps": 56,
                "net_tx_bps": 78,
            }
        ],
    }
