from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VramConfidence(StrEnum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    LOW = "low"


# システムRAMを表す device の id。GPU以外の配置はこれ 1 つである。
HOST_DEVICE_ID = "host"


class ComputeMode(StrEnum):
    EXCLUSIVE_REQUIRED = "exclusive-required"
    EXCLUSIVE_PREFERRED = "exclusive-preferred"
    SHARED_SAFE = "shared-safe"
    ENDPOINT_MANAGED = "endpoint-managed"


class WorkloadClass(StrEnum):
    INTERACTIVE = "interactive"
    AGENT_INTERACTIVE = "agent-interactive"
    WORKFLOW = "workflow"
    BACKGROUND = "background"
    BATCH = "batch"
    MAINTENANCE = "maintenance"


class WaitReason(StrEnum):
    DEVICE_BUSY_EXCLUSIVE = "device_busy_exclusive"
    INSUFFICIENT_VRAM = "insufficient_vram"
    HELD_BY_OTHER_OWNER = "held_by_other_owner"
    QUEUE_POSITION = "queue_position"
    MODEL_LOADING = "model_loading"
    PROVIDER_DRAINING = "provider_draining"
    DEPENDENCY_PENDING = "dependency_pending"
    INSUFFICIENT_CAPACITY = "insufficient_capacity"


class RequestState(StrEnum):
    WAITING = "waiting"
    GRANTED = "granted"
    REJECTED = "rejected"
    CANCELED = "canceled"
    EXPIRED = "expired"


class LeaseState(StrEnum):
    GRANTED = "granted"
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    CANCELED = "canceled"


class VramRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resident_bytes: int = Field(ge=0, le=2**50)
    execution_peak_bytes: int = Field(ge=0, le=2**50)
    cold_load_peak_bytes: int = Field(ge=0, le=2**50)
    headroom_bytes: int = Field(ge=0, le=2**50)
    confidence: VramConfidence
    # 全部載せるほど VRAM が無くても、これだけあれば動く、という下限。
    # 画像生成は重みをRAMに置き、実行するモジュールだけをVRAMへ送る形で走れる
    # （diffusers の model cpu offload）。枠が小さければ細かく往復して遅く、
    # 大きければ多く常駐して速い、と連続的に変わる。実測（2026-09-05、
    # FLUX.2 Klein 4B / 1024²）: 全常駐 21.9GiB で 2.98秒、枠 8GiB で 6.7秒、
    # 枠 7GiB では OOM。省略した要求は従来どおり全常駐しか受け付けない。
    minimum_bytes: int | None = Field(default=None, ge=0, le=2**50)

    @property
    def required_bytes(self) -> int:
        return max(self.resident_bytes, self.execution_peak_bytes, self.cold_load_peak_bytes) + self.headroom_bytes

    @property
    def floor_bytes(self) -> int:
        """この要求が動ける最小の枠。下限が無ければ全常駐そのもの。"""
        if self.minimum_bytes is None:
            return self.required_bytes
        return min(self.minimum_bytes, self.required_bytes)


class ResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    owner: str = Field(min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    device: str = Field(default="auto", min_length=1, max_length=64)
    preferred_devices: list[str] = Field(default_factory=list, max_length=16)
    forbidden_devices: list[str] = Field(default_factory=list, max_length=16)
    vram: VramRequest
    # host（システムRAM）に載せるときに要る量。同じモデルでも置き場所で必要量が
    # 違う。vram の見積りは device_map で段階的に載せるときのGPU側ピークで、RAM
    # 配置の実態とは別物である。実測: FLUX.2 Klein 4B は VRAM 31.1GB の申告に
    # 対しCPU実行のRSSが16.3GB。VRAMの数字をRAMに当てると、30GBの機械では
    # host が永久に grant されない。省略時は vram の値をそのまま使う。
    host_bytes: int | None = Field(default=None, ge=0, le=2**50)
    compute_mode: ComputeMode
    priority: int = Field(default=0, ge=-100, le=100)
    workload_class: WorkloadClass = Field(default=WorkloadClass.BACKGROUND, alias="class")
    residency_key: str | None = Field(default=None, min_length=1, max_length=256)
    max_wait_sec: float = Field(default=300, gt=0, le=3600)
    on_insufficient: Literal["queue", "fail_fast"] = "queue"
    estimated_runtime_sec: float | None = Field(default=None, gt=0, le=86_400)

    @field_validator("device")
    @classmethod
    def valid_device(cls, value: str) -> str:
        if value != "auto" and not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("device IDが不正です")
        return value

    @field_validator("preferred_devices", "forbidden_devices")
    @classmethod
    def unique_devices(cls, value: list[str]) -> list[str]:
        if any(not item or not item.replace("-", "").replace("_", "").isalnum() for item in value):
            raise ValueError("device IDが不正です")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def coherent_devices(self) -> "ResourceRequest":
        if self.device != "auto" and (self.preferred_devices or self.forbidden_devices):
            raise ValueError("固定deviceとpreferred/forbiddenは同時指定できません")
        if set(self.preferred_devices) & set(self.forbidden_devices):
            raise ValueError("同じdeviceをpreferredとforbiddenに指定できません")
        return self

    @model_validator(mode="after")
    def host_bytes_needs_host(self) -> "ResourceRequest":
        # host を候補にしていない要求の host_bytes は使われない。黙って無視すると
        # 「申告したのに効かない」に気づけないので、受け取らない。
        if self.host_bytes is not None and HOST_DEVICE_ID not in self.preferred_devices:
            raise ValueError("host_bytesはpreferred_devicesにhostを挙げた要求にだけ指定できます")
        return self


class BlockingResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    bytes: int = Field(ge=0)
    deferred: bool = False


class RequestStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    state: RequestState
    owner: str
    job_id: str
    device_id: str | None = None
    lease_id: str | None = None
    # 実際に貸した枠。下限で受理されたときは required より小さい。利用者は
    # この値に自分を縛る（例: torch の per-process memory fraction）。
    granted_bytes: int | None = Field(default=None, ge=0)
    reason: WaitReason | None = None
    queue_position: int | None = Field(default=None, ge=1)
    blocking: list[BlockingResource] = Field(default_factory=list)
    eta_sec: int | None = Field(default=None, ge=0)
    eta_confidence: Literal["measured", "estimated", "low"] | None = None
    actions: list[Literal["cancel", "lower_priority"]] = Field(default_factory=list)
    requested_at: float
    deadline_at: float


class LeaseStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    request_id: str
    owner: str
    job_id: str
    device_id: str
    reserved_bytes: int = Field(ge=0)
    compute_mode: ComputeMode
    residency_key: str | None = None
    state: LeaseState
    granted_at: float
    expires_at: float


class DeviceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    total_bytes: int = Field(ge=0)
    observed_used_bytes: int = Field(ge=0)
    fixed_reserved_bytes: int = Field(ge=0)
    lease_reserved_bytes: int = Field(ge=0)
    admitted_free_bytes: int = Field(ge=0)
    compatible: bool = True
    resident_keys: list[str] = Field(default_factory=list)
