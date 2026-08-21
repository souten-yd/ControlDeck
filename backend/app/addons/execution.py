from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from app.addon_runtime import grants as runtime_grants
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
_agent_tool_map: dict[str, tuple[int, str, str]] = {}
_WORKFLOW_INTERNAL_KEYS = {
    "retry_count", "retry_wait", "node_timeout", "on_error", "output_var",
    "__pause_response", "__workflow_id", "__execution_id", "__node_id",
}


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


def _headers(
    addon_id: str,
    subject: str,
    *,
    actor_user_id: int | None = None,
    grant_ids: tuple[str, ...] | None = None,
) -> dict[str, str]:
    token = tokens.issue(
        addon_id,
        subject=subject,
        kind="service",
        actor_user_id=actor_user_id,
        grant_ids=grant_ids,
    )
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
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
    actor_user_id: int | None = None,
    grant_ids: tuple[str, ...] | None = None,
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
                headers=_headers(
                    addon_id,
                    subject,
                    actor_user_id=actor_user_id,
                    grant_ids=grant_ids,
                ),
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


async def agent_schema(addon_id: str, contribution_id: str, *, permissions: set[str] | None = None) -> dict[str, Any]:
    contribution = (
        find_for_runtime("agent_tools", addon_id, contribution_id)
        if permissions is None else find_for_user("agent_tools", addon_id, contribution_id, permissions)
    )
    return await schema(addon_id, contribution["schema_path"])


def agent_tool_name(addon_id: str, contribution_id: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_]", "_", f"addon_{addon_id}_{contribution_id}")[:52]
    suffix = hashlib.sha256(f"{addon_id}:{contribution_id}".encode()).hexdigest()[:8]
    return f"{stem}_{suffix}"


