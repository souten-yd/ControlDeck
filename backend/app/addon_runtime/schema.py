from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.resources.schema import ComputeMode, VramRequest, WorkloadClass


PRIORITY_CEILINGS = {
    WorkloadClass.INTERACTIVE: 30,
    WorkloadClass.AGENT_INTERACTIVE: 25,
    WorkloadClass.WORKFLOW: 15,
    WorkloadClass.BACKGROUND: 0,
    WorkloadClass.BATCH: 0,
    WorkloadClass.MAINTENANCE: -10,
}


class RuntimeResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    device: str = Field(default="auto", min_length=1, max_length=64)
    preferred_devices: list[str] = Field(default_factory=list, max_length=16)
    forbidden_devices: list[str] = Field(default_factory=list, max_length=16)
    vram: VramRequest
    compute_mode: ComputeMode
    priority: int = Field(default=0, ge=-100, le=100)
    workload_class: WorkloadClass = Field(default=WorkloadClass.BACKGROUND, alias="class")
    residency_key: str | None = Field(default=None, min_length=1, max_length=256)
    max_wait_sec: float = Field(default=300, gt=0, le=3600)
    on_insufficient: Literal["queue", "fail_fast"] = "queue"
    estimated_runtime_sec: float | None = Field(default=None, gt=0, le=86_400)

    @model_validator(mode="after")
    def enforce_runtime_policy(self) -> "RuntimeResourceRequest":
        if self.priority > PRIORITY_CEILINGS[self.workload_class]:
            raise ValueError(
                f"{self.workload_class.value} classのpriority上限は"
                f"{PRIORITY_CEILINGS[self.workload_class]}です"
            )
        if self.device != "auto" and (self.preferred_devices or self.forbidden_devices):
            raise ValueError("固定deviceとpreferred/forbiddenは同時指定できません")
        if set(self.preferred_devices) & set(self.forbidden_devices):
            raise ValueError("同じdeviceをpreferredとforbiddenに指定できません")
        return self


class RuntimeJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    detached: bool = False

    @field_validator("title")
    @classmethod
    def safe_title(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Job titleに制御文字は使用できません")
        return value


class RuntimeJobProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, gt=0)


class RuntimeJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    progress: RuntimeJobProgress | None = None
    message: str | None = Field(default=None, min_length=1, max_length=1000)
    wait_reason: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    status: Literal["succeeded", "failed", "canceled"] | None = None
    result: Any = None
    error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def terminal_fields_are_bounded(self) -> "RuntimeJobUpdate":
        if self.status is None and (self.result is not None or self.error is not None):
            raise ValueError("result/errorはterminal updateでだけ指定できます")
        if self.status == "succeeded" and self.error:
            raise ValueError("succeeded Jobにerrorは指定できません")
        if self.status == "failed" and not self.error:
            raise ValueError("failed Jobにはerrorが必要です")
        return self
