from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.application_builder.diagnostics import Diagnostic
from app.application_builder.type_system import TypeRef


class PortIR(BaseModel):
    name: str
    type: TypeRef
    required: bool = False
    description: str = ""


class ExecutionPolicyIR(BaseModel):
    async_mode: bool = True
    retry_count: int = 0
    retry_wait_seconds: float = 0
    timeout_seconds: float | None = None
    on_error: str = "stop"
    join_mode: str = "first"
    requires_approval: bool = False
    cancelable: bool = True


class NodeCodegenIR(BaseModel):
    target: str
    support: str
    planned_support: str | None = None
    source_available: bool = False
    generator: str = ""
    reason: str = ""
    packages: list[str] = Field(default_factory=list)


class NodeIR(BaseModel):
    id: str
    node_type: str
    version: int = 1
    display_name: str
    config: dict[str, Any]
    inputs: list[PortIR] = Field(default_factory=list)
    outputs: list[PortIR] = Field(default_factory=list)
    execution: ExecutionPolicyIR
    codegen: NodeCodegenIR


class EdgeIR(BaseModel):
    id: str
    source_node: str
    source_port: str = "output"
    target_node: str
    target_port: str = "input"
    branch: str | None = None
    data_type: TypeRef
    condition: dict[str, Any] | None = None


class SecretReferenceIR(BaseModel):
    name: str


class WorkflowIR(BaseModel):
    schema_version: int = 1
    workflow_id: int | None = None
    workflow_version_id: int | None = None
    name: str
    inputs: list[PortIR]
    outputs: list[PortIR]
    nodes: list[NodeIR]
    edges: list[EdgeIR]
    required_secrets: list[SecretReferenceIR]
    capabilities: list[str]
    side_effects: list[str]
    diagnostics: list[Diagnostic]


class ApplicationIR(BaseModel):
    schema_version: int = 1
    name: str
    display_name: str
    application_type: str
    theme: dict[str, Any] = Field(default_factory=dict)
    navigation: dict[str, Any] = Field(default_factory=dict)
    pages: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    api_endpoints: list[dict[str, Any]] = Field(default_factory=list)
    background_jobs: list[dict[str, Any]] = Field(default_factory=list)
    client_state: list[dict[str, Any]] = Field(default_factory=list)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[dict[str, Any] | str] = Field(default_factory=list)
    targets: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
