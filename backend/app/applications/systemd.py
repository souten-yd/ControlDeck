"""systemd ユーザーユニットの生成と制御。

- ユニットは ~/.config/systemd/user/cdapp-{id}.service に生成する
- すべての subprocess は配列引数（shell=False）
- ユニットファイルへ埋め込む値はフィールドごとに検証・エスケープする
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

UNIT_PREFIX = "cdapp-"
UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9@_.\-]+\.service$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 上書きすると危険な環境変数（登録時に警告対象）
DANGEROUS_ENV_KEYS = {"LD_PRELOAD", "PYTHONPATH", "BASH_ENV", "ENV", "PROMPT_COMMAND"}

RESTART_POLICIES = {"no", "on-failure", "always", "on-success"}


def user_unit_dir() -> Path:
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    return d


def unit_name_for(app_id: int) -> str:
    return f"{UNIT_PREFIX}{app_id}.service"


def _escape_exec_arg(arg: str) -> str:
    """ExecStart の 1 引数を systemd のクォート規則でエスケープする。"""
    if "\n" in arg or "\x00" in arg:
        raise ValueError("引数に改行・NUL 文字は使用できません")
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
    # specifier / 変数展開を無効化
    escaped = escaped.replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def _escape_env_value(value: str) -> str:
    if "\n" in value or "\x00" in value:
        raise ValueError("環境変数の値に改行・NUL 文字は使用できません")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return escaped


def _sanitize_description(name: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", name)[:200]


def build_unit_content(
    *,
    name: str,
    exec_argv: list[str],
    working_directory: str | None,
    environment: dict[str, str],
    restart_policy: str,
    stop_timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
) -> str:
    if not exec_argv:
        raise ValueError("実行コマンドが空です")
    if restart_policy not in RESTART_POLICIES:
        raise ValueError(f"不正な再起動ポリシー: {restart_policy}")
    if not 1 <= stop_timeout_seconds <= 600:
        raise ValueError("停止タイムアウトは 1〜600 秒で指定してください")
    exec_path = Path(exec_argv[0])
    if not exec_path.is_absolute():
        raise ValueError("実行ファイルは絶対パスで指定してください")

    lines = [
        "[Unit]",
        f"Description=Control Deck: {_sanitize_description(name)}",
        "After=network-online.target",
        # 再起動ループ検出: 60 秒に 5 回失敗で起動を止める
        "StartLimitIntervalSec=60",
        "StartLimitBurst=5",
        "",
        "[Service]",
        "Type=exec",
    ]
    if working_directory:
        wd = Path(working_directory)
        if not wd.is_absolute():
            raise ValueError("作業ディレクトリは絶対パスで指定してください")
        lines.append(f"WorkingDirectory={_escape_exec_arg(str(wd))[1:-1]}")
    for key, value in environment.items():
        if not ENV_KEY_RE.match(key):
            raise ValueError(f"不正な環境変数名: {key}")
        lines.append(f'Environment="{key}={_escape_env_value(value)}"')
    lines += [
        "ExecStart=" + " ".join(_escape_exec_arg(a) for a in exec_argv),
        f"Restart={restart_policy}",
        "RestartSec=3",
        f"TimeoutStopSec={stop_timeout_seconds}",
        "KillSignal=SIGTERM",
        f"StandardOutput=append:{stdout_path}",
        f"StandardError=append:{stderr_path}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def _systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=check,
    )


def write_unit(unit_name: str, content: str) -> Path:
    if not UNIT_NAME_RE.match(unit_name) or not unit_name.startswith(UNIT_PREFIX):
        raise ValueError(f"不正なユニット名: {unit_name}")
    path = user_unit_dir() / unit_name
    path.write_text(content, encoding="utf-8")
    _systemctl("daemon-reload")
    return path


def remove_unit(unit_name: str) -> None:
    if not unit_name.startswith(UNIT_PREFIX):
        raise ValueError(f"Control Deck 管理外のユニットは削除できません: {unit_name}")
    _systemctl("disable", unit_name)
    path = user_unit_dir() / unit_name
    if path.exists():
        path.unlink()
    _systemctl("daemon-reload")


def start(unit_name: str) -> tuple[bool, str]:
    r = _systemctl("start", unit_name)
    return r.returncode == 0, r.stderr.strip()


def stop(unit_name: str) -> tuple[bool, str]:
    r = _systemctl("stop", unit_name)
    return r.returncode == 0, r.stderr.strip()


def restart(unit_name: str) -> tuple[bool, str]:
    r = _systemctl("restart", unit_name)
    return r.returncode == 0, r.stderr.strip()


def kill(unit_name: str) -> tuple[bool, str]:
    r = _systemctl("kill", "-s", "SIGKILL", unit_name)
    return r.returncode == 0, r.stderr.strip()


def reset_failed(unit_name: str) -> None:
    _systemctl("reset-failed", unit_name)


def set_enabled(unit_name: str, enabled: bool) -> None:
    _systemctl("enable" if enabled else "disable", unit_name)


def query_status(unit_name: str) -> dict:
    """systemd の状態をアプリ状態へマッピングして返す。"""
    r = _systemctl(
        "show",
        unit_name,
        "--property=ActiveState,SubState,MainPID,ExecMainStatus,NRestarts,LoadState,UnitFileState",
    )
    props: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k] = v

    active = props.get("ActiveState", "")
    sub = props.get("SubState", "")
    load = props.get("LoadState", "")
    pid = int(props.get("MainPID") or 0)

    if load in ("not-found", "bad-setting", "error", "masked"):
        status = "UNKNOWN"
    elif active == "active":
        status = "RUNNING"
    elif active == "activating":
        status = "STARTING"
    elif active == "deactivating":
        status = "STOPPING"
    elif active == "failed":
        status = "FAILED"
    elif active == "inactive":
        status = "STOPPED"
    elif active == "reloading":
        status = "RESTARTING"
    else:
        status = "UNKNOWN"

    started_at: str | None = None
    uptime_seconds: float | None = None
    if pid > 0:
        try:
            import psutil

            create = psutil.Process(pid).create_time()
            uptime_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - create)
            started_at = datetime.fromtimestamp(create, tz=timezone.utc).isoformat()
        except Exception:
            pass

    return {
        "status": status,
        "sub_state": sub,
        "pid": pid or None,
        "exit_code": int(props.get("ExecMainStatus") or 0),
        "restart_count": int(props.get("NRestarts") or 0),
        "enabled": props.get("UnitFileState", "") == "enabled",
        "started_at": started_at,
        "uptime_seconds": uptime_seconds,
    }


def systemd_user_available() -> bool:
    try:
        r = _systemctl("is-system-running")
        return r.returncode in (0, 1)  # degraded でも利用可能
    except Exception:
        return False
