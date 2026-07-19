"""ワークフローノードの実行能力・副作用・型metadata。

実行器とは独立した宣言情報で、dry-run、API、UI、LLM catalog整合検査に使う。
実行可否をCSSだけで隠さず、backendを正とする。
"""
from __future__ import annotations

from typing import Any

from app.workflows.catalog import NODE_CATALOG
from app.workflows.validation import REQUIRED_KEYS

SIDE_EFFECTS: dict[str, str] = {
    # プロセス・管理対象の状態を変える
    "app.start": "process", "app.stop": "process", "app.restart": "process",
    "cmd.ssh": "process", "cmd.git": "process", "cmd.cpp_build": "process",
    "cmd.python": "process", "net.wol": "external", "flow.call": "process",
    # 永続データ/ファイルを変更し得る
    "file.write": "write", "file.op": "write", "http.download": "write",
    "rag.build": "write", "db.query": "write",
    # 外部通信・計算資源を使用する（GETでも相手側へ通信するためnoneにはしない）
    "http.request": "external", "notify.webhook": "external", "llm.chat": "external",
    "web.scrape": "external", "web.browser": "external", "web.search": "external",
    "academic.search": "external", "research.deep": "external", "code.agent": "process",
    "ai.utility": "external",
    # ローカル読み取り
    "app.status": "read", "file.read": "read", "file.exists": "read", "file.glob": "read",
    "media.ocr": "read", "rag.query": "read",
}

CAPABILITIES: dict[str, list[str]] = {
    "app.start": ["apps.control"], "app.stop": ["apps.control"],
    "app.restart": ["apps.control"], "app.status": ["apps.read"],
    "file.read": ["filesystem.read"], "file.exists": ["filesystem.read"],
    "file.glob": ["filesystem.read"],
    "file.write": ["filesystem.write"], "file.op": ["filesystem.write"],
    "http.download": ["network", "filesystem.write"],
    "http.request": ["network"], "notify.webhook": ["network", "notification"],
    "web.scrape": ["network"], "web.browser": ["network", "browser"],
    "web.search": ["network"], "academic.search": ["network"],
    "research.deep": ["network", "llm"], "llm.chat": ["network", "llm"],
    "ai.utility": ["network", "llm"],
    "media.ocr": ["filesystem.read", "process.exec"],
    "rag.build": ["knowledge.write", "llm"], "rag.query": ["knowledge.read", "llm"],
    "db.query": ["database"], "cmd.ssh": ["network", "process.exec"],
    "cmd.git": ["filesystem.write", "process.exec"],
    "cmd.cpp_build": ["filesystem.write", "process.exec"],
    "cmd.python": ["process.exec"], "net.wol": ["network"],
    "flow.call": ["workflow.call"],
    "code.agent": ["filesystem.read", "filesystem.write", "process.exec", "llm"],
}

