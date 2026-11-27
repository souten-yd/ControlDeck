/** AssistantChat の外の補助（型・保存・アップロード）。発言の部品（ChatMessage）と本体の両方から使う。 */
import type { AssistantMode as Mode } from "./assistantMode";
import type { CodeChange } from "./OpenCodeChanges";
import type { OpenCodeTodo } from "./TodoFloat";
import type { OpenCodePermission, OpenCodeTool, PermissionMode } from "./OpenCodeActivity";

/** 進み具合の取れるアップロード（fetch では送信の進捗が取れない）。 */
export function uploadWithProgress(url: string, form: FormData, onProgress: (ratio: number) => void): Promise<{ ok: boolean; status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "ControlDeck");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.upload.onload = () => onProgress(1);
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, body });
    };
    xhr.timeout = 10 * 60_000;
    xhr.onerror = () => reject(new Error("アップロードに失敗しました（接続が切れました）"));
    xhr.onabort = () => reject(new Error("アップロードを中断しました"));
    xhr.ontimeout = () => reject(new Error("アップロードが時間切れになりました"));
    xhr.send(form);
  });
}

/** 添付した画像を返す URL（送った発言の中に出す）。 */
export const attachmentUrl = (convId: string, id: string) =>
  `/api/v1/chat/conversations/${encodeURIComponent(convId)}/attachments/${encodeURIComponent(id)}`;

export interface SourceItem { reference_id?: string; title: string; url: string; snippet?: string; source?: string; kind?: string }
export interface Quality { score: number; label: string; breakdown: Record<string, number>; errors: string[]; warnings: string[] }
export interface GenData { name: string; definition: { nodes: { id: string; type: string; name?: string }[]; edges: unknown[] }; valid: boolean; warnings: string[]; goal: string; quality?: Quality }
export interface BuildState { lines: string[]; status: string; workflowId?: number; done: boolean; quality?: Quality }
export interface ResearchStep { tool: "web" | "academic"; query: string }
export interface AssistantPlan { mode: "chat" | "web" | "academic" | "deep" | "research"; reason: string; steps: ResearchStep[]; max_iterations: number; decided_by: "rule" | "llm" | "fallback" | "tools" }
export interface ResearchProgress { phase: string; label: string; iteration: number; details?: Record<string, unknown> }
export interface DeepResearchResult {
  rounds: number;
  search_calls: number;
  sources_discovered: number;
  sources_selected: number;
  repositories_inspected: number;
  coverage?: { coverage_score?: number; gaps?: string[]; contradictions?: string[] };
  citation_metrics?: { citation_coverage?: number; cited_sources?: number; report_chars?: number; revised?: boolean; section_count?: number; completed_sections?: number; possibly_truncated_sections?: string[] };
  coverage_limits?: string[];
  context_profile?: { enabled?: boolean; requested_tokens?: number; applied?: boolean; runtime?: string; reason?: string };
}

/** 届いた順の並び。t = 思考（thinking の from〜to 文字）、x = 本文（content の from〜to）、u = 道具（id） */
export type TimelineSeg = { k: "t" | "x"; from: number; to: number } | { k: "u"; id: string } | { k: "n"; text: string };

/** 思考・本文の断片を並びの最後へ足す（同じ種類が続けば伸ばす）。 */
export function extendTimeline(timeline: TimelineSeg[] | undefined, k: "t" | "x", start: number, length: number): TimelineSeg[] {
  const list = timeline ?? [];
  const last = list[list.length - 1];
  if (last && last.k === k) return [...list.slice(0, -1), { ...last, to: start + length }];
  return [...list, { k, from: start, to: start + length }];
}

