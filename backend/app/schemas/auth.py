from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    # 二要素認証の 6 桁コードまたはリカバリーコード
    totp_code: str | None = Field(default=None, max_length=32)


class TotpVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class TotpSetupResponse(BaseModel):
    secret: str
    qr_data_uri: str
    provisioning_uri: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    permissions: list[str]
    totp_enabled: bool
    recovery_codes_remaining: int = 0
    totp_required: bool = False


class SessionOut(BaseModel):
    id: int
    ip_address: str
    user_agent: str
    created_at: datetime
    last_seen_at: datetime
    current: bool
