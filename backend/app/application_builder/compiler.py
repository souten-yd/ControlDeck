from __future__ import annotations

import copy
from datetime import datetime
import json
import math
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.application_builder.capabilities import FRAMEWORKS, FRAMEWORK_BY_ID, node_support
from app.application_builder.design_system.components import (
    BINDING_SOURCE_IDS, COMPONENT_BY_TYPE, DESIGN_TOKENS, EVENT_ACTION_BY_ID, PRESET_BY_ID,
)
from app.application_builder.diagnostics import Diagnostic, diagnostic
from app.application_builder.ir import (
    ApplicationIR, EdgeIR, ExecutionPolicyIR, NodeCodegenIR, NodeIR, PortIR,
    SecretReferenceIR, WorkflowIR,
)
from app.application_builder.type_system import TypeRef, from_json_schema, is_assignable, parse_type
from app.workflows.contracts import build_input_schema, build_output_schema
from app.workflows.node_metadata import metadata_by_type
from app.workflows.redaction import collect_sensitive_values, is_sensitive_key
from app.schemas.application_builder import ApplicationSpecV1
from pydantic import ValidationError

SECRET_REF = re.compile(r"\{\{\s*secrets\.([A-Za-z0-9_.-]+)\s*\}\}")
BINDING_PREFIXES = BINDING_SOURCE_IDS
SUPPORTED_GENERATED_SCHEMA_KEYWORDS = frozenset({
    "$schema", "$id", "$comment", "title", "description", "default", "examples", "deprecated",
    "readOnly", "writeOnly", "type", "enum", "const", "required", "properties", "additionalProperties",
    "items", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
    "allOf", "anyOf", "oneOf", "not",
})


def compile_workflow(
    definition: dict[str, Any], *, name: str, workflow_id: int | None = None,
    workflow_version_id: int | None = None, target: str = "csharp",
) -> WorkflowIR:
    diagnostics: list[Diagnostic] = []
    metadata = metadata_by_type()
    nodes_raw = definition.get("nodes") if isinstance(definition.get("nodes"), list) else []
    edges_raw = definition.get("edges") if isinstance(definition.get("edges"), list) else []
    input_schema = build_input_schema(definition)
    output_schema = build_output_schema(definition)
    definition_text = json.dumps(definition, ensure_ascii=False)
    secret_names = sorted(set(SECRET_REF.findall(definition_text)))
    secret_aliases = {name: f"SECRET_{index:03d}" for index, name in enumerate(secret_names, 1)}
    inputs = _schema_ports(input_schema)
    outputs = _schema_ports(output_schema)
    nodes: list[NodeIR] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(nodes_raw):
        node_id = str(raw.get("id") or "")
        node_type = str(raw.get("type") or "")
        if not node_id or node_id in by_id:
            diagnostics.append(diagnostic(
                "NODE_ID_INVALID", "error", "ノードIDが空または重複しています",
                path=f"workflow.nodes.{index}.id", source="workflow-compiler",
            ))
            continue
        by_id[node_id] = raw
        meta = metadata.get(node_type)
        if meta is None:
            diagnostics.append(diagnostic(
                "NODE_UNSUPPORTED", "error", f"未知のノード種類です: {node_type}",
                path=f"workflow.nodes.{node_id}", source="workflow-compiler",
            ))
            output_ports: list[PortIR] = []
        else:
            output_ports = []
            for key, value in meta.get("output_schema", {}).items():
                ref, issues = parse_type(value)
                output_ports.append(PortIR(name=key, type=ref))
                diagnostics.extend(_at_path(issues, f"workflow.nodes.{node_id}.outputs.{key}"))
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        if any(value and SECRET_REF.search(value) is None for value in collect_sensitive_values(config)):
            diagnostics.append(diagnostic(
                "WORKFLOW_SECRET_LITERAL_FORBIDDEN", "error",
                "Workflow設定へ秘密値を直接保存できません",
                path=f"workflow.nodes.{node_id}.config", source="security-validator",
                suggested_fix="{{secrets.NAME}}参照を使用してください",
            ))
        support = node_support(node_type, target)
        if support["support"] == "unsupported":
            diagnostics.append(diagnostic(
                "TARGET_NODE_UNSUPPORTED", "error", f"{node_type} は {target} targetで未対応です",
                path=f"workflow.nodes.{node_id}", source="target-validator", suggested_fix=support["reason"],
            ))
        elif support["support"] != "native":
            diagnostics.append(diagnostic(
                "TARGET_NODE_FALLBACK", "warning", f"{node_type} は {support['support']} 方式です",
                path=f"workflow.nodes.{node_id}", source="target-validator", suggested_fix=support["reason"],
            ))
        retry_count = _int(config.get("retry_count"), 0)
        nodes.append(NodeIR(
            id=node_id, node_type=node_type, version=int(raw.get("version") or 1),
            display_name=str(raw.get("name") or node_id), config=_codegen_config(config, secret_aliases),
            inputs=[], outputs=output_ports,
            execution=ExecutionPolicyIR(
                retry_count=retry_count,
                retry_wait_seconds=_float(config.get("retry_wait"), 5 if retry_count else 0),
                timeout_seconds=_optional_float(config.get("node_timeout")), on_error=str(config.get("on_error") or "stop"),
                join_mode=str(config.get("join") or config.get("mode") or "first"),
                requires_approval=node_type == "human.approval" or bool(config.get("require_approval")),
                cancelable=bool((meta or {}).get("supports", {}).get("cancel", True)),
            ),
            codegen=NodeCodegenIR(target=target, **support),
        ))

    edges: list[EdgeIR] = []
    for index, raw in enumerate(edges_raw):
        source, target_node = str(raw.get("source") or ""), str(raw.get("target") or "")
        if source not in by_id or target_node not in by_id:
            diagnostics.append(diagnostic(
                "EDGE_ENDPOINT_MISSING", "error", "接続先ノードが存在しません",
                path=f"workflow.edges.{index}", source="workflow-compiler",
            ))
            continue
        source_port = str(raw.get("source_port") or raw.get("sourceHandle") or "output")
        target_port = str(raw.get("target_port") or raw.get("targetHandle") or "input")
        source_meta = metadata.get(str(by_id[source].get("type") or ""), {})
        raw_type = (source_meta.get("output_schema") or {}).get(source_port)
        edge_type, issues = parse_type(raw_type)
        diagnostics.extend(_at_path(issues, f"workflow.edges.{index}.type"))
        if raw.get("target_type"):
            expected_type, expected_issues = parse_type(raw.get("target_type"))
            diagnostics.extend(_at_path(expected_issues, f"workflow.edges.{index}.target_type"))
            if not is_assignable(edge_type, expected_type):
                diagnostics.append(diagnostic(
                    "TYPE_MISMATCH", "error",
                    f"{edge_type.canonical()} を {expected_type.canonical()} へ接続できません",
                    path=f"workflow.edges.{index}", source="type-validator",
                    suggested_fix="明示的なdata.transformノードを追加してください",
                ))
        branch = raw.get("branch") or raw.get("sourceHandle")
        edges.append(EdgeIR(
            id=str(raw.get("id") or f"edge-{index + 1}"), source_node=source, source_port=source_port,
            target_node=target_node, target_port=target_port, branch=str(branch) if branch else None,
            data_type=edge_type, condition={"branch": branch} if branch else None,
        ))

    for cycle in _find_cycles(edges):
        if not any(str(by_id[node_id].get("type")) == "control.loop" for node_id in cycle if node_id in by_id):
            diagnostics.append(diagnostic(
                "WORKFLOW_CYCLE_UNSUPPORTED", "error", f"許可されていない循環です: {' → '.join(cycle)}",
                path="workflow.edges", source="workflow-compiler",
                suggested_fix="control.loopを使用するか、循環をサブフローへ分離してください",
            ))

    secrets = [SecretReferenceIR(name=item) for item in secret_names]
    capabilities = sorted({cap for raw in nodes_raw for cap in (metadata.get(str(raw.get("type")), {}).get("capabilities") or [])})
    side_effects = sorted({effect for raw in nodes_raw if (effect := metadata.get(str(raw.get("type")), {}).get("side_effect", "none")) != "none"})
    return WorkflowIR(
        workflow_id=workflow_id, workflow_version_id=workflow_version_id, name=name,
        inputs=inputs, outputs=outputs, nodes=nodes, edges=edges, required_secrets=secrets,
        capabilities=capabilities, side_effects=side_effects, diagnostics=diagnostics,
    )


