"""metrics の配信が黙って止まらないことを守る。

いずれも実際に起きた不具合の再発防止。購読者が外されて無音になる、
保存の例外で毎分ぶんの記録が消える、無出力のときに何も送らない——
どれも画面からは「再接続中が居座る」「グラフが止まる」としか見えず、
症状から原因へ辿るのに時間がかかった。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

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


def _snapshot(cpu: float) -> dict:
    return {
        "cpu": {"percent": cpu},
        "memory": {"percent": 50.0},
        "gpu": None,
        "io": {"disk_read_bps": 1.0, "disk_write_bps": 2.0, "net_rx_bps": 3.0, "net_tx_bps": 4.0},
    }


def test_slow_subscriber_stays_subscribed_and_gets_newest(caplog):
    """遅い購読者を切り離さない。

    以前は Queue が埋まった時点で購読を外していた。外された側は
    `queue.get()` で永久に止まり、送信しないので切断にも気づけず、
    ブラウザからは完全な無音になっていた。
    """
    collector = MetricsCollector()

    async def scenario() -> list[int]:
        queue = collector.subscribe()
        with caplog.at_level(logging.WARNING, logger="app.monitoring.collector"):
            for seq in range(20):          # maxsize=5 を大きく超えて配る
                collector._broadcast({"seq": seq})
        assert queue in collector._subscribers, "詰まっただけで購読を外してはいけない"
        assert queue.qsize() == 5
        return [queue.get_nowait()["seq"] for _ in range(5)]

    # 捨てるのは古い方。最新が必ず残る（メトリクスは最新の1点だけが要る）
    assert asyncio.run(scenario()) == [15, 16, 17, 18, 19]
    # 捨てたことは黙らない。ただし毎回は出さない（遅い購読者は毎回詰まる）
    warnings = [r for r in caplog.records if "遅い購読者" in r.getMessage()]
    assert len(warnings) == 1


def test_flush_minute_persists_when_rows_for_the_hour_already_exist(client):
    """保存が毎分 TypeError で落ちないこと。

    SQLite の DateTime は offset を落として保存する。既定の
    synchronize_session だと ORM が delete の条件を Python 側でも
    session 上の行へ当てにいき、読み直した naive な timestamp と
    aware な cutoff を比べて落ちていた。commit ごと巻き戻るので
    分・時メトリクスが一行も残らず、グラフが止まって見えた。
    """
    _clean_metrics()
    now = datetime.now(timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    db = SessionLocal()
    try:
        # 同じ時間の行を先に置く。_update_hour がこれを読み込み、
        # naive な timestamp が session に載ることが再現の条件。
        db.add(MetricMinute(timestamp=hour_start, cpu_percent=10))
        db.add(MetricHour(timestamp=hour_start, minute_count=1, cpu_percent=10))
        db.commit()
    finally:
        db.close()

    collector = MetricsCollector()
    collector._minute_bucket = [_snapshot(20.0), _snapshot(40.0)]
    collector._flush_minute()          # 以前はここで TypeError

    db = SessionLocal()
    try:
        minutes = db.execute(select(MetricMinute).order_by(MetricMinute.timestamp)).scalars().all()
        assert len(minutes) == 2, "新しい分の行が保存されていない"
        assert minutes[-1].cpu_percent == 30.0
        hour = db.execute(select(MetricHour)).scalar_one()
        assert hour.minute_count == 2
    finally:
        db.close()
        _clean_metrics()


def test_metrics_stream_sends_stale_heartbeat_while_no_new_value(admin_client, monkeypatch):
    """新しい値が来なくても黙らない。

    アプリ層の heartbeat が無いと、uvicorn の Ping/Pong はブラウザの
    onmessage に届かないので生存の合図にならない。送信を試みないため
    相手の切断にも気づけない。
    """
    from app.monitoring import router

    monkeypatch.setattr(router, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(router.collector, "latest", _snapshot(12.0))
    # 常駐の収集タスクが本物の値を流し込むと、無出力の状況を作れない。
    # 誰も入れない Queue を渡して、heartbeat だけが動く状態にする。
    idle: asyncio.Queue = asyncio.Queue(maxsize=5)
    monkeypatch.setattr(router.collector, "subscribe", lambda: idle)
    monkeypatch.setattr(router.collector, "unsubscribe", lambda _q: None)

    with admin_client.websocket_connect("/api/v1/system/metrics/stream") as websocket:
        first = websocket.receive_json()
        assert "stale" not in first, "接続直後は今の値そのもの"
        assert "cpu" in first

        # 常駐の収集タスクが latest を更新するので値は比べない。
        # ここで守りたいのは「無出力でも送り続ける」ことだけ。
        for _ in range(2):
            beat = websocket.receive_json()
            assert beat["stale"] is True, "無出力のときは stale 印付きで送る"
            assert "cpu" in beat
