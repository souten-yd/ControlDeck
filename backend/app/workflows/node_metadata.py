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
    "cmd.python": "process", "net.wol": "external", "flow.call": "process", "flow.map": "process", "control.try": "process",
    "event.emit": "process", "control.rate_limit": "write", "control.circuit_breaker": "write",
    # 永続データ/ファイルを変更し得る
    "file.write": "write", "file.op": "write", "http.download": "write",
    "rag.build": "write", "db.query": "write", "data.queue": "write", "data.cache": "write", "data.state": "write",
    # 外部通信・計算資源を使用する（GETでも相手側へ通信するためnoneにはしない）
    "http.request": "external", "notify.webhook": "external", "llm.chat": "external",
    "web.scrape": "external", "web.browser": "external", "web.search": "external",
    "academic.search": "external", "research.deep": "external", "code.agent": "process",
    "ai.utility": "external",
    # ローカル読み取り
    "app.status": "read", "file.read": "read", "file.exists": "read", "file.glob": "read",
    "media.ocr": "read", "rag.query": "read", "ai.route": "read",
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
    "ai.route": ["llm.route", "monitoring.read"],
    "media.ocr": ["filesystem.read", "process.exec"],
    "rag.build": ["knowledge.write", "llm"], "rag.query": ["knowledge.read", "llm"],
    "db.query": ["database"], "cmd.ssh": ["network", "process.exec"],
    "cmd.git": ["filesystem.write", "process.exec"],
    "cmd.cpp_build": ["filesystem.write", "process.exec"],
    "cmd.python": ["process.exec"], "net.wol": ["network"],
    "flow.call": ["workflow.call"],
    "flow.map": ["workflow.call"],
    "control.try": ["workflow.call"],
    "human.approval": ["human.interaction"],
    "human.form": ["human.interaction"],
    "data.queue": ["workflow.queue"],
    "data.cache": ["workflow.cache"],
    "data.state": ["workflow.state"],
    "event.emit": ["workflow.event"],
    "control.rate_limit": ["workflow.control"],
    "control.circuit_breaker": ["workflow.control"],
    "code.agent": ["filesystem.read", "filesystem.write", "process.exec", "llm"],
}

# 代表出力。値はJSON Schema風の型名（UIの変数pickerとdry-run説明用）。
OUTPUT_SCHEMAS: dict[str, dict[str, str]] = {
    "trigger": {"message": "string", "event_source": "string", "event_name": "string", "event_id": "string", "data": "object", "source_workflow_id": "integer", "resource": "string", "value": "number"},
    "app.start": {"app": "string", "status": "string"},
    "app.stop": {"app": "string", "status": "string"},
    "app.restart": {"app": "string", "status": "string"},
    "app.status": {"app": "string", "status": "string", "pid": "integer", "uptime_seconds": "number"},
    "condition.if": {"result": "boolean", "left": "any", "right": "any"},
    "control.loop": {"index": "integer", "item": "any", "total": "integer", "done": "boolean", "results": "array"},
    "human.approval": {"approved": "boolean", "message": "string", "approver": "string"},
    "human.form": {"submitted": "boolean", "response": "object", "message": "string", "assignee": "string"},
    "control.merge": {"mode": "string", "items": "array", "values": "array", "count": "integer", "succeeded": "integer", "value": "any"},
    "control.delay": {"waited_seconds": "number", "scheduled_for": "string", "resumed_at": "string", "durable": "boolean"},
    "control.try": {"execution_id": "integer", "status": "string", "ok": "boolean", "outputs": "object", "result": "string", "error": "object"},
    "flow.map": {"results": "array", "count": "integer", "succeeded": "integer", "failed": "integer", "all_succeeded": "boolean", "execution_ids": "array", "target_workflow_id": "integer", "target_version_id": "integer"},
    "control.rate_limit": {"acquired": "boolean", "scope": "string", "used": "integer", "remaining": "integer", "reset_at": "string", "waited_seconds": "number", "durable": "boolean"},
    "control.circuit_breaker": {"operation": "string", "scope": "string", "allowed": "boolean", "probe": "boolean", "state": "string", "consecutive_failures": "integer", "retry_at": "string"},
    "util.wait": {"waited_seconds": "number"}, "util.now": {"text": "string", "date": "string", "time": "string"},
    "var.set": {"value": "any"}, "string.op": {"result": "any"}, "text.markdown": {"html": "string"},
    "data.transform": {"value": "any", "valid": "boolean", "errors": "array", "csv": "string", "rows": "array", "count": "integer"},
    "data.template": {"text": "string", "value": "any", "format": "string"},
    "data.filter": {"items": "array", "count": "integer", "original_count": "integer"},
    "data.aggregate": {"result": "any", "groups": "array", "count": "integer", "operation": "string"},
    "data.batch": {"batches": "array", "batch_count": "integer", "item_count": "integer", "batch_size": "integer"},
    "data.queue": {"operation": "string", "queue": "string", "found": "boolean", "item_id": "integer", "value": "any", "size": "integer", "enqueued": "boolean"},
    "data.cache": {"operation": "string", "namespace": "string", "key": "string", "found": "boolean", "value": "any", "size": "integer", "expires_at": "string", "stored": "boolean", "deleted": "boolean"},
    "data.state": {"operation": "string", "namespace": "string", "key": "string", "found": "boolean", "value": "any", "value_type": "string", "version": "integer", "stored": "boolean", "deleted": "boolean"},
    "event.emit": {"event_id": "string", "event_name": "string", "status": "string", "target_count": "integer", "delivered_count": "integer", "failed_count": "integer", "execution_ids": "array", "failed_workflow_ids": "array", "durable": "boolean"},
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
    "flow.return": {"name": "string", "type": "string", "renderer": "string", "value": "any", "terminal": "boolean"},
    "flow.error": {},
    "flow.note": {"note": "string", "level": "string"},
    "test.assert": {"passed": "boolean", "operator": "string", "actual": "any", "expected": "any"},
    "flow.call": {"execution_id": "integer", "result": "object"},
    "notify.webhook": {"status_code": "integer", "ok": "boolean"},
    "code.agent": {"output": "string", "events": "integer", "operation": "string", "project_path": "string"},
    "ai.utility": {"vectors": "array", "dim": "integer", "results": "array", "score": "number", "reason": "string"},
    "ai.route": {"base_url": "string", "model": "string", "strategy": "string", "score": "number", "loaded": "boolean", "available": "boolean", "context_window": "integer", "vram_free_bytes": "integer", "reason": "string", "candidates": "array", "runtime_snapshot": "object"},
}

