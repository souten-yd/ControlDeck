"""Deterministic Workflow Project Intelligence and versioned operation patches."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApplicationProject, Workflow, WorkflowExecution, WorkflowTestCase
from app.workflows import engine
from app.workflows.node_metadata import metadata_by_type
from app.workflows.redaction import is_sensitive_key, redact
from app.workflows.validation import quality_score, semantic_check

PATCH_VERSION = 1
MAX_OPERATIONS = 100
MAX_PATCH_BYTES = 256 * 1024


class WorkflowPatchError(ValueError):
    pass


def _definition_parts(definition: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise WorkflowPatchError("Workflow definitionにはnodesとedgesの配列が必要です")
    return nodes, edges


def _contains_unsafe_secret(value: Any, key: str = "") -> bool:
    if is_sensitive_key(key):
        return not (isinstance(value, str) and "{{secrets." in value)
    if isinstance(value, dict):
        return any(_contains_unsafe_secret(child, str(child_key)) for child_key, child in value.items())
    if isinstance(value, list):
        return any(_contains_unsafe_secret(child) for child in value)
    return False


def validate_operations(operations: Any) -> list[dict[str, Any]]:
    if not isinstance(operations, list):
        raise WorkflowPatchError("operationsは配列にしてください")
    if len(operations) > MAX_OPERATIONS:
        raise WorkflowPatchError(f"patch operationsは{MAX_OPERATIONS}件以内にしてください")
    try:
        serialized = json.dumps(operations, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowPatchError("patch operationsは有限値のJSONにしてください") from exc
    if len(serialized.encode("utf-8")) > MAX_PATCH_BYTES:
        raise WorkflowPatchError("patch operationsは256KiB以内にしてください")
    allowed = {"set_config", "update_node", "add_node", "remove_node", "add_edge", "remove_edge"}
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise WorkflowPatchError(f"operation {index + 1}がobjectではありません")
        op = str(raw.get("op") or "")
        if op not in allowed:
            raise WorkflowPatchError(f"operation {index + 1}のop '{op}' は未対応です")
        if op == "set_config" and is_sensitive_key(str(raw.get("key") or "")):
            value = raw.get("value")
            if not (isinstance(value, str) and "{{secrets." in value):
                raise WorkflowPatchError("AI patchへ秘密値を直接設定できません。Secret参照を使用してください")
        if _contains_unsafe_secret(raw):
            raise WorkflowPatchError("AI patchへ秘密値を直接設定できません。Secret参照を使用してください")
        normalized.append(copy.deepcopy(raw))
    return normalized


def apply_operations(definition: dict[str, Any], operations: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply stable node-ID operations, returning definition and RFC6902 export."""
    ops = validate_operations(operations)
    result = copy.deepcopy(definition)
    nodes, edges = _definition_parts(result)
    json_patch: list[dict[str, Any]] = []

    def node_index(node_id: str) -> int:
        found = next((i for i, node in enumerate(nodes) if str(node.get("id")) == node_id), None)
        if found is None:
            raise WorkflowPatchError(f"node '{node_id}' が見つかりません")
        return found

    for item in ops:
        op = item["op"]
        if op == "set_config":
            node_id = str(item.get("node_id") or "")
            key = str(item.get("key") or "")
            if not key or len(key) > 64:
                raise WorkflowPatchError("set_configには1〜64文字のkeyが必要です")
            index = node_index(node_id)
            config = nodes[index].setdefault("config", {})
            if not isinstance(config, dict):
                raise WorkflowPatchError(f"node '{node_id}' のconfigがobjectではありません")
            config[key] = copy.deepcopy(item.get("value"))
            json_patch.append({"op": "add", "path": f"/nodes/{index}/config/{_pointer(key)}", "value": copy.deepcopy(item.get("value"))})
        elif op == "update_node":
            node_id = str(item.get("node_id") or "")
            changes = item.get("changes")
            if not isinstance(changes, dict) or not changes:
                raise WorkflowPatchError("update_nodeにはchanges objectが必要です")
            unsupported = set(changes) - {"name", "disabled", "position"}
            if unsupported:
                raise WorkflowPatchError(f"update_nodeで変更できないfieldです: {sorted(unsupported)[0]}")
            index = node_index(node_id)
            for key, value in changes.items():
                nodes[index][key] = copy.deepcopy(value)
                json_patch.append({"op": "add", "path": f"/nodes/{index}/{_pointer(key)}", "value": copy.deepcopy(value)})
        elif op == "add_node":
            node = item.get("node")
            if not isinstance(node, dict) or not str(node.get("id") or "") or not str(node.get("type") or ""):
                raise WorkflowPatchError("add_nodeにはidとtypeを持つnodeが必要です")
            if any(str(existing.get("id")) == str(node["id"]) for existing in nodes):
                raise WorkflowPatchError(f"node ID '{node['id']}' は既に存在します")
            nodes.append(copy.deepcopy(node))
            json_patch.append({"op": "add", "path": "/nodes/-", "value": copy.deepcopy(node)})
        elif op == "remove_node":
            node_id = str(item.get("node_id") or "")
            index = node_index(node_id)
            if nodes[index].get("type") == "trigger":
                raise WorkflowPatchError("triggerはpatchで削除できません")
            nodes.pop(index)
            json_patch.append({"op": "remove", "path": f"/nodes/{index}"})
            for edge_index in range(len(edges) - 1, -1, -1):
                if edges[edge_index].get("source") == node_id or edges[edge_index].get("target") == node_id:
                    edges.pop(edge_index)
                    json_patch.append({"op": "remove", "path": f"/edges/{edge_index}"})
        elif op == "add_edge":
            edge = item.get("edge")
            if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
                raise WorkflowPatchError("add_edgeにはsourceとtargetが必要です")
            node_ids = {str(node.get("id")) for node in nodes}
            if str(edge["source"]) not in node_ids or str(edge["target"]) not in node_ids:
                raise WorkflowPatchError("add_edgeの接続先nodeが存在しません")
            signature = _edge_signature(edge)
            if any(_edge_signature(existing) == signature for existing in edges):
                raise WorkflowPatchError("同じedgeが既に存在します")
            edges.append(copy.deepcopy(edge))
            json_patch.append({"op": "add", "path": "/edges/-", "value": copy.deepcopy(edge)})
        elif op == "remove_edge":
            signature = _edge_signature(item)
            index = next((i for i, edge in enumerate(edges) if _edge_signature(edge) == signature), None)
            if index is None:
                raise WorkflowPatchError("削除対象edgeが見つかりません")
            edges.pop(index)
            json_patch.append({"op": "remove", "path": f"/edges/{index}"})

    try:
        engine.validate_definition(json.dumps(result, ensure_ascii=False))
    except engine.DefinitionError as exc:
        raise WorkflowPatchError(f"patch後の構造が不正です: {exc}") from exc
    return result, json_patch


