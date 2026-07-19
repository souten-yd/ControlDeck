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
    "human.approval": ["human.interaction"],
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
    "human.approval": {"approved": "boolean", "message": "string", "approver": "string"},
    "control.merge": {"mode": "string", "items": "array", "values": "array", "count": "integer", "succeeded": "integer", "value": "any"},
    "util.wait": {"waited_seconds": "number"}, "util.now": {"text": "string", "date": "string", "time": "string"},
    "var.set": {"value": "any"}, "string.op": {"result": "any"}, "text.markdown": {"html": "string"},
    "data.transform": {"value": "any", "valid": "boolean", "errors": "array", "csv": "string", "rows": "array", "count": "integer"},
    "data.template": {"text": "string", "value": "any", "format": "string"},
    "data.filter": {"items": "array", "count": "integer", "original_count": "integer"},
    "data.aggregate": {"result": "any", "groups": "array", "count": "integer", "operation": "string"},
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
    "output.render": {"name": "string", "type": "string", "renderer": "string", "value": "any"},
    "flow.call": {"execution_id": "integer", "result": "object"},
    "notify.webhook": {"status_code": "integer", "ok": "boolean"},
    "code.agent": {"output": "string", "events": "integer", "operation": "string", "project_path": "string"},
    "ai.utility": {"vectors": "array", "dim": "integer", "results": "array", "score": "number", "reason": "string"},
}

_INTEGER_KEYS = {
    "app_id", "count", "parallel", "max_results", "workflow_id", "agent_max_steps", "limit", "top_n",
    "max_rounds", "max_search_calls", "max_evidence_chars", "max_report_tokens", "retry_count", "quorum",
}
_NUMBER_KEYS = {"seconds", "timeout", "startup_timeout", "retry_wait", "node_timeout", "approval_timeout_seconds"}
_BOOLEAN_KEYS = {"multiple", "full_page", "hyde", "multi_query", "recursive", "auto_load"}
_ARRAY_KEYS = {"inputs", "extractors", "sources"}

RECOMMENDED_CONFIG: dict[str, Any] = {
    "retry_count": 1, "retry_wait": 1, "node_timeout": 60, "on_error": "stop",
    "max_results": 8, "top_k": 4, "top_n": 5, "limit": 100,
    "parallel": 3, "max_rounds": 3, "max_search_calls": 16,
}

EXECUTOR_DEFAULTS: dict[str, Any] = {
    "retry_count": 0, "retry_wait": 0, "on_error": "stop",
}

CONFIG_REASONS: dict[str, str] = {
    "retry_count": "一時的な通信・runtime失敗を吸収します。副作用ノードでは重複実行に注意してください。",
    "retry_wait": "即時再試行による連続失敗と外部サービスへの集中を避けます。",
    "node_timeout": "停止した外部処理がワークフロー全体を占有し続けることを防ぎます。",
    "on_error": "既定は安全側の停止です。継続・error branchは失敗後の契約を確認して選びます。",
    "max_results": "精度と処理時間・後段token量のバランスがよい初期件数です。",
    "top_k": "RAG文脈を確保しつつ、無関係な断片とtoken消費を抑える推奨値です。",
    "parallel": "ローカル資源と外部rate limitを圧迫しにくい並列数です。",
    "auto_load": "管理中のローカルLLMを実行直前に起動・ロードし、準備完了まで待ちます。通常は有効のまま使用します。",
    "startup_timeout": "大型モデルのロード待ち上限です。短すぎると正常な初回ロードも失敗するため240秒を推奨します。",
    "keep_alive": "実行後にモデルを保持する時間です。連続実行の再ロードを避けたい場合だけ指定します。",
}

