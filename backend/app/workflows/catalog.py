"""ワークフロー生成 LLM へ渡すノードカタログ（コンパクト仕様）。

各ノードの id・用途・主要 config キーを列挙する。フロントの nodeTypes.ts とは別に、
LLM プロンプト用の最小限の仕様をここで一元管理する（ノード追加時はここも更新）。
"""
from __future__ import annotations

# (type, 用途, config キー例, 分岐/ループの有無)
NODE_CATALOG: list[dict] = [
    {"type": "trigger", "desc": "開始トリガー。manual/schedule/webhook/alert/custom workflow event/system", "keys": ["mode", "inputs", "webhook_token", "event_source", "event_name", "rule_filter", "system_event", "resource_filter", "file_path"]},
    {"type": "llm.chat", "desc": "LLM 生成(OpenAI互換)。管理中のローカルmodelは必要時に自動ロードして待機", "keys": ["base_url", "model", "system", "prompt", "response_format", "auto_load", "startup_timeout", "keep_alive"]},
    {"type": "rag.build", "desc": "テキストをナレッジに取り込み。collection/text/strategy", "keys": ["collection", "text", "path", "strategy"]},
    {"type": "rag.query", "desc": "ナレッジ検索。collection/question/search_mode(hybrid/vector/fulltext/graph)/hyde/multi_query。出力 context", "keys": ["collection", "question", "search_mode", "top_k", "hyde", "multi_query"]},
    {"type": "academic.search", "desc": "論文/文献/特許/市場を検索。source(all/openalex/arxiv/crossref/semanticscholar/europepmc/doaj/dblp/patent/market)/query。出力 results,text", "keys": ["source", "query", "max_results"]},
    {"type": "web.search", "desc": "Web検索。query/engine(duckduckgo/searxng)。出力 urls,results,text", "keys": ["query", "engine", "searxng_url", "max_results"]},
    {"type": "research.deep", "desc": "共有Deep Researchエンジン。Web/PDF/学術/GitHub/RAG/local codeを反復探索し引用検証済みレポートを返す", "keys": ["topic", "depth", "sources", "web_engine", "searxng_url", "categories", "collection", "project_path", "llm_base_url", "llm_model", "max_rounds", "max_search_calls", "max_evidence_chars", "max_report_tokens"]},
    {"type": "web.scrape", "desc": "URL本文/要素抽出。url/extractors。出力は抽出項目名", "keys": ["url", "extractors"]},
    {"type": "http.request", "desc": "HTTPリクエスト。method/url/body。出力 status_code,body", "keys": ["method", "url", "headers", "body", "timeout", "expected_status"]},
    {"type": "http.download", "desc": "URLをファイル保存。url/path", "keys": ["url", "path"]},
    {"type": "condition.if", "desc": "条件分岐(true/false 2出力)。left/op/right", "keys": ["left", "op", "right"], "branches": True},
    {"type": "control.loop", "desc": "順次/並列mapループ(body/done 2出力)。反復結果はresults", "keys": ["mode", "count", "items", "parallel"], "loop": True},
    {"type": "human.approval", "desc": "人の承認まで実行を一時停止。message/approver/approval_timeout_seconds。承認・却下は監査記録", "keys": ["message", "approver", "approval_timeout_seconds"]},
    {"type": "human.form", "desc": "型付きフォーム送信まで実行を永続停止。message/approver/inputs/form_timeout_seconds", "keys": ["message", "approver", "inputs", "form_timeout_seconds"]},
    {"type": "control.merge", "desc": "複数分岐をwait_all/first_success/first_complete/quorum/collectで合流", "keys": ["mode", "quorum"]},
    {"type": "control.delay", "desc": "DB checkpointへ保存する再起動対応delay。0.1秒〜7日、待機中cancel可能", "keys": ["seconds", "message"]},
    {"type": "control.try", "desc": "公開済みサブフローをtry境界として実行しsuccess/errorへ分岐。後処理は両枝をmerge", "keys": ["workflow_id", "message", "input_json", "timeout"], "branch_handles": ["success", "error"]},
    {"type": "flow.map", "desc": "JSON array各項目を同じ公開版サブフローへ型付き並列入力し、入力順で結果を集約", "keys": ["workflow_id", "items", "parallel", "failure_policy", "message", "input_json", "timeout"]},
    {"type": "control.rate_limit", "desc": "Workflow内の同一scopeで実行回数を永続共有し、固定時間窓でwait/reject", "keys": ["scope", "max_calls", "window_seconds", "mode", "max_wait_seconds"]},
    {"type": "control.circuit_breaker", "desc": "失敗が続く依存先をCLOSED/OPEN/HALF_OPENで永続遮断。checkはallowed/blockedへ分岐", "keys": ["operation", "scope", "failure_threshold", "recovery_seconds"], "branch_handles": ["allowed", "blocked"]},
    {"type": "util.wait", "desc": "待機。seconds", "keys": ["seconds"]},
    {"type": "util.now", "desc": "現在日時。format。出力 text,date,time", "keys": ["format"]},
    {"type": "var.set", "desc": "変数セット。name/value。出力 value", "keys": ["name", "value"]},
    {"type": "string.op", "desc": "文字列操作。op(template/upper/lower/trim/replace/regex_extract/split/json_extract)/text。出力 result", "keys": ["op", "text"]},
    {"type": "data.transform", "desc": "JSON parse/get/set/schema検証とCSV相互変換", "keys": ["operation", "input", "path", "value", "schema", "delimiter"]},
    {"type": "data.template", "desc": "LLMやコード実行を使わない確定的なMustache/Jinja風テンプレート整形。text/json出力", "keys": ["template", "data", "output_format"]},
    {"type": "data.filter", "desc": "arrayをfield条件でfilterし、unique/sort/limitを確定的に適用", "keys": ["input", "field", "operator", "value", "unique_by", "sort_by", "sort_order", "limit"]},
    {"type": "data.aggregate", "desc": "arrayを任意fieldでgroup化しcount/sum/avg/min/maxを集計", "keys": ["input", "operation", "field", "group_by"]},
    {"type": "data.batch", "desc": "JSON arrayを順序を保った一定件数のbatch配列へ分割", "keys": ["input", "batch_size"]},
    {"type": "data.queue", "desc": "Workflow単位の再起動対応bounded FIFO。enqueue/dequeue/peek/size", "keys": ["operation", "queue", "value"]},
    {"type": "data.cache", "desc": "Workflow単位の再起動対応TTL cache。set/get/delete/size。最大30日", "keys": ["operation", "namespace", "key", "value", "ttl_seconds"]},
    {"type": "data.state", "desc": "Workflow単位の期限なし型付きstate。get/set/delete/incrementとversion CAS", "keys": ["operation", "namespace", "key", "value", "value_type", "expected_version", "delta"]},
    {"type": "event.emit", "desc": "durable outboxへ業務eventを発行し公開済みcustom event triggerへ配送", "keys": ["event_name", "payload"]},
    {"type": "text.markdown", "desc": "Markdown→HTML。text", "keys": ["text"]},
    {"type": "file.read", "desc": "ファイル読込。path。出力 content", "keys": ["path"]},
    {"type": "file.write", "desc": "ファイル出力。path/content", "keys": ["path", "content", "append"]},
    {"type": "file.exists", "desc": "ファイル存在/サイズ確認。path", "keys": ["path"]},
    {"type": "file.op", "desc": "ファイルのcopy/move/delete/mkdir。op/source/dest_dir", "keys": ["op", "source", "dest_dir"]},
    {"type": "file.glob", "desc": "許可root内の相対glob検索。出力matches/paths", "keys": ["base_path", "pattern", "recursive", "kind", "limit"]},
    {"type": "db.query", "desc": "SQL実行。engine/path or url/query。出力 rows", "keys": ["engine", "path", "query"]},
    {"type": "media.ocr", "desc": "画像OCR。path/lang。出力 text", "keys": ["path", "lang"]},
    {"type": "app.start", "desc": "アプリ起動。app_id", "keys": ["app_id"]},
    {"type": "app.stop", "desc": "アプリ停止。app_id", "keys": ["app_id"]},
    {"type": "app.restart", "desc": "アプリ再起動。app_id", "keys": ["app_id"]},
    {"type": "app.status", "desc": "アプリ状態。app_id。出力 status", "keys": ["app_id"]},
    {"type": "cmd.ssh", "desc": "SSH実行(鍵認証)。host/user/command。出力 stdout", "keys": ["host", "user", "command"]},
    {"type": "cmd.git", "desc": "Git操作。subcommand/args/cwd", "keys": ["subcommand", "args", "cwd"]},
    {"type": "cmd.cpp_build", "desc": "CMake/Makeビルド。cwd/system/build_dir/args", "keys": ["cwd", "system", "build_dir", "cmake_args", "make_args"]},
    {"type": "cmd.python", "desc": "許可時だけPythonコード実行。code/cwd", "keys": ["code", "cwd"]},
    {"type": "web.browser", "desc": "Playwrightブラウザ操作。url/action/selector/output_path", "keys": ["url", "action", "selector", "output_path"]},
    {"type": "net.wol", "desc": "Wake-on-LAN。mac", "keys": ["mac"]},
    {"type": "notify.webhook", "desc": "Webhook通知。url/format(discord/slack/generic)/message", "keys": ["url", "format", "message"]},
    {"type": "signal.display", "desc": "チャットフローで値を返答表示。signal/value", "keys": ["signal", "value"]},
    {"type": "output.render", "desc": "型付き最終出力。Markdown/JSON/table/image/file/link/status/metric/citation等を共通contractで返す", "keys": ["name", "title", "description", "value", "renderer", "schema", "downloadable", "copyable", "sensitive", "filename", "mime_type"]},
    {"type": "flow.return", "desc": "終端専用の明示Return。output.renderと同じ型付き最終出力contract", "keys": ["name", "title", "description", "value", "renderer", "schema", "filename", "mime_type", "downloadable", "copyable", "collapsible", "sensitive"]},
    {"type": "flow.error", "desc": "意図したtyped errorを発生。code/message/details。retryせず通常のerror routeへ接続可能", "keys": ["code", "message", "details"]},
    {"type": "flow.note", "desc": "実行履歴にも残る副作用なし注釈。level(info/warning)/text", "keys": ["level", "text"]},
    {"type": "test.assert", "desc": "回帰フロー用の決定的assertion。actual/operator/expected。不一致はASSERTION_FAILED", "keys": ["actual", "operator", "expected", "message"]},
    {"type": "flow.call", "desc": "別ワークフローをサブフローとして実行し結果(result)を受け取る。workflow_id/message", "keys": ["workflow_id", "message", "input_json"]},
    {"type": "ai.utility", "desc": "embedding/rerank/LLM judgeを共通endpointで実行", "keys": ["operation", "base_url", "model", "input", "query", "documents", "rubric", "top_n", "timeout"]},
    {"type": "ai.route", "desc": "稼働中runtime・model・VRAMを評価し、後続LLMへ実行経路を返す", "keys": ["candidates", "strategy", "min_context", "min_free_vram_mb", "allow_unavailable"]},
]

from app.features.registry import is_enabled as _feature_enabled

if _feature_enabled("opencode"):
    NODE_CATALOG.append({
        "type": "code.agent",
        "desc": "OpenCode coding agent。operation(analyze/implement/fix/test/review)、project_path、instruction",
        "keys": ["operation", "project_path", "instruction", "base_url", "model"],
    })


def catalog_prompt() -> str:
    """LLM プロンプトに埋め込むノード一覧テキスト。"""
    lines = []
    for n in NODE_CATALOG:
        tag = ""
        if n.get("branch_handles"):
            tag = f" [分岐: sourceHandle={'/'.join(n['branch_handles'])}]"
        elif n.get("branches"):
            tag = " [分岐: sourceHandle=true/false]"
        elif n.get("loop"):
            tag = " [ループ: sourceHandle=body/done]"
        lines.append(f"- {n['type']}: {n['desc']}{tag}")
    return "\n".join(lines)


def valid_types() -> set[str]:
    return {n["type"] for n in NODE_CATALOG} | {"trigger"}
