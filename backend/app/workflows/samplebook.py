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
    {
        "id": "typed-guard-return",
        "title": "入力ガードと明示 Return",
        "icon": "✓",
        "category": "入門",
        "desc": "条件、注釈、Assert、明示エラー、型付きReturnを外部サービスなしで学ぶ安全な最小フロー。",
        "usage": (
            "成功と想定内エラーを明示するフロー制御の基本形です。外部サービスなしで実行できます。\n\n"
            "■ 動かし方\n"
            "1. statusへ ready を入力するとNoteとAssertを通過し、型付き結果をReturnします\n"
            "2. ready以外では flow.error が INPUT_NOT_READY を返し、無駄なretryを行いません\n\n"
            "■ 学べること\n"
            "- flow.returnは終端専用で、RunnerとAPIへ同じoutput contractを返す\n"
            "- flow.errorとtest.assertの失敗は機械判定可能なcodeを持つ\n"
            "- flow.noteは副作用なしで実行履歴へ判断材料を残す"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "状態入力", {"mode": "manual", "inputs": [
                    {"key": "status", "label": "状態", "type": "text", "required": True,
                     "default": "ready"},
                ]}, 40),
                _n("guard", "condition.if", "実行条件", {
                    "left": "{{trigger.status}}", "op": "eq", "right": "ready",
                }, 300),
                _n("note", "flow.note", "検証開始を記録", {
                    "level": "info", "text": "status={{trigger.status}} を検証します",
                }, 560, 80),
                _n("assert", "test.assert", "状態を検証", {
                    "actual": "{{trigger.status}}", "operator": "eq", "expected": "ready",
                    "message": "statusがreadyではありません",
                }, 800, 80),
                _n("result", "flow.return", "結果を返す", {
                    "name": "result", "title": "検証結果", "renderer": "status",
                    "value": "{{trigger.status}}", "copyable": True,
                }, 1040, 80),
                _n("error", "flow.error", "入力エラー", {
                    "code": "INPUT_NOT_READY", "message": "statusをreadyにしてください",
                    "details": '{"status":"{{trigger.status}}"}',
                }, 560, 300),
            ],
            "edges": [
                _e("trigger", "guard"), _e("guard", "note", "true"),
                _e("guard", "error", "false"), _e("note", "assert"),
                _e("assert", "result"),
            ],
        },
    },
    {
        "id": "system-disk-alert",
        "title": "ディスク監視イベントを記録",
        "icon": "◉",
        "category": "運用・自動化",
        "desc": "ディスク使用率アラートを公開済みフローで受け、型付きの監視結果を残す安全な雛形。",
        "usage": (
            "ControlDeckの監視イベントから自動起動する最小構成です。\n\n"
            "■ 動かし方\n"
            "1. アラートで disk_percent のルールを作成します\n"
            "2. このフローを公開し、自動起動を有効にします\n"
            "3. しきい値超過時にイベント種別・対象・現在値が実行履歴へ残ります\n\n"
            "下書きの変更は再公開するまで自動起動へ影響しません。通知や復旧処理はReturnの前へ追加してください。"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "ディスク監視", {
                    "mode": "system", "system_event": "disk", "resource_filter": "",
                }, 40),
                _n("note", "flow.note", "監視イベントを記録", {
                    "level": "warning",
                    "text": "{{trigger.metric}}={{trigger.value}}（しきい値 {{trigger.threshold}}）",
                }, 320),
                _n("result", "flow.return", "監視結果", {
                    "name": "disk_alert", "title": "ディスク監視", "renderer": "status",
                    "value": "{{trigger.message}}", "copyable": True,
                }, 600),
            ],
            "edges": [_e("trigger", "note"), _e("note", "result")],
        },
    },
    # ---- Workflow IDE 差別化 flow ----
    {
        "id": "execution-time-travel",
        "title": "実行Time Travel — 当時版と現在版を比較",
        "icon": "↶",
        "category": "Workflow IDE",
        "desc": "公開versionごとの実行snapshotを残し、過去入力を現在版／当時版へ再投入して差を確認する安全な教材。",
        "usage": (
            "外部依存なしでexecution time travelを確認します。まずv1を公開・実行し、prefixを変更してv2を公開します。"
            "実行履歴からv1を開き、「現在のフローで再実行」と「当時のフローで再実行」を選ぶと、同じ入力に対するresultの差と固定version IDを比較できます。\n\n"
            "■ Failure injection\nprefixを空にして公開checkのblocking diagnosticを確認します。\n\n"
            "■ Recovery\n過去入力をloadし、現在版を修正するか、当時版retryで再現条件を固定します。"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "再現入力", {"mode": "manual", "inputs": [
                    {"key": "message", "label": "Message", "type": "text", "required": True, "sample": "same input"},
                ]}, 40),
                _n("format", "data.template", "Version付き整形", {
                    "template": "v1: {{trigger.message}}", "output_format": "text",
                }, 320),
                _n("result", "flow.return", "比較結果", {
                    "name": "result", "title": "Time Travel結果", "renderer": "text", "value": "{{format.text}}",
                }, 600),
            ], "edges": [_e("trigger", "format"), _e("format", "result")],
        },
    },
    {
        "id": "local-llm-route",
        "title": "Local LLM Runtime Route",
        "icon": "⌘",
        "category": "Workflow IDE",
        "desc": "稼働中model、context、loaded状態、空きVRAMから実行経路を選び、LLMへ動的接続する。",
        "usage": (
            "AI Runtime RouteをLLMの直前に置き、{{route.base_url}}と{{route.model}}を設定へ渡します。"
            "候補を空欄にするとControlDeckが検出したOllama／llama.cpp等からBalanced戦略で選択します。\n\n"
            "■ Failure injection\nmin_contextを利用可能modelより大きくし、AI_RUNTIME_UNAVAILABLEとerror routeを確認します。\n\n"
            "■ Recovery\n条件を緩めるか候補JSONへ別runtimeを追加します。VRAM sensorがN/AでもVRAM条件が0なら実行できます。"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "質問", {"mode": "manual", "inputs": [
                    {"key": "message", "label": "質問", "type": "paragraph", "required": True, "sample": "ControlDeckとは？"},
                ]}, 40),
                _n("route", "ai.route", "Runtime自動選択", {
                    "strategy": "balanced", "min_context": 0, "min_free_vram_mb": 0, "allow_unavailable": False,
                }, 300),
                _n("llm", "llm.chat", "選択modelで回答", {
                    "base_url": "{{route.base_url}}", "model": "{{route.model}}", "prompt": "{{trigger.message}}",
                    "system": "日本語で簡潔に回答してください。", "auto_load": True,
                }, 560),
                _n("result", "flow.return", "回答", {
                    "name": "answer", "title": "回答", "renderer": "markdown", "value": "{{llm.content}}",
                }, 820),
            ], "edges": [_e("trigger", "route"), _e("route", "llm"), _e("llm", "result")],
        },
    },
    {
        "id": "pc-state-recovery",
        "title": "PC State Recovery — 再起動を越える進捗",
        "icon": "◆",
        "category": "Workflow IDE",
        "desc": "型付き永続stateとdurable delayを組み合わせ、ControlDeck再起動後も同じ実行から進捗を回復する。",
        "usage": (
            "stateを初期化し、durable delayでcheckpointを保存した後、counterを原子的に加算します。"
            "Delay待機中にcontrol-deck-webを再起動すると、同じexecution IDで処理が再開します。\n\n"
            "■ Failure injection\nDelay待機中にserviceを再起動し、実行がWAITINGからSUCCEEDEDへ戻ることを確認します。\n\n"
            "■ Recovery\nstate versionが競合した場合は最新値をgetし、expected_versionを更新して再試行します。"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "回復テスト開始", {"mode": "manual"}, 40),
                _n("initialize", "data.state", "進捗を初期化", {
                    "operation": "set", "namespace": "recovery", "key": "progress", "value_type": "integer", "value": 0,
                }, 280),
                _n("checkpoint", "control.delay", "再起動可能な待機", {"seconds": 5, "message": "service再起動を試せます"}, 520),
                _n("advance", "data.state", "進捗を加算", {
                    "operation": "increment", "namespace": "recovery", "key": "progress", "delta": 1,
                }, 760),
                _n("result", "flow.return", "回復結果", {
                    "name": "progress", "title": "回復後進捗", "renderer": "metric", "value": "{{advance.value}}",
                }, 1000),
            ], "edges": [_e("trigger", "initialize"), _e("initialize", "checkpoint"), _e("checkpoint", "advance"), _e("advance", "result")],
        },
    },
    {
        "id": "ai-patch-recovery",
        "title": "AI Diagnose & Patch — Timeout修復",
        "icon": "✦",
        "category": "Workflow IDE",
        "desc": "意図的なtimeoutをProject Intelligenceで診断し、操作差分を確認して選択適用する。",
        "usage": (
            "このSampleは最初の実行が意図的にTIMED_OUTになります。EditorのProject Intelligenceを開き、"
            "ローカル診断またはAI再検討を実行すると、node_timeoutを延長するoperation patchが提示されます。\n\n"
            "■ Failure injection\nwait 1秒に対しnode_timeout 0.1秒を設定済みです。\n\n"
            "■ Recovery\nBefore／After qualityと操作JSONを確認し、案を選択適用して再実行します。AI案は自動適用されません。"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "修復テスト", {"mode": "manual"}, 40),
                _n("wait", "util.wait", "意図的なTimeout", {"seconds": 1, "node_timeout": 0.1}, 320),
                _n("result", "flow.return", "修復後結果", {
                    "name": "result", "title": "修復結果", "renderer": "status", "value": "timeoutを解消しました",
                }, 600),
            ], "edges": [_e("trigger", "wait"), _e("wait", "result")],
        },
    },
    {
        "id": "regression-batch",
        "title": "Regression Batch — 型付き回帰テスト",
        "icon": "▦",
        "category": "Workflow IDE",
        "desc": "配列を決定的にbatch化し、Assertと保存済みtest caseの一括実行で変更を回帰確認する。",
        "usage": (
            "入力配列を2件ずつに分け、batch数が3であることをassertします。PreviewのTest Casesから複数入力を保存し、"
            "Run Allで公開前の回帰を一括実行できます。Project IntelligenceのBaselineテスト生成も利用できます。\n\n"
            "■ Failure injection\nbatch_sizeまたは期待値を変更し、ASSERTION_FAILEDを確認します。\n\n"
            "■ Recovery\n失敗caseの実入力・node outputを確認し、定義を直して同じtest batchを再実行します。"
        ),
        "definition": {
            "nodes": [
                _n("trigger", "trigger", "回帰入力", {"mode": "manual", "inputs": [
                    {"key": "items", "label": "Items", "type": "json_array", "required": True, "default": [1, 2, 3, 4, 5]},
                ]}, 40),
                _n("batch", "data.batch", "2件ずつ分割", {"input": "{{trigger.items}}", "batch_size": 2}, 300),
                _n("assert", "test.assert", "Batch数を検証", {
                    "actual": "{{batch.batch_count}}", "operator": "eq", "expected": "3", "message": "batch数が期待値と異なります",
                }, 560),
                _n("result", "flow.return", "回帰結果", {
                    "name": "batches", "title": "Batch結果", "renderer": "json_tree", "value": "{{batch.batches}}",
                }, 820),
            ], "edges": [_e("trigger", "batch"), _e("batch", "assert"), _e("assert", "result")],
        },
    },
]


