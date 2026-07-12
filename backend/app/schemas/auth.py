from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    permissions: list[str]
    totp_enabled: bool


class SessionOut(BaseModel):
    id: int
    ip_address: str
    user_agent: str
    created_at: datetime
    last_seen_at: datetime
    current: bool
