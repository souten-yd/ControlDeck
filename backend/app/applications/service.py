"""アプリ登録・検証・実行制御のサービス層。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import psutil

from app.applications import systemd as sd
from app.config import app_logs_dir
from app.models import ManagedApplication
from app.schemas.apps import AppCreate, AppOut, AppRuntime, AppUpdate
from app.security.crypto import decrypt_text, encrypt_text, mask_env

# CPU% 計測用のプロセスキャッシュ（連続ポーリング間で有効な値を得るため）
_proc_cache: dict[int, psutil.Process] = {}


class AppValidationError(ValueError):
    pass


def _require_file(path: str | None, label: str, *, executable: bool = False) -> str:
    if not path:
        raise AppValidationError(f"{label}を指定してください")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise AppValidationError(f"{label}は絶対パスで指定してください: {path}")
    resolved = p.resolve()
    if not resolved.is_file():
        raise AppValidationError(f"{label}が存在しません: {resolved}")
    if executable and not shutil.os.access(resolved, shutil.os.X_OK):
        raise AppValidationError(f"{label}に実行権限がありません: {resolved}")
    return str(resolved)


def _require_dir(path: str | None, label: str) -> str | None:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise AppValidationError(f"{label}は絶対パスで指定してください: {path}")
    resolved = p.resolve()
    if not resolved.is_dir():
        raise AppValidationError(f"{label}が存在しません: {resolved}")
    return str(resolved)


def build_exec_argv(app: ManagedApplication) -> list[str]:
    args: list[str] = json.loads(app.arguments_json or "[]")
    if app.application_type == "python_script":
        return [app.python_path, app.script_path, *args]
    if app.application_type == "shell_script":
        return ["/bin/bash", app.script_path, *args]
    if app.application_type == "executable":
        return [app.executable_path, *args]
    raise AppValidationError(f"実行対象の種類が不正です: {app.application_type}")


def validate_fields(data: AppCreate) -> None:
    if data.application_type == "url_shortcut":
        url = (data.url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise AppValidationError("URL は http:// または https:// で指定してください")
        return  # URL ショートカットはプロセスではないため以降の検証は不要
    if data.application_type == "python_script":
        _require_file(data.python_path, "Python 実行ファイル", executable=True)
        _require_file(data.script_path, "スクリプト")
    elif data.application_type == "shell_script":
        _require_file(data.script_path, "シェルスクリプト")
    elif data.application_type == "executable":
        _require_file(data.executable_path, "実行ファイル", executable=True)
    elif data.application_type == "systemd_service":
        name = data.systemd_unit_name or ""
        if not sd.UNIT_NAME_RE.match(name):
            raise AppValidationError(f"不正なユニット名です: {name}")
        if name.startswith(sd.UNIT_PREFIX):
            raise AppValidationError("Control Deck 管理ユニットは直接登録できません")
    _require_dir(data.working_directory, "作業ディレクトリ")
    for key in data.environment:
        if not sd.ENV_KEY_RE.match(key):
            raise AppValidationError(f"不正な環境変数名: {key}")


def env_warnings(env: dict[str, str]) -> list[str]:
    return [
        f"環境変数 {k} は動作へ広範な影響を与える可能性があります"
        for k in env
        if k in sd.DANGEROUS_ENV_KEYS
    ]


def get_environment(app: ManagedApplication) -> dict[str, str]:
    if not app.environment_json_encrypted:
        return {}
    try:
        return json.loads(decrypt_text(app.environment_json_encrypted))
    except Exception:
        return {}


def set_environment(app: ManagedApplication, env: dict[str, str]) -> None:
    app.environment_json_encrypted = encrypt_text(json.dumps(env)) if env else None


def sync_unit(app: ManagedApplication) -> None:
    """ManagedApplication からユニットファイルを生成・更新する。"""
    if app.application_type in ("systemd_service", "url_shortcut"):
        return  # 既存ユニット / URL ショートカットはユニット生成なし
    logs = app_logs_dir(app.id)
    content = sd.build_unit_content(
        name=app.name,
        exec_argv=build_exec_argv(app),
        working_directory=app.working_directory,
        environment=get_environment(app),
        restart_policy=app.restart_policy,
        stop_timeout_seconds=app.stop_timeout_seconds,
        stdout_path=logs / "stdout.log",
        stderr_path=logs / "stderr.log",
    )
    sd.write_unit(app.systemd_unit_name, content)
    sd.set_enabled(app.systemd_unit_name, app.auto_start)


def runtime_info(app: ManagedApplication) -> AppRuntime:
    if app.application_type == "url_shortcut":
        return AppRuntime(status="URL")  # プロセスではないので特別状態
    try:
        q = sd.query_status(app.systemd_unit_name)
    except Exception:
        return AppRuntime(status="UNKNOWN")
    cpu = None
    mem = None
    pid = q.get("pid")
    if pid:
        try:
            proc = _proc_cache.get(pid)
            if proc is None or not proc.is_running():
                proc = psutil.Process(pid)
                proc.cpu_percent(None)  # 初回サンプル
                _proc_cache[pid] = proc
                cpu = 0.0
            else:
                cpu = proc.cpu_percent(None)
            mem = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    mem += child.memory_info().rss
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
    return AppRuntime(
        status=q["status"],
        pid=pid,
        uptime_seconds=q.get("uptime_seconds"),
        started_at=q.get("started_at"),
        restart_count=q.get("restart_count", 0),
        cpu_percent=cpu,
        memory_bytes=mem,
    )


def to_out(app: ManagedApplication) -> AppOut:
    env = get_environment(app)
    return AppOut(
        id=app.id,
        name=app.name,
        description=app.description,
        application_type=app.application_type,
        icon_path=app.icon_path,
        working_directory=app.working_directory,
        executable_path=app.executable_path,
        script_path=app.script_path,
        python_path=app.python_path,
        url=app.url,
        arguments=json.loads(app.arguments_json or "[]"),
        environment_masked=mask_env(env),
        auto_start=app.auto_start,
        restart_policy=app.restart_policy,
        stop_timeout_seconds=app.stop_timeout_seconds,
        systemd_unit_name=app.systemd_unit_name,
        created_at=app.created_at,
        updated_at=app.updated_at,
        runtime=runtime_info(app),
        env_warnings=env_warnings(env),
    )