def _codegen_config(config: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    """Redact literals while retaining opaque, value-free Secret positions for generators."""
    sensitive_values = {
        value for value in collect_sensitive_values(config)
        if value and SECRET_REF.search(value) is None
    }

    def visit(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {str(child_key): visit(child, str(child_key)) for child_key, child in value.items()}
        if isinstance(value, list):
            return [visit(child, key) for child in value]
        if isinstance(value, tuple):
            return [visit(child, key) for child in value]
        if is_sensitive_key(key):
            if not isinstance(value, str) or SECRET_REF.search(value) is None:
                return "***"
        if isinstance(value, str):
            result = SECRET_REF.sub(lambda match: "{{secrets." + aliases[match.group(1)] + "}}", value)
            for sensitive in sensitive_values:
                result = result.replace(sensitive, "***")
            return result
        return copy.deepcopy(value)

    return visit(config)


def _schema_keyword_issues(schema: Any, path: str) -> list[Diagnostic]:
    """Return keywords that the dependency-free generated .NET validator cannot enforce."""
    if not isinstance(schema, dict):
        return []
    issues: list[Diagnostic] = []
    for key in schema:
        if key not in SUPPORTED_GENERATED_SCHEMA_KEYWORDS:
            issues.append(diagnostic(
                "API_SCHEMA_KEYWORD_UNSUPPORTED", "error",
                f"JSON Schema keyword '{key}' は生成runtimeで未対応です",
                path=f"{path}.{key}", source="api-validator",
                suggested_fix="対応keywordだけへ置き換えるかschemaを空にしてください",
            ))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            issues.extend(_schema_keyword_issues(child, f"{path}.properties.{name}"))
    items = schema.get("items")
    if isinstance(items, dict):
        issues.extend(_schema_keyword_issues(items, f"{path}.items"))
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        issues.extend(_schema_keyword_issues(additional, f"{path}.additionalProperties"))
    for keyword in ("allOf", "anyOf", "oneOf"):
        children = schema.get(keyword)
        if isinstance(children, list):
            for index, child in enumerate(children):
                issues.extend(_schema_keyword_issues(child, f"{path}.{keyword}.{index}"))
    if isinstance(schema.get("not"), dict):
        issues.extend(_schema_keyword_issues(schema["not"], f"{path}.not"))
    return issues


def _api_schema_issues(schema: Any, path: str) -> list[Diagnostic]:
    if schema in (None, {}):
        return []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        detail = exc.message if isinstance(exc.message, str) else "JSON Schemaが不正です"
        return [diagnostic(
            "API_SCHEMA_INVALID", "error", detail[:500], path=path, source="api-validator",
        )]
    return _schema_keyword_issues(schema, path)


def compile_application(spec: dict[str, Any]) -> ApplicationIR:
    app = spec.get("application") if isinstance(spec.get("application"), dict) else {}
    return ApplicationIR(
        schema_version=int(spec.get("schemaVersion") or 1), name=str(app.get("name") or "Application"),
        display_name=str(app.get("displayName") or app.get("name") or "Application"),
        application_type=str(app.get("applicationType") or "web"),
        theme=copy.deepcopy(spec.get("theme") or {}), navigation=copy.deepcopy(spec.get("navigation") or {}),
        pages=copy.deepcopy(spec.get("pages") or []), entities=copy.deepcopy(spec.get("entities") or []),
        api_endpoints=copy.deepcopy(spec.get("apiEndpoints") or []),
        background_jobs=copy.deepcopy(spec.get("backgroundJobs") or []),
        client_state=copy.deepcopy(spec.get("clientState") or []),
        queries=copy.deepcopy(spec.get("queries") or []),
        workflows=copy.deepcopy(spec.get("workflows") or []), permissions=copy.deepcopy(spec.get("permissions") or []),
        targets=copy.deepcopy(spec.get("targets") or []), diagnostics=validate_application_spec(spec),
    )


def validate_application_spec(spec: dict[str, Any]) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    try:
        ApplicationSpecV1.model_validate(spec)
    except ValidationError as exc:
        for item in exc.errors(include_url=False):
            issues.append(diagnostic(
                "SPEC_SCHEMA_INVALID", "error", str(item.get("msg") or "Application Specが不正です"),
                path=".".join(str(part) for part in item.get("loc") or ()), source="spec-schema",
            ))
    if spec.get("schemaVersion") != 1:
        issues.append(diagnostic("SPEC_VERSION_UNSUPPORTED", "error", "schemaVersionは1にしてください", path="schemaVersion"))
    app = spec.get("application") if isinstance(spec.get("application"), dict) else {}
    name = str(app.get("name") or "")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name):
        issues.append(diagnostic(
            "APP_IDENTIFIER_INVALID", "error", "application.nameは英字始まりのidentifierにしてください",
            path="application.name", suggested_fix="例: ServerMonitor",
        ))
    ids_by_section: dict[str, set[str]] = {}
    for section in ("pages", "entities", "apiEndpoints", "backgroundJobs", "clientState", "queries", "workflows", "targets"):
        seen: set[str] = set()
        for index, item in enumerate(spec.get(section) or []):
            item_id = str(item.get("id") or "") if isinstance(item, dict) else ""
            if not item_id:
                issues.append(diagnostic("SPEC_ID_REQUIRED", "error", f"{section}のidが必要です", path=f"{section}.{index}.id"))
            elif item_id in seen:
                issues.append(diagnostic("SPEC_ID_DUPLICATE", "error", f"id '{item_id}' が重複しています", path=f"{section}.{index}.id"))
            seen.add(item_id)
        ids_by_section[section] = seen
    for index, item in enumerate((spec.get("navigation") or {}).get("items") or []):
        page_id = str(item.get("pageId") or "") if isinstance(item, dict) else ""
        if page_id and page_id not in ids_by_section["pages"]:
            issues.append(diagnostic("PAGE_REFERENCE_MISSING", "error", f"page '{page_id}' が存在しません", path=f"navigation.items.{index}.pageId"))
    _validate_entities(spec, issues)
    _validate_client_state(spec, issues)
    _validate_queries(spec, issues)
    workflow_bindings = {
        int(item["workflowId"])
        for item in spec.get("workflows") or []
        if isinstance(item, dict) and isinstance(item.get("workflowId"), int)
    }
    endpoint_routes: set[tuple[str, str]] = set()
    for index, endpoint in enumerate(spec.get("apiEndpoints") or []):
        if not isinstance(endpoint, dict):
            continue
        route = (str(endpoint.get("method") or "POST").upper(), str(endpoint.get("path") or ""))
        route_key = (route[0], re.sub(r"\{[A-Za-z][A-Za-z0-9_]*\}", "{}", route[1]))
        if route_key in endpoint_routes:
            issues.append(diagnostic(
                "API_ROUTE_DUPLICATE", "error", f"API route '{route[0]} {route[1]}' が重複しています",
                path=f"apiEndpoints.{index}.path", source="api-validator",
            ))
        endpoint_routes.add(route_key)
        workflow_id = endpoint.get("workflowId")
        if isinstance(workflow_id, int) and workflow_id not in workflow_bindings:
            issues.append(diagnostic(
                "API_WORKFLOW_REFERENCE_MISSING", "error", f"Workflow #{workflow_id} のbindingが存在しません",
                path=f"apiEndpoints.{index}.workflowId", source="api-validator",
            ))
        parameters = re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", route[1])
        if len(parameters) != len(set(parameters)):
            issues.append(diagnostic(
                "API_PATH_PARAMETER_DUPLICATE", "error", "同じpath parameterを複数回使用できません",
                path=f"apiEndpoints.{index}.path", source="api-validator",
            ))
        if endpoint.get("authentication") == "anonymous":
            issues.append(diagnostic(
                "API_ANONYMOUS_EXPLICIT", "warning", f"{route[0]} {route[1]} は認証なしで公開されます",
                path=f"apiEndpoints.{index}.authentication", source="security-validator",
                suggested_fix="公開が不要ならauthenticationをinheritへ戻してください",
            ))
        issues.extend(_api_schema_issues(endpoint.get("requestSchema"), f"apiEndpoints.{index}.requestSchema"))
        issues.extend(_api_schema_issues(endpoint.get("responseSchema"), f"apiEndpoints.{index}.responseSchema"))
    for index, job in enumerate(spec.get("backgroundJobs") or []):
        if not isinstance(job, dict):
            continue
        workflow_id = job.get("workflowId")
        if isinstance(workflow_id, int) and workflow_id not in workflow_bindings:
            issues.append(diagnostic(
                "JOB_WORKFLOW_REFERENCE_MISSING", "error", f"Workflow #{workflow_id} のbindingが存在しません",
                path=f"backgroundJobs.{index}.workflowId", source="job-validator",
            ))
        trigger = str(job.get("trigger") or "manual")
        schedule = str(job.get("schedule") or "").strip()
        if trigger != "manual" and not schedule:
            issues.append(diagnostic(
                "JOB_SCHEDULE_REQUIRED", "error", f"{trigger} jobにはscheduleが必要です",
                path=f"backgroundJobs.{index}.schedule", source="job-validator",
            ))
        if trigger == "interval" and schedule:
            try:
                interval_seconds = float(schedule)
                valid_interval = 1 <= interval_seconds <= 31_536_000
            except (TypeError, ValueError):
                valid_interval = False
            if not valid_interval:
                issues.append(diagnostic(
                    "JOB_INTERVAL_INVALID", "error", "interval scheduleは1〜31536000秒で指定してください",
                    path=f"backgroundJobs.{index}.schedule", source="job-validator",
                ))
        if trigger == "daily" and schedule and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule):
            issues.append(diagnostic(
                "JOB_DAILY_INVALID", "error", "daily scheduleは24時間表記のHH:MMで指定してください",
                path=f"backgroundJobs.{index}.schedule", source="job-validator",
            ))
        if trigger == "cron" and schedule:
            cron_valid = len(schedule.split()) == 5
            if cron_valid:
                try:
                    cron_valid = croniter.is_valid(schedule)
                except (TypeError, ValueError, KeyError):
                    cron_valid = False
            if not cron_valid:
                issues.append(diagnostic(
                    "JOB_CRON_INVALID", "error", "cron scheduleは有効な5-field式で指定してください",
                    path=f"backgroundJobs.{index}.schedule", source="job-validator",
                ))
        if trigger == "manual" and schedule:
            issues.append(diagnostic(
                "JOB_MANUAL_SCHEDULE_UNUSED", "warning", "manual jobのscheduleは使用されません",
                path=f"backgroundJobs.{index}.schedule", source="job-validator",
            ))
        time_zone = str(job.get("timeZone") or "UTC")
        try:
            ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            issues.append(diagnostic(
                "JOB_TIME_ZONE_INVALID", "error", f"time zone '{time_zone}' が見つかりません",
                path=f"backgroundJobs.{index}.timeZone", source="job-validator",
            ))
    known_frameworks = {item["id"] for item in FRAMEWORKS}
    for index, target in enumerate(spec.get("targets") or []):
        framework = str(target.get("framework") or "") if isinstance(target, dict) else ""
        if framework not in known_frameworks:
            issues.append(diagnostic("TARGET_UNKNOWN", "error", f"framework '{framework}' は未登録です", path=f"targets.{index}.framework"))
            continue
        platforms = target.get("platforms") if isinstance(target, dict) else None
        if not isinstance(platforms, list) or not platforms:
            issues.append(diagnostic("TARGET_PLATFORM_REQUIRED", "error", "platformを1件以上指定してください", path=f"targets.{index}.platforms", source="target-validator"))
            continue
        allowed_platforms = set(FRAMEWORK_BY_ID[framework]["platforms"])
        unsupported = [str(item) for item in platforms if not isinstance(item, str) or item not in allowed_platforms]
        if unsupported:
            issues.append(diagnostic(
                "TARGET_PLATFORM_UNSUPPORTED", "error", f"{framework}は次のplatformへ対応しません: {', '.join(unsupported)}",
                path=f"targets.{index}.platforms", source="target-validator",
            ))
    llm_runtime = spec.get("llmRuntime") if isinstance(spec.get("llmRuntime"), dict) else {}
    if llm_runtime.get("mode") == "external" and llm_runtime.get("bundleRuntime") is True:
        issues.append(diagnostic(
            "LLM_RUNTIME_BUNDLE_CONFLICT", "error", "External providerではLLM runtimeを同梱できません",
            path="llmRuntime.bundleRuntime", source="target-validator",
            suggested_fix="bundleRuntimeをfalseにし、LM StudioまたはOllamaへ接続してください",
        ))
    _validate_theme(spec, issues)
    _scan_security_and_bindings(spec, issues)
    _validate_component_trees(spec, issues, ids_by_section)
    if not spec.get("pages"):
        issues.append(diagnostic("GUI_EMPTY", "suggestion", "ページはまだありません", path="pages", source="gui-validator"))
    return issues


