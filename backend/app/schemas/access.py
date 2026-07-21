from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ManagedUserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role_id: int
    role_name: str
    is_active: bool
    totp_enabled: bool
    created_at: datetime
    last_login_at: datetime | None


class ManagedUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    display_name: str = Field(default="", max_length=128)
    password: str = Field(min_length=8, max_length=256)
    role_id: int = Field(gt=0)


class ManagedUserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role_id: int | None = Field(default=None, gt=0)
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=256)


class ManagedRoleOut(BaseModel):
    id: int
    name: str
    permissions: list[str]
    preset: bool
    user_count: int


class ManagedRoleCreate(BaseModel):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    permissions: list[str] = Field(default_factory=list, max_length=128)


class ManagedRoleUpdate(BaseModel):
    permissions: list[str] = Field(max_length=128)
