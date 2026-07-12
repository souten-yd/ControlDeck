"""アラート評価ループ。

メトリクススナップショット + アプリ状態を定期評価し、しきい値を duration_seconds 継続して
超えたら AlertEvent を発火して通知する。条件が解消したら resolved にする。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AlertEvent, AlertRule, NotificationChannel, utcnow
from app.security.crypto import decrypt_text

logger = logging.getLogger("control_deck.alerts")

OPERATORS = {
    "gt": lambda v, t: v > t,
    "gte": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "lte": lambda v, t: v <= t,
}

# ルールごとの「しきい値超過が始まった時刻」を保持（継続時間判定用）
_breach_since: dict[int, float] = {}
# ルールごとの現在アクティブな AlertEvent ID
_active_event: dict[int, int] = {}


def _metric_value(metric: str, snapshot: dict, rule: AlertRule, db) -> float | None:
    if not snapshot:
        return None
    gpu = snapshot.get("gpu") or {}
    if metric == "cpu_percent":
        return snapshot.get("cpu", {}).get("percent")
    if metric == "memory_percent":
        return snapshot.get("memory", {}).get("percent")
    if metric == "cpu_temp_c":
        return snapshot.get("cpu", {}).get("temperature_c")
    if metric == "gpu_percent":
        return gpu.get("utilization_percent")
    if metric == "gpu_temp_c":
        return gpu.get("temperature_c")
    if metric == "vram_percent":
        used, total = gpu.get("vram_used_bytes"), gpu.get("vram_total_bytes")
        return (used / total * 100) if used is not None and total else None
    if metric == "disk_percent":
        import psutil

        try:
            return psutil.disk_usage("/").percent
        except OSError:
            return None
    if metric == "app_down":
        # アプリが停止/失敗なら 1、稼働なら 0
        from app.applications import service as apps
        from app.models import ManagedApplication

        if rule.app_id is None:
            return None
        app = db.get(ManagedApplication, rule.app_id)
        if app is None:
            return None
        status = apps.runtime_info(app).status
        return 1.0 if status in ("STOPPED", "FAILED", "UNKNOWN") else 0.0
    return None


METRIC_LABELS = {
    "cpu_percent": "CPU 使用率",
    "memory_percent": "RAM 使用率",
    "cpu_temp_c": "CPU 温度",
    "gpu_percent": "GPU 使用率",
    "gpu_temp_c": "GPU 温度",
    "vram_percent": "VRAM 使用率",
    "disk_percent": "ディスク使用率",
    "app_down": "アプリ停止",
}


async def _dispatch(rule: AlertRule, value: float | None, db) -> None:
    channel_ids = json.loads(rule.channel_ids_json or "[]")
    if not channel_ids:
        return
    from app.alerts.notify import send_notification

    label = METRIC_LABELS.get(rule.metric, rule.metric)
    title = f"🚨 アラート: {rule.name}"
    if rule.metric == "app_down":
        message = f"{label} を検知しました"
    else:
        message = f"{label} が {value:.1f}（しきい値 {rule.operator} {rule.threshold}）"
    for cid in channel_ids:
        ch = db.get(NotificationChannel, cid)
        if ch is None or not ch.enabled:
            continue
        try:
            url = decrypt_text(ch.url_encrypted)
        except Exception:
            continue
        await send_notification(ch.channel_type, url, title, message)


async def evaluate_once() -> None:
    from app.monitoring.collector import collector

    snapshot = collector.latest
    now = time.monotonic()
    db = SessionLocal()
    try:
        rules = db.execute(select(AlertRule).where(AlertRule.enabled.is_(True))).scalars().all()
        for rule in rules:
            value = _metric_value(rule.metric, snapshot, rule, db)
            if value is None:
                continue
            breached = OPERATORS.get(rule.operator, OPERATORS["gt"])(value, rule.threshold)

            if breached:
                started = _breach_since.setdefault(rule.id, now)
                sustained = now - started >= rule.duration_seconds
                already_active = rule.id in _active_event
                if sustained and not already_active:
                    # クールダウン確認
                    last = rule.last_triggered_at
                    if last is not None and last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last is not None and (utcnow() - last).total_seconds() < rule.cooldown_seconds:
                        continue
                    label = METRIC_LABELS.get(rule.metric, rule.metric)
                    event = AlertEvent(
                        rule_id=rule.id, rule_name=rule.name, value=value,
                        message=f"{label} = {value:.1f}", status="active", notified=False,
                    )
                    db.add(event)
                    rule.last_triggered_at = utcnow()
                    db.commit()
                    _active_event[rule.id] = event.id
                    await _dispatch(rule, value, db)
                    event.notified = True
                    db.commit()
                    logger.warning("アラート発火: %s (%s=%.1f)", rule.name, rule.metric, value)
            else:
                _breach_since.pop(rule.id, None)
                event_id = _active_event.pop(rule.id, None)
                if event_id is not None:
                    event = db.get(AlertEvent, event_id)
                    if event is not None and event.status == "active":
                        event.status = "resolved"
                        event.resolved_at = utcnow()
                        db.commit()
                        logger.info("アラート解消: %s", rule.name)
    except Exception:
        logger.exception("alert evaluation error")
    finally:
        db.close()


async def alert_loop() -> None:
    from app.maintenance.watchdog import beat

    await asyncio.sleep(15)  # 収集の立ち上がりを待つ
    while True:
        try:
            await evaluate_once()
        except Exception:
            logger.exception("alert loop error")
        beat("alerts")
        await asyncio.sleep(15)
