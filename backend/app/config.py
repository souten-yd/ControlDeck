"""アプリ設定。config/config.yaml（または CONTROL_DECK_CONFIG）を読み込む。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[2]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class SecurityConfig(BaseModel):
    session_timeout_minutes: int = 480
    allow_arbitrary_commands: bool = False
    # HTTPS リバースプロキシ配下で true にする（Cookie に Secure を付与）
    secure_cookies: bool = False
    # 旧設定。trueなら管理者へ二要素認証を必須化する。
    require_totp_for_admin: bool = False
    # optional / administrators / all。legacy require_totp_for_admin=trueも管理者必須として扱う。
    totp_requirement: Literal["optional", "administrators", "all"] = "optional"
    # 電源の即時操作・予約時に、ログイン済みsessionとは別にTOTP再認証を要求する。
    require_totp_for_power: bool = False
    # 直接接続元IPごとの共通保護。login等は別のより厳しい制限も併用する。
    api_rate_limit_per_minute: int = Field(default=5000, ge=60, le=100_000)
    download_rate_limit_per_minute: int = Field(default=300, ge=10, le=10_000)
    websocket_rate_limit_per_minute: int = Field(default=300, ge=10, le=10_000)


class FilesConfig(BaseModel):
    allowed_roots: list[str] = []
    max_upload_size_gb: int = 100
    trash_enabled: bool = True
    trash_retention_days: int = 30
    trash_max_size_gb: int = 10


class TerminalConfig(BaseModel):
    enabled: bool = True
    shell: str = "/bin/bash"
    max_sessions: int = 10


class ElectricityConfig(BaseModel):
    enabled: bool = True
    price_per_kwh_yen: float = 35.69
    psu_efficiency: float = 0.85
    persistence_interval_seconds: int = 600

    @field_validator("price_per_kwh_yen")
    @classmethod
    def _price_nonneg(cls, v: float) -> float:
        if v < 0:
            raise ValueError("price_per_kwh_yen は 0 以上である必要があります")
        return v

    @field_validator("psu_efficiency")
    @classmethod
    def _eff_range(cls, v: float) -> float:
        if not (0.50 <= v <= 1.00):
            raise ValueError("psu_efficiency は 0.50〜1.00 の範囲である必要があります")
        return v

    @field_validator("persistence_interval_seconds")
    @classmethod
    def _interval_range(cls, v: int) -> int:
        if not (60 <= v <= 3600):
            raise ValueError("persistence_interval_seconds は 60〜3600 の範囲である必要があります")
        return v


class MonitoringConfig(BaseModel):
    interval_seconds: float = 2.0
    raw_retention_hours: int = 24
    minute_retention_days: int = 30
    hour_retention_days: int = 365
    electricity: ElectricityConfig = ElectricityConfig()


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


class ApplicationBuilderConfig(BaseModel):
    # 空ならCONTROL_DECK_DOTNET、次にPATHを使う。設定時は解決済みの
    # executableだけを隔離buildのSDK allowlistとして受け入れる。
    dotnet_path: str | None = None


class HealthCommandDefinition(BaseModel):
    """ローカル管理者が明示的に許可した固定ヘルスチェックargv。"""

    label: str = Field(min_length=1, max_length=80)
    argv: list[str] = Field(min_length=1, max_length=32)

    @field_validator("argv")
    @classmethod
    def _fixed_argv(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item or len(item) > 4096 for item in value):
            raise ValueError("health command argvは空文字・NULを含まず各4096文字以下にしてください")
        if not Path(value[0]).is_absolute():
            raise ValueError("health commandの実行ファイルは絶対パスで指定してください")
        sensitive = ("password", "passwd", "secret", "token", "api_key", "authorization", "cookie", "bearer")
        if any(any(marker in item.casefold() for marker in sensitive) for item in value):
            raise ValueError("health command argvへ秘密値や認証情報を含めないでください")
        return value


class SystemServiceDefinition(BaseModel):
    """root所有catalogへ明示導入するsystem service操作定義。"""

    label: str = Field(min_length=1, max_length=80)
    unit: str = Field(pattern=r"^[A-Za-z0-9@_.-]+\.service$", max_length=128)
    actions: list[str] = Field(default_factory=lambda: ["start", "stop", "restart"], min_length=1, max_length=3)

    @field_validator("actions")
    @classmethod
    def _safe_actions(cls, value: list[str]) -> list[str]:
        allowed = {"start", "stop", "restart"}
        if len(set(value)) != len(value) or any(action not in allowed for action in value):
            raise ValueError("system service actionsは重複なしのstart/stop/restartだけです")
        return value


class ApplicationsConfig(BaseModel):
    health_commands: dict[str, HealthCommandDefinition] = Field(default_factory=dict)
    system_services: dict[str, SystemServiceDefinition] = Field(default_factory=dict)

    @field_validator("health_commands")
    @classmethod
    def _command_ids(cls, value: dict[str, HealthCommandDefinition]) -> dict[str, HealthCommandDefinition]:
        import re

        if len(value) > 64:
            raise ValueError("health commandは最大64件です")
        if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) is None for key in value):
            raise ValueError("health command IDは英小文字で始まる英数字・_-、最大64文字です")
        return value

    @field_validator("system_services")
    @classmethod
    def _system_service_ids(cls, value: dict[str, SystemServiceDefinition]) -> dict[str, SystemServiceDefinition]:
        import re

        if len(value) > 64:
            raise ValueError("system serviceは最大64件です")
        if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) is None for key in value):
            raise ValueError("system service IDは英小文字で始まる英数字・_-、最大64文字です")
        return value


class Config(BaseModel):
    server: ServerConfig = ServerConfig()
    security: SecurityConfig = SecurityConfig()
    files: FilesConfig = FilesConfig()
    terminal: TerminalConfig = TerminalConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    logs: LogsConfig = LogsConfig()
    ui: UIConfig = UIConfig()
    applications: ApplicationsConfig = ApplicationsConfig()
    application_builder: ApplicationBuilderConfig = ApplicationBuilderConfig()
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


def application_builds_dir() -> Path:
    """Application Builderが所有する隔離buildのsource／artifact root。"""
    d = data_dir() / "application-builds"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workflow_artifacts_dir() -> Path:
    """Workflow engineだけが所有するoffload済み成果物root。"""
    d = data_dir() / "workflow-artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_url() -> str:
    from app.database.runtime import normalized_database_url

    override = os.environ.get("CONTROL_DECK_DB_URL")
    if override:
        return normalized_database_url(override)
    return f"sqlite:///{data_dir() / 'control-deck.db'}"
