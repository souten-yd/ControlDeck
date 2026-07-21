from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.security.permissions import ALL_PERMISSIONS

PLUGIN_ID_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"


class NavigationContribution(BaseModel):
    """Control Deck のナビゲーションへ加える外部 Web UI。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=1, max_length=2048)
    permission: str = "apps.view"

    @model_validator(mode="after")
    def validate_navigation(self) -> "NavigationContribution":
        if self.permission not in ALL_PERMISSIONS:
            raise ValueError("navigation.permission は Control Deck の既知の権限にしてください")
        if self.url.startswith("/") and not self.url.startswith("//") and "\\" not in self.url:
            return self
        parsed = urlsplit(self.url)
        if parsed.username or parsed.password or not parsed.hostname or parsed.fragment:
            raise ValueError("navigation.url に認証情報またはfragmentは指定できません")
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("navigation.url は同一origin path、HTTPS、またはloopback HTTPにしてください")
        return self


class PluginManifest(BaseModel):
    """Control Deck plugin manifest API v1。任意コードは本体へ読み込まない。"""

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["1"] = "1"
    id: str = Field(pattern=PLUGIN_ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$", max_length=64)
    description: str = Field(default="", max_length=300)
    publisher: str = Field(min_length=1, max_length=120)
    capabilities: list[Literal["navigation"]] = Field(default_factory=lambda: ["navigation"], min_length=1, max_length=8)
    navigation: NavigationContribution

    @model_validator(mode="after")
    def validate_manifest(self) -> "PluginManifest":
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities を重複させることはできません")
        if any(ord(character) < 32 or ord(character) == 127 for character in self.name):
            raise ValueError("name に制御文字は使用できません")
        return self
