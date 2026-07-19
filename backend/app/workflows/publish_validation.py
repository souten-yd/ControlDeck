"""公開前検証の唯一の実装。

安全プレビュー画面と実際の公開処理が同じ判定結果を使うため、公開固有の
DB状態（secret、固定データ、回帰テスト）もここで検査する。
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workflow, WorkflowPinnedData, WorkflowSecret, WorkflowTestCase
from app.workflows import engine
from app.workflows.validation import quality_score, semantic_check


def check_publishability(db: Session, workflow: Workflow, definition: dict[str, Any] | None = None) -> dict[str, Any]:
    """draftまたは指定definitionが公開可能かを、副作用なしで判定する。"""
    blocking: list[str] = []
    warnings: list[str] = []
    if definition is None:
        definition_json = workflow.definition_json or "{}"
        try:
            definition = json.loads(definition_json)
        except json.JSONDecodeError as exc:
            return _result([str(exc)], warnings, [], [])
    else:
        definition_json = json.dumps(definition, ensure_ascii=False)

    try:
        engine.validate_definition(definition_json)
    except engine.DefinitionError as exc:
        blocking.append(str(exc))

    nodes = definition.get("nodes", []) if isinstance(definition, dict) else []
    edges = definition.get("edges", []) if isinstance(definition, dict) else []
    semantic_blocking, semantic_warnings = semantic_check(nodes, edges)
    blocking.extend(semantic_blocking)
    warnings.extend(semantic_warnings)

    output_nodes = [
        node for node in nodes
        if node.get("type") in ("signal.display", "output.render", "flow.return")
    ]
    if not output_nodes:
        blocking.append(
            "正式な最終出力ノードがありません。output.render（推奨）、signal.display、"
            "または flow.return を終端へ追加してください"
        )
    output_names = [
        str((node.get("config") or {}).get("signal") or (node.get("config") or {}).get("name") or node.get("id"))
        for node in output_nodes
    ]
    duplicates = sorted({name for name in output_names if output_names.count(name) > 1})
    if duplicates:
        blocking.append(f"最終出力名が重複しています: {', '.join(duplicates)}")

    references = set(re.findall(r"\{\{\s*secrets\.([A-Za-z0-9_.-]+)\s*\}\}", definition_json))
    available = set(db.execute(select(WorkflowSecret.name)).scalars().all())
    missing = sorted(references - available)
    if missing:
        blocking.append(f"未登録のsecretがあります: {', '.join(missing)}")

    pin_count = len(db.execute(select(WorkflowPinnedData.id).where(
        WorkflowPinnedData.workflow_id == workflow.id,
    )).scalars().all())
    if pin_count:
        blocking.append(f"固定データが{pin_count}件残っています。解除してから公開してください")

    cases = db.execute(select(WorkflowTestCase).where(
        WorkflowTestCase.workflow_id == workflow.id,
    )).scalars().all()
    failed_cases = [case.name for case in cases if case.last_status in ("FAILED", "ERROR", "RUNNING")]
    if failed_cases:
        blocking.append(f"未合格の回帰テストがあります: {', '.join(failed_cases)}")
    if not cases:
        warnings.append("回帰テストケースがありません")
    elif any(case.last_status == "NEVER" for case in cases):
        warnings.append("未実行の回帰テストケースがあります")

    return _result(blocking, warnings, nodes, edges)


def _result(blocking: list[str], warnings: list[str], nodes: list[dict], edges: list[dict]) -> dict[str, Any]:
    return {
        "publishable": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "quality": quality_score(nodes, edges),
    }
