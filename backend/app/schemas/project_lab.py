"""Project Labの宣言manifest。実行は後続Phaseで、このschemaでは候補だけを定義する。"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    label: str = Field(min_length=1, max_length=100)
    type: Literal["cli", "web", "static_html", "test", "artifact"]
    command: list[str] = Field(default_factory=list, max_length=64)
    cwd: str = Field(default=".", max_length=512)
    environment: dict[str, str] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list, max_length=32)
    artifacts: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 1024 or "\x00" in item for item in value):
            raise ValueError("commandは空要素を含まないargv配列で指定してください")
        if value:
            executable = value[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
            option = value[1].lower() if len(value) > 1 else ""
            if (executable in {"sh", "bash", "zsh", "fish"} and option in {"-c", "-lc"}) or (
                executable in {"cmd", "cmd.exe"} and option in {"/c", "/k"}
            ) or (executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"} and option in {"-command", "-c"}):
                raise ValueError("shell文字列の実行は禁止です。実行fileと引数を直接argvへ指定してください")
            secret_arg = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)\s*[:=]")
            if any(secret_arg.search(item) for item in value):
                raise ValueError("秘密値をcommandへ直書きせずsecret_refsを使用してください")
        return value

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        if value.startswith(("/", "~")) or ".." in value.replace("\\", "/").split("/"):
            raise ValueError("cwdはproject内の相対pathで指定してください")
        return value or "."

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        secret_pattern = re.compile(r"(secret|password|passwd|token|api[_-]?key|private[_-]?key)", re.I)
        for key, item in value.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key):
                raise ValueError(f"environment名が不正です: {key}")
            if secret_pattern.search(key) or len(item) > 2048 or "\x00" in item:
                raise ValueError("秘密値はenvironmentへ直書きせずsecret_refsを使用してください")
        return value

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_refs(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", item) for item in value):
            raise ValueError("secret_refsはSecret名だけを指定してください")
        return value

    @field_validator("artifacts")
    @classmethod
    def validate_artifacts(cls, value: list[str]) -> list[str]:
        for item in value:
            normalized = item.replace("\\", "/")
            if not item or item.startswith(("/", "~")) or ".." in normalized.split("/"):
                raise ValueError("artifact globはproject内の相対patternで指定してください")
        return value


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(alias="schemaVersion")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    profiles: list[ProjectProfile] = Field(default_factory=list, max_length=32)
