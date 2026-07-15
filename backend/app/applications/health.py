"""管理アプリのヘルスチェック。任意シェルは実行しない。"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import datetime, timezone
from threading import Lock

import httpx
from sqlalchemy import select

from app.database import SessionLocal
from app.files import service as files
from app.models import ManagedApplication
from app.schemas.apps import HealthCheckConfig, HealthCheckResult

_cache: dict[int, HealthCheckResult] = {}
_lock = Lock()
logger = logging.getLogger(__name__)


def cached(app_id: int) -> HealthCheckResult | None:
    with _lock:
        return _cache.get(app_id)


def clear(app_id: int) -> None:
    with _lock:
        _cache.pop(app_id, None)


def _result(ok: bool, message: str, started: float) -> HealthCheckResult:
    return HealthCheckResult(
        ok=ok,
        message=message[:500],
        checked_at=datetime.now(timezone.utc).isoformat(),
        latency_ms=round((time.monotonic() - started) * 1000, 1),
    )


def run(config: HealthCheckConfig, *, process_running: bool) -> HealthCheckResult:
    started = time.monotonic()
    try:
        if config.type == "none":
            return _result(True, "ヘルスチェック未設定", started)
        if config.type == "process":
            return _result(process_running, "プロセス稼働中" if process_running else "プロセス停止", started)
        if not process_running:
            return _result(False, "プロセスが稼働していません", started)
        if config.type == "tcp":
            if config.port is None:
                return _result(False, "TCPポートが未設定です", started)
            with socket.create_connection((config.host, config.port), timeout=config.timeout_seconds):
                return _result(True, f"TCP {config.host}:{config.port} 接続成功", started)
        if config.type == "http":
            if not config.url.startswith(("http://", "https://")):
                return _result(False, "HTTP URLが不正です", started)
            with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False) as client:
                with client.stream("GET", config.url) as response:
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) >= 64 * 1024:
                            body = body[:64 * 1024]
                            break
                    if response.status_code != config.expected_status:
                        return _result(False, f"HTTP {response.status_code}（期待 {config.expected_status}）", started)
                    if config.body_contains and config.body_contains not in body.decode(response.encoding or "utf-8", errors="replace"):
                        return _result(False, "レスポンス本文が期待文字列を含みません", started)
                    return _result(True, f"HTTP {response.status_code}", started)
        if config.type == "file":
            path = files.resolve(config.path)
            return _result(path.is_file(), "ファイルを確認" if path.is_file() else "通常ファイルではありません", started)
        return _result(False, "未対応のヘルスチェックです", started)
    except (OSError, httpx.HTTPError, files.FileAccessError, ValueError) as e:
        return _result(False, f"確認失敗: {e}", started)


def check_app(app: ManagedApplication) -> HealthCheckResult:
    from app.applications import service as apps

    config = apps.get_health_check(app)
    process_running = apps.runtime_info(app, include_health=False).status in ("RUNNING", "DEGRADED")
    result = run(config, process_running=process_running)
    with _lock:
        _cache[app.id] = result
    return result


async def health_check_loop() -> None:
    while True:
        try:
            db = SessionLocal()
            try:
                rows = db.execute(select(ManagedApplication)).scalars().all()
                apps = [app for app in rows if app.health_check_json and app.health_check_json != "{}"]
                await asyncio.gather(*(asyncio.to_thread(check_app, app) for app in apps))
            finally:
                db.close()
        except Exception:
            # 個別チェック失敗はrun()が結果化する。DB等の一時障害でもループを止めない。
            logger.exception("application health-check loop failed")
        await asyncio.sleep(15)
