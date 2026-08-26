from __future__ import annotations

import copy
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.addons.contract import ADDON_CONTRACT_VERSION, AddonHealthState, AddonReasonCode, ContributionAvailability
from app.security.permissions import ALL_PERMISSIONS

PLUGIN_ID_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"
CONTRIBUTION_ID_PATTERN = r"^[a-z][a-z0-9._-]{0,127}$"
SEMVER_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
CONTRACT_RANGE_PATTERN = r"^>=[0-9]+\.[0-9]+ <[0-9]+\.[0-9]+$"
PRESENTATIONAL_FORWARD_FIELDS = frozenset({"badge", "hint", "icon", "order"})
MAX_MANIFEST_BYTES = 64 * 1024

HostCapability: TypeAlias = Literal[
    "context.read",
    "theme.read",
    "route.open",
    "files.pick",
    "files.export",
    "projects.pick",
    "jobs.read",
    "jobs.write",
    "notifications.show",
    "resources.acquire",
    "ai.inference",
    "devices.relay",
]
LocalizedLabel: TypeAlias = str | dict[Literal["en", "ja"], str]


def _clean_text(value: str, field: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} に制御文字は使用できません")
    return value


def _validate_relative_path(value: str, field: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError(f"{field} は / で始まる同一service内pathにしてください")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError(f"{field} にscheme、host、fragmentは指定できません")
    return value


def _validate_service_url(value: str, field: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or not parsed.hostname or parsed.fragment:
        raise ValueError(f"{field} に認証情報またはfragmentは指定できません")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError(f"{field} はHTTPSまたはloopback HTTPにしてください")
    return value


class NavigationContributionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=1, max_length=2048)
    permission: str = "apps.view"

    @model_validator(mode="after")
    def validate_navigation(self) -> "NavigationContributionV1":
        if self.permission not in ALL_PERMISSIONS:
            raise ValueError("navigation.permission は Control Deck の既知の権限にしてください")
        if self.url.startswith("/") and not self.url.startswith("//") and "\\" not in self.url:
            return self
        _validate_service_url(self.url, "navigation.url")
        return self


class PluginManifestV1(BaseModel):
    """Plugin SDK v1 contract kept byte-for-byte compatible at the API edge."""

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["1"] = "1"
    id: str = Field(pattern=PLUGIN_ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=SEMVER_PATTERN, max_length=64)
    description: str = Field(default="", max_length=300)
    publisher: str = Field(min_length=1, max_length=120)
    capabilities: list[Literal["navigation"]] = Field(default_factory=lambda: ["navigation"], min_length=1, max_length=8)
    navigation: NavigationContributionV1

    @model_validator(mode="after")
    def validate_manifest(self) -> "PluginManifestV1":
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("capabilities を重複させることはできません")
        _clean_text(self.name, "name")
        return self


class AddonRequires(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addon_contract: str = Field(default=">=2.0 <3.0", pattern=CONTRACT_RANGE_PATTERN)


class AddonRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["external-service"] = "external-service"
    base_url: str = Field(max_length=2048)
    health_path: str = Field(default="/health", max_length=512)

    @model_validator(mode="after")
    def validate_runtime(self) -> "AddonRuntime":
        _validate_service_url(self.base_url, "runtime.base_url")
        _validate_relative_path(self.health_path, "runtime.health_path")
        parsed = urlsplit(self.base_url)
        if parsed.query or (parsed.path not in {"", "/"}):
            raise ValueError("runtime.base_url はorigin rootだけを指定してください")
        return self


class ContributionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=CONTRIBUTION_ID_PATTERN)
    label: LocalizedLabel
    permission: str = "apps.view"

    @model_validator(mode="after")
    def validate_base(self) -> "ContributionBase":
        if self.permission not in ALL_PERMISSIONS:
            raise ValueError("contribution.permission は Control Deck の既知の権限にしてください")
        labels = [self.label] if isinstance(self.label, str) else list(self.label.values())
        if not labels or any(not label or len(label) > 80 for label in labels):
            raise ValueError("label は1〜80文字にしてください")
        for label in labels:
            _clean_text(label, "label")
        return self


class NavigationContributionV2(ContributionBase):
    route: str = Field(max_length=512)
    icon: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,31}$")
    order: int = Field(default=100, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_route(self) -> "NavigationContributionV2":
        _validate_relative_path(self.route, "navigation.route")
        return self


class EmbeddedViewContribution(ContributionBase):
    route: str = Field(max_length=512)
    path: str = Field(default="/", max_length=512)
    mobile: Literal["embedded", "companion", "link_out"] = "companion"

    @model_validator(mode="after")
    def validate_paths(self) -> "EmbeddedViewContribution":
        _validate_relative_path(self.route, "embedded_view.route")
        _validate_relative_path(self.path, "embedded_view.path")
        return self


class EndpointContribution(ContributionBase):
    endpoint: str = Field(max_length=512)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "EndpointContribution":
        _validate_relative_path(self.endpoint, "contribution.endpoint")
        return self


class SettingsContribution(ContributionBase):
    route: str = Field(max_length=512)

    @model_validator(mode="after")
    def validate_route(self) -> "SettingsContribution":
        _validate_relative_path(self.route, "settings.route")
        return self


class WorkflowExecutorContribution(EndpointContribution):
    input_schema_path: str = Field(max_length=512)
    output_schema_path: str = Field(max_length=512)

    @model_validator(mode="after")
    def validate_schema_paths(self) -> "WorkflowExecutorContribution":
        _validate_relative_path(self.input_schema_path, "workflow_executor.input_schema_path")
        _validate_relative_path(self.output_schema_path, "workflow_executor.output_schema_path")
        return self


class AgentToolContribution(EndpointContribution):
    schema_path: str = Field(max_length=512)

    @model_validator(mode="after")
    def validate_schema_path(self) -> "AgentToolContribution":
        _validate_relative_path(self.schema_path, "agent_tool.schema_path")
        return self


class DeviceRelayContribution(EndpointContribution):
    protocol: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9.-]*/[0-9]+$")
    transport: Literal["websocket"] = "websocket"


