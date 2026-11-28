"""情報の伝達ルートの地図（ルートマップ）。地図の正本はこのファイルのデータ。

目的: 「どこから・どの道で・どこへ・何が」流れるかをデータで持ち、docs/ROUTE-MAP.md の図と表をここから作る。
新しく AI（モデル・CLI・道具）を足す・情報を渡す機能を作るときは、先にここへルートを書く。
backend/tests/test_route_map.py がコードの実際の一覧（ゲートウェイの呼び出し元・MCP の道具・通知の種類・
外部への出口・tmux への送り込み）と突き合わせ、地図に無いルートがあると落ちる。
根拠: 2026-11-27 利用者「情報伝達のルートを調査して明確にし、新たに AI を追加したり機能を開発する際は
そのルートマップを保守する方法で開発を進めれば迷わないのではないか」。

作り直し: `./deck.sh route-map`（docs/ROUTE-MAP.md を書き直す。手で編集しない）。
秘密（鍵の値）はここに書かない。「どこに保存されるか」「誰が読まないか」だけを書く。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "ROUTE-MAP.md"

CHANNELS = ("api", "mcp", "gateway", "tmux", "file", "notify", "egress", "relay", "subprocess")
GUARDS = {
    "confidential": "秘密の印（機密の会話・発明の内容）を見て外部へ出さない",
    "local_only": "ローカルの LLM だけを使う（外部のモデルへは出ない）",
    "approval": "利用者の許可・設定（外部送信の許可、権限の確認、承認）が要る",
    "audit": "監査ログ・使用の記録を残す",
    "auth": "ログイン・権限・トークンが要る",
    "path_check": "パスの検証（許したディレクトリの外へ出さない）",
    "no_secrets": "鍵・トークンの値を読まない・返さない・ログに出さない（暗号化して保存）",
    "ssrf_guard": "出口の検査（内部アドレスへ出ない・許した宛先だけ）",
    "loopback": "同じ機械（127.0.0.1）の中だけ",
}
AREAS = {
    "ui": "利用者・画面・API",
    "llm": "LLM（ゲートウェイ・ローカル・外部）",
    "assistant": "AI アシスタントと OpenCode",
    "mcp": "ControlDeck の MCP の道具",
    "nexarch": "Nexarch（AI Commander・チーム）",
    "external": "外部への通信の出口",
    "notify": "通知",
    "sandbox": "箱（開発用の作業場）とホストの中継",
    "secrets": "秘密（鍵）の保存と読む所",
}


@dataclass(frozen=True)
class Node:
    id: str
    label: str


@dataclass(frozen=True)
class Route:
    id: str
    src: str  # 指示書の `from`（Python の予約語を避ける）
    dst: str  # `to`
    channel: str
    carries: str
    guards: tuple[str, ...]
    owner: tuple[str, ...]  # このルートを持つファイル（リポジトリからの相対パス）
    area: str
    notes: str = ""
    callers: tuple[str, ...] = ()  # gateway: x-control-deck-client の値
    tools: tuple[str, ...] = ()  # mcp: 道具の名前
    kinds: tuple[str, ...] = ()  # notify: 通知の種類


def _o(*paths: str) -> tuple[str, ...]:
    return tuple(f"backend/app/{p}" for p in paths)


NODES: tuple[Node, ...] = (
    Node("user", "利用者（ブラウザ・iPhone）"),
    Node("frontend", "画面（frontend / React）"),
    Node("backend", "backend（FastAPI）"),
    Node("gateway", "LLM ゲートウェイ（/api/v1/llm/v1）"),
    Node("ai_gateway", "Add-on 用 AI ゲートウェイ（ローカル専用）"),
    Node("llama", "llama.cpp"),
    Node("strata", "Strata"),
    Node("maya", "Maya"),
    Node("radiance", "Radiance"),
    Node("sly", "Sly-Radiance"),
    Node("ollama", "Ollama"),
    Node("openrouter", "外部 LLM（OpenRouter ほか）"),
    Node("assistant", "AI アシスタント（チャット）"),
    Node("workflows", "Workflow エンジン・Project Lab"),
    Node("news", "ニュース機能"),
    Node("models_mgmt", "モデル管理"),
    Node("knowledge", "ナレッジ・特許・規格"),
    Node("addons", "Add-on（外部アプリ）"),
    Node("opencode", "OpenCode（serve・実行）"),
    Node("opencode_tui", "OpenCode の TUI（tmux）"),
    Node("mcp", "ControlDeck の MCP サーバー"),
    Node("mcp_bridge", "MCP の橋（stdio）"),
    Node("sandbox_box", "箱の OpenCode（sandbox）"),
    Node("sandbox_relay", "箱の中継（sandbox_gateway）"),
    Node("terminal", "Terminal（tmux・PTY）"),
    Node("console", "Nexarch のコンソール（Claude・Codex・OpenCode の CLI）"),
    Node("workdir", "作業フォルダ（Nexarch の担当の作業場所。/data1tb・/data2tb の中）"),
    Node("codedev", "CodeDEV（作業場所の置き場。Project Lab・OpenCode・Nexarch が共有）"),
    Node("board", "チームの板（teams.json）"),
    Node("nx_locks", "ロックの保存（console-locks.json・0600）"),
    Node("usage_files", "使用率のファイル（usage・Codex の記録）"),
    Node("cli_accounts", "CLI アカウントのフォルダ（ログインの鍵が入る）"),
    Node("notify_core", "通知の入口（notify_event）"),
    Node("push_service", "Web Push（Apple・Google の push service）"),
    Node("chat_channels", "Discord・Slack・メール"),
    Node("web", "公開インターネット（Web・学術・フォーラム）"),
    Node("github", "GitHub"),
    Node("hf", "Hugging Face"),
    Node("pkg_registry", "PyPI・npm"),
    Node("google_sdk", "Google の公式配布（dl.google.com。Android SDK の cmdline-tools と sdkmanager の取得先）"),
    Node("news_hub", "QNAP ニュースハブ"),
    Node("secret_store", "鍵の保存（暗号化・0600）"),
    Node("host_systemd", "systemd・Podman・ホストの CLI"),
    Node("host_usb", "ホストの USB 機器（/sys と /dev）と usbmuxd のソケット"),
)

ROUTES: tuple[Route, ...] = (
    # ------------------------------------------------------------ ui
    Route("ui-browser", "user", "frontend", "api", "画面の操作・表示（PWA・iPhone の殻を含む）", ("auth",),
          _o("main.py"), "ui", "ログインは auth（パスワード・passkey・TOTP）。アカウントは souten だけ"),
    Route("ui-api-core", "frontend", "backend", "api",
          "画面の操作と表示データ（Dashboard・Apps・Files・Logs・System・設定・認証・アクセス管理）",
          ("auth", "audit", "path_check"),
          _o("main.py", "auth/router.py", "files/router.py", "applications/router.py", "access/router.py"), "ui",
          "機能の束の単位。endpoint を 1 本ずつは並べない"),
    Route("ui-api-models", "frontend", "backend", "api",
          "モデルの導入・起動・停止・切り替え・ライブラリ・リソース（ランタイム共通の目録・取得・停止/再開・ファイル登録の読み取り・設定の形を含む）",
          ("auth", "audit", "path_check"),
          _o("models_mgmt/router.py", "models_mgmt/model_paths.py", "models_mgmt/runtime_catalog.py",
             "models_mgmt/runtime_settings.py", "resources/router.py", "jobs/router.py"), "ui"),
    Route("ui-api-features", "frontend", "backend", "api", "機能（オプション）の導入・更新・設定、Add-on の管理", ("auth", "audit"),
          _o("features/router.py", "addons/router.py"), "ui"),
    Route("ui-api-chat", "frontend", "assistant", "api", "チャットの発言・会話・添付・音声入力・ストリーム", ("auth", "confidential"),
          _o("workflows/chat_router.py", "workflows/chat_persist.py", "workflows/asr.py"), "ui",
          "秘密の会話は confidential_scope で外部（Web 調査・外部モデル）を断つ"),
    Route("ui-api-workflows", "frontend", "workflows", "api", "Workflow・フロー App・Project Lab の編集と実行", ("auth", "audit"),
          _o("workflows/router.py", "flow_app/router.py", "project_lab/router.py"), "ui"),
    Route("ui-api-knowledge", "frontend", "knowledge", "api", "ナレッジ・特許・規格・メモリの検索と管理", ("auth", "confidential"),
          _o("knowledge/router.py", "patents/router.py", "memory/router.py"), "ui",
          "文書ごとに機密区分（confidential 既定）。外部 LLM 向けの横断検索は confidential を返さない"),
    Route("ui-api-news", "frontend", "news", "api", "ニュースの閲覧・ソース管理・ハブの設定", ("auth",), _o("news/router.py"), "ui"),
    Route("ui-api-monitor", "frontend", "backend", "api", "監視・ファン・GPU・電源・診断・アラート", ("auth", "audit"),
          _o("monitoring/router.py", "power/router.py", "diagnostics/router.py", "alerts/router.py"), "ui"),
    Route("ui-api-opencode", "frontend", "opencode", "api", "OpenCode 画面（TUI・会話・作業場・関門・取り込み）", ("auth", "audit", "path_check"),
          _o("integrations/opencode/router.py"), "ui"),
    Route("ui-api-keys", "frontend", "secret_store", "api", "外部サービスの鍵の設定（設定済みか・末尾 4 文字しか返さない）", ("auth", "no_secrets", "audit"),
          _o("settings_keys.py"), "ui"),
    Route("ui-terminal-ws", "frontend", "terminal", "api", "Terminal の入出力（WebSocket・スクロール履歴・添付）", ("auth", "audit"),
          _o("terminals/router.py", "terminals/stream.py"), "ui"),
    # ------------------------------------------------------------ llm
    Route("llm-gw-opencode", "opencode", "gateway", "gateway", "OpenCode の LLM 要求（ControlDeck が生成した runtime config が識別ヘッダを付ける）",
          ("approval", "audit", "confidential"), _o("integrations/opencode/provider.py", "models_mgmt/gateway.py"), "llm",
          "外部モデルへ出してよいかは呼び出し元ごとの egress 設定", callers=("opencode",)),
    Route("llm-gw-assistant", "assistant", "gateway", "gateway", "AI アシスタントの LLM 要求（会話・要約・圧縮・RAG）",
          ("approval", "audit", "confidential"),
          _o("workflows/chat_router.py", "workflows/chat_persist.py", "workflows/chat_compaction.py", "workflows/rag.py"),
          "llm", "秘密の会話は機密の印を付けて、ゲートウェイが cloud: を 403 で断る", callers=("assistant",)),
    Route("llm-gw-workflows", "workflows", "gateway", "gateway", "Workflow の LLM ノード（AI 補助・judge）",
          ("approval", "audit"), _o("workflows/nodes.py"), "llm",
          "気になる点: caller=workflows を名乗って送る場所が見つからない（nodes.py は識別ヘッダ無しで送り、api として扱われる）",
          callers=("workflows",)),
    Route("llm-gw-news", "news", "gateway", "gateway", "ニュースの要約・一言・分類（公開情報だけ）", ("approval", "audit"),
          _o("news/llm.py", "news/hub_jobs.py"), "llm", "ハブから借りた仕事（要約・抽出）も同じ道。送るのは記事の題・抜粋・出どころだけ",
          callers=("news",)),
    Route("llm-gw-api", "user", "gateway", "gateway", "ゲートウェイを直接使う外部クライアント（識別ヘッダ無し・未知の値は api 扱い）",
          ("auth", "approval", "audit", "confidential"), _o("models_mgmt/gateway.py", "models_mgmt/cloud.py"), "llm",
          "鍵は設定の API キー。同じ機械（loopback）＋ x-requested-with: ControlDeck は鍵なしで通る", callers=("api",)),
    Route("llm-internal-features", "knowledge", "gateway", "gateway", "特許・ナレッジ・要約・Add-on の内部の LLM 呼び出し（runtime_provider 経由）",
          ("confidential", "local_only", "audit"),
          _o("models_mgmt/runtime_provider.py", "patents/judge.py", "patents/service.py", "patents/survey.py", "knowledge/analysis.py",
             "knowledge/essence.py", "knowledge/pdf_ingest.py"),
          "llm", "発明の内容は confidential の印を付け、ローカル以外へ出ない"),
    Route("llm-visual-review", "backend", "gateway", "gateway", "画面の撮影の PNG を画像を読めるローカル LLM に見せる（関門）",
          ("confidential", "local_only"), _o("maintenance/visual_review.py"), "llm",
          "識別ヘッダは visual-review（CALLERS に無いので api 扱い）。機密の印で外部を断つ"),
    Route("llm-addon-ai", "addons", "ai_gateway", "api", "Add-on からの AI 利用（ローカル専用・Add-on のトークン）",
          ("auth", "local_only", "loopback"), _o("models_mgmt/ai_gateway.py", "addon_runtime/ai.py"), "llm"),
    Route("llm-local-llama", "gateway", "llama", "gateway", "ローカル LLM への転送（GPU の受け入れ制御・必要なら読み込み）", ("loopback", "audit"),
          _o("models_mgmt/gateway.py", "models_mgmt/llama.py"), "llm", "呼ばれたらロードする方式（起動中だけに絞らない）"),
    Route("llm-local-strata", "gateway", "strata", "gateway", "Strata への転送", ("loopback",),
          _o("models_mgmt/strata_gateway.py", "models_mgmt/strata.py"), "llm"),
    Route("llm-local-maya", "gateway", "maya", "gateway", "Maya（Maya-S・DS-XS）への転送", ("loopback",), _o("models_mgmt/maya_s.py"), "llm"),
    Route("llm-local-radiance", "gateway", "radiance", "gateway", "Radiance（podman のコンテナ）への転送", ("loopback",),
          _o("models_mgmt/radiance.py", "models_mgmt/container_runtime.py"), "llm"),
    Route("llm-local-sly", "gateway", "sly", "gateway", "Sly-Radiance への転送", ("loopback",), _o("models_mgmt/sly_radiance.py"), "llm"),
    Route("llm-local-ollama", "gateway", "ollama", "gateway", "Ollama への転送", ("loopback",), _o("models_mgmt/ollama.py"), "llm"),
    Route("llm-cloud", "gateway", "openrouter", "gateway", "外部モデル（cloud:）へ転送。月の上限・データの扱い（zdr・deny）を要求ごとに付ける",
          ("confidential", "approval", "audit", "ssrf_guard", "no_secrets"), _o("models_mgmt/gateway.py", "models_mgmt/cloud.py"), "llm",
          "秘密の印は呼び出し元の許可より先に 403。送り先は固定 URL（custom だけ HTTPS の利用者指定）。キーは暗号化して保存"),
    # ------------------------------------------------------------ assistant
    Route("asst-opencode-serve", "assistant", "opencode", "api",
          "AI アシスタントの OpenCode 会話（opencode serve の API。本文・道具の実行・許可の問い合わせ・中断）",
          ("auth", "loopback", "approval", "no_secrets", "path_check"), _o("integrations/opencode/chat_server.py", "workflows/chat_persist.py"),
          "assistant", "サーバーはプロジェクト×利用者ごと。パスワードは環境変数ファイル（0600）で渡し argv に載せない。権限は controldeck-permission プラグイン"),
    Route("asst-opencode-model", "assistant", "opencode", "api",
          "会話のモデルを OpenCode のセッションのモデルへ（会話で選んだモデルをそのまま。使えなければ Models の既定の OpenCode のモデル）",
          ("confidential", "approval", "no_secrets"), _o("integrations/opencode/model_choice.py", "workflows/chat_persist.py", "integrations/opencode/chat_server.py"),
          "assistant", "外部のモデルは OpenCode への外部送信の許可があるときだけ。秘密の会話は OpenCode を使わず一覧にも外部を出さない。選んだモデルは発言の meta に残る"),
    Route("asst-opencode-tui", "opencode_tui", "terminal", "tmux", "OpenCode の TUI を tmux のセッションで開く（--standalone。閉じたら記録だけ残す）",
          ("auth", "path_check"), _o("integrations/opencode/tui_history.py", "integrations/opencode/router.py"), "assistant"),
    Route("asst-run-inline", "assistant", "terminal", "tmux",
          "返事のコマンドの「▶ 実行」を、画面の中で実行する（tmux のセッションへ貼る・出力を読む・Ctrl-C・sudo のパスワードを標準入力で渡す）",
          ("auth", "audit", "no_secrets"), _o("terminals/run.py", "terminals/manager.py"), "assistant",
          "管理者だけ・作った本人のセッションだけ。コマンドの本文とパスワードは監査・ログに残さない（長さと指紋だけ）。パスワードは求められているときだけ送る（409）"),
    Route("asst-runtime-config", "backend", "opencode", "file", "OpenCode の隔離した runtime config（モデル・ゲートウェイの URL・MCP の橋・識別ヘッダ）を生成して渡す",
          ("no_secrets", "loopback"), _o("integrations/opencode/provider.py"), "assistant",
          "利用者のグローバル設定は触らない。MCP のトークンは短命・利用者に結びつく"),
    Route("asst-mcp-bridge", "opencode", "mcp_bridge", "mcp", "OpenCode が ControlDeck の道具を呼ぶ（stdio の橋が host の MCP へ中継）",
          ("auth", "loopback"), _o("integrations/opencode/addon_mcp_bridge.py"), "assistant", "橋は権限を持たず、渡された短命トークンで転送するだけ"),
    Route("asst-reports", "opencode", "backend", "mcp", "作業結果（UI 検証・精密調査・レビュー）を control_deck.report で受け取り画面へ",
          ("auth", "path_check"), _o("integrations/opencode/reports.py"), "assistant"),
    Route("asst-zen-key", "secret_store", "sandbox_box", "file", "OpenCode Zen（無料モデル）の鍵。箱の OpenCode が opencode.ai へ直接つなぐときだけ環境変数で渡す",
          ("no_secrets",), _o("integrations/opencode/zen.py"), "assistant", "Zen はゲートウェイを通らない（外部へ直結）。値は返さず末尾 4 文字だけ"),
    # ------------------------------------------------------------ mcp
    Route("mcp-transport", "mcp_bridge", "mcp", "mcp", "MCP の入口（list_tools・call_tool）。権限ごとに見せる道具を絞る", ("auth", "approval", "audit"),
          _o("addons/agent_mcp.py", "addons/tokens.py"), "mcp"),
    Route("mcp-lab", "mcp", "workflows", "mcp", "Project Lab の実行の開始・状態・ログ・停止", ("auth", "audit", "approval"),
          _o("addons/agent_mcp.py", "project_lab/runs.py"), "mcp",
          tools=("control_deck.lab_projects", "control_deck.lab_run_start", "control_deck.lab_run_status",
                 "control_deck.lab_run_logs", "control_deck.lab_run_stop")),
    Route("mcp-report", "mcp", "backend", "mcp", "作業結果の報告", ("auth", "path_check"),
          _o("addons/agent_mcp.py", "integrations/opencode/reports.py"), "mcp", tools=("control_deck.report",)),
    Route("mcp-research", "mcp", "web", "mcp", "調査（Web・学術・Deep・複合）の実行", ("auth", "confidential", "ssrf_guard"),
          _o("addons/agent_mcp.py", "workflows/deep_research.py"), "mcp",
          "外部へ出る調査。秘密の会話・オフライン指定では出口で止まる", tools=("control_deck.research",)),
    Route("mcp-knowledge", "mcp", "knowledge", "mcp", "ナレッジの検索", ("auth", "confidential"),
          _o("addons/agent_mcp.py", "knowledge/store.py"), "mcp", tools=("control_deck.knowledge_search",)),
    Route("mcp-news-knowledge", "mcp", "news_hub", "mcp", "ニュースハブの Things のカード（概要・別名・関係・主張・変化と矛盾）と根拠の記事を引く", ("auth", "confidential"),
          _o("addons/agent_mcp.py", "knowledge/hub_expand.py"), "mcp",
          "MCP の相手のモデルは分からないので公開の区分だけ。ハブへは読む API だけ（LAN）。件数・大きさに上限", tools=("control_deck.news_knowledge",)),
    Route("mcp-diagnostics", "mcp", "backend", "mcp", "診断の情報（ログの要約など）", ("auth", "no_secrets"),
          _o("addons/agent_mcp.py", "diagnostics/report.py"), "mcp", tools=("control_deck.diagnostics",)),
    Route("mcp-contract", "mcp", "backend", "mcp", "道具の契約（引数の仕様）の取得", ("auth",),
          _o("addons/agent_mcp.py", "addons/contract.py"), "mcp", tools=("control_deck.tool_contract",)),
    Route("mcp-project-grants", "mcp", "addons", "mcp", "Project のファイルを Add-on へ渡す・Add-on の出力を受ける（許可の付与）",
          ("auth", "path_check", "approval"), _o("addons/agent_mcp.py", "addons/execution.py"), "mcp",
          tools=("control_deck.project_output_grant", "control_deck.project_input_grant")),
    Route("mcp-addon-tools", "mcp", "addons", "mcp", "Add-on が宣言した道具（動的）。名前は Add-on ごと", ("auth", "approval", "audit"),
          _o("addons/agent_mcp.py", "addons/execution.py", "addons/registry.py"), "mcp"),
    # ------------------------------------------------------------ nexarch
    Route("nx-console-open", "backend", "console", "tmux", "コンソールを開く（CLI の実行ファイル名だけ。アカウントの環境変数はフォルダのパスだけ）",
          ("auth", "audit", "path_check", "no_secrets"), _o("commander/consoles.py", "commander/accounts.py"), "nexarch",
          "利用者の入力はコマンドに混ぜない。ログインは利用者がコンソール内で行う"),
    Route("nx-console-read", "console", "backend", "tmux", "画面の読み取り（capture_text）。状態の判定・報告の取り込み", ("auth",),
          _o("commander/status.py", "commander/consoles.py", "commander/team_watch.py", "commander/teams.py", "commander/resume.py",
             "terminals/manager.py"), "nexarch", "LLM を使わず正規表現で読む"),
    Route("nx-console-lock", "frontend", "backend", "api",
          "コンソールの鍵（ロック）の付け外し・一括で閉じる。鍵のかかったコンソールは 409 で閉じられない",
          ("auth", "audit"), _o("commander/router.py", "commander/locks.py", "terminals/router.py"), "nexarch",
          "外すのは画面（terminal.use）の操作だけ。AI・MCP へは公開しない（道具は足していない）。"
          "閉じる経路（Terminal・Nexarch・一括）は streams.close_session より前に断る"),
    Route("nx-console-lock-store", "backend", "nx_locks", "file",
          "ロックしたコンソールの情報（cli・account・cwd・label・model・effort・owner）の保存。tmux が消えた後も残す",
          ("no_secrets", "audit"), _o("commander/locks.py"), "nexarch",
          "0600・置き換えで書く。鍵の値は 1 つも入らない（label・cli・アカウント名・作業フォルダのパスだけ）"),
    Route("nx-console-vanish-watch", "console", "backend", "tmux",
          "見回り（既存の resume の 60 秒ごと）: ロックしたコンソールの tmux セッションが外から消えたら検知（監査・通知）",
          ("audit", "no_secrets"), _o("commander/locks.py", "commander/resume.py"), "nexarch",
          "tmux の確認（has-session）に失敗しても消失と扱わない。dead pane はセッションが残っているので消失ではない"),
    Route("nx-console-restore", "backend", "console", "tmux",
          "消えたロックのコンソールを同じ cli・account・作業フォルダ・label・model・effort で開き直す"
          "（続きから: Claude --continue・Codex resume --last・OpenCode は既存の再開）",
          ("audit", "path_check", "no_secrets"), _o("commander/locks.py", "commander/consoles.py"), "nexarch",
          "作業フォルダは保存した実体のパスを改めて検証。失敗も数えて 10 分 3 回まで・設定で切れる"),
    Route("nx-resume-inject", "backend", "console", "tmux", "上限からの自動再開「続けてください」を送る（inject_input）", ("audit", "approval"),
          _o("commander/resume.py", "commander/settings.py"), "nexarch",
          "送る直前に画面を読み直し、止まったままのときだけ。回数・間隔に歯止め。コンソールごとに止められる"),
    Route("nx-team-inject", "backend", "console", "tmux", "チームのタスクを担当のコンソールへ送る（短い文）", ("auth", "audit"),
          _o("commander/teams.py"), "nexarch"),
    Route("nx-team-checks", "backend", "workdir", "subprocess",
          "チームのタスクの確認のコマンドを担当の作業フォルダで実行し、合否と出力の最後を板へ（LLM なし）",
          ("audit", "path_check", "no_secrets"), _o("commander/team_checks.py"), "nexarch",
          "shell なし・最小の環境（PATH・HOME・LANG）・600 秒・出力は一時ファイル（上限 50MB）。監査ログはコマンドと終了コードだけ"),
    Route("nx-team-retry-inject", "backend", "console", "tmux",
          "確認のコマンドが落ちたとき、失敗の出力を付けて担当へ自動で再挑戦を送る（2 回まで。直せなければ blocked と通知）",
          ("audit",), _o("commander/team_checks.py"), "nexarch"),
    Route("nx-team-run", "backend", "workdir", "subprocess",
          "チームのタスクをヘッドレスの CLI（claude・codex・opencode）で 1 回実行し、JSON の行から進み具合・報告・使用量・上限を板へ（LLM なし）",
          ("audit", "path_check", "no_secrets"), _o("commander/team_runner.py"), "nexarch",
          "shell なし・最小の環境（PATH・HOME・LANG など）＋アカウントの設定フォルダ・出力は一時ファイル（上限 50MB）・2 時間で打ち切り・止められる。"
          "本文・出力はログに出さない。監査ログは担当・CLI・終了コード・時間・上限かだけ"),
    Route("nx-team-zen", "secret_store", "opencode", "file",
          "AI チームの担い手が OpenCode（Zen・Go）のモデル（opencode-go/glm-5.3 など）のとき、Zen の鍵を OpenCode へ渡し、OpenCode が opencode.ai へ直接つなぐ",
          ("no_secrets",), _o("commander/team_runner.py", "integrations/opencode/zen.py"), "nexarch",
          "ゲートウェイを通らない（外部へ直結）。鍵は箱なら 0600 の --env-file（使い終わったら消す）、ホストなら子の環境変数だけ。argv・ログに出さない。"
          "コンソール（TUI）では断る（鍵を渡す道が無い）。2026-11-28 本番で GLM をチームに当てようとして欠けが分かった"),
    Route("nx-team-sandbox-probe", "backend", "workdir", "subprocess",
          "codex のサンドボックス（bwrap）がこの機械で使えるかを一度だけ調べて覚える（`bwrap --ro-bind / / --dev /dev true`）。使えなければ codex を danger-full-access で動かす（チームの設定で止めることもできる）",
          ("no_secrets",), _o("commander/team_runner.py"), "nexarch",
          "shell なし・引数は固定・10 秒。Ubuntu の kernel.apparmor_restrict_unprivileged_userns=1 では bwrap が落ち、codex は書き込みも shell も全部失敗するのに完了と返す"),
    Route("nx-team-git", "backend", "workdir", "subprocess",
          "上限で止まった担当の作業フォルダの変更（git status --short・git diff --stat）を引き継ぎの本文のために読む",
          ("path_check", "no_secrets"), _o("commander/team_runner.py", "commander/team_dispatch.py"), "nexarch",
          "shell なし・引数は固定・15 秒・読むだけ。git が無い・リポジトリでなければ省く"),
    Route("nx-team-handoff-inject", "backend", "console", "tmux",
          "上限で止まったタスクを次の候補（画面のコンソール）へ、引き継ぎの文を付けて送り直す。待ちのタスクを空いた担当へ自動で送る（配り手）",
          ("audit",), _o("commander/team_dispatch.py", "commander/teams.py"), "nexarch",
          "同時に走らせる数・担い手ごとに 1 つ・担当ファイルの重なり・ローカルの同時の数の決まりの範囲だけ"),
    Route("nx-team-dispatcher", "backend", "board", "file",
          "待ちのタスクを空いた担い手へ割り当て（60 秒ごと＋タスクが終わったとき）・上限の方針（引き継ぐ／回復を待つ）・中断の回収",
          ("audit",), _o("commander/team_dispatch.py", "commander/team_runner.py"), "nexarch"),
    Route("nx-team-leader-run", "backend", "workdir", "subprocess",
          "「頼む」（一文）: 任命されたチームリーダー（claude・codex・opencode）をヘッドレスで長く走らせる。MCP の nexarch_team だけを渡し、書かせない（claude は dontAsk＋MCP と読む道具だけ・codex は read-only）。上限ならセカンダリで team_status から再開・finish が無ければ最大 3 回再開",
          ("audit", "path_check", "no_secrets"), _o("commander/team_leader.py", "commander/team_runner.py"), "nexarch",
          "token は argv に出さない（claude は 0600 の設定ファイル・codex は環境変数＋env_vars・opencode は 0600 の runtime config）。本文・出力・token はログに出さない"),
    Route("nx-team-leader-mcp", "mcp_bridge", "board", "mcp",
          "チームリーダーが nexarch_team の道具で Nexarch の板を動かす（計画・振り分け・待つ・結果・確認・統合・質問・終わり）。token は実行ごと・チームに縛る・期限つき・loopback だけ",
          ("auth", "loopback", "audit", "no_secrets"), _o("commander/leader_mcp.py", "integrations/opencode/addon_mcp_bridge.py"), "nexarch",
          "道具は team_status・write_plan・research・delegate・wait・read_result・run_checks・integrate・ask_user・wait_answer・report・finish"
          "（別の MCP サーバー nexarch_team の道具で、agent_mcp の control_deck.* ではない）。別のチームは触れない。再開後は古い token が使えない。"
          "呼び出しの履歴は道具の名前と成否だけ。担い手は段（tier）からコードが決める"),
    Route("nx-team-worker-mcp", "mcp_bridge", "mcp", "mcp",
          "チームの担い手（opencode・claude・codex）が ControlDeck の MCP の道具（板の読み・ノート・計画の補助）を呼ぶ。"
          "token は実行ごと・チームと担当に縛る・期限つき・loopback だけ。作った利用者の権限で動き、作成者が分からない旧チームには付けない",
          ("auth", "loopback", "audit", "no_secrets"),
          _o("commander/team_runner.py", "addons/agent_mcp.py", "integrations/opencode/addon_mcp_bridge.py"), "nexarch",
          "調べる・レビューの担当には読み取りの道具だけを渡し、書き込みの道具はサーバー側でも断る。OpenCode が有効なときだけ付け、"
          "local_only のチームはローカルの担い手だけ。token は argv・ログに出さない（0600 の設定・環境変数だけ）"),
    Route("nx-team-leader-research-web", "mcp_bridge", "web", "egress",
          "チームリーダーの research(web): 既存の調査の道具（検索・ページを読む）。チームの設定 web_research が false なら断る",
          ("auth", "confidential", "ssrf_guard"), _o("commander/leader_mcp.py", "workflows/research_tools.py"), "nexarch",
          "取得した文は資料として扱う（資料の中の指示には従わない注意書きつき）。秘密の会話・オフライン指定では出口で止まる"),
    Route("nx-team-leader-research-knowledge", "mcp_bridge", "knowledge", "api",
          "チームリーダーの research(knowledge): ナレッジの検索（公開・AI 利用可の文書だけ。cloud 扱い）",
          ("auth", "confidential"), _o("commander/leader_mcp.py", "knowledge/context.py"), "nexarch"),
    Route("nx-team-worktree-merge", "backend", "workdir", "subprocess",
          "implement の仕事ごとに git の worktree＋ブランチ（作業場所の .nexarch-wt/ の中）を作り、統合はチームのブランチへ merge --no-ff（競合は abort して implement へ戻す）・「取り込む」は元のブランチへ。shell なし・フックは動かさない",
          ("audit", "path_check"), _o("commander/team_git.py", "commander/leader_mcp.py", "commander/team_leader.py"), "nexarch",
          "作業フォルダの許可の中だけ・.git/info/exclude へ足す・Nexarch が git init した作業場所は終わりに fast-forward だけ"),
    Route("nx-team-assistant-request", "mcp", "board", "mcp",
          "AI アシスタントの会話の OpenCode から、その作業場所でチームを作ってチームリーダーに頼む・状態の要約を引く",
          ("auth", "path_check"), _o("addons/agent_mcp.py", "commander/team_leader.py"), "nexarch",
          "保存先は会話の作業場所そのまま（新しいフォルダを作らない）", tools=("control_deck.nexarch_team_request", "control_deck.nexarch_team_status")),
    Route("nx-team-models", "cli_accounts", "backend", "file",
          "担い手のモデルと考えるレベルの一覧: codex の models_cache.json（slug と考えるレベルだけ）・ゲートウェイのモデルの一覧・claude は固定の別名。5 分覚える。"
          "画面には 1 つの形（ローカル・opencode・openrouter・claude・codex の group と endpoint）で返す（/model-choices・brief B）",
          ("auth", "no_secrets"), _o("commander/models.py", "commander/router.py"), "nexarch",
          "auth.json など鍵のファイルは読まない・返さない。外へは何も取りに行かない"),
    Route("nx-team-role-console", "backend", "console", "tmux",
          "役割の担い手が画面ありのとき、Nexarch のコンソールを自動で開く（チームの作業場所・役割のモデルと考えるレベルの引数つき）",
          ("audit", "path_check"), _o("commander/team_roles.py", "commander/consoles.py"), "nexarch",
          "実行ファイル名は CLIS の表だけ・モデルと考えるレベルは英数と ._:/#- だけに検証。開ける数は役割の数まで"),
    Route("nx-team-handoff-choose", "frontend", "backend", "api",
          "引き継ぎ先が無くて止まったタスクへ、画面で選んだ担い手（コンソールとモデル別）で引き継いで再開する。選べばその役割のセカンダリへ足せる",
          ("auth", "audit"), _o("commander/router.py", "commander/team_roles.py"), "nexarch"),
    Route("nx-team-root", "backend", "workdir", "file",
          "チームの保存先（Nexarch の設定の team_root）の下へ `<名前>-日時` の作業場所を作る（git init・manifest）。AI アシスタントから作るときは会話の作業場所をそのまま使う",
          ("auth", "audit", "path_check"), _o("commander/settings.py", "commander/teams.py", "integrations/opencode/projects.py"), "nexarch",
          "保存先は作業フォルダの決まり（/data1tb・/data2tb の中・realpath・data_dir など禁止）で検証。未設定なら CodeDEV の自動の作業場所"),
    Route("nx-team-inputs", "backend", "workdir", "file",
          "チームへ渡す資料（ファイル・フォルダ）を作業場所の .nexarch/inputs へ読み取り専用（0444）の写しとして置き、依頼の全文を .nexarch/request.md へ。"
          "worktree には .nexarch のハードリンクと、あれば frontend/node_modules・.venv の相対リンクを渡す（中身は写さない）",
          ("auth", "path_check", "no_secrets"),
          _o("commander/team_inputs.py", "commander/team_leader.py", "commander/team_git.py", "commander/workdir.py", "commander/router.py"), "nexarch",
          "作業フォルダと同じ決まり（許可ルート・realpath・拒否の場所・リンク逃げ）。シンボリックリンクでの資料の公開はしない・元資料は書かない・件数と大きさに上限"),
    Route("nx-team-auto-resume", "backend", "board", "file",
          "ControlDeck の起動時に、実行中のまま残ったチームリーダーを保存済みの依頼から続きで再開する（自動再開は 30 分に 2 回まで・チームごとに永続）。"
          "続けて失敗したら止めて知らせる。利用者が止めたチームは再開しない",
          ("audit",), _o("commander/team_watch.py", "commander/team_leader.py", "commander/teams.py"), "nexarch",
          "teams.json は置き換えで書き、直前の 1 世代を .bak に残す。壊れていたら .bak から戻して診断に出す"),
    Route("ws-auto-project", "backend", "codedev", "file",
          "作業場所の自動作成（nexarch-/team-/chat-日時のフォルダ・git init）と表示名・作成元（.controldeck/project.json）",
          ("auth", "audit", "path_check"), _o("integrations/opencode/projects.py", "commander/consoles.py",
                                               "workflows/chat_persist.py", "project_lab/service.py"), "nexarch",
          "フォルダ名は変えない（OpenCode の会話の履歴がパスに結び付く）。表示名だけ変えられる"),
    Route("nx-automation-inject", "backend", "terminal", "tmux", "Terminal の automation が対象のセッションへコマンドを送る",
          ("auth", "audit", "approval"), _o("terminals/automation_worker.py", "terminals/automation.py", "terminals/manager.py"), "nexarch"),
    Route("nx-team-board", "backend", "board", "file", "タスク板の保存（teams.json・0600・置き換えで書く）", ("auth", "no_secrets"),
          _o("commander/teams.py"), "nexarch"),
    Route("nx-team-watch", "console", "board", "tmux", "作業中のタスクの担当を 60 秒ごとに見て、報告の取り込み・止まった・上限の印を板へ", ("audit",),
          _o("commander/team_watch.py"), "nexarch"),
    Route("nx-claude-usage", "console", "usage_files", "file", "Claude Code の statusline が使用率（5 時間・週）と回復の時刻だけを書く", ("no_secrets",),
          _o("commander/statusline.py", "commander/usage.py"), "nexarch", "ログインの鍵は読まない（statusline の JSON に鍵は入らない）"),
    Route("nx-codex-usage", "usage_files", "backend", "file", "Codex の sessions の記録（rollout）の最後の rate_limits を読む", ("no_secrets",),
          _o("commander/usage.py"), "nexarch", "auth.json など鍵のファイルは読まない"),
    Route("nx-accounts", "backend", "cli_accounts", "file", "CLI アカウントのフォルダを作る・消す・ログイン済みかをファイルの有無だけで判定",
          ("auth", "audit", "no_secrets"), _o("commander/accounts.py", "commander/router.py"), "nexarch",
          "中身（auth.json・.credentials.json）は読まない・返さない"),
    # ------------------------------------------------------------ external
    Route("ext-research-search", "workflows", "web", "egress", "Web・学術・特許・市場の検索、ページの取得、フォーラム・Reddit の検索",
          ("confidential", "ssrf_guard", "approval"),
          _o("workflows/research_tools.py", "workflows/external_search.py", "workflows/nodes.py", "workflows/research_keys.py"), "external",
          "ensure_external_allowed を通る。秘密の会話・オフライン調査では通信の前に止まる"),
    Route("ext-web-collector", "backend", "web", "egress", "Web 収集（取得・ブラウザ・robots・データ API）", ("confidential", "ssrf_guard"),
          _o("web_collector/egress.py", "web_collector/browser.py", "web_collector/fetch.py", "web_collector/data_api.py",
             "web_collector/robots.py"), "external",
          "出口（guarded_client）が内部アドレスを断つ。接続の直前に ensure_external_allowed"),
    Route("ext-github-research", "workflows", "github", "egress", "GitHub の調査（リポジトリ・Issue の検索）", ("confidential", "ssrf_guard"),
          _o("workflows/github_research.py"), "external"),
    Route("ext-hf-search", "models_mgmt", "hf", "egress", "Hugging Face のモデル検索", ("confidential",), _o("models_mgmt/hf_search.py"), "external"),
    Route("ext-hf-catalog-details", "models_mgmt", "hf", "egress", "Models の目録のカードを開いたときに、量子化ごとのファイルの大きさを読む（24 時間覚える）", ("confidential",), _o("models_mgmt/catalog_details.py"), "external"),
    Route("ext-hf-download", "backend", "hf", "egress",
          "モデルの取得（目録・検索からの HuggingFace の metadata 問い合わせと resolve の本体。llama.cpp・Radiance・Sly-Radiance・Strata・Maya の全 5 ランタイムが共通の取得 Job を使い、停止・再開・取り消しも同じ道）",
          ("auth", "audit", "path_check", "ssrf_guard", "confidential"),
          _o("models_mgmt/hf.py"), "external",
          "出口は guarded transport（web_collector/egress）: 名前解決→全アドレス検査→検査済み IP へ接続。"
          "redirect は自動で追わず各段を https か確認してから追う。metadata の問い合わせ・取得・"
          "再開の前に ensure_external_allowed（秘密の会話・オフライン調査では止まる）。"
          "量子化のサブフォルダは tree API で全片を列挙し、選んだ量子化だけを取得する"),
    Route("ext-news-sources", "news", "web", "egress", "ニュースの取得（RSS・ページ・Google ニュース・画像）", ("ssrf_guard",),
          _o("news/sources.py", "news/probe.py", "news/gnews.py", "news/images.py", "news/sourcemgr.py"), "external",
          "公開情報だけ。出口は guarded_client"),
    Route("ext-knowledge-sources", "knowledge", "web", "egress", "特許・論文の公開データ（EPO・USPTO・特許庁）の取り込み", ("ssrf_guard", "no_secrets"),
          _o("knowledge/sources.py"), "external", "キーは暗号化して保存。取り込んだ文書は既定で confidential"),
    Route("ext-package-updates", "backend", "pkg_registry", "egress", "pip・npm の更新の確認（公開情報を読むだけ）", ("ssrf_guard",),
          _o("features/package_updates.py"), "external"),
    Route("ext-release-update", "backend", "github", "egress",
          "ControlDeck・Strata・機能の Release の確認と取得（許可したホストだけ・署名・ハッシュの検証）", ("ssrf_guard", "audit", "approval"),
          _o("features/release_bundle.py", "maintenance/self_update.py", "maintenance/auto_update.py", "models_mgmt/strata.py"), "external",
          "本番の更新は承認待ち・Job 待ちがある"),
    Route("ext-android-sdk", "backend", "google_sdk", "egress",
          "Android SDK（コマンドライン）の取得: 利用者が設定の「導入」を押し、ライセンスへの同意にチェックを入れたときだけ。目録で固定した公式の zip（sha256 照合）と、sdkmanager が取る SDK の部品",
          ("auth", "audit", "approval", "path_check", "confidential"),
          _o("features/android_sdk.py", "features/router.py", "features/registry.py"), "external",
          "自動更新・自動取得はしない。https と host（dl.google.com）を固定し、取得の直前に ensure_external_allowed。zip は sha256 を照合し、"
          "パスの逃げ出し・リンクを拒否して展開。sdkmanager は argv（shell=True 禁止）、ライセンスの yes は同意したときだけ"),
    Route("sbx-android-sdk", "sandbox_box", "backend", "file",
          "入っている Android SDK（data/tools/android-sdk）を箱へ読み取り専用で bind し、ANDROID_HOME・ANDROID_SDK_ROOT・PATH を渡す（入っていなければ何も足さない）",
          ("path_check",), _o("features/android_sdk.py", "maintenance/sandbox.py", "integrations/opencode/provider.py", "integrations/opencode/usb.py"), "sandbox",
          "箱からは書き換えられない（読み取り専用）。Gradle のキャッシュは箱の HOME。作業場の箱は通信が中継だけなので依存の取得は通らない"),
    Route("sbx-host-adb-stop", "frontend", "host_systemd", "subprocess",
          "ホストの adb サーバーを止める（adb kill-server。利用者の権限・shell なし。機器のシートの「ホストの adb を止める」）",
          ("auth", "audit", "approval"), _o("maintenance/devices.py", "integrations/opencode/router.py"), "sandbox",
          "管理者だけ（settings.manage・監査 opencode.host_adb_stop）。止めるのは adb のサーバーだけで、機器・ほかのプロセスには触れない"),
    Route("ext-feature-install", "backend", "pkg_registry", "subprocess", "機能の導入（pip・npm をユーザー空間へ。shell=True 禁止・sudo 不要）",
          ("auth", "audit", "path_check"), _o("features/registry.py", "features/cli.py", "features/playwright_browsers.py"), "external"),
    Route("ext-news-hub-link", "backend", "news_hub", "egress", "ニュースハブとのペアリング・差分の同期・仕事の貸し出し（LAN。リダイレクトを辿らない）",
          ("auth", "no_secrets", "audit"),
          _o("news/hub_link.py", "news/hub_sync.py", "news/hub_jobs.py", "news/hub_discovery.py"), "external",
          "指紋の目視確認が済むまで同期しない。トークン・秘密鍵は暗号化して保存"),
    Route("ext-hub-expand", "knowledge", "news_hub", "egress", "検索の語の拡張（問いの文をハブの検索 API へ送り、別名・上位下位の語をもらう。特許・Web 調査・RAG。LAN・読むだけ・LLM なし）",
          ("confidential", "no_secrets"),
          _o("knowledge/hub_expand.py", "workflows/rag.py", "patents/service.py", "workflows/research_tools.py"), "external",
          "設定（news.hub.features.expand_*）で入り切り。特許は秘密の区分として問い、ハブのつなぎが許さなければ空で元の語だけ。インターネットへは何も送らない（広げた語を Web 検索に使うときの出口は既存の ensure_external_allowed）"),
    Route("ext-hub-key-seal", "backend", "news_hub", "egress", "利用者が押したときだけ、OpenRouter の鍵をハブの公開鍵で封じて渡す（ECIES）",
          ("approval", "no_secrets"), _o("news/hub_link.py"), "external",
          "秘密が外（NAS）へ出る唯一の道。封じた形だけが流れ、ログ・応答には出ない"),
    Route("ext-news-hub-llm", "news_hub", "gateway", "gateway", "ハブから借りた仕事（要約・抽出）が ControlDeck のゲートウェイ（caller=news）を使う",
          ("approval", "audit"), _o("news/hub_jobs.py"), "external",
          "ローカルのモデルなら GPU を使う。クラウドのモデルのときは外部送信の許可が無ければその種類は借りない"),
    Route("ext-git-service", "backend", "github", "subprocess", "git リポジトリの操作（gitrepos）", ("auth", "audit", "path_check"),
          _o("gitrepos/service.py"), "external"),
    # ------------------------------------------------------------ notify
    Route("notify-assistant", "assistant", "notify_core", "notify", "会話の回の終わり・質問待ち・許可待ち・失敗・中断・調査の完了",
          ("confidential", "no_secrets"), _o("alerts/chat_events.py", "workflows/chat_persist.py"), "notify",
          "秘密の会話は題も要約も載せない。本文・パス・鍵を載せない",
          kinds=("code_done", "code_question", "code_permission", "code_failed", "code_interrupted", "research_done", "chat_done")),
    Route("notify-team", "backend", "notify_core", "notify", "AI チームの出来事（質問・完了と停止・仕事が止まった・報告・引き継ぎ。種類ごとに利用者が選ぶ）。タイトルだけ", ("no_secrets",),
          _o("commander/team_watch.py"), "notify", kinds=("team_question", "team_finish", "team_blocked", "team_report", "team_handoff")),
    Route("notify-commander", "backend", "notify_core", "notify",
          "ロックしたコンソールが外から消えた・開き直した・復旧の上限で諦めたとき。タイトルだけ",
          ("no_secrets",), _o("commander/locks.py"), "notify", kinds=("console_vanish",)),
    Route("notify-workspace", "opencode", "notify_core", "notify", "作業場の関門・取り込み・マージ・ディスクの空き", ("audit",),
          _o("integrations/opencode/router.py", "workflows/chat_persist.py"), "notify",
          kinds=("workspace_gate", "workspace_adopted", "workspace_disk")),
    Route("notify-project", "workflows", "notify_core", "notify", "Project Lab の実行の終わり（起動した人へ）", ("auth",),
          _o("project_lab/runs.py"), "notify", kinds=("project_run",)),
    Route("notify-media", "addons", "notify_core", "notify", "画像・音楽・3D の生成の完了・失敗", (),
          _o("addons/execution.py"), "notify", kinds=("media_done",)),
    Route("notify-news", "news", "notify_core", "notify", "日刊・週刊のまとめ", (), _o("news/digest.py"), "notify", kinds=("news_digest",)),
    Route("notify-model", "gateway", "notify_core", "notify", "モデルの読み込みの失敗（管理者）", ("auth",),
          _o("models_mgmt/gateway.py"), "notify", kinds=("model_error",)),
    Route("notify-update", "backend", "notify_core", "notify", "本番の更新の完了・失敗・承認待ち", ("approval",),
          _o("maintenance/auto_update.py"), "notify", kinds=("update",)),
    Route("notify-alert", "backend", "notify_core", "notify", "アラートルールの発火", (), _o("alerts/engine.py"), "notify", kinds=("alert",)),
    Route("notify-patent", "knowledge", "notify_core", "notify", "特許の期限（件数だけ。案件名は載せない）", ("confidential",),
          _o("patents/watch.py"), "notify", kinds=("patent_watch",)),
    Route("notify-webpush", "notify_core", "push_service", "egress", "Web Push（RFC 8291 暗号化・VAPID）。利用者ごとの設定・お休みモードを通ってから",
          ("approval", "ssrf_guard", "no_secrets"), _o("alerts/events.py", "alerts/webpush.py"), "notify",
          "送り先は既知の push service だけ。通知の文は Apple・Google の中継を通るので短く、秘密を載せない"),
    Route("notify-channels", "backend", "chat_channels", "egress", "通知チャンネル（Discord・Slack・メール）への送信", ("approval", "no_secrets"),
          _o("alerts/notify.py"), "notify", "送り先の URL・認証は暗号化して保存"),
    # ------------------------------------------------------------ sandbox
    Route("sbx-relay-llm", "sandbox_box", "sandbox_relay", "relay",
          "箱の OpenCode が本番のゲートウェイへ届く唯一の道（models・chat・load の 3 つだけ。受けた頭は上流へ渡さない）",
          ("loopback", "approval", "confidential"), _o("maintenance/sandbox_gateway.py"), "sandbox",
          "起動の引数で機能を許す（--allow-llm・--allow-load）。許さない機能のパスは 403。cloud: は --allow-cloud のときだけ"),
    Route("sbx-relay-mcp", "sandbox_box", "mcp", "relay", "箱から呼べる ControlDeck の道具（MCP_ALLOWED_TOOLS だけ）。MCP のトークンは箱の外の中継だけが持つ",
          ("auth", "approval", "no_secrets"), _o("maintenance/sandbox_gateway.py"), "sandbox",
          "箱から来た Authorization は捨てる。更新後のトークンも箱へ返さない"),
    Route("sbx-lifecycle", "backend", "host_systemd", "subprocess", "箱（作業場）の作成・起動・停止・取り込み・マージ（systemd・podman・git）",
          ("auth", "audit", "path_check", "approval"),
          _o("maintenance/sandbox.py", "maintenance/workspaces.py", "maintenance/dev_check.py", "maintenance/change_policy.py"), "sandbox"),
    Route("sbx-usb-devices", "sandbox_box", "host_usb", "file",
          "箱へ渡す USB 機器（シリアル・adb・fastboot の --device と keep-groups。作業場と OpenCode のプロジェクトの箱）",
          ("auth", "audit", "path_check", "approval"),
          _o("maintenance/devices.py", "integrations/opencode/usb.py", "integrations/opencode/provider.py", "maintenance/sandbox.py"), "sandbox",
          "既定は渡さない（プロジェクトは off）。渡すのは /sys から解決した実体だけで、/dev 全体・--privileged・ブロックデバイスは渡さない。"
          "設定は管理者だけ（settings.manage・監査 opencode.devices）で、箱の中から変えられない（設定は data にあり、プロジェクトの外）"),
    Route("sbx-usb-muxd", "sandbox_box", "host_usb", "file", "iOS 開発用にホストの usbmuxd のソケットを箱へ bind（iPhone の機器があるとき、渡す設定のときだけ）",
          ("approval", "audit"), _o("maintenance/devices.py", "integrations/opencode/usb.py"), "sandbox",
          "usbmuxd が動いていなければ渡さず画面に案内するだけ（インストールはしない）"),
    # ------------------------------------------------------------ secrets
    Route("sec-cloud-keys", "backend", "secret_store", "file", "クラウド AI の API キー（cloud-providers.json）", ("no_secrets",),
          _o("models_mgmt/cloud.py", "security/crypto.py"), "secrets", "読むのはゲートウェイが外部モデルへ転送する瞬間だけ"),
    Route("sec-research-keys", "backend", "secret_store", "file", "調査のキー（Reddit・Stack Exchange）", ("no_secrets",),
          _o("workflows/research_keys.py"), "secrets", "読むのは調査の道具だけ"),
    Route("sec-source-keys", "backend", "secret_store", "file", "特許・論文の公開データのキー", ("no_secrets",), _o("knowledge/sources.py"), "secrets"),
    Route("sec-gateway-key", "backend", "secret_store", "file", "ゲートウェイの API キー（外部クライアント用。内部の呼び出しは発行済みの鍵を使う）",
          ("no_secrets", "auth"), _o("models_mgmt/gateway.py", "security/crypto.py"), "secrets"),
    Route("sec-hub-token", "backend", "secret_store", "file", "ニュースハブのトークンと秘密鍵（hub.json・0600）", ("no_secrets",),
          _o("news/hub_link.py"), "secrets"),
    Route("sec-vapid", "backend", "secret_store", "file", "Web Push の VAPID 秘密鍵", ("no_secrets",), _o("alerts/webpush.py"), "secrets"),
    Route("sec-sessions", "backend", "secret_store", "file", "パスワード・セッション・passkey・TOTP", ("no_secrets", "auth"),
          _o("security/passwords.py", "security/sessions.py", "auth/passkeys.py", "auth/totp.py"), "secrets"),
    Route("sec-mcp-token", "backend", "mcp_bridge", "file", "MCP の短命トークン（利用者に結びつく）を OpenCode の runtime config へ", ("no_secrets", "auth"),
          _o("addons/tokens.py", "integrations/opencode/provider.py"), "secrets"),
    Route("sec-cli-login", "cli_accounts", "console", "file", "Claude・Codex のログインの鍵は CLI が保存・読む。Nexarch（ControlDeck）は読まない", ("no_secrets",),
          _o("commander/accounts.py"), "secrets", "ログイン済みかはファイルの有無だけで判定"),
)

NOTE_SOURCES = {
    "callers": "ゲートウェイの呼び出し元（x-control-deck-client）",
    "tools": "MCP の道具",
    "kinds": "通知の種類",
}


def validate() -> list[str]:
    """地図の形の検査（データだけ。コードとの突き合わせは試験）。問題の文を返す。"""
    problems: list[str] = []
    node_ids = {n.id for n in NODES}
    seen: set[str] = set()
    for route in ROUTES:
        if route.id in seen:
            problems.append(f"id が重なっています: {route.id}")
        seen.add(route.id)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", route.id):
            problems.append(f"id は小文字の英数字とハイフンだけ: {route.id}")
        if route.channel not in CHANNELS:
            problems.append(f"{route.id}: channel が不明です: {route.channel}")
        if route.area not in AREAS:
            problems.append(f"{route.id}: area が不明です: {route.area}")
        for guard in route.guards:
            if guard not in GUARDS:
                problems.append(f"{route.id}: guards が不明です: {guard}")
        for end in (route.src, route.dst):
            if end not in node_ids:
                problems.append(f"{route.id}: ノードが無い: {end}")
    return problems


# ------------------------------------------------------------------ 文書の生成

def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _label(text: str) -> str:
    return text.replace('"', "'")


def _mermaid(area: str, nodes: dict[str, Node]) -> list[str]:
    routes = [r for r in ROUTES if r.area == area]
    used: list[str] = []
    for route in routes:
        for end in (route.src, route.dst):
            if end not in used:
                used.append(end)
    lines = ["```mermaid", "flowchart LR"]
    for node_id in used:
        lines.append(f'  {node_id}["{_label(nodes[node_id].label)}"]')
    for route in routes:
        lines.append(f'  {route.src} -->|"{route.id} ({route.channel})"| {route.dst}')
    lines.append("```")
    return lines


def render() -> str:
    nodes = {n.id: n for n in NODES}
    out = [
        "# 情報の伝達ルートの地図",
        "",
        "**このファイルは `./deck.sh route-map` が `backend/app/route_map.py` から作る。手で編集しない**（試験が内容の一致を見る）。",
        "新しく AI（モデル・CLI・道具）を足す・情報を渡す機能を作るときは、先に `backend/app/route_map.py` にルートを書き、作り直す。",
        "地図に無いルート（ゲートウェイの呼び出し元・MCP の道具・通知の種類・外部への出口・tmux への送り込み）は"
        " `backend/tests/test_route_map.py` が落とす。",
        "秘密（鍵の値）は書かない。「どこに保存されるか」だけを書く。",
        "",
        f"- ルート: {len(ROUTES)} 本、ノード: {len(NODES)} 個",
        "",
        "## 守りの印（guards）",
        "",
        "| 印 | 意味 |",
        "|---|---|",
    ]
    out += [f"| `{key}` | {_cell(text)} |" for key, text in GUARDS.items()]
    out += ["", "## 領域ごとのルート", ""]
    for area, title in AREAS.items():
        routes = [r for r in ROUTES if r.area == area]
        out += [f"### {title}（{len(routes)} 本）", ""]
        out += _mermaid(area, nodes)
        out += ["", "| id | from | to | channel | 流れるもの | guards | owner | 備考 |", "|---|---|---|---|---|---|---|---|"]
        for r in routes:
            owner = "<br>".join(f"`{p}`" for p in r.owner)
            guards = " ".join(f"`{g}`" for g in r.guards) or "**なし**"
            out.append(f"| `{r.id}` | {_cell(nodes[r.src].label)} | {_cell(nodes[r.dst].label)} | {r.channel} | {_cell(r.carries)} | {guards} | {owner} | {_cell(r.notes)} |")
        out.append("")
    out += ["## 名前の対応表（試験がコードの一覧と突き合わせる）", ""]
    for field, title in NOTE_SOURCES.items():
        out += [f"### {title}", "", "| 名前 | ルート |", "|---|---|"]
        for r in ROUTES:
            for name in getattr(r, field):
                out.append(f"| `{name}` | `{r.id}` |")
        out.append("")
    out += ["## ノード", "", "| id | 名前 |", "|---|---|"]
    out += [f"| `{n.id}` | {_cell(n.label)} |" for n in NODES]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    problems = validate()
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    DOC.write_text(render(), encoding="utf-8")
    print(f"wrote {DOC.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