# 新規ノードへ安全に投入できる決定的な初期値。URL・path・secret・モデル名など、
# 環境依存値は推測しない。executorの暗黙値と異なる場合は必ず明示して保存する。
INITIAL_CONFIGS: dict[str, dict[str, Any]] = {
    "trigger": {"mode": "manual", "inputs": []},
    "condition.if": {"op": "eq", "right": "true"},
    "control.loop": {"mode": "count", "count": 1, "parallel": 3},
    "human.approval": {"message": "この処理を続行しますか？", "approval_timeout_seconds": 86400},
    "control.merge": {"mode": "wait_all"},
    "util.wait": {"seconds": 1},
    "util.now": {"format": "%Y-%m-%d %H:%M:%S"},
    "var.set": {"name": "result"},
    "string.op": {"op": "template"},
    "data.transform": {"operation": "json_parse", "delimiter": ","},
    "data.template": {"output_format": "text"},
    "data.filter": {"operator": "truthy", "sort_order": "asc", "limit": 100},
    "data.aggregate": {"operation": "count"},
    "file.write": {"append": ""},
    "file.glob": {"pattern": "*", "recursive": False, "kind": "all", "limit": 100},
    "llm.chat": {"response_format": "text", "auto_load": True, "startup_timeout": 240},
    "rag.query": {"search_mode": "hybrid", "top_k": 4, "hyde": False, "multi_query": False},
    "academic.search": {"source": "all", "max_results": 8},
    "web.search": {"engine": "searxng", "max_results": 8},
    "research.deep": {"depth": "standard", "sources": ["web", "academic", "github", "direct"]},
    "http.request": {"method": "GET", "timeout": 30},
    "db.query": {"engine": "sqlite"},
    "web.browser": {"action": "content"},
    "notify.webhook": {"format": "generic"},
    "output.render": {"name": "result", "renderer": "auto", "copyable": True},
    "ai.utility": {"operation": "embedding", "timeout": 60, "top_n": 5},
}

# 接続時に上流出力を提案する主要入力。空欄だけを補完し、ユーザー値は上書きしない。
PRIMARY_INPUTS: dict[str, str] = {
    "condition.if": "left", "control.loop": "items", "var.set": "value",
    "string.op": "text", "data.transform": "input", "data.template": "template",
    "data.filter": "input", "data.aggregate": "input", "text.markdown": "text",
    "file.write": "content", "llm.chat": "prompt", "rag.build": "text",
    "rag.query": "question", "academic.search": "query", "web.search": "query",
    "research.deep": "topic", "web.scrape": "url", "http.request": "url",
    "http.download": "url", "notify.webhook": "message", "signal.display": "value",
    "output.render": "value", "flow.call": "message", "ai.utility": "input",
}

PRIMARY_OUTPUTS: dict[str, str] = {
    "trigger": "message", "app.status": "status", "condition.if": "result",
    "control.loop": "results", "control.merge": "value", "var.set": "value",
    "string.op": "result", "data.transform": "value", "data.template": "text",
    "data.filter": "items", "data.aggregate": "result", "file.read": "content",
    "file.glob": "paths", "llm.chat": "content", "rag.query": "context",
    "academic.search": "results", "web.search": "results", "research.deep": "report",
    "http.request": "body", "web.scrape": "url", "web.browser": "content",
    "db.query": "rows", "output.render": "value", "flow.call": "result",
    "code.agent": "output", "ai.utility": "results",
}

EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "condition.if": [{"title": "HTTP成功時だけ続行", "config": {"left": "{{http.ok}}", "op": "eq", "right": "true"}}],
    "llm.chat": [{"title": "上流テキストを要約", "config": {"prompt": "次を簡潔に要約してください。\n\n{{input.content}}", "response_format": "text"}}],
    "data.filter": [{"title": "score 0.8以上の上位10件", "config": {"input": "{{search.results}}", "field": "score", "operator": "gte", "value": 0.8, "sort_by": "score", "sort_order": "desc", "limit": 10}}],
    "http.request": [{"title": "JSON APIを読み取る", "config": {"method": "GET", "url": "https://example.com/api/status", "timeout": 30}}],
    "output.render": [{"title": "Markdownを最終出力", "config": {"name": "answer", "title": "回答", "value": "{{llm.content}}", "renderer": "markdown", "copyable": True}}],
    "research.deep": [{"title": "標準の技術調査", "config": {"topic": "{{trigger.topic}}", "depth": "standard", "sources": ["web", "academic", "github", "direct"]}}],
}

QUICK_STARTS: dict[str, str] = {
    "output.render": "valueへ上流変数を挿入し、用途に合うrendererを選びます。nameはフロー内で一意にします。",
    "llm.chat": "モデルを検出し、promptへ上流の本文を挿入します。ローカルモデルは既定で自動起動・ロードし、準備完了まで待つため、通常は事前起動不要です。",
    "http.request": "URLを入力します。読み取りはGETのまま試し、送信時だけmethodとbodyを変更します。",
    "research.deep": "topicへ調査テーマを挿入します。まず標準深度で試し、不足時だけ詳細・徹底へ上げます。",
}


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


