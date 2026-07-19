from __future__ import annotations

from typing import Any, Literal

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


class ApplicationPatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: Literal["add", "remove", "replace", "move"]
    path: str = Field(min_length=1, max_length=2048)
    from_path: str | None = Field(default=None, alias="from", max_length=2048)
    value: Any = None


class ApplicationPatchPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    patches: list[ApplicationPatchOperation] = Field(min_length=1, max_length=200)


class ApplicationPatchApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_checksum: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    patches: list[ApplicationPatchOperation] = Field(min_length=1, max_length=200)


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


class ComponentLockV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure: bool = False
    binding: bool = False
    style: bool = False
    position: bool = False
    content: bool = False


class SemanticComponentV1(BaseModel):
    """Framework名を含まない、generator間で共有するUI部品。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    properties: dict[str, Any] = Field(default_factory=dict)
    binding: str | dict[str, Any] | None = None
    events: dict[str, Any] = Field(default_factory=dict)
    responsive: dict[str, Any] = Field(default_factory=dict)
    locked: ComponentLockV1 = Field(default_factory=ComponentLockV1)
    children: list["SemanticComponentV1"] = Field(default_factory=list)


class ApplicationPageV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=128)
    title: str = ""
    description: str = ""
    root: SemanticComponentV1 | None = None


class ApplicationSpecV1(BaseModel):
    """未知fieldを許容し、後続schema versionの情報をround-tripで保持する。"""

    model_config = ConfigDict(extra="allow")

    schemaVersion: int = Field(default=1, ge=1)
    application: ApplicationInfoV1
    theme: dict[str, Any] = Field(default_factory=dict)
    navigation: dict[str, Any] = Field(default_factory=dict)
    pages: list[ApplicationPageV1] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    apiEndpoints: list[dict[str, Any]] = Field(default_factory=list)
    backgroundJobs: list[dict[str, Any]] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[Any] = Field(default_factory=list)
    targets: list[TargetProfileV1] = Field(default_factory=list)