_INTEGER_KEYS = {
    "app_id", "count", "parallel", "max_results", "workflow_id", "agent_max_steps", "limit", "top_n",
    "max_rounds", "max_search_calls", "max_evidence_chars", "max_report_tokens", "retry_count", "quorum",
    "max_calls", "batch_size", "failure_threshold", "min_context", "min_free_vram_mb",
}
_NUMBER_KEYS = {"seconds", "timeout", "startup_timeout", "retry_wait", "node_timeout", "approval_timeout_seconds", "form_timeout_seconds", "window_seconds", "max_wait_seconds", "recovery_seconds"}
_BOOLEAN_KEYS = {"multiple", "full_page", "hyde", "multi_query", "recursive", "auto_load", "allow_unavailable"}
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
    "human.form": {
        "message": "必要な情報を入力してください",
        "form_timeout_seconds": 86400,
        "inputs": [{"key": "comment", "label": "入力", "type": "text", "required": True}],
    },
    "control.merge": {"mode": "wait_all"},
    "control.delay": {"seconds": 1, "message": "待機中"},
    "control.try": {"timeout": 600},
    "flow.map": {"parallel": 3, "failure_policy": "stop", "timeout": 600},
    "control.rate_limit": {"scope": "external-api", "max_calls": 10, "window_seconds": 60, "mode": "wait", "max_wait_seconds": 60},
    "control.circuit_breaker": {"operation": "check", "scope": "external-api", "failure_threshold": 3, "recovery_seconds": 60},
    "util.wait": {"seconds": 1},
    "util.now": {"format": "%Y-%m-%d %H:%M:%S"},
    "var.set": {"name": "result"},
    "string.op": {"op": "template"},
    "data.transform": {"operation": "json_parse", "delimiter": ","},
    "data.template": {"output_format": "text"},
    "data.filter": {"operator": "truthy", "sort_order": "asc", "limit": 100},
    "data.aggregate": {"operation": "count"},
    "data.batch": {"batch_size": 100},
    "data.queue": {"operation": "size", "queue": "default"},
    "data.cache": {"operation": "size", "namespace": "default", "ttl_seconds": 3600},
    "data.state": {"operation": "get", "namespace": "default", "key": "value", "value_type": "auto"},
    "event.emit": {"event_name": "custom.event", "payload": {}},
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
    "flow.return": {"name": "result", "renderer": "auto", "copyable": True},
    "flow.error": {"code": "FLOW_ERROR", "message": "ワークフローを停止します"},
    "flow.note": {"level": "info"},
    "test.assert": {"operator": "eq", "message": "期待値と一致しません"},
    "ai.utility": {"operation": "embedding", "timeout": 60, "top_n": 5},
    "ai.route": {"strategy": "balanced", "min_context": 0, "min_free_vram_mb": 0, "allow_unavailable": False},
}

