"""副作用を一切起こさないワークフロー静的シミュレーション。"""
from __future__ import annotations

import re
from collections import Counter, deque
from typing import Any

from app.workflows.engine import DefinitionError, parse_definition, validate_definition
from app.workflows.node_metadata import metadata_by_type
from app.workflows.validation import REQUIRED_KEYS, quality_score, semantic_check

_SENSITIVE_KEY = re.compile(r"(password|passwd|token|secret|authorization|api[_-]?key)", re.I)
_SECRET_TEMPLATE = re.compile(r"\{\{\s*secrets\.[^}]+\}\}", re.I)


def _bounded_int(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def _redact(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "***"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_TEMPLATE.sub("{{secrets.***}}", value)
    return value


def simulate_node(node_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """単一nodeの予定操作を返す。executor、secret、外部I/Oには触れない。"""
    metadata = metadata_by_type().get(node_type)
    if metadata is None:
        raise DefinitionError(f"未知のノード種類: {node_type}")
    errors = []
    for key in REQUIRED_KEYS.get(node_type, []):
        if not str(config.get(key, "") or "").strip():
            errors.append(f"必須設定 '{key}' が空です")
    return {
        "ok": not errors,
        "dry_run": True,
        "type": node_type,
        "status": "SIMULATED" if not errors else "BLOCKED",
        "description": metadata["description"],
        "side_effect": metadata["side_effect"],
        "capabilities": metadata["capabilities"],
        "config": _redact(config),
        "would_output": metadata["output_schema"],
        "errors": errors,
        "notice": "executorは呼び出していません。外部操作・書き込みは発生していません。",
    }


def simulate_definition(definition: dict[str, Any], input_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """definitionを静的に辿り、実行予定だけを返す。DB行も作らない。"""
    import json

    definition_json = json.dumps(definition, ensure_ascii=False)
    try:
        validate_definition(definition_json)
        structural_errors: list[str] = []
    except DefinitionError as exc:
        structural_errors = [str(exc)]
    try:
        nodes, edges = parse_definition(definition_json)
    except DefinitionError as exc:
        return {
            "valid": False, "dry_run": True, "errors": [str(exc)], "warnings": [],
            "summary": {"nodes": 0, "reachable": 0, "side_effects": {}}, "plan": [],
        }

    semantic_errors, warnings = semantic_check(nodes, edges)
    errors = structural_errors + semantic_errors
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    trigger = next((n for n in nodes if n.get("type") == "trigger"), None)
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for edge in edges:
        outgoing.setdefault(str(edge.get("source")), []).append(edge)
        incoming.setdefault(str(edge.get("target")), []).append(edge)

    # 最短waveをBFSで求める。cycle/loop back edgeはvisited depthで有限化する。
    depths: dict[str, int] = {}
    if trigger and trigger.get("id"):
        queue: deque[tuple[str, int]] = deque([(str(trigger["id"]), 0)])
        while queue:
            node_id, depth = queue.popleft()
            if node_id in depths and depths[node_id] <= depth:
                continue
            depths[node_id] = depth
            for edge in outgoing.get(node_id, []):
                target = str(edge.get("target"))
                if target in by_id:
                    queue.append((target, depth + 1))

    metadata = metadata_by_type()
    plans: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        node_id = str(node.get("id") or "")
        node_type = str(node.get("type") or "")
        meta = metadata.get(node_type, {
            "description": "", "side_effect": "none", "capabilities": [], "output_schema": {},
        })
        deps = []
        for edge in incoming.get(node_id, []):
            deps.append({
                "source": edge.get("source"),
                "branch": edge.get("branch") or edge.get("sourceHandle"),
            })
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        plans.append({
            "id": node_id,
            "name": node.get("name") or node_id,
            "type": node_type,
            "wave": depths.get(node_id),
            "status": "SIMULATED" if node_id in depths else "UNREACHABLE",
            "depends_on": deps,
            "description": meta.get("description", ""),
            "side_effect": meta.get("side_effect", "none"),
            "capabilities": meta.get("capabilities", []),
            "config": _redact(config),
            "would_output": meta.get("output_schema", {}),
            "control": {
                "retry_count": _bounded_int(config.get("retry_count", 0) or 0, 0, 5),
                "require_approval": bool(config.get("require_approval")),
                "on_error": str(config.get("on_error") or "stop"),
                "join": str(config.get("join") or "first"),
            },
            "_index": index,
        })
    plans.sort(key=lambda item: (item["wave"] is None, item["wave"] or 0, item["_index"]))
    for item in plans:
        item.pop("_index", None)

    reachable = [item for item in plans if item["status"] == "SIMULATED"]
    side_effects = Counter(item["side_effect"] for item in reachable if item["side_effect"] != "none")
    score = quality_score(nodes, edges, run_ok=None)
    return {
        "valid": not errors,
        "dry_run": True,
        "errors": errors,
        "warnings": warnings,
        "input": _redact(input_data or {}),
        "summary": {
            "nodes": len(nodes),
            "reachable": len(reachable),
            "side_effects": dict(sorted(side_effects.items())),
            "quality": score,
        },
        "plan": plans,
        "notice": "executor・secret復号・外部通信・プロセス・DB更新・ファイル書込は実行していません。",
    }
