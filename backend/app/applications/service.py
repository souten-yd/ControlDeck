"""アプリ登録・検証・実行制御のサービス層。"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import psutil

from app.applications import systemd as sd
from app.config import app_logs_dir
from app.models import ManagedApplication
from app.schemas.apps import AppCreate, AppOut, AppRuntime, AppUpdate, HealthCheckConfig
from app.security.crypto import decrypt_text, encrypt_text, mask_env

# CPU% 計測用のプロセスキャッシュ（連続ポーリング間で有効な値を得るため）
_proc_cache: dict[int, psutil.Process] = {}
logger = logging.getLogger(__name__)


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
    has_code = bool((data.code or "").strip())
    if data.application_type == "python_script":
        _require_file(data.python_path, "Python 実行ファイル", executable=True)
        if not has_code:
            _require_file(data.script_path, "スクリプト")
    elif data.application_type == "shell_script":
        if not has_code:
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
    health = data.health_check
    if health.type == "tcp" and (not health.host.strip() or health.port is None):
        raise AppValidationError("TCPヘルスチェックにはホストとポートが必要です")
    if health.type == "http" and not health.url.startswith(("http://", "https://")):
        raise AppValidationError("HTTPヘルスチェックには http:// または https:// のURLが必要です")
    if health.type == "file":
        from app.files import service as files

        try:
            files.resolve(health.path, must_exist=False)
        except (OSError, files.FileAccessError) as e:
            raise AppValidationError(str(e)) from e
    if health.type == "command":
        from app.config import get_config

        if not health.command_id or health.command_id not in get_config().applications.health_commands:
            raise AppValidationError("登録済みの許可コマンドを選択してください")


def is_managed_code(app: ManagedApplication) -> bool:
    """script_path が Control Deck 管理のインラインコードファイルか。"""
    from app.config import app_scripts_dir

    if not app.script_path:
        return False
    try:
        return Path(app.script_path).resolve().parent == app_scripts_dir().resolve()
    except OSError:
        return False


def write_app_code(app: ManagedApplication, code: str) -> None:
    """インラインコードを data_dir/scripts へ保存し、script_path に設定する。"""
    from app.config import app_scripts_dir

    ext = "py" if app.application_type == "python_script" else "sh"
    path = app_scripts_dir() / f"app-{app.id}.{ext}"
    path.write_text(code, encoding="utf-8")
    path.chmod(0o700)
    app.script_path = str(path)


def read_app_code(app: ManagedApplication) -> str | None:
    if not is_managed_code(app):
        return None
    try:
        return Path(app.script_path).read_text(encoding="utf-8")
    except OSError:
        return None


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


def get_health_check(app: ManagedApplication) -> HealthCheckConfig:
    try:
        return HealthCheckConfig.model_validate(json.loads(app.health_check_json or "{}"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return HealthCheckConfig()


def set_health_check(app: ManagedApplication, config: HealthCheckConfig) -> None:
    app.health_check_json = json.dumps(config.model_dump(), ensure_ascii=False)


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


def runtime_info(app: ManagedApplication, *, include_health: bool = True) -> AppRuntime:
    if app.application_type == "url_shortcut":
        return AppRuntime(status="URL")  # プロセスではないので特別状態
    try:
        q = sd.query_status(app.systemd_unit_name)
    except Exception:
        return AppRuntime(status="UNKNOWN")
    cpu = None
    mem = None
    gpu_percent = None
    vram_bytes = None
    ports: set[int] = set()
    pid = q.get("pid")
    if pid:
        process_ids = {int(pid)}
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
            _collect_listen_ports(proc, ports)
            for child in proc.children(recursive=True):
                try:
                    process_ids.add(child.pid)
                    mem += child.memory_info().rss
                    _collect_listen_ports(child, ports)
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
        try:
            from app.applications import gpu_usage

            # app ID + MainPIDでsampling世代を分離し、process再起動時のDRM counterを
            # 前のprocessと差分計算しない。
            gpu_percent, vram_bytes = gpu_usage.collect(process_ids, scope_id=f"{app.id}:{pid}")
        except Exception as exc:
            # Kernel/driverがfdinfo統計を公開しない環境ではN/Aへ縮退する。
            logger.debug("application GPU usage unavailable: app_id=%s error=%s", app.id, type(exc).__name__)
            gpu_percent, vram_bytes = None, None
    health = None
    status = q["status"]
    if include_health and get_health_check(app).type != "none":
        from app.applications import health as app_health

        health = app_health.cached(app.id)
        if status == "RUNNING" and health is not None and not health.ok:
            status = "DEGRADED"
    return AppRuntime(
        status=status,
        pid=pid,
        uptime_seconds=q.get("uptime_seconds"),
        started_at=q.get("started_at"),
        restart_count=q.get("restart_count", 0),
        cpu_percent=cpu,
        memory_bytes=mem,
        gpu_percent=gpu_percent,
        vram_bytes=vram_bytes,
        listening_ports=sorted(ports),
        health=health,
    )


def _collect_listen_ports(proc: "psutil.Process", ports: set[int]) -> None:
    """プロセスが LISTEN している TCP ポートを収集する（Web ボタン用）。"""
    try:
        conns_fn = getattr(proc, "net_connections", None) or proc.connections
        for c in conns_fn(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                ports.add(c.laddr.port)
    except (psutil.Error, OSError):
        pass


def to_out(app: ManagedApplication) -> AppOut:
    env = get_environment(app)
    return AppOut(
        id=app.id,
        name=app.name,
        description=app.description,
        application_type=app.application_type,
        # 保存先の実パスは API へ露出しない。認証・権限確認付き配信 URL のみ返す。
        icon_path=(f"/api/v1/apps/{app.id}/icon?v={int(app.updated_at.timestamp())}" if app.icon_path else None),
        working_directory=app.working_directory,
        executable_path=app.executable_path,
        script_path=app.script_path,
        python_path=app.python_path,
        url=app.url,
        web_port=app.web_port,
        arguments=json.loads(app.arguments_json or "[]"),
        environment_masked=mask_env(env),
        auto_start=app.auto_start,
        restart_policy=app.restart_policy,
        stop_timeout_seconds=app.stop_timeout_seconds,
        health_check=get_health_check(app),
        systemd_unit_name=app.systemd_unit_name,
        created_at=app.created_at,
        updated_at=app.updated_at,
        runtime=runtime_info(app),
        env_warnings=env_warnings(env),
        # SearXNG は API 利用時にサーバー側でオンデマンド起動・アイドル停止するため、
        # ユーザー操作対象から外す（Apps 画面では非表示）。
        system_managed=app.name.strip().lower() == "searxng",
    )