def preview_patch(definition: dict[str, Any], operations: Any) -> dict[str, Any]:
    patched, json_patch = apply_operations(definition, operations)
    before_nodes, before_edges = _definition_parts(definition)
    after_nodes, after_edges = _definition_parts(patched)
    errors, warnings = semantic_check(after_nodes, after_edges)
    return {
        "patch_version": PATCH_VERSION,
        "valid": not errors,
        "operations": validate_operations(operations),
        "json_patch": json_patch,
        "summary": {
            "operation_count": len(json_patch),
            "nodes_before": len(before_nodes), "nodes_after": len(after_nodes),
            "edges_before": len(before_edges), "edges_after": len(after_edges),
        },
        "quality_before": quality_score(before_nodes, before_edges),
        "quality_after": quality_score(after_nodes, after_edges),
        "errors": errors, "warnings": warnings,
        "patched_definition": patched,
    }


def _pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _edge_signature(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(edge.get("source") or ""), str(edge.get("target") or ""),
        str(edge.get("branch") or edge.get("source_handle") or edge.get("sourceHandle") or ""),
    )


def _duration_ms(entry: dict[str, Any]) -> float | None:
    try:
        started = datetime.fromisoformat(str(entry.get("started_at")))
        finished = datetime.fromisoformat(str(entry.get("finished_at")))
        return max(0.0, (finished - started).total_seconds() * 1000)
    except (TypeError, ValueError):
        return None


