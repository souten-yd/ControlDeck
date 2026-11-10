import json

from pydantic import BaseModel, Field, field_validator


class PasskeyRegistration(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=32)


class PasskeyResponse(BaseModel):
    credential: dict

    @field_validator("credential")
    @classmethod
    def bound_credential(cls, value: dict) -> dict:
        if len(json.dumps(value)) > 65536:
            raise ValueError("パスキーの応答が大きすぎます")
        return value
