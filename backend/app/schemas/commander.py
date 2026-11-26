from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AccountCreate(BaseModel):
    """アカウントの作成。名前はアカウントのフォルダの名前になる。"""

    name: str = Field(min_length=1, max_length=32)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        from app.commander.accounts import ACCOUNT_NAME_RE

        cleaned = value.strip()
        if not ACCOUNT_NAME_RE.fullmatch(cleaned):
            raise ValueError(
                "アカウント名は英小数字・_・- で 32 文字以内（先頭は英小数字）にしてください"
            )
        return cleaned


class ConsoleCreate(BaseModel):
    """コンソールを開く（CLI・アカウント・作業フォルダ・個数・表示名）。"""

    cli: str = Field(min_length=1, max_length=32)
    account: str = Field(default="default", min_length=1, max_length=32)
    cwd: str = Field(min_length=1, max_length=4096)
    count: int = Field(default=1, ge=1, le=8)
    label: str = Field(default="", max_length=64)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        cleaned = value.strip()
        if any(ord(c) < 0x20 or ord(c) == 0x7f for c in cleaned):
            raise ValueError("表示名に制御文字（tab・改行など）は使えません")
        return cleaned


class ConsoleAutoResume(BaseModel):
    """コンソールごとの自動再開（上限からの「続けて」）の入り切り。"""

    enabled: bool


class AccountLabel(BaseModel):
    """アカウントの画面の名前（空で外す）。既定のアカウントにも付けられる。"""

    label: str = Field(default="", max_length=32)