def _topological_order(nodes: list[dict], edges: list[dict]) -> list[str]:
    ids = [str(node.get("id")) for node in nodes]
    incoming = {node_id: 0 for node_id in ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source in outgoing and target in incoming:
            outgoing[source].append(target)
            incoming[target] += 1
    queue = [node_id for node_id in ids if incoming[node_id] == 0]
    order: list[str] = []
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return order


def _sample_input(field: dict[str, Any]) -> Any:
    if "default" in field:
        return copy.deepcopy(field["default"])
    if "sample" in field:
        return copy.deepcopy(field["sample"])
    kind = str(field.get("type") or "text")
    if kind in {"number"}:
        return 1
    if kind == "boolean":
        return True
    if kind in {"select", "multi_select"}:
        options = field.get("options")
        values = options.splitlines() if isinstance(options, str) else list(options or [])
        return (values[:1] if kind == "multi_select" else (values[0] if values else ""))
    if kind == "json":
        return {}
    if kind in {"json_array", "file_list"}:
        return []
    if kind == "key_value":
        return {}
    return "sample"


def suggested_tests(definition: dict[str, Any]) -> list[dict[str, Any]]:
    nodes, _ = _definition_parts(definition)
    trigger = next((node for node in nodes if node.get("type") == "trigger"), {})
    fields = (trigger.get("config") or {}).get("inputs", [])
    inputs = {
        str(field.get("key")): _sample_input(field)
        for field in fields if isinstance(field, dict) and str(field.get("key") or "")
    }
    assertions = []
    for node in nodes:
        if node.get("type") not in {"flow.return", "output.render"}:
            continue
        name = str((node.get("config") or {}).get("name") or "")
        if name:
            assertions.append({"path": f"outputs.{name}.value", "operator": "exists"})
    return [{
        "name": "AI baseline", "inputs": inputs, "mocks": {},
        "expected_outputs": {}, "assertions": assertions,
        "reason": "型付きsample/default入力でWorkflowが成功し、宣言済み出力が存在することを確認します",
    }]


def project_intelligence(db: Session, workflow: Workflow) -> dict[str, Any]:
    definition = json.loads(workflow.definition_json or "{}")
    nodes, edges = _definition_parts(definition)
    metadata = metadata_by_type()
    executions = db.execute(select(WorkflowExecution).where(
        WorkflowExecution.workflow_id == workflow.id,
    ).order_by(WorkflowExecution.started_at.desc()).limit(20)).scalars().all()
    semantic_errors, semantic_warnings = semantic_check(nodes, edges)
    issues: list[dict[str, Any]] = [
        {"code": "SEMANTIC_ERROR", "severity": "blocking", "message": message, "node_id": None, "path": None, "details": {}, "autofix": []}
        for message in semantic_errors
    ] + [
        {"code": "SEMANTIC_WARNING", "severity": "warning", "message": message, "node_id": None, "path": None, "details": {}, "autofix": []}
        for message in semantic_warnings
    ]
    stats: dict[str, dict[str, Any]] = {
        str(node.get("id")): {"runs": 0, "failed": 0, "timed_out": 0, "durations_ms": []}
        for node in nodes
    }
    for execution in executions:
        try:
            context = json.loads(execution.context_json or "{}")
        except json.JSONDecodeError:
            continue
        for node_id, entry in context.items():
            if node_id not in stats or not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "")
            if status in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELED"}:
                stats[node_id]["runs"] += 1
            stats[node_id]["failed"] += int(status == "FAILED")
            stats[node_id]["timed_out"] += int(status == "TIMED_OUT")
            duration = _duration_ms(entry)
            if duration is not None:
                stats[node_id]["durations_ms"].append(duration)
    node_health: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id"))
        stat = stats[node_id]
        durations = stat.pop("durations_ms")
        average = round(sum(durations) / len(durations), 1) if durations else None
        failure_rate = round((stat["failed"] + stat["timed_out"]) / stat["runs"], 3) if stat["runs"] else 0
        node_health.append({"node_id": node_id, "type": node.get("type"), **stat, "failure_rate": failure_rate, "average_duration_ms": average})
        if stat["runs"] >= 3 and failure_rate >= 0.5:
            issues.append({
                "code": "NODE_FAILURE_RATE", "severity": "warning", "node_id": node_id,
                "path": f"nodes.{node_id}", "message": f"直近{stat['runs']}回の失敗率が{failure_rate * 100:.0f}%です",
                "details": {"failure_rate": failure_rate}, "autofix": [],
            })
    side_effects = [{
        "node_id": str(node.get("id")), "type": str(node.get("type")),
        "effect": metadata.get(str(node.get("type")), {}).get("side_effect", "unknown"),
    } for node in nodes if metadata.get(str(node.get("type")), {}).get("side_effect", "none") != "none"]
    unknowns = []
    for node in nodes:
        if node.get("type") == "llm.chat":
            config = node.get("config") or {}
            if not config.get("model"):
                unknowns.append({"node_id": node.get("id"), "message": "LLM modelが未設定です"})
            if not config.get("base_url"):
                unknowns.append({"node_id": node.get("id"), "message": "LLM endpointは実行時の既定値に依存します"})
    latest = executions[0] if executions else None
    linked_projects = db.execute(select(ApplicationProject).where(
        ApplicationProject.workflow_id == workflow.id,
    )).scalars().all()
    existing_tests = db.execute(select(WorkflowTestCase).where(
        WorkflowTestCase.workflow_id == workflow.id,
    )).scalars().all()
    return {
        "workflow_id": workflow.id, "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "nodes": len(nodes), "edges": len(edges), "executions_analyzed": len(executions),
            "successes": sum(item.status == "SUCCEEDED" for item in executions),
            "failures": sum(item.status in {"FAILED", "TIMED_OUT"} for item in executions),
            "linked_projects": len(linked_projects), "test_cases": len(existing_tests),
        },
        "quality": quality_score(nodes, edges, latest.status == "SUCCEEDED" if latest else None),
        "issues": issues, "execution_order": _topological_order(nodes, edges),
        "side_effects": side_effects, "unknowns": unknowns,
        "node_health": node_health, "suggested_tests": suggested_tests(definition),
        "latest_execution": ({"id": latest.id, "status": latest.status, "error": redact(latest.error) } if latest else None),
    }