def _node_help(description: str, required: set[str], outputs: dict[str, str], side_effect: str) -> str:
    required_text = "、".join(sorted(required)) if required else "なし（初期値のまま試せます）"
    output_text = "、".join(f"{key}: {value}" for key, value in outputs.items()) or "実行状態のみ"
    effect_text = {
        "none": "外部状態を変更しません。", "read": "ローカル資源を読み取ります。",
        "write": "ファイルまたは永続データを変更する可能性があります。",
        "external": "外部サービスへ通信します。", "process": "プロセスまたは管理対象を操作します。",
    }.get(side_effect, "実行前に副作用を確認してください。")
    return f"{description}\n\n必須設定: {required_text}\n主な出力: {output_text}\n安全性: {effect_text} まず安全プレビューで入力と副作用を確認してください。"


def _field_reason(key: str, required: set[str], initial_config: dict[str, Any]) -> str:
    if key in CONFIG_REASONS:
        return CONFIG_REASONS[key]
    if key in initial_config:
        return "ControlDeckの一般的な用途で安全に試しやすい初期値です。必要な場合だけ変更してください。"
    if key in required:
        return "このノードの実行に必要な設定です。上流変数または実行環境に合う値を指定してください。"
    return "任意設定です。既定動作を変更したい場合だけ指定してください。"


def node_catalog() -> list[dict[str, Any]]:
    """全実装ノードのmetadata。executor集合との整合はテストで強制する。"""
    from app.workflows.nodes import NODE_EXECUTORS

    descriptions = {item["type"]: item.get("desc", "") for item in NODE_CATALOG}
    keys = {item["type"]: item.get("keys", []) for item in NODE_CATALOG}
    types = sorted(set(NODE_EXECUTORS) | {"control.loop"})
    result: list[dict[str, Any]] = []
    for node_type in types:
        required = set(REQUIRED_KEYS.get(node_type, []))
        common_keys = [] if node_type == "trigger" else ["retry_count", "retry_wait", "node_timeout", "on_error"]
        config_keys = list(dict.fromkeys([*keys.get(node_type, []), *required, *common_keys]))
        outputs = OUTPUT_SCHEMAS.get(node_type, {})
        initial_config = INITIAL_CONFIGS.get(node_type, {})
        result.append({
            "type": node_type,
            "version": 1,
            "metadata_version": 3,
            "description": descriptions.get(node_type, ""),
            "side_effect": SIDE_EFFECTS.get(node_type, "none"),
            "capabilities": CAPABILITIES.get(node_type, []),
            "config_schema": {
                key: {
                    "type": _config_type(key), "required": key in required,
                    **({"default": EXECUTOR_DEFAULTS[key]} if key in EXECUTOR_DEFAULTS else {}),
                    **({"recommended": RECOMMENDED_CONFIG[key]} if key in RECOMMENDED_CONFIG else
                       ({"recommended": INITIAL_CONFIGS[node_type][key]} if key in INITIAL_CONFIGS.get(node_type, {}) else {})),
                    "reason": _field_reason(key, required, initial_config),
                }
                for key in config_keys
            },
            "initial_config": initial_config,
            "input_schema": {},
            "output_schema": outputs,
            "ui_hints": {
                "help": _node_help(descriptions.get(node_type, ""), required, outputs, SIDE_EFFECTS.get(node_type, "none")),
                "quick_start": QUICK_STARTS.get(node_type, "推奨設定を適用し、必須入力へ上流変数を挿入してください。"),
                "variable_picker": True,
                "show_recommended_defaults": True,
                "primary_input": PRIMARY_INPUTS.get(node_type),
                "primary_output": PRIMARY_OUTPUTS.get(node_type),
                "examples": EXAMPLES.get(node_type, ([{"title": "推奨初期構成", "config": initial_config}] if initial_config else [])),
            },
            "security": {
                "allowed_in_generated_app": node_type not in {"cmd.python", "cmd.ssh", "code.agent"},
                "requires_secret_reference": False,
            },
            "supports": {
                "retry": node_type not in ("trigger", "control.loop", "human.approval"),
                "cancel": True,
                "progress": node_type in {"control.loop", "data.transform", "data.filter", "data.aggregate", "file.glob", "ai.utility", "llm.chat"},
                "dry_run": True,
            },
        })
    return result


def metadata_by_type() -> dict[str, dict[str, Any]]:
    return {item["type"]: item for item in node_catalog()}