export interface PersistMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  status: string;
  job_id: string | null;
  model: string;
  /** 失敗・中断の理由（成功した回答では空）。 */
  error?: string;
  meta?: { changes?: CodeChange[]; reverted?: boolean; resumed?: boolean; mode?: string; mode_choice?: string; model_choice?: string; code_model?: string; model_note?: string; research_depth?: string; agent_choice?: string; agent?: "build" | "plan"; project_path?: string; timeline?: TimelineSeg[]; auto_handoff?: { task: string }; handoff?: { messages: number; chars: number }; archives?: { id: string; name: string }[];
    imports?: { name: string; path: string; files: number }[]; sources?: SourceItem[]; plan?: AssistantPlan; progress?: ResearchProgress[]; research?: DeepResearchResult; compaction?: CompactionNote; attachments?: string[];
    tools?: OpenCodeTool[]; todos?: OpenCodeTodo[]; permissions?: OpenCodePermission[]; permission_mode?: PermissionMode; confidential?: boolean; checkpoint?: { sha: string; count: number; workspace: string };
    media?: ChatMedia;
    memory?: { count: number; ids: number[] }; memory_created?: { created: number; pending: number } };
}

export interface ChatMedia { workspace: string; items: { path: string; kind: "image" | "video"; size: number }[] }

export interface Msg {
  role: "user" | "assistant";
  content: string;
  /** 送信直後、本文が届くまでだけ出す仮の文（「🔎 検索中...」）。本文（content）には入れない（読み上げ・コピー・保存の対象にしないため） */
  placeholder?: string;
  /** OpenCode がこの回で変えたファイル */
  changes?: CodeChange[];
  reverted?: boolean;
  /** 中断・止めた・失敗した OpenCode の回を「続きから」で再開し終えた（ボタンを二重に出さない） */
  resumed?: boolean;
  /** 自動保存の時点（「この時点に戻す」の戻し先） */
  checkpoint?: { sha: string; count: number; workspace: string };
  /** この回に撮った画面・動画（作業場の画面の記録） */
  media?: ChatMedia;
  /** 送った画像（表示用の URL） */
  images?: string[];
  /** 送った圧縮ファイルの名前（OpenCode のプロジェクトへ展開した） */
  archives?: string[];
  /** OpenCode: 道具の実行と許可の問い合わせ */
  tools?: OpenCodeTool[];
  todos?: OpenCodeTodo[];
  /** 回答した機能（"code" = OpenCode）。保存した発言から */
  answeredBy?: string;
  /** オフライン調査（外部へ調査しない）で答えた。外部の検索・取得はしていない */
  confidentialAnswer?: boolean;
  /** 答えに使った記憶（件数と ID。本文は持たない）と、この回で作った記憶 */
  memoryUsed?: { count: number; ids: number[] };
  memoryCreated?: { created: number; pending: number };
  /** OpenCode のエージェント（plan は計画だけ。何も変えない） */
  agent?: "build" | "plan";
  /** 自動の会話から、LLM の判断で OpenCode に任せた作業（2026-11-20 利用者「自動でも適切に OpenCode へ。すぐ移る」） */
  autoHandoff?: string;
  /** OpenCode: 届いた順の並び（思考・本文は文字の位置、道具は ID）。思考を道具の前後で分けて出す */
  timeline?: TimelineSeg[];
  /** OpenCode: この回答が動いたプロジェクト（解決したパス） */
  projectPath?: string;
  permissions?: OpenCodePermission[];
  /** 生成中のジョブ（停止に使う） */
  jobId?: string | null;
  kind?: "text" | "sources" | "gen" | "build" | "run";
  sources?: SourceItem[];
  gen?: GenData;
  build?: BuildState;
  thinking?: string;
  streaming?: boolean;
  // 永続チャット（DB 会話）: assistant メッセージの ID と状態
  messageId?: string;
  persistStatus?: string; // generating / completed / failed / interrupted / canceled
  /** 失敗・中断の理由（サーバーが記録したもの）。 */
  error?: string;
  connectionState?: "live" | "reconnecting";
  plan?: AssistantPlan;
  progress?: ResearchProgress[];
  research?: DeepResearchResult;
  /** この回答の前に古いやり取りを自動で要約した（会話の自動圧縮） */
  compaction?: CompactionNote;
}

export interface CompactionNote { tokens_before?: number; tokens_after?: number; threshold?: number; folded_messages?: number }