# 代表出力。値はJSON Schema風の型名（UIの変数pickerとdry-run説明用）。
OUTPUT_SCHEMAS: dict[str, dict[str, str]] = {
    "trigger": {"message": "string"},
    "app.start": {"app": "string", "status": "string"},
    "app.stop": {"app": "string", "status": "string"},
    "app.restart": {"app": "string", "status": "string"},
    "app.status": {"app": "string", "status": "string", "pid": "integer", "uptime_seconds": "number"},
    "condition.if": {"result": "boolean", "left": "any", "right": "any"},
    "control.loop": {"index": "integer", "item": "any", "total": "integer", "done": "boolean", "results": "array"},
    "util.wait": {"waited_seconds": "number"}, "util.now": {"text": "string", "date": "string", "time": "string"},
    "var.set": {"value": "any"}, "string.op": {"result": "any"}, "text.markdown": {"html": "string"},
    "data.transform": {"value": "any", "valid": "boolean", "errors": "array", "csv": "string", "rows": "array", "count": "integer"},
    "file.read": {"content": "string", "path": "string"},
    "file.write": {"path": "string", "bytes": "integer"},
    "file.op": {"path": "string", "deleted": "string", "created": "string"}, "file.exists": {"exists": "boolean", "size": "integer"},
    "file.glob": {"matches": "array", "paths": "array", "count": "integer"},
    "llm.chat": {"content": "string", "thinking": "string", "usage": "object"},
    "media.ocr": {"text": "string"}, "rag.build": {"collection": "string", "chunks": "integer"},
    "rag.query": {"context": "string", "results": "array"},
    "academic.search": {"results": "array", "text": "string"},
    "web.search": {"results": "array", "urls": "array", "text": "string"},
    "research.deep": {"report": "string", "sources": "array", "research": "object", "sub_questions": "array", "count": "integer"},
    "http.request": {"status_code": "integer", "ok": "boolean", "body": "string"},
    "http.download": {"path": "string", "bytes": "integer"},
    "web.scrape": {"status_code": "integer", "url": "string"},
    "web.browser": {"url": "string", "title": "string", "content": "string"},
    "net.wol": {"sent": "boolean"}, "cmd.ssh": {"stdout": "string", "stderr": "string", "exit_code": "integer"},
    "cmd.git": {"stdout": "string", "stderr": "string", "exit_code": "integer"},
    "cmd.cpp_build": {"stdout": "string", "stderr": "string", "exit_code": "integer"},
    "cmd.python": {"stdout": "string", "stderr": "string", "exit_code": "integer"},
    "db.query": {"rows": "array", "row_count": "integer", "affected": "integer"},
    "signal.display": {"signal": "string", "value": "any"},
    "flow.call": {"execution_id": "integer", "result": "object"},
    "notify.webhook": {"status_code": "integer", "ok": "boolean"},
    "code.agent": {"output": "string", "events": "integer", "operation": "string", "project_path": "string"},
    "ai.utility": {"vectors": "array", "dim": "integer", "results": "array", "score": "number", "reason": "string"},
}

_INTEGER_KEYS = {
    "app_id", "count", "parallel", "max_results", "workflow_id", "agent_max_steps", "limit", "top_n",
    "max_rounds", "max_search_calls", "max_evidence_chars", "max_report_tokens",
}
_NUMBER_KEYS = {"seconds", "timeout"}
_BOOLEAN_KEYS = {"multiple", "full_page", "hyde", "multi_query", "recursive"}
_ARRAY_KEYS = {"inputs", "extractors", "sources"}


def _config_type(key: str) -> str:
    if key in _INTEGER_KEYS:
        return "integer"
    if key in _NUMBER_KEYS:
        return "number"
    if key in _BOOLEAN_KEYS:
        return "boolean"
    if key in _ARRAY_KEYS:
        return "array"
    return "string"


def node_catalog() -> list[dict[str, Any]]:
    """全実装ノードのmetadata。executor集合との整合はテストで強制する。"""
    from app.workflows.nodes import NODE_EXECUTORS

    descriptions = {item["type"]: item.get("desc", "") for item in NODE_CATALOG}
    keys = {item["type"]: item.get("keys", []) for item in NODE_CATALOG}
    types = sorted(set(NODE_EXECUTORS) | {"control.loop"})
    result: list[dict[str, Any]] = []
    for node_type in types:
        required = set(REQUIRED_KEYS.get(node_type, []))
        config_keys = list(dict.fromkeys([*keys.get(node_type, []), *required]))
        result.append({
            "type": node_type,
            "version": 1,
            "description": descriptions.get(node_type, ""),
            "side_effect": SIDE_EFFECTS.get(node_type, "none"),
            "capabilities": CAPABILITIES.get(node_type, []),
            "config_schema": {
                key: {"type": _config_type(key), "required": key in required}
                for key in config_keys
            },
            "output_schema": OUTPUT_SCHEMAS.get(node_type, {}),
            "supports": {
                "retry": node_type not in ("trigger", "control.loop"),
                "cancel": True,
                "progress": node_type in {"control.loop", "data.transform", "file.glob", "ai.utility"},
                "dry_run": True,
            },
        })
    return result


def metadata_by_type() -> dict[str, dict[str, Any]]:
    return {item["type"]: item for item in node_catalog()}
