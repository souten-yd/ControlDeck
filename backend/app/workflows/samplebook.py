"""サンプルブック — ノードの使い方を学べるサンプルワークフロー集。

サンプルは既存ノードのみで構成する（新規ノードは追加しない方針）。
「コピーして使う」でメインのワークフロー一覧へ登録し、それをベースに開発する。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User, Workflow
from app.security.deps import require_permission

router = APIRouter(prefix="/workflows/samples", tags=["samples"])

OLLAMA = "http://127.0.0.1:11434/v1"
MODEL = "llama3.2"


def _n(nid: str, ntype: str, name: str, config: dict, x: int, y: int = 160) -> dict:
    return {"id": nid, "type": ntype, "name": name, "config": config, "position": {"x": x, "y": y}}


def _e(source: str, target: str, branch: str | None = None) -> dict:
    e: dict = {"source": source, "target": target}
    if branch:
        e["branch"] = branch
    return e


SAMPLES: list[dict] = [
    # ---- 入門 ----
    {
        "id": "hello-llm",
        "title": "はじめての LLM チャットフロー",
        "icon": "💬",
        "category": "入門",
        "desc": "入力した質問を LLM に渡し、回答をチャットに表示する最小構成。",
        "usage": (
            "最も基本的な「トリガー → LLM 生成 → 信号表示」の 3 ノード構成です。\n\n"
            "■ 動かし方\n"
            "1. 実行ボタンを押すと「質問」の入力を求められます\n"
            "2. LLM 生成ノードが {{trigger.message}} で入力を受け取り回答を生成\n"
            "3. 信号表示ノードが {{llm.content}} をチャット欄に表示\n\n"
            "■ カスタマイズ\n"
            "- LLM 生成のシステムプロンプトで口調や役割を変更\n"
            "- モデル/エンドポイントは環境に合わせて変更（設定パネルで稼働中サーバーを検出可）"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "質問入力", {"mode": "manual", "inputs": [
                    {"key": "message", "label": "質問", "type": "paragraph", "required": True}]}, 60),
                _n("llm", "llm.chat", "LLM 回答", {"base_url": OLLAMA, "model": MODEL,
                    "system": "あなたは親切なアシスタントです。日本語で簡潔に答えてください。",
                    "prompt": "{{trigger.message}}"}, 340),
                _n("reply", "signal.display", "回答表示", {"signal": "reply", "value": "{{llm.content}}"}, 620),
            ],
            "edges": [_e("trigger", "llm"), _e("llm", "reply")],
        },
    },
    {
        "id": "web-summary",
        "title": "Web ページを要約して保存",
        "icon": "📰",
        "category": "入門",
        "desc": "URL の本文を抽出 → LLM 要約 → ファイル保存とチャット表示。",
        "usage": (
            "「Web スクレイピング → LLM 生成 → ファイル出力 / 信号表示」の定番パターンです。\n\n"
            "■ 動かし方\n"
            "1. 実行時に URL を入力\n"
            "2. スクレイピングノードが body 全文をテキスト抽出（抽出ビューワで特定要素に絞ることも可能）\n"
            "3. 要約を summary.md に保存し、チャットにも表示\n\n"
            "■ カスタマイズ\n"
            "- 抽出項目を記事タイトルや本文だけに絞ると要約精度が向上\n"
            "- file.write のパスは許可ルート配下（設定の files.allowed_roots）にしてください"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "URL 入力", {"mode": "manual", "inputs": [
                    {"key": "url", "label": "要約したいページの URL", "type": "text", "required": True}]}, 60),
                _n("scrape", "web.scrape", "本文抽出", {"url": "{{trigger.url}}", "extractors": [
                    {"name": "body", "selector": "body", "attribute": "text", "multiple": False}]}, 340),
                _n("llm", "llm.chat", "要約", {"base_url": OLLAMA, "model": MODEL,
                    "system": "与えられた Web ページ本文を、見出し付き Markdown で 300 字程度に要約してください。",
                    "prompt": "URL: {{trigger.url}}\n\n本文:\n{{scrape.body}}"}, 620),
                _n("save", "file.write", "ファイル保存", {"path": "/tmp/summary.md",
                    "content": "# 要約: {{trigger.url}}\n\n{{llm.content}}"}, 900, 60),
                _n("reply", "signal.display", "表示", {"signal": "reply", "value": "{{llm.content}}"}, 900, 260),
            ],
            "edges": [_e("trigger", "scrape"), _e("scrape", "llm"), _e("llm", "save"), _e("llm", "reply")],
        },
    },
    # ---- AI・RAG ----
    {
        "id": "rag-qa",
        "title": "ナレッジ Q&A（ハイブリッド RAG）",
        "icon": "📚",
        "category": "AI・RAG",
        "desc": "ナレッジから関連文脈を検索し、根拠付きで LLM が回答する王道 RAG。",
        "usage": (
            "「RAG 検索 → LLM 生成」の基本 RAG パイプラインです。\n\n"
            "■ 事前準備\n"
            "- Knowledge ページ（またはこのサンプルの rag.build 版）でコレクション docs に文書を取り込んでおく\n"
            "- 埋め込みモデル（例: nomic-embed-text）を Ollama に pull しておく\n\n"
            "■ ポイント\n"
            "- 検索方式は hybrid（ベクトル+全文）が既定でおすすめ\n"
            "- HyDE やマルチクエリ（RAG-Fusion）を有効にすると曖昧な質問に強くなります\n"
            "- LLM には {{rag.context}} を渡し「資料に基づいて回答」させるのが定石"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "質問入力", {"mode": "manual", "inputs": [
                    {"key": "message", "label": "質問", "type": "paragraph", "required": True}]}, 60),
                _n("rag", "rag.query", "ナレッジ検索", {"collection": "docs", "question": "{{trigger.message}}",
                    "top_k": 4, "search_mode": "hybrid"}, 340),
                _n("llm", "llm.chat", "根拠付き回答", {"base_url": OLLAMA, "model": MODEL,
                    "system": "以下の資料のみに基づいて日本語で回答してください。資料にない内容は「資料にありません」と答えること。",
                    "prompt": "資料:\n{{rag.context}}\n\n質問: {{trigger.message}}"}, 620),
                _n("reply", "signal.display", "回答表示", {"signal": "reply", "value": "{{llm.content}}"}, 900),
            ],
            "edges": [_e("trigger", "rag"), _e("rag", "llm"), _e("llm", "reply")],
        },
    },
    {
        "id": "graphrag-qa",
        "title": "GraphRAG — 知識グラフで関係を辿る Q&A",
        "icon": "🕸️",
        "category": "AI・RAG",
        "desc": "rag.query の検索方式 graph で、エンティティ間の関係（グラフ事実）も文脈に加えて回答。",
        "usage": (
            "GraphRAG は通常の類似検索に加え、取り込み時に構築した知識グラフからエンティティの関係"
            "（{{rag.facts}}）を辿って文脈を拡張します。「A と B の関係は？」のような質問に強い方式です。\n\n"
            "■ 事前準備\n"
            "- Knowledge ページでコレクションを GraphRAG 有効で構築（エンティティ抽出に LLM を使用）\n\n"
            "■ ポイント\n"
            "- 新しいノードは不要 — RAG 検索ノードの「検索方式: グラフ拡張（GraphRAG）」を選ぶだけ\n"
            "- LLM プロンプトに {{rag.context}}（本文）と {{rag.facts}}（関係）の両方を渡すのがコツ"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "質問入力", {"mode": "manual", "inputs": [
                    {"key": "message", "label": "質問（関係を問うと効果的）", "type": "paragraph", "required": True}]}, 60),
                _n("rag", "rag.query", "グラフ検索", {"collection": "docs", "question": "{{trigger.message}}",
                    "top_k": 4, "search_mode": "graph"}, 340),
                _n("llm", "llm.chat", "関係を踏まえて回答", {"base_url": OLLAMA, "model": MODEL,
                    "system": "資料とエンティティ関係に基づいて、日本語で根拠を示しつつ回答してください。",
                    "prompt": "資料:\n{{rag.context}}\n\nエンティティ関係:\n{{rag.facts}}\n\n質問: {{trigger.message}}"}, 620),
                _n("reply", "signal.display", "回答表示", {"signal": "reply", "value": "{{llm.content}}"}, 900),
            ],
            "edges": [_e("trigger", "rag"), _e("rag", "llm"), _e("llm", "reply")],
        },
    },
    {
        "id": "kb-ingest",
        "title": "Web 記事をナレッジへ取り込み",
        "icon": "📥",
        "category": "AI・RAG",
        "desc": "URL の本文を抽出してコレクションへ登録。RAG Q&A の下ごしらえ。",
        "usage": (
            "「Web スクレイピング → RAG 構築」で、気になった記事をワンアクションでナレッジ化します。\n\n"
            "■ 動かし方\n"
            "1. 実行時に URL を入力\n"
            "2. 本文を抽出し、コレクション docs にチャンク分割して埋め込み登録\n"
            "3. 追加チャンク数をチャットに表示\n\n"
            "■ カスタマイズ\n"
            "- チャンク戦略 parent_child にすると「子で検索し親を文脈に」でき、長文に強くなります\n"
            "- 取り込み後は『ナレッジ Q&A』サンプルで質問できます"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "URL 入力", {"mode": "manual", "inputs": [
                    {"key": "url", "label": "取り込む記事の URL", "type": "text", "required": True}]}, 60),
                _n("scrape", "web.scrape", "本文抽出", {"url": "{{trigger.url}}", "extractors": [
                    {"name": "body", "selector": "body", "attribute": "text", "multiple": False}]}, 340),
                _n("build", "rag.build", "ナレッジ登録", {"collection": "docs", "text": "{{scrape.body}}",
                    "source": "{{trigger.url}}", "strategy": "recursive",
                    "base_url": OLLAMA, "embed_model": "nomic-embed-text"}, 620),
                _n("reply", "signal.display", "結果表示", {"signal": "status",
                    "value": "取り込み完了: {{build.added_chunks}} チャンク（計 {{build.total_chunks}}）"}, 900),
            ],
            "edges": [_e("trigger", "scrape"), _e("scrape", "build"), _e("build", "reply")],
        },
    },
    {
        "id": "deep-research",
        "title": "Deep Research レポート生成",
        "icon": "🧠",
        "category": "AI・RAG",
        "desc": "Web・PDF・学術・GitHubを反復探索し、引用検証済みレポートを生成して保存。",
        "usage": (
            "AIアシスタントと同じDeep Researchエンジンで「計画 → 反復検索 → 本文/PDF取得 → coverage再評価 → 引用検証」まで実行します。\n\n"
            "■ 動かし方\n"
            "1. 実行時に調査テーマを入力\n"
            "2. レポートを /tmp/research.md に保存し、チャットにも表示\n\n"
            "■ カスタマイズ\n"
            "- depthをquick/standard/deep/exhaustiveから選び、探索時間と網羅性を調整\n"
            "- sourcesにragを加えると自分のナレッジ、local_codeを加えると許可root内projectも静的解析\n"
            "- research.coverage/citation_metrics/coverage_limitsを後段の品質判定に利用可能\n"
            "- 詳細・徹底は長時間かかるため、まずstandardでテストしてから深度を上げる"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "テーマ入力", {"mode": "manual", "inputs": [
                    {"key": "topic", "label": "調査テーマ", "type": "paragraph", "required": True}]}, 60),
                _n("deep", "research.deep", "Deep Research", {"topic": "{{trigger.topic}}",
                    "depth": "standard", "sources": "web,academic,github,direct",
                    "web_engine": "searxng", "categories": "general,science,news",
                    "llm_base_url": OLLAMA, "llm_model": MODEL}, 340),
                _n("save", "file.write", "レポート保存", {"path": "/tmp/research.md",
                    "content": "{{deep.report}}"}, 620, 60),
                _n("reply", "signal.display", "レポート表示", {"signal": "reply", "value": "{{deep.report}}"}, 620, 260),
            ],
            "edges": [_e("trigger", "deep"), _e("deep", "save"), _e("deep", "reply")],
        },
    },
    # ---- 情報収集 ----
    {
        "id": "paper-watch",
        "title": "論文ウォッチ（串刺し検索 → 要約 → 通知）",
        "icon": "🎓",
        "category": "情報収集",
        "desc": "毎朝、学術ソースを串刺し検索して LLM 要約を Discord へ届ける定期フロー。",
        "usage": (
            "外部検索ノードの「串刺し（全学術ソース並列）」で OpenAlex / arXiv / Crossref などを同時検索し、"
            "重複を除去した結果を LLM がダイジェスト化します。\n\n"
            "■ 事前準備\n"
            "- notify.webhook の URL を自分の Discord/Slack Webhook に変更\n"
            "- 検索クエリを自分のテーマに変更\n\n"
            "■ ポイント\n"
            "- トリガーが「毎日 08:00」なので、一覧で「スケジュール有効化」すると自動実行されます\n"
            "- 手動実行でも動作確認できます"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "毎朝 8 時", {"mode": "daily", "time": "08:00"}, 60),
                _n("papers", "academic.search", "串刺し検索", {"source": "all",
                    "query": "LLM agents workflow automation", "max_results": 6}, 340),
                _n("llm", "llm.chat", "ダイジェスト作成", {"base_url": OLLAMA, "model": MODEL,
                    "system": "論文リストから重要そうな 5 件を選び、タイトル(原題)・一言要約・URL を日本語の箇条書きで。",
                    "prompt": "{{papers.text}}"}, 620),
                _n("notify", "notify.webhook", "Discord 通知", {"url": "https://discord.com/api/webhooks/XXXX/XXXX",
                    "format": "discord", "message": "📚 今日の論文ダイジェスト\n{{llm.content}}"}, 900, 60),
                _n("reply", "signal.display", "表示", {"signal": "reply", "value": "{{llm.content}}"}, 900, 260),
            ],
            "edges": [_e("trigger", "papers"), _e("papers", "llm"), _e("llm", "notify"), _e("llm", "reply")],
        },
    },
    {
        "id": "web-digest",
        "title": "Web 検索ダイジェスト（SearXNG 対応）",
        "icon": "🔍",
        "category": "情報収集",
        "desc": "キーワードで Web 検索し、結果を LLM がまとめてチャットに表示。",
        "usage": (
            "「Web 検索 → LLM 生成 → 信号表示」の情報収集パターンです。\n\n"
            "■ ポイント\n"
            "- 既定はキー不要の DuckDuckGo。SearXNG を自前で立てている場合はエンジンを searxng にして URL を設定"
            "（JSON 出力の有効化が必要）\n"
            "- {{search.urls}} を Web スクレイピングやファイルダウンロードに繋げば本文取得まで自動化できます\n"
            "- ループノードと組み合わせると URL ごとの個別処理も可能"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "キーワード入力", {"mode": "manual", "inputs": [
                    {"key": "message", "label": "検索キーワード", "type": "text", "required": True}]}, 60),
                _n("search", "web.search", "Web 検索", {"query": "{{trigger.message}}",
                    "engine": "duckduckgo", "max_results": 8}, 340),
                _n("llm", "llm.chat", "まとめ", {"base_url": OLLAMA, "model": MODEL,
                    "system": "検索結果を要点別に整理し、参照 URL 付きの日本語ダイジェストを Markdown で作成してください。",
                    "prompt": "検索キーワード: {{trigger.message}}\n\n{{search.text}}"}, 620),
                _n("reply", "signal.display", "表示", {"signal": "reply", "value": "{{llm.content}}"}, 900),
            ],
            "edges": [_e("trigger", "search"), _e("search", "llm"), _e("llm", "reply")],
        },
    },
    {
        "id": "site-watch",
        "title": "サイト監視 — キーワード出現で通知",
        "icon": "👀",
        "category": "情報収集",
        "desc": "30 分ごとにページを取得し、キーワードが含まれていたら Webhook 通知。",
        "usage": (
            "「スクレイピング → 条件分岐 → 通知」の監視パターンです。在庫復活・障害情報・新着告知などに。\n\n"
            "■ 事前準備\n"
            "- 監視 URL・キーワード・Webhook URL を自分のものに変更\n"
            "- 一覧で「スケジュール有効化」して定期実行を開始\n\n"
            "■ ポイント\n"
            "- 条件分岐の true 側だけに通知を繋ぐことで「見つかった時だけ」通知されます\n"
            "- 変化検知にしたい場合は file.read/file.write で前回値を保存し比較する形に発展できます"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "30 分ごと", {"mode": "interval", "interval_minutes": 30}, 60),
                _n("scrape", "web.scrape", "ページ取得", {"url": "https://example.com/news", "extractors": [
                    {"name": "body", "selector": "body", "attribute": "text", "multiple": False}]}, 340),
                _n("hit", "condition.if", "キーワード判定", {"left": "{{scrape.body}}",
                    "op": "contains", "right": "入荷"}, 620),
                _n("notify", "notify.webhook", "通知", {"url": "https://discord.com/api/webhooks/XXXX/XXXX",
                    "format": "discord", "message": "🔔 キーワードを検出しました: https://example.com/news"}, 900, 80),
                _n("result", "output.render", "監視結果", {"name": "monitor_status", "title": "サイト監視結果",
                    "renderer": "status", "value": "キーワード判定: {{hit.result}}"}, 900, 260),
                _n("alert", "output.render", "検出結果", {"name": "monitor_alert", "title": "キーワードを検出",
                    "renderer": "status", "value": "キーワードを検出し、通知処理を完了しました"}, 1180, 80),
            ],
            "edges": [_e("trigger", "scrape"), _e("scrape", "hit"), _e("hit", "notify", "true"),
                      _e("notify", "alert"), _e("hit", "result", "false")],
        },
    },
    # ---- 運用自動化 ----
    {
        "id": "app-heal",
        "title": "アプリ死活監視・自動復旧",
        "icon": "🩺",
        "category": "運用自動化",
        "desc": "5 分ごとにアプリ状態を確認し、停止していたら再起動して通知。",
        "usage": (
            "「アプリ状態取得 → 条件分岐 → アプリ再起動 → 通知」の自己修復パターンです。\n\n"
            "■ 事前準備\n"
            "- 各ノードの「アプリ」欄で、Apps ページに登録済みの管理対象アプリを選択\n"
            "- 通知先 Webhook URL を変更（不要なら通知ノードごと削除）\n\n"
            "■ ポイント\n"
            "- 条件分岐は {{status.status}} が running で「ない」場合に true 側へ進みます\n"
            "- interval を短くしすぎると負荷になるため 5 分程度がおすすめ"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "5 分ごと", {"mode": "interval", "interval_minutes": 5}, 60),
                _n("status", "app.status", "状態確認", {"app_id": 1}, 340),
                _n("down", "condition.if", "停止している?", {"left": "{{status.status}}",
                    "op": "ne", "right": "running"}, 620),
                _n("restart", "app.restart", "再起動", {"app_id": 1}, 900, 80),
                _n("notify", "notify.webhook", "復旧通知", {"url": "https://discord.com/api/webhooks/XXXX/XXXX",
                    "format": "discord", "message": "🔄 {{status.app}} が停止していたため再起動しました"}, 1180, 80),
                _n("result", "output.render", "監視結果", {"name": "application_status", "title": "アプリ監視結果",
                    "renderer": "status", "value": "{{status.app}}: {{status.status}}"}, 900, 260),
                _n("recovered", "output.render", "復旧結果", {"name": "recovery_status", "title": "アプリ復旧結果",
                    "renderer": "status", "value": "{{status.app}} を再起動し、通知処理を完了しました"}, 1460, 80),
            ],
            "edges": [_e("trigger", "status"), _e("status", "down"), _e("down", "restart", "true"),
                      _e("restart", "notify"), _e("notify", "recovered"), _e("down", "result", "false")],
        },
    },
    {
        "id": "repo-sync",
        "title": "リポジトリ自動 pull + 結果通知",
        "icon": "⎇",
        "category": "運用自動化",
        "desc": "毎日決まった時刻に git pull し、結果サマリを通知する定期メンテフロー。",
        "usage": (
            "「Git 操作 → LLM 生成（任意）→ 通知」の定期メンテパターンです。\n\n"
            "■ 事前準備\n"
            "- Git 操作ノードの作業ディレクトリを対象リポジトリに変更（許可ルート配下のみ）\n"
            "- 通知先 Webhook URL を変更\n\n"
            "■ ポイント\n"
            "- {{git.stdout}} / {{git.exit_code}} を条件分岐に繋げば「更新があった時だけビルド」なども組めます\n"
            "- C++ ビルドノードや SSH 実行ノードと組み合わせると CI 風の自動化が可能"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "毎日 07:00", {"mode": "daily", "time": "07:00"}, 60),
                _n("git", "cmd.git", "git pull", {"subcommand": "pull", "args": "", "cwd": "/home/user/repo"}, 340),
                _n("notify", "notify.webhook", "結果通知", {"url": "https://discord.com/api/webhooks/XXXX/XXXX",
                    "format": "discord", "message": "⎇ git pull 結果 (exit {{git.exit_code}})\n{{git.stdout}}"}, 620),
                _n("result", "output.render", "Git結果", {"name": "git_result", "title": "Git pull結果",
                    "renderer": "code", "value": "exit={{git.exit_code}}\n{{git.stdout}}"}, 900, 260),
            ],
            "edges": [_e("trigger", "git"), _e("git", "notify"), _e("git", "result")],
        },
    },
    {
        "id": "order-analysis",
        "title": "受注データ分析 — 抽出・集計・型付きダッシュボード",
        "icon": "📊",
        "category": "データ処理",
        "desc": "JSON受注データを金額で抽出し、地域別集計・表・KPIへ並列出力する複合フロー。外部サービス不要。",
        "usage": (
            "型付き入力、配列filter、group集計、並列分岐、複数のoutput contractを組み合わせた実用例です。\n\n"
            "■ 動かし方\n"
            "1. 受注データへJSON配列を入力（初期サンプルをそのまま利用できます）\n"
            "2. 最低金額以上の注文だけを抽出し、金額降順に並べます\n"
            "3. 地域別の売上集計を作り、対象注文をTable、集計をJSON、件数をMetricで同時表示します\n\n"
            "■ 学べること\n"
            "- {{trigger.orders}} の型付き入力を後段のarray処理へ渡す方法\n"
            "- 1つの出力から複数ノードへ分岐して並列処理する方法\n"
            "- output.renderを複数置き、APIでもGUIでも同じ正式出力を返す方法"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "受注データ入力", {"mode": "manual", "inputs": [
                    {"key": "orders", "label": "受注データ", "type": "JSON", "required": True,
                     "default": [{"id": "A-101", "region": "東日本", "amount": 12000},
                                 {"id": "A-102", "region": "西日本", "amount": 4800},
                                 {"id": "A-103", "region": "東日本", "amount": 8600}]},
                    {"key": "minimum", "label": "最低金額", "type": "number", "required": True, "default": 5000},
                ]}, 40),
                _n("filter", "data.filter", "対象注文を抽出", {"input": "{{trigger.orders}}", "field": "amount",
                    "operator": "gte", "value": "{{trigger.minimum}}", "unique_by": "id",
                    "sort_by": "amount", "sort_order": "desc"}, 320),
                _n("aggregate", "data.aggregate", "地域別売上を集計", {"input": "{{filter.items}}",
                    "operation": "sum", "field": "amount", "group_by": "region"}, 600, 80),
                _n("table", "output.render", "対象注文テーブル", {"name": "orders", "title": "対象注文",
                    "renderer": "table", "value": "{{filter.items}}", "schema": '{"type":"array"}'}, 600, 300),
                _n("groups", "output.render", "地域別集計", {"name": "sales_by_region", "title": "地域別売上",
                    "renderer": "json_tree", "value": "{{aggregate.groups}}", "schema": '{"type":"array"}'}, 880, 60),
                _n("count", "output.render", "対象件数", {"name": "order_count", "title": "対象件数",
                    "renderer": "metric", "value": "{{filter.count}}", "schema": '{"type":"integer"}'}, 880, 300),
            ],
            "edges": [_e("trigger", "filter"), _e("filter", "aggregate"), _e("filter", "table"),
                      _e("aggregate", "groups"), _e("filter", "count")],
        },
    },
]

_BY_ID = {s["id"]: s for s in SAMPLES}


@router.get("")
def list_samples(user: User = Depends(require_permission("workflows.run"))):
    """サンプル一覧（カード表示用のメタ + プレビュー用の定義）。"""
    return [
        {
            "id": s["id"], "title": s["title"], "icon": s["icon"], "category": s["category"],
            "desc": s["desc"], "usage": s["usage"],
            "node_count": len(s["definition"]["nodes"]),
            "node_types": [n["type"] for n in s["definition"]["nodes"]],
            "definition": s["definition"],
        }
        for s in SAMPLES
    ]


class InstallBody(BaseModel):
    """コピー時にサンプル既定の LLM を利用環境のものへ差し替える（任意）。"""

    base_url: str = ""
    model: str = ""


def _substitute_llm(definition: dict, base_url: str, model: str) -> dict:
    """サンプル既定の LLM エンドポイント/モデルを指定値へ置換した定義を返す。"""
    if not base_url and not model:
        return definition
    definition = json.loads(json.dumps(definition))  # deep copy
    for node in definition.get("nodes", []):
        config = node.get("config") or {}
        for key in ("base_url", "llm_base_url"):
            if base_url and config.get(key) == OLLAMA:
                config[key] = base_url
        for key in ("model", "llm_model"):
            if model and config.get(key) == MODEL:
                config[key] = model
    return definition


@router.post("/{sample_id}/install", status_code=201)
def install_sample(
    sample_id: str,
    request: Request,
    body: InstallBody | None = None,
    user: User = Depends(require_permission("workflows.edit")),
    db: Session = Depends(get_db),
):
    """サンプルのコピーをワークフローとして登録し、エディタで開ける ID を返す。"""
    s = _BY_ID.get(sample_id)
    if s is None:
        raise HTTPException(status_code=404, detail="サンプルが見つかりません")
    definition = _substitute_llm(s["definition"], (body.base_url if body else "").strip(), (body.model if body else "").strip())
    wf = Workflow(
        name=f"{s['title']}",
        description=f"サンプル「{s['title']}」から作成。{s['desc']}",
        definition_json=json.dumps(definition, ensure_ascii=False),
        created_by=user.id,
    )
    db.add(wf)
    db.commit()
    audit.record(db, "workflow.create", user=user, resource_type="workflow",
                 resource_id=str(wf.id), request=request, metadata={"sample": sample_id})
    return {"id": wf.id, "name": wf.name}
