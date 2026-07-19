from __future__ import annotations

import copy
import json
import re
from typing import Any

from app.application_builder.capabilities import FRAMEWORKS, node_support
from app.application_builder.diagnostics import Diagnostic, diagnostic
from app.application_builder.ir import (
    ApplicationIR, EdgeIR, ExecutionPolicyIR, NodeCodegenIR, NodeIR, PortIR,
    SecretReferenceIR, WorkflowIR,
)
from app.application_builder.type_system import TypeRef, from_json_schema, is_assignable, parse_type
from app.workflows.contracts import build_input_schema, build_output_schema
from app.workflows.node_metadata import metadata_by_type
from app.workflows.redaction import redact
from app.schemas.application_builder import ApplicationSpecV1
from pydantic import ValidationError

SECRET_REF = re.compile(r"\{\{\s*secrets\.([A-Za-z0-9_.-]+)\s*\}\}")
BINDING_PREFIXES = {
    "workflow-input", "workflow-output", "node-output", "api", "entity", "query",
    "state", "route", "form", "system", "constant",
}


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
        nodes.append(NodeIR(
            id=node_id, node_type=node_type, version=int(raw.get("version") or 1),
            display_name=str(raw.get("name") or node_id), config=redact(copy.deepcopy(config)),
            inputs=[], outputs=output_ports,
            execution=ExecutionPolicyIR(
                retry_count=_int(config.get("retry_count"), 0), retry_wait_seconds=_float(config.get("retry_wait"), 0),
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

    definition_text = json.dumps(definition, ensure_ascii=False)
    secrets = [SecretReferenceIR(name=item) for item in sorted(set(SECRET_REF.findall(definition_text)))]
    capabilities = sorted({cap for raw in nodes_raw for cap in (metadata.get(str(raw.get("type")), {}).get("capabilities") or [])})
    side_effects = sorted({effect for raw in nodes_raw if (effect := metadata.get(str(raw.get("type")), {}).get("side_effect", "none")) != "none"})
    return WorkflowIR(
        workflow_id=workflow_id, workflow_version_id=workflow_version_id, name=name,
        inputs=inputs, outputs=outputs, nodes=nodes, edges=edges, required_secrets=secrets,
        capabilities=capabilities, side_effects=side_effects, diagnostics=diagnostics,
    )


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
    for section in ("pages", "entities", "apiEndpoints", "backgroundJobs", "workflows", "targets"):
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
    known_frameworks = {item["id"] for item in FRAMEWORKS}
    for index, target in enumerate(spec.get("targets") or []):
        framework = str(target.get("framework") or "") if isinstance(target, dict) else ""
        if framework not in known_frameworks:
            issues.append(diagnostic("TARGET_UNKNOWN", "error", f"framework '{framework}' は未登録です", path=f"targets.{index}.framework"))
    _scan_security_and_bindings(spec, issues)
    if not spec.get("pages"):
        issues.append(diagnostic("GUI_EMPTY", "suggestion", "ページはまだありません", path="pages", source="gui-validator"))
    return issues


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
        "apiEndpoints": [], "backgroundJobs": [], "workflows": workflows, "permissions": [],
        "targets": [{"id": "web", "platforms": ["web"], "framework": "aspnet-blazor"}],
    }


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
