/** ワークフローノードのメタデータ（ラベル・アイコン・設定フォーム定義）。 */

export interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "app" | "code" | "inputs";
  options?: { value: string; label: string }[];
  placeholder?: string;
  hint?: string;
  showIf?: { key: string; value: string };
}

export interface OutputDef {
  key: string;
  label: string;
}

export interface NodeTypeDef {
  label: string;
  category: string;
  color: string;
  icon: string; // 絵文字アイコン
  fields: FieldDef[];
  outputs?: OutputDef[]; // 出力変数（変数ピッカーに表示）
  branches?: boolean; // true/false の 2 出力
  loop?: boolean; // body/done の 2 出力
  desc?: string;
}

/** トリガーの入力フィールド定義（Dify の User Input 相当） */
export interface TriggerInputDef {
  key: string;
  label?: string;
  type: "text" | "paragraph" | "number" | "select" | "file";
  required?: boolean;
  options?: string; // select 用（改行区切り）
}

const TEMPLATE_HINT = "{{ノードID.フィールド}} で前段の出力を参照できます";

export const NODE_TYPES: Record<string, NodeTypeDef> = {
  trigger: {
    label: "トリガー",
    category: "トリガー",
    color: "#8b5cf6",
    icon: "▶",
    fields: [
      {
        key: "mode",
        label: "起動方法",
        type: "select",
        options: [
          { value: "manual", label: "手動のみ" },
          { value: "interval", label: "一定間隔" },
          { value: "daily", label: "毎日指定時刻" },
          { value: "cron", label: "Cron 式" },
        ],
      },
      { key: "interval_minutes", label: "間隔（分）", type: "number", placeholder: "60", showIf: { key: "mode", value: "interval" } },
      { key: "time", label: "時刻 (HH:MM)", type: "text", placeholder: "08:00", showIf: { key: "mode", value: "daily" } },
      { key: "cron", label: "Cron 式", type: "text", placeholder: "0 8 * * *", showIf: { key: "mode", value: "cron" } },
      { key: "inputs", label: "入力フィールド", type: "inputs", hint: "実行時に入力を求め、{{トリガーID.変数名}} で全後段ノードから参照できます" },
    ],
    outputs: [{ key: "message", label: "チャット入力" }],
  },

  // ---- アプリ ----
  "app.start": { label: "アプリ起動", category: "アプリ", color: "#10b981", icon: "⏵", fields: [{ key: "app_id", label: "アプリ", type: "app" }], outputs: [{ key: "app", label: "アプリ名" }, { key: "status", label: "状態" }] },
  "app.stop": { label: "アプリ停止", category: "アプリ", color: "#10b981", icon: "⏹", fields: [{ key: "app_id", label: "アプリ", type: "app" }], outputs: [{ key: "app", label: "アプリ名" }, { key: "status", label: "状態" }] },
  "app.restart": { label: "アプリ再起動", category: "アプリ", color: "#10b981", icon: "↻", fields: [{ key: "app_id", label: "アプリ", type: "app" }], outputs: [{ key: "app", label: "アプリ名" }, { key: "status", label: "状態" }] },
  "app.status": { label: "アプリ状態取得", category: "アプリ", color: "#10b981", icon: "◍", fields: [{ key: "app_id", label: "アプリ", type: "app" }], outputs: [{ key: "app", label: "アプリ名" }, { key: "status", label: "状態" }, { key: "pid", label: "PID" }, { key: "uptime_seconds", label: "稼働秒数" }] },

  // ---- 制御 ----
  "condition.if": {
    label: "条件分岐",
    category: "制御",
    color: "#f59e0b",
    icon: "◆",
    branches: true,
    fields: [
      { key: "left", label: "左辺", type: "text", placeholder: "{{n1.status_code}}", hint: TEMPLATE_HINT },
      {
        key: "op", label: "演算子", type: "select",
        options: [
          { value: "eq", label: "= 等しい" }, { value: "ne", label: "≠ 等しくない" },
          { value: "gt", label: "> より大きい" }, { value: "gte", label: "≥ 以上" },
          { value: "lt", label: "< より小さい" }, { value: "lte", label: "≤ 以下" },
          { value: "contains", label: "を含む" },
        ],
      },
      { key: "right", label: "右辺", type: "text", placeholder: "200" },
    ],
    outputs: [{ key: "result", label: "判定結果" }, { key: "left", label: "左辺値" }, { key: "right", label: "右辺値" }],
  },
  "control.loop": {
    label: "ループ",
    category: "制御",
    color: "#f59e0b",
    icon: "⟳",
    loop: true,
    desc: "body から繰り返し、完了後 done へ。{{このID.item}} / {{このID.index}} を参照可",
    fields: [
      {
        key: "mode", label: "種類", type: "select",
        options: [
          { value: "count", label: "回数指定" },
          { value: "foreach", label: "リスト each（JSON 配列 or 改行区切り）" },
        ],
      },
      { key: "count", label: "回数", type: "number", placeholder: "5", showIf: { key: "mode", value: "count" } },
      { key: "items", label: "リスト", type: "textarea", placeholder: '["a","b"] または改行区切り', showIf: { key: "mode", value: "foreach" }, hint: TEMPLATE_HINT },
    ],
    outputs: [{ key: "item", label: "現在の要素" }, { key: "index", label: "インデックス" }, { key: "total", label: "総数" }],
  },
  "util.wait": { label: "待機", category: "制御", color: "#f59e0b", icon: "⏱", fields: [{ key: "seconds", label: "秒数", type: "number", placeholder: "10" }], outputs: [{ key: "waited_seconds", label: "待機秒数" }] },

  // ---- 変数・文字列・テキスト ----
  "var.set": {
    label: "変数セット",
    category: "データ",
    color: "#6366f1",
    icon: "𝑥",
    fields: [
      { key: "name", label: "変数名", type: "text", placeholder: "result" },
      { key: "value", label: "値", type: "textarea", hint: TEMPLATE_HINT },
    ],
    outputs: [{ key: "value", label: "値" }],
  },
  "string.op": {
    label: "文字列操作",
    category: "データ",
    color: "#6366f1",
    icon: "✎",
    fields: [
      {
        key: "op", label: "操作", type: "select",
        options: [
          { value: "template", label: "テンプレート展開" }, { value: "upper", label: "大文字" },
          { value: "lower", label: "小文字" }, { value: "trim", label: "空白除去" },
          { value: "replace", label: "置換" }, { value: "regex_extract", label: "正規表現抽出" },
          { value: "split", label: "分割" }, { value: "length", label: "文字数" },
          { value: "json_extract", label: "JSON 抽出" },
        ],
      },
      { key: "text", label: "対象テキスト", type: "textarea", hint: TEMPLATE_HINT },
      { key: "find", label: "検索文字列", type: "text", showIf: { key: "op", value: "replace" } },
      { key: "replace", label: "置換後", type: "text", showIf: { key: "op", value: "replace" } },
      { key: "pattern", label: "正規表現", type: "text", showIf: { key: "op", value: "regex_extract" } },
      { key: "sep", label: "区切り文字", type: "text", placeholder: ",", showIf: { key: "op", value: "split" } },
      { key: "path", label: "JSON パス（a.b.0）", type: "text", showIf: { key: "op", value: "json_extract" } },
    ],
    outputs: [{ key: "result", label: "結果" }, { key: "text", label: "元テキスト" }],
  },
  "text.markdown": {
    label: "Markdown→HTML",
    category: "データ",
    color: "#6366f1",
    icon: "M↓",
    fields: [{ key: "text", label: "Markdown", type: "textarea", hint: TEMPLATE_HINT }],
    outputs: [{ key: "html", label: "HTML" }, { key: "markdown", label: "Markdown" }],
  },
  "db.query": {
    label: "DB クエリ",
    category: "データ",
    color: "#6366f1",
    icon: "🗄",
    desc: "SQLite / PostgreSQL などへ SQL 実行",
    fields: [
      { key: "engine", label: "接続方法", type: "select", options: [{ value: "sqlite", label: "SQLite ファイル" }, { value: "url", label: "接続 URL" }] },
      { key: "path", label: "SQLite パス", type: "text", hint: "許可ルート配下", showIf: { key: "engine", value: "sqlite" } },
      { key: "url", label: "接続 URL", type: "text", placeholder: "postgresql+psycopg://user:pass@host/db", showIf: { key: "engine", value: "url" } },
      { key: "query", label: "SQL", type: "code", placeholder: "SELECT * FROM t WHERE id = :id", hint: TEMPLATE_HINT },
      { key: "params", label: "パラメータ（JSON）", type: "textarea", placeholder: '{"id": 1}' },
    ],
    outputs: [{ key: "rows", label: "行データ" }, { key: "row_count", label: "行数" }, { key: "columns", label: "カラム名" }],
  },

  // ---- ファイル ----
  "file.read": { label: "ファイル読込", category: "ファイル", color: "#64748b", icon: "📄", fields: [{ key: "path", label: "パス", type: "text", hint: TEMPLATE_HINT }], outputs: [{ key: "content", label: "内容" }, { key: "path", label: "パス" }] },
  "file.write": {
    label: "ファイル出力",
    category: "ファイル",
    color: "#64748b",
    icon: "💾",
    fields: [
      { key: "path", label: "パス", type: "text", hint: TEMPLATE_HINT },
      { key: "content", label: "内容", type: "textarea", hint: TEMPLATE_HINT },
      { key: "append", label: "追記モード", type: "select", options: [{ value: "", label: "上書き" }, { value: "1", label: "追記" }] },
    ],
    outputs: [{ key: "path", label: "パス" }, { key: "bytes", label: "バイト数" }],
  },
  "file.op": {
    label: "ファイル操作",
    category: "ファイル",
    color: "#64748b",
    icon: "🗂",
    fields: [
      { key: "op", label: "操作", type: "select", options: [{ value: "copy", label: "コピー" }, { value: "move", label: "移動" }, { value: "delete", label: "削除" }, { value: "mkdir", label: "フォルダ作成" }] },
      { key: "source", label: "対象パス", type: "text", hint: TEMPLATE_HINT },
      { key: "dest_dir", label: "移動/コピー先", type: "text", showIf: { key: "op", value: "copy" } },
    ],
    outputs: [{ key: "path", label: "結果パス" }],
  },
  "file.exists": { label: "ファイル存在確認", category: "ファイル", color: "#64748b", icon: "🔍", fields: [{ key: "path", label: "パス", type: "text", hint: TEMPLATE_HINT }], outputs: [{ key: "exists", label: "存在するか" }, { key: "size", label: "サイズ" }] },

  // ---- AI ----
  "llm.chat": {
    label: "LLM 生成",
    category: "AI",
    color: "#a855f7",
    icon: "✦",
    desc: "OpenAI 互換 API（Ollama / vLLM / llama.cpp / OpenAI）。稼働中サーバーの検出可",
    fields: [
      { key: "base_url", label: "エンドポイント", type: "text", placeholder: "http://127.0.0.1:11434/v1" },
      { key: "model", label: "モデル", type: "text", placeholder: "llama3" },
      { key: "api_key", label: "API キー（任意）", type: "text", placeholder: "sk-..." },
      { key: "system", label: "システムプロンプト", type: "textarea", hint: TEMPLATE_HINT },
      { key: "prompt", label: "プロンプト", type: "textarea", hint: TEMPLATE_HINT },
      {
        key: "response_format", label: "出力形式", type: "select",
        options: [
          { value: "", label: "テキスト" },
          { value: "json_object", label: "JSON（形式自由）" },
          { value: "json_schema", label: "JSON スキーマ指定（構造化出力）" },
        ],
      },
      { key: "json_schema", label: "JSON スキーマ", type: "code", showIf: { key: "response_format", value: "json_schema" }, hint: "下のプリセットから雛形を挿入できます。結果は {{ID.json.フィールド}} で参照" },
      { key: "temperature", label: "温度", type: "number", placeholder: "0.7" },
      { key: "max_tokens", label: "最大トークン（任意）", type: "number" },
    ],
    outputs: [
      { key: "content", label: "応答テキスト" },
      { key: "json", label: "構造化出力(JSON)" },
      { key: "model", label: "モデル名" },
      { key: "tokens", label: "使用トークン" },
    ],
  },
  "media.ocr": {
    label: "OCR",
    category: "AI",
    color: "#a855f7",
    icon: "👁",
    desc: "画像から文字認識（tesseract）",
    fields: [
      { key: "path", label: "画像パス", type: "text", hint: TEMPLATE_HINT },
      { key: "lang", label: "言語", type: "text", placeholder: "jpn+eng" },
    ],
    outputs: [{ key: "text", label: "認識テキスト" }, { key: "chars", label: "文字数" }],
  },
  "rag.build": {
    label: "RAG 構築",
    category: "AI",
    color: "#a855f7",
    icon: "📚",
    desc: "テキストを埋め込んでコレクションへ登録",
    fields: [
      { key: "collection", label: "コレクション名", type: "text", placeholder: "docs" },
      { key: "text", label: "テキスト", type: "textarea", hint: `${TEMPLATE_HINT}（空ならパスから読込）` },
      { key: "path", label: "またはファイルパス", type: "text", hint: TEMPLATE_HINT },
      { key: "base_url", label: "埋め込みエンドポイント", type: "text", placeholder: "http://127.0.0.1:11434/v1" },
      { key: "embed_model", label: "埋め込みモデル", type: "text", placeholder: "nomic-embed-text" },
      { key: "api_key", label: "API キー（任意）", type: "text" },
      { key: "reset", label: "既存を消去", type: "select", options: [{ value: "", label: "追記" }, { value: "1", label: "作り直す" }] },
    ],
    outputs: [{ key: "collection", label: "コレクション" }, { key: "added_chunks", label: "追加チャンク数" }, { key: "total_chunks", label: "総チャンク数" }],
  },
  "rag.query": {
    label: "RAG 検索",
    category: "AI",
    color: "#a855f7",
    icon: "🔎",
    desc: "コレクションから関連文脈を取得（{{ID.context}} を LLM へ）",
    fields: [
      { key: "collection", label: "コレクション名", type: "text", placeholder: "docs" },
      { key: "question", label: "質問", type: "textarea", hint: TEMPLATE_HINT },
      { key: "top_k", label: "取得件数", type: "number", placeholder: "4" },
      { key: "base_url", label: "埋め込みエンドポイント", type: "text", placeholder: "http://127.0.0.1:11434/v1" },
      { key: "embed_model", label: "埋め込みモデル", type: "text", placeholder: "nomic-embed-text" },
      { key: "api_key", label: "API キー（任意）", type: "text" },
    ],
    outputs: [{ key: "context", label: "関連文脈" }, { key: "matches", label: "マッチ一覧" }, { key: "count", label: "件数" }],
  },

  // ---- ユーティリティ ----
  "util.now": {
    label: "現在日時",
    category: "データ",
    color: "#6366f1",
    icon: "🕐",
    desc: "現在日時を取得（strftime 書式）",
    fields: [{ key: "format", label: "書式", type: "text", placeholder: "%Y-%m-%d %H:%M:%S" }],
    outputs: [
      { key: "text", label: "整形日時" }, { key: "iso", label: "ISO8601" },
      { key: "date", label: "日付" }, { key: "time", label: "時刻" },
      { key: "timestamp", label: "UNIX秒" }, { key: "weekday", label: "曜日" },
    ],
  },

  // ---- ネットワーク ----
  "http.request": {
    label: "HTTP リクエスト",
    category: "ネットワーク",
    color: "#0ea5e9",
    icon: "🌐",
    fields: [
      { key: "method", label: "メソッド", type: "select", options: ["GET", "POST", "PUT", "DELETE", "HEAD"].map((m) => ({ value: m, label: m })) },
      { key: "url", label: "URL", type: "text", placeholder: "http://127.0.0.1:8000/health", hint: TEMPLATE_HINT },
      { key: "expect_status", label: "期待ステータス（任意）", type: "number", placeholder: "200" },
      { key: "body", label: "ボディ（任意）", type: "textarea", hint: TEMPLATE_HINT },
    ],
    outputs: [{ key: "status_code", label: "ステータスコード" }, { key: "ok", label: "成功か" }, { key: "body", label: "レスポンス本文" }],
  },
  "web.scrape": {
    label: "Web スクレイピング",
    category: "ネットワーク",
    color: "#0ea5e9",
    icon: "🕸",
    fields: [
      { key: "url", label: "URL", type: "text", hint: TEMPLATE_HINT },
      { key: "selector", label: "CSS セレクター（空=全文）", type: "text", placeholder: "h1.title, .price" },
      { key: "attribute", label: "属性（空=テキスト）", type: "text", placeholder: "href" },
    ],
    outputs: [{ key: "first", label: "最初の一致" }, { key: "results", label: "一致リスト" }, { key: "count", label: "件数" }, { key: "text", label: "全文" }],
  },
  "web.browser": {
    label: "ブラウザ操作",
    category: "ネットワーク",
    color: "#0ea5e9",
    icon: "🖥",
    desc: "Playwright（ヘッドレス Chromium）",
    fields: [
      { key: "url", label: "URL", type: "text", hint: TEMPLATE_HINT },
      { key: "action", label: "動作", type: "select", options: [{ value: "content", label: "HTML 取得" }, { value: "text", label: "要素テキスト" }, { value: "screenshot", label: "スクリーンショット" }] },
      { key: "selector", label: "セレクター", type: "text", showIf: { key: "action", value: "text" } },
      { key: "output_path", label: "保存先", type: "text", showIf: { key: "action", value: "screenshot" } },
    ],
    outputs: [{ key: "title", label: "ページタイトル" }, { key: "text", label: "要素テキスト" }, { key: "content", label: "HTML" }, { key: "screenshot", label: "スクショパス" }],
  },
  "net.wol": {
    label: "Wake-on-LAN",
    category: "ネットワーク",
    color: "#0ea5e9",
    icon: "⏻",
    fields: [
      { key: "mac", label: "MAC アドレス", type: "text", placeholder: "AA:BB:CC:DD:EE:FF" },
      { key: "broadcast", label: "ブロードキャスト", type: "text", placeholder: "255.255.255.255" },
    ],
    outputs: [{ key: "sent", label: "送信したか" }],
  },
  "http.download": {
    label: "ファイルダウンロード",
    category: "ネットワーク",
    color: "#0ea5e9",
    icon: "⬇",
    desc: "URL の内容をファイルへ保存（上限 500MB）",
    fields: [
      { key: "url", label: "URL", type: "text", hint: TEMPLATE_HINT },
      { key: "path", label: "保存先パス", type: "text", hint: "許可ルート配下" },
    ],
    outputs: [{ key: "path", label: "保存パス" }, { key: "bytes", label: "サイズ" }],
  },

  // ---- コマンド ----
  "cmd.ssh": {
    label: "SSH 実行",
    category: "コマンド",
    color: "#0891b2",
    icon: "⌘",
    desc: "鍵認証・非対話（BatchMode）",
    fields: [
      { key: "host", label: "ホスト", type: "text", placeholder: "server.local", hint: TEMPLATE_HINT },
      { key: "user", label: "ユーザー", type: "text", placeholder: "ubuntu" },
      { key: "port", label: "ポート", type: "number", placeholder: "22" },
      { key: "command", label: "コマンド", type: "textarea", hint: TEMPLATE_HINT },
    ],
    outputs: [{ key: "stdout", label: "標準出力" }, { key: "stderr", label: "標準エラー" }, { key: "exit_code", label: "終了コード" }, { key: "ok", label: "成功か" }],
  },
  "cmd.git": {
    label: "Git 操作",
    category: "コマンド",
    color: "#0891b2",
    icon: "⎇",
    fields: [
      {
        key: "subcommand", label: "サブコマンド", type: "select",
        options: ["status", "pull", "push", "fetch", "add", "commit", "checkout", "clone", "log", "diff", "branch", "merge", "reset", "stash", "tag", "remote", "rev-parse"].map((s) => ({ value: s, label: s })),
      },
      { key: "args", label: "引数", type: "text", placeholder: "-m \"message\"", hint: TEMPLATE_HINT },
      { key: "cwd", label: "作業ディレクトリ", type: "text", hint: "許可ルート配下のみ" },
    ],
    outputs: [{ key: "stdout", label: "標準出力" }, { key: "stderr", label: "標準エラー" }, { key: "exit_code", label: "終了コード" }, { key: "ok", label: "成功か" }],
  },
  "cmd.cpp_build": {
    label: "C++ ビルド",
    category: "コマンド",
    color: "#0891b2",
    icon: "⚙",
    fields: [
      { key: "system", label: "ビルドシステム", type: "select", options: [{ value: "cmake", label: "CMake" }, { value: "make", label: "Make" }] },
      { key: "cwd", label: "プロジェクトディレクトリ", type: "text", hint: "許可ルート配下のみ" },
      { key: "build_dir", label: "ビルドディレクトリ", type: "text", placeholder: "build", showIf: { key: "system", value: "cmake" } },
      { key: "cmake_args", label: "CMake 引数", type: "text", showIf: { key: "system", value: "cmake" } },
      { key: "make_args", label: "Make 引数", type: "text", showIf: { key: "system", value: "make" } },
    ],
    outputs: [{ key: "stdout", label: "標準出力" }, { key: "stderr", label: "標準エラー" }, { key: "exit_code", label: "終了コード" }, { key: "ok", label: "成功か" }],
  },
  "cmd.python": {
    label: "Python 実行",
    category: "コマンド",
    color: "#0891b2",
    icon: "🐍",
    desc: "初期無効（security.allow_arbitrary_commands で許可）",
    fields: [
      { key: "code", label: "コード", type: "code", placeholder: "print('hello')" },
      { key: "stdin", label: "標準入力（任意）", type: "textarea", hint: TEMPLATE_HINT },
      { key: "cwd", label: "作業ディレクトリ", type: "text", hint: "許可ルート配下のみ" },
    ],
    outputs: [{ key: "stdout", label: "標準出力" }, { key: "stderr", label: "標準エラー" }, { key: "exit_code", label: "終了コード" }, { key: "ok", label: "成功か" }],
  },

  // ---- チャット ----
  "signal.display": {
    label: "信号表示",
    category: "チャット",
    color: "#14b8a6",
    icon: "📡",
    desc: "入力データを右側のチャットウィンドウに表示する",
    fields: [
      {
        key: "signal", label: "記録する信号", type: "select",
        options: [
          { value: "reply", label: "返答 (reply)" },
          { value: "output", label: "出力 (output)" },
          { value: "status", label: "状態 (status)" },
          { value: "log", label: "ログ (log)" },
          { value: "chart", label: "チャート (chart)" },
        ],
      },
      { key: "value", label: "表示する値", type: "textarea", hint: `${TEMPLATE_HINT}（例: {{llm.content}}）` },
    ],
    outputs: [{ key: "value", label: "表示値" }],
  },

  // ---- 通知 ----
  "notify.webhook": {
    label: "Webhook 通知",
    category: "通知",
    color: "#ec4899",
    icon: "🔔",
    fields: [
      { key: "url", label: "Webhook URL", type: "text", placeholder: "https://discord.com/api/webhooks/..." },
      { key: "format", label: "形式", type: "select", options: [{ value: "generic", label: "汎用 JSON" }, { value: "discord", label: "Discord" }, { value: "slack", label: "Slack" }] },
      { key: "message", label: "メッセージ", type: "textarea", hint: TEMPLATE_HINT },
    ],
    outputs: [{ key: "status_code", label: "ステータスコード" }, { key: "ok", label: "成功か" }],
  },
};

