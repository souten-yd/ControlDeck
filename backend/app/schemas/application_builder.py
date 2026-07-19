from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApplicationProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4000)
    workflow_id: int | None = Field(default=None, ge=1)
    spec: dict[str, Any] | None = None


class ApplicationProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    spec: dict[str, Any] | None = None


class ApplicationValidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    workflow_id: int | None = Field(default=None, ge=1)
    workflow_version_id: int | None = Field(default=None, ge=1)
    target: str = Field(default="csharp", pattern="^(csharp|cpp)$")


class WorkflowApplicationCreate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    source: str = Field(default="draft", pattern="^(draft|published)$")


class ApplicationInfoV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    displayName: str = ""
    description: str = ""
    applicationType: str = "web"
    authentication: str = "local"
    database: str = "none"


class TargetProfileV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    platforms: list[str]
    framework: str


class ApplicationSpecV1(BaseModel):
    """未知fieldを許容し、後続schema versionの情報をround-tripで保持する。"""

    model_config = ConfigDict(extra="allow")

    schemaVersion: int = Field(default=1, ge=1)
    application: ApplicationInfoV1
    theme: dict[str, Any] = Field(default_factory=dict)
    navigation: dict[str, Any] = Field(default_factory=dict)
    pages: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    apiEndpoints: list[dict[str, Any]] = Field(default_factory=list)
    backgroundJobs: list[dict[str, Any]] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[Any] = Field(default_factory=list)
    targets: list[TargetProfileV1] = Field(default_factory=list)
