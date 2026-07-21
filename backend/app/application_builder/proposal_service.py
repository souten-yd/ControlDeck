from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.application_builder.patch_service import preview_patches
from app.schemas.application_builder import (
    ApplicationDesignProposalEnvelope,
    ApplicationDesignProposalRequest,
)
from app.workflows.redaction import collect_sensitive_values, redact

Complete = Callable[[list[dict[str, str]], dict[str, Any]], Awaitable[str]]
_DIRECTIONS = {"simple", "balanced", "dense"}
_MAX_CONTEXT_CHARS = 60_000


class _RawPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "remove", "replace", "move"]
    path: str = Field(min_length=1, max_length=2048)
    from_path: str | None = Field(alias="from", max_length=2048)
    value_json: str = Field(alias="valueJson", max_length=50_000)


class _RawProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    direction: Literal["simple", "balanced", "dense"]
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)
    rationale: list[str] = Field(max_length=8)
    patches: list[_RawPatch] = Field(min_length=1, max_length=200)
    warnings: list[str] = Field(max_length=8)

    @field_validator("rationale", "warnings", mode="before")
    @classmethod
    def normalize_text_list(cls, value: Any) -> Any:
        # 一部のOpenAI互換runtimeはJSON Schema fallback時に単一要素arrayをstring化する。
        # codeやobjectへの自由化はせず、文字列だけを決定的に1要素へ正規化する。
        return [value] if isinstance(value, str) else value


class _RawEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[_RawProposal] = Field(min_length=3, max_length=3)


class ProposalGenerationError(RuntimeError):
    """秘密値やprovider応答本文を含めない、利用者向け生成エラー。"""


class ProposalInputError(ProposalGenerationError):
    """LLM呼出し前に判定できるscope／context入力エラー。"""


async def generate_design_proposals(
    spec: dict[str, Any], request: ApplicationDesignProposalRequest,
    *, complete: Complete | None = None,
) -> dict[str, Any]:
    _validate_scope(spec, request)
    context = _prompt_context(spec)
    from app.application_builder.design_system.components import component_catalog

    catalog = component_catalog()
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": json.dumps({
            "instruction": request.instruction,
            "scope": request.scope,
            "targetId": request.target_id,
            "mode": request.mode,
            "applicationSpec": context,
            "workflowContract": _workflow_contract_context(context),
            "allowedSemanticComponents": catalog["components"],
            "allowedDesignTokens": catalog["designTokens"],
            "allowedBindingSources": catalog["bindingSources"],
        }, ensure_ascii=False, separators=(",", ":"))},
    ]
    runner = complete or _runtime_complete(request)
    try:
        raw = await runner(messages, _response_schema())
        payload = _json_payload(raw)
        raw_envelope = _RawEnvelope.model_validate(payload)
        envelope = ApplicationDesignProposalEnvelope(proposals=[_convert_proposal(item) for item in raw_envelope.proposals])
    except ProposalGenerationError:
        raise
    except Exception as exc:
        raise ProposalGenerationError("LLMの設計案がApplication Spec Patch schemaに適合しません") from exc
    if {item.direction for item in envelope.proposals} != _DIRECTIONS:
        raise ProposalGenerationError("Simple／Balanced／Denseの3案が揃っていません")
    proposals = []
    for item in envelope.proposals:
        preview = preview_patches(spec, item.patches)
        proposals.append({**item.model_dump(by_alias=True), "preview": preview})
    return {"proposals": proposals}