/** LLM 構造化出力のスキーマプリセット */
export const JSON_SCHEMA_PRESETS: { label: string; schema: object }[] = [
  {
    label: "情報抽出",
    schema: { type: "object", properties: { title: { type: "string", description: "タイトル" }, summary: { type: "string", description: "要約" }, tags: { type: "array", items: { type: "string" }, description: "タグ" } }, required: ["title", "summary"] },
  },
  {
    label: "分類",
    schema: { type: "object", properties: { category: { type: "string", enum: ["A", "B", "C"], description: "分類カテゴリ" }, confidence: { type: "number", description: "確信度 0-1" }, reason: { type: "string", description: "理由" } }, required: ["category"] },
  },
  {
    label: "リスト生成",
    schema: { type: "object", properties: { items: { type: "array", items: { type: "object", properties: { name: { type: "string" }, description: { type: "string" } }, required: ["name"] } } }, required: ["items"] },
  },
  {
    label: "評価/スコア",
    schema: { type: "object", properties: { score: { type: "integer", minimum: 0, maximum: 100 }, strengths: { type: "array", items: { type: "string" } }, weaknesses: { type: "array", items: { type: "string" } } }, required: ["score"] },
  },
];

export const CATEGORY_ORDER = ["チャット", "アプリ", "制御", "データ", "ファイル", "AI", "ネットワーク", "コマンド", "通知"];