def _sample_value(field: dict) -> object:
    if "sample" in field:
        return field["sample"]
    if "default" in field:
        return field["default"]
    kind = str(field.get("type") or "text").lower()
    if kind in {"number", "integer"}:
        return 1
    if kind == "boolean":
        return True
    if kind in {"json_array", "file_list", "multi_select"}:
        return []
    if kind in {"json", "key_value"}:
        return {}
    return "sample"


def _enrich_samples() -> None:
    """Attach the complete Phase 6 learning/verification contract to every sample."""
    from app.workflows.contracts import build_input_schema, build_output_schema
    from app.workflows.node_metadata import metadata_by_type
    from app.workflows.redaction import is_sensitive_key

    metadata = metadata_by_type()
    for sample in SAMPLES:
        definition = sample["definition"]
        nodes = definition.get("nodes", [])
        node_types = [str(node.get("type")) for node in nodes]
        node_meta = [metadata.get(node_type, {}) for node_type in node_types]
        trigger = next((node for node in nodes if node.get("type") == "trigger"), {})
        fields = (trigger.get("config") or {}).get("inputs", [])
        sample_input = {
            str(field.get("key")): _sample_value(field)
            for field in fields if isinstance(field, dict) and field.get("key")
        }
        side_effects = sorted({str(item.get("side_effect")) for item in node_meta if item.get("side_effect") not in {None, "none"}})
        capabilities = sorted({capability for item in node_meta for capability in item.get("capabilities", [])})
        external_nodes = [node for node in nodes if metadata.get(str(node.get("type")), {}).get("side_effect") == "external"]
        model_nodes = [node for node in nodes if "llm" in metadata.get(str(node.get("type")), {}).get("capabilities", [])]
        app_nodes = [node for node in nodes if str(node.get("type", "")).startswith("app.")]
        secret_requirements = []
        if any(node.get("type") == "notify.webhook" for node in nodes):
            secret_requirements.append("Webhook URLをSecret Storeへ登録")
        if any(
            is_sensitive_key(str(config_key)) and value
            for node in nodes for config_key, value in (node.get("config") or {}).items()
        ):
            secret_requirements.append("API credentialをSecret Storeへ登録")
        outputs = build_output_schema(definition)
        sample["guide"] = {
            "goal": sample["desc"],
            "difficulty": "advanced" if len(nodes) >= 6 or side_effects else ("intermediate" if len(nodes) >= 4 else "beginner"),
            "estimated_minutes": max(5, min(30, len(nodes) * 2 + len(side_effects) * 3)),
            "required_capabilities": capabilities,
            "side_effects": side_effects,
            "required_resources": {
                "secrets": secret_requirements,
                "models": ["利用可能なローカル／OpenAI互換model"] if model_nodes else [],
                "apps": ["対象Managed Applicationを選択"] if app_nodes else [],
            },
            "typed_input": build_input_schema(definition),
            "typed_output": outputs,
            "sample_input": sample_input,
            "expected_assertions": [
                {"path": "execution.status", "operator": "eq", "expected": "FAILED" if sample["id"] == "ai-patch-recovery" else "SUCCEEDED"},
                *({"path": "outputs", "operator": "exists"} for _ in [0] if outputs),
            ],
            "mock_data": {
                str(node.get("id")): {"mode": "fixture", "note": "外部呼び出しを行わず型付きfixtureを返します"}
                for node in external_nodes
            },
            "node_walkthrough": [
                {"order": index + 1, "node_id": node.get("id"), "type": node.get("type"), "purpose": node.get("name") or metadata.get(str(node.get("type")), {}).get("description")}
                for index, node in enumerate(nodes)
            ],
            "failure_injection": [
                "必須入力を空にしてpublish／run前のblocking diagnosticを確認します。",
                "外部依存またはtimeout条件を一時的に失敗させ、typed error routeと履歴を確認します。" if side_effects else "期待値を一時的に変更し、決定的な失敗とnode outputを確認します。",
            ],
            "recovery_retry": "失敗nodeのresolved input／typed errorを確認し、一時失敗だけを有限retryします。定義変更後は同じ保存入力で再実行します。",
            "install_preview": {
                "node_count": len(nodes), "edge_count": len(definition.get("edges", [])),
                "side_effects": side_effects, "required_capabilities": capabilities,
                "requires_model": bool(model_nodes), "requires_secret": bool(secret_requirements), "requires_app": bool(app_nodes),
                "intentional_failure": sample["id"] == "ai-patch-recovery",
            },
        }


_enrich_samples()

_BY_ID = {s["id"]: s for s in SAMPLES}


@router.get("")
def list_samples(user: User = Depends(require_permission("workflows.run"))):
    """サンプル一覧（カード表示用のメタ + プレビュー用の定義）。"""
    return [
        {
            "id": s["id"], "title": s["title"], "icon": s["icon"], "category": s["category"],
            "desc": s["desc"], "usage": s["usage"],
            "guide": s["guide"],
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
