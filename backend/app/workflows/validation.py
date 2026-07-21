"""ワークフロー定義の意味検証と品質スコア。

engine.validate_definition は構造検証（ID 重複・エッジ参照先・trigger 数）を担う。
本モジュールはその上位の「意味」レベルの検査を行い、生成ワークフローの自動修正と
品質表示に使う。エラーは修正必須、警告は改善推奨として区別する。
"""
from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator, SchemaError

# {{nodeId.field...}} / {{vars.x}} / {{secrets.x}} を拾う
_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

# ノード種別ごとの「これが空だと動かない」主要必須キー
REQUIRED_KEYS: dict[str, list[str]] = {
    "code.agent": ["operation", "project_path", "instruction"],
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
    "file.op": ["op", "source"],
    "condition.if": ["left", "op"],
    "flow.call": ["workflow_id"],
    "flow.map": ["workflow_id", "items"],
    "control.try": ["workflow_id"],
    "control.rate_limit": ["scope"],
    "control.circuit_breaker": ["scope"],
    "signal.display": ["value"],
    "output.render": ["name", "value"],
    "flow.return": ["name", "value"],
    "flow.note": ["text"],
    "test.assert": ["operator"],
    "control.delay": ["seconds"],
    "research.deep": ["topic"],
    "media.ocr": ["path"],
    "web.browser": ["url"],
    "net.wol": ["mac"],
    "cmd.ssh": ["host", "command"],
    "cmd.git": ["subcommand", "cwd"],
    "cmd.cpp_build": ["cwd"],
    "cmd.python": ["code"],
    "notify.webhook": ["url", "message"],
    "db.query": ["query"],
    "data.transform": ["operation", "input"],
    "data.template": ["template"],
    "data.filter": ["input", "operator"],
    "data.aggregate": ["input", "operation"],
    "data.batch": ["input"],
    "data.queue": ["queue"],
    "data.cache": ["namespace"],
    "data.state": ["namespace", "key"],
    "file.glob": ["base_path", "pattern"],
    "ai.utility": ["operation", "base_url", "model"],
    "ai.route": [],
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
        if n.get("type") == "trigger":
            mode = str(config.get("mode") or "manual")
            allowed_modes = {"manual", "interval", "daily", "cron", "webhook", "event", "system"}
            if mode not in allowed_modes:
                errors.append(f"トリガーの起動方法 '{mode}' は無効です")
            if mode == "system":
                source = str(config.get("system_event") or "")
                allowed_sources = {"gpu", "vram", "disk", "llama_server", "systemd", "file"}
                if source not in allowed_sources:
                    errors.append("システムトリガーの監視対象を選択してください")
                if source == "file" and not str(config.get("file_path") or "").strip():
                    errors.append("ファイル変更トリガーには監視パスが必要です")
            if mode == "event":
                source = str(config.get("event_source") or "alert")
                if source not in {"alert", "workflow"}:
                    errors.append("イベントトリガーのevent sourceが不正です")
                if source == "workflow":
                    event_name = str(config.get("event_name") or "").strip()
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,127}", event_name):
                        errors.append("Customイベントトリガーには有効なevent名が必要です")
        if n.get("type") == "control.loop":
            if config.get("mode") == "count" and not str(config.get("count", "") or "").strip():
                warnings.append(f"ループ '{n.get('name') or n.get('id')}' の回数が未設定です")
        if n.get("type") == "llm.chat" and str(config.get("agent_tools", "") or "") == "1":
            steps = _as_int(config.get("agent_max_steps", 6) or 6, 6)
            if steps > 12:
                warnings.append(f"エージェント '{n.get('name') or n.get('id')}' の最大ラウンド数が大きすぎます（{steps}）")
        if n.get("type") == "ai.route":
            strategy = str(config.get("strategy") or "balanced")
            if strategy not in {"balanced", "availability", "loaded", "context", "vram"}:
                errors.append(f"AI Routeノード '{n.get('name') or n.get('id')}' の選択戦略が不正です")
            for key, maximum in (("min_context", 10_000_000), ("min_free_vram_mb", 10_000_000)):
                raw = config.get(key, 0)
                if isinstance(raw, str) and "{{" in raw:
                    continue
                parsed = _as_int(raw, -1)
                if parsed < 0 or parsed > maximum:
                    errors.append(f"AI Routeノード '{n.get('name') or n.get('id')}' の{key}が範囲外です")
            raw_candidates = config.get("candidates")
            if raw_candidates not in (None, "") and not (isinstance(raw_candidates, str) and "{{" in raw_candidates):
                try:
                    parsed_candidates = json.loads(raw_candidates) if isinstance(raw_candidates, str) else raw_candidates
                except json.JSONDecodeError:
                    parsed_candidates = None
                if not isinstance(parsed_candidates, list) or len(parsed_candidates) > 20:
                    errors.append(f"AI Routeノード '{n.get('name') or n.get('id')}' の候補は20件以内のJSON arrayにしてください")

    # 5. 視覚的error routeと共通実行制御
    for n in nodes:
        if n.get("type") == "trigger":
            continue
        name = n.get("name") or n.get("id")
        config = n.get("config") or {}
        on_error = str(config.get("on_error") or "stop")
        if on_error not in {"stop", "continue", "branch"}:
            errors.append(f"ノード '{name}' の失敗時設定 '{on_error}' は無効です")
        timeout = config.get("node_timeout")
        if timeout not in (None, ""):
            try:
                if float(timeout) < 0.1:
                    errors.append(f"ノード '{name}' のtimeoutは0.1秒以上にしてください")
            except (TypeError, ValueError):
                errors.append(f"ノード '{name}' のtimeoutは数値で指定してください")
        if on_error == "branch":
            branches = [str(e.get("branch") or e.get("sourceHandle") or "") for e in edges if e.get("source") == n.get("id")]
            if "error" not in branches:
                warnings.append(f"ノード '{name}' は失敗分岐が有効ですが「失敗」経路が未接続です")
            if branches.count("error") > 1 or branches.count("timeout") > 1:
                warnings.append(f"ノード '{name}' の失敗／時間切れ経路が複数接続されています")
        if n.get("type") in {"human.approval", "human.form"}:
            is_form = n.get("type") == "human.form"
            raw_timeout = config.get("form_timeout_seconds" if is_form else "approval_timeout_seconds")
            if raw_timeout not in (None, ""):
                try:
                    if float(raw_timeout) < 0.1:
                        if is_form:
                            errors.append(f"フォームノード '{name}' の期限は0.1秒以上にしてください")
                        else:
                            errors.append(f"承認ノード '{name}' の承認期限は0.1秒以上にしてください")
                except (TypeError, ValueError):
                    if is_form:
                        errors.append(f"フォームノード '{name}' の期限は数値で指定してください")
                    else:
                        errors.append(f"承認ノード '{name}' の承認期限は数値で指定してください")
            raw_schema = config.get("form_schema")
            raw_inputs = config.get("inputs")
            if is_form and not isinstance(raw_inputs, list) and not isinstance(raw_schema, dict):
                errors.append(f"フォームノード '{name}' には入力フィールドを1件以上設定してください")
            if is_form and isinstance(raw_inputs, list):
                if not raw_inputs:
                    errors.append(f"フォームノード '{name}' には入力フィールドを1件以上設定してください")
                if len(raw_inputs) > 20:
                    errors.append(f"フォームノード '{name}' の入力フィールドは20件以内にしてください")
                seen_keys: set[str] = set()
                allowed_types = {"text", "paragraph", "number", "boolean", "select", "multi_select", "date", "datetime", "json", "json_array", "key_value"}
                for index, field in enumerate(raw_inputs, start=1):
                    if not isinstance(field, dict):
                        errors.append(f"フォームノード '{name}' の入力{index}が不正です")
                        continue
                    key = str(field.get("key") or "").strip()
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", key):
                        errors.append(f"フォームノード '{name}' の入力{index}の変数名が不正です")
                    elif key in seen_keys:
                        errors.append(f"フォームノード '{name}' の入力変数 '{key}' が重複しています")
                    seen_keys.add(key)
                    input_type = str(field.get("type") or "text")
                    if input_type not in allowed_types:
                        errors.append(f"フォームノード '{name}' の入力 '{key or index}' の型 '{input_type}' は未対応です")
                    if input_type in {"select", "multi_select"} and not str(field.get("options") or "").strip():
                        errors.append(f"フォームノード '{name}' の入力 '{key or index}' には選択肢が必要です")
            elif isinstance(raw_schema, dict):
                try:
                    Draft202012Validator.check_schema(raw_schema)
                except SchemaError as exc:
                    errors.append(f"{'フォーム' if is_form else '承認'}ノード '{name}' の入力schemaが不正です: {exc.message}")
                if raw_schema.get("type") not in (None, "object"):
                    errors.append(f"{'フォーム' if is_form else '承認'}ノード '{name}' の入力schemaはobject型にしてください")
        if n.get("type") == "control.merge":
            incoming_count = sum(1 for edge in edges if edge.get("target") == n.get("id"))
            mode = str(config.get("mode") or "wait_all")
            if incoming_count < 2:
                warnings.append(f"合流ノード '{name}' には2本以上の入力を接続してください")
            if mode not in {"wait_all", "first_success", "first_complete", "quorum", "collect"}:
                errors.append(f"合流ノード '{name}' の合流方式 '{mode}' は無効です")
            if mode == "quorum":
                quorum = _as_int(config.get("quorum"), 0)
                if quorum < 1:
                    errors.append(f"合流ノード '{name}' のquorumは1以上にしてください")
                elif incoming_count and quorum > incoming_count:
                    errors.append(f"合流ノード '{name}' のquorumが入力数を超えています（{quorum}/{incoming_count}）")
        if n.get("type") == "flow.map":
            parallel = _as_int(config.get("parallel", 3), 0)
            if parallel < 1 or parallel > 5:
                errors.append(f"Subflow Mapノード '{name}' の並列数は1〜5にしてください")
            failure_policy = str(config.get("failure_policy") or "stop")
            if failure_policy not in {"stop", "collect"}:
                errors.append(f"Subflow Mapノード '{name}' の失敗方針が不正です")
            raw_items = config.get("items")
            if isinstance(raw_items, list):
                if len(raw_items) > 100:
                    errors.append(f"Subflow Mapノード '{name}' のitemsは100件以内にしてください")
            elif isinstance(raw_items, str) and "{{" not in raw_items:
                try:
                    parsed_items = json.loads(raw_items)
                except json.JSONDecodeError:
                    parsed_items = None
                if not isinstance(parsed_items, list):
                    errors.append(f"Subflow Mapノード '{name}' のitemsは有効なJSON arrayにしてください")
                elif len(parsed_items) > 100:
                    errors.append(f"Subflow Mapノード '{name}' のitemsは100件以内にしてください")
        if n.get("type") == "data.batch":
            batch_size = config.get("batch_size", 100)
            if not (isinstance(batch_size, str) and "{{" in batch_size):
                parsed_size = _as_int(batch_size, 0)
                if parsed_size < 1 or parsed_size > 1_000:
                    errors.append(f"Batchノード '{name}' のbatch sizeは1〜1000にしてください")
            raw_input = config.get("input")
            if isinstance(raw_input, list) and len(raw_input) > 10_000:
                errors.append(f"Batchノード '{name}' のinputは10000件以内にしてください")
            elif isinstance(raw_input, str) and "{{" not in raw_input:
                try:
                    parsed_input = json.loads(raw_input)
                except json.JSONDecodeError:
                    parsed_input = None
                if not isinstance(parsed_input, list):
                    errors.append(f"Batchノード '{name}' のinputは有効なJSON arrayにしてください")
                elif len(parsed_input) > 10_000:
                    errors.append(f"Batchノード '{name}' のinputは10000件以内にしてください")
        if n.get("type") == "control.rate_limit":
            scope = str(config.get("scope") or "").strip()
            if scope and "{{" not in scope and not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", scope):
                errors.append(f"Rate Limitノード '{name}' のscopeが不正です")
            mode = str(config.get("mode") or "wait")
            if mode not in {"wait", "reject"}:
                errors.append(f"Rate Limitノード '{name}' の到達時動作が不正です")
            limits = (("最大実行数", "max_calls", 1, 10_000), ("時間窓", "window_seconds", 0.1, 86_400), ("最大待機", "max_wait_seconds", 0, 3_600))
            for label, key, minimum, maximum in limits:
                raw = config.get(key, {"max_calls": 1, "window_seconds": 60, "max_wait_seconds": 60}[key])
                if isinstance(raw, str) and "{{" in raw:
                    continue
                try:
                    number = float(raw)
                except (TypeError, ValueError):
                    number = minimum - 1
                if number < minimum or number > maximum or (key == "max_calls" and not number.is_integer()):
                    errors.append(f"Rate Limitノード '{name}' の{label}が範囲外です")
        if n.get("type") == "control.circuit_breaker":
            scope = str(config.get("scope") or "").strip()
            if scope and "{{" not in scope and not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", scope):
                errors.append(f"Circuit Breakerノード '{name}' のscopeが不正です")
            operation = str(config.get("operation") or "check")
            if operation not in {"check", "record_success", "record_failure", "status", "reset"} and "{{" not in operation:
                errors.append(f"Circuit Breakerノード '{name}' の操作が不正です")
            threshold = config.get("failure_threshold", 3)
            if not (isinstance(threshold, str) and "{{" in threshold):
                parsed_threshold = _as_int(threshold, 0)
                if parsed_threshold < 1 or parsed_threshold > 1_000:
                    errors.append(f"Circuit Breakerノード '{name}' の失敗しきい値は1〜1000にしてください")
            recovery = config.get("recovery_seconds", 60)
            if not (isinstance(recovery, str) and "{{" in recovery):
                try:
                    parsed_recovery = float(recovery)
                except (TypeError, ValueError):
                    parsed_recovery = 0
                if parsed_recovery < 0.1 or parsed_recovery > 604_800:
                    errors.append(f"Circuit Breakerノード '{name}' の回復待機は0.1秒〜7日にしてください")
            branches = [str(e.get("branch") or e.get("sourceHandle") or "") for e in edges if e.get("source") == n.get("id")]
            if operation == "check" and "allowed" not in branches:
                warnings.append(f"Circuit Breakerノード '{name}' の許可経路が未接続です")
            if operation == "check" and "blocked" not in branches:
                warnings.append(f"Circuit Breakerノード '{name}' の遮断経路が未接続です")
        if n.get("type") == "data.queue":
            operation = str(config.get("operation") or "size")
            if operation not in {"enqueue", "dequeue", "peek", "size"} and "{{" not in operation:
                errors.append(f"Queueノード '{name}' の操作 '{operation}' は無効です")
            queue_name = str(config.get("queue") or "").strip()
            if queue_name and "{{" not in queue_name and not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", queue_name):
                errors.append(f"Queueノード '{name}' のqueue名が不正です")
            if operation == "enqueue" and "value" not in config:
                errors.append(f"Queueノード '{name}' のenqueueにはvalueが必要です")
        if n.get("type") == "data.cache":
            operation = str(config.get("operation") or "size")
            if operation not in {"set", "get", "delete", "size"} and "{{" not in operation:
                errors.append(f"Cacheノード '{name}' の操作 '{operation}' は無効です")
            namespace = str(config.get("namespace") or "").strip()
            if namespace and "{{" not in namespace and not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", namespace):
                errors.append(f"Cacheノード '{name}' のnamespaceが不正です")
            key = str(config.get("key") or "").strip()
            if operation != "size" and not key:
                errors.append(f"Cacheノード '{name}' の{operation}にはkeyが必要です")
            elif key and "{{" not in key and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", key):
                errors.append(f"Cacheノード '{name}' のkeyが不正です")
            if operation == "set":
                if "value" not in config:
                    errors.append(f"Cacheノード '{name}' のsetにはvalueが必要です")
                ttl = config.get("ttl_seconds", 3600)
                if not (isinstance(ttl, str) and "{{" in ttl):
                    parsed_ttl = _as_int(ttl, 0)
                    if parsed_ttl < 1 or parsed_ttl > 2_592_000:
                        errors.append(f"Cacheノード '{name}' のTTLは1秒〜30日にしてください")
        if n.get("type") == "data.state":
            operation = str(config.get("operation") or "get")
            if operation not in {"get", "set", "delete", "increment"} and "{{" not in operation:
                errors.append(f"Stateノード '{name}' の操作 '{operation}' は無効です")
            namespace = str(config.get("namespace") or "").strip()
            if namespace and "{{" not in namespace and not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", namespace):
                errors.append(f"Stateノード '{name}' のnamespaceが不正です")
            key = str(config.get("key") or "").strip()
            if key and "{{" not in key and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", key):
                errors.append(f"Stateノード '{name}' のkeyが不正です")
            value_type = str(config.get("value_type") or "auto")
            if value_type not in {"auto", "string", "number", "integer", "boolean", "object", "array"}:
                errors.append(f"Stateノード '{name}' のvalue typeが不正です")
            if operation == "set" and "value" not in config:
                errors.append(f"Stateノード '{name}' のsetにはvalueが必要です")
            expected = config.get("expected_version")
            if expected is not None and expected != "" and not (isinstance(expected, str) and "{{" in expected):
                if isinstance(expected, bool) or not re.fullmatch(r"\+?\d+", str(expected).strip()):
                    errors.append(f"Stateノード '{name}' のexpected versionは0以上の整数にしてください")
        if n.get("type") == "event.emit":
            event_name = str(config.get("event_name") or "").strip()
            if event_name and "{{" not in event_name and not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,127}", event_name):
                errors.append(f"Event発行ノード '{name}' のevent名が不正です")
            if not event_name:
                errors.append(f"Event発行ノード '{name}' にはevent名が必要です")
            payload = config.get("payload", {})
            if not isinstance(payload, (dict, str)):
                errors.append(f"Event発行ノード '{name}' のpayloadはJSON objectにしてください")
            elif isinstance(payload, str) and "{{" not in payload:
                try:
                    parsed_payload = json.loads(payload)
                except json.JSONDecodeError:
                    parsed_payload = None
                if not isinstance(parsed_payload, dict):
                    errors.append(f"Event発行ノード '{name}' のpayloadは有効なJSON objectにしてください")
        if n.get("type") == "flow.return":
            outgoing_count = sum(1 for edge in edges if edge.get("source") == n.get("id"))
            if outgoing_count:
                errors.append(f"Returnノード '{name}' は終端専用です。後続エッジを削除してください")
        if n.get("type") == "flow.note" and str(config.get("level") or "info") not in {"info", "warning"}:
            errors.append(f"Noteノード '{name}' のlevelはinfoまたはwarningを指定してください")
        if n.get("type") == "test.assert" and str(config.get("operator") or "eq") not in {
            "eq", "ne", "gt", "gte", "lt", "lte", "contains",
        }:
            errors.append(f"Assertノード '{name}' の演算子が無効です")
        if n.get("type") == "control.delay":
            raw_seconds = config.get("seconds")
            if isinstance(raw_seconds, str) and "{{" in raw_seconds:
                pass
            else:
                try:
                    seconds = float(raw_seconds)
                    if not 0.1 <= seconds <= 7 * 86400:
                        errors.append(f"Delayノード '{name}' の秒数は0.1秒〜7日の範囲にしてください")
                except (TypeError, ValueError):
                    errors.append(f"Delayノード '{name}' の秒数は数値または上流変数で指定してください")

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
    output_types = {"signal.display", "output.render", "flow.return", "file.write", "notify.webhook", "http.request", "rag.build"}
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
