"""配布アプリ（Flow App）で実行できるノードの判定。

配布物はControl Deck本体のDB・GPU runtime・allowed rootsを持たないため、
標準ライブラリとhttpxだけで完結するノードだけを許可する。判定は書き出し前に行い、
利用者へ「なぜ書き出せないか」を具体的に返す。
"""
from __future__ import annotations

from typing import Any

# 本体のnodes.pyをそのまま同梱して実行する。ここに載るのは追加サービス不要のものだけ。
PORTABLE_NODES: frozenset[str] = frozenset({
    "trigger", "condition.if", "control.merge", "control.loop", "control.delay",
    "util.wait", "util.now", "var.set", "string.op",
    "data.transform", "data.template", "data.filter", "data.aggregate", "data.batch",
    "text.markdown", "output.render", "signal.display",
    "flow.return", "flow.error", "flow.note", "test.assert",
    "http.request", "http.download", "notify.webhook",
    "file.read", "file.write", "file.exists", "file.glob", "file.op",
    "llm.chat",
})

# 実行できない理由（利用者向けの短い説明）。
NON_PORTABLE_REASONS: dict[str, str] = {
    "rag.build": "Knowledge（RAG）はControl Deckのベクトルストアが必要です",
    "rag.query": "Knowledge（RAG）はControl Deckのベクトルストアが必要です",
    "research.deep": "Deep ResearchはControl Deckの検索基盤が必要です",
    "web.search": "Web検索はControl Deckの検索設定（SearXNG等）が必要です",
    "academic.search": "学術検索はControl Deckの検索基盤が必要です",
    "web.scrape": "スクレイピングは追加ライブラリ（BeautifulSoup）が必要です",
    "web.browser": "ブラウザ操作はControl Deckのブラウザ基盤が必要です",
    "media.ocr": "OCRはControl Deckの追加ランタイムが必要です",
    "ai.utility": "AI補助（embedding/rerank/judge）はControl Deckのruntime管理が必要です",
    "ai.route": "AIルーティングはControl Deckのモデル登録情報が必要です",
    "db.query": "DB照会はControl Deckの接続設定が必要です",
    "cmd.python": "Pythonコード実行はControl Deckの隔離実行基盤が必要です",
    "cmd.ssh": "SSH実行はControl Deckの資格情報管理が必要です",
    "cmd.git": "Git操作はControl Deckのリポジトリ管理が必要です",
    "cmd.cpp_build": "C++ビルドはControl Deckのビルド基盤が必要です",
    "code.agent": "OpenCodeアドオンはControl Deck上でだけ動作します",
    "app.start": "アプリ操作はControl Deckのsystemd管理が必要です",
    "app.stop": "アプリ操作はControl Deckのsystemd管理が必要です",
    "app.restart": "アプリ操作はControl Deckのsystemd管理が必要です",
    "app.status": "アプリ操作はControl Deckのsystemd管理が必要です",
    "human.approval": "承認待ちはControl Deckの実行基盤が必要です",
    "human.form": "実行中フォームはControl Deckの実行基盤が必要です",
    "flow.call": "サブフロー呼び出しは他のWorkflow定義が必要です",
    "flow.map": "サブフローmapは他のWorkflow定義が必要です",
    "data.queue": "キューはControl Deckの永続ストアが必要です",
    "data.cache": "キャッシュはControl Deckの永続ストアが必要です",
    "data.state": "状態保存はControl Deckの永続ストアが必要です",
    "event.emit": "イベント発行はControl Deckのイベント基盤が必要です",
    "control.try": "try/catchノードはControl Deckの監査基盤が必要です",
    "control.rate_limit": "レート制限はControl Deckの永続ストアが必要です",
    "control.circuit_breaker": "サーキットブレーカーはControl Deckの永続ストアが必要です",
    "net.wol": "Wake on LANはControl Deckのネットワーク設定が必要です",
}


def _diagnostic(code: str, severity: str, message: str, *, path: str = "", fix: str = "") -> dict[str, Any]:
    item = {"code": code, "severity": severity, "message": message, "source": "flow-app"}
    if path:
        item["path"] = path
    if fix:
        item["suggestedFix"] = fix
    return item


def analyze(definition: dict[str, Any]) -> dict[str, Any]:
    """書き出し可否と診断を返す（副作用なし）。"""
    nodes = [node for node in (definition.get("nodes") or []) if isinstance(node, dict)]
    diagnostics: list[dict[str, Any]] = []
    blocked: list[str] = []
    used: dict[str, int] = {}
    for node in nodes:
        node_type = str(node.get("type") or "")
        used[node_type] = used.get(node_type, 0) + 1
        if node_type in PORTABLE_NODES or bool(node.get("disabled")):
            continue
        blocked.append(node_type)
        reason = NON_PORTABLE_REASONS.get(node_type, "配布アプリでは実行できないノードです")
        diagnostics.append(_diagnostic(
            "FLOW_APP_NODE_UNSUPPORTED", "error",
            f"「{node.get('name') or node_type}」（{node_type}）は書き出せません。{reason}",
            path=f"nodes.{node.get('id')}",
            fix="このノードを外すか、Control Deck上での実行に切り替えてください",
        ))
    if not any(str(node.get("type")) == "trigger" for node in nodes):
        diagnostics.append(_diagnostic(
            "FLOW_APP_TRIGGER_MISSING", "error", "トリガーノードがありません", fix="トリガーノードを追加してください",
        ))
    if any(str(node.get("type")) == "llm.chat" for node in nodes):
        diagnostics.append(_diagnostic(
            "FLOW_APP_LLM_ENDPOINT", "warning",
            "LLMノードを含みます。配布先で FLOW_APP_LLM_BASE_URL と FLOW_APP_LLM_MODEL を設定してください",
            fix="環境変数、または --env-file で接続先を渡します",
        ))
    if any(str(node.get("type")) in {"file.write", "file.op"} for node in nodes):
        diagnostics.append(_diagnostic(
            "FLOW_APP_FILE_WRITE", "warning",
            "ファイル書き込みノードを含みます。配布先では実行したユーザーの権限でそのまま書き込みます",
        ))
    return {
        "portable": not any(item["severity"] == "error" for item in diagnostics),
        "diagnostics": diagnostics,
        "blockedNodeTypes": sorted(set(blocked)),
        "nodeTypes": dict(sorted(used.items())),
    }
