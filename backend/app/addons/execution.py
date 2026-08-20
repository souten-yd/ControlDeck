from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from app.addons import health, registry, tokens
from app.security.permissions import ALL_PERMISSIONS

SCHEMA_LIMIT_BYTES = 64 * 1024
REQUEST_LIMIT_BYTES = 1024 * 1024
RESPONSE_LIMIT_BYTES = 4 * 1024 * 1024
SCHEMA_TIMEOUT_SECONDS = 5.0
EXECUTION_TIMEOUT_SECONDS = 120.0
WORKFLOW_NODE_PREFIX = "addon.workflow:"
_NODE_PATTERN = re.compile(r"^addon\.workflow:([a-z][a-z0-9-]{0,63}):([a-z][a-z0-9._-]{0,127})$")
_schema_cache: dict[tuple[int, str, str], dict[str, Any]] = {}


class AddonExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "addon_execution_failed", status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def workflow_node_type(addon_id: str, contribution_id: str) -> str:
    return f"{WORKFLOW_NODE_PREFIX}{addon_id}:{contribution_id}"


def parse_workflow_node_type(node_type: str) -> tuple[str, str] | None:
    match = _NODE_PATTERN.fullmatch(node_type)
    return match.groups() if match else None


def is_workflow_node_type(node_type: object) -> bool:
    return isinstance(node_type, str) and parse_workflow_node_type(node_type) is not None


def _effective(kind: str, permissions: set[str]) -> list[dict[str, Any]]:
    return registry.effective_for_permissions(permissions).get("contributions", {}).get(kind, [])


def discover(kind: str, permissions: set[str]) -> list[dict[str, Any]]:
    return _effective(kind, permissions)


def _find(kind: str, addon_id: str, contribution_id: str, permissions: set[str]) -> dict[str, Any]:
    item = next((
        value for value in _effective(kind, permissions)
        if value["addon_id"] == addon_id and value["id"] == contribution_id
    ), None)
    if item is None:
        try:
            current = registry.status(addon_id)
        except registry.AddonRegistryError as exc:
            raise AddonExecutionError("拡張機能が登録されていません", code="addon_not_found", status_code=404) from exc
        declared = any(value.get("id") == contribution_id for value in current.get("contributions", {}).get(kind, []))
        if not current.get("enabled"):
            raise AddonExecutionError("拡張機能は無効です", code="addon_disabled", status_code=409)
        if declared:
            raise AddonExecutionError("拡張機能の実行contributionは現在利用できません", code="contribution_unavailable", status_code=409)
        raise AddonExecutionError("実行contributionが見つかりません", code="contribution_not_found", status_code=404)
    return item


def find_for_user(kind: str, addon_id: str, contribution_id: str, permissions: set[str]) -> dict[str, Any]:
    return _find(kind, addon_id, contribution_id, permissions)


def find_for_runtime(kind: str, addon_id: str, contribution_id: str) -> dict[str, Any]:
    return _find(kind, addon_id, contribution_id, set(ALL_PERMISSIONS))


def _url(addon_id: str, path: str) -> str:
    current = registry.status(addon_id)
    return health.approved_health_url(current["runtime"]["base_url"], path)


def _headers(addon_id: str, subject: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {tokens.issue(addon_id, subject=subject, kind='service')}",
        "Content-Type": "application/json",
        "X-Control-Deck-Addon-ID": addon_id,
    }


def _decode_json(content: bytes, *, label: str, limit: int) -> dict[str, Any]:
    if len(content) > limit:
        raise AddonExecutionError(f"{label}が{limit // 1024}KiB上限を超えました", code="response_too_large")
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AddonExecutionError(f"{label}はJSON objectである必要があります", code="invalid_response") from exc
    if not isinstance(value, dict):
        raise AddonExecutionError(f"{label}はJSON objectである必要があります", code="invalid_response")
    return value


