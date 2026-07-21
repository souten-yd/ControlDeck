"""Structured documentation derived from canonical workflow node metadata.

The documentation contract is intentionally uniform so the SampleBook, inspector,
API clients and AI catalog cannot silently lose safety or operational sections when
a node is added. Node-specific descriptions/examples remain in node_metadata; this
module turns them into a complete reference contract.
"""
from __future__ import annotations

from typing import Any


TITLES = {
    "none": "副作用なし", "read": "読み取り", "write": "永続データ変更",
    "external": "外部通信", "process": "プロセス操作",
}

ERRORS_BY_EFFECT = {
    "none": ["INVALID_CONFIG", "TEMPLATE_RESOLUTION_FAILED", "NODE_TIMEOUT"],
    "read": ["RESOURCE_NOT_FOUND", "ACCESS_DENIED", "NODE_TIMEOUT"],
    "write": ["ACCESS_DENIED", "CONFLICT", "CAPACITY_EXCEEDED", "NODE_TIMEOUT"],
    "external": ["CONNECTION_FAILED", "REMOTE_ERROR", "RATE_LIMITED", "NODE_TIMEOUT"],
    "process": ["PROCESS_FAILED", "ACCESS_DENIED", "NODE_TIMEOUT"],
}


def _when_to_use(node_type: str, effect: str, capabilities: list[str]) -> list[str]:
    if node_type == "trigger":
        return ["Workflowの開始条件と型付き入力を定義するとき", "手動・schedule・webhook・event・system起動を1つの契約へ統一するとき"]
    if node_type.startswith("control.") or node_type.startswith("condition."):
        return ["処理順、分岐、合流、待機または失敗境界を明示するとき", "実行履歴から制御判断を再現可能にしたいとき"]
    if node_type.startswith("data.") or node_type in {"var.set", "string.op", "text.markdown"}:
        return ["上流の型付き値を後段が必要とする形へ決定的に変換するとき", "同じ入力から同じ結果を得る必要があるとき"]
    if effect == "external":
        return ["Workflowから明示した外部サービスへ接続するとき", "timeout・retry・error routeを含めて外部依存を観測したいとき"]
    if effect in {"write", "process"}:
        return ["監査可能なWorkflow実行として管理対象を変更するとき", "入力・権限・失敗経路を事前確認して操作するとき"]
    return ["上流データまたはローカル状態を読み取り、型付き出力を後段へ渡すとき", f"{', '.join(capabilities) or '標準Workflow機能'}をフローへ組み込むとき"]


def _when_not_to_use(node_type: str, effect: str) -> list[str]:
    result = ["設定値・入力型・出力契約を確認できないまま本番フローへ追加するとき"]
    if node_type.startswith("flow.") and node_type in {"flow.return", "flow.error"}:
        result.append("後続処理を継続する必要がある中間地点")
    elif effect in {"write", "process"}:
        result.append("安全プレビュー、必要権限、対象resource、重複実行の影響を確認できないとき")
    elif effect == "external":
        result.append("接続先、credential、rate limit、timeoutの運用責任が決まっていないとき")
    else:
        result.append("専用の型付きnodeで表現できる処理を自由codeへ置き換えるだけのとき")
    return result


def _recipes(node_type: str, primary_input: str | None, primary_output: str | None) -> list[dict[str, str]]:
    input_hint = primary_input or "主要入力"
    output_hint = primary_output or "型付き出力"
    return [
        {
            "title": "最小構成",
            "steps": f"Triggerから接続し、{input_hint}へ上流変数を設定して{output_hint}をPreviewで確認します。",
        },
        {
            "title": "運用構成",
            "steps": f"{node_type}の前後へ型検証または条件分岐を置き、timeout／error routeと最終Returnを接続します。",
        },
    ]


def build_documentation(metadata: dict[str, Any]) -> dict[str, Any]:
    node_type = str(metadata["type"])
    effect = str(metadata.get("side_effect") or "none")
    config_schema = metadata.get("config_schema") or {}
    outputs = metadata.get("output_schema") or {}
    capabilities = list(metadata.get("capabilities") or [])
    supports = metadata.get("supports") or {}
    sensitive_keys = [key for key in config_schema if any(part in key.lower() for part in ("secret", "password", "token", "api_key", "authorization", "cookie"))]
    variable_examples = [f"{{{{{node_type.replace('.', '_')}.{key}}}}}" for key in list(outputs)[:3]]
    if node_type == "trigger":
        variable_examples = ["{{trigger.message}}", "{{trigger.<input_key>}}"]
    return {
        "purpose": metadata.get("description") or f"{node_type}をWorkflow内で実行します。",
        "when_to_use": _when_to_use(node_type, effect, capabilities),
        "when_not_to_use": _when_not_to_use(node_type, effect),
        "configuration": [
            {
                "key": key, "type": schema.get("type", "string"),
                "required": bool(schema.get("required")),
                "default": schema.get("default"), "recommended": schema.get("recommended"),
                "description": schema.get("reason") or "実行動作を調整する設定です。",
            }
            for key, schema in config_schema.items()
        ],
        "typed_inputs": metadata.get("input_schema") or {},
        "typed_outputs": outputs,
        "variable_examples": variable_examples or [f"{{{{{node_type.replace('.', '_')}.output}}}}"],
        "side_effect": {"level": effect, "label": TITLES.get(effect, effect), "requires_review": effect in {"write", "external", "process"}},
        "permissions": ["workflows.run", *capabilities],
        "secrets": {
            "accepted_keys": sensitive_keys,
            "policy": "秘密値はliteralで保存せず{{secrets.NAME}}参照を使用し、履歴・出力・監査ではredactします。" if sensitive_keys or effect == "external" else "このnodeは通常Secretを必要としません。上流Secretを出力へ複製しないでください。",
        },
        "retry_timeout_error_route": {
            "retry_supported": bool(supports.get("retry")), "cancel_supported": bool(supports.get("cancel")),
            "guidance": "一時失敗だけを有限回retryし、node_timeoutを設定します。回復処理はon_error=branchのerror／timeout handleへ接続します。" if supports.get("retry") else "内部または明示的な再開契約を使うため、共通retryは使用しません。error／timeout状態を履歴で確認します。",
        },
        "representative_errors": ERRORS_BY_EFFECT.get(effect, ERRORS_BY_EFFECT["none"]),
        "performance_cost": "外部latency・rate limit・token/compute使用量を見積もり、件数上限とtimeoutを設定してください。" if effect == "external" or "llm" in capabilities else "入力件数・payload上限を守り、大量データはbatch／subflowへ分割してください。",
        "recipes": _recipes(node_type, metadata.get("ui_hints", {}).get("primary_input"), metadata.get("ui_hints", {}).get("primary_output")),
        "migration_note": f"node_version={metadata.get('version', 1)}、metadata_version={metadata.get('metadata_version', 3)}。未知fieldは保持し、既存値を推奨値で自動上書きしません。",
    }