def _validate_client_state(spec: dict[str, Any], issues: list[Diagnostic]) -> None:
    states = spec.get("clientState")
    if not isinstance(states, list):
        return
    total_bytes = 0
    for index, state in enumerate(states):
        if not isinstance(state, dict) or "initialValue" not in state:
            continue
        value = state.get("initialValue")
        state_type = str(state.get("type") or "")
        nullable = state.get("nullable") is True
        valid = value is None and nullable
        if value is not None:
            valid = {
                "string": isinstance(value, str),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
                "boolean": isinstance(value, bool),
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
            }.get(state_type, False)
        if not valid:
            issues.append(diagnostic(
                "CLIENT_STATE_INITIAL_TYPE_INVALID", "error",
                f"client state '{state.get('id')}' のinitialValueが{state_type}と一致しません",
                path=f"clientState.{index}.initialValue", source="state-validator",
            ))
            continue
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            issues.append(diagnostic(
                "CLIENT_STATE_INITIAL_INVALID", "error", "client stateのinitialValueは有効なJSON値にしてください",
                path=f"clientState.{index}.initialValue", source="state-validator",
            ))
            continue
        total_bytes += len(encoded)
        if len(encoded) > 65_536:
            issues.append(diagnostic(
                "CLIENT_STATE_INITIAL_TOO_LARGE", "error", "client stateのinitialValueは64KiB以下にしてください",
                path=f"clientState.{index}.initialValue", source="state-validator",
            ))
    if total_bytes > 262_144:
        issues.append(diagnostic(
            "CLIENT_STATE_TOTAL_TOO_LARGE", "error", "client state初期値の合計は256KiB以下にしてください",
            path="clientState", source="state-validator",
        ))


