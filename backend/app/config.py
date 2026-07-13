"""アプリ設定。config/config.yaml（または CONTROL_DECK_CONFIG）を読み込む。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class SecurityConfig(BaseModel):
    session_timeout_minutes: int = 480
    allow_arbitrary_commands: bool = False
    # HTTPS リバースプロキシ配下で true にする（Cookie に Secure を付与）
    secure_cookies: bool = False
    # 管理者に二要素認証を推奨する（UI にバナー表示）
    require_totp_for_admin: bool = False


class FilesConfig(BaseModel):
    allowed_roots: list[str] = []
    max_upload_size_gb: int = 100


class TerminalConfig(BaseModel):
    enabled: bool = True
    shell: str = "/bin/bash"
    max_sessions: int = 10


class MonitoringConfig(BaseModel):
    interval_seconds: float = 2.0
    raw_retention_hours: int = 24
    minute_retention_days: int = 30


class LogsConfig(BaseModel):
    retention_days: int = 30
    rotate_size_mb: int = 100
    rotate_generations: int = 10
    audit_retention_days: int = 180


class UIConfig(BaseModel):
    app_name: str = "Ubuntu Control Deck"
    accent_color: str = "#3b82f6"
    default_theme: str = "system"
    metric_refresh_seconds: int = 2


class Config(BaseModel):
    server: ServerConfig = ServerConfig()
    security: SecurityConfig = SecurityConfig()
    files: FilesConfig = FilesConfig()
    terminal: TerminalConfig = TerminalConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    logs: LogsConfig = LogsConfig()
    ui: UIConfig = UIConfig()
    data_dir: str = "~/.local/share/control-deck"
    # GitHub 管理でクローンするリポジトリの格納先
    git_apps_dir: str = "~/ControlDeckApps"


def _config_path() -> Path | None:
    env = os.environ.get("CONTROL_DECK_CONFIG")
    if env:
        return Path(env)
    candidate = REPO_ROOT / "config" / "config.yaml"
    if candidate.exists():
        return candidate
    user_conf = Path.home() / ".config" / "control-deck" / "config.yaml"
    if user_conf.exists():
        return user_conf
    return None


@lru_cache
def get_config() -> Config:
    path = _config_path()
    if path and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Config.model_validate(raw)
    return Config()


def data_dir() -> Path:
    d = Path(get_config().data_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_logs_dir(app_id: int) -> Path:
    d = data_dir() / "logs" / str(app_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def icons_dir() -> Path:
    d = data_dir() / "icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_scripts_dir() -> Path:
    """インラインコードで登録したアプリのスクリプト保存先。"""
    d = data_dir() / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_url() -> str:
    override = os.environ.get("CONTROL_DECK_DB_URL")
    if override:
        return override
    return f"sqlite:///{data_dir() / 'control-deck.db'}"
