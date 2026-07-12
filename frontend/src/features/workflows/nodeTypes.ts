/** ワークフローノードのメタデータ（ラベル・設定フォーム定義）。 */

export interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "app";
  options?: { value: string; label: string }[];
  placeholder?: string;
  hint?: string;
  showIf?: { key: string; value: string };
}

export interface NodeTypeDef {
  label: string;
  category: string;
  color: string;
  fields: FieldDef[];
  branches?: boolean; // true/false の 2 出力を持つ
}

export const NODE_TYPES: Record<string, NodeTypeDef> = {
  trigger: {
    label: "トリガー",
    category: "トリガー",
    color: "#8b5cf6",
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
  "app.start": {
    label: "アプリ起動",
    category: "アプリ",
    color: "#10b981",
    fields: [{ key: "app_id", label: "アプリ", type: "app" }],
  },
  "app.stop": {
    label: "アプリ停止",
    category: "アプリ",
    color: "#10b981",
    fields: [{ key: "app_id", label: "アプリ", type: "app" }],
  },
  "app.restart": {
    label: "アプリ再起動",
    category: "アプリ",
    color: "#10b981",
    fields: [{ key: "app_id", label: "アプリ", type: "app" }],
  },
  "app.status": {
    label: "アプリ状態取得",
    category: "アプリ",
    color: "#10b981",
    fields: [{ key: "app_id", label: "アプリ", type: "app" }],
  },
  "http.request": {
    label: "HTTP リクエスト",
    category: "ネットワーク",
    color: "#0ea5e9",
    fields: [
      {
        key: "method",
        label: "メソッド",
        type: "select",
        options: ["GET", "POST", "PUT", "DELETE", "HEAD"].map((m) => ({ value: m, label: m })),
      },
      { key: "url", label: "URL", type: "text", placeholder: "http://127.0.0.1:8000/health" },
      { key: "expect_status", label: "期待ステータス（任意）", type: "number", placeholder: "200" },
      { key: "body", label: "ボディ（任意）", type: "textarea", hint: "{{ノードID.フィールド}} で前段の出力を参照可能" },
    ],
  },
  "condition.if": {
    label: "条件分岐",
    category: "制御",
    color: "#f59e0b",
    branches: true,
    fields: [
      { key: "left", label: "左辺", type: "text", placeholder: "{{n1.status_code}}" },
      {
        key: "op",
        label: "演算子",
        type: "select",
        options: [
          { value: "eq", label: "= 等しい" },
          { value: "ne", label: "≠ 等しくない" },
          { value: "gt", label: "> より大きい" },
          { value: "gte", label: "≥ 以上" },
          { value: "lt", label: "< より小さい" },
          { value: "lte", label: "≤ 以下" },
          { value: "contains", label: "を含む" },
        ],
      },
      { key: "right", label: "右辺", type: "text", placeholder: "200" },
    ],
  },
  "util.wait": {
    label: "待機",
    category: "制御",
    color: "#f59e0b",
    fields: [{ key: "seconds", label: "秒数", type: "number", placeholder: "10" }],
  },
  "notify.webhook": {
    label: "Webhook 通知",
    category: "通知",
    color: "#ec4899",
    fields: [
      { key: "url", label: "Webhook URL", type: "text", placeholder: "https://discord.com/api/webhooks/..." },
      {
        key: "format",
        label: "形式",
        type: "select",
        options: [
          { value: "generic", label: "汎用 JSON" },
          { value: "discord", label: "Discord" },
          { value: "slack", label: "Slack" },
        ],
      },
      { key: "message", label: "メッセージ", type: "textarea", hint: "{{ノードID.フィールド}} 参照可" },
    ],
  },
  "file.exists": {
    label: "ファイル存在確認",
    category: "ファイル",
    color: "#64748b",
    fields: [{ key: "path", label: "パス", type: "text", placeholder: "/data/flag.txt" }],
  },
};

export const DEFAULT_DEFINITION = {
  nodes: [
    {
      id: "trigger",
      type: "trigger",
      name: "トリガー",
      config: { mode: "manual" },
      position: { x: 80, y: 120 },
    },
  ],
  edges: [],
};

let counter = 0;
export function newNodeId(): string {
  counter += 1;
  return `n${Date.now().toString(36)}${counter}`;
}
