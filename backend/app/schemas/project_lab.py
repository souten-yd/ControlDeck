"""Project Labの宣言manifest。実行は後続Phaseで、このschemaでは候補だけを定義する。"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_argv(value: list[str], *, what: str) -> list[str]:
    """argv配列の検証（commandとwatch.buildで共通）。shell文字列・秘密の直書きを断る。"""
    if any(not item or len(item) > 1024 or "\x00" in item for item in value):
        raise ValueError(f"{what}は空要素を含まないargv配列で指定してください")
    if value:
        executable = value[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        option = value[1].lower() if len(value) > 1 else ""
        if (executable in {"sh", "bash", "zsh", "fish"} and option in {"-c", "-lc"}) or (
            executable in {"cmd", "cmd.exe"} and option in {"/c", "/k"}
        ) or (executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"} and option in {"-command", "-c"}):
            raise ValueError("shell文字列の実行は禁止です。実行fileと引数を直接argvへ指定してください")
        secret_arg = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)\s*[:=]")
        if any(secret_arg.search(item) for item in value):
            raise ValueError(f"秘密値を{what}へ直書きせずsecret_refsを使用してください")
    return value


def _validate_relative_globs(value: list[str]) -> list[str]:
    for item in value:
        normalized = item.replace("\\", "/")
        if not item or len(item) > 512 or "\x00" in item or item.startswith(("/", "~")) \
                or ".." in normalized.split("/"):
            raise ValueError("globはproject内の相対patternで指定してください（..・絶対pathは不可）")
    return value


class ProjectWatch(BaseModel):
    """見張りモード（ライブ確認）。保存のたびに build → 実行し直す。

    pathsとignoreはproject直下からの相対glob。`/`を含まないpatternは、どの深さのファイル名にも当たる。
    `.controldeck/`・`.git/`・`node_modules/`・`__pycache__/`・`build/` は設定にかかわらず見張らない。
    mode: restart=ビルドしてからcommandを起動し直す（Webサーバー）、rerun=ビルドしてからcommandを
    最後まで流し直す（スクリプト・テスト・C++）、native=見張らない（Viteなどアプリ自身が反映する）。
    省略したmodeはwebならrestart、それ以外はrerun。
    """

    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=lambda: ["**/*"], max_length=32)
    ignore: list[str] = Field(default_factory=list, max_length=64)
    build: list[str] = Field(default_factory=list, max_length=64)
    mode: Literal["restart", "rerun", "native"] | None = None
    debounce_ms: int = Field(default=400, ge=100, le=5000)

    @field_validator("paths", "ignore")
    @classmethod
    def validate_globs(cls, value: list[str]) -> list[str]:
        return _validate_relative_globs(value)

    @field_validator("build")
    @classmethod
    def validate_build(cls, value: list[str]) -> list[str]:
        return _validate_argv(value, what="build")


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

    watch: ProjectWatch | None = None

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        return _validate_argv(value, what="command")

    @model_validator(mode="after")
    def validate_watch(self) -> "ProjectProfile":
        if self.watch is not None:
            if self.type not in {"cli", "test", "web"}:
                raise ValueError("watchはcli / test / web profileだけに指定できます")
            if self.watch.mode is None:
                self.watch.mode = "restart" if self.type == "web" else "rerun"
        return self

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


class ProjectRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)


class ProjectLabSettingsBody(BaseModel):
    """previewの挙動設定。将来項目を足しても既存clientを壊さないよう任意fieldにする。"""

    model_config = ConfigDict(extra="forbid")

    allow_external_preview: bool | None = None
    run_memory_max_mb: int | None = Field(default=None, ge=512, le=16384)


class ProjectFileRunCreate(BaseModel):
    """manifest不要の単体file実行。pathはproject内の相対pathだけを受け付ける。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class ProjectPublishBody(BaseModel):
    """静的サイトの公開指示。

    visibility に既定値は置かない。押した側が毎回選ぶ——「前回のまま public」で
    出てしまう事故を、既定値で作らない。
    """

    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1, max_length=100)
    visibility: Literal["public", "private"]
    directory: str | None = Field(default=None, max_length=200)
