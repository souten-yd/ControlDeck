"""ワークフロー定義の意味検証と品質スコア。

engine.validate_definition は構造検証（ID 重複・エッジ参照先・trigger 数）を担う。
本モジュールはその上位の「意味」レベルの検査を行い、生成ワークフローの自動修正と
品質表示に使う。エラーは修正必須、警告は改善推奨として区別する。
"""
from __future__ import annotations

import re

# {{nodeId.field...}} / {{vars.x}} / {{secrets.x}} を拾う
_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

# ノード種別ごとの「これが空だと動かない」主要必須キー
REQUIRED_KEYS: dict[str, list[str]] = {
    "app.start": ["app_id"],
    "app.stop": ["app_id"],
    "app.restart": ["app_id"],
    "app.status": ["app_id"],
    "llm.chat": ["model", "prompt"],
    "rag.query": ["collection", "question"],
    "rag.build": ["collection"],
    "web.search": ["query"],
    "academic.search": ["query"],
    "web.scrape": ["url"],
    "http.request": ["url"],
    "http.download": ["url", "path"],
    "file.read": ["path"],
    "file.write": ["path"],
    "file.exists": ["path"],
    "file.op": ["operation", "path"],
    "condition.if": ["left", "op"],
    "flow.call": ["workflow_id"],
    "signal.display": ["value"],
    "research.deep": ["topic"],
    "media.ocr": ["path"],
    "web.browser": ["url"],
    "net.wol": ["mac"],
    "cmd.ssh": ["host", "command"],
    "cmd.git": ["subcommand", "cwd"],
    "cmd.cpp_build": ["path"],
    "cmd.python": ["code"],
    "notify.webhook": ["url", "message"],
    "db.query": ["query"],
}


def _referenced_ids(config: dict) -> set[str]:
    """config 内のテンプレート参照から、先頭のノード ID 群を抽出する。"""
    ids: set[str] = set()
    for v in config.values():
        if isinstance(v, str):
            for m in _TEMPLATE_RE.findall(v):
                head = m.split(".")[0]
                ids.add(head)
        elif isinstance(v, (dict, list)):
            ids |= _referenced_ids_deep(v)
    return ids


def _referenced_ids_deep(obj) -> set[str]:
    ids: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            ids |= _referenced_ids_deep(v)
    elif isinstance(obj, list):
        for v in obj:
            ids |= _referenced_ids_deep(v)
    elif isinstance(obj, str):
        for m in _TEMPLATE_RE.findall(obj):
            ids.add(m.split(".")[0])
    return ids


def semantic_check(nodes: list[dict], edges: list[dict]) -> tuple[list[str], list[str]]:
    """意味検証。(errors, warnings) を返す。errors は自動修正の対象。"""
    errors: list[str] = []
    warnings: list[str] = []
    ids = {n.get("id") for n in nodes}
    by_id = {n.get("id"): n for n in nodes}
    trigger = next((n for n in nodes if n.get("type") == "trigger"), None)

    # 1. 到達不能ノード（trigger から辿れない）
    if trigger is not None:
        adj: dict[str, list[str]] = {}
        for e in edges:
            adj.setdefault(e.get("source"), []).append(e.get("target"))
        seen = set()
        stack = [trigger.get("id")]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        for n in nodes:
            if n.get("id") not in seen and n.get("type") != "trigger":
                warnings.append(f"ノード '{n.get('name') or n.get('id')}' はトリガーから到達できません（未接続）")

    # 2. ダングリング変数参照（存在しないノード ID を参照）
    special = {"trigger", "vars", "secrets"}
    for n in nodes:
        for ref in _referenced_ids(n.get("config") or {}):
            if ref in special or ref in ids:
                continue
            # trigger の実 ID も許可
            if trigger is not None and ref == trigger.get("id"):
                continue
            errors.append(f"ノード '{n.get('name') or n.get('id')}' が存在しない変数 '{{{{{ref}...}}}}' を参照しています")

    # 3. 主要必須設定の欠落
    for n in nodes:
        req = REQUIRED_KEYS.get(n.get("type", ""), [])
        config = n.get("config") or {}
        for key in req:
            if not str(config.get(key, "") or "").strip():
                errors.append(f"ノード '{n.get('name') or n.get('id')}'（{n.get('type')}）の必須設定 '{key}' が空です")

    # 4. ループ / エージェントの終了条件
    for n in nodes:
        config = n.get("config") or {}
        if n.get("type") == "control.loop":
            if config.get("mode") == "count" and not str(config.get("count", "") or "").strip():
                warnings.append(f"ループ '{n.get('name') or n.get('id')}' の回数が未設定です")
        if n.get("type") == "llm.chat" and str(config.get("agent_tools", "") or "") == "1":
            steps = _as_int(config.get("agent_max_steps", 6) or 6, 6)
            if steps > 12:
                warnings.append(f"エージェント '{n.get('name') or n.get('id')}' の最大ラウンド数が大きすぎます（{steps}）")

    return errors, warnings


def quality_score(nodes: list[dict], edges: list[dict], run_ok: bool | None = None) -> dict:
    """生成ワークフローの品質スコア（0-100）と内訳を返す。"""
    errors, warnings = semantic_check(nodes, edges)
    non_trigger = [n for n in nodes if n.get("type") != "trigger"]

    breakdown: dict[str, int] = {}
    # 構造妥当性（意味エラーが無い）
    breakdown["構造・型整合"] = 30 if not errors else max(0, 30 - len(errors) * 10)
    # 到達性（到達不能ノードが無い）
    unreachable = sum(1 for w in warnings if "到達できません" in w)
    breakdown["到達性"] = 20 if unreachable == 0 else max(0, 20 - unreachable * 10)
    # 出力の存在（signal.display / file.write / notify.webhook などの出力ノード）
    output_types = {"signal.display", "file.write", "notify.webhook", "http.request", "rag.build"}
    breakdown["出力の明確さ"] = 15 if any(n.get("type") in output_types for n in nodes) else 5
    # エラー処理（on_error 設定 or リトライのあるノードが 1 つ以上）
    has_err_handling = any(
        (n.get("config") or {}).get("on_error") not in (None, "", "stop")
        or _as_int((n.get("config") or {}).get("retry_count", 0) or 0) > 0
        for n in non_trigger
    )
    breakdown["エラー処理"] = 15 if has_err_handling else 5
    # 実動作確認
    if run_ok is True:
        breakdown["実動作確認"] = 20
    elif run_ok is False:
        breakdown["実動作確認"] = 0
    else:
        breakdown["実動作確認"] = 8  # 未確認

    total = sum(breakdown.values())
    if run_ok is True:
        label = "動作確認済み"
    elif errors:
        label = "要修正"
    elif warnings:
        label = "一部警告"
    else:
        label = "検証済み"
    return {"score": total, "label": label, "breakdown": breakdown,
            "errors": errors, "warnings": warnings}