# 接続時に上流出力を提案する主要入力。空欄だけを補完し、ユーザー値は上書きしない。
PRIMARY_INPUTS: dict[str, str] = {
    "condition.if": "left", "control.loop": "items", "var.set": "value",
    "control.delay": "seconds",
    "string.op": "text", "data.transform": "input", "data.template": "template",
    "data.filter": "input", "data.aggregate": "input", "data.batch": "input", "data.queue": "value", "data.cache": "value", "data.state": "value", "event.emit": "payload", "text.markdown": "text",
    "file.write": "content", "llm.chat": "prompt", "rag.build": "text",
    "rag.query": "question", "academic.search": "query", "web.search": "query",
    "research.deep": "topic", "web.scrape": "url", "http.request": "url",
    "http.download": "url", "notify.webhook": "message", "signal.display": "value",
    "output.render": "value", "flow.return": "value", "flow.error": "message",
    "flow.note": "text", "test.assert": "actual", "flow.call": "message", "flow.map": "items", "control.try": "message", "ai.utility": "input",
}

PRIMARY_OUTPUTS: dict[str, str] = {
    "trigger": "message", "app.status": "status", "condition.if": "result",
    "control.loop": "results", "control.merge": "value", "control.delay": "waited_seconds", "human.form": "response", "var.set": "value",
    "string.op": "result", "data.transform": "value", "data.template": "text",
    "data.filter": "items", "data.aggregate": "result", "data.batch": "batches", "data.queue": "value", "data.cache": "value", "data.state": "value", "event.emit": "event_id", "file.read": "content",
    "file.glob": "paths", "llm.chat": "content", "rag.query": "context",
    "academic.search": "results", "web.search": "results", "research.deep": "report",
    "http.request": "body", "web.scrape": "url", "web.browser": "content",
    "db.query": "rows", "output.render": "value", "flow.return": "value",
    "flow.note": "note", "test.assert": "passed", "flow.call": "result", "flow.map": "results", "control.try": "result",
    "code.agent": "output", "ai.utility": "results", "ai.route": "base_url",
}

EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "condition.if": [{"title": "HTTP成功時だけ続行", "config": {"left": "{{http.ok}}", "op": "eq", "right": "true"}}],
    "llm.chat": [{"title": "上流テキストを要約", "config": {"prompt": "次を簡潔に要約してください。\n\n{{input.content}}", "response_format": "text"}}],
    "data.filter": [{"title": "score 0.8以上の上位10件", "config": {"input": "{{search.results}}", "field": "score", "operator": "gte", "value": 0.8, "sort_by": "score", "sort_order": "desc", "limit": 10}}],
    "http.request": [{"title": "JSON APIを読み取る", "config": {"method": "GET", "url": "https://example.com/api/status", "timeout": 30}}],
    "output.render": [{"title": "Markdownを最終出力", "config": {"name": "answer", "title": "回答", "value": "{{llm.content}}", "renderer": "markdown", "copyable": True}}],
    "flow.return": [{"title": "処理結果を明示的に返す", "config": {"name": "result", "title": "処理結果", "value": "{{process.value}}", "renderer": "auto", "copyable": True}}],
    "test.assert": [{"title": "HTTP成功を検証", "config": {"actual": "{{http.ok}}", "operator": "eq", "expected": "true", "message": "HTTP処理が成功しませんでした"}}],
    "control.delay": [{"title": "再起動可能な待機", "config": {"seconds": 60, "message": "外部サービスの準備を待機中"}}],
    "control.try": [{"title": "公開サブフローを安全に試行", "config": {"workflow_id": 1, "message": "{{trigger.message}}", "timeout": 600}}],
    "flow.map": [{"title": "検索結果を同じ公開版で並列処理", "config": {"workflow_id": 1, "items": "{{search.results}}", "parallel": 3, "failure_policy": "collect", "message": "{{map.item}}", "timeout": 600}}],
    "control.rate_limit": [{"title": "外部APIを毎分10回に制限", "config": {"scope": "external-api", "max_calls": 10, "window_seconds": 60, "mode": "wait", "max_wait_seconds": 60}}],
    "control.circuit_breaker": [{"title": "3回失敗で1分遮断", "config": {"operation": "check", "scope": "external-api", "failure_threshold": 3, "recovery_seconds": 60}}],
    "data.batch": [{"title": "100件ずつに分割", "config": {"input": "{{source.items}}", "batch_size": 100}}],
    "data.queue": [{"title": "処理待ち項目をFIFOへ追加", "config": {"operation": "enqueue", "queue": "jobs", "value": "{{trigger.message}}"}}],
    "data.cache": [{"title": "API結果を1時間再利用", "config": {"operation": "set", "namespace": "api", "key": "latest", "value": "{{http.body}}", "ttl_seconds": 3600}}],
    "data.state": [{"title": "成功回数を原子的に加算", "config": {"operation": "increment", "namespace": "metrics", "key": "success_count", "delta": 1}}],
    "event.emit": [{"title": "レポート完成を通知", "config": {"event_name": "report.completed", "payload": {"report_id": "{{trigger.report_id}}"}}}],
    "human.form": [{"title": "担当者から修正内容を受け取る", "config": {"message": "修正内容を入力してください", "form_timeout_seconds": 86400, "inputs": [{"key": "comment", "label": "修正内容", "type": "paragraph", "required": True, "maxLength": 2000}]}}],
    "research.deep": [{"title": "標準の技術調査", "config": {"topic": "{{trigger.topic}}", "depth": "standard", "sources": ["web", "academic", "github", "direct"]}}],
    "ai.route": [{"title": "利用可能なruntimeを自動選択", "config": {"strategy": "balanced", "min_context": 0, "min_free_vram_mb": 0, "allow_unavailable": False}}],
}