async def agent_tool_definitions(permissions: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    revision = registry.revision()
    for contribution in discover("agent_tools", permissions):
        try:
            parameters = await agent_schema(
                contribution["addon_id"], contribution["id"], permissions=permissions,
            )
        except AddonExecutionError:
            continue
        name = agent_tool_name(contribution["addon_id"], contribution["id"])
        _agent_tool_map[name] = (revision, contribution["addon_id"], contribution["id"])
        label = contribution["label"]
        if isinstance(label, dict):
            label = label.get("ja") or label.get("en") or contribution["id"]
        result.append({
            "type": "function",
            "function": {
                "name": name,
                "description": f"{contribution['addon_id']} Add-on: {label}"[:240],
                "parameters": parameters,
            },
        })
    for name, value in list(_agent_tool_map.items()):
        if value[0] != revision:
            _agent_tool_map.pop(name, None)
    return result


def _agent_target(name: str) -> tuple[str, str] | None:
    value = _agent_tool_map.get(name)
    if value is None or value[0] != registry.revision():
        return None
    return value[1], value[2]


def execution_owner(execution_id: object) -> int | None:
    try:
        parsed = int(execution_id)
    except (TypeError, ValueError):
        return None
    from app.database import SessionLocal
    from app.models import Workflow, WorkflowExecution

    with SessionLocal() as db:
        execution = db.get(WorkflowExecution, parsed)
        workflow = db.get(Workflow, execution.workflow_id) if execution else None
        return workflow.created_by if workflow else None


def execution_permissions(execution_id: object) -> set[str]:
    owner = execution_owner(execution_id)
    if owner is None:
        return set()
    from app.database import SessionLocal
    from app.models import User
    from app.security.deps import user_permissions

    with SessionLocal() as db:
        user = db.get(User, owner)
        return user_permissions(user) if user and user.is_active else set()


async def create_agent_tool_job(
    addon_id: str,
    contribution_id: str,
    arguments: dict[str, Any],
    *,
    owner_user_id: int | None,
    permissions: set[str] | None,
) -> Any:
    from app.jobs import service as jobs

    tool_schema = await agent_schema(addon_id, contribution_id, permissions=permissions)
    _reject_unscoped_host_paths(arguments)
    validate(tool_schema, arguments, label="agent tool input")

    async def runner(job) -> dict[str, Any]:
        job.log("Add-on agent toolを実行しています")
        output = await invoke(
            "agent_tools", addon_id, contribution_id,
            {"input": arguments, "correlation": {"job_id": job.id}},
            subject=f"job:{job.id}", actor_user_id=owner_user_id, permissions=permissions,
        )
        return {
            "job_id": job.id,
            "asset_id": f"job-result:{job.id}",
            "output": output,
        }

    return jobs.create(
        f"addon.agent_tool.{addon_id}.{contribution_id}",
        f"{addon_id}: {contribution_id}",
        runner,
        owner_user_id=owner_user_id,
    )


async def wait_agent_tool_job(job: Any, *, timeout: float = EXECUTION_TIMEOUT_SECONDS) -> dict[str, Any]:
    from app.jobs import service as jobs

    deadline = asyncio.get_running_loop().time() + max(1.0, min(timeout, EXECUTION_TIMEOUT_SECONDS))
    try:
        while job.status in {"queued", "running"}:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            job.changed.clear()
            if job.status in {"queued", "running"}:
                await asyncio.wait_for(job.changed.wait(), timeout=remaining)
    except TimeoutError as exc:
        await jobs.cancel_and_wait(job.id)
        raise AddonExecutionError(
            "Add-on agent tool Jobがtimeoutしました", code="agent_tool_timeout", status_code=504,
        ) from exc
    except asyncio.CancelledError:
        await jobs.cancel_and_wait(job.id)
        raise
    if job.task is not None and job.task is not asyncio.current_task():
        await asyncio.gather(job.task, return_exceptions=True)
    if job.status != "succeeded" or not isinstance(job.result, dict):
        raise AddonExecutionError(
            "Add-on agent tool Jobが失敗しました", code=job.error or "agent_tool_failed", status_code=502,
        )
    return job.result


def _reject_unscoped_host_paths(value: Any) -> None:
    """Keep raw host paths out of arguments crossing the Add-on boundary."""
    if isinstance(value, dict):
        for nested in value.values():
            _reject_unscoped_host_paths(nested)
        return
    if isinstance(value, list):
        for nested in value:
            _reject_unscoped_host_paths(nested)
        return
    if isinstance(value, str) and (value.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:[\\\\/]", value)):
        raise AddonExecutionError(
            "agent toolにはhost pathではなくasset/grant IDを指定してください",
            code="unscoped_host_path", status_code=422,
        )


async def invoke_agent_tool_name(name: str, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    target = _agent_target(name)
    if target is None:
        return None
    execution_id = context.get("__execution_id")
    permissions = execution_permissions(execution_id)
    if not permissions:
        raise AddonExecutionError("agent toolの実行userを解決できません", code="agent_owner_unavailable", status_code=403)
    job = await create_agent_tool_job(
        *target, arguments,
        owner_user_id=execution_owner(execution_id), permissions=permissions,
    )
    return await wait_agent_tool_job(job)


def validate_context_reference(
    context_type: str,
    resource_id: str,
    *,
    addon_id: str,
    owner_user_id: int,
) -> tuple[str, str | None]:
    if context_type == "file" and resource_id.startswith("/"):
        try:
            grant = runtime_grants.create(addon_id, owner_user_id, resource_id, "read")
        except (runtime_grants.GrantError, PermissionError, FileNotFoundError, OSError) as exc:
            raise AddonExecutionError(str(exc), code="context_not_found", status_code=404) from exc
        return grant["grant_id"], grant["grant_id"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", resource_id):
        raise AddonExecutionError("context resource_idはopaque IDで指定してください", code="invalid_context", status_code=422)
    if context_type == "project":
        from app.project_lab.service import ProjectLabError, resolve_project

        try:
            resolve_project(resource_id)
        except ProjectLabError as exc:
            raise AddonExecutionError(str(exc), code="context_not_found", status_code=404) from exc
        return resource_id, None
    from app.database import SessionLocal
    from app.models import Job, Workflow

    if context_type == "file":
        # File context accepts only an already opaque host asset/grant identifier, never a path.
        if not resource_id.startswith(("asset:", "grant:")):
            raise AddonExecutionError("file contextにはhostのasset/grant IDが必要です", code="invalid_context", status_code=422)
        return resource_id, resource_id if resource_id.startswith("grant:") else None
    with SessionLocal() as db:
        if context_type == "workflow":
            try:
                row = db.get(Workflow, int(resource_id))
            except ValueError:
                row = None
            if row is None:
                raise AddonExecutionError("workflowが見つかりません", code="context_not_found", status_code=404)
        elif context_type == "job":
            row = db.get(Job, resource_id)
            if row is None or row.owner_user_id not in {None, owner_user_id}:
                raise AddonExecutionError("jobが見つかりません", code="context_not_found", status_code=404)
        else:
            raise AddonExecutionError("context typeが不正です", code="invalid_context", status_code=422)
    return resource_id, None


def validate_context_result(addon_id: str, value: dict[str, Any]) -> dict[str, Any]:
    action = value.get("action")
    if action is None:
        return value
    if action != "open_route":
        raise AddonExecutionError(
            "Context Action応答のactionが不正です",
            code="invalid_context_response",
            status_code=502,
        )
    route = value.get("route")
    if not isinstance(route, str) or len(route) > 2048 or any(ord(character) < 32 for character in route):
        raise AddonExecutionError(
            "Context Action応答のrouteが不正です",
            code="invalid_context_response",
            status_code=502,
        )
    parsed = urlsplit(route)
    try:
        decoded_path = unquote(parsed.path, errors="strict")
    except UnicodeDecodeError as exc:
        raise AddonExecutionError(
            "Context Action応答のrouteが不正です",
            code="invalid_context_response",
            status_code=502,
        ) from exc
    prefix = f"/x/{addon_id}"
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or "\\" in decoded_path
        or any(ord(character) < 32 for character in decoded_path)
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        or (decoded_path != prefix and not decoded_path.startswith(f"{prefix}/"))
    ):
        raise AddonExecutionError(
            "Context Actionは自身のAdd-on routeだけを開けます",
            code="invalid_context_response",
            status_code=502,
        )
    return value


async def invoke_context_action(
    addon_id: str,
    contribution_id: str,
    *,
    context_type: str,
    resource_id: str,
    input_value: dict[str, Any],
    owner_user_id: int,
    permissions: set[str],
) -> dict[str, Any]:
    contribution = find_for_user("context_actions", addon_id, contribution_id, permissions)
    if context_type not in contribution["contexts"]:
        raise AddonExecutionError("このactionは指定contextを受け付けません", code="invalid_context", status_code=422)
    scoped_resource_id, grant_id = validate_context_reference(
        context_type,
        resource_id,
        addon_id=addon_id,
        owner_user_id=owner_user_id,
    )
    result = await invoke(
        "context_actions", addon_id, contribution_id,
        {
            "input": input_value,
            "context": {"type": context_type, "resource_id": scoped_resource_id, "grant_id": grant_id},
        },
        subject=f"context:{owner_user_id}",
        actor_user_id=owner_user_id,
        grant_ids=(grant_id,) if grant_id is not None else (),
        permissions=permissions,
    )
    return validate_context_result(addon_id, result)


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


def _json_type(value: Any) -> str:
    if isinstance(value, list):
        value = next((item for item in value if item != "null"), "string")
    return value if value in {"string", "number", "integer", "boolean", "object", "array"} else "string"


def _output_types(schema_value: dict[str, Any]) -> dict[str, str]:
    properties = schema_value.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {
        str(key): _json_type(value.get("type") if isinstance(value, dict) else None)
        for key, value in properties.items()
    }


async def workflow_catalog(permissions: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for contribution in discover("workflow_executors", permissions):
        try:
            input_schema, output_schema = await workflow_schemas(
                contribution["addon_id"], contribution["id"], permissions=permissions,
            )
        except AddonExecutionError:
            continue
        properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
        required = set(input_schema.get("required") or [])
        label = contribution["label"]
        if isinstance(label, dict):
            label = label.get("ja") or label.get("en") or next(iter(label.values()), contribution["id"])
        config_schema = {
            str(key): {
                "type": _json_type(value.get("type") if isinstance(value, dict) else None),
                "required": key in required,
                "reason": str(value.get("description") or "Add-onが宣言した入力です")[:300]
                if isinstance(value, dict) else "Add-onが宣言した入力です",
                **({"default": value["default"]} if isinstance(value, dict) and "default" in value else {}),
            }
            for key, value in properties.items()
        }
        initial_config = {
            key: value["default"] for key, value in properties.items()
            if isinstance(value, dict) and "default" in value
        }
        result.append({
            "type": workflow_node_type(contribution["addon_id"], contribution["id"]),
            "version": 1,
            "metadata_version": 3,
            "description": f"{contribution['addon_id']} Add-onのremote executor「{label}」を実行します。",
            "side_effect": "external",
            "capabilities": ["addon.remote", "network"],
            "config_schema": config_schema,
            "initial_config": initial_config,
            "input_schema": input_schema,
            "output_schema": _output_types(output_schema),
            "ui_hints": {
                "help": "入力と出力はAdd-onのJSON Schemaでhostが検証します。無効化後も保存済みnodeは削除されません。",
                "quick_start": "必須入力を設定し、安全プレビューでschemaを確認してから実行してください。",
                "variable_picker": True,
                "show_recommended_defaults": True,
                "primary_input": next(iter(properties), None),
                "primary_output": next(iter(_output_types(output_schema)), None),
                "examples": [],
            },
            "security": {"allowed_in_generated_app": False, "requires_secret_reference": False},
            "supports": {"retry": True, "cancel": True, "progress": False, "dry_run": True},
            "addon": {
                "id": contribution["addon_id"], "contribution_id": contribution["id"], "label": label,
            },
        })
    return result


def _render_input(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        from app.workflows.nodes import render_template

        rendered = render_template(value, context)
        stripped = rendered.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        return rendered
    if isinstance(value, list):
        return [_render_input(item, context) for item in value]
    if isinstance(value, dict):
        return {str(key): _render_input(item, context) for key, item in value.items()}
    return value


async def execute_workflow_node(node_type: str, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_workflow_node_type(node_type)
    if parsed is None:
        raise AddonExecutionError("remote workflow node typeが不正です", code="invalid_node_type", status_code=422)
    addon_id, contribution_id = parsed
    input_schema, output_schema = await workflow_schemas(addon_id, contribution_id)
    payload = {
        key: _render_input(value, context) for key, value in config.items()
        if key not in _WORKFLOW_INTERNAL_KEYS
    }
    validate(input_schema, payload, label="workflow input")
    execution_id = config.get("__execution_id")
    actor_user_id = execution_owner(execution_id)
    if actor_user_id is None:
        raise AddonExecutionError(
            "workflow execution ownerを解決できません",
            code="workflow_owner_unavailable",
            status_code=403,
        )
    output = await invoke(
        "workflow_executors", addon_id, contribution_id,
        {
            "input": payload,
            "correlation": {
                "execution_id": str(execution_id) if execution_id is not None else None,
                "node_id": str(config.get("__node_id") or "") or None,
            },
        },
        subject=f"workflow:{execution_id if execution_id is not None else 'preview'}",
        actor_user_id=actor_user_id,
    )
    validate(output_schema, output, label="workflow output")
    return output


def workflow_executor(node_type: str) -> Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]] | None:
    if not is_workflow_node_type(node_type):
        return None

    async def execute(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        try:
            return await execute_workflow_node(node_type, config, context)
        except AddonExecutionError as exc:
            from app.workflows.nodes import NodeError

            raise NodeError(
                str(exc), code=exc.code.upper(),
                retryable=exc.code in {"upstream_unavailable", "upstream_error", "contribution_unavailable"},
            ) from exc

    return execute


def dry_run_workflow_node(node_type: str, config: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_workflow_node_type(node_type)
    if parsed is None:
        raise AddonExecutionError("remote workflow node typeが不正です", code="invalid_node_type", status_code=422)
    addon_id, contribution_id = parsed
    # Discovery populates the schema cache. Dry-run itself never performs external I/O.
    input_schema = cached_workflow_input_schema(node_type)
    errors: list[str] = []
    if input_schema is None:
        errors.append("schema cacheがありません。node catalogを再取得してください")
    else:
        try:
            validate(
                input_schema,
                {key: value for key, value in config.items() if key not in _WORKFLOW_INTERNAL_KEYS},
                label="workflow input",
            )
        except AddonExecutionError as exc:
            errors.append(str(exc))
    return {
        "ok": not errors,
        "dry_run": True,
        "type": node_type,
        "status": "SIMULATED" if not errors else "BLOCKED",
        "description": f"{addon_id}/{contribution_id} remote executor",
        "side_effect": "external",
        "capabilities": ["addon.remote", "network"],
        "config": config,
        "would_output": {},
        "errors": errors,
        "notice": "schema検証だけを行い、Add-on executorは呼び出していません。",
    }


def reset_for_tests() -> None:
    _schema_cache.clear()
    _agent_tool_map.clear()