async def schema(addon_id: str, path: str, *, subject: str = "discovery") -> dict[str, Any]:
    key = (registry.revision(), addon_id, path)
    cached = _schema_cache.get(key)
    if cached is not None:
        return cached
    try:
        async with _client(SCHEMA_TIMEOUT_SECONDS) as client:
            response = await client.get(_url(addon_id, path), headers=_headers(addon_id, subject))
    except (httpx.HTTPError, registry.AddonRegistryError) as exc:
        raise AddonExecutionError("拡張機能のschemaを取得できません", code="schema_unavailable") from exc
    if response.status_code != 200:
        raise AddonExecutionError("拡張機能のschema取得に失敗しました", code="schema_unavailable")
    value = _decode_json(response.content, label="schema response", limit=SCHEMA_LIMIT_BYTES)
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise AddonExecutionError("拡張機能のJSON Schemaが不正です", code="invalid_schema") from exc
    if value.get("type") != "object":
        raise AddonExecutionError("拡張機能のschema rootはobjectにしてください", code="invalid_schema")
    for stale in [cache_key for cache_key in _schema_cache if cache_key[0] != key[0]]:
        _schema_cache.pop(stale, None)
    _schema_cache[key] = value
    return value


def validate(schema_value: dict[str, Any], value: Any, *, label: str) -> None:
    try:
        Draft202012Validator(schema_value).validate(value)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        location = f" ({path})" if path else ""
        raise AddonExecutionError(
            f"{label}がcontribution schemaに一致しません{location}",
            code="schema_validation_failed",
            status_code=422,
        ) from exc


async def invoke(
    kind: str,
    addon_id: str,
    contribution_id: str,
    payload: dict[str, Any],
    *,
    subject: str,
    permissions: set[str] | None = None,
    timeout: float = EXECUTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    contribution = (
        find_for_runtime(kind, addon_id, contribution_id)
        if permissions is None else find_for_user(kind, addon_id, contribution_id, permissions)
    )
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > REQUEST_LIMIT_BYTES:
        raise AddonExecutionError("拡張機能へのinputが1MiB上限を超えました", code="request_too_large", status_code=413)
    started = time.monotonic()
    result = "success"
    status_code = 0
    try:
        async with _client(max(1.0, min(timeout, EXECUTION_TIMEOUT_SECONDS))) as client:
            response = await client.post(
                _url(addon_id, contribution["endpoint"]),
                headers=_headers(addon_id, subject),
                content=encoded,
            )
        status_code = response.status_code
        if response.is_redirect:
            result = "redirect_rejected"
            raise AddonExecutionError("拡張機能のredirectは許可されていません", code="redirect_rejected")
        if response.status_code >= 400:
            result = "upstream_error"
            raise AddonExecutionError("拡張機能の実行に失敗しました", code="upstream_error")
        output = _decode_json(response.content, label="execution response", limit=RESPONSE_LIMIT_BYTES)
        # Disable/unavailable races fail closed even if the upstream already returned.
        if permissions is None:
            find_for_runtime(kind, addon_id, contribution_id)
        else:
            find_for_user(kind, addon_id, contribution_id, permissions)
        return output
    except asyncio.CancelledError:
        result = "canceled"
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        result = "upstream_unavailable"
        raise AddonExecutionError("拡張機能serviceへ接続できません", code="upstream_unavailable") from exc
    except registry.AddonRegistryError as exc:
        result = "upstream_unavailable"
        raise AddonExecutionError("拡張機能serviceへ接続できません", code="upstream_unavailable") from exc
    except AddonExecutionError:
        if result == "success":
            result = "rejected"
        raise
    finally:
        try:
            registry.record_activity(
                addon_id,
                f"addon.{kind.rstrip('s')}.invoke",
                result,
                {
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "status_code": status_code,
                    "field_count": len(payload),
                    "byte_count": len(encoded),
                },
            )
        except registry.AddonRegistryError:
            pass


async def workflow_schemas(addon_id: str, contribution_id: str, *, permissions: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    contribution = (
        find_for_runtime("workflow_executors", addon_id, contribution_id)
        if permissions is None else find_for_user("workflow_executors", addon_id, contribution_id, permissions)
    )
    return await asyncio.gather(
        schema(addon_id, contribution["input_schema_path"]),
        schema(addon_id, contribution["output_schema_path"]),
    )


def cached_workflow_input_schema(node_type: str) -> dict[str, Any] | None:
    parsed = parse_workflow_node_type(node_type)
    if parsed is None:
        return None
    addon_id, contribution_id = parsed
    try:
        contribution = find_for_runtime("workflow_executors", addon_id, contribution_id)
    except AddonExecutionError:
        return None
    return _schema_cache.get((registry.revision(), addon_id, contribution["input_schema_path"]))


def reset_for_tests() -> None:
    _schema_cache.clear()
