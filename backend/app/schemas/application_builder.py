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


class PlatformAdvisorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    platforms: list[Literal["web", "linux", "windows", "macos", "android", "ios"]] = Field(default_factory=lambda: ["web"], min_length=1, max_length=6)
    offline: bool = False
    local_files: bool = Field(default=False, alias="localFiles")
    tray: bool = False
    background: bool = False
    gpu: bool = False
    embedded_server: bool = Field(default=False, alias="embeddedServer")
    store: bool = False
    preferred_language: Literal["any", "csharp", "typescript", "rust", "dart", "kotlin", "cpp"] = Field(default="any", alias="preferredLanguage")
    prefer_native_feel: bool = Field(default=False, alias="preferNativeFeel")
    prefer_web_reuse: bool = Field(default=False, alias="preferWebReuse")
    prefer_small_size: bool = Field(default=False, alias="preferSmallSize")


class ApplicationPreflightBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    workflow_id: int | None = Field(default=None, ge=1)
    workflow_version_id: int | None = Field(default=None, ge=1)


class ApplicationSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target_id: str = Field(alias="targetId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")


class ApplicationBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target_id: str = Field(alias="targetId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    timeout_seconds: int = Field(default=900, alias="timeoutSeconds", ge=60, le=3600)


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


class ApplicationDesignProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=3, max_length=4000)
    scope: Literal["application", "page", "component", "mobile"] = "application"
    target_id: str | None = Field(default=None, max_length=128)
    mode: Literal["preserve", "balanced", "redesign"] = "balanced"
    base_url: str = Field(min_length=8, max_length=512)
    model: str = Field(min_length=1, max_length=256)


class ApplicationDesignProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    direction: Literal["simple", "balanced", "dense"]
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)
    rationale: list[str] = Field(default_factory=list, max_length=8)
    patches: list[ApplicationPatchOperation] = Field(min_length=1, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class ApplicationDesignProposalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[ApplicationDesignProposal] = Field(min_length=3, max_length=3)


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


class LlmRuntimeV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: Literal["none", "external", "embedded", "remote"] = "none"
    provider: Literal["ollama", "lmstudio", "openai-compatible", "controldeck"] | None = None
    bundleRuntime: bool = False
    baseUrlEnvironment: str = "LLM_BASE_URL"
    modelEnvironment: str = "LLM_MODEL"


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


class ApplicationApiEndpointV1(BaseModel):
    """Framework非依存のHTTP endpoint。handler codeは保存せずWorkflowへ結び付ける。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    method: Literal["POST"] = "POST"
    path: str = Field(
        min_length=1, max_length=512,
        pattern=r"^/(?:$|(?:[A-Za-z0-9._~-]+|\{[A-Za-z][A-Za-z0-9_]*\})(?:/(?:[A-Za-z0-9._~-]+|\{[A-Za-z][A-Za-z0-9_]*\}))*)$",
    )
    workflowId: int = Field(ge=1)
    mode: Literal["sync", "async"] = "sync"
    authentication: Literal["inherit", "anonymous"] = "inherit"
    requestSchema: dict[str, Any] = Field(default_factory=dict)
    responseSchema: dict[str, Any] = Field(default_factory=dict)
    timeoutSeconds: float = Field(default=120, ge=0.1, le=7200)


class ApplicationBackgroundJobV1(BaseModel):
    """Phase Cで生成するbackground jobの宣言。実行内容はWorkflowだけを参照する。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    workflowId: int = Field(ge=1)
    trigger: Literal["manual", "interval", "daily", "cron"] = "manual"
    schedule: str = Field(default="", max_length=256)
    timeZone: str = Field(default="UTC", min_length=1, max_length=64)
    input: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    timeoutSeconds: float = Field(default=7200, ge=0.1, le=7200)
    concurrencyPolicy: Literal["skip", "queue-one"] = "skip"
    catchUpPolicy: Literal["skip", "run-once"] = "run-once"


class ApplicationClientStateV1(BaseModel):
    """Browser-memory state declared independently from component bindings/events."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
    type: Literal["string", "integer", "number", "boolean", "object", "array"]
    initialValue: Any
    nullable: bool = False


class ApplicationQueryFilterV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    operator: Literal["eq", "ne", "contains", "starts-with", "gt", "gte", "lt", "lte", "is-null"]
    value: Any = None


class ApplicationQuerySortV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    direction: Literal["asc", "desc"] = "asc"


class ApplicationQueryV1(BaseModel):
    """Typed, read-only collection query executed by the generated browser UI."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
    source: Literal["entity", "api"] = "entity"
    entityId: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    endpointId: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    input: dict[str, Any] = Field(default_factory=dict)
    resultPath: str = Field(default="", max_length=256, pattern=r"^(?:[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*)?$")
    filters: list[ApplicationQueryFilterV1] = Field(default_factory=list, max_length=20)
    sort: list[ApplicationQuerySortV1] = Field(default_factory=list, max_length=3)
    pagination: Literal["none", "offset"] = "offset"
    limit: int = Field(default=20, ge=1, le=100)
    autoLoad: bool = True
    cachePolicy: Literal["network-only", "memory"] = "memory"
    staleTimeSeconds: int = Field(default=30, ge=0, le=3600)


class ApplicationEntityReferenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entityId: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    onDelete: Literal["restrict", "cascade", "set-null"] = "restrict"


class ApplicationEntityFieldV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    type: Literal["string", "integer", "number", "boolean", "datetime", "json"]
    nullable: bool = False
    default: Any = None
    hasDefault: bool = False
    maxLength: int | None = Field(default=None, ge=1, le=1_000_000)
    unique: bool = False
    indexed: bool = False
    reference: ApplicationEntityReferenceV1 | None = None


class ApplicationEntityCrudV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    operations: list[Literal["create", "read", "list", "update", "delete"]] = Field(
        default_factory=lambda: ["create", "read", "list", "update", "delete"],
        min_length=1, max_length=5,
    )
    basePath: str | None = Field(
        default=None, min_length=1, max_length=256,
        pattern=r"^/api/[A-Za-z][A-Za-z0-9_-]*(?:/[A-Za-z][A-Za-z0-9_-]*)*$",
    )


class ApplicationEntityV1(BaseModel):
    """SQLite-backed entity. The generated runtime owns id/createdAt/updatedAt columns."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    displayName: str = Field(default="", max_length=256)
    tableName: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]{0,127}$")
    fields: list[ApplicationEntityFieldV1] = Field(min_length=1, max_length=100)
    crud: ApplicationEntityCrudV1 = Field(default_factory=ApplicationEntityCrudV1)


class ApplicationSpecV1(BaseModel):
    """未知fieldを許容し、後続schema versionの情報をround-tripで保持する。"""

    model_config = ConfigDict(extra="allow")

    schemaVersion: int = Field(default=1, ge=1)
    application: ApplicationInfoV1
    theme: dict[str, Any] = Field(default_factory=dict)
    navigation: dict[str, Any] = Field(default_factory=dict)
    pages: list[ApplicationPageV1] = Field(default_factory=list)
    entities: list[ApplicationEntityV1] = Field(default_factory=list, max_length=100)
    apiEndpoints: list[ApplicationApiEndpointV1] = Field(default_factory=list)
    backgroundJobs: list[ApplicationBackgroundJobV1] = Field(default_factory=list)
    clientState: list[ApplicationClientStateV1] = Field(default_factory=list, max_length=100)
    queries: list[ApplicationQueryV1] = Field(default_factory=list, max_length=100)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[Any] = Field(default_factory=list)
    targets: list[TargetProfileV1] = Field(default_factory=list)
    llmRuntime: LlmRuntimeV1 = Field(default_factory=LlmRuntimeV1)
