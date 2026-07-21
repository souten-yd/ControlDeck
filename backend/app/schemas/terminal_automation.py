from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SnippetVariable(BaseModel):
    name: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(default="", max_length=80)
    default: str = Field(default="", max_length=4096)
    required: bool = False


class SnippetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=96)
    description: str = Field(default="", max_length=320)
    content: str = Field(min_length=1, max_length=65_536)
    variables: list[SnippetVariable] = Field(default_factory=list, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("名前を入力してください")
        return cleaned

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(len(value) > 32 for value in cleaned):
            raise ValueError("タグは32文字以内です")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("タグが重複しています")
        return cleaned

    @field_validator("variables")
    @classmethod
    def unique_variables(cls, values: list[SnippetVariable]) -> list[SnippetVariable]:
        names = [value.name for value in values]
        if len(set(names)) != len(names):
            raise ValueError("変数名が重複しています")
        return values


class SnippetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=96)
    description: str | None = Field(default=None, max_length=320)
    content: str | None = Field(default=None, min_length=1, max_length=65_536)
    variables: list[SnippetVariable] | None = Field(default=None, max_length=16)
    tags: list[str] | None = Field(default=None, max_length=8)


class ComposeRequest(BaseModel):
    snippet_ids: list[int] = Field(min_length=1, max_length=20)
    parameters: dict[str, str] = Field(default_factory=dict)
    mode: Literal["detached", "terminal"] = "detached"
    target_session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}$")
    working_directory: str = Field(default="", max_length=1024)
    condition_type: Literal["always", "shell_ready", "program_equals"] = "always"
    condition_value: str = Field(default="", max_length=128)
    timeout_seconds: int = Field(default=3600, ge=1, le=86_400)

    @field_validator("snippet_ids")
    @classmethod
    def unique_snippet_ids(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values) or len(set(values)) != len(values):
            raise ValueError("Snippet IDが不正または重複しています")
        return values

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 32:
            raise ValueError("parameterが多すぎます")
        for name, value in values.items():
            if len(name) > 48 or len(value) > 4096 or "\x00" in value:
                raise ValueError("parameterが不正です")
        return values

    @model_validator(mode="after")
    def validate_target(self) -> "ComposeRequest":
        if self.mode == "terminal" and not self.target_session_id:
            raise ValueError("Send to sessionには対象Terminalが必要です")
        if self.condition_type == "program_equals" and not self.condition_value.strip():
            raise ValueError("program条件を入力してください")
        return self


class ScheduleCreate(ComposeRequest):
    name: str = Field(min_length=1, max_length=128)
    recurrence: Literal["once", "daily", "weekly", "biweekly"] = "once"
    next_run_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    run_if_missed: bool = True

    @field_validator("next_run_at")
    @classmethod
    def aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone付き日時を指定してください")
        return value


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    snippet_ids: list[int] | None = Field(default=None, min_length=1, max_length=20)
    parameters: dict[str, str] | None = None
    mode: Literal["detached", "terminal"] | None = None
    target_session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}$")
    working_directory: str | None = Field(default=None, max_length=1024)
    condition_type: Literal["always", "shell_ready", "program_equals"] | None = None
    condition_value: str | None = Field(default=None, max_length=128)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    recurrence: Literal["once", "daily", "weekly", "biweekly"] | None = None
    next_run_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    run_if_missed: bool | None = None
    enabled: bool | None = None
