from __future__ import annotations

import base64
import binascii
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addon_runtime.service import audit_runtime
from app.models_mgmt.ai_gateway import AITargetUnavailable, capability_available, resolve_ai_target
from app.models_mgmt.runtime_provider import RuntimeChatRequest, RuntimeProviderError, provider_for_base_url


router = APIRouter(prefix="/{addon_id}/ai")
AIAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability("ai.inference"))]

MAX_REQUEST_JSON_BYTES = 12 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 4
MAX_RESPONSE_SCHEMA_BYTES = 64 * 1024
_IMAGE_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/webp;base64,",
)


class RuntimeAIRequest(BaseModel):
    """Provider-neutral bounded request from an Add-on to ControlDeck AI."""

    model_config = ConfigDict(extra="forbid")

    capability: Literal["text.generate", "vision.analyze"]
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=32)
    response_format: dict[str, Any] | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=8192)
    timeout_seconds: int = Field(default=120, ge=1, le=300)

    @model_validator(mode="after")
    def validate_bounds(self) -> "RuntimeAIRequest":
        try:
            encoded = json.dumps(self.messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("messages はJSON化可能な値にしてください") from exc
        if len(encoded) > MAX_REQUEST_JSON_BYTES:
            raise ValueError("AI request が大きすぎます")
        if self.response_format is not None:
            try:
                schema = json.dumps(self.response_format, separators=(",", ":")).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError("response_format はJSON化可能な値にしてください") from exc
            if len(schema) > MAX_RESPONSE_SCHEMA_BYTES:
                raise ValueError("response_format が大きすぎます")

        image_count = 0
        total_image_bytes = 0
        for message in self.messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                image_count += 1
                if image_count > MAX_IMAGES:
                    raise ValueError(f"画像は最大{MAX_IMAGES}枚です")
                image_url = part.get("image_url")
                url = str(image_url.get("url") if isinstance(image_url, dict) else "")
                prefix = next((value for value in _IMAGE_PREFIXES if url.startswith(value)), None)
                if prefix is None:
                    raise ValueError("画像入力はbounded data URLだけを使用できます")
                try:
                    raw = base64.b64decode(url[len(prefix):], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("画像data URLのbase64が不正です") from exc
                if not raw or len(raw) > MAX_IMAGE_BYTES:
                    raise ValueError("画像1枚は2MiB以内にしてください")
                total_image_bytes += len(raw)
                if total_image_bytes > MAX_TOTAL_IMAGE_BYTES:
                    raise ValueError("画像入力の合計は8MiB以内にしてください")
        if self.capability == "vision.analyze" and image_count == 0:
            raise ValueError("vision.analyze には画像入力が必要です")
        return self


@router.get("/capabilities")
async def ai_capabilities(principal: AIAuth):
    del principal
    return {
        "text.generate": {"available": await capability_available("text.generate")},
        "vision.analyze": {"available": await capability_available("vision.analyze")},
    }


@router.post("/complete")
async def ai_complete(body: RuntimeAIRequest, request: Request, principal: AIAuth):
    try:
        target = await resolve_ai_target(body.capability)
    except AITargetUnavailable as exc:
        raise HTTPException(status_code=503, detail="要求されたAI capabilityを利用できません") from exc

    runtime_request = RuntimeChatRequest(
        base_url=target.base_url,
        model=target.model,
        messages=body.messages,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        thinking=False,
        disable_thinking=True,
        response_format=body.response_format,
        timeout_seconds=body.timeout_seconds,
    )
    try:
        content = await provider_for_base_url(target.base_url).complete(runtime_request)
    except RuntimeProviderError as exc:
        raise HTTPException(status_code=502, detail="ControlDeck AI gatewayで生成に失敗しました") from exc

    audit_runtime(
        request,
        principal,
        "addon.runtime.ai.complete",
        "ai_gateway",
        body.capability,
        {"capability": body.capability},
    )
    return {"content": content, "capability": body.capability}
