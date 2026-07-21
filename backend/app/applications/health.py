"""管理アプリのヘルスチェック。任意シェルは実行しない。"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore, Lock

import httpx
from sqlalchemy import select

from app.database import SessionLocal
from app.config import get_config
from app.files import service as files
from app.models import ManagedApplication
from app.schemas.apps import HealthCheckConfig, HealthCheckResult

_cache: dict[int, HealthCheckResult] = {}
_runtime_states: dict[int, str] = {}
_lock = Lock()
logger = logging.getLogger(__name__)

_HEALTH_UNIT_PREFIX = "control-deck-health-check-"
_SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_command_slots = BoundedSemaphore(4)


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


def command_catalog() -> list[dict[str, str]]:
    return [
        {"id": command_id, "label": definition.label}
        for command_id, definition in sorted(get_config().applications.health_commands.items())
    ]


def _run_allowed_command(command_id: str, timeout_seconds: float) -> tuple[bool, str]:
    definition = get_config().applications.health_commands.get(command_id)
    if definition is None:
        return False, "許可コマンドが見つかりません"
    if not _command_slots.acquire(timeout=timeout_seconds):
        return False, "許可コマンドの同時実行上限に達しました"
    try:
        return _execute_allowed_command(command_id, definition.label, definition.argv, timeout_seconds)
    finally:
        _command_slots.release()


def _execute_allowed_command(
    command_id: str, label: str, configured_argv: list[str], timeout_seconds: float,
) -> tuple[bool, str]:
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    if not systemd_run or not systemctl:
        return False, "systemd user managerを利用できません"
    executable = Path(configured_argv[0])
    try:
        resolved = executable.resolve(strict=True)
    except OSError:
        return False, "許可コマンドの実行ファイルが見つかりません"
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return False, "許可コマンドの実行ファイルに実行権限がありません"
    unit = f"{_HEALTH_UNIT_PREFIX}{uuid.uuid4().hex}.service"
    runtime_limit = max(1, int(timeout_seconds + 0.999))
    argv = [
        systemd_run, "--user", "--quiet", "--wait", "--collect", f"--unit={unit}",
        "--property=Type=exec", "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes",
        "--property=ProtectSystem=strict", "--property=ProtectHome=read-only",
        "--property=MemoryMax=256M", "--property=CPUQuota=100%", "--property=TasksMax=64",
        "--property=StandardOutput=null", "--property=StandardError=null",
        f"--property=RuntimeMaxSec={runtime_limit}s", "--working-directory=/",
        f"--setenv=PATH={_SAFE_PATH}", "--", str(resolved), *configured_argv[1:],
    ]
    try:
        completed = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout_seconds + 5, check=False,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(
            [systemctl, "--user", "stop", unit], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
        return False, f"許可コマンド「{label}」がタイムアウトしました"
    except OSError:
        return False, "許可コマンドを起動できません"
    if completed.returncode != 0:
        logger.info("health command failed: id=%s unit=%s exit=%d", command_id, unit, completed.returncode)
        return False, f"許可コマンド「{label}」が失敗しました（終了コード {completed.returncode}）"
    return True, f"許可コマンド「{label}」成功"


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
        if config.type == "command":
            ok, message = _run_allowed_command(config.command_id, config.timeout_seconds)
            return _result(ok, message, started)
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
                from app.applications import service as app_service

                states = await asyncio.gather(*(
                    asyncio.to_thread(app_service.runtime_info, app, include_health=False) for app in rows
                ))
                changes: list[tuple[ManagedApplication, str, str]] = []
                current_ids: set[int] = set()
                for app, runtime in zip(rows, states, strict=False):
                    current_ids.add(app.id)
                    status = str(runtime.status)
                    previous = _runtime_states.get(app.id)
                    _runtime_states[app.id] = status
                    if previous is not None and previous != status:
                        changes.append((app, previous, status))
                for removed_id in set(_runtime_states) - current_ids:
                    _runtime_states.pop(removed_id, None)
                if changes:
                    from app.workflows.engine import fire_system_triggers

                    for app, previous, status in changes:
                        await fire_system_triggers("systemd", {
                            "resource": app.name, "app": app.name, "app_id": app.id,
                            "unit": app.systemd_unit_name, "status": status, "previous_status": previous,
                        })
            finally:
                db.close()
        except Exception:
            # 個別チェック失敗はrun()が結果化する。DB等の一時障害でもループを止めない。
            logger.exception("application health-check loop failed")
        await asyncio.sleep(15)