def _validate_queries(spec: dict[str, Any], issues: list[Diagnostic]) -> None:
    entities = {
        str(item.get("id")): item
        for item in spec.get("entities") or []
        if isinstance(item, dict) and item.get("id")
    }
    endpoints = {
        str(item.get("id")): item
        for item in spec.get("apiEndpoints") or []
        if isinstance(item, dict) and item.get("id")
    }
    for index, query in enumerate(spec.get("queries") or []):
        if not isinstance(query, dict):
            continue
        source = str(query.get("source") or "entity")
        if source == "api":
            endpoint_id = str(query.get("endpointId") or "")
            endpoint = endpoints.get(endpoint_id)
            if endpoint is None:
                issues.append(diagnostic(
                    "QUERY_API_ENDPOINT_MISSING", "error", f"query API endpoint '{endpoint_id}' が存在しません",
                    path=f"queries.{index}.endpointId", source="query-validator",
                ))
                continue
            if endpoint.get("mode", "sync") != "sync":
                issues.append(diagnostic(
                    "QUERY_API_ASYNC_UNSUPPORTED", "error", "API queryはsync endpointだけを使用できます",
                    path=f"queries.{index}.endpointId", source="query-validator",
                ))
            if "{" in str(endpoint.get("path") or ""):
                issues.append(diagnostic(
                    "QUERY_API_ROUTE_PARAMETER_UNSUPPORTED", "error", "API queryはroute parameter付きendpointを使用できません",
                    path=f"queries.{index}.endpointId", source="query-validator",
                ))
            request_schema = endpoint.get("requestSchema") if isinstance(endpoint.get("requestSchema"), dict) else {}
            query_input = query.get("input") if isinstance(query.get("input"), dict) else {}
            try:
                input_size = len(json.dumps(query_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
            except (TypeError, ValueError):
                input_size = 65_537
            if input_size > 65_536:
                issues.append(diagnostic(
                    "QUERY_API_INPUT_TOO_LARGE", "error", "API query inputは64KiB以下のJSON objectにしてください",
                    path=f"queries.{index}.input", source="query-validator",
                ))
            elif request_schema:
                try:
                    input_errors = Draft202012Validator(request_schema).iter_errors(query_input)
                    for error_index, error in enumerate(input_errors):
                        if error_index >= 20:
                            break
                        suffix = ".".join(str(part) for part in error.absolute_path)
                        issues.append(diagnostic(
                            "QUERY_API_INPUT_INVALID", "error", str(error.message)[:500],
                            path=f"queries.{index}.input" + (f".{suffix}" if suffix else ""), source="query-validator",
                        ))
                except (SchemaError, TypeError, ValueError):
                    pass  # API schema validator emits the authoritative schema diagnostic.
            result_schema = _schema_at_path(
                endpoint.get("responseSchema") if isinstance(endpoint.get("responseSchema"), dict) else {},
                str(query.get("resultPath") or ""),
            )
            if not _is_object_collection_schema(result_schema):
                issues.append(diagnostic(
                    "QUERY_API_RESULT_NOT_COLLECTION", "error",
                    "API queryのresponseSchema/resultPathはobject itemのarrayを指す必要があります",
                    path=f"queries.{index}.resultPath", source="query-validator",
                ))
            if query.get("filters") or query.get("sort") or query.get("pagination", "offset") != "none":
                issues.append(diagnostic(
                    "QUERY_API_COLLECTION_OPTIONS_UNSUPPORTED", "error",
                    "API queryのfilter／sort／paginationはendpoint input側で明示してください",
                    path=f"queries.{index}", source="query-validator",
                ))
            continue
        entity_id = str(query.get("entityId") or "")
        entity = entities.get(entity_id)
        if entity is None:
            issues.append(diagnostic(
                "QUERY_ENTITY_MISSING", "error", f"query source Entity '{entity_id}' が存在しません",
                path=f"queries.{index}.entityId", source="query-validator",
            ))
            continue
        crud = entity.get("crud") if isinstance(entity.get("crud"), dict) else {}
        operations = crud.get("operations") or []
        if not crud.get("enabled") or "list" not in operations:
            issues.append(diagnostic(
                "QUERY_ENTITY_LIST_UNAVAILABLE", "error",
                f"query source Entity '{entity_id}' のCRUD list operationが公開されていません",
                path=f"queries.{index}.entityId", source="query-validator",
                suggested_fix="EntityのCRUD listを有効にしてください",
            ))
            continue
        fields = _entity_query_fields(entity)
        for filter_index, item in enumerate(query.get("filters") or []):
            if not isinstance(item, dict):
                continue
            field_name = str(item.get("field") or "")
            field = fields.get(field_name)
            operator = str(item.get("operator") or "")
            if field is None:
                issues.append(diagnostic(
                    "QUERY_FILTER_FIELD_MISSING", "error", f"filter field '{field_name}' がEntityにありません",
                    path=f"queries.{index}.filters.{filter_index}.field", source="query-validator",
                ))
                continue
            allowed = _query_operators(field[0], field[1])
            if operator not in allowed:
                issues.append(diagnostic(
                    "QUERY_FILTER_OPERATOR_INVALID", "error",
                    f"{field_name} ({field[0]}) ではoperator '{operator}' を使用できません",
                    path=f"queries.{index}.filters.{filter_index}.operator", source="query-validator",
                ))
            elif operator != "is-null" and not _query_value_matches(item.get("value"), field[0]):
                issues.append(diagnostic(
                    "QUERY_FILTER_VALUE_INVALID", "error", f"filter valueが{field_name} ({field[0]}) と一致しません",
                    path=f"queries.{index}.filters.{filter_index}.value", source="query-validator",
                ))
        seen_sort: set[str] = set()
        for sort_index, item in enumerate(query.get("sort") or []):
            field_name = str(item.get("field") or "") if isinstance(item, dict) else ""
            if field_name not in fields:
                issues.append(diagnostic(
                    "QUERY_SORT_FIELD_MISSING", "error", f"sort field '{field_name}' がEntityにありません",
                    path=f"queries.{index}.sort.{sort_index}.field", source="query-validator",
                ))
            elif field_name in seen_sort:
                issues.append(diagnostic(
                    "QUERY_SORT_FIELD_DUPLICATE", "error", f"sort field '{field_name}' が重複しています",
                    path=f"queries.{index}.sort.{sort_index}.field", source="query-validator",
                ))
            seen_sort.add(field_name)


def _schema_at_path(schema: dict[str, Any], path: str) -> dict[str, Any]:
    current: Any = schema
    for segment in path.split(".") if path else []:
        properties = current.get("properties") if isinstance(current, dict) else None
        current = properties.get(segment) if isinstance(properties, dict) else None
        if not isinstance(current, dict):
            return {}
    return current if isinstance(current, dict) else {}


def _is_object_collection_schema(schema: dict[str, Any]) -> bool:
    items = schema.get("items") if isinstance(schema, dict) else None
    return schema.get("type") == "array" and isinstance(items, dict) and items.get("type") == "object"


def _entity_query_fields(entity: dict[str, Any]) -> dict[str, tuple[str, bool]]:
    fields: dict[str, tuple[str, bool]] = {
        "id": ("string", False), "createdAt": ("datetime", False), "updatedAt": ("datetime", False),
    }
    for field in entity.get("fields") or []:
        if isinstance(field, dict) and field.get("id"):
            fields[str(field["id"])] = (str(field.get("type") or ""), bool(field.get("nullable")))
    return fields


def _query_operators(field_type: str, nullable: bool) -> set[str]:
    result = {"eq", "ne"}
    if field_type == "string":
        result.update({"contains", "starts-with"})
    if field_type in {"integer", "number", "datetime"}:
        result.update({"gt", "gte", "lt", "lte"})
    if field_type == "json":
        result.clear()
    if nullable:
        result.add("is-null")
    return result


def _query_value_matches(value: Any, field_type: str) -> bool:
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "datetime":
        if not isinstance(value, str):
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.tzinfo is not None
        except ValueError:
            return False
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if field_type == "boolean":
        return isinstance(value, bool)
    return False


def _validate_entities(spec: dict[str, Any], issues: list[Diagnostic]) -> None:
    raw_entities = spec.get("entities")
    entities = [item for item in raw_entities if isinstance(item, dict)] if isinstance(raw_entities, list) else []
    if not entities:
        return
    app = spec.get("application") if isinstance(spec.get("application"), dict) else {}
    if app.get("database") != "sqlite":
        issues.append(diagnostic(
            "ENTITY_DATABASE_UNSUPPORTED", "error", "Entity生成にはapplication.databaseをsqliteへ設定してください",
            path="application.database", source="entity-validator",
        ))
    entity_ids = {str(item.get("id") or "") for item in entities}
    table_names: set[str] = set()
    crud_paths: set[str] = set()
    reserved_fields = {"id", "createdAt", "updatedAt"}
    for entity_index, entity in enumerate(entities):
        entity_id = str(entity.get("id") or "")
        table_name = str(entity.get("tableName") or _snake_case(entity_id))
        if table_name in table_names:
            issues.append(diagnostic(
                "ENTITY_TABLE_DUPLICATE", "error", f"SQLite table '{table_name}' が重複しています",
                path=f"entities.{entity_index}.tableName", source="entity-validator",
            ))
        table_names.add(table_name)
        crud = entity.get("crud") if isinstance(entity.get("crud"), dict) else {}
        if crud.get("enabled"):
            base_path = str(crud.get("basePath") or f"/api/entities/{_kebab_case(entity_id)}")
            if base_path in crud_paths:
                issues.append(diagnostic(
                    "ENTITY_CRUD_ROUTE_DUPLICATE", "error", f"CRUD path '{base_path}' が重複しています",
                    path=f"entities.{entity_index}.crud.basePath", source="entity-validator",
                ))
            crud_paths.add(base_path)
            operations = crud.get("operations") or ["create", "read", "list", "update", "delete"]
            if len(operations) != len(set(operations)):
                issues.append(diagnostic(
                    "ENTITY_CRUD_OPERATION_DUPLICATE", "error", "CRUD operationを重複指定できません",
                    path=f"entities.{entity_index}.crud.operations", source="entity-validator",
                ))
        field_ids: set[str] = set()
        raw_fields = entity.get("fields")
        fields = raw_fields if isinstance(raw_fields, list) else []
        for field_index, field in enumerate(fields):
            if not isinstance(field, dict):
                continue
            field_path = f"entities.{entity_index}.fields.{field_index}"
            field_id = str(field.get("id") or "")
            if field_id in reserved_fields:
                issues.append(diagnostic(
                    "ENTITY_FIELD_RESERVED", "error", f"field '{field_id}' はgenerator管理列です",
                    path=f"{field_path}.id", source="entity-validator",
                ))
            if field_id in field_ids:
                issues.append(diagnostic(
                    "ENTITY_FIELD_DUPLICATE", "error", f"field '{field_id}' が重複しています",
                    path=f"{field_path}.id", source="entity-validator",
                ))
            field_ids.add(field_id)
            field_type = str(field.get("type") or "")
            max_length = field.get("maxLength")
            if max_length is not None and field_type != "string":
                issues.append(diagnostic(
                    "ENTITY_FIELD_MAX_LENGTH_TYPE", "error", "maxLengthはstring fieldだけに指定できます",
                    path=f"{field_path}.maxLength", source="entity-validator",
                ))
            reference = field.get("reference") if isinstance(field.get("reference"), dict) else None
            if reference:
                target = str(reference.get("entityId") or "")
                if target not in entity_ids:
                    issues.append(diagnostic(
                        "ENTITY_REFERENCE_MISSING", "error", f"参照先Entity '{target}' が存在しません",
                        path=f"{field_path}.reference.entityId", source="entity-validator",
                    ))
                if field_type != "string":
                    issues.append(diagnostic(
                        "ENTITY_REFERENCE_TYPE_INVALID", "error", "Entity id参照fieldはstringにしてください",
                        path=f"{field_path}.type", source="entity-validator",
                    ))
                if reference.get("onDelete") == "set-null" and not field.get("nullable", False):
                    issues.append(diagnostic(
                        "ENTITY_REFERENCE_SET_NULL_REQUIRED", "error", "onDelete=set-nullにはnullable=trueが必要です",
                        path=f"{field_path}.nullable", source="entity-validator",
                    ))
            if field.get("hasDefault") and not _entity_default_matches(field.get("default"), field_type, bool(field.get("nullable"))):
                issues.append(diagnostic(
                    "ENTITY_FIELD_DEFAULT_TYPE", "error", f"defaultが{field_type} fieldと一致しません",
                    path=f"{field_path}.default", source="entity-validator",
                ))
            elif field.get("hasDefault") and field_type == "datetime":
                try:
                    parsed_default = datetime.fromisoformat(str(field.get("default")).replace("Z", "+00:00"))
                    valid_datetime = parsed_default.tzinfo is not None
                except ValueError:
                    valid_datetime = False
                if not valid_datetime:
                    issues.append(diagnostic(
                        "ENTITY_FIELD_DEFAULT_DATETIME", "error", "datetime defaultはoffset付きISO 8601にしてください",
                        path=f"{field_path}.default", source="entity-validator",
                    ))
            if (field.get("hasDefault") and field_type == "string" and isinstance(field.get("default"), str)
                    and isinstance(max_length, int) and len(field["default"]) > max_length):
                issues.append(diagnostic(
                    "ENTITY_FIELD_DEFAULT_MAX_LENGTH", "error", "string defaultがmaxLengthを超えています",
                    path=f"{field_path}.default", source="entity-validator",
                ))


def _entity_default_matches(value: Any, field_type: str, nullable: bool) -> bool:
    if value is None:
        return nullable
    if field_type in {"string", "datetime"}:
        return isinstance(value, str)
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "json":
        return True
    return False


def _snake_case(value: str) -> str:
    converted = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"[^a-z0-9_]", "_", converted)


def _kebab_case(value: str) -> str:
    converted = re.sub(r"(?<!^)(?=[A-Z])", "-", value).lower()
    return re.sub(r"[^a-z0-9_-]", "-", converted)


def _validate_component_trees(spec: dict[str, Any], issues: list[Diagnostic], ids_by_section: dict[str, set[str]]) -> None:
    seen: set[str] = set()
    entity_fields = {
        str(entity.get("id")): {"id", "createdAt", "updatedAt", *(
            str(field.get("id")) for field in entity.get("fields", []) if isinstance(field, dict)
        )}
        for entity in spec.get("entities", []) if isinstance(entity, dict) and isinstance(entity.get("fields"), list)
    }
    state_ids = ids_by_section.get("clientState", set())
    endpoint_by_id = {str(item.get("id")): item for item in spec.get("apiEndpoints", []) if isinstance(item, dict)}
    query_info: dict[str, dict[str, Any]] = {}
    entity_by_id = {str(item.get("id")): item for item in spec.get("entities", []) if isinstance(item, dict)}
    for query in spec.get("queries", []):
        if not isinstance(query, dict):
            continue
        source = str(query.get("source") or "entity")
        if source == "entity":
            fields = set(_entity_query_fields(entity_by_id.get(str(query.get("entityId") or ""), {})))
        else:
            endpoint = endpoint_by_id.get(str(query.get("endpointId") or ""), {})
            schema = _schema_at_path(endpoint.get("responseSchema") if isinstance(endpoint.get("responseSchema"), dict) else {}, str(query.get("resultPath") or ""))
            items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
            fields = set((items.get("properties") or {}).keys()) if isinstance(items.get("properties"), dict) else set()
        query_info[str(query.get("id") or "")] = {"source": source, "fields": fields}

    def visit(component: Any, path: str) -> None:
        if not isinstance(component, dict):
            issues.append(diagnostic("COMPONENT_INVALID", "error", "componentはobjectで指定してください", path=path, source="gui-validator"))
            return
        component_id = str(component.get("id") or "")
        component_type = str(component.get("type") or "")
        if component_id in seen:
            issues.append(diagnostic("COMPONENT_ID_DUPLICATE", "error", f"component id '{component_id}' が重複しています", path=f"{path}.id", source="gui-validator"))
        elif component_id:
            seen.add(component_id)
        definition = COMPONENT_BY_TYPE.get(component_type)
        if definition is None:
            issues.append(diagnostic("COMPONENT_TYPE_UNKNOWN", "error", f"component type '{component_type}' は未登録です", path=f"{path}.type", source="gui-validator"))
        children = component.get("children") or []
        if not isinstance(children, list):
            issues.append(diagnostic("COMPONENT_CHILDREN_INVALID", "error", "childrenはarrayで指定してください", path=f"{path}.children", source="gui-validator"))
            return
        if definition is not None and not definition["container"] and children:
            issues.append(diagnostic("COMPONENT_CHILDREN_FORBIDDEN", "error", f"{component_type} は子部品を持てません", path=f"{path}.children", source="gui-validator"))
        if definition is not None:
            properties = component.get("properties") if isinstance(component.get("properties"), dict) else {}
            _validate_component_properties(definition, properties, path, issues)
            _validate_component_binding(
                component.get("binding"), component_type, properties, path, issues,
                entity_fields, state_ids, query_info,
            )
            _validate_component_events(component.get("events"), definition, path, ids_by_section, issues)
            for required in definition.get("accessibility", {}).get("requiredProperties", []):
                # 省略時はcatalogの決定的defaultを使える。明示的な空ラベルだけを拒否する。
                value = properties.get(required, definition.get("defaults", {}).get(required))
                if not isinstance(value, str) or not value.strip():
                    issues.append(diagnostic(
                        "A11Y_LABEL_REQUIRED", "error", f"{component_type} の{required}が必要です",
                        path=f"{path}.properties.{required}", source="accessibility-validator",
                        suggested_fix="意味が伝わる短いラベルを設定してください",
                    ))
        for index, child in enumerate(children):
            visit(child, f"{path}.children.{index}")

    for page_index, page in enumerate(spec.get("pages") or []):
        if isinstance(page, dict) and page.get("root") is not None:
            visit(page["root"], f"pages.{page_index}.root")
            if not str(page.get("title") or "").strip():
                issues.append(diagnostic(
                    "A11Y_PAGE_TITLE_MISSING", "warning", "Page titleがありません",
                    path=f"pages.{page_index}.title", source="accessibility-validator",
                    suggested_fix="navigationと読み上げで識別できるPage titleを設定してください",
                ))


def _validate_component_binding(
    binding: Any, component_type: str, properties: dict[str, Any], path: str, issues: list[Diagnostic],
    entity_fields: dict[str, set[str]], state_ids: set[str], query_info: dict[str, dict[str, Any]],
) -> None:
    if binding in (None, ""):
        return
    source: Any
    reference: Any
    if isinstance(binding, str):
        if ":" not in binding:
            issues.append(diagnostic(
                "BINDING_FORMAT_INVALID", "error", "bindingはsource:reference形式で指定してください",
                path=f"{path}.binding", source="binding-validator",
            ))
            return
        source, reference = binding.split(":", 1)
    elif isinstance(binding, dict):
        if set(binding) - {"source", "reference", "path"}:
            issues.append(diagnostic(
                "BINDING_FORMAT_INVALID", "error", "bindingに未登録fieldを保存できません",
                path=f"{path}.binding", source="binding-validator",
            ))
        source = binding.get("source")
        reference = binding.get("reference", binding.get("path"))
    else:
        issues.append(diagnostic(
            "BINDING_FORMAT_INVALID", "error", "bindingはsource:reference形式で指定してください",
            path=f"{path}.binding", source="binding-validator",
        ))
        return
    if source not in BINDING_SOURCE_IDS:
        issues.append(diagnostic(
            "BINDING_SOURCE_UNKNOWN", "error", f"binding source '{source}' は未登録です",
            path=f"{path}.binding", source="binding-validator",
        ))
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 512:
        issues.append(diagnostic(
            "BINDING_REFERENCE_INVALID", "error", "binding referenceを1〜512文字で指定してください",
            path=f"{path}.binding", source="binding-validator",
        ))
    elif SECRET_REF.search(reference):
        issues.append(diagnostic(
            "BINDING_SECRET_FORBIDDEN", "error", "BindingへSecret参照を保存できません",
            path=f"{path}.binding", source="security-validator",
        ))
    elif source == "entity":
        entity_id, separator, field_id = reference.partition(".")
        if entity_id not in entity_fields:
            issues.append(diagnostic(
                "BINDING_ENTITY_MISSING", "error", f"binding先Entity '{entity_id}' が存在しません",
                path=f"{path}.binding", source="binding-validator",
            ))
        elif separator and field_id not in entity_fields[entity_id]:
            issues.append(diagnostic(
                "BINDING_ENTITY_FIELD_MISSING", "error", f"binding先field '{reference}' が存在しません",
                path=f"{path}.binding", source="binding-validator",
            ))
    elif source == "state" and reference not in state_ids:
        issues.append(diagnostic(
            "BINDING_STATE_MISSING", "error", f"binding先client state '{reference}' が存在しません",
            path=f"{path}.binding", source="binding-validator",
        ))
    elif source == "query":
        if reference not in query_info:
            issues.append(diagnostic(
                "BINDING_QUERY_MISSING", "error", f"binding先query '{reference}' が存在しません",
                path=f"{path}.binding", source="binding-validator",
            ))
        elif component_type != "data.table":
            issues.append(diagnostic(
                "BINDING_QUERY_CONSUMER_UNSUPPORTED", "error",
                f"{component_type} はcollection queryを表示できません",
                path=f"{path}.binding", source="binding-validator",
            ))
        else:
            info = query_info[reference]
            allowed_fields = info["fields"]
            columns = properties.get("columns") if isinstance(properties.get("columns"), list) else []
            for index, column in enumerate(columns):
                key = str(column.get("key") or "") if isinstance(column, dict) else ""
                if key and key not in allowed_fields:
                    issues.append(diagnostic(
                        "BINDING_QUERY_FIELD_MISSING", "error",
                        f"query '{reference}' の結果にfield '{key}' はありません",
                        path=f"{path}.properties.columns.{index}.key", source="binding-validator",
                    ))
            if info["source"] == "api" and any(properties.get(key) is True for key in ("enableCreate", "enableUpdate", "enableDelete")):
                issues.append(diagnostic(
                    "BINDING_QUERY_MUTATION_UNSUPPORTED", "error",
                    "API queryへbindingしたData TableではEntity mutationを有効にできません",
                    path=f"{path}.properties", source="binding-validator",
                ))


def _validate_component_events(
    events: Any, definition: dict[str, Any], path: str,
    ids_by_section: dict[str, set[str]], issues: list[Diagnostic],
) -> None:
    if events in (None, {}):
        return
    if not isinstance(events, dict):
        issues.append(diagnostic(
            "COMPONENT_EVENTS_INVALID", "error", "eventsはobjectで指定してください",
            path=f"{path}.events", source="event-validator",
        ))
        return
    allowed_events = {item["name"]: item for item in definition.get("eventSchema", [])}
    for event_name, config in events.items():
        event_path = f"{path}.events.{event_name}"
        event_definition = allowed_events.get(event_name)
        if event_definition is None:
            issues.append(diagnostic(
                "COMPONENT_EVENT_UNKNOWN", "error", f"{definition['type']} にevent '{event_name}' はありません",
                path=event_path, source="event-validator",
            ))
            continue
        if not isinstance(config, dict):
            issues.append(diagnostic(
                "COMPONENT_EVENT_INVALID", "error", "event設定はobjectで指定してください",
                path=event_path, source="event-validator",
            ))
            continue
        if set(config) - {"action", "target"}:
            issues.append(diagnostic(
                "COMPONENT_EVENT_INVALID", "error", "event設定に未登録fieldを保存できません",
                path=event_path, source="event-validator",
            ))
        action = config.get("action")
        action_definition = EVENT_ACTION_BY_ID.get(action)
        if action_definition is None or action not in event_definition.get("actions", []):
            issues.append(diagnostic(
                "COMPONENT_EVENT_ACTION_INVALID", "error", f"event action '{action}' は使用できません",
                path=f"{event_path}.action", source="event-validator",
            ))
            continue
        target = config.get("target")
        if not isinstance(target, str) or not target.strip() or len(target) > 256:
            issues.append(diagnostic(
                "COMPONENT_EVENT_TARGET_INVALID", "error", "event targetを1〜256文字で指定してください",
                path=f"{event_path}.target", source="event-validator",
            ))
            continue
        if SECRET_REF.search(target):
            issues.append(diagnostic(
                "COMPONENT_EVENT_SECRET_FORBIDDEN", "error", "Event targetへSecret参照を保存できません",
                path=f"{event_path}.target", source="security-validator",
            ))
            continue
        target_section = action_definition.get("targetSection")
        if target_section and target not in ids_by_section.get(target_section, set()):
            issues.append(diagnostic(
                "COMPONENT_EVENT_TARGET_MISSING", "error", f"{target_section} target '{target}' が存在しません",
                path=f"{event_path}.target", source="event-validator",
            ))
        if action == "state-set" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", target):
            issues.append(diagnostic(
                "COMPONENT_EVENT_TARGET_INVALID", "error", "State keyは英字始まりのidentifierにしてください",
                path=f"{event_path}.target", source="event-validator",
            ))


def _validate_component_properties(
    definition: dict[str, Any], properties: dict[str, Any], path: str, issues: list[Diagnostic],
) -> None:
    defaults = definition.get("defaults", {})
    for field in definition.get("propertySchema", []):
        key = str(field.get("key") or "")
        if not key:
            continue
        value = properties.get(key, defaults.get(key))
        if value is None:
            if field.get("required"):
                issues.append(diagnostic(
                    "COMPONENT_PROPERTY_REQUIRED", "error", f"{definition['type']} の{key}が必要です",
                    path=f"{path}.properties.{key}", source="component-property-validator",
                ))
            continue
        field_type = field.get("type")
        valid = (
            (field_type in {"string", "multiline", "enum"} and isinstance(value, str))
            or (field_type == "boolean" and isinstance(value, bool))
            or (field_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (field_type == "json")
            or (field_type == "responsive-columns" and isinstance(value, dict))
            or (field_type in {"table-columns", "chart-series"} and isinstance(value, list))
        )
        if not valid:
            issues.append(diagnostic(
                "COMPONENT_PROPERTY_TYPE_INVALID", "error", f"{key}は{field_type}で指定してください",
                path=f"{path}.properties.{key}", source="component-property-validator",
            ))
            continue
        if field_type in {"responsive-columns", "table-columns", "chart-series"}:
            _validate_structured_property(field, value, f"{path}.properties.{key}", issues)
        if field_type == "enum" and value not in field.get("options", []):
            issues.append(diagnostic(
                "COMPONENT_PROPERTY_VALUE_INVALID", "error", f"{key}の値 '{value}' は未登録です",
                path=f"{path}.properties.{key}", source="component-property-validator",
            ))
        if field_type == "number":
            minimum, maximum = field.get("minimum"), field.get("maximum")
            if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
                issues.append(diagnostic(
                    "COMPONENT_PROPERTY_RANGE_INVALID", "error", f"{key}が許可範囲外です",
                    path=f"{path}.properties.{key}", source="component-property-validator",
                ))


def _validate_structured_property(field: dict[str, Any], value: Any, path: str, issues: list[Diagnostic]) -> None:
    field_type = field.get("type")
    if field_type == "responsive-columns":
        allowed = set(field.get("breakpoints", []))
        for key in value:
            if key not in allowed:
                issues.append(diagnostic(
                    "COMPONENT_PROPERTY_ITEM_INVALID", "error", f"未知のbreakpointです: {key}",
                    path=f"{path}.{key}", source="component-property-validator",
                ))
        for breakpoint in allowed:
            columns = value.get(breakpoint)
            if not isinstance(columns, int) or isinstance(columns, bool) or not field.get("minimum", 1) <= columns <= field.get("maximum", 12):
                issues.append(diagnostic(
                    "COMPONENT_PROPERTY_RANGE_INVALID", "error", f"{breakpoint}列数が許可範囲外です",
                    path=f"{path}.{breakpoint}", source="component-property-validator",
                ))
        return
    maximum = int(field.get("maximumItems") or 0)
    if maximum and len(value) > maximum:
        issues.append(diagnostic(
            "COMPONENT_PROPERTY_ITEMS_EXCEEDED", "error", f"項目数は最大{maximum}件です",
            path=path, source="component-property-validator",
        ))
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}.{index}"
        if not isinstance(item, dict):
            issues.append(diagnostic(
                "COMPONENT_PROPERTY_ITEM_INVALID", "error", "項目はobjectで指定してください",
                path=item_path, source="component-property-validator",
            ))
            continue
        key = item.get("key")
        label = item.get("label")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key):
            issues.append(diagnostic(
                "COMPONENT_PROPERTY_ITEM_INVALID", "error", "keyは英字始まりのidentifierにしてください",
                path=f"{item_path}.key", source="component-property-validator",
            ))
        elif key in seen:
            issues.append(diagnostic(
                "COMPONENT_PROPERTY_ITEM_DUPLICATE", "error", f"key '{key}' が重複しています",
                path=f"{item_path}.key", source="component-property-validator",
            ))
        else:
            seen.add(key)
        if not isinstance(label, str) or not label.strip():
            issues.append(diagnostic(
                "A11Y_LABEL_REQUIRED", "error", "表示ラベルが必要です",
                path=f"{item_path}.label", source="accessibility-validator",
            ))
        if field_type == "table-columns" and item.get("type", "string") not in field.get("columnTypes", []):
            issues.append(diagnostic(
                "COMPONENT_PROPERTY_ITEM_INVALID", "error", "未登録のcolumn typeです",
                path=f"{item_path}.type", source="component-property-validator",
            ))
        if field_type == "chart-series" and item.get("tone", "accent") not in field.get("tones", []):
            issues.append(diagnostic(
                "COMPONENT_PROPERTY_ITEM_INVALID", "error", "未登録のseries toneです",
                path=f"{item_path}.tone", source="component-property-validator",
            ))