QUICK_STARTS: dict[str, str] = {
    "output.render": "valueへ上流変数を挿入し、用途に合うrendererを選びます。nameはフロー内で一意にします。",
    "flow.return": "終端へ置き、valueへ返す値を挿入します。後続ノードは接続できません。",
    "flow.error": "停止理由と機械判定用codeを設定します。回復処理が必要なら失敗時設定をerror branchへ変更します。",
    "test.assert": "actualへ検証対象を挿入し、演算子とexpectedを設定します。不一致は再試行せず失敗します。",
    "control.delay": "待機秒数を設定します。長い待機はDBへ保存され、service再起動後も同じ実行から再開します。",
    "control.try": "公開済みサブフローを選びます。success/errorをそれぞれ処理し、共通後処理が必要なら両枝をcontrol.mergeへ接続します。",
    "flow.map": "公開済みサブフローとJSON arrayを選びます。各子にはitem／index／totalが型付きで渡り、結果は入力順です。失敗を親へ伝えるか収集するかを明示します。",
    "control.rate_limit": "同じ外部先を共有するノードでscopeを揃えます。通常はwait、即時に失敗経路へ送る場合だけrejectを選びます。",
    "control.circuit_breaker": "依存先の直前でcheckしallowedへ処理を接続します。成功経路でrecord_success、失敗経路でrecord_failureを同じscopeへ記録します。",
    "data.batch": "arrayと1batchの件数を指定します。batchesをSubflow Mapへ渡すと、各子フローが一定件数ずつ処理できます。",
    "data.queue": "queue名と操作を選びます。enqueueした値は実行とservice再起動を越えて保持され、dequeueは最古の1件だけを原子的に取り出します。",
    "data.cache": "namespace・key・操作を選びます。setした値はservice再起動を越えて共有されますが、TTL到来後は自動的に見つからなくなります。永続設定値には次段階のstateを使用します。",
    "data.state": "namespace・key・操作を選びます。初回setで型が固定され、versionをexpected versionへ渡すと読み取り後の競合を検出できます。incrementはnumber／integerだけを原子的に更新します。",
    "event.emit": "event名とJSON object payloadを設定します。発行はDB outboxへ先に保存され、公開・有効化済みの同名custom event triggerへ配送されます。event IDで再配送時の重複を判別できます。",
    "human.form": "入力項目を追加し、後続から {{フォームID.response.項目名}} で参照します。待機は再起動後も同じ実行から継続します。",
    "llm.chat": "モデルを検出し、promptへ上流の本文を挿入します。ローカルモデルは既定で自動起動・ロードし、準備完了まで待つため、通常は事前起動不要です。",
    "http.request": "URLを入力します。読み取りはGETのまま試し、送信時だけmethodとbodyを変更します。",
    "research.deep": "topicへ調査テーマを挿入します。まず標準深度で試し、不足時だけ詳細・徹底へ上げます。",
    "ai.route": "LLMの前に配置し、出力のbase_urlとmodelをLLMノードへ接続します。通常はbalancedのままで稼働中モデルを優先します。",
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
        item = {
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
                "retry": node_type not in (
                    "trigger", "control.loop", "human.approval", "human.form", "control.delay", "control.try", "flow.map", "control.rate_limit", "control.circuit_breaker", "flow.error", "test.assert",
                ),
                "cancel": True,
                "progress": node_type in {"control.loop", "flow.map", "data.transform", "data.filter", "data.aggregate", "file.glob", "ai.utility", "llm.chat"},
                "dry_run": True,
            },
        }
        from app.workflows.node_documentation import build_documentation

        item["documentation"] = build_documentation(item)
        result.append(item)
    return result


def metadata_by_type() -> dict[str, dict[str, Any]]:
    return {item["type"]: item for item in node_catalog()}
