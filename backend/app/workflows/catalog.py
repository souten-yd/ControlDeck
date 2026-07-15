"""ワークフロー生成 LLM へ渡すノードカタログ（コンパクト仕様）。

各ノードの id・用途・主要 config キーを列挙する。フロントの nodeTypes.ts とは別に、
LLM プロンプト用の最小限の仕様をここで一元管理する（ノード追加時はここも更新）。
"""
from __future__ import annotations

# (type, 用途, config キー例, 分岐/ループの有無)
NODE_CATALOG: list[dict] = [
    {"type": "trigger", "desc": "開始トリガー。config.mode=manual/interval/daily/cron。inputs で実行時入力を定義", "keys": ["mode", "inputs"]},
    {"type": "llm.chat", "desc": "LLM 生成(OpenAI互換)。prompt/system/base_url/model/response_format", "keys": ["base_url", "model", "system", "prompt", "response_format"]},
    {"type": "rag.build", "desc": "テキストをナレッジに取り込み。collection/text/strategy", "keys": ["collection", "text", "path", "strategy"]},
    {"type": "rag.query", "desc": "ナレッジ検索。collection/question/search_mode(hybrid/vector/fulltext/graph)/hyde/multi_query。出力 context", "keys": ["collection", "question", "search_mode", "hyde", "multi_query"]},
    {"type": "academic.search", "desc": "論文/文献/特許/市場を検索。source(all/openalex/arxiv/crossref/semanticscholar/europepmc/doaj/dblp/patent/market)/query。出力 results,text", "keys": ["source", "query", "max_results"]},
    {"type": "web.search", "desc": "Web検索。query/engine(duckduckgo/searxng)。出力 urls,results,text", "keys": ["query", "engine", "searxng_url"]},
    {"type": "research.deep", "desc": "Deep Research。topic を分解し多ソース反復探索→引用付きレポート。出力 report", "keys": ["topic", "sources", "collection", "llm_model"]},
    {"type": "web.scrape", "desc": "URL本文/要素抽出。url/extractors。出力は抽出項目名", "keys": ["url", "extractors"]},
    {"type": "http.request", "desc": "HTTPリクエスト。method/url/body。出力 status_code,body", "keys": ["method", "url", "body"]},
    {"type": "http.download", "desc": "URLをファイル保存。url/path", "keys": ["url", "path"]},
    {"type": "condition.if", "desc": "条件分岐(true/false 2出力)。left/op/right", "keys": ["left", "op", "right"], "branches": True},
    {"type": "control.loop", "desc": "ループ(body/done 2出力)。mode=count/foreach。{{ID.item}}/{{ID.index}}", "keys": ["mode", "count", "items"], "loop": True},
    {"type": "util.wait", "desc": "待機。seconds", "keys": ["seconds"]},
    {"type": "util.now", "desc": "現在日時。format。出力 text,date,time", "keys": ["format"]},
    {"type": "var.set", "desc": "変数セット。name/value。出力 value", "keys": ["name", "value"]},
    {"type": "string.op", "desc": "文字列操作。op(template/upper/lower/trim/replace/regex_extract/split/json_extract)/text。出力 result", "keys": ["op", "text"]},
    {"type": "text.markdown", "desc": "Markdown→HTML。text", "keys": ["text"]},
    {"type": "file.read", "desc": "ファイル読込。path。出力 content", "keys": ["path"]},
    {"type": "file.write", "desc": "ファイル出力。path/content", "keys": ["path", "content"]},
    {"type": "file.exists", "desc": "ファイル存在/サイズ確認。path", "keys": ["path"]},
    {"type": "file.op", "desc": "ファイルのcopy/move/delete/mkdir。operation/path/destination", "keys": ["operation", "path", "destination"]},
    {"type": "db.query", "desc": "SQL実行。engine/path or url/query。出力 rows", "keys": ["engine", "path", "query"]},
    {"type": "media.ocr", "desc": "画像OCR。path/lang。出力 text", "keys": ["path", "lang"]},
    {"type": "app.start", "desc": "アプリ起動。app_id", "keys": ["app_id"]},
    {"type": "app.stop", "desc": "アプリ停止。app_id", "keys": ["app_id"]},
    {"type": "app.restart", "desc": "アプリ再起動。app_id", "keys": ["app_id"]},
    {"type": "app.status", "desc": "アプリ状態。app_id。出力 status", "keys": ["app_id"]},
    {"type": "cmd.ssh", "desc": "SSH実行(鍵認証)。host/user/command。出力 stdout", "keys": ["host", "user", "command"]},
    {"type": "cmd.git", "desc": "Git操作。subcommand/args/cwd", "keys": ["subcommand", "args", "cwd"]},
    {"type": "cmd.cpp_build", "desc": "CMake/Makeビルド。path/build_system/args", "keys": ["path", "build_system", "args"]},
    {"type": "cmd.python", "desc": "許可時だけPythonコード実行。code/cwd", "keys": ["code", "cwd"]},
    {"type": "web.browser", "desc": "Playwrightブラウザ操作。url/action/selector/output_path", "keys": ["url", "action", "selector", "output_path"]},
    {"type": "net.wol", "desc": "Wake-on-LAN。mac", "keys": ["mac"]},
    {"type": "notify.webhook", "desc": "Webhook通知。url/format(discord/slack/generic)/message", "keys": ["url", "format", "message"]},
    {"type": "signal.display", "desc": "チャットフローで値を返答表示。signal/value", "keys": ["signal", "value"]},
    {"type": "flow.call", "desc": "別ワークフローをサブフローとして実行し結果(result)を受け取る。workflow_id/message", "keys": ["workflow_id", "message", "input_json"]},
]


def catalog_prompt() -> str:
    """LLM プロンプトに埋め込むノード一覧テキスト。"""
    lines = []
    for n in NODE_CATALOG:
        tag = ""
        if n.get("branches"):
            tag = " [分岐: sourceHandle=true/false]"
        elif n.get("loop"):
            tag = " [ループ: sourceHandle=body/done]"
        lines.append(f"- {n['type']}: {n['desc']}{tag}")
    return "\n".join(lines)


def valid_types() -> set[str]:
    return {n["type"] for n in NODE_CATALOG} | {"trigger"}
