from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ApplicationType = Literal["python_script", "shell_script", "executable", "systemd_service", "url_shortcut"]
RestartPolicy = Literal["no", "on-failure", "always", "on-success"]


class AppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    application_type: ApplicationType
    working_directory: str | None = None
    executable_path: str | None = None
    script_path: str | None = None
    python_path: str | None = None
    url: str | None = None
    arguments: list[str] = []
    environment: dict[str, str] = {}
    auto_start: bool = False
    restart_policy: RestartPolicy = "no"
    stop_timeout_seconds: int = Field(default=20, ge=1, le=600)
    # systemd_service タイプ用（既存ユーザーユニット名）
    systemd_unit_name: str | None = None


class AppUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    working_directory: str | None = None
    executable_path: str | None = None
    script_path: str | None = None
    python_path: str | None = None
    arguments: list[str] | None = None
    environment: dict[str, str] | None = None
    auto_start: bool | None = None
    restart_policy: RestartPolicy | None = None
    stop_timeout_seconds: int | None = Field(default=None, ge=1, le=600)


class AppRuntime(BaseModel):
    status: str
    pid: int | None = None
    uptime_seconds: float | None = None
    started_at: str | None = None
    restart_count: int = 0
    cpu_percent: float | None = None
    memory_bytes: int | None = None


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
    arguments: list[str]
    environment_masked: dict[str, str]
    auto_start: bool
    restart_policy: str
    stop_timeout_seconds: int
    systemd_unit_name: str
    created_at: datetime
    updated_at: datetime
    runtime: AppRuntime
    env_warnings: list[str] = []