export const DEFAULT_DEFINITION = {
  nodes: [
    { id: "trigger", type: "trigger", name: "トリガー", config: { mode: "manual" }, position: { x: 80, y: 160 } },
  ],
  edges: [],
};

let counter = 0;
export function newNodeId(): string {
  counter += 1;
  return `n${Date.now().toString(36)}${counter}`;
}

// ---- カスタムノード / スニペット（localStorage 保存） ----

export interface Snippet {
  id: string;
  name: string;
  // 部分グラフ（複数ノード + 内部エッジ）
  nodes: Array<{ id: string; type: string; name?: string; config?: Record<string, unknown>; position?: { x: number; y: number } }>;
  edges: Array<{ source: string; target: string; branch?: string | null }>;
  createdAt: number;
}

const SNIPPET_KEY = "cd-workflow-snippets";

export function loadSnippets(): Snippet[] {
  try {
    return JSON.parse(localStorage.getItem(SNIPPET_KEY) || "[]");
  } catch {
    return [];
  }
}

export function saveSnippet(snippet: Snippet): void {
  const list = loadSnippets().filter((s) => s.id !== snippet.id);
  list.push(snippet);
  localStorage.setItem(SNIPPET_KEY, JSON.stringify(list));
}

export function deleteSnippet(id: string): void {
  localStorage.setItem(SNIPPET_KEY, JSON.stringify(loadSnippets().filter((s) => s.id !== id)));
}