class ContextActionContribution(EndpointContribution):
    contexts: list[Literal["file", "project", "workflow", "job"]] = Field(min_length=1, max_length=4)


class SetupChecklistContribution(ContributionBase):
    pass


class AddonContributions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    navigation: list[NavigationContributionV2] = Field(default_factory=list, max_length=32)
    embedded_views: list[EmbeddedViewContribution] = Field(default_factory=list, max_length=16)
    commands: list[EndpointContribution] = Field(default_factory=list, max_length=64)
    quick_actions: list[EndpointContribution] = Field(default_factory=list, max_length=32)
    settings: list[SettingsContribution] = Field(default_factory=list, max_length=32)
    workflow_executors: list[WorkflowExecutorContribution] = Field(default_factory=list, max_length=64)
    agent_tools: list[AgentToolContribution] = Field(default_factory=list, max_length=64)
    device_relays: list[DeviceRelayContribution] = Field(default_factory=list, max_length=16)
    context_actions: list[ContextActionContribution] = Field(default_factory=list, max_length=64)
    setup_checklist: list[SetupChecklistContribution] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "AddonContributions":
        for name in type(self).model_fields:
            values = getattr(self, name)
            ids = [item.id for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"contributions.{name} のidを重複させることはできません")
        return self


class AddonManifestV2(BaseModel):
    """Declarative contract for an isolated Add-on v2 service."""

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["2"]
    id: str = Field(pattern=PLUGIN_ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=SEMVER_PATTERN, max_length=64)
    description: str = Field(default="", max_length=300)
    publisher: str = Field(min_length=1, max_length=120)
    requires: AddonRequires = Field(default_factory=AddonRequires)
    runtime: AddonRuntime
    contributions: AddonContributions
    host_capabilities: list[HostCapability] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_manifest(self) -> "AddonManifestV2":
        _clean_text(self.name, "name")
        if len(self.host_capabilities) != len(set(self.host_capabilities)):
            raise ValueError("host_capabilities を重複させることはできません")
        contribution_ids: set[str] = set()
        for name in type(self.contributions).model_fields:
            for contribution in getattr(self.contributions, name):
                qualified = f"{name}:{contribution.id}"
                if qualified in contribution_ids:
                    raise ValueError("contribution IDを重複させることはできません")
                contribution_ids.add(qualified)
        return self


class HealthAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["retry", "open_route", "open_logs", "disable", "documentation"]
    route: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_action(self) -> "HealthAction":
        if self.kind == "open_route":
            if self.route is None:
                raise ValueError("open_route actionにはrouteが必要です")
            _validate_relative_path(self.route, "action.route")
        elif self.route is not None:
            raise ValueError("open_route以外のactionにrouteは指定できません")
        return self


class ContributionAvailabilityDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal[ContributionAvailability.DEGRADED, ContributionAvailability.UNAVAILABLE]
    reason_code: AddonReasonCode
    message: str = Field(min_length=1, max_length=300)
    action: HealthAction


class SetupChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=CONTRIBUTION_ID_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    state: Literal["ok", "missing", "error", "checking"]
    detail: str | None = Field(default=None, max_length=300)
    message: str | None = Field(default=None, max_length=300)
    action: HealthAction | None = None


class AddonHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AddonHealthState
    contract_version: Literal["2.0"]
    reason_code: AddonReasonCode | None = None
    message: str | None = Field(default=None, max_length=300)
    action: HealthAction | None = None
    contributions: dict[str, ContributionAvailability | ContributionAvailabilityDetail] = Field(
        default_factory=dict, max_length=256,
    )
    setup: list[SetupChecklistItem] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_state_detail(self) -> "AddonHealthReport":
        if self.status in {AddonHealthState.DEGRADED, AddonHealthState.UNAVAILABLE, AddonHealthState.SETUP_REQUIRED}:
            if self.reason_code is None and not self.setup and not any(
                isinstance(value, ContributionAvailabilityDetail) for value in self.contributions.values()
            ):
                raise ValueError("非healthy healthにはreason_code、setup、またはcontribution detailが必要です")
        ids = [item.id for item in self.setup]
        if len(ids) != len(set(ids)):
            raise ValueError("setup item idを重複させることはできません")
        return self


Manifest: TypeAlias = PluginManifestV1 | AddonManifestV2


@dataclass(frozen=True)
class ParsedManifest:
    manifest: Manifest
    warnings: tuple[str, ...] = ()


def _strip_forward_presentational_fields(value: Any, path: str, warnings: list[str]) -> Any:
    if isinstance(value, list):
        return [_strip_forward_presentational_fields(item, f"{path}[{index}]", warnings) for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        current = f"{path}.{key}" if path else key
        navigation_item = path.startswith("contributions.navigation[")
        known_here = {"icon", "order"} if navigation_item else set()
        if key in PRESENTATIONAL_FORWARD_FIELDS and key not in known_here:
            warnings.append(f"{current}: このhostでは未対応の表示fieldを無視しました")
            continue
        cleaned[key] = _strip_forward_presentational_fields(item, current, warnings)
    return cleaned


def contract_is_compatible(requirement: str, host_version: str = ADDON_CONTRACT_VERSION) -> bool:
    match = re.fullmatch(r">=(\d+)\.(\d+) <(\d+)\.(\d+)", requirement)
    host_match = re.fullmatch(r"(\d+)\.(\d+)", host_version)
    if match is None or host_match is None:
        return False
    lower = (int(match.group(1)), int(match.group(2)))
    upper = (int(match.group(3)), int(match.group(4)))
    host = (int(host_match.group(1)), int(host_match.group(2)))
    return lower <= host < upper


def parse_manifest(value: Any) -> ParsedManifest:
    if not isinstance(value, dict):
        raise ValueError("manifest rootはJSON objectにしてください")
    api_version = value.get("api_version")
    if api_version == "1":
        return ParsedManifest(PluginManifestV1.model_validate(value))
    if api_version != "2":
        raise ValueError(f"未対応のapi_versionです: {api_version!r}")
    warnings: list[str] = []
    cleaned = _strip_forward_presentational_fields(copy.deepcopy(value), "", warnings)
    manifest = AddonManifestV2.model_validate(cleaned)
    if not contract_is_compatible(manifest.requires.addon_contract):
        raise ValueError(
            f"addon contract {manifest.requires.addon_contract!r} はhost {ADDON_CONTRACT_VERSION}と互換性がありません"
        )
    return ParsedManifest(manifest, tuple(warnings))


def load_manifest_file(source: Path) -> ParsedManifest:
    """Read a bounded user-owned manifest without following a symlink target."""

    expanded = source.expanduser()
    try:
        info = expanded.lstat()
    except FileNotFoundError as exc:
        raise ValueError("manifestが見つかりません") from exc
    if not stat.S_ISREG(info.st_mode) or expanded.is_symlink():
        raise ValueError("manifestはsymlinkではない通常fileにしてください")
    if info.st_uid != os.getuid():
        raise ValueError("manifestは実行user所有にしてください")
    if info.st_mode & 0o002:
        raise ValueError("manifestをotherから書込み可能にはできません")
    if info.st_size > MAX_MANIFEST_BYTES:
        raise ValueError("manifestは64KiB以下にしてください")
    try:
        raw = json.loads(expanded.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest JSONが不正です: {exc}") from exc
    return parse_manifest(raw)