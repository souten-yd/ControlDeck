/** ワークフローノードのメタデータ（ラベル・アイコン・設定フォーム定義）。 */

export interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "app" | "code";
  options?: { value: string; label: string }[];
  placeholder?: string;
  hint?: string;
  showIf?: { key: string; value: string };
}

export interface NodeTypeDef {
  label: string;
  category: string;
  color: string;
  icon: string; // 絵文字アイコン
  fields: FieldDef[];
  branches?: boolean; // true/false の 2 出力
  loop?: boolean; // body/done の 2 出力
  desc?: string;
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
    ],
  },

  // ---- アプリ ----
  "app.start": { label: "アプリ起動", category: "アプリ", color: "#10b981", icon: "⏵", fields: [{ key: "app_id", label: "アプリ", type: "app" }] },
  "app.stop": { label: "アプリ停止", category: "アプリ", color: "#10b981", icon: "⏹", fields: [{ key: "app_id", label: "アプリ", type: "app" }] },
  "app.restart": { label: "アプリ再起動", category: "アプリ", color: "#10b981", icon: "↻", fields: [{ key: "app_id", label: "アプリ", type: "app" }] },
  "app.status": { label: "アプリ状態取得", category: "アプリ", color: "#10b981", icon: "◍", fields: [{ key: "app_id", label: "アプリ", type: "app" }] },

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
  },
  "util.wait": { label: "待機", category: "制御", color: "#f59e0b", icon: "⏱", fields: [{ key: "seconds", label: "秒数", type: "number", placeholder: "10" }] },

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
  },
  "text.markdown": {
    label: "Markdown→HTML",
    category: "データ",
    color: "#6366f1",
    icon: "M↓",
    fields: [{ key: "text", label: "Markdown", type: "textarea", hint: TEMPLATE_HINT }],
  },

  // ---- ファイル ----
  "file.read": { label: "ファイル読込", category: "ファイル", color: "#64748b", icon: "📄", fields: [{ key: "path", label: "パス", type: "text", hint: TEMPLATE_HINT }] },
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
  },
  "file.exists": { label: "ファイル存在確認", category: "ファイル", color: "#64748b", icon: "🔍", fields: [{ key: "path", label: "パス", type: "text", hint: TEMPLATE_HINT }] },

  // ---- AI ----
  "llm.chat": {
    label: "LLM 生成",
    category: "AI",
    color: "#a855f7",
    icon: "✦",
    desc: "OpenAI 互換 API（Ollama / vLLM / llama.cpp / OpenAI）",
    fields: [
      { key: "base_url", label: "エンドポイント", type: "text", placeholder: "http://127.0.0.1:11434/v1" },
      { key: "model", label: "モデル", type: "text", placeholder: "llama3" },
      { key: "api_key", label: "API キー（任意）", type: "text", placeholder: "sk-..." },
      { key: "system", label: "システムプロンプト", type: "textarea", hint: TEMPLATE_HINT },
      { key: "prompt", label: "プロンプト", type: "textarea", hint: TEMPLATE_HINT },
      { key: "temperature", label: "温度", type: "number", placeholder: "0.7" },
      { key: "max_tokens", label: "最大トークン（任意）", type: "number" },
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
  },
};

export const CATEGORY_ORDER = ["アプリ", "制御", "データ", "ファイル", "AI", "ネットワーク", "コマンド", "通知"];

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