def _validate_theme(spec: dict[str, Any], issues: list[Diagnostic]) -> None:
    theme = spec.get("theme")
    if not isinstance(theme, dict):
        return
    preset = theme.get("preset")
    if preset and preset not in PRESET_BY_ID:
        issues.append(diagnostic(
            "THEME_PRESET_UNKNOWN", "error", f"theme preset '{preset}' は未登録です",
            path="theme.preset", source="design-token-validator",
            suggested_fix="schema APIのpresetsから選択してください",
        ))
    tokens = theme.get("tokens", {})
    if not isinstance(tokens, dict):
        issues.append(diagnostic(
            "THEME_TOKENS_INVALID", "error", "theme.tokensはobjectで指定してください",
            path="theme.tokens", source="design-token-validator",
        ))
        return
    for key, value in tokens.items():
        allowed = DESIGN_TOKENS.get(str(key))
        if allowed is None:
            issues.append(diagnostic(
                "DESIGN_TOKEN_UNKNOWN", "error", f"design token '{key}' は未登録です",
                path=f"theme.tokens.{key}", source="design-token-validator",
            ))
        elif value not in allowed:
            issues.append(diagnostic(
                "DESIGN_TOKEN_VALUE_INVALID", "error", f"{key} tokenの値 '{value}' は未登録です",
                path=f"theme.tokens.{key}", source="design-token-validator",
                suggested_fix=f"次から選択してください: {', '.join(allowed)}",
            ))


