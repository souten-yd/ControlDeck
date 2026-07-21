"""アラート評価ループ。

メトリクススナップショット + アプリ状態を定期評価し、しきい値を duration_seconds 継続して
超えたら AlertEvent を発火して通知する。条件が解消したら resolved にする。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AlertEvent, AlertLogCursor, AlertRule, NotificationChannel, utcnow
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

LOG_ERROR_METRIC = "app_log_error"
_ERROR_PATTERN = re.compile(rb"\b(?:ERROR|CRITICAL|FATAL)\b", re.IGNORECASE)
_LOG_SCAN_BYTES = 256 * 1024
_LOG_MATCH_OVERLAP = len("CRITICAL") - 1


def _scan_log_errors(rule: AlertRule, db: Session) -> float | None:
    """新規ログ範囲だけを走査する。本文はDB・通知・内部ログへ出さない。"""
    if rule.id is None or rule.app_id is None:
        return None
    from app.logs import service as logs

    errors = 0
    readable_streams = 0
    for stream in logs.STREAMS:
        cursor = db.execute(
            select(AlertLogCursor).where(
                AlertLogCursor.rule_id == rule.id,
                AlertLogCursor.stream == stream,
            )
        ).scalar_one_or_none()
        path = logs.log_path(rule.app_id, stream).resolve()
        try:
            stat = path.stat()
        except FileNotFoundError:
            if cursor is None:
                db.add(AlertLogCursor(rule_id=rule.id, stream=stream))
            else:
                cursor.file_identity, cursor.offset = "", 0
            readable_streams += 1
            continue
        except OSError as error:
            logger.warning("アプリログ状態取得失敗: app=%s stream=%s (%s)", rule.app_id, stream, type(error).__name__)
            continue

        readable_streams += 1
        identity = f"{stat.st_dev}:{stat.st_ino}"
        if cursor is None:
            # API外で作られた既存ルールも初回は末尾から開始し、過去ログを誤通知しない。
            db.add(AlertLogCursor(rule_id=rule.id, stream=stream, file_identity=identity, offset=stat.st_size))
            continue
        if cursor.file_identity != identity or stat.st_size < cursor.offset:
            cursor.file_identity, cursor.offset = identity, 0
        if stat.st_size == cursor.offset:
            continue

        start = max(0, cursor.offset - _LOG_MATCH_OVERLAP)
        overlap = cursor.offset - start
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                data = handle.read(_LOG_SCAN_BYTES + overlap)
        except OSError as error:
            logger.warning("アプリログ読取失敗: app=%s stream=%s (%s)", rule.app_id, stream, type(error).__name__)
            readable_streams -= 1
            continue
        for match in _ERROR_PATTERN.finditer(data):
            if match.end() > overlap:
                errors += 1
        cursor.file_identity = identity
        cursor.offset += max(0, len(data) - overlap)
    return float(errors) if readable_streams else None


def _metric_value(metric: str, snapshot: dict, rule: AlertRule, db: Session | None) -> float | None:
    if metric == LOG_ERROR_METRIC:
        if db is None:
            return None
        return _scan_log_errors(rule, db)
    if metric in APP_METRICS:
        from app.applications import service as apps
        from app.models import ManagedApplication

        if db is None or rule.app_id is None:
            return None
        app = db.get(ManagedApplication, rule.app_id)
        if app is None:
            return None
        runtime = apps.runtime_info(app, include_health=metric == "app_health_failed")
        if metric == "app_down":
            return 1.0 if runtime.status in ("STOPPED", "FAILED", "UNKNOWN") else 0.0
        if metric == "app_health_failed":
            return 1.0 if runtime.status == "DEGRADED" or (runtime.health is not None and not runtime.health.ok) else 0.0
        return float(runtime.restart_count)
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
    "app_health_failed": "ヘルスチェック失敗",
    "app_restart_loop": "再起動回数",
    LOG_ERROR_METRIC: "ログ ERROR",
}
APP_METRICS = {"app_down", "app_health_failed", "app_restart_loop", LOG_ERROR_METRIC}
BOOLEAN_METRICS = {"app_down", "app_health_failed", LOG_ERROR_METRIC}


async def _dispatch(rule: AlertRule, value: float | None, db) -> bool:
    channel_ids = json.loads(rule.channel_ids_json or "[]")
    if not channel_ids:
        return False
    from app.alerts.notify import send_notification

    label = METRIC_LABELS.get(rule.metric, rule.metric)
    title = f"🚨 アラート: {rule.name}"
    if rule.metric in BOOLEAN_METRICS:
        message = f"{label} を検知しました"
    else:
        message = f"{label} が {value:.1f}（しきい値 {rule.operator} {rule.threshold}）"
    attempted = False
    all_sent = True
    for cid in channel_ids:
        ch = db.get(NotificationChannel, cid)
        if ch is None or not ch.enabled:
            continue
        attempted = True
        try:
            url = decrypt_text(ch.url_encrypted)
        except Exception:
            all_sent = False
            continue
        if not await send_notification(ch.channel_type, url, title, message):
            all_sent = False
    return attempted and all_sent


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
            # 旧app_down ruleはUIが隠した既定threshold=90を保存していた。boolean条件は
            # stored comparatorに依存せず1をtrueとして既存ruleも正しく発火させる。
            breached = value >= 1 if rule.metric in BOOLEAN_METRICS else OPERATORS.get(rule.operator, OPERATORS["gt"])(value, rule.threshold)
            # active 判定は DB を正とする（再起動でメモリが消えても重複発火・残留しない）
            active_event = db.execute(
                select(AlertEvent)
                .where(AlertEvent.rule_id == rule.id, AlertEvent.status == "active")
                .order_by(AlertEvent.triggered_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if breached:
                started = _breach_since.setdefault(rule.id, now)
                sustained = rule.metric == LOG_ERROR_METRIC or now - started >= rule.duration_seconds
                if sustained and active_event is None:
                    last = rule.last_triggered_at
                    if last is not None and last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last is not None and (utcnow() - last).total_seconds() < rule.cooldown_seconds:
                        if rule.metric == LOG_ERROR_METRIC:
                            db.commit()
                        continue
                    label = METRIC_LABELS.get(rule.metric, rule.metric)
                    event = AlertEvent(
                        rule_id=rule.id, rule_name=rule.name, value=value,
                        message=f"{label} = {value:.1f}", status="active", notified=False,
                    )
                    db.add(event)
                    rule.last_triggered_at = utcnow()
                    db.commit()
                    event.notified = await _dispatch(rule, value, db)
                    db.commit()
                    logger.warning("アラート発火: %s (%s=%.1f)", rule.name, rule.metric, value)
                    # イベントトリガーのワークフローを起動（自己修復フロー等）
                    try:
                        from app.workflows.engine import fire_event_triggers, fire_system_triggers

                        workflow_payload = {
                            "message": f"アラート: {rule.name}（{label} = {value:.1f}）",
                            "rule": rule.name, "metric": rule.metric,
                            "value": value, "threshold": rule.threshold,
                        }
                        await fire_event_triggers("alert", workflow_payload)
                        source = {
                            "gpu_percent": "gpu", "gpu_temp_c": "gpu",
                            "vram_percent": "vram", "disk_percent": "disk",
                        }.get(rule.metric)
                        if source:
                            await fire_system_triggers(source, {
                                **workflow_payload,
                                "resource": rule.name,
                                "app_id": rule.app_id,
                            })
                    except Exception:
                        logger.exception("event trigger dispatch error")
            else:
                _breach_since.pop(rule.id, None)
                # 条件解消: この rule の active イベントをすべて resolved にする（残留防止）
                if active_event is not None:
                    db.execute(
                        update(AlertEvent)
                        .where(AlertEvent.rule_id == rule.id, AlertEvent.status == "active")
                        .values(status="resolved", resolved_at=utcnow())
                    )
                    db.commit()
                    logger.info("アラート解消: %s", rule.name)
            if rule.metric == LOG_ERROR_METRIC:
                # イベントを作らない静穏時も読取位置を永続化する。
                db.commit()
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
