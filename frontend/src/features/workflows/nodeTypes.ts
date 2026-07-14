/** ワークフローノードのメタデータ（ラベル・アイコン・設定フォーム定義）。 */

export interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "app" | "code" | "inputs" | "extractors" | "workflow";
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

/** Web スクレイピングの抽出項目（各項目が出力変数になる） */
export interface ExtractorDef {
  name: string;
  selector: string;
  attribute: string; // text | html | href | src | ...
  multiple: boolean;
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
          { value: "webhook", label: "Webhook（外部から POST）" },
          { value: "event", label: "イベント（アラート発火時）" },
        ],
      },
      { key: "interval_minutes", label: "間隔（分）", type: "number", placeholder: "60", showIf: { key: "mode", value: "interval" } },
      { key: "time", label: "時刻 (HH:MM)", type: "text", placeholder: "08:00", showIf: { key: "mode", value: "daily" } },
      { key: "cron", label: "Cron 式", type: "text", placeholder: "0 8 * * *", showIf: { key: "mode", value: "cron" } },
      { key: "webhook_token", label: "Webhook トークン", type: "text", showIf: { key: "mode", value: "webhook" }, hint: "POST /api/v1/hooks/{トークン} で起動（16 文字以上・スケジュール有効化が必要）。ボディの JSON が {{トリガーID.キー}} になります" },
      { key: "rule_filter", label: "アラート名フィルタ（任意・部分一致）", type: "text", showIf: { key: "mode", value: "event" }, hint: "空なら全アラートで起動。{{トリガーID.rule}} / {{トリガーID.value}} を参照可（スケジュール有効化が必要）" },
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
      {
        key: "think", label: "思考 think（推論モデル・Ollama直結時）", type: "select",
        options: [
          { value: "", label: "モデル設定に従う" },
          { value: "off", label: "オフ（思考なし・高速）" },
          { value: "on", label: "オン" },
          { value: "low", label: "低" },
          { value: "medium", label: "中" },
          { value: "high", label: "高" },
        ],
        hint: "思考をオフにすると高速化。構造化出力(JSON)使用時は無効",
      },
      {
        key: "agent_tools", label: "エージェントモード", type: "select",
        options: [
          { value: "", label: "無効（通常の 1 回生成）" },
          { value: "1", label: "有効 — ツールを自律的に使う" },
        ],
        hint: "LLM が Web検索/学術検索/RAG検索/HTTP GET/ファイル読込 を必要に応じて反復実行します（tool calling 対応モデルが必要）",
      },
      { key: "agent_max_steps", label: "最大ツールラウンド数", type: "number", placeholder: "6", showIf: { key: "agent_tools", value: "1" } },
    ],
    outputs: [
      { key: "content", label: "応答テキスト" },
      { key: "json", label: "構造化出力(JSON)" },
      { key: "thinking", label: "思考トレース(推論モデル)" },
      { key: "model", label: "モデル名" },
      { key: "tokens", label: "使用トークン" },
      { key: "tool_log", label: "ツール実行ログ(エージェント)" },
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
    desc: "テキストを埋め込んでナレッジへ登録（チャンク戦略を選択可）",
    fields: [
      { key: "collection", label: "コレクション名", type: "text", placeholder: "docs" },
      { key: "text", label: "テキスト", type: "textarea", hint: `${TEMPLATE_HINT}（空ならパスから読込）` },
      { key: "path", label: "またはファイルパス", type: "text", hint: TEMPLATE_HINT },
      { key: "source", label: "出典名（任意）", type: "text", hint: TEMPLATE_HINT },
      {
        key: "strategy", label: "チャンク戦略", type: "select",
        options: [
          { value: "", label: "コレクション設定に従う" },
          { value: "recursive", label: "再帰分割（汎用）" },
          { value: "fixed", label: "固定長" },
          { value: "sentence", label: "文単位" },
          { value: "paragraph", label: "段落単位" },
          { value: "markdown", label: "Markdown 見出し" },
          { value: "parent_child", label: "親子（子で検索し親を文脈に）" },
        ],
      },
      { key: "size", label: "チャンク文字数", type: "number", placeholder: "800" },
      { key: "overlap", label: "オーバーラップ", type: "number", placeholder: "100" },
      { key: "parent_mode", label: "親モード", type: "select", showIf: { key: "strategy", value: "parent_child" }, options: [{ value: "paragraph", label: "段落" }, { value: "full_doc", label: "文書全体" }] },
      { key: "base_url", label: "埋め込みエンドポイント", type: "text", placeholder: "http://127.0.0.1:11434/v1" },
      { key: "embed_model", label: "埋め込みモデル", type: "text", placeholder: "nomic-embed-text" },
      { key: "api_key", label: "API キー（任意）", type: "text" },
      { key: "reset", label: "既存を消去", type: "select", options: [{ value: "", label: "追記" }, { value: "1", label: "作り直す" }] },
    ],
    outputs: [{ key: "collection", label: "コレクション" }, { key: "doc_id", label: "文書ID" }, { key: "added_chunks", label: "追加チャンク数" }, { key: "total_chunks", label: "総チャンク数" }],
  },
  "rag.query": {
    label: "RAG 検索",
    category: "AI",
    color: "#a855f7",
    icon: "🔎",
    desc: "ナレッジから関連文脈を取得（{{ID.context}} を LLM へ）。検索方式を選択可",
    fields: [
      { key: "collection", label: "コレクション名", type: "text", placeholder: "docs" },
      { key: "question", label: "質問", type: "textarea", hint: TEMPLATE_HINT },
      { key: "top_k", label: "取得件数", type: "number", placeholder: "4" },
      {
        key: "search_mode", label: "検索方式", type: "select",
        options: [
          { value: "", label: "コレクション設定に従う" },
          { value: "hybrid", label: "ハイブリッド（推奨）" },
          { value: "vector", label: "ベクトルのみ" },
          { value: "fulltext", label: "全文（キーワード）のみ" },
          { value: "graph", label: "グラフ拡張（GraphRAG）" },
        ],
      },
      { key: "hyde", label: "HyDE（仮想文書で精度向上）", type: "select", options: [{ value: "", label: "無効" }, { value: "1", label: "有効（LLM使用）" }] },
      { key: "multi_query", label: "マルチクエリ数（RAG-Fusion）", type: "number", placeholder: "0" },
      { key: "llm_base_url", label: "LLM エンドポイント（HyDE/MQ用）", type: "text", placeholder: "http://127.0.0.1:11434/v1", showIf: { key: "hyde", value: "1" } },
      { key: "llm_model", label: "LLM モデル（HyDE/MQ用）", type: "text", placeholder: "llama3.2", showIf: { key: "hyde", value: "1" } },
      { key: "api_key", label: "API キー（任意）", type: "text" },
    ],
    outputs: [{ key: "context", label: "関連文脈" }, { key: "matches", label: "マッチ一覧" }, { key: "facts", label: "グラフ事実" }, { key: "count", label: "件数" }, { key: "mode", label: "使用した方式" }],
  },
  "academic.search": {
    label: "外部検索",
    category: "AI",
    color: "#a855f7",
    icon: "🎓",
    desc: "論文/文献/特許/市場を検索。「串刺し」で複数学術ソースを並列検索。RAG取り込みや要約へ",
    fields: [
      {
        key: "source", label: "ソース", type: "select",
        options: [
          { value: "all", label: "串刺し（全学術ソース並列）" },
          { value: "openalex", label: "OpenAlex（全分野・大規模）" },
          { value: "arxiv", label: "arXiv（論文プレプリント）" },
          { value: "crossref", label: "Crossref（文献/DOI）" },
          { value: "semanticscholar", label: "Semantic Scholar" },
          { value: "europepmc", label: "Europe PMC（生医学）" },
          { value: "doaj", label: "DOAJ（オープンアクセス誌）" },
          { value: "dblp", label: "DBLP（計算機科学）" },
          { value: "patent", label: "特許（PatentsView・要APIキー）" },
          { value: "market", label: "市場調査（SEC EDGAR 企業開示）" },
        ],
      },
      { key: "query", label: "検索クエリ", type: "text", hint: TEMPLATE_HINT },
      { key: "max_results", label: "件数", type: "number", placeholder: "10" },
      { key: "api_key", label: "API キー（特許のみ・無料）", type: "text", showIf: { key: "source", value: "patent" }, hint: "data.uspto.gov で取得" },
    ],
    outputs: [{ key: "results", label: "結果一覧" }, { key: "text", label: "まとめテキスト" }, { key: "count", label: "件数" }],
  },
  "web.search": {
    label: "Web 検索",
    category: "ネットワーク",
    color: "#0ea5e9",
    icon: "🔍",
    desc: "Web 検索の結果(タイトル/URL/スニペット)。URL を Web スクレイピングへ繋いで本文取得",
    fields: [
      { key: "query", label: "検索クエリ", type: "text", hint: TEMPLATE_HINT },
      { key: "engine", label: "エンジン", type: "select", options: [{ value: "duckduckgo", label: "DuckDuckGo（キー不要）" }, { value: "searxng", label: "SearXNG（自前/公開）" }] },
      { key: "searxng_url", label: "SearXNG URL", type: "text", placeholder: "http://127.0.0.1:8888", showIf: { key: "engine", value: "searxng" }, hint: "空ならローカル既定（./deck.sh searxng で導入・停止中は自動起動）" },
      { key: "categories", label: "カテゴリ（任意）", type: "text", placeholder: "general, science, news", showIf: { key: "engine", value: "searxng" } },
      { key: "max_results", label: "件数", type: "number", placeholder: "8" },
    ],
    outputs: [{ key: "results", label: "結果一覧" }, { key: "urls", label: "URL 配列" }, { key: "first_url", label: "先頭URL" }, { key: "text", label: "まとめ" }, { key: "count", label: "件数" }],
  },
  "research.deep": {
    label: "Deep Research",
    category: "AI",
    color: "#a855f7",
    icon: "🧠",
    desc: "テーマを分解し複数ソースを反復探索して引用付きレポートを生成（手軽な一括実行）",
    fields: [
      { key: "topic", label: "調査テーマ", type: "textarea", hint: TEMPLATE_HINT },
      { key: "sources", label: "ソース（カンマ区切り）", type: "text", placeholder: "rag,web,arxiv,market", hint: "rag / web / arxiv / crossref / patent / market" },
      { key: "collection", label: "RAG コレクション（rag 使用時）", type: "text", placeholder: "docs" },
      { key: "sub_questions", label: "サブ質問数", type: "number", placeholder: "4" },
      { key: "results_per_source", label: "ソース毎の件数", type: "number", placeholder: "4" },
      { key: "web_engine", label: "Web エンジン", type: "select", options: [{ value: "duckduckgo", label: "DuckDuckGo" }, { value: "searxng", label: "SearXNG" }] },
      { key: "searxng_url", label: "SearXNG URL", type: "text", showIf: { key: "web_engine", value: "searxng" }, hint: "空ならローカル既定" },
      { key: "llm_base_url", label: "LLM エンドポイント", type: "text", placeholder: "http://127.0.0.1:11434/v1" },
      { key: "llm_model", label: "LLM モデル", type: "text", placeholder: "llama3.2" },
      { key: "api_key", label: "API キー（任意）", type: "text" },
    ],
    outputs: [{ key: "report", label: "レポート" }, { key: "findings", label: "収集資料" }, { key: "sub_questions", label: "サブ質問" }, { key: "count", label: "資料数" }],
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
    desc: "抽出ビューワでページから要素をクリック選択してセレクタを自動生成。複数出力対応",
    fields: [
      { key: "url", label: "URL", type: "text", hint: TEMPLATE_HINT },
      { key: "extractors", label: "抽出項目", type: "extractors", hint: "「抽出ビューワを開く」でページから選択、または手動で追加。各項目が出力変数になります" },
    ],
    outputs: [{ key: "status_code", label: "ステータスコード" }],
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

  // ---- 制御（サブフロー） ----
  "flow.call": {
    label: "サブフロー呼び出し",
    category: "制御",
    color: "#f59e0b",
    icon: "🧩",
    desc: "別のワークフローを実行して結果を受け取る。共通処理の部品化に",
    fields: [
      { key: "workflow_id", label: "呼び出すワークフロー", type: "workflow" },
      { key: "message", label: "メッセージ入力（{{trigger.message}} へ）", type: "textarea", hint: TEMPLATE_HINT },
      { key: "input_json", label: "追加入力（JSON・任意）", type: "code", placeholder: '{"topic": "{{n1.content}}"}', hint: "トリガー入力フィールドへ渡す値" },
      { key: "timeout", label: "待ち時間上限（秒）", type: "number", placeholder: "600" },
    ],
    outputs: [
      { key: "result", label: "結果（信号表示の連結）" },
      { key: "execution_id", label: "実行 ID" },
      { key: "status", label: "実行ステータス" },
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

/**
 * ノードリファレンス用の詳細ドキュメント（Markdown ライクなプレーンテキスト）。
 * サンプルブックの「ノードリファレンス」タブで NODE_TYPES とマージして表示する。
 */
export const NODE_DOCS: Record<string, string> = {
  trigger:
    "すべてのワークフローの起点。1 フローに必ず 1 つ置きます。\n\n■ 起動方法\n- 手動のみ: 実行ボタン/チャットから起動\n- 一定間隔・毎日・Cron: 一覧で「スケジュール有効化」すると自動実行\n\n■ 入力フィールド\n実行時にユーザーへ入力を求めるフォームを定義できます（Dify の User Input 相当）。入力値は {{トリガーID.キー}} で全ノードから参照できます。チャットから実行した場合、本文は {{トリガーID.message}} に入ります。",
  "signal.display":
    "値をチャットウィンドウへ表示する出力ノード。フローの「返答」を作る役割です。\n\n■ 使い方\n表示する値に {{llm.content}} など前段の出力を指定。信号種別（reply/output/status/log/chart)で表示スタイルが変わります。\n\n■ 組み合わせ\nLLM 生成 → 信号表示 が最小のチャットボット構成。AI アシスタントの自動生成フローでも最終ノードとして使われます。",
  "app.start": "Apps ページに登録した管理対象アプリを起動します。\n\n■ 組み合わせ\nWake-on-LAN → 待機 → アプリ起動、条件分岐（停止中なら）→ 起動 など。",
  "app.stop": "管理対象アプリを停止します。メンテナンス時間帯の自動停止などに。",
  "app.restart": "管理対象アプリを再起動します。\n\n■ 組み合わせ\nアプリ状態取得 → 条件分岐（running でない）→ 再起動 → Webhook 通知 で自己修復フローになります（サンプル「アプリ死活監視・自動復旧」参照）。",
  "app.status":
    "アプリの状態（running/stopped 等）・PID・稼働秒数を取得します。\n\n■ 出力\n{{ID.status}} を条件分岐に渡すのが定番。{{ID.uptime_seconds}} で「起動直後は無視」などの制御も可能。",
  "condition.if":
    "左辺と右辺を比較し、true / false の 2 方向へ分岐します。\n\n■ 使い方\n左辺にテンプレート（例 {{n1.status_code}}）、演算子と右辺を設定。エッジは右側の緑ハンドル（true）と赤ハンドル（false)から引きます。\n\n■ ヒント\n数値比較は自動で数値化されます。「を含む」は部分文字列判定でキーワード監視に便利。",
  "control.loop":
    "body 側のノード列を繰り返し実行し、完了後に done 側へ進みます。\n\n■ モード\n- 回数指定: {{ID.index}} が 0..n-1\n- リスト each: JSON 配列 or 改行区切りを 1 件ずつ {{ID.item}} に\n\n■ 組み合わせ\nWeb 検索の {{search.urls}} を items に渡して URL ごとにスクレイピング、など。上限 100 回。",
  "util.wait": "指定秒数待機します。Wake-on-LAN 後の起動待ち、API のレート制限対策などに。最大 1 時間。",
  "var.set":
    "値に名前を付けて保存します。output_var と違い、フロー途中で明示的に変数を作る用途。\n\n■ 参照\n{{vars.変数名}} でどのノードからでも参照できます。設定パネルの変数ピッカーにも表示されます。",
  "string.op":
    "テキスト加工の万能ノード。\n\n■ 主な操作\n- テンプレート展開: 複数出力の合成に\n- 置換 / 正規表現抽出 / 分割\n- JSON 抽出: LLM の JSON 応答から a.b.0 パスで値を取り出す\n\n■ 組み合わせ\nLLM 生成（JSON モード）→ 文字列操作(json_extract) → 条件分岐 が構造化パイプラインの定石。",
  "text.markdown": "Markdown を HTML に変換します。レポートをメール/Web 表示用に整形する時に。",
  "db.query":
    "SQLite ファイルまたは接続 URL（PostgreSQL 等）へ SQL を実行します。\n\n■ 使い方\nSELECT は {{ID.rows}} に行データ（JSON）が入ります。パラメータは :name 形式 + JSON で安全にバインド。\n\n■ 組み合わせ\nDB クエリ → LLM 生成（要約/分析）→ 通知 で日次レポートが作れます。",
  "file.read": "テキストファイルを読み込み {{ID.content}} に格納。許可ルート配下のみアクセス可能です。",
  "file.write": "テキストをファイルへ書き込み（上書き/追記）。レポート保存やログ蓄積に。許可ルート配下のみ。",
  "file.op": "コピー/移動/削除/フォルダ作成。ダウンロード後の整理などに。",
  "file.exists": "ファイルの存在とサイズを確認。条件分岐と組み合わせて「初回だけ実行」を作れます。",
  "llm.chat":
    "OpenAI 互換 API（Ollama / vLLM / llama.cpp / OpenAI）でテキスト生成する中核ノード。\n\n■ 使い方\nエンドポイント/モデルは設定パネルで稼働中サーバーを自動検出できます。プロンプトに {{前段.出力}} を埋め込んで使います。\n\n■ 構造化出力\n出力形式を JSON スキーマ指定にすると {{ID.json.フィールド}} で値を直接参照でき、条件分岐や DB 保存と繋げやすくなります。\n\n■ 組み合わせ\nRAG 検索 → LLM（根拠付き回答）、Web 検索 → LLM（ダイジェスト）、DB → LLM（分析）。",
  "media.ocr": "画像から文字を認識（tesseract）。スクリーンショット → OCR → LLM 整形 のような紙情報のデジタル化に。",
  "rag.build":
    "テキスト/ファイルをチャンク分割・埋め込みしてナレッジ（コレクション）へ登録します。\n\n■ チャンク戦略\n- recursive: 汎用（迷ったらこれ）\n- markdown: 見出し構造を保持\n- parent_child: 子チャンクで検索し親を文脈に（長文に強い）\n\n■ 組み合わせ\nWeb スクレイピング → RAG 構築 で記事の取り込み（サンプル「Web 記事をナレッジへ取り込み」）。Knowledge ページでも管理できます。",
  "rag.query":
    "ナレッジから関連文脈を検索します。RAG の検索段。\n\n■ 検索方式\n- hybrid: ベクトル+全文の融合（推奨）\n- vector / fulltext: 単独方式\n- graph: GraphRAG。知識グラフからエンティティ関係（{{ID.facts}}）も取得し、関係を問う質問に強い\n\n■ 精度向上\nHyDE（仮想文書）とマルチクエリ（RAG-Fusion）を有効にすると曖昧な質問への再現率が上がります（LLM 使用）。\n\n■ 組み合わせ\n{{ID.context}}（+ graph 時は {{ID.facts}}）を LLM 生成のプロンプトに渡して根拠付き回答を作ります。",
  "academic.search":
    "論文・文献・特許・市場情報の外部検索。\n\n■ ソース\n「串刺し」は OpenAlex / Crossref / arXiv / Europe PMC / DBLP / DOAJ を並列検索し、タイトルで重複統合・被引用数順に並べます。個別ソース指定も可能。特許（PatentsView）のみ無料 API キーが必要。\n\n■ 出力\n{{ID.text}} は LLM に渡しやすい整形済みテキスト、{{ID.results}} は構造化データ。\n\n■ 組み合わせ\n串刺し検索 → LLM 要約 → Webhook 通知 で論文ウォッチ（サンプルあり）。RAG 構築へ繋げば文献ナレッジも作れます。",
  "web.search":
    "Web 検索の結果（タイトル/URL/スニペット）を取得します。\n\n■ エンジン\n- DuckDuckGo: キー不要ですぐ使える\n- SearXNG: ./deck.sh searxng で直接導入済みなら URL 空欄でローカルインスタンス（127.0.0.1:8888）を使用。ControlDeck と同時に起動/停止し、停止中でも検索時に自動起動。カテゴリ絞り込み可\n\n■ 組み合わせ\n{{ID.urls}} をループ → Web スクレイピングで本文収集、{{ID.text}} を LLM でダイジェスト化。",
  "research.deep":
    "Deep Research を 1 ノードで実行する上位ノード。テーマをサブ質問に分解 → 複数ソース（rag/web/arxiv/crossref/patent/market）を反復探索 → 引用付き統合レポートを生成します。\n\n■ 使い方\n数分かかるため、結果は file.write で保存 + signal.display で表示が定番。sources に rag を含めると自分のナレッジも探索します。\n\n■ 使い分け\n単発の検索は web.search / academic.search、深掘り調査レポートはこのノード。",
  "util.now": "現在日時を strftime 書式で取得。ファイル名（report_{{now.date}}.md）や通知メッセージに。",
  "http.request":
    "任意の HTTP API を呼び出します。\n\n■ 使い方\nGET でヘルスチェック、POST + JSON ボディで API 連携。期待ステータスを設定すると不一致で失敗扱いになり、条件分岐なしで異常検知できます。",
  "web.scrape":
    "ページから要素を抽出します。\n\n■ 抽出ビューワ\n「抽出ビューワを開く」でページを表示し、要素をクリックするだけでセレクタを自動生成。各抽出項目がそのまま出力変数になります。\n\n■ ヒント\nJavaScript 描画が必要なページはブラウザ操作ノード（Playwright）を使ってください。",
  "web.browser": "ヘッドレス Chromium（Playwright）でページを開き、HTML 取得・要素テキスト・スクリーンショットを実行。JS レンダリングが必要なサイトはこちら。",
  "net.wol": "Wake-on-LAN のマジックパケットを送信。待機 → SSH 実行 / アプリ起動と繋げてリモート PC の自動起動フローに。",
  "http.download": "URL の内容をファイルへ保存（上限 500MB）。モデル/データセットの定期取得などに。",
  "cmd.ssh": "リモートホストでコマンドを実行（鍵認証・非対話）。~/.ssh の鍵設定が前提。{{ID.stdout}} を LLM や通知へ渡せます。",
  "cmd.git": "clone/pull/commit など Git 操作。許可ルート配下のリポジトリのみ。定期 pull → ビルド → 通知の CI 風フローに。",
  "cmd.cpp_build": "CMake / Make でビルドを実行。Git 操作と組み合わせて更新→ビルド→結果通知を自動化。",
  "cmd.python": "Python コードを実行します（セキュリティ設定 security.allow_arbitrary_commands の許可が必要）。標準ノードで足りない加工処理の最終手段。",
  "notify.webhook":
    "Discord / Slack / 汎用 JSON の Webhook へ通知します。\n\n■ 使い方\n形式を選んで URL を設定、メッセージにテンプレートで結果を埋め込みます。フローの「完了報告」や条件分岐の true 側の警報として最後に置くのが定番。",
  "flow.call":
    "別のワークフローをサブフローとして実行し、完了を待って結果を受け取ります。\n\n■ 使い方\n呼び出し先を選び、メッセージ（相手の {{trigger.message}} になる）や追加入力を渡します。相手側の「信号表示」ノードの値が {{ID.result}} として返ります。\n\n■ 組み合わせ\n「要約」「通知」などの共通処理を 1 つのワークフローにして部品化し、複数のフローから呼び出せます。ネストは 3 段まで。",
};

/**
 * 全ノード共通の実行制御設定（エディタの「実行制御」セクションで編集）。
 * - retry_count / retry_wait: 失敗時の自動リトライ
 * - on_error: stop(全体停止) / continue(無視して続行) / branch(error ハンドルへ分岐)
 * - require_approval: 実行前に承認を要求（情報パネルから承認/却下）
 * - join: "all" で全入力エッジの完了を待つ合流ノードになる
 */
export const COMMON_CONTROL_KEYS = ["retry_count", "retry_wait", "on_error", "require_approval", "join"] as const;

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