def default_spec(
    name: str, description: str, workflow_id: int | None,
    *, workflow_version_id: int | None = None, source: str = "draft",
) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9_]", "", name.title().replace(" ", "")) or "GeneratedApplication"
    if not safe[0].isalpha():
        safe = f"App{safe}"
    workflows = [{
        "id": "main", "workflowId": workflow_id, "workflowVersionId": workflow_version_id,
        "source": source, "mode": "hybrid",
    }] if workflow_id else []
    return {
        "schemaVersion": 1,
        "application": {"name": safe[:128], "displayName": name, "description": description, "applicationType": "web", "authentication": "local", "database": "none"},
        "theme": {"preset": "control-deck-modern", "mode": "system", "tokens": {}},
        "navigation": {"type": "sidebar", "items": []}, "pages": [], "entities": [],
        "apiEndpoints": [], "backgroundJobs": [], "clientState": [], "queries": [], "workflows": workflows, "permissions": [],
        "targets": [{"id": "web", "platforms": ["web"], "framework": "aspnet-blazor"}],
        "llmRuntime": {"mode": "none", "provider": None, "bundleRuntime": False,
                       "baseUrlEnvironment": "LLM_BASE_URL", "modelEnvironment": "LLM_MODEL"},
    }


def workflow_app_spec(
    name: str, description: str, workflow_id: int,
    *, input_schema: dict[str, Any], output_schema: dict[str, Any],
    workflow_version_id: int | None = None, source: str = "draft",
) -> dict[str, Any]:
    """Create a runnable GUI baseline directly from a Workflow contract.

    The generated action is intentionally backed by the same request/response
    schemas used by the ASP.NET generator. This keeps the editor suggestion and
    the exported application from drifting apart.
    """
    spec = default_spec(
        name, description, workflow_id,
        workflow_version_id=workflow_version_id, source=source,
    )
    spec["application"]["authentication"] = "api-key"
    def generated_schema(value: Any, *, property_map: bool = False) -> Any:
        if isinstance(value, list):
            return [generated_schema(item) for item in value]
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        if property_map:
            return {str(key): generated_schema(child) for key, child in value.items()}
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key not in SUPPORTED_GENERATED_SCHEMA_KEYWORDS:
                continue
            result[key] = generated_schema(child, property_map=key == "properties")
        return result

    request_schema = generated_schema(input_schema)
    response_schema = generated_schema(output_schema)
    request_properties = request_schema.get("properties") if isinstance(request_schema.get("properties"), dict) else {}
    response_properties = response_schema.get("properties") if isinstance(response_schema.get("properties"), dict) else {}

    def contract_items(properties: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for field_name, raw in properties.items():
            field = raw if isinstance(raw, dict) else {}
            field_type = str(field.get("type") or "object")
            control = {
                "string": "select" if isinstance(field.get("enum"), list) else "text",
                "integer": "number", "number": "number", "boolean": "boolean",
                "array": "json", "object": "json",
            }.get(field_type, "json")
            items.append({
                "name": str(field_name), "label": str(field.get("title") or field_name),
                "type": field_type, "control": control,
            })
        return items

    inputs = contract_items(request_properties)
    outputs = contract_items(response_properties)
    summary = (
        f"{len(inputs)}個の入力からWorkflowを実行し、{len(outputs)}個の出力を表示します。"
        if inputs or outputs else "Workflowを実行し、結果を表示します。"
    )
    spec["navigation"] = {"type": "sidebar", "items": [{"id": "home", "label": "Home", "pageId": "home"}]}
    spec["apiEndpoints"] = [{
        "id": "run-workflow", "method": "POST", "path": "/api/workflow/run",
        "workflowId": workflow_id, "mode": "sync", "authentication": "inherit",
        "requestSchema": request_schema, "responseSchema": response_schema,
        "timeoutSeconds": 120,
    }]
    spec["pages"] = [{
        "id": "home", "title": name, "description": description,
        "root": {
            "id": "workflow-app", "type": "layout.stack",
            "properties": {"gap": "lg", "direction": "vertical"},
            "children": [
                {"id": "app-title", "type": "display.text", "properties": {"text": name}, "children": []},
                {"id": "app-summary", "type": "display.markdown", "properties": {"value": description or summary}, "children": []},
                {"id": "workflow-card", "type": "layout.card", "properties": {"padding": "lg"}, "children": [{
                    "id": "run-workflow", "type": "action.workflow-run", "children": [],
                    "properties": {
                        "label": "実行", "workflowBinding": "main", "endpointId": "run-workflow",
                        "resultLabel": "実行結果", "contractInputs": inputs, "contractOutputs": outputs,
                    },
                }]},
            ],
        },
    }]
    spec["xAppAdvisor"] = {
        "status": "ready", "strategy": "workflow-contract", "version": 1,
        "inputs": inputs, "outputs": outputs,
        "message": "Workflow契約から動作可能な推奨GUIを自動構成しました。",
    }
    return spec


def _schema_ports(schema: dict[str, Any]) -> list[PortIR]:
    required = set(schema.get("required") or [])
    ports = []
    for name, child in (schema.get("properties") or {}).items():
        ref, _issues = from_json_schema(child if isinstance(child, dict) else {})
        ports.append(PortIR(name=name, type=ref, required=name in required, description=str((child or {}).get("description") or "")))
    return ports


def _scan_security_and_bindings(value: Any, issues: list[Diagnostic], path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}".strip(".")
            lowered = str(key).lower()
            if lowered in {"password", "token", "secret", "apikey", "api_key", "privatekey", "private_key"} and isinstance(child, str) and child and not child.startswith("secret:"):
                issues.append(diagnostic(
                    "SECRET_LITERAL_FORBIDDEN", "error", "秘密値をSpecへ直接保存できません",
                    path=child_path, source="security-validator", suggested_fix="secret:NAME参照を使用してください",
                ))
            if key in {"source", "binding"} and isinstance(child, str) and ":" in child:
                prefix = child.split(":", 1)[0]
                if prefix not in BINDING_PREFIXES:
                    issues.append(diagnostic("BINDING_SOURCE_UNKNOWN", "error", f"binding source '{prefix}' は未登録です", path=child_path, source="binding-validator"))
            _scan_security_and_bindings(child, issues, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_security_and_bindings(child, issues, f"{path}.{index}".strip("."))


def _at_path(issues: list[Diagnostic], path: str) -> list[Diagnostic]:
    for issue in issues:
        issue.path = path
    return issues


def _int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _float(value, 0)


def _find_cycles(edges: list[EdgeIR]) -> list[list[str]]:
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge.source_node, []).append(edge.target_node)
    visiting: list[str] = []
    visited: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node_id: str) -> None:
        if node_id in visiting:
            start = visiting.index(node_id)
            cycle = visiting[start:] + [node_id]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        if node_id in visited:
            return
        visiting.append(node_id)
        for target in outgoing.get(node_id, []):
            visit(target)
        visiting.pop()
        visited.add(node_id)

    for node_id in sorted(outgoing):
        visit(node_id)
    return cycles