def _runtime_complete(request: ApplicationDesignProposalRequest) -> Complete:
    async def run(messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        from app.models_mgmt.runtime_provider import (
            RuntimeChatRequest,
            RuntimeProviderError,
            provider_for_base_url,
        )
        runtime_request = RuntimeChatRequest(
            base_url=request.base_url,
            model=request.model,
            messages=messages,
            temperature=0.25,
            max_tokens=8192,
            thinking=False,
            disable_thinking=True,
            response_format={
                "type": "json_schema", "name": "application_design_proposals",
                "schema": schema, "strict": True,
            },
            keep_alive="30m",
            timeout_seconds=300,
        )
        try:
            return await provider_for_base_url(request.base_url).complete(runtime_request)
        except RuntimeProviderError as exc:
            raise ProposalGenerationError(str(exc)) from exc

    return run


def _prompt_context(spec: dict[str, Any]) -> dict[str, Any]:
    safe = redact(spec, sensitive_values=collect_sensitive_values(spec))
    compact = _trim_strings(safe)
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > _MAX_CONTEXT_CHARS:
        raise ProposalInputError("Application SpecがAI設計コンテキスト上限を超えています。対象範囲を小さくしてください")
    return compact


def _trim_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _trim_strings(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_trim_strings(child) for child in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


def _workflow_contract_context(spec: dict[str, Any]) -> dict[str, Any] | None:
    advisor = spec.get("xAppAdvisor")
    endpoints = spec.get("apiEndpoints") if isinstance(spec.get("apiEndpoints"), list) else []
    workflow_endpoint = next((
        item for item in endpoints
        if isinstance(item, dict) and isinstance(item.get("workflowId"), int)
    ), None)
    if not isinstance(advisor, dict) and workflow_endpoint is None:
        return None
    return {
        "advisor": advisor if isinstance(advisor, dict) else None,
        "requestSchema": workflow_endpoint.get("requestSchema", {}) if workflow_endpoint else {},
        "responseSchema": workflow_endpoint.get("responseSchema", {}) if workflow_endpoint else {},
        "invariant": "All workflow inputs, outputs, binding IDs, and executable endpoint wiring must remain functional.",
    }


def _validate_scope(spec: dict[str, Any], request: ApplicationDesignProposalRequest) -> None:
    if request.scope in {"page", "component"} and not request.target_id:
        raise ProposalInputError("選択範囲にはtarget IDが必要です")
    if request.scope == "page":
        if not any(isinstance(page, dict) and page.get("id") == request.target_id for page in spec.get("pages", [])):
            raise ProposalInputError("対象PageがApplication Specに存在しません")
    if request.scope == "component" and not _contains_component(spec.get("pages", []), request.target_id or ""):
        raise ProposalInputError("対象ComponentがApplication Specに存在しません")


def _contains_component(value: Any, target_id: str) -> bool:
    if isinstance(value, list):
        return any(_contains_component(item, target_id) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("id") == target_id and isinstance(value.get("type"), str):
        return True
    return any(_contains_component(child, target_id) for child in value.values())


def _json_payload(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalGenerationError("LLMが有効なJSON設計案を返しませんでした") from exc


def _response_schema() -> dict[str, Any]:
    schema = _RawEnvelope.model_json_schema(by_alias=True)
    schema.pop("title", None)
    return schema


def _convert_proposal(raw: _RawProposal) -> dict[str, Any]:
    patches = []
    for item in raw.patches:
        patch: dict[str, Any] = {"op": item.op, "path": item.path}
        if item.op == "move":
            if not item.from_path:
                raise ProposalGenerationError("move Patchにfromがありません")
            patch["from"] = item.from_path
        elif item.op in {"add", "replace"}:
            try:
                patch["value"] = json.loads(item.value_json)
            except json.JSONDecodeError as exc:
                raise ProposalGenerationError("Patch valueJsonが有効なJSONではありません") from exc
        patches.append(patch)
    return {
        "id": raw.id, "direction": raw.direction, "title": raw.title, "summary": raw.summary,
        "rationale": raw.rationale, "patches": patches, "warnings": raw.warnings,
    }


def _system_prompt() -> str:
    return """You design ControlDeck Application Specs. Return JSON only, never source code.
Create exactly three items in a top-level `proposals` array with unique directions: simple, balanced, dense.
Use only these keys per proposal: id, direction, title, summary, rationale, patches, warnings.
Each proposal must contain RFC 6902 add/remove/replace/move operations against the supplied spec.
Each patch must have exactly op, path, from, valueJson. Set from to null unless op is move.
valueJson must be a JSON-encoded string for add/replace, and an empty string for remove/move.
Respect the requested scope and redesign mode. Preserve every supplied Workflow input/output and its executable endpoint wiring.
Never modify locked fields, invent framework-specific component classes,
insert secrets, credentials, executable code, shell commands, or remote assets. Prefer existing semantic component types,
bindings, design tokens, responsive rules, and stable component IDs. A proposal is only a suggestion and is not applied automatically."""