def deterministic_diagnosis(definition: dict[str, Any], execution: WorkflowExecution | None) -> dict[str, Any]:
    nodes, _ = _definition_parts(definition)
    node_by_id = {str(node.get("id")): node for node in nodes}
    failed_id: str | None = None
    failed_entry: dict[str, Any] = {}
    if execution is not None:
        try:
            context = json.loads(execution.context_json or "{}")
        except json.JSONDecodeError:
            context = {}
        for node_id, entry in context.items():
            if isinstance(entry, dict) and entry.get("status") in {"FAILED", "TIMED_OUT"}:
                failed_id, failed_entry = node_id, entry
                break
    if failed_id is None:
        errors, warnings = semantic_check(nodes, definition.get("edges", []))
        message = (errors or warnings or ["直近の失敗はありません。静的品質と回帰テストを確認してください"])[0]
        return {"cause": message, "confidence": 0.65 if errors else 0.4, "failed_node_id": None, "options": [{"title": "変更せず確認", "impact": "definitionを変更しません", "operations": []}]}

    error_context = failed_entry.get("error_context") if isinstance(failed_entry.get("error_context"), dict) else {}
    code = str(error_context.get("code") or failed_entry.get("error_code") or "NODE_FAILED")
    message = str(error_context.get("message") or failed_entry.get("error") or execution.error or "ノードが失敗しました")
    options = [{"title": "変更せず再現条件を確認", "impact": "definitionを変更しません", "operations": []}]
    node = node_by_id.get(failed_id, {})
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    retryable = bool(error_context.get("retryable", True))
    if failed_entry.get("status") == "TIMED_OUT" or code == "NODE_TIMEOUT":
        current = float(config.get("node_timeout") or 120)
        options.insert(0, {
            "title": "timeoutを段階的に延長", "impact": "待機上限が延びます。根本原因が停止なら実行時間も増えます",
            "operations": [{"op": "set_config", "node_id": failed_id, "key": "node_timeout", "value": min(3600, max(current + 30, current * 2))}],
        })
    elif retryable and int(config.get("retry_count") or 0) < 2:
        options.insert(0, {
            "title": "一時失敗を2回まで再試行", "impact": "副作用nodeでは重複実行の可能性があるためdiff確認が必要です",
            "operations": [
                {"op": "set_config", "node_id": failed_id, "key": "retry_count", "value": 2},
                {"op": "set_config", "node_id": failed_id, "key": "retry_wait", "value": 2},
            ],
        })
    return {
        "cause": f"{failed_id} が {code} で失敗しました: {message}",
        "confidence": 0.9 if error_context else 0.75, "failed_node_id": failed_id,
        "error_code": code, "options": options,
    }