/** 回答が最後まで出なかった終わり方。ジョブの状態（succeeded など）と発言の状態の両方を受ける。 */
export interface GenStats {
  phase: string; tokPerSec: number; genTokens: number;
  promptTokens: number | null; contextMax: number | null; detail?: string;
}

export function settledStatus(status: unknown): string {
  if (status === "failed" || status === "canceled" || status === "interrupted") return status;
  if (status === "cancelled") return "canceled";
  return "completed";
}

/** DB の発言を画面の形へ。 */
export function restoreMessage(convId: string, m: PersistMsg): Msg {
  return {
    role: m.role,
    content: m.content,
    images: m.meta?.attachments?.map((a) => attachmentUrl(convId, a)),
    archives: m.meta?.archives?.map((a) => a.name),
    tools: m.meta?.tools,
    todos: m.meta?.todos,
    changes: m.meta?.changes,
    reverted: m.meta?.reverted,
    resumed: m.meta?.resumed,
    checkpoint: m.meta?.checkpoint,
    media: m.meta?.media,
    permissions: m.meta?.permissions,
    answeredBy: m.meta?.mode,
    confidentialAnswer: m.meta?.confidential,
    memoryUsed: m.meta?.memory,
    memoryCreated: m.meta?.memory_created,
    agent: m.meta?.agent,
    autoHandoff: m.meta?.auto_handoff?.task,
    timeline: m.meta?.timeline,
    projectPath: m.meta?.project_path,
    jobId: m.job_id,
    thinking: m.thinking || undefined,
    messageId: m.id,
    persistStatus: m.status,
    error: m.error || undefined,
    streaming: m.status === "generating",
    sources: m.meta?.sources,
    kind: m.meta?.sources?.length ? "sources" : "text",
    plan: m.meta?.plan,
    progress: m.meta?.progress,
    research: m.meta?.research,
    compaction: m.meta?.compaction,
  };
}

export const LS_KEY = "cd-assistant-settings";
export const LS_HISTORY = "cd-assistant-history"; // PC の履歴の列を畳んだか（"min"）

export function readHistoryCollapsed(): boolean {
  try {
    return localStorage.getItem(LS_HISTORY) === "min";
  } catch {
    return false;
  }
}

export function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

/** hint は空の会話に出す説明、short は機能メニューの一行、tile は＋メニューのタイルの短い名前（省略なし 2026-11-22）。 */
export const MODES: { id: Mode; icon: string; label: string; hint: string; short: string; tile?: string; needsEdit?: boolean }[] = [
  { id: "chat", icon: "💬", label: "Chat", short: "LLM と自由に対話", hint: "LLM と自由に対話します" },
  { id: "web", icon: "🌐", label: "Web Search", short: "Web を検索して根拠つきで回答", hint: "Web 検索（DuckDuckGo / SearXNG）の結果を根拠に回答します" },
  { id: "academic", icon: "🎓", label: "Academic", short: "論文・学術ソースを検索して回答", hint: "学術ソース串刺し検索（OpenAlex/arXiv 等）を根拠に回答します" },
  { id: "deep", icon: "🔬", label: "Deep Research", short: "分解して調べ、引用付きレポート（数分）", hint: "テーマを分解して収集し、引用付きレポートを生成します（数分かかります）" },
  { id: "research", icon: "🧭", label: "Research", short: "Web と学術を組み合わせて調査", hint: "LLMがWeb・学術検索を組み合わせ、不足を評価しながら要約します" },
  { id: "gen", icon: "⚙️", label: "Generate Flow", tile: "Gen Flow", short: "ワークフローを自動で作って確認", hint: "やりたいことを書くと、ワークフローを自動生成 → 登録 → 動作確認 → 修正まで行います", needsEdit: true },
  { id: "run", icon: "▶", label: "Run Flow", short: "既存のワークフローを実行", hint: "既存のワークフローをチャットから実行し、結果を表示します" },
  { id: "code", icon: "⌨️", label: "OpenCode", short: "コーディングエージェントが作業", hint: "コーディングエージェント（OpenCode）がこのチャット内で作業し、進捗と結果を表示します（同じ会話で継続対話。フル操作はOpenCode画面から）" },
];
