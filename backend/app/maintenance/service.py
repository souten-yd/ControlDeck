"""自己メンテナンスループ。

1 時間ごと（起動 5 分後に初回）に以下を実行する:
- 管理アプリログのローテーション（copytruncate + gzip、世代管理、保持日数）
- 期限切れ・失効セッションの purge
- 監査ログの保持期間超過分の削除
- SQLite WAL checkpoint + PRAGMA optimize
- data_dir ディスク残量の自己点検

すべて失敗しても本体を止めない（ログに記録して継続）。
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import data_dir, get_config

logger = logging.getLogger("control_deck.maintenance")

INITIAL_DELAY = 300
INTERVAL = 3600

# 直近の実行結果（self-status API 用）
last_run: dict = {"at": None, "results": {}}


def rotate_log_file(path: Path, max_bytes: int, generations: int) -> bool:
    """copytruncate 方式でローテーションする。

    systemd の append: が保持する fd を切らないため、コピー後に元ファイルを truncate する。
    世代: path.1.gz（最新）〜 path.{generations}.gz（最古）。
    """
    if not path.exists() or path.stat().st_size <= max_bytes:
        return False
    # 既存世代を後ろへずらす
    oldest = path.with_name(f"{path.name}.{generations}.gz")
    oldest.unlink(missing_ok=True)
    for i in range(generations - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}.gz")
        if src.exists():
            src.rename(path.with_name(f"{path.name}.{i + 1}.gz"))
    # copy → gzip → truncate
    with path.open("rb") as f_in, gzip.open(path.with_name(f"{path.name}.1.gz"), "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    with path.open("r+b") as f:
        f.truncate(0)
    return True


def _rotate_app_logs() -> dict:
    cfg = get_config().logs
    max_bytes = cfg.rotate_size_mb * 1024 * 1024
    logs_root = data_dir() / "logs"
    rotated = 0
    removed = 0
    if logs_root.is_dir():
        cutoff = time.time() - cfg.retention_days * 86400
        for log_file in logs_root.glob("*/std*.log"):
            try:
                if rotate_log_file(log_file, max_bytes, cfg.rotate_generations):
                    rotated += 1
            except OSError as e:
                logger.warning("ローテーション失敗 %s: %s", log_file, e)
        for gz in logs_root.glob("*/std*.log.*.gz"):
            try:
                if gz.stat().st_mtime < cutoff:
                    gz.unlink()
                    removed += 1
            except OSError:
                pass
    return {"rotated": rotated, "expired_removed": removed}


def _purge_sessions() -> dict:
    from sqlalchemy import delete, or_

    from app.database import SessionLocal
    from app.models import UserSession

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    db = SessionLocal()
    try:
        result = db.execute(
            delete(UserSession).where(
                or_(UserSession.expires_at < cutoff, UserSession.revoked_at < cutoff)
            )
        )
        db.commit()
        return {"purged": result.rowcount}
    finally:
        db.close()


def _purge_audit_logs() -> dict:
    from sqlalchemy import delete

    from app.database import SessionLocal
    from app.models import AuditLog

    days = get_config().logs.audit_retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        result = db.execute(delete(AuditLog).where(AuditLog.timestamp < cutoff))
        db.commit()
        return {"purged": result.rowcount, "retention_days": days}
    finally:
        db.close()


def _optimize_db() -> dict:
    from sqlalchemy import text

    from app.database import engine

    if not engine.url.drivername.startswith("sqlite"):
        return {"skipped": True}
    with engine.connect() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.execute(text("PRAGMA optimize"))
    return {"ok": True}


def _check_disk() -> dict:
    usage = shutil.disk_usage(data_dir())
    percent_free = usage.free / usage.total * 100
    if percent_free < 10:
        logger.warning(
            "data_dir のディスク残量が少なくなっています: 残り %.1f%%（%s）",
            percent_free,
            data_dir(),
        )
    return {"free_percent": round(percent_free, 1)}


def _purge_file_trash() -> dict:
    from app.files.service import enforce_trash_limits, purge_expired_trash

    removed = purge_expired_trash()
    enforce_trash_limits()
    return {"purged": removed, "retention_days": get_config().files.trash_retention_days}


TASKS = {
    "rotate_app_logs": _rotate_app_logs,
    "purge_sessions": _purge_sessions,
    "purge_audit_logs": _purge_audit_logs,
    "purge_file_trash": _purge_file_trash,
    "optimize_db": _optimize_db,
    "check_disk": _check_disk,
}


def run_maintenance() -> dict:
    results: dict = {}
    for name, fn in TASKS.items():
        try:
            results[name] = {"ok": True, **fn()}
        except Exception as e:
            logger.exception("メンテナンスタスク %s 失敗", name)
            results[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    last_run["at"] = datetime.now(timezone.utc).isoformat()
    last_run["results"] = results
    logger.info("自己メンテナンス完了: %s", {k: v.get("ok") for k, v in results.items()})
    return results


async def maintenance_loop() -> None:
    from app.maintenance.watchdog import beat

    await asyncio.sleep(INITIAL_DELAY)
    while True:
        try:
            await asyncio.to_thread(run_maintenance)
        except Exception:
            logger.exception("maintenance loop error")
        beat("maintenance")
        await asyncio.sleep(INTERVAL)
