from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ApplicationType = Literal["python_script", "shell_script", "executable", "systemd_service", "url_shortcut"]
RestartPolicy = Literal["no", "on-failure", "always", "on-success"]
HealthCheckType = Literal["none", "process", "tcp", "http", "file", "command"]


class HealthCheckConfig(BaseModel):
    type: HealthCheckType = "none"
    host: str = "127.0.0.1"
    port: int | None = Field(default=None, ge=1, le=65535)
    url: str = ""
    expected_status: int = Field(default=200, ge=100, le=599)
    body_contains: str = Field(default="", max_length=500)
    path: str = ""
    command_id: str = Field(default="", max_length=64)
    timeout_seconds: float = Field(default=3, ge=0.2, le=30)


class HealthCheckResult(BaseModel):
    ok: bool
    message: str
    checked_at: str
    latency_ms: float


class AppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    application_type: ApplicationType
    working_directory: str | None = None
    executable_path: str | None = None
    script_path: str | None = None
    python_path: str | None = None
    url: str | None = None
    # インラインコード（指定時は data_dir/scripts へ保存し script_path に設定）
    code: str | None = None
    arguments: list[str] = []
    environment: dict[str, str] = {}
    auto_start: bool = False
    restart_policy: RestartPolicy = "no"
    stop_timeout_seconds: int = Field(default=20, ge=1, le=600)
    # systemd_service タイプ用（既存ユーザーユニット名）
    systemd_unit_name: str | None = None
    # Web ボタンで開くポート
    web_port: int | None = Field(default=None, ge=1, le=65535)
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)


class AppUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    working_directory: str | None = None
    executable_path: str | None = None
    script_path: str | None = None
    python_path: str | None = None
    url: str | None = None
    code: str | None = None
    arguments: list[str] | None = None
    environment: dict[str, str] | None = None
    auto_start: bool | None = None
    restart_policy: RestartPolicy | None = None
    stop_timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    # Web ボタンで開くポート（null で未設定に戻す）
    web_port: int | None = Field(default=None, ge=1, le=65535)
    health_check: HealthCheckConfig | None = None


class AppRuntime(BaseModel):
    status: str
    pid: int | None = None
    uptime_seconds: float | None = None
    started_at: str | None = None
    restart_count: int = 0
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    gpu_percent: float | None = None
    vram_bytes: int | None = None
    # プロセスツリーが LISTEN している TCP ポート（Web ボタン用）
    listening_ports: list[int] = []
    health: HealthCheckResult | None = None


class AppOut(BaseModel):
    id: int
    name: str
    description: str
    application_type: str
    icon_path: str | None
    working_directory: str | None
    executable_path: str | None
    script_path: str | None
    python_path: str | None
    url: str | None = None
    web_port: int | None = None
    arguments: list[str]
    environment_masked: dict[str, str]
    auto_start: bool
    restart_policy: str
    stop_timeout_seconds: int
    health_check: HealthCheckConfig
    systemd_unit_name: str
    created_at: datetime
    updated_at: datetime
    runtime: AppRuntime
    env_warnings: list[str] = []
    # サーバー側が起動/停止を完全管理するインフラアプリ（SearXNG等）。UIは操作を出さない
    system_managed: bool = False
