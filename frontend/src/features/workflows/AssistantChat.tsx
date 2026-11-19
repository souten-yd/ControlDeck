/** AI アシスタント — チャット / Web・学術検索 / Deep サーチ / ワークフロー生成・実行。
 *
 * - チャット: ローカル LLM（OpenAI 互換）とストリーミング対話
 * - Web検索/学術検索: 検索結果を根拠に回答（出典表示、SearXNG 対応）
 * - Deepサーチ: サブ質問分解→収集→引用付きレポート
 * - フロー生成: 目的→定義生成→登録→動作確認→自動修正（ビルドログ表示）
 * - フロー実行: 既存ワークフローをチャットから実行し signal.display を表示
 */
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, wsUrl } from "../../api/client";
import { useMeta } from "../../api/hooks";
import { useAuth, useToasts } from "../../stores";
import { IconChip, IconMic, IconSearch, IconSend, IconStop, IconTerminal } from "../../components/icons";
import { SHELL_TOPBAR_CLASS, ShellMenuButton, useShell } from "../../layouts/shell";
import { useKeyboardViewportFit } from "../../hooks/useKeyboardViewportFit";
import { FilePicker } from "../../components/FilePicker";
import { NODE_TYPES } from "./nodeTypes";
import { detectAssistantMode, resolveConversationPrefs, SEARCH_MODES, updateConversationPrefs, validChoice, validDepth, wantsResearch, type AssistantMode as Mode, type AssistantModeChoice, type ConversationPrefs, type ResearchDepth } from "./assistantMode";
import { useAssistantAsr } from "./useAssistantAsr";
import { useAssistantTts } from "./useAssistantTts";
import { TodoFloat, type OpenCodeTodo } from "./TodoFloat";
import { QueuedRequests, VoiceWave, type QueuedItem } from "./QueuedRequests";
import { OpenCodeChanges, type CodeChange } from "./OpenCodeChanges";
import { MessageActions, MessageEditor, type MessageAction } from "./MessageActions";
import { reportClientTiming } from "../../lib/clientTiming";
import { ConfirmDialog } from "../../components/ui";
import { writeClipboard } from "../../lib/clipboard";
import {
  ConversationHistory, HistoryCloseButton, HistoryHeader, HistorySheet, IconHistoryPanel, TitleEditor,
  type ConversationSummary,
} from "./ConversationHistory";
import { ComposerPill, PlusMenu, type PlusAction } from "./PlusMenu";

/** 進み具合の取れるアップロード（fetch では送信の進捗が取れない）。 */
function uploadWithProgress(url: string, form: FormData, onProgress: (ratio: number) => void): Promise<{ ok: boolean; status: number; body: unknown }> {
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
import {
  OpenCodeActivity, PERMISSION_MODES,
  CODE_STEPS_CARD, ToolRow,
  type OpenCodePermission, type OpenCodeTool, type PermissionMode,
} from "./OpenCodeActivity";

// Markdown（表・見出し・コード）と TeX の式。ライブラリは回答を描くときに初めて読む。
const MarkdownMessage = lazy(() => import("./MarkdownMessage"));

/** 添付した画像を返す URL（送った発言の中に出す）。 */
const attachmentUrl = (convId: string, id: string) =>
  `/api/v1/chat/conversations/${encodeURIComponent(convId)}/attachments/${encodeURIComponent(id)}`;

/** 本文が無く画像だけを送るときの発言。 */
const IMAGE_ONLY_PROMPT = "添付した画像を見てください。";
const ARCHIVE_ONLY_PROMPT = "添付した圧縮ファイルの中身を確認して、構成と内容を説明してください。";
/** 圧縮ファイルの名前（サーバーの archive_import.SUFFIXES と同じ） */
const ARCHIVE_RE = /\.(zip|7z|rar|tar|tgz|tbz2|txz|tar\.gz|tar\.bz2|tar\.xz)$/i;
const ARCHIVE_MAX_BYTES = 200 * 1024 * 1024;

/** hint は空の会話に出す説明、short は機能メニューの一行。 */
const MODES: { id: Mode; icon: string; label: string; hint: string; short: string; needsEdit?: boolean }[] = [
  { id: "chat", icon: "💬", label: "Chat", short: "LLM と自由に対話", hint: "LLM と自由に対話します" },
  { id: "web", icon: "🌐", label: "Web Search", short: "Web を検索して根拠つきで回答", hint: "Web 検索（DuckDuckGo / SearXNG）の結果を根拠に回答します" },
  { id: "academic", icon: "🎓", label: "Academic", short: "論文・学術ソースを検索して回答", hint: "学術ソース串刺し検索（OpenAlex/arXiv 等）を根拠に回答します" },
  { id: "deep", icon: "🔬", label: "Deep Research", short: "分解して調べ、引用付きレポート（数分）", hint: "テーマを分解して収集し、引用付きレポートを生成します（数分かかります）" },
  { id: "research", icon: "🧭", label: "Research", short: "Web と学術を組み合わせて調査", hint: "LLMがWeb・学術検索を組み合わせ、不足を評価しながら要約します" },
  { id: "gen", icon: "⚙️", label: "Generate Flow", short: "ワークフローを自動で作って確認", hint: "やりたいことを書くと、ワークフローを自動生成 → 登録 → 動作確認 → 修正まで行います", needsEdit: true },
  { id: "run", icon: "▶", label: "Run Flow", short: "既存のワークフローを実行", hint: "既存のワークフローをチャットから実行し、結果を表示します" },
  { id: "code", icon: "⌨️", label: "OpenCode", short: "コーディングエージェントが作業", hint: "コーディングエージェント（OpenCode）がこのチャット内で作業し、進捗と結果を表示します（同じ会話で継続対話。フル操作はOpenCode画面から）" },
];

interface SourceItem { reference_id?: string; title: string; url: string; snippet?: string; source?: string; kind?: string }
interface Quality { score: number; label: string; breakdown: Record<string, number>; errors: string[]; warnings: string[] }
interface GenData { name: string; definition: { nodes: { id: string; type: string; name?: string }[]; edges: unknown[] }; valid: boolean; warnings: string[]; goal: string; quality?: Quality }
interface BuildState { lines: string[]; status: string; workflowId?: number; done: boolean; quality?: Quality }
interface ResearchStep { tool: "web" | "academic"; query: string }
interface AssistantPlan { mode: "chat" | "web" | "academic" | "deep" | "research"; reason: string; steps: ResearchStep[]; max_iterations: number; decided_by: "rule" | "llm" | "fallback" }
interface ResearchProgress { phase: string; label: string; iteration: number; details?: Record<string, unknown> }
interface DeepResearchResult {
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
type TimelineSeg = { k: "t" | "x"; from: number; to: number } | { k: "u"; id: string } | { k: "n"; text: string };

/** 思考・本文の断片を並びの最後へ足す（同じ種類が続けば伸ばす）。 */
function extendTimeline(timeline: TimelineSeg[] | undefined, k: "t" | "x", start: number, length: number): TimelineSeg[] {
  const list = timeline ?? [];
  const last = list[list.length - 1];
  if (last && last.k === k) return [...list.slice(0, -1), { ...last, to: start + length }];
  return [...list, { k, from: start, to: start + length }];
}

interface PersistMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  status: string;
  job_id: string | null;
  model: string;
  /** 失敗・中断の理由（成功した回答では空）。 */
  error?: string;
  meta?: { changes?: CodeChange[]; reverted?: boolean; mode?: string; mode_choice?: string; research_depth?: string; agent_choice?: string; agent?: "build" | "plan"; project_path?: string; timeline?: TimelineSeg[]; handoff?: { messages: number; chars: number }; archives?: { id: string; name: string }[];
    imports?: { name: string; path: string; files: number }[]; sources?: SourceItem[]; plan?: AssistantPlan; progress?: ResearchProgress[]; research?: DeepResearchResult; compaction?: CompactionNote; attachments?: string[];
    tools?: OpenCodeTool[]; todos?: OpenCodeTodo[]; permissions?: OpenCodePermission[]; permission_mode?: PermissionMode };
}

interface Msg {
  role: "user" | "assistant";
  content: string;
  /** OpenCode がこの回で変えたファイル */
  changes?: CodeChange[];
  reverted?: boolean;
  /** 送った画像（表示用の URL） */
  images?: string[];
  /** 送った圧縮ファイルの名前（OpenCode のプロジェクトへ展開した） */
  archives?: string[];
  /** OpenCode: 道具の実行と許可の問い合わせ */
  tools?: OpenCodeTool[];
  todos?: OpenCodeTodo[];
  /** 回答した機能（"code" = OpenCode）。保存した発言から */
  answeredBy?: string;
  /** OpenCode のエージェント（plan は計画だけ。何も変えない） */
  agent?: "build" | "plan";
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

interface CompactionNote { tokens_before?: number; tokens_after?: number; threshold?: number; folded_messages?: number }

/** 回答が最後まで出なかった終わり方。ジョブの状態（succeeded など）と発言の状態の両方を受ける。 */
interface GenStats {
  phase: string; tokPerSec: number; genTokens: number;
  promptTokens: number | null; contextMax: number | null; detail?: string;
}

function settledStatus(status: unknown): string {
  if (status === "failed" || status === "canceled" || status === "interrupted") return status;
  if (status === "cancelled") return "canceled";
  return "completed";
}

/** DB の発言を画面の形へ。 */
function restoreMessage(convId: string, m: PersistMsg): Msg {
  return {
    role: m.role,
    content: m.content,
    images: m.meta?.attachments?.map((a) => attachmentUrl(convId, a)),
    archives: m.meta?.archives?.map((a) => a.name),
    tools: m.meta?.tools,
    todos: m.meta?.todos,
    changes: m.meta?.changes,
    reverted: m.meta?.reverted,
    permissions: m.meta?.permissions,
    answeredBy: m.meta?.mode,
    agent: m.meta?.agent,
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

const LS_KEY = "cd-assistant-settings";
const LS_HISTORY = "cd-assistant-history"; // PC の履歴の列を畳んだか（"min"）
const NEW_CHAT_TITLE = "新しいチャット";

function readHistoryCollapsed(): boolean {
  try {
    return localStorage.getItem(LS_HISTORY) === "min";
  } catch {
    return false;
  }
}
const LS_CONV = "cd-chat-conversation"; // 永続チャットの会話 ID（本文は DB に保存）
const LS_TTS_REPLY = "cd-assistant-tts-reply"; // TTS 返答（回答を読み上げる）を端末ごとに覚える
/** 会話モードの段階。話し終わりは、最後の声から TALK_TURN_END_MS 黙ったとき */
type TalkPhase = "off" | "starting" | "listening" | "thinking" | "speaking";
const TALK_TURN_END_MS = 1200;

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

/** アプリの殻（AppLayout）の中に置く 1 画面。上の行は殻と同じ形で、左上の ☰ が
 *  同じドロワーを開く。画面は高さ固定で、動くのは会話の一覧だけ。 */
export default function AssistantChat() {
  const shell = useShell();
  useKeyboardViewportFit();
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const saved = useRef(loadSettings()).current;
  const [storedChoice, setModeChoice] = useState<AssistantModeChoice>("auto");
  const [codeAgent, setCodeAgent] = useState<"build" | "plan">("build");
  // 「調査」の深さ。自動は依頼の中身から Web / Web+論文 / Deep / 論文 を選ぶ
  const [researchDepth, setResearchDepth] = useState<ResearchDepth>("auto");
  const [messages, setMessages] = useState<Msg[]>([]); // 会話本文は DB から復元
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [baseUrl, setBaseUrl] = useState<string>(saved.baseUrl || "http://127.0.0.1:11434/v1");
  const [model, setModel] = useState<string>(saved.model || "");
  // ⚙️で選択中runtimeのendpoint（最後に追従した値）。変わったら手動選択より優先して切り替える。
  const [autoBase, setAutoBase] = useState<string>(saved.autoBase || "");
  // Web検索エンジンはSearXNG固定（ローカル・オンデマンド起動）
  const engine = "searxng";
  const searxngUrl = "";
  const [deepDepth, setDeepDepth] = useState<string>(saved.deepDepth || "deep");
  const [deepSources, setDeepSources] = useState<string[]>(
    Array.isArray(saved.deepSources) ? saved.deepSources : ["web", "academic", "github", "direct", "rag"],
  );
  const [deepProjectPath, setDeepProjectPath] = useState<string>(saved.deepProjectPath || "");
  const [runTarget, setRunTarget] = useState<number | "">("");
  // OpenCodeモード: 新規（既定）/ CodeDEV既存 / 📁フォルダ（CodeDEV外はコピー取込）
  const [codeProject, setCodeProject] = useState("__new__");
  const [codeNewName, setCodeNewName] = useState("");
  const [codeFolderPath, setCodeFolderPath] = useState("");
  const [codePicker, setCodePicker] = useState(false);
  // 📎添付: 画像は次の送信でVLMへ、文書はアップロード時に会話コレクションへRAG登録済み
  const [attachments, setAttachments] = useState<{
    id: string; name: string; kind: "image" | "document" | "archive"; status: "uploading" | "ready"; preview?: string;
    /** 圧縮ファイルの中のファイル数 */
    files?: number;
    /** 0〜1。送り終わった文書は、資料への登録（サーバー側の処理）を待つ */
    progress?: number; size?: number;
  }[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [convId, setConvId] = useState<string>(() => localStorage.getItem(LS_CONV) || "");
  const [conversationTitle, setConversationTitle] = useState(NEW_CHAT_TITLE);
  const [resolvedDecision, setResolvedDecision] = useState<AssistantPlan | null>(null);
  const [routing, setRouting] = useState(false);
  // 生成統計（右下表示）: フェーズ / tok/s / コンテキスト使用量
  const [genStats, setGenStats] = useState<GenStats | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  const { data: runtimeEnvironment } = useQuery({
    queryKey: ["runtime-environment"],
    queryFn: () => api<{ policy: { assistant_name: string } }>("/models/runtime-environment"),
  });
  const assistantName = runtimeEnvironment?.policy.assistant_name || "AIアシスタント";
  const { data: appMeta } = useMeta();
  const opencodeAvailable = !!appMeta?.enabled_features?.includes("opencode-v2") && can("terminal.use");
  // 覚えていた機能が今は使えない（OpenCode を無効にした・権限が無い）ときは、選択を消さずに自動で動かす
  const modeChoice: AssistantModeChoice = (storedChoice === "code" && !opencodeAvailable)
    || (MODES.find((m) => m.id === storedChoice)?.needsEdit && !can("workflows.edit")) ? "auto" : storedChoice;
  const { data: codeProjects } = useQuery({
    queryKey: ["opencode-projects"],
    queryFn: () => api<{ root: string; projects: { name: string; path: string; git: boolean }[] }>("/opencode/projects"),
    enabled: opencodeAvailable,
    staleTime: 30_000,
  });
  const { data: conversations } = useQuery({
    queryKey: ["chat-conversations"],
    queryFn: () => api<ConversationSummary[]>("/chat/conversations"),
  });
  // 診断用: 画面を開いた時刻と、会話の一覧が届いた時刻
  const openedAtRef = useRef(performance.now());
  const listLoadedAtRef = useRef(0);
  useEffect(() => {
    if (conversations && !listLoadedAtRef.current) listLoadedAtRef.current = performance.now();
  }, [conversations]);

  useEffect(() => {
    const current = conversations?.find((conversation) => conversation.id === convId);
    if (current) setConversationTitle(current.title);
  }, [conversations, convId]);

  // 永続チャット: マウント時に DB 会話を復元し、生成中メッセージがあれば購読を再開する。
  // これにより、生成中にブラウザを閉じても回答はサーバー側で保存され、再度開くと続きが見える。
  useEffect(() => {
    let cancelled = false;
    const openedAt = openedAtRef.current;
    (async () => {
      let id = convId;
      try {
        if (!id) {
          return; // 空の下書きは送信されるまでDBへ登録しない
        }
        const data = await api<{ messages: PersistMsg[] }>(`/chat/conversations/${id}/messages`);
        if (cancelled) return;
        const fetchedAt = performance.now();
        const restored = data.messages.map((m) => restoreMessage(id, m));
        if (restored.length > 0) setMessages(restored);
        restoreCodeProject(data.messages, id);
        // 診断: 開いてから会話が描かれるまで（2 フレーム後＝描画が済んだ時点）を送る
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const renderedAt = performance.now();
          window.setTimeout(() => reportClientTiming("assistant_open", openedAt, {
            messages_ms: fetchedAt - openedAt, rendered_ms: renderedAt - openedAt,
            list_ms: listLoadedAtRef.current ? listLoadedAtRef.current - openedAt : -1,
            message_count: restored.length,
          }), 1500);
        }));
        // 生成中のまま残っているメッセージを購読再開
        const gen = data.messages.find((m) => m.role === "assistant" && m.status === "generating");
        if (gen) {
          setBusy(true);
          streamMessage(gen.id).finally(() => setBusy(false));
        }
      } catch {
        // 会話が消えていたら作り直す
        localStorage.removeItem(LS_CONV);
        setConvId("");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    localStorage.setItem(LS_KEY, JSON.stringify({ baseUrl, model, autoBase, deepDepth, deepSources, deepProjectPath }));
  }, [baseUrl, model, autoBase, deepDepth, deepSources, deepProjectPath]);

  // SearXNG は基本停止・使う時だけ起動。検索系モード + SearXNG 選択時に先読み起動して
  // 実際の検索でコールドスタート（2〜3 秒）を待たずに済むようにする
  useEffect(() => {
    const detected = modeChoice === "auto" || modeChoice === "investigate"
      ? detectAssistantMode(input, [], can("workflows.edit"), opencodeAvailable).mode : modeChoice;
    if ((detected === "web" || detected === "deep" || detected === "research") && engine === "searxng" && !searxngUrl) {
      api("/chat/searxng-warmup", { method: "POST" }).catch(() => {});
    }
  }, [modeChoice, input, engine, searxngUrl, can]);

  useEffect(() => {
    if (!stickToBottomRef.current) return;
    const frame = requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (element) element.scrollTop = element.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages]);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // LLM エンドポイント自動検出
  const { data: endpoints } = useQuery({
    queryKey: ["llm-endpoints"],
    queryFn: () => api<{ base_url: string; models: string[]; managed?: boolean; selected?: boolean; selected_runtime?: string;
      /** 外部のフロンティアモデル（AI アシスタントからの送信を許可したときだけ並ぶ）。 */
      external_models?: { id: string; name: string; provider: string }[] }[]>("/workflows/llm-endpoints"),
    staleTime: 60_000,
    enabled: can("workflows.edit"),
  });
  // ⚙️の選択runtime（selected=true）へ追従する。選択が前回追従時から変わったら
  // 保存済みの接続先とモデルを上書きし、同じ選択のままなら手動指定を尊重する。
  // ゲートウェイはランタイムを変えてもアドレスが同じなので、Models で選んだ
  // ランタイムも追従の鍵に含める。変わったらモデルを auto（選んだランタイム）へ戻す。
  useEffect(() => {
    if (!endpoints?.length) return;
    const preferred = endpoints.find((ep) => ep.selected) ?? endpoints[0];
    if (!preferred) return;
    const followKey = preferred.selected_runtime ? `${preferred.base_url}#${preferred.selected_runtime}` : preferred.base_url;
    if (followKey !== autoBase || !model) {
      setAutoBase(followKey);
      setBaseUrl(preferred.base_url);
      if (followKey !== autoBase && preferred.models.includes("auto")) setModel("auto");
      // 外部のモデルを既定にはしない（送信は利用者が自分で選んだときだけ）。
      else if (!preferred.models.includes(model)) {
        const external = new Set((preferred.external_models ?? []).map((item) => item.id));
        setModel(preferred.models.find((item) => !external.has(item)) ?? "");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoints]);

  // フロー実行モード用のワークフロー一覧
  const { data: workflows } = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api<Array<{ id: number; name: string }>>("/workflow-runner"),
    enabled: can("workflows.run"),
  });

  // 会話の流れ: OpenCode が作業中か、引き継ぐ回答があるか（ボタンを押さずに引き継ぐ判定に使う）
  const isCodeAnswer = (m: Msg) => m.answeredBy === "code" || (m.tools?.length ?? 0) > 0 || (m.permissions?.length ?? 0) > 0;
  const lastCodeIndex = messages.reduce((last, m, i) => (isCodeAnswer(m) ? i : last), -1);
  const inCodeConversation = lastCodeIndex >= 0;
  const pendingHandoff = messages.slice(lastCodeIndex + 1).some((m) => m.role === "assistant" && !m.streaming && m.content.trim());
  const conversationContext = { inCodeConversation, hasAnswers: pendingHandoff };
  const archiveWaiting = attachments.some((a) => a.kind === "archive" && a.status === "ready");
  // プランは OpenCode の機能。プラン中は、調査は「調べて計画」、自動のチャットも OpenCode の計画として扱う。
  // 2026-11-18 利用者「調査とプランを選べるが、プロジェクトを選べない。どこに書き込まれるのか」
  const plansInOpenCode = codeAgent === "plan" && opencodeAvailable && !attachments.some((a) => a.kind === "image");
  const detected = detectAssistantMode(input, workflows ?? [], can("workflows.edit"), opencodeAvailable, conversationContext);
  const autoDecision = archiveWaiting && opencodeAvailable
    ? { mode: "code" as const, reason: "圧縮ファイルを OpenCode のプロジェクトへ展開" }
    : plansInOpenCode && (detected.mode === "chat" || SEARCH_MODES.includes(detected.mode))
      ? { mode: "code" as const, research: SEARCH_MODES.includes(detected.mode), reason: "プランは OpenCode で作ります" }
      : detected;
  const displayedDecision = input.trim() || archiveWaiting ? autoDecision : (resolvedDecision ?? autoDecision);
  const effectiveMode: Mode = modeChoice === "auto" ? displayedDecision.mode
    : modeChoice === "investigate"
      ? plansInOpenCode ? "code" : (researchDepth !== "auto" ? researchDepth : SEARCH_MODES.includes(displayedDecision.mode) ? displayedDecision.mode : "web")
      : modeChoice;

  const append = (m: Msg) => setMessages((prev) => [...prev, m]);
  const patchLast = (fn: (m: Msg) => Msg) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

  /** 永続チャット: assistant メッセージの生成を購読する（切断してもサーバーは継続）。
   * 対象メッセージ（messageId 一致）へ delta/thinking を書き込む。 */
  const streamMessage = (messageId: string) => {
    setGenStats(null);   // 前の回答の「完了」を残さない（段階はサーバーからすぐ届く）
    return streamMessageInner(messageId);
  };
  const streamMessageInner = (messageId: string) =>
    new Promise<void>((resolve) => {
      const patchMsg = (fn: (m: Msg) => Msg) =>
        setMessages((prev) => prev.map((m) => (m.messageId === messageId ? fn(m) : m)));
      let attempts = 0;
      let settled = false;
      let pendingContent = "";
      let pendingThinking = "";
      let flushTimer = 0;
      const flush = () => {
        window.clearTimeout(flushTimer);
        flushTimer = 0;
        if (!pendingContent && !pendingThinking) return;
        const content = pendingContent;
        const thinking = pendingThinking;
        pendingContent = "";
        pendingThinking = "";
        patchMsg((m) => {
          // OpenCode の回答だけ並びを持つ（flush の前に種類が変わったら先に flush しているので、1 回の flush は 1 種類）
          let timeline = m.timeline;
          if (m.answeredBy === "code") {
            if (thinking) timeline = extendTimeline(timeline, "t", (m.thinking ?? "").length, thinking.length);
            if (content) timeline = extendTimeline(timeline, "x", m.content.length, content.length);
          }
          return { ...m, content: m.content + content, thinking: (m.thinking ?? "") + thinking, timeline };
        });
      };
      const scheduleFlush = () => {
        if (!flushTimer) flushTimer = window.setTimeout(flush, 40);
      };
      const connect = () => {
        const ws = new WebSocket(wsUrl(`/chat/messages/${messageId}/stream`));
        wsRef.current = ws;
        let receivedDone = false;
        ws.onopen = () => patchMsg((m) => ({ ...m, connectionState: "live", streaming: true }));
        ws.onmessage = (ev) => {
          const d = JSON.parse(ev.data);
          if (d.type === "snapshot") {
            flush();
            patchMsg((m) => ({ ...m, content: d.content, thinking: d.thinking || undefined,
              timeline: Array.isArray(d.timeline) && d.timeline.length ? d.timeline : m.timeline,
              changes: Array.isArray(d.changes) && d.changes.length ? d.changes : m.changes }));
          } else if (d.type === "delta") {
            if (pendingThinking) flush();   // 種類が変わる所で区切る（思考と本文の順番を保つ）
            pendingContent += d.content;
            scheduleFlush();
          } else if (d.type === "thinking") {
            if (pendingContent) flush();
            pendingThinking += d.content;
            scheduleFlush();
          } else if (d.type === "stats") {
            setGenStats({
              phase: d.phase || "", tokPerSec: d.tok_per_sec ?? 0, genTokens: d.gen_tokens ?? 0,
              promptTokens: d.prompt_tokens ?? null, contextMax: d.context_max ?? null, detail: d.detail || "",
            });
          } else if (d.type === "todos") {
            patchMsg((m) => ({ ...m, todos: d.todos as OpenCodeTodo[] }));
          } else if (d.type === "changes") {
            patchMsg((m) => ({ ...m, changes: d.changes as CodeChange[] }));
          } else if (d.type === "steer") {
            patchMsg((m) => ({ ...m, timeline: [...(m.timeline ?? []), { k: "n" as const, text: String(d.text ?? "") }] }));
          } else if (d.type === "sources") patchMsg((m) => ({ ...m, kind: "sources", sources: d.sources }));
          else if (d.type === "plan") patchMsg((m) => ({ ...m, plan: d.plan }));
          else if (d.type === "compaction") patchMsg((m) => ({ ...m, compaction: d }));
          else if (d.type === "tool") {
            flush();   // 道具より前に届いた思考・本文を先に並べる
            patchMsg((m) => {
              const tool = d.tool as OpenCodeTool;
              const tools = m.tools ?? [];
              const known = tools.some((t) => t.id === tool.id);
              // 道具が届くのは OpenCode だけ。準備中に開き直した発言はまだ印が無いので、ここで付ける。
              // それまでの思考は 1 つのまとまりとして並びの先頭に置く
              const base = m.timeline?.length ? m.timeline
                : [...(m.thinking ? [{ k: "t" as const, from: 0, to: m.thinking.length }] : []),
                  ...(m.content ? [{ k: "x" as const, from: 0, to: m.content.length }] : [])];
              return { ...m, answeredBy: "code", tools: known ? tools.map((t) => (t.id === tool.id ? { ...t, ...tool } : t)) : [...tools, tool],
                timeline: known ? (m.timeline ?? base) : [...base, { k: "u", id: tool.id }] };
            });
          }
          else if (d.type === "permission") patchMsg((m) => {
            const item = d.permission as OpenCodePermission;
            const list = m.permissions ?? [];
            return { ...m, permissions: list.some((p) => p.id === item.id)
              ? list.map((p) => (p.id === item.id ? { ...p, ...item } : p)) : [...list, item] };
          });
          else if (d.type === "progress") patchMsg((m) => ({
            ...m, progress: [...(m.progress ?? []), { phase: d.phase, label: d.label, iteration: d.iteration, details: d.details }],
          }));
          else if (d.type === "done") {
            receivedDone = true;
            flush();
            patchMsg((m) => ({
              ...m, streaming: false, persistStatus: settledStatus(d.status),
              error: typeof d.error === "string" && d.error ? d.error : m.error, connectionState: "live",
            }));
          } else if (d.type === "error") {
            receivedDone = true;
            flush();
            patchMsg((m) => ({ ...m, content: m.content + `\n⚠️ ${d.message}`, streaming: false }));
          }
        };
        ws.onclose = () => {
          flush();
          if (receivedDone || settled) {
            if (!settled) { settled = true; resolve(); }
            return;
          }
          if (attempts < 5) {
            attempts += 1;
            patchMsg((m) => ({ ...m, connectionState: "reconnecting", streaming: true }));
            window.setTimeout(connect, Math.min(500 * 2 ** (attempts - 1), 5000));
          } else {
            settled = true;
            patchMsg((m) => ({ ...m, streaming: false, connectionState: "reconnecting" }));
            resolve();
          }
        };
        ws.onerror = () => ws.close();
      };
      connect();
    });

  /** 会話の作業フォルダを戻す: 最後の OpenCode の回答が動いたプロジェクト。2026-11-18 利用者
   *  「AI アシスタントを閉じて戻ると作業フォルダの設定が外れる」。画面の中だけで覚えていたので、既定の新規作成に戻っていた。 */
  const restoreCodeProject = (rows: PersistMsg[], conversationId: string) => {
    const last = [...rows].reverse().find((m) => m.role === "assistant" && m.meta?.mode === "code" && m.meta?.project_path);
    // 機能の固定・調査の深さ・プランは会話ごとに戻す（この端末の選択 → 送った発言の記録の順）
    const sentRow = [...rows].reverse().find((m) => m.role === "user" && m.meta?.mode_choice);
    const sent = sentRow?.meta;
    const prefs = resolveConversationPrefs(conversationId, {
      choice: validChoice(sent?.mode_choice), depth: validDepth(sent?.research_depth),
      agent: sent?.agent_choice === "plan" || sent?.agent_choice === "build" ? sent.agent_choice : undefined,
    }, sentRow?.id ?? "", rows.map((m) => m.id));
    setModeChoice(prefs.choice ?? "auto");
    setResearchDepth(prefs.depth ?? "auto");
    returnChoiceRef.current = null;
    setCodeAgent(prefs.agent ?? (last?.meta?.agent === "plan" ? "plan" : "build"));
    const path = last?.meta?.project_path;
    if (!path) {
      // OpenCode を使っていない会話に、別の会話で選んだフォルダを持ち越さない（知らない場所で作業しない）
      setCodeProject("__new__");
      setCodeNewName("");
      setCodeFolderPath("");
      return;
    }
    const name = path.split("/").pop() ?? "";
    const listed = codeProjects?.projects.some((p) => p.name === name && (p.path === path || codeProjects.root.replace(/\/+$/, "") === path.slice(0, -name.length - 1)));
    if (listed) {
      setCodeProject(name);
    } else if (!codeProjects) {
      // 一覧がまだ届いていない。フォルダとして持っておき、一覧が届いたら名前に直す（下の useEffect）
      setCodeFolderPath(path);
      setCodeProject("__folder__");
    } else {
      // 消した・名前を変えた・CodeDEV の外のフォルダは選び直さない。別の会話の選択も残さない
      // （未選択で送ると、サーバーがこの会話の最後のフォルダが残っていれば続ける）
      setCodeProject("__new__");
      setCodeFolderPath("");
    }
    setCodeNewName("");
  };

  // プロジェクトの一覧が後から届いたら、CodeDEV の中のフォルダ指定はプロジェクト名の選択に直す
  useEffect(() => {
    if (codeProject !== "__folder__" || !codeProjects) return;
    const root = codeProjects.root.replace(/\/+$/, "");
    const name = codeFolderPath.split("/").pop() ?? "";
    const listed = codeProjects.projects.some((p) => p.name === name && (p.path === codeFolderPath || codeFolderPath === `${root}/${name}`));
    if (listed) {
      setCodeProject(name);
      setCodeFolderPath("");
    }
  }, [codeProjects?.root, codeProject, codeFolderPath]);

  const openConversation = async (id: string): Promise<boolean> => {
    if (!id || id === convId || busy) return false;
    wsRef.current?.close();
    const data = await api<{ conversation: ConversationSummary; messages: PersistMsg[] }>(`/chat/conversations/${id}/messages`);
    const restored = data.messages.map((message) => restoreMessage(id, message));
    localStorage.setItem(LS_CONV, id);
    setAttachments([]); // 別の会話の添付を持ち越さない
    setConvId(id);
    setConversationTitle(data.conversation.title);
    setMessages(restored);
    restoreCodeProject(data.messages, id);
    const generating = data.messages.find((message) => message.role === "assistant" && message.status === "generating");
    if (generating) {
      setBusy(true);
      streamMessage(generating.id).finally(() => setBusy(false));
    }
    return true;
  };

  const resetConversationChoices = () => {
    setModeChoice("auto");
    setResearchDepth("auto");
    setCodeAgent("build");
    setResolvedDecision(null);
    returnChoiceRef.current = null;
    setCodeProject("__new__");   // 作業フォルダも会話ごと
    setCodeNewName("");
    setCodeFolderPath("");
  };

  const newConversation = () => {
    if (busy) return;
    wsRef.current?.close();
    localStorage.removeItem(LS_CONV);
    setConvId("");
    setConversationTitle(NEW_CHAT_TITLE);
    setMessages([]);
    setAttachments([]); // 前の会話の添付を持ち越さない
    resetConversationChoices(); // 機能の固定・プランは会話ごと（新しい会話へ持ち越さない）
    setGenStats(null);
    setHistoryOpen(false);
    // シートが閉じるときに元のボタンへフォーカスを戻すので、その後で入力欄へ
    window.setTimeout(() => composerRef.current?.focus({ preventScroll: true }), 50);
  };

  const renameConversation = async (id: string, title: string) => {
    const next = title.trim();
    if (!id || !next) return;
    try {
      await api(`/chat/conversations/${id}`, { method: "PATCH", json: { title: next } });
      if (id === convIdRef.current) setConversationTitle(next);
      qc.invalidateQueries({ queryKey: ["chat-conversations"] });
      show("会話名を変更しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "会話名を変更できませんでした", "error");
    }
  };

  const [deletingConversation, setDeletingConversation] = useState<ConversationSummary | null>(null);
  const confirmDeleteConversation = async () => {
    const target = deletingConversation;
    setDeletingConversation(null);
    if (!target) return;
    try {
      await api(`/chat/conversations/${target.id}`, { method: "DELETE" });
      if (target.id === convIdRef.current) {
        wsRef.current?.close();
        localStorage.removeItem(LS_CONV);
        setConvId("");
        setConversationTitle(NEW_CHAT_TITLE);
        setMessages([]);
        setAttachments([]); // 消した会話の添付を持ち越さない
        resetConversationChoices();
      }
      qc.invalidateQueries({ queryKey: ["chat-conversations"] });
      show("会話を削除しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "会話を削除できませんでした", "error");
    }
  };

  // ---- 履歴のスワイプ: 右でアーカイブ、左で削除（確認なし・数秒は「元に戻す」） ----
  const [showArchived, setShowArchived] = useState(false);
  const { data: archivedConversations } = useQuery({
    queryKey: ["chat-conversations", "archived"],
    queryFn: () => api<ConversationSummary[]>("/chat/conversations?archived=true"),
    enabled: showArchived,
  });
  const pendingDeletes = useRef(new Map<string, number>());
  const leaveIfCurrent = (id: string) => {
    if (id !== convIdRef.current) return;
    wsRef.current?.close();
    localStorage.removeItem(LS_CONV);
    setConvId("");
    setConversationTitle(NEW_CHAT_TITLE);
    setMessages([]);
    setAttachments([]);
    resetConversationChoices();
  };
  const dropFromLists = (id: string) => {
    for (const key of [["chat-conversations"], ["chat-conversations", "archived"]]) {
      qc.setQueryData<ConversationSummary[]>(key, (list) => list?.filter((item) => item.id !== id));
    }
  };
  const archiveConversation = async (conversation: ConversationSummary, archived: boolean) => {
    dropFromLists(conversation.id);
    if (archived) leaveIfCurrent(conversation.id);
    try {
      await api(`/chat/conversations/${conversation.id}`, { method: "PATCH", json: { archived } });
      show(archived ? `「${conversation.title}」をアーカイブしました` : `「${conversation.title}」を戻しました`, "success",
        archived ? { label: "元に戻す", onClick: () => void archiveConversation(conversation, false) } : undefined);
    } catch (e) {
      show(e instanceof Error ? e.message : "アーカイブできませんでした", "error");
    } finally {
      qc.invalidateQueries({ queryKey: ["chat-conversations"] });
    }
  };
  const flushDelete = async (conversation: ConversationSummary) => {
    pendingDeletes.current.delete(conversation.id);
    try {
      await api(`/chat/conversations/${conversation.id}`, { method: "DELETE" });
    } catch (e) {
      show(e instanceof Error ? e.message : "会話を削除できませんでした", "error");
      qc.invalidateQueries({ queryKey: ["chat-conversations"] });
    }
  };
  const swipeDeleteConversation = (conversation: ConversationSummary) => {
    // すぐ一覧から消し、6 秒後に本当に消す（その間は「元に戻す」で取り消せる）
    dropFromLists(conversation.id);
    leaveIfCurrent(conversation.id);
    const timer = window.setTimeout(() => void flushDelete(conversation), 6000);
    pendingDeletes.current.set(conversation.id, timer);
    show(`「${conversation.title}」を削除しました`, "success", {
      label: "元に戻す",
      onClick: () => {
        window.clearTimeout(pendingDeletes.current.get(conversation.id));
        pendingDeletes.current.delete(conversation.id);
        qc.invalidateQueries({ queryKey: ["chat-conversations"] });
      },
    }, 6000);
  };
  // 画面を離れる・閉じるときは、待っている削除をすぐ済ませる
  useEffect(() => {
    const flushAll = () => {
      for (const [id, timer] of pendingDeletes.current) {
        window.clearTimeout(timer);
        void fetch(`/api/v1/chat/conversations/${encodeURIComponent(id)}`, {
          method: "DELETE", credentials: "same-origin", keepalive: true, headers: { "X-Requested-With": "ControlDeck" },
        });
      }
      pendingDeletes.current.clear();
    };
    window.addEventListener("pagehide", flushAll);
    return () => {
      window.removeEventListener("pagehide", flushAll);
      flushAll();
    };
  }, []);

  // ---- 履歴（PC は左の列、携帯は左から出るシート）と題名の書き換え ----
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(readHistoryCollapsed);
  const [renamingTitle, setRenamingTitle] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const setHistoryCollapsedSaved = (next: boolean) => {
    setHistoryCollapsed(next);
    try {
      localStorage.setItem(LS_HISTORY, next ? "min" : "full");
    } catch { /* 保存できなくても、この場の開閉は効く */ }
  };
  const toggleHistory = () => {
    if (window.matchMedia("(min-width: 1024px)").matches) setHistoryCollapsedSaved(!historyCollapsed);
    else setHistoryOpen(true);
  };
  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const onChange = () => query.matches && setHistoryOpen(false);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  /* 入力欄は中身に合わせて伸びる（上限まで。超えたら中をスクロール）。 */
  useEffect(() => {
    const element = composerRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  }, [input]);
  const pickConversation = async (id: string) => {
    setHistoryOpen(false);
    if (id === convIdRef.current) return;
    try {
      await openConversation(id);
    } catch (e) {
      show(e instanceof Error ? e.message : "会話を開けませんでした", "error");
      qc.invalidateQueries({ queryKey: ["chat-conversations"] });
    }
  };

  const convIdRef = useRef(convId);
  convIdRef.current = convId;
  const ensureConversation = async (): Promise<string> => {
    if (convIdRef.current) return convIdRef.current;
    const c = await api<{ id: string }>("/chat/conversations", { method: "POST" });
    localStorage.setItem(LS_CONV, c.id);
    convIdRef.current = c.id;
    setConvId(c.id);
    qc.invalidateQueries({ queryKey: ["chat-conversations"] });
    return c.id;
  };

  /** 📎添付アップロード。画像→保存（送信時にVLMへ）、文書/PDF→会話コレクションへ即RAG登録。 */
  // FileList は選択欄と連動していて、選択欄を空にすると中身も消える。呼ぶ側で File[] に写してから渡す。
  const attachFiles = async (files: File[]) => {
    if (files.length === 0) return;
    let cid: string;
    try {
      cid = await ensureConversation();
    } catch (e) {
      show(e instanceof Error ? e.message : "会話の作成に失敗しました", "error");
      return;
    }
    for (const [index, file] of files.slice(0, 8).entries()) {
      const tempId = `up-${Date.now()}-${index}-${file.name}`;
      const isImage = file.type.startsWith("image/");
      const isArchive = ARCHIVE_RE.test(file.name);
      if (isArchive && !opencodeAvailable) {
        show("圧縮ファイルは OpenCode で展開して使います（OpenCode が使えません）", "error");
        continue;
      }
      if (isArchive && file.size > ARCHIVE_MAX_BYTES) {
        // サーバーは受け取り終えてから断るので、送る前に止める（大きいものを送り切らない）
        show(`圧縮ファイルは ${ARCHIVE_MAX_BYTES / 2 ** 20} MiB 以内にしてください`, "error");
        continue;
      }
      // 貼り付けた画像は名前が「image.png」だけになりがち。時刻で見分けられるようにする。
      const name = isImage && /^image\.\w+$/.test(file.name) ? `貼り付け ${new Date().toLocaleTimeString()}.${file.name.split(".").pop()}` : file.name;
      const preview = isImage ? URL.createObjectURL(file) : undefined;
      setAttachments((prev) => [...prev, { id: tempId, name, kind: isImage ? "image" : isArchive ? "archive" : "document", status: "uploading", preview, progress: 0, size: file.size }]);
      try {
        const form = new FormData();
        form.append("file", file, name);
        const response = await uploadWithProgress(`/api/v1/chat/conversations/${cid}/attachments`, form, (ratio) => {
          setAttachments((prev) => prev.map((a) => (a.id === tempId ? { ...a, progress: ratio } : a)));
        });
        if (!response.ok) throw new Error((response.body as { detail?: string } | null)?.detail ?? `アップロード失敗 (${response.status})`);
        const data = response.body as { kind: "image" | "document" | "archive"; id: string; name: string; chunks?: number; files?: number; note?: string };
        setAttachments((prev) => prev.map((a) => (a.id === tempId ? { id: data.id, name: data.name, kind: data.kind, status: "ready", preview: a.preview, files: data.files } : a)));
        if (data.kind === "document") show(data.note || `「${data.name}」を会話の資料として登録しました（${data.chunks ?? 0}チャンク）`);
        if (data.kind === "archive") {
          // 圧縮ファイルは OpenCode がプロジェクトへ展開して使う（2026-11-18 利用者の依頼）
          // 固定している機能は変えない（自動なら、送るときに OpenCode へ回す）
          show(modeChoice === "auto" || modeChoice === "code"
            ? `「${data.name}」（${data.files ?? 0} ファイル）は、送ると OpenCode のプロジェクトへ展開します`
            : `「${data.name}」を展開するには、＋から OpenCode か自動判定を選んでください`);
        }
      } catch (e) {
        if (preview) URL.revokeObjectURL(preview);
        setAttachments((prev) => prev.filter((a) => a.id !== tempId));
        show(e instanceof Error ? e.message : "添付に失敗しました", "error");
      }
    }
  };

  // ---- 会話の分岐・編集・削除 ----
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Msg | null>(null);
  const pendingResend = useRef<string | null>(null);
  type ForkResult = { id: string; title: string; attachments: string[]; documents_copied: boolean; opencode_context_reset: boolean };
  const forkAt = async (messageId: string, include: boolean): Promise<ForkResult | null> => {
    if (!convIdRef.current || busy) return null;
    try {
      const fork = await api<ForkResult>(`/chat/conversations/${convIdRef.current}/fork`, {
        method: "POST", json: { message_id: messageId, include },
      });
      qc.invalidateQueries({ queryKey: ["chat-conversations"] });
      // 分岐・編集して送り直しても、いまの選択を引き継ぐ（履歴が空の分岐でも固定が外れない）
      updateConversationPrefs(fork.id, { choice: returnChoiceRef.current ?? storedChoice, depth: researchDepth, agent: codeAgent }, "*");
      const folder = { project: codeProject, name: codeNewName, path: codeFolderPath };
      if (!(await openConversation(fork.id))) {
        show("分岐した会話を開けませんでした。履歴から開いてください", "error");
        return null;
      }
      // 作業フォルダも引き継ぐ（履歴が空の分岐でも、編集して送り直せるように）
      setCodeProject(folder.project);
      setCodeNewName(folder.name);
      setCodeFolderPath(folder.path);
      if (fork.opencode_context_reset) show("分岐しました。OpenCode は新しいセッションで始まります（前のやり取りは引き継がれません）");
      return fork;
    } catch (e) {
      show(e instanceof Error ? e.message : "分岐できませんでした", "error");
      return null;
    }
  };
  const onMessageAction = async (msg: Msg, action: MessageAction) => {
    if (action === "copy") {
      const ok = await writeClipboard(msg.content);
      show(ok ? "コピーしました" : "コピーできませんでした", ok ? undefined : "error");
      return;
    }
    if (!msg.messageId) return;
    if (action === "fork") {
      const fork = await forkAt(msg.messageId, true);
      if (fork && !fork.opencode_context_reset) show("ここから分岐しました（元の会話は履歴に残っています）");
    } else if (action === "edit") {
      setEditingId(msg.messageId);
    } else if (action === "delete") {
      setDeleting(msg);
    }
  };
  const resendEdited = async (messageId: string, text: string) => {
    const fork = await forkAt(messageId, false);
    if (!fork) return;
    setEditingId(null);
    // 書き直した発言に付いていた画像は、分岐先に写してあるので送り直しに載せる
    if (fork.attachments.length) {
      setAttachments(fork.attachments.map((id) => ({ id, name: id, kind: "image" as const, status: "ready" as const, preview: attachmentUrl(fork.id, id) })));
    }
    // 分岐した会話の表示が整ってから送る（次の描画で会話 ID が新しい方になる）
    pendingResend.current = text;
  };
  useEffect(() => {
    const text = pendingResend.current;
    if (!text || busy) return;
    pendingResend.current = null;
    void send(text);
  });
  const confirmDelete = async () => {
    const target = deleting;
    setDeleting(null);
    if (!target?.messageId || !convIdRef.current) return;
    try {
      await api(`/chat/conversations/${convIdRef.current}/messages/${target.messageId}`, { method: "DELETE" });
      setMessages((prev) => {
        const index = prev.findIndex((m) => m.messageId === target.messageId);
        if (index < 0) return prev;
        let end = index + 1;
        if (target.role === "user") while (end < prev.length && prev[end].role !== "user") end += 1;
        return [...prev.slice(0, index), ...prev.slice(end)];
      });
      show("削除しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "削除できませんでした", "error");
    }
  };

  // ---- OpenCode: 権限モード・許可への返答・停止 ----
  const { data: permissionModeData } = useQuery({
    queryKey: ["opencode-permission-mode"],
    queryFn: () => api<{ mode: PermissionMode }>("/chat/opencode/permission-mode"),
    staleTime: 60_000,
  });
  const permissionMode: PermissionMode = permissionModeData?.mode ?? "auto";
  const canChangePermissionMode = can("settings.manage");
  const permissionChainRef = useRef<Promise<void>>(Promise.resolve());
  const changePermissionMode = async (mode: PermissionMode) => {
    if (!canChangePermissionMode || mode === permissionMode) return;
    // 先に画面を変える（返事を待つと、Shift+Tab を続けて押したときに古い状態から数えて同じ所へ戻っていた）
    qc.setQueryData(["opencode-permission-mode"], { mode });
    // 送るのは 1 つずつ順に（続けて押すと、先に押した方が後で届いてサーバーと画面が食い違っていた。レビュー指摘）
    const run = permissionChainRef.current.then(async () => {
      try {
        await api("/chat/opencode/permission-mode", { method: "PUT", json: { mode } });
        show(`権限: ${PERMISSION_MODES.find((m) => m.id === mode)?.label}（次の操作から）`);
      } catch (e) {
        show(e instanceof Error ? e.message : "権限モードを変えられませんでした", "error");
        // 失敗したらサーバーの値に合わせ直す（手元の古い値へ戻さない）
        await qc.invalidateQueries({ queryKey: ["opencode-permission-mode"] });
      }
    });
    permissionChainRef.current = run;
    await run;
  };
  // OpenCode のエージェント: ビルド（作る・直す）／プラン（計画だけ。ファイルも状態も変えない）。
  // 2026-11-18 利用者「プランの切り替えを追加し、プランの際はビルドモードではないことが明確にわかること」
  const planFromCycleRef = useRef(false);
  // 「調べて OpenCode に渡す」は 1 回だけ調査にして、送ったら元の選択へ戻す
  const returnChoiceRef = useRef<AssistantModeChoice | null>(null);
  const rememberPrefs = (next: Partial<ConversationPrefs>) => {
    const seen = [...messages].reverse().find((m) => m.role === "user" && m.messageId)?.messageId;
    if (convIdRef.current) updateConversationPrefs(convIdRef.current, next, seen);
  };
  const finishOneShot = () => {
    if (!returnChoiceRef.current) return;
    setModeChoice(returnChoiceRef.current);
    returnChoiceRef.current = null;
  };
  const chooseMode = (choice: AssistantModeChoice) => {
    returnChoiceRef.current = null;
    setModeChoice(choice);
    rememberPrefs({ choice });
  };
  const changeCodeAgent = (agent: "build" | "plan") => {
    if (agent === codeAgent) return;
    setCodeAgent(agent);
    rememberPrefs({ agent });
    show(agent === "plan" ? "プラン: 計画だけを立てます（ファイルは変えません）" : "ビルド: 作る・直すに戻しました");
  };
  // Shift+Tab と札: Auto → 確認 → プラン → Auto（Claude Code と同じ並び）。権限を変えられない人はビルド ⇄ プラン
  const cyclePermissionMode = () => {
    if (codeAgent === "plan") {
      changeCodeAgent("build");
      // Shift+Tab で 確認 → プラン と来たときだけ Auto へ進む（＋から入ったプランを出ても権限は変えない）
      if (canChangePermissionMode && planFromCycleRef.current) void changePermissionMode(PERMISSION_MODES[0].id);
      planFromCycleRef.current = false;
      return;
    }
    const index = PERMISSION_MODES.findIndex((m) => m.id === permissionMode);
    if (!canChangePermissionMode || index === PERMISSION_MODES.length - 1) {
      planFromCycleRef.current = canChangePermissionMode;
      changeCodeAgent("plan");
      return;
    }
    void changePermissionMode(PERMISSION_MODES[index + 1].id);
  };
  const answerPermission = async (messageId: string, requestId: string, decision: "once" | "always" | "reject") => {
    if (!convId) return;
    try {
      await api(`/chat/conversations/${convId}/opencode/permission`, {
        method: "POST", json: { message_id: messageId, request_id: requestId, decision },
      });
      setMessages((prev) => prev.map((m) => (m.messageId === messageId
        ? { ...m, permissions: (m.permissions ?? []).map((p) => (p.id === requestId ? { ...p, status: decision } : p)) }
        : m)));
    } catch (e) {
      show(e instanceof Error ? e.message : "許可を伝えられませんでした", "error");
    }
  };
  const runningJob = messages.find((m) => m.streaming && m.jobId)?.jobId ?? null;
  const stopRunning = async () => {
    if (!runningJob) return;
    try {
      await api(`/jobs/${encodeURIComponent(runningJob)}/cancel`, { method: "POST" });
      show("止めています…");
    } catch (e) {
      show(e instanceof Error ? e.message : "止められませんでした", "error");
    }
  };

  const send = async (providedText?: string, agentOverride?: "build" | "plan", modeOverride?: AssistantModeChoice) => {
    const agent = agentOverride ?? codeAgent;
    // 「このプランで作る」は OpenCode に決め打ちで送る（機能をチャットなどに変えていても）
    const choice = modeOverride ?? modeChoice;
    const readyImages = attachments.filter((a) => a.kind === "image" && a.status === "ready");
    const readyArchives = attachments.filter((a) => a.kind === "archive" && a.status === "ready");
    if (attachments.some((a) => a.status === "uploading")) {
      show("添付のアップロードが終わってから送信してください", "error");
      return;
    }
    const text = (providedText ?? input).trim()
      || (readyArchives.length > 0 ? ARCHIVE_ONLY_PROMPT : readyImages.length > 0 ? IMAGE_ONLY_PROMPT : "");
    if (!text) return;
    // 画像は OpenCode に渡せない（VLM のチャットで見る）。画像があるときは会話の流れで OpenCode へ回さない
    const decision = detectAssistantMode(text, workflows ?? [], can("workflows.edit"), opencodeAvailable,
      readyImages.length > 0 ? {} : conversationContext);
    const planInOpenCode = agent === "plan" && opencodeAvailable && attachments.every((a) => a.kind !== "image");
    // OpenCode を手で選んでいても、「調べて反映して」なら先に調べる
    let researchFirst = choice === "auto" ? Boolean(decision.research)
      : choice === "code" ? wantsResearch(text) : false;
    let selectedMode: Mode = choice === "auto" ? (readyArchives.length > 0 && opencodeAvailable ? "code" : decision.mode)
      : choice === "investigate" ? (researchDepth === "auto" ? "web" : researchDepth) : choice;
    // プラン中の調査は、調べた結果をもとに OpenCode が計画する（2026-11-18 利用者の依頼）
    if (planInOpenCode && (SEARCH_MODES.includes(selectedMode) || (choice === "auto" && selectedMode === "chat"))) {
      researchFirst = SEARCH_MODES.includes(selectedMode);
      selectedMode = "code";
    }
    let selectedPlan: AssistantPlan | undefined;
    const selectedRunTarget = runTarget || decision.workflowId || "";
    if (selectedMode === "run" && selectedRunTarget === "") {
      show("実行するワークフローを選択してください", "error");
      return;
    }
    const codeProjectName = codeProject === "__new__" ? codeNewName.trim() : codeProject === "__folder__" ? "" : codeProject;
    const codeFolder = codeProject === "__folder__" ? codeFolderPath.trim() : "";
    if (readyImages.length > 0 && selectedMode === "code") {
      // OpenCode は画像を受け取れない。黙って落とさず、知らせる（レビュー指摘 2026-11-18）
      show("画像は OpenCode に渡せません。＋から Chat を選ぶと画像を見て答えます", "error");
      return;
    }
    if (readyArchives.length > 0 && selectedMode !== "code") {
      show("圧縮ファイルは OpenCode で展開して使います。＋から OpenCode を選んでください", "error");
      return;
    }
    // この会話に OpenCode の回答があれば、空でもサーバーが最後のプロジェクトで続ける
    if (selectedMode === "code" && !codeProjectName && !codeFolder && !messages.some((m) => m.projectPath)) {
      show("OpenCodeのプロジェクトを指定してください（新規名の入力・既存の選択・📁フォルダ）", "error");
      return;
    }
    // 発言に記録するのは利用者の選択（1 回だけの調査や「このプランで作る」の送り先ではない）
    const sentPrefs = { mode_choice: returnChoiceRef.current ?? storedChoice, research_depth: researchDepth, agent_choice: agent };
    if (busy) {
      // 生成中は送信待ちへ（サーバーが持ち、回答が終わったら送る。2026-11-18 利用者の依頼）
      if (selectedMode === "gen" || selectedMode === "run") {
        show("ワークフローの作成・実行は、いまの回答が終わってから送ってください", "error");
        return;
      }
      const cid = await ensureConversation();
      // サーバーでまだ生成が始まっていない（自動判定中・ワークフロー）なら、すぐには送らせない。前の発言が
      // 終わったときに送る（先に送られて順番が入れ替わっていた。レビュー指摘）
      const hold = !messages.some((m) => m.role === "assistant" && m.streaming && m.messageId);
      if (hold) deferredQueueRef.current = true;
      const json = selectedMode === "code"
        ? { content: text, mode: "code", base_url: baseUrl, model, code_project: codeProjectName, code_project_path: codeFolder,
          attachments: readyArchives.map((a) => a.id), code_research: researchFirst, code_research_depth: choice === "investigate" ? researchDepth : "auto", code_agent: agent, ...sentPrefs }
        : { content: text, mode: choice === "auto" ? "auto" : selectedMode, base_url: baseUrl, model, engine,
          searxng_url: searxngUrl, attachments: readyImages.map((a) => a.id), deep_depth: deepDepth, deep_sources: deepSources,
          code_project_path: deepSources.includes("local_code") ? deepProjectPath : "", ...sentPrefs };
      try {
        await api(`/chat/conversations/${cid}/queue`, { method: "POST", json: { ...json, hold } });
        if (choice === "investigate") finishOneShot();
        setInput("");
        setAttachments([]);
      } catch (e) {
        show(e instanceof Error ? e.message : "送信待ちに入れられませんでした", "error");
      } finally {
        void refetchQueue();
      }
      return;
    }
    setInput("");
    setBusy(true);
    setGenStats(null);
    let buildContinues = false;
    const userMsg: Msg = { role: "user", content: text, images: readyImages.map((a) => a.preview ?? "").filter(Boolean),
      archives: readyArchives.map((a) => a.name) };
    append(userMsg);
    try {
      const investigateAuto = choice === "investigate" && researchDepth === "auto" && selectedMode !== "code";
      if ((choice === "auto" && selectedMode !== "gen" && selectedMode !== "run" && selectedMode !== "code") || investigateAuto) {
        setRouting(true);
        selectedPlan = await api<AssistantPlan>("/chat/route", {
          method: "POST", json: { content: text, base_url: baseUrl, model, conversation_id: convIdRef.current ?? "" },
        });
        setRouting(false);
        if (investigateAuto && !SEARCH_MODES.includes(selectedPlan.mode)) {
          // 「調査」を選んだのに対話と判定されたら、少なくとも Web を調べる
          selectedMode = "web";
          selectedPlan = undefined;
        } else {
          selectedMode = selectedPlan.mode;
          setResolvedDecision(selectedPlan);
        }
      }
      if (selectedMode === "chat" || selectedMode === "web" || selectedMode === "academic" || selectedMode === "deep" || selectedMode === "research") {
        // 全て永続パス: サーバー側ジョブで検索/生成し DB 保存。ブラウザを閉じても継続・復元できる。
        const cid = await ensureConversation();
        const imageIds = readyImages.map((a) => a.id);
        const res = await api<{ assistant_message_id: string; user_message_id?: string }>(`/chat/conversations/${cid}/send`, {
          method: "POST",
          json: {
            content: text, mode: selectedMode, plan: selectedPlan, base_url: baseUrl, model,
            engine, searxng_url: searxngUrl, attachments: imageIds,
            deep_depth: deepDepth, deep_sources: deepSources,
            code_project_path: deepSources.includes("local_code") ? deepProjectPath : "",
            ...sentPrefs,
          },
        });
        if (choice === "investigate") finishOneShot();
        setAttachments([]);
        qc.invalidateQueries({ queryKey: ["chat-conversations"] });   // 話し始めたらすぐ題名を出す
        if (res.user_message_id) {
          updateConversationPrefs(cid, {}, res.user_message_id);   // この送信より後の選択を正とする
          setMessages((prev) => prev.map((m) => (m === userMsg ? { ...m, messageId: res.user_message_id } : m)));
        }
        const hint =
          selectedMode === "deep" ? "🔬 Deep サーチ中...（サーバー側で継続）" :
          selectedMode === "research" ? "🧭 調査計画に沿って複数ソースを確認中..." :
          selectedMode === "web" || selectedMode === "academic" ? "🔎 検索中...（サーバー側で継続）" : "";
        append({ role: "assistant", content: hint, streaming: true, messageId: res.assistant_message_id, persistStatus: "generating" });
        await streamMessage(res.assistant_message_id);
      } else if (selectedMode === "code") {
        // OpenCodeチャット実行: TUIへ遷移せず、サーバー側でheadless実行して
        // 進捗・結果をこのチャットへストリームする（Codex/Claude風）。
        // 同じ会話では前回のopencodeセッションを自動継続する。
        const cid = await ensureConversation();
        const res = await api<{ assistant_message_id: string; job_id?: string; user_message_id?: string }>(`/chat/conversations/${cid}/send`, {
          method: "POST",
          json: { content: text, mode: "code", base_url: baseUrl, model, code_project: codeProjectName, code_project_path: codeFolder,
            attachments: readyArchives.map((a) => a.id), code_research: researchFirst, code_research_depth: choice === "investigate" ? researchDepth : "auto", code_agent: agent, ...sentPrefs },
        });
        setAttachments((prev) => prev.filter((a) => a.kind !== "archive"));
        if (choice === "investigate") finishOneShot();
        setResolvedDecision(null);
        qc.invalidateQueries({ queryKey: ["opencode-projects"] });
        qc.invalidateQueries({ queryKey: ["chat-conversations"] });
        // 新規作成したプロジェクトを以後の既定として維持する（機能の選択は変えない）
        if (codeProject === "__new__" && codeProjectName) {
          setCodeProject(codeProjectName);
          setCodeNewName("");
        }
        if (res.user_message_id) {
          updateConversationPrefs(cid, {}, res.user_message_id);   // この送信より後の選択を正とする
          setMessages((prev) => prev.map((m) => (m === userMsg ? { ...m, messageId: res.user_message_id } : m)));
        }
        append({ role: "assistant", content: "", streaming: true, messageId: res.assistant_message_id,
          persistStatus: "generating", jobId: res.job_id ?? null, answeredBy: "code", agent });
        await streamMessage(res.assistant_message_id);
      } else if (selectedMode === "gen") {
        // 利用者の追補指定により、自動判定した生成は確認を挟まず、そのまま
        // サーバージョブで生成・登録・動作確認・自動修正へ進める。
        buildContinues = true;
        window.setTimeout(() => autoBuild({
          name: "", definition: { nodes: [], edges: [] }, valid: false,
          warnings: [], goal: text,
        }, false), 0);
      } else if (selectedMode === "run") {
        const wf = workflows?.find((w) => w.id === selectedRunTarget);
        append({ role: "assistant", content: `▶ 「${wf?.name ?? selectedRunTarget}」を実行中...`, kind: "run", streaming: true });
        const { execution_id } = await api<{ execution_id: number }>(`/workflow-runner/${selectedRunTarget}/runs`, {
          method: "POST",
          json: { input: { message: text } },
        });
        const result = await pollExecution(execution_id);
        patchLast((m) => ({ ...m, content: result, streaming: false }));
      }
    } catch (e) {
      patchLast((m) =>
        m.role === "assistant"
          ? { ...m, content: `⚠️ ${e instanceof Error ? e.message : "エラーが発生しました"}`, streaming: false }
          : m,
      );
    } finally {
      setRouting(false);
      if (!buildContinues) setBusy(false);
      // 題名（最初の発言から付く）と並び順（更新日時）を履歴へ反映する。内容から作る題名は回答の少し後に届く
      qc.invalidateQueries({ queryKey: ["chat-conversations"] });
      for (const wait of [12_000, 30_000, 75_000]) {
        window.setTimeout(() => qc.invalidateQueries({ queryKey: ["chat-conversations"] }), wait);
      }
    }
  };

  // 音声入力: 話の区切りごとに入力欄へ足す（送信はしない）。✕ で取り消すと、始めた時の文に戻す
  // 会話モードの間は入力欄ではなく、その回の発言（turnRef）へ足す
  const voiceBaseRef = useRef("");
  const joinSpoken = (prev: string, text: string) =>
    prev + (prev && /[A-Za-z0-9.,!?]$/.test(prev) && /^[A-Za-z0-9]/.test(text) ? " " : "") + text;
  const asr = useAssistantAsr({
    onText: (text) => {
      if (talkRef.current !== "off") turnRef.current = joinSpoken(turnRef.current, text);
      else setInput((prev) => joinSpoken(prev, text));
    },
    onError: (message) => show(message, "error"),
  });

  // ---- TTS 返答と会話モード（2026-11-19 利用者の依頼） ----
  // TTS 返答: 回答が終わったら主要な文言を声で読む（コード・表・URL・出典は読まない）。端末ごとに覚える
  // 会話モード: 話して黙る（TALK_TURN_END_MS）と送信 → 回答を読む → また聞く、を停止まで続ける。
  //   回答待ち・読み上げの間はマイクを止める（自分の声を拾って送らない）
  const tts = useAssistantTts({ onError: (message) => show(message, "error") });
  const [ttsReply, setTtsReply] = useState(() => { try { return localStorage.getItem(LS_TTS_REPLY) === "1"; } catch { return false; } });
  const ttsReplyRef = useRef(ttsReply);
  ttsReplyRef.current = ttsReply;
  const [talk, setTalk] = useState<TalkPhase>("off");
  const talkRef = useRef<TalkPhase>("off");
  const turnRef = useRef("");
  const replyStartedRef = useRef(false);
  const lastSpokenRef = useRef<string | undefined>(undefined);
  const setTalkPhase = (next: TalkPhase) => { talkRef.current = next; setTalk(next); };
  const toggleTtsReply = () => {
    const next = !ttsReply;
    if (next) tts.unlock(); else tts.stop();
    setTtsReply(next);
    try { localStorage.setItem(LS_TTS_REPLY, next ? "1" : "0"); } catch { /* 保存できなくても今は使える */ }
    show(next ? "回答を読み上げます" : "読み上げをやめました");
  };
  const startTalk = () => {
    if (talkRef.current !== "off" || asr.active) return;
    tts.unlock();   // 操作の中で一度鳴らす（iPhone で後から読み上げられるように）
    turnRef.current = "";
    setTalkPhase("starting");
    void asr.start();
  };
  const stopTalk = () => {
    setTalkPhase("off");
    turnRef.current = "";
    tts.stop();
    asr.cancel();
  };
  const speakThenListen = async (reply: string) => {
    setTalkPhase("speaking");
    if (reply.trim()) await tts.speak(reply);
    if (talkRef.current === "off") return;
    asr.resume();
    setTalkPhase("listening");
  };
  const startVoice = () => {
    voiceBaseRef.current = input;
    void asr.start();
  };
  const cancelVoice = () => {
    asr.cancel();
    setInput(voiceBaseRef.current);
  };

  const sendRef = useRef(send);
  sendRef.current = send;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  // 会話モード: マイクの準備ができたら聞き始める。マイクが止まったら（60 分・失敗）会話も終える
  useEffect(() => {
    if (talk === "starting" && asr.phase === "listening") setTalkPhase("listening");
    else if (talk !== "off" && (asr.phase === "error" || (talk !== "starting" && asr.phase === "idle"))) stopTalk();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asr.phase, talk]);
  // 会話モード: 話し終わり（文字にし終えて、黙って TALK_TURN_END_MS）を見て送る
  useEffect(() => {
    if (talk !== "listening") return;
    const timer = window.setInterval(() => {
      const text = turnRef.current.trim();
      if (!text || !asr.settled() || asr.quietMs() < TALK_TURN_END_MS) return;
      turnRef.current = "";
      asr.pause();
      replyStartedRef.current = false;
      setTalkPhase("thinking");
      void sendRef.current(text).finally(() => {
        // 送れなかった（入力の検査で止まった等）: 回答は来ないので、また聞く
        if (talkRef.current === "thinking" && !replyStartedRef.current) {
          asr.resume();
          setTalkPhase("listening");
        }
      });
    }, 200);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [talk]);
  // 回答が終わったら読む（会話モードは読み終えてから聞き直す）
  const prevBusyRef = useRef(busy);
  useEffect(() => {
    const was = prevBusyRef.current;
    prevBusyRef.current = busy;
    if (busy && !was) {
      if (talkRef.current === "thinking") replyStartedRef.current = true;
      else if (talkRef.current === "off") tts.stop();   // 次を送ったら前の読み上げは止める
      return;
    }
    if (busy || !was) return;
    const latest = [...messagesRef.current].reverse().find((m) => m.role === "assistant");
    const reply = latest && !latest.streaming ? latest.content : "";
    if (talkRef.current === "thinking") {
      void speakThenListen(reply);
    } else if (talkRef.current === "off" && ttsReplyRef.current && reply.trim() && latest?.messageId !== lastSpokenRef.current) {
      lastSpokenRef.current = latest?.messageId;
      void tts.speak(reply);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);
  // 会話を切り替えたら、読み上げと会話モードを止める（最初の発言で会話が作られたときは止めない）
  const prevConvRef = useRef(convId);
  useEffect(() => {
    const previous = prevConvRef.current;
    prevConvRef.current = convId;
    if (!previous || previous === convId) return;
    if (talkRef.current !== "off") stopTalk(); else tts.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId]);

  // ---- 送信待ちの要望（生成中に送ったもの。サーバーが持ち、回答が終わったら送る） ----
  type QueueState = { items: QueuedItem[]; generating_message_id: string | null; held: boolean };
  const { data: queue, refetch: refetchQueue } = useQuery({
    queryKey: ["chat-queue", convId],
    queryFn: () => api<QueueState>(`/chat/conversations/${convId}/queue`),
    enabled: Boolean(convId),
    refetchInterval: (q) => (busy || (q.state.data?.items.length ?? 0) > 0 ? 1500 : false),
  });
  const queueItems = convId ? queue?.items ?? [] : [];
  const [removedQueue, setRemovedQueue] = useState<{ ids: string[]; label: string } | null>(null);
  useEffect(() => {
    if (!removedQueue) return;
    const timer = window.setTimeout(() => setRemovedQueue(null), 10_000);
    return () => window.clearTimeout(timer);
  }, [removedQueue]);
  const queueCall = async <T,>(path: string, init: Parameters<typeof api>[1], fail: string): Promise<T | null> => {
    try {
      return await api<T>(`/chat/conversations/${convId}/queue${path}`, init);
    } catch (e) {
      show(e instanceof Error ? e.message : fail, "error");
      return null;
    } finally {
      void refetchQueue();
    }
  };
  const editQueued = async (id: string, content: string) =>
    (await queueCall(`/${id}`, { method: "PATCH", json: { content } }, "書き直せませんでした")) !== null;
  const deleteQueued = async (id: string) => {
    if (await queueCall(`/${id}`, { method: "DELETE" }, "取り消せませんでした")) {
      setRemovedQueue({ ids: [id], label: "送信待ちを 1 件取り消しました" });
    }
  };
  const steerQueued = async (id: string) => {
    if (await queueCall(`/${id}/steer`, { method: "POST" }, "今は伝えられませんでした")) show("作業中の OpenCode に伝えました");
  };
  const clearQueued = async () => {
    const result = await queueCall<{ ids: string[] }>("", { method: "DELETE" }, "取り消せませんでした");
    if (result?.ids.length) setRemovedQueue({ ids: result.ids, label: `送信待ちを ${result.ids.length} 件取り消しました` });
  };
  const restoreQueued = async () => {
    const ids = removedQueue?.ids ?? [];
    setRemovedQueue(null);
    for (const id of ids) await queueCall(`/${id}/restore`, { method: "POST" }, "戻せませんでした");
  };
  const sendQueuedNow = async () => { await queueCall("/dispatch", { method: "POST" }, "送れませんでした"); };
  // hold で貯めた要望: 画面の処理（自動判定・ワークフロー）が終わったら送る。止めた・失敗した後は送らない
  const deferredQueueRef = useRef(false);
  useEffect(() => {
    if (busy || !deferredQueueRef.current || !convId) return;
    deferredQueueRef.current = false;
    const last = [...messages].reverse().find((m) => m.role === "assistant");
    if (last && ["canceled", "failed", "interrupted"].includes(last.persistStatus ?? "")) return;
    // サーバーの生成で終わったなら、サーバーがもう送っている（409 は気にしない）
    void api(`/chat/conversations/${convId}/queue/dispatch`, { method: "POST" }).catch(() => undefined).finally(() => void refetchQueue());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, convId]);
  // サーバーが送信待ちを送り始めたら（この画面の外で始まった生成も）、会話を読み直して購読する
  const syncingRef = useRef(false);
  useEffect(() => {
    const generating = queue?.generating_message_id;
    if (!convId || busy || !generating || syncingRef.current) return;
    if (messages.some((m) => m.messageId === generating && !m.streaming)) return;   // 終わったのを知っている
    syncingRef.current = true;
    const cid = convId;
    void (async () => {
      try {
        const data = await api<{ conversation: ConversationSummary; messages: PersistMsg[] }>(`/chat/conversations/${cid}/messages`);
        setMessages(data.messages.map((m) => restoreMessage(cid, m)));
        const live = data.messages.find((m) => m.role === "assistant" && m.status === "generating");
        if (live) {
          setBusy(true);
          try { await streamMessage(live.id); } finally { setBusy(false); }
        }
      } catch {
        // 次の確認で拾い直す
      } finally {
        syncingRef.current = false;
        void refetchQueue();
        qc.invalidateQueries({ queryKey: ["chat-conversations"] });
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queue?.generating_message_id, busy, convId]);

  /** 実行完了までポーリングし、signal.display の値を集めて返す。 */
  const pollExecution = async (execId: number): Promise<string> => {
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const ex = await api<{ status: string; error: string; outputs: Record<string, { value?: unknown }> }>(
        `/workflow-runner/executions/${execId}`,
      );
      if (["QUEUED", "RUNNING", "WAITING"].includes(ex.status)) continue;
      const displays = Object.values(ex.outputs || {}).map((entry) => entry.value).filter((value) => value !== undefined && value !== null).map((value) => typeof value === "string" ? value : JSON.stringify(value, null, 2));
      if (ex.status === "SUCCEEDED")
        return displays.length ? displays.join("\n\n") : "✅ 実行が完了しました（表示ノードなし）";
      return `❌ 実行が ${ex.status} で終了しました${ex.error ? `: ${ex.error}` : ""}${displays.length ? `\n\n${displays.join("\n\n")}` : ""}`;
    }
    return "⏱ 実行が長時間続いています。実行履歴で状態を確認してください。";
  };

  /** 自動ビルド（生成→検証→登録→実行→修正）を WS で実行しログを流す。 */
  const autoBuild = (gen: GenData, useExisting: boolean) => {
    if (busy) return;
    setBusy(true);
    append({
      role: "assistant",
      content: "",
      kind: "build",
      build: { lines: ["🚀 自動ビルドを開始します"], status: "RUNNING", done: false },
      streaming: true,
    });
    const ws = new WebSocket(wsUrl("/chat/build"));
    wsRef.current = ws;
    const push = (line: string, extra?: Partial<BuildState>) =>
      patchLast((m) => ({
        ...m,
        build: m.build && { ...m.build, ...extra, lines: [...m.build.lines, line] },
      }));
    ws.onopen = () =>
      ws.send(
        JSON.stringify({
          goal: gen.goal,
          name: gen.name,
          base_url: baseUrl,
          model,
          definition: useExisting ? gen.definition : undefined,
          run_check: true,
        }),
      );
    ws.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      if (d.type === "phase") {
        const label: Record<string, string> = {
          generate: `🧠 設計中（${d.attempt} 回目）`,
          validate: "🔍 定義を検証中",
          register: `📝 ワークフロー #${d.workflow_id} として登録しました`,
          run: "▶ 動作確認のため実行中...",
          check: d.status === "SUCCEEDED" ? "✅ 動作確認 OK" : `⚠️ 実行結果: ${d.status}`,
        };
        push(label[d.phase] ?? d.phase, d.workflow_id ? { workflowId: d.workflow_id } : undefined);
      } else if (d.type === "log") {
        push(d.message);
      } else if (d.type === "done") {
        const ok = d.status === "SUCCEEDED" || d.status === "REGISTERED";
        push(
          ok
            ? `🎉 完了: 「${d.name}」を登録${d.status === "SUCCEEDED" ? "し、動作確認に成功" : ""}しました`
            : "⚠️ 自動修正の上限に達しました。登録済みの定義をエディタで確認・修正してください",
          { status: d.status, workflowId: d.workflow_id ?? undefined, done: true, quality: d.quality ?? undefined },
        );
      } else if (d.type === "error") {
        push(`⚠️ ${d.message}`, { status: "FAILED", done: true });
      }
    };
    ws.onclose = () => {
      patchLast((m) => ({ ...m, streaming: false, build: m.build && { ...m.build, done: true } }));
      setBusy(false);
    };
    ws.onerror = () => ws.close();
  };

  const registerOnly = async (gen: GenData) => {
    try {
      const res = await api<{ id: number; name: string }>("/chat/register-workflow", {
        method: "POST",
        json: { name: gen.name, definition: gen.definition },
      });
      show(`「${res.name}」を登録しました`);
      navigate(`/workflows/${res.id}`);
    } catch (e) {
      show(e instanceof Error ? e.message : "登録に失敗しました", "error");
    }
  };

  const currentMode = MODES.find((m) => m.id === effectiveMode)!;
  // 管理下のモデルは接続先を意識させない（どのポートで動くかは ControlDeck が決める）。
  // 外部エンドポイントだけ、区別が付くようURLを添える。
  const modelOptions =
    endpoints?.flatMap((ep) => ep.models.map((m) => {
      const external = ep.external_models?.find((item) => item.id === m);
      return {
        base: ep.base_url, model: m, managed: !!ep.managed, external: !!external,
        // auto は特定モデルを固定しない仮想モデル。転送先は ControlDeck が決める。
        label: external ? `外部｜${external.name}`
          : m === "auto" ? "自動（Models で選んだランタイム）" : ep.managed ? m : `${m} — ${ep.base_url}`,
      };
    })) ?? [];
  const localOptions = modelOptions.filter((o) => !o.external);
  const externalOptions = modelOptions.filter((o) => o.external);
  const externalSelected = externalOptions.find((o) => o.base === baseUrl && o.model === model);
  const hasContent = Boolean(input.trim()) || attachments.some((a) => (a.kind === "image" || a.kind === "archive") && a.status === "ready");
  const showComposerStatus = talk !== "off" || asr.phase === "installing" || asr.active || busy || genStats !== null;

  // 検索系（Web・論文・Deep Research・Research）は「調査」1 つにまとめ、深さは入力欄の下で選ぶ
  const modeOptions = MODES
    .filter((item) => (!item.needsEdit || can("workflows.edit")) && (item.id !== "code" || opencodeAvailable)
      && !SEARCH_MODES.includes(item.id))
    .map((item): { id: string; icon: string; label: string; hint: string } => ({ id: item.id, icon: item.icon, label: item.label, hint: item.short }));
  modeOptions.splice(1, 0, { id: "investigate", icon: "🔎", label: "調査", hint: "Web・論文を調べて根拠つきで回答（深さは自動か指定）" });
  const showDepth = modeChoice === "investigate";
  // OpenCode の会話（道具を使った回答がある）か、いま OpenCode で送るなら、権限の札を出す
  const showPermissionPill = opencodeAvailable && (effectiveMode === "code" || inCodeConversation);
  const currentPermission = PERMISSION_MODES.find((m) => m.id === permissionMode) ?? PERMISSION_MODES[0];
  // プランは OpenCode を使う場面でだけ効く（チャット・調査では関係ない）
  const planActive = opencodeAvailable && codeAgent === "plan";
  const planIgnored = planActive && effectiveMode !== "code";   // Chat などを固定している
  const lastProjectPath = [...messages].reverse().find((m) => m.projectPath)?.projectPath ?? "";
  const planFolder = codeProject === "__folder__" ? codeFolderPath
    : codeProject === "__new__" ? (codeNewName.trim() || lastProjectPath.split("/").pop() || "") : codeProject;
  const latestAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const latestCodeTurnId = [...messages].reverse().find((m) => m.role === "assistant" && m.answeredBy === "code" && m.messageId)?.messageId;
  const canSteer = busy && Boolean(latestAssistant?.streaming && latestAssistant.messageId && latestAssistant.answeredBy === "code");
  // 前回の OpenCode の回答より後に、チャット・調査の発言があるか（引き継ぐものがあるか）
  const plusActions: PlusAction[] = [
    {
      id: "tts-reply", icon: "🔊", label: ttsReply ? "読み上げをやめる" : "回答を読み上げる（TTS 返答）",
      hint: ttsReply ? "回答の読み上げを止めます" : "回答が終わったら主要な文言を声で読みます。コード・表・URL は読みません",
      onSelect: toggleTtsReply,
    },
    {
      id: "talk", icon: "🗣", label: "会話モード",
      hint: "話して少し黙ると送信し、回答を声で返します。停止を押すまで交互に続きます",
      disabled: busy || asr.active || asr.phase === "installing", onSelect: startTalk,
    },
  ];
  if (opencodeAvailable && pendingHandoff && effectiveMode !== "code") {
    plusActions.push({
      id: "handoff", icon: <IconTerminal />, label: "この会話を OpenCode に引き継ぐ",
      hint: "ここまでのチャット・調査の結果（出典つき）を前提に、同じ会話で OpenCode が作業します",
      onSelect: () => {
        chooseMode("code");
        if (!input.trim()) setInput("ここまでの会話の内容をもとに実装してください。");
        window.setTimeout(() => composerRef.current?.focus({ preventScroll: true }), 50);
      },
    });
  }
  if (opencodeAvailable && inCodeConversation && effectiveMode === "code" && modeChoice !== "investigate") {
    plusActions.push({
      id: "investigate-for-code", icon: <IconSearch />, label: "調べて OpenCode に渡す",
      hint: "いったん調査に切り替えます。調べた結果は、次に OpenCode へ送るときに自動で引き継がれます",
      onSelect: () => {
        returnChoiceRef.current = storedChoice;
        setModeChoice("investigate");
        show("調査の結果は、次に OpenCode で送るときに引き継がれます");
        window.setTimeout(() => composerRef.current?.focus({ preventScroll: true }), 50);
      },
    });
  }
  const historyProps = {
    conversations,
    currentId: convId,
    busy,
    onOpen: (id: string) => void pickConversation(id),
    onNew: newConversation,
    onRename: renameConversation,
    onDelete: setDeletingConversation,
    onArchive: (conversation: ConversationSummary, archived: boolean) => void archiveConversation(conversation, archived),
    onSwipeDelete: swipeDeleteConversation,
    archivedConversations,
    onShowArchived: setShowArchived,
  };
  /** 失敗・中断した回答の「再試行」: 直前の自分の発言をもう一度送る。 */
  /** 失敗した最後の回答をやり直す: 直前の自分の発言を、付けていた画像ごと送り直す。
   * 入力欄の下書き・添付を巻き込まないよう、それがあるときは再試行を出さない。 */
  const retryFrom = (index: number) => {
    for (let i = index - 1; i >= 0; i -= 1) {
      const original = messages[i];
      if (original.role !== "user") continue;
      const imageIds = (original.images ?? [])
        .map((url) => decodeURIComponent(url.split("/").pop() ?? ""))
        .filter((id) => /^[0-9a-f]{16}\.(png|jpg|webp|gif)$/.test(id));
      if (imageIds.length && convIdRef.current) {
        // 画像を添付に戻してから送る（描き直しの後で送る。send は描画時点の添付を読む）
        const cid = convIdRef.current;
        setAttachments(imageIds.map((id) => ({ id, name: id, kind: "image" as const, status: "ready" as const, preview: attachmentUrl(cid, id) })));
        pendingResend.current = original.content;
      } else {
        void send(original.content);
      }
      return;
    }
  };
  const canRetry = !busy && !input.trim() && attachments.length === 0;

  return (
    <section
      data-assistant-shell
      aria-label={assistantName}
      className="flex h-full min-h-0 w-full min-w-0 max-w-full overflow-hidden overscroll-none bg-zinc-50 dark:bg-zinc-950"
    >
      {/* PC: 会話の履歴の列（畳める。状態は端末に覚える） */}
      {!historyCollapsed && (
        <aside data-assistant-history-sidebar className="hidden w-72 shrink-0 border-r border-zinc-200/80 bg-zinc-100/60 dark:border-zinc-800/80 dark:bg-zinc-900/60 lg:block">
          <ConversationHistory
            {...historyProps}
            header={<HistoryHeader action={
              <button type="button" onClick={() => setHistoryCollapsedSaved(true)} aria-label="履歴を閉じる" aria-expanded title="履歴を閉じる"
                className="-mr-1.5 grid h-11 w-11 shrink-0 place-items-center rounded-xl text-xl text-zinc-600 transition-colors hover:bg-zinc-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800">
                <IconHistoryPanel />
              </button>
            } />}
          />
        </aside>
      )}
      {/* 携帯: 上の行の履歴ボタンで左から出るシート */}
      <HistorySheet open={historyOpen} onClose={() => setHistoryOpen(false)}>
        <ConversationHistory {...historyProps} header={<HistoryHeader action={<HistoryCloseButton onClose={() => setHistoryOpen(false)} />} />} />
      </HistorySheet>
      {deletingConversation && (
        <ConfirmDialog
          title="会話を削除"
          message={`「${deletingConversation.title}」を削除します。発言・添付・会話の資料も消え、元に戻せません。`}
          confirmLabel="削除する"
          onConfirm={() => void confirmDeleteConversation()}
          onClose={() => setDeletingConversation(null)}
        />
      )}

      <div data-assistant-main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* 上の行。殻の上の行と同じ高さ・同じ面: ☰・履歴・題名（押して名前を変更）・モデル。 */}
        <div data-assistant-topbar className={SHELL_TOPBAR_CLASS}>
        <div className="flex h-14 min-w-0 items-center gap-1 px-3 md:px-5">
          <ShellMenuButton className="-ml-1.5" />
          <button
            type="button"
            onClick={toggleHistory}
            aria-label="会話の履歴"
            aria-haspopup="dialog"
            title="会話の履歴"
            data-assistant-history-button
            className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl text-xl text-zinc-600 transition-colors hover:bg-zinc-200/60 active:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:active:bg-zinc-700 md:-ml-1.5 ${historyCollapsed ? "" : "lg:hidden"}`}
          >
            <IconHistoryPanel />
          </button>
          <h1 className="min-w-0 flex-1 text-[15px] font-semibold leading-6 tracking-tight">
            {renamingTitle && convId ? (
              <TitleEditor
                initial={conversationTitle}
                label="会話名"
                onDone={(title) => {
                  setRenamingTitle(false);
                  if (title && title !== conversationTitle) void renameConversation(convId, title);
                }}
                className="h-10 w-full min-w-0 rounded-lg border border-accent-500 bg-white px-2 text-[15px] font-medium outline-none ring-2 ring-accent-500/20 dark:bg-zinc-900"
              />
            ) : convId ? (
              <button
                type="button"
                onClick={() => setRenamingTitle(true)}
                title="押して名前を変更"
                aria-label={`会話名: ${conversationTitle}（押して名前を変更）`}
                data-assistant-title
                className="block min-h-11 max-w-full truncate rounded-lg px-2 text-left transition-colors hover:bg-zinc-200/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 dark:hover:bg-zinc-800"
              >
                {conversationTitle}
              </button>
            ) : (
              <span data-assistant-title className="block truncate px-2">{NEW_CHAT_TITLE}</span>
            )}
          </h1>
          <button
            onClick={() => setShowSettings((v) => !v)}
            aria-label={showSettings ? "AI設定を閉じる" : "AI設定を開く"}
            aria-expanded={showSettings}
            title={model || "設定"}
            className={`flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-2.5 text-xs font-medium transition-colors ${
              showSettings ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-400" : "text-zinc-600 hover:bg-zinc-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800"
            }`}
          >
            <IconChip className="shrink-0 text-lg" />
            <span className="max-w-[5.5rem] truncate max-[359px]:hidden sm:max-w-[10rem]">{model || "設定"}</span>
          </button>
          {shell?.extras}
        </div>
        </div>

        {/* 設定パネル */}
        {showSettings && (
          <div className="grid max-h-[50dvh] min-w-0 shrink-0 gap-2.5 overflow-y-auto overflow-x-hidden overscroll-contain border-b border-zinc-200 bg-zinc-50/60 px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-900/60 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-500">LLM モデル</span>
              {modelOptions.length ? (
                <select
                  value={`${baseUrl}|${model}`}
                  onChange={(e) => {
                    const [b, m] = e.target.value.split("|");
                    setBaseUrl(b);
                    setModel(m);
                  }}
                  className="w-full min-w-0 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm"
                >
                  {!modelOptions.some((o) => o.base === baseUrl && o.model === model) && (
                    <option value={`${baseUrl}|${model}`}>{model || "モデルを選択"}</option>
                  )}
                  {localOptions.map((o) => (
                    <option key={`${o.base}|${o.model}`} value={`${o.base}|${o.model}`}>
                      {o.label}
                    </option>
                  ))}
                  {/* 外部のモデルはローカルと混ぜず、送信されることが分かる見出しの下に並べる。 */}
                  {externalOptions.length > 0 && (
                    <optgroup label="外部（フロンティア）— 外部へ送信">
                      {externalOptions.map((o) => (
                        <option key={`${o.base}|${o.model}`} value={`${o.base}|${o.model}`}>
                          {o.label}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
              ) : (
                <div className="flex min-w-0 gap-1.5">
                  <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://127.0.0.1:11434/v1" className="min-w-0 w-1/2 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
                  <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="llama3.2" className="min-w-0 w-1/2 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
                </div>
              )}
              {externalSelected && (
                <span role="note" className="mt-1 block rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                  外部へ送信: 会話・添付・検索や道具の結果が {externalSelected.label.replace(/^外部｜/, "").split(" / ")[0]} へ送られます。秘密の情報を扱う会話ではローカルのモデルを選んでください。
                </span>
              )}
            </label>
            {effectiveMode === "deep" && (
              <div className="space-y-2 sm:col-span-2">
                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="block">
                    <span className="mb-1 block text-xs text-zinc-500">Deep Research 検索深度</span>
                    <select value={deepDepth} onChange={(event) => setDeepDepth(event.target.value)} className="w-full rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm">
                      <option value="quick">クイック · 2周 / 8検索</option>
                      <option value="standard">標準 · 3周 / 16検索</option>
                      <option value="deep">詳細 · 4周 / 24検索</option>
                      <option value="exhaustive">徹底 · 最大6周 / 36検索</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-xs text-zinc-500">ローカルコードproject（任意）</span>
                    <input value={deepProjectPath} onChange={(event) => setDeepProjectPath(event.target.value)} placeholder="files.allowed_roots 配下の絶対path" className="w-full rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
                  </label>
                </div>
                <fieldset>
                  <legend className="mb-1 text-xs text-zinc-500">調査ソース</legend>
                  <div className="flex flex-wrap gap-1.5">
                    {[
                      ["web", "Web / PDF"], ["academic", "学術"], ["github", "GitHub"],
                      ["direct", "直接URL"], ["rag", "添付 / RAG"], ["local_code", "ローカルコード"],
                      ["patent", "特許"], ["market", "市場"],
                    ].map(([value, label]) => (
                      <label key={value} className="flex min-h-9 items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2 text-xs dark:border-zinc-700 dark:bg-zinc-900">
                        <input
                          type="checkbox"
                          checked={deepSources.includes(value)}
                          onChange={(event) => setDeepSources((current) => event.target.checked
                            ? [...current, value]
                            : current.filter((item) => item !== value))}
                        />
                        {label}
                      </label>
                    ))}
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">添付PDF・文書は会話RAGから再利用します。ローカルコードは許可root配下を読み取り専用で静的解析します。</p>
                </fieldset>
              </div>
            )}
          </div>
        )}

        {/* メッセージ */}
        <div
          ref={scrollRef}
          onScroll={(event) => {
            const element = event.currentTarget;
            stickToBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
          }}
          data-assistant-messages
          className="min-h-0 min-w-0 flex-1 touch-pan-y space-y-4 overflow-x-hidden overflow-y-auto overscroll-contain px-3 py-5 [-webkit-overflow-scrolling:touch] sm:px-5"
        >
          {/* いまの回答のリストだけ。動いている間か、終わっていない項目があるときに出す */}
          <TodoFloat todos={latestAssistant?.todos} tools={latestAssistant?.tools}
            active={busy || Boolean(latestAssistant?.todos?.some((t) => t.status !== "completed" && t.status !== "cancelled"))} />
          {messages.length === 0 && (
            <div data-assistant-empty className="mx-auto max-w-md pt-[12vh] text-center">
              <p aria-hidden className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent-50 text-2xl text-accent-600 dark:bg-accent-600/15 dark:text-accent-300">{modeChoice === "auto" ? "✦" : currentMode.icon}</p>
              <p className="mt-4 text-xl font-semibold tracking-tight">{assistantName}</p>
              <p className="mt-1.5 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
                {modeChoice === "auto" ? "何でも聞いてください。内容に合わせて、対話・検索・調査・ワークフローを自動で選びます。" : `${currentMode.label} — ${currentMode.hint}`}
              </p>
              {effectiveMode === "gen" && (
                <p className="mt-3 rounded-xl bg-zinc-100 p-3 text-left text-xs leading-relaxed text-zinc-500 dark:bg-zinc-800/60 dark:text-zinc-400">
                  例:「毎朝 8 時に arXiv で LLM の論文を検索して要約を Discord に送る」
                  「URL を入力すると本文を要約してナレッジに登録するフロー」
                </p>
              )}
            </div>
          )}
          <div className="mx-auto max-w-3xl space-y-5">
            {messages.map((m, i) => (
              <MessageBubble
                key={m.messageId ?? i}
                msg={m}
                conversationId={convId}
                canRevertChanges={!busy && !!m.messageId && m.messageId === latestCodeTurnId}
                onChangesReverted={(id) => {
                  setMessages((prev) => prev.map((x) => (x.messageId === id ? { ...x, reverted: true } : x)));
                  show("この回の変更を取り消しました");
                }}
                onRetry={canRetry && i === messages.length - 1 ? () => retryFrom(i) : undefined}
                onRegister={registerOnly}
                onAutoBuild={autoBuild}
                onOpen={(id) => navigate(`/workflows/${id}`)}
                onReference={(referenceId) => {
                  setInput((current) => `${current}${current && !current.endsWith(" ") ? " " : ""}[${referenceId}] `);
                }}
                onAnswerPermission={answerPermission}
                onBuildFromPlan={!busy && !hasContent && i === messages.length - 1 && m.agent === "plan" && m.persistStatus === "completed"
                  ? () => { setCodeAgent("build"); rememberPrefs({ agent: "build" }); void send("このプランで実装してください。", "build", "code"); } : undefined}
                editing={!!m.messageId && editingId === m.messageId}
                actionsDisabled={busy}
                onAction={(action) => void onMessageAction(m, action)}
                onResend={(text) => m.messageId && void resendEdited(m.messageId, text)}
                resendDisabled={busy}
                onCancelEdit={() => setEditingId(null)}
              />
            ))}
            {deleting && (
              <ConfirmDialog
                title="メッセージを削除"
                message={deleting.role === "user" ? "この発言と、その回答を削除します。元に戻せません。" : "この回答を削除します。元に戻せません。"}
                confirmLabel="削除する"
                onConfirm={() => void confirmDelete()}
                onClose={() => setDeleting(null)}
              />
            )}
          </div>
        </div>

        {/* 入力欄 */}
        <div
          data-assistant-composer
          className="assistant-composer-safe min-w-0 max-w-full shrink-0 bg-transparent px-3 pt-2 sm:px-5"
        >
          <div className="mx-auto max-w-3xl">
          {effectiveMode === "run" && (
            <select
              value={runTarget}
              onChange={(e) => setRunTarget(e.target.value ? Number(e.target.value) : "")}
              className="mb-2 w-full rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            >
              <option value="">実行するワークフローを選択...</option>
              {workflows?.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          )}
          {effectiveMode === "code" && (
            <div className="mb-2 flex min-w-0 gap-1.5">
              <select
                aria-label="作業フォルダ（OpenCode のプロジェクト）"
                value={codeProject}
                onChange={(e) => setCodeProject(e.target.value)}
                className={`rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900 ${codeProject === "__new__" ? "w-32 shrink-0" : "min-w-0 flex-1"}`}
              >
                <option value="__new__">➕ 新規作成</option>
                {(codeProjects?.projects ?? []).map((p) => (
                  <option key={p.name} value={p.name}>📚 {p.name}</option>
                ))}
                {codeFolderPath && <option value="__folder__">📁 {codeFolderPath}</option>}
              </select>
              {codeProject === "__new__" && (
                <input
                  value={codeNewName}
                  onChange={(e) => setCodeNewName(e.target.value)}
                  placeholder="プロジェクト名（例: my-app）"
                  className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900"
                />
              )}
              <button
                type="button"
                onClick={() => setCodePicker(true)}
                aria-label="フォルダから選択"
                title="フォルダから選択（CodeDEV外はコピーして取り込みます）"
                className="grid w-10 shrink-0 place-items-center rounded-lg border border-zinc-300 text-base dark:border-zinc-700"
              >📁</button>
            </div>
          )}
          {codePicker && (
            <FilePicker
              mode="dir"
              title="プロジェクトフォルダを選択（CodeDEV外はコピーして取り込み）"
              initialPath={codeFolderPath || undefined}
              onSelect={(p) => { setCodeFolderPath(p); setCodeProject("__folder__"); setCodePicker(false); }}
              onClose={() => setCodePicker(false)}
            />
          )}
          {convId && <QueuedRequests items={queueItems} held={Boolean(queue?.held)} removed={removedQueue}
            onEdit={editQueued} onDelete={(id) => void deleteQueued(id)} onRestore={() => void restoreQueued()}
            onSendNow={() => void sendQueuedNow()} onClear={() => void clearQueued()}
            canSteer={canSteer} onSteer={(id) => void steerQueued(id)} />}
          {attachments.length > 0 && (
            <div className="mb-1.5 flex flex-wrap gap-1.5">
              {attachments.map((a) => (
                <span key={a.id} className="relative flex items-center gap-1.5 overflow-hidden rounded-full border border-zinc-200 bg-white py-1 pl-2.5 pr-1 text-[11px] text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                  {a.preview
                    ? <img src={a.preview} alt="" className="h-6 w-6 shrink-0 rounded object-cover" />
                    : <span aria-hidden>{a.kind === "image" ? "🖼️" : a.kind === "archive" ? "📦" : "📄"}</span>}
                  <span className="max-w-[10rem] truncate">{a.name}</span>
                  {a.status === "uploading" ? (
                    <span className="flex shrink-0 items-center gap-1 pr-1.5 tabular-nums text-zinc-500"
                      role="progressbar" aria-label={`${a.name}を送信中`} aria-valuemin={0} aria-valuemax={100}
                      aria-valuenow={Math.round((a.progress ?? 0) * 100)}>
                      {(a.progress ?? 0) >= 1
                        ? <span className="animate-pulse">{a.kind === "document" ? "資料に登録中…" : a.kind === "archive" ? "中身を確認中…" : "保存中…"}</span>
                        : <>{Math.round((a.progress ?? 0) * 100)}%</>}
                      {/* チップの下端の進捗バー。送り終わったら処理中の流れる帯にする */}
                      <span aria-hidden className="absolute inset-x-0 bottom-0 h-0.5 bg-zinc-100 dark:bg-zinc-800">
                        <span className={`block h-full bg-accent-500 transition-[width] duration-200 ${(a.progress ?? 0) >= 1 ? "animate-pulse" : ""}`}
                          style={{ width: `${Math.max(4, Math.round((a.progress ?? 0) * 100))}%` }} />
                      </span>
                    </span>
                  ) : a.kind === "document" ? (
                    <span className="pr-1.5 text-[10px] text-emerald-600 dark:text-emerald-400">資料登録済</span>
                  ) : a.kind === "archive" ? (
                    <>
                      <span className="text-[10px] text-zinc-500">{a.files ?? 0} ファイル・送ると展開</span>
                      <button type="button" onClick={() => {
                        setAttachments((prev) => prev.filter((x) => x.id !== a.id));
                        // サーバーに置いた圧縮ファイル（最大 200 MiB）も消す
                        if (convIdRef.current) void api(`/chat/conversations/${convIdRef.current}/attachments/${a.id}`, { method: "DELETE" }).catch(() => {});
                      }}
                        aria-label={`${a.name}を取り消す`} className="grid h-5 w-5 place-items-center rounded-full text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800">×</button>
                    </>
                  ) : (
                    <button type="button" onClick={() => {
                      if (a.preview) URL.revokeObjectURL(a.preview);
                      setAttachments((prev) => prev.filter((x) => x.id !== a.id));
                      // 保存した PDF も消す（送らないものを OpenCode や調査に渡さない）
                      if (a.kind === "document" && /^[0-9a-f]{16}$/.test(a.id) && convIdRef.current) {
                        void api(`/chat/conversations/${convIdRef.current}/attachments/${a.id}`, { method: "DELETE" }).catch(() => {});
                      }
                    }}
                      aria-label={`${a.name}を取り消す`} className="grid h-5 w-5 place-items-center rounded-full text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800">×</button>
                  )}
                </span>
              ))}
            </div>
          )}
          <input ref={fileInputRef} type="file" multiple className="hidden"
            accept="image/png,image/jpeg,image/webp,image/gif,.pdf,.txt,.md,.markdown,.csv,.json,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.java,.go,.rs,.c,.cpp,.h,.sh,.html,.css,.toml,.sql,.zip,.7z,.rar,.tar,.tgz,.tar.gz,.tbz2,.tar.bz2,.txz,.tar.xz"
            onChange={(e) => { const picked = Array.from(e.target.files ?? []); e.target.value = ""; void attachFiles(picked); }} />
          {/* 入力カード（上が本文、下が道具の行: 添付・機能・音声・送信）。状態は
              カードの下に固定高で置き、表示が変わっても入力位置を動かさない。 */}
          {planActive && (
            /* プラン中はビルドではないことを、入力欄の真上ではっきり出す */
            <div data-plan-banner role="status"
              className="mb-2 flex w-full min-w-0 items-center gap-2 rounded-2xl border border-sky-300 bg-sky-50 px-3 py-1.5 text-xs text-sky-900 dark:border-sky-700 dark:bg-sky-950/50 dark:text-sky-100">
              <span aria-hidden className="text-base">📋</span>
              <span className="min-w-0 flex-1 leading-snug"><b>プラン</b> — {planIgnored ? "いまは OpenCode を使わない機能なので、プランは使われません。" : modeChoice === "investigate" ? "調べた結果をもとに計画します。" : "計画だけを立てます。"}{!planIgnored && <span data-plan-target> 作業フォルダ: {planFolder || "未選択（下で選んでください）"}。</span>}ファイルの編集や変更を伴うコマンドはしません。</span>
              <button type="button" onClick={() => changeCodeAgent("build")}
                className="min-h-9 shrink-0 rounded-lg border border-sky-300 bg-white px-2.5 text-xs font-semibold text-sky-800 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-900 dark:text-sky-100 dark:hover:bg-sky-800 [@media(pointer:coarse)]:min-h-11">ビルドに切り替える</button>
            </div>
          )}
          <div data-assistant-input-card data-agent={planActive ? "plan" : "build"} className={`w-full min-w-0 rounded-3xl border bg-white shadow-sm transition dark:bg-zinc-900 ${planActive
            ? "border-sky-400 ring-2 ring-sky-400/20 dark:border-sky-600"
            : "border-zinc-200 focus-within:border-accent-500/70 focus-within:ring-2 focus-within:ring-accent-500/15 dark:border-zinc-700"}`}>
            <textarea
              ref={composerRef}
              data-assistant-input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPaste={(e) => {
                // クリップボードの画像（スクリーンショット・コピーした画像）を添付にする。文字の貼り付けはそのまま。
                const images = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
                // Excel・Word などは文字と一緒に画像版も入れる。文字があれば文字の貼り付けとして扱う。
                if (images.length === 0 || e.clipboardData.getData("text/plain").trim()) return;
                e.preventDefault();
                void attachFiles(images);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send();
                } else if (e.key === "Tab" && e.shiftKey && showPermissionPill) {
                  // Claude Code と同じく Shift+Tab で権限モードを切り替える
                  e.preventDefault();
                  cyclePermissionMode();
                }
              }}
              rows={1}
              placeholder={
                planActive ? "プラン: 何を計画しますか（ファイルは変えません）"
                : effectiveMode === "gen"
                  ? "作りたいワークフローを日本語で説明..."
                  : effectiveMode === "run"
                    ? "ワークフローへの入力（{{trigger.message}}）..."
                    : "メッセージを入力..."
              }
              className="block max-h-40 w-full min-w-0 resize-none border-0 bg-transparent px-4 pb-1 pt-3.5 text-base leading-6 outline-none placeholder:text-zinc-400 sm:text-[15px]"
            />
            <div data-assistant-input-row className="flex w-full min-w-0 items-center gap-1 p-1.5">
              {talk !== "off" ? (
                <>
                  {/* 会話モード: 聞く → 考える → 話す を停止まで繰り返す */}
                  <span data-talk-phase={talk} className={`grid h-11 w-11 shrink-0 place-items-center rounded-full text-lg ${talk === "listening" ? "bg-emerald-50 dark:bg-emerald-950/40" : "bg-zinc-100 dark:bg-zinc-800"}`} aria-hidden>
                    {talk === "speaking" ? "🔊" : talk === "thinking" ? "💭" : "🗣"}
                  </span>
                  {talk === "listening"
                    ? <VoiceWave meter={asr.meter} pending={asr.pending} finishing={false} />
                    : <span className="min-w-0 flex-1 truncate px-1 text-sm text-zinc-600 dark:text-zinc-300">
                      {talk === "starting" ? "マイクを準備しています…" : talk === "thinking" ? "回答を待っています…" : "回答を読み上げています"}
                    </span>}
                  {talk === "speaking" && (
                    <button type="button" onClick={() => tts.stop()} aria-label="読み上げを飛ばして話す" title="読み上げを飛ばして話す"
                      className="min-h-11 shrink-0 rounded-full px-3 text-xs font-medium text-zinc-600 hover:bg-zinc-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 dark:text-zinc-300 dark:hover:bg-zinc-800">飛ばす</button>
                  )}
                  <button type="button" onClick={stopTalk} aria-label="会話モードを止める" title="会話モードを止める"
                    className="flex h-11 shrink-0 items-center gap-1.5 rounded-full bg-zinc-900 px-4 text-sm font-medium text-white shadow-sm transition hover:bg-zinc-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white">
                    <IconStop className="text-base" />停止
                  </button>
                </>
              ) : asr.active ? (
                <>
                  {/* 聞いている間（Claude のように波形と時間を出す）。✕ は取り消し（この回の文字を戻す）、✓ は終わり */}
                  <button type="button" onClick={cancelVoice} aria-label="音声入力を取り消す" title="取り消す（この回に入れた文字を戻す）"
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-lg text-zinc-600 hover:bg-zinc-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 dark:text-zinc-300 dark:hover:bg-zinc-800">✕</button>
                  <VoiceWave meter={asr.meter} pending={asr.pending} finishing={asr.phase === "finishing"} />
                  <button type="button" onClick={() => void asr.finish()} disabled={asr.phase !== "listening"}
                    aria-label="音声入力を終える" title="終える（文字は入力欄に残ります）"
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-accent-600 text-lg font-bold text-white shadow-sm transition hover:bg-accent-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 disabled:opacity-60">✓</button>
                </>
              ) : (
                <>
              <PlusMenu
                attachDisabled={busy}
                onAttach={() => fileInputRef.current?.click()}
                mode={modeChoice}
                modeOptions={modeOptions}
                detectedLabel={currentMode.label}
                routing={routing}
                onModeChange={(id) => chooseMode(id as AssistantModeChoice)}
                permission={opencodeAvailable ? { mode: permissionMode, editable: canChangePermissionMode, onChange: (m) => void changePermissionMode(m) } : null}
                agent={opencodeAvailable ? { mode: codeAgent, onChange: changeCodeAgent } : null}
                actions={plusActions}
              />
              <div className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
                {modeChoice === "auto" ? (
                  <span data-mode-detected aria-live="polite" className="min-w-0 truncate px-1 text-xs text-zinc-500 dark:text-zinc-400">
                    <span aria-hidden>✦ </span>自動 · {routing ? "計画中…" : currentMode.label}
                  </span>
                ) : (
                  <ComposerPill icon={modeOptions.find((o) => o.id === modeChoice)?.icon ?? "✦"}
                    label={modeOptions.find((o) => o.id === modeChoice)?.label ?? currentMode.label}
                    title="機能（＋で変更）" kind="mode" onClear={() => { if (returnChoiceRef.current) finishOneShot(); else chooseMode("auto"); }} />
                )}
                {planActive ? (
                  <ComposerPill icon="📋" label="プラン" tone="plan"
                    title="プラン: 計画だけ。ファイルの編集・変更を伴うコマンドはしません（押すと切替・PC は Shift+Tab）"
                    kind="plan" onClick={cyclePermissionMode} />
                ) : showPermissionPill && (
                  <ComposerPill icon={<span className="font-mono tracking-[-0.2em]">{currentPermission.icon}</span>}
                    label={currentPermission.label} tone={permissionMode === "auto" ? "accent" : "amber"}
                    title={canChangePermissionMode ? `OpenCode の権限: ${currentPermission.label}（押すと切替・PC は Shift+Tab）` : `OpenCode: ビルド（押すとプランへ）`}
                    kind="permission" onClick={cyclePermissionMode} />
                )}
                {ttsReply && (
                  <ComposerPill icon="🔊" label={tts.speaking ? "読み上げ中" : "読み上げ"} tone="accent" kind="tts-reply" compact
                    title={tts.speaking ? "いまの読み上げを止める" : "TTS 返答: 回答を声で読みます（× でやめる）"}
                    onClick={() => tts.stop()} onClear={toggleTtsReply} />
                )}
              </div>
              <button
                type="button"
                onClick={startVoice}
                disabled={asr.phase === "installing"}
                aria-label="音声で入力"
                title={asr.phase === "installing" ? "音声入力モデルを導入中" : "音声で入力（止めるまで聞き続けます）"}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-lg text-zinc-600 transition hover:bg-zinc-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 disabled:opacity-50 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                <IconMic />
              </button>
              {busy && runningJob && (
                <button type="button" onClick={() => void stopRunning()} aria-label="止める" title="止める"
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-zinc-900 text-white shadow-sm transition hover:bg-zinc-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white">
                  <IconStop className="text-base" />
                </button>
              )}
              {/* 生成中も、書いてあれば送れる（送信待ちに入り、回答が終わったら送る） */}
              {(!busy || !runningJob || hasContent) && (
                <button
                  onClick={() => void send()}
                  disabled={!hasContent}
                  aria-label={busy ? "送信待ちに入れる" : "送信"}
                  title={busy ? "送信待ちに入れる（回答が終わったら送ります）" : "送信（Enter）"}
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-accent-600 text-white shadow-sm transition hover:bg-accent-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 disabled:bg-zinc-200 disabled:text-zinc-400 disabled:shadow-none dark:disabled:bg-zinc-800 dark:disabled:text-zinc-600"
                >
                  {busy && !hasContent ? <span className="animate-pulse">…</span> : <IconSend className="text-base" />}
                </button>
              )}
                </>
              )}
            </div>
          </div>
          {/* 状態の行。指で使う端末（キーボードの案内が要らない）では、出すものが無いとき行ごと畳む
              （iPhone で入力欄の下が空きすぎていた。2026-11-17） */}
          <div data-assistant-composer-status aria-live="polite"
            className={`min-w-0 items-center gap-2 px-3 text-[10px] leading-none text-zinc-500 ${
              showComposerStatus || showDepth ? "flex h-8" : "hidden h-8 [@media(pointer:fine)]:flex"}`}>
            {showDepth && (
              <label className="flex shrink-0 items-center gap-1">
                <span>深さ</span>
                <select aria-label="調べる深さ" value={researchDepth} onChange={(e) => { const depth = e.target.value as ResearchDepth; setResearchDepth(depth); rememberPrefs({ depth }); }}
                  className="h-7 rounded-lg border border-zinc-300 bg-white px-1.5 text-[11px] text-zinc-700 outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200">
                  <option value="auto">自動{researchDepth === "auto" && displayedDecision && SEARCH_MODES.includes(displayedDecision.mode) ? `（${MODES.find((m) => m.id === displayedDecision.mode)?.label}）` : ""}</option>
                  <option value="web">速い（Web）</option>
                  <option value="research">標準（Web と論文）</option>
                  <option value="deep">深い（レポート・数分）</option>
                  <option value="academic">論文だけ</option>
                </select>
              </label>
            )}
            {showComposerStatus ? (talk !== "off" ? (
              <span className="min-w-0 truncate">{asr.phase === "installing" ? "初回の音声入力モデルを導入中…" : asr.phase === "permission" ? "マイクの許可を待っています…"
                : talk === "listening" ? "会話モード: 話し終えて少し黙ると送ります · 停止で終わる" : talk === "thinking" ? "会話モード: 回答を待っています" : "会話モード: 読み上げ中（飛ばすとすぐ話せます）"}</span>
            ) : asr.phase === "installing" || asr.active ? (
              <span className="min-w-0 truncate">{asr.phase === "installing" ? "初回の音声入力モデルを導入中…" : asr.phase === "permission" ? "マイクの許可を待っています…"
                : asr.listening ? "話した区切りごとに入力欄へ入れます · ✓ で終わる（最長 60 分）" : "残りを文字にしています…"}</span>
            ) : (
              <GenStatsBadge stats={genStats} busy={busy} />
            )) : (
              <span className="min-w-0 truncate [@media(pointer:coarse)]:hidden">Enterで送信 · Shift+Enterで改行</span>
            )}
          </div>
          {/* 行を畳んだときは、ホームバーの帯の分だけ空ける（帯が無い・WebView が短いときは最小限） */}
          {!(showComposerStatus || showDepth) && (
            <div aria-hidden className="assistant-composer-gap hidden [@media(pointer:coarse)]:block" />
          )}
          </div>
        </div>
      </div>
    </section>
  );
}

/** トークン数の短縮表記（3214 → 3.2K）。 */
function fmtTok(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10000) return `${(n / 1000).toFixed(1)}K`;
  return `${Math.round(n / 1000)}K`;
}

/** 生成統計の左下表示: 思考状態 / tok/s / コンテキスト使用量。
 * ヒント文と同じ行高に収まる枠なしのプレーン表示（入力欄の位置を動かさない）。 */
const PHASE_LABEL: Record<string, string> = {
  loading: "モデル準備中", waiting: "応答待ち", prefill: "プリフィル中", thinking: "思考中", answer: "生成中", answering: "生成中",
  tool: "道具を実行中", background: "裏のエージェント作業中", retry: "再試行待ち", code: "OpenCode 実行中",
};

function GenStatsBadge({ stats, busy }: {
  stats: GenStats | null;
  busy: boolean;
}) {
  // いまの段階に入ってからの秒数（プリフィルやモデルの読み込みは速度が出ないので、動いていることを時間で見せる）
  const [startedAt, setStartedAt] = useState(() => Date.now());
  const [, tick] = useState(0);
  const phaseKey = `${stats?.phase ?? ""}:${stats?.detail ?? ""}`;
  useEffect(() => setStartedAt(Date.now()), [phaseKey]);
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  const row = "num flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap";
  const sep = <span className="text-zinc-300 dark:text-zinc-600">·</span>;
  const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  if (!stats) {
    if (!busy) return null;
    return (
      <span className={row} data-gen-phase="sending">
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-500" />送信中{sep}{elapsed} 秒
      </span>
    );
  }
  const done = stats.phase === "done" || !busy;
  const label = done ? "完了" : stats.phase === "tool" && stats.detail ? `${stats.detail} を実行中` : PHASE_LABEL[stats.phase] ?? "生成中";
  const dot = done ? "bg-zinc-400" : stats.phase === "thinking" ? "animate-pulse bg-violet-500"
    : ["loading", "waiting", "prefill", "retry"].includes(stats.phase) ? "animate-pulse bg-amber-500" : "animate-pulse bg-emerald-500";
  const used = (stats.promptTokens ?? 0) + stats.genTokens;
  const pct = stats.contextMax ? Math.min(100, (used / stats.contextMax) * 100) : 0;
  return (
    <span className={`${row} ${done ? "opacity-60" : ""}`} data-gen-phase={done ? "done" : stats.phase} title={stats.detail || undefined}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      {label}
      {!done && <>{sep}{elapsed} 秒</>}
      {stats.tokPerSec > 0 && <>{sep}{stats.tokPerSec.toFixed(1)} tok/s</>}
      {stats.contextMax ? (
        <>{sep}<span className="flex items-center gap-1.5" title={`コンテキスト ${used.toLocaleString()} / ${stats.contextMax.toLocaleString()} トークン`}>
          <span className="h-1 w-10 shrink-0 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
            <span className="block h-full rounded-full bg-accent-500 transition-[width] duration-500" style={{ width: `${pct}%` }} />
          </span>
          {fmtTok(used)}/{fmtTok(stats.contextMax)}
        </span></>
      ) : stats.genTokens > 0 ? (
        <>{sep}<span>{fmtTok(stats.genTokens)} tok</span></>
      ) : null}
    </span>
  );
}

/** 思考プロセス（推論モデル・折り畳み）。2026-11-18 利用者の依頼:
 *  見出しのタップで開閉し、開いた欄の中のタップでも閉じる（長い思考の下から上へ戻らずに済む）。
 *  考え始めたら 1 回だけ自動で開き、回答が始まったら自動で閉じる（利用者が触っていれば、どちらもしない）。
 *  閉じたら会話の欄の中だけをスクロールして見出しの位置まで戻す（iPhone の外側の画面は動かさない）。
 *  欄の中のタップは、ダブルクリックや文字の選択の途中では閉じない（コピーできるように）。 */
function ThinkingBlock({ text, thinking, flat = false }: { text: string; thinking: boolean; /** 作業のカードの 1 行として出す */ flat?: boolean }) {
  const [open, setOpen] = useState(thinking);
  const touched = useRef(false);
  const autoOpened = useRef(thinking);
  const head = useRef<HTMLButtonElement>(null);
  const pendingClose = useRef<number | null>(null);
  useEffect(() => {
    if (touched.current) return;
    if (thinking && !autoOpened.current) {
      autoOpened.current = true;
      setOpen(true);
    } else if (!thinking) {
      setOpen(false);   // 回答が始まった（または回答済み）
    }
  }, [thinking]);
  useEffect(() => () => { if (pendingClose.current) window.clearTimeout(pendingClose.current); }, []);
  const scrollBackToHead = () => {
    const button = head.current;
    const scroller = button?.closest<HTMLElement>("[data-assistant-messages]");
    if (!button || !scroller) return;
    const top = button.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    if (top < 0) scroller.scrollTop += top - 8;
  };
  const close = () => {
    touched.current = true;
    setOpen(false);
    window.requestAnimationFrame(scrollBackToHead);
  };
  const toggle = () => {
    touched.current = true;
    if (open) close();
    else setOpen(true);
  };
  return (
    <div data-thinking className={flat ? "px-3 py-0.5" : "rounded-lg bg-zinc-100/70 px-2.5 py-1.5 dark:bg-zinc-800/70"}>
      <button ref={head} type="button" aria-expanded={open} onClick={toggle}
        className="flex min-h-9 w-full items-center gap-1 text-left text-xs font-medium text-zinc-600 focus:outline-none focus-visible:underline dark:text-zinc-400">
        <span aria-hidden className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
        💭 思考プロセス{thinking ? "（考え中…）" : ""}
      </button>
      {open && (
        <p data-thinking-body title="タップで閉じる"
          onClick={(event) => {
            // 1 回目のクリックだけで判断しない（ダブルクリックで語を選ぶ途中で閉じない）
            if (pendingClose.current) window.clearTimeout(pendingClose.current);
            pendingClose.current = null;
            if (event.detail !== 1) return;
            pendingClose.current = window.setTimeout(() => {
              pendingClose.current = null;
              if (!window.getSelection()?.toString()) close();
            }, 280);
          }}
          className="mb-1.5 mt-0.5 cursor-pointer whitespace-pre-wrap text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">
          {text}
        </p>
      )}
    </div>
  );
}

/** 作業の間に置ける本文の終わりの位置（0 は置けない）。古い記録は位置が合わないので従来どおり下にまとめる。
 *  後ろに残る分（許可されなかった旨など、並びに載らない追記）は呼び出し側が下に出す。
 *  コードの囲みが区切りをまたぐと、別々に表示して崩れるので、まとめて出す。 */
function inlineTextEnd(msg: Msg): number {
  const segments = msg.timeline ?? [];
  let at = 0;
  for (const [i, seg] of segments.entries()) {
    if (seg.k !== "x") continue;
    if (seg.from !== at || seg.to > msg.content.length) return 0;
    const fences = msg.content.slice(seg.from, seg.to).match(/^ {0,3}(```|~~~)/gm)?.length ?? 0;
    if (i < segments.length - 1 && fences % 2 === 1) return 0;
    at = seg.to;
  }
  return at;
}

/** OpenCode の回答を届いた順に並べる。続く思考と道具は 1 枚のカードに、本文は作業の前後の位置に出す
 *  （2026-11-18 利用者「チャットウィンドウの視認性を向上させて」。段階ごとの文が 1 段落につながっていた）。
 *  許可の確認は最後に出す。 */
function CodeTimeline({ msg, textEnd, onAnswer }: {
  msg: Msg; textEnd: number; onAnswer: (requestId: string, decision: "once" | "always" | "reject") => Promise<void>;
}) {
  const segments = msg.timeline ?? [];
  const tools = new Map((msg.tools ?? []).map((t) => [t.id, t]));
  const thinking = msg.thinking ?? "";
  const lastThinking = segments.reduce((last, seg, i) => (seg.k === "t" ? i : last), -1);
  const blocks: ReactNode[] = [];
  let steps: ReactNode[] = [];
  let stepsFrom = 0;   // カードの鍵は最初の項目の位置（本文が続いても作り直さず、開いた行を保つ）
  const addStep = (i: number, step: ReactNode) => {
    if (!steps.length) stepsFrom = i;
    steps.push(step);
  };
  const flushSteps = () => {
    if (steps.length) blocks.push(<div key={`steps-${stepsFrom}`} data-code-steps className={CODE_STEPS_CARD}>{steps}</div>);
    steps = [];
  };
  segments.forEach((seg, i) => {
    if (seg.k === "u") {
      const tool = tools.get(seg.id);
      if (tool) addStep(i, <ToolRow key={tool.id} tool={tool} />);
      return;
    }
    if (seg.k === "n") {
      addStep(i, <p key={`steer-${i}`} data-code-steer className="px-3 py-2 text-xs leading-relaxed text-zinc-700 dark:text-zinc-200">
        <span className="font-semibold text-accent-700 dark:text-accent-300">↪ 途中で伝えた</span> <span className="whitespace-pre-wrap">{seg.text}</span></p>);
      return;
    }
    if (seg.k === "x") {
      const text = msg.content.slice(seg.from, seg.to);
      if (!textEnd || !text.trim()) return;
      flushSteps();
      blocks.push(
        <div key={`text-${i}`} data-code-text>
          <Suspense fallback={<p className="whitespace-pre-wrap leading-relaxed">{text}</p>}>
            <MarkdownMessage text={text} />
          </Suspense>
        </div>,
      );
      return;
    }
    const text = thinking.slice(seg.from, seg.to);
    if (!text.trim()) return;
    // 考えている途中なのは、最後の思考で、その後ろに道具も本文も無いときだけ
    const active = Boolean(msg.streaming) && i === lastThinking && i === segments.length - 1;
    addStep(i, <ThinkingBlock key={`think-${i}`} text={text} thinking={active} flat />);
  });
  flushSteps();
  // 並びに載っていない道具（古い記録・途中から購読）も落とさない
  const listed = new Set(segments.flatMap((seg) => (seg.k === "u" ? [seg.id] : [])));
  const rest = (msg.tools ?? []).filter((t) => !listed.has(t.id));
  return (
    <>
      {blocks}
      {rest.length > 0 && <OpenCodeActivity tools={rest} permissions={[]} onAnswer={onAnswer} />}
      {(msg.permissions?.length ?? 0) > 0 && <OpenCodeActivity tools={[]} permissions={msg.permissions} onAnswer={onAnswer} />}
    </>
  );
}

function MessageBubble({
  msg,
  onRegister,
  onAutoBuild,
  onOpen,
  onReference,
  onAnswerPermission,
  onBuildFromPlan,
  editing = false,
  actionsDisabled = false,
  onAction,
  onResend,
  onCancelEdit,
  onRetry,
  resendDisabled = false,
  conversationId = "",
  canRevertChanges = false,
  onChangesReverted,
}: {
  conversationId?: string;
  /** 最新の OpenCode の回で、作業が止まっているときだけ */
  canRevertChanges?: boolean;
  onChangesReverted?: (messageId: string) => void;
  /** 失敗・中断した回答をやり直す（直前の自分の発言を送り直す）。生成中は渡さない。 */
  onRetry?: () => void;
  /** プランの回答の後で、そのプランをビルドで実装する */
  onBuildFromPlan?: () => void;
  resendDisabled?: boolean;
  editing?: boolean;
  actionsDisabled?: boolean;
  onAction: (action: MessageAction) => void;
  onResend: (text: string) => void;
  onCancelEdit: () => void;
  msg: Msg;
  onRegister: (g: GenData) => void;
  onAutoBuild: (g: GenData, useExisting: boolean) => void;
  onOpen: (id: number) => void;
  onReference: (referenceId: string) => void;
  onAnswerPermission: (messageId: string, requestId: string, decision: "once" | "always" | "reject") => Promise<void>;
}) {
  if (msg.role === "user") {
    return (
      <div className="flex min-w-0 justify-end">
        <div className="flex min-w-0 max-w-[85%] flex-col items-end gap-1.5">
          {msg.archives && msg.archives.length > 0 && (
            <div className="flex flex-wrap justify-end gap-1.5" aria-label="送った圧縮ファイル">
              {msg.archives.map((name) => (
                <span key={name} className="flex max-w-full items-center gap-1 rounded-full border border-zinc-200 bg-white px-2.5 py-1 text-[11px] text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                  <span aria-hidden>📦</span><span className="min-w-0 truncate">{name}</span>
                </span>
              ))}
            </div>
          )}
          {msg.images && msg.images.length > 0 && (
            <div className="flex flex-wrap justify-end gap-1.5">
              {msg.images.map((src) => (
                <a key={src} href={src} target="_blank" rel="noopener noreferrer">
                  <img src={src} alt="添付した画像" loading="lazy"
                    className="max-h-40 max-w-[12rem] rounded-xl border border-zinc-200 object-contain dark:border-zinc-700" />
                </a>
              ))}
            </div>
          )}
          {editing ? (
            <MessageEditor initial={msg.content} disabled={resendDisabled} onSubmit={onResend} onCancel={onCancelEdit} />
          ) : (
            <div className="min-w-0 whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent-600 px-3.5 py-2.5 text-[15px] leading-6 text-white [overflow-wrap:anywhere]">
              {msg.content}
            </div>
          )}
          {!editing && (
            <MessageActions align="end" disabled={actionsDisabled}
              actions={msg.messageId ? ["copy", "edit", "fork", "delete"] : ["copy"]} onSelect={onAction} />
          )}
        </div>
      </div>
    );
  }
  const unsettled = !msg.streaming && (msg.persistStatus === "failed" || msg.persistStatus === "canceled" || msg.persistStatus === "interrupted")
    ? msg.persistStatus : null;
  const textEnd = msg.timeline?.length && msg.messageId ? inlineTextEnd(msg) : 0;
  const restText = msg.content.slice(textEnd);
  const assistantActions: MessageAction[] = (msg.messageId && !msg.streaming ? ["copy", "fork", "delete"] as MessageAction[] : ["copy"] as MessageAction[])
    .filter((action) => action !== "copy" || !!msg.content);
  return (
    <div data-assistant-message={msg.persistStatus ?? ""} className="flex min-w-0 flex-col items-start gap-0.5">
    <div className="flex w-full min-w-0 justify-start">
      <div className="w-full min-w-0 space-y-2 break-words px-1 text-[15px] leading-7 [overflow-wrap:anywhere]">
        {msg.agent === "plan" && (
          <p data-plan-answer className="inline-flex items-center gap-1 rounded-full border border-sky-300 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold leading-5 text-sky-800 dark:border-sky-700 dark:bg-sky-950/50 dark:text-sky-200">
            <span aria-hidden>📋</span>プラン（ファイルは変えていません）
          </p>
        )}
        {msg.compaction && (
          <p className="text-[11px] text-zinc-500 dark:text-zinc-400" role="status">
            🗜️ 会話が長くなったため、古いやり取り{msg.compaction.folded_messages ? `（${msg.compaction.folded_messages}件）` : ""}を要約して続けました
            {msg.compaction.tokens_before && msg.compaction.tokens_after
              ? `：約${msg.compaction.tokens_before.toLocaleString()} → ${msg.compaction.tokens_after.toLocaleString()}トークン`
              : ""}
          </p>
        )}
        {msg.connectionState === "reconnecting" && (
          <p className="text-[11px] font-medium text-amber-600 dark:text-amber-400" role="status">接続を復旧しています。生成はサーバー側で継続中です…</p>
        )}
        {(msg.plan || msg.research || (msg.progress && msg.progress.length > 0)) && (
          /* 機能選択判断は選択結果のみ表示し、理由・計画・統計はタップで展開する */
          <details className="w-fit max-w-full rounded-xl border border-accent-200 bg-accent-50/50 px-3 py-1.5 open:w-full dark:border-accent-900 dark:bg-accent-950/20">
            <summary className="cursor-pointer text-xs font-semibold text-accent-700 dark:text-accent-300">
              {msg.progress?.[0]?.phase === "opencode" ? "⌨️ OpenCode" : `🧭 ${MODES.find((m) => m.id === msg.plan?.mode)?.label ?? "調査"}`}{msg.streaming ? "（実行中）" : ""}
            </summary>
            {msg.plan?.reason && (
              <p className="mt-2 text-[11px] text-zinc-600 dark:text-zinc-300">判断理由: {msg.plan.reason}</p>
            )}
            {msg.plan?.steps && msg.plan.steps.length > 0 && (
              <ol className="mt-2 space-y-1 text-[11px] text-zinc-600 dark:text-zinc-300">
                {msg.plan.steps.map((step, index) => <li key={`${step.tool}-${index}`}>{index + 1}. {step.tool === "web" ? "Web" : "学術"}: {step.query}</li>)}
              </ol>
            )}
            {msg.progress && msg.progress.length > 0 && (
              <p className="mt-2 text-[11px] text-zinc-500">最新: {msg.progress[msg.progress.length - 1].label}</p>
            )}
            {msg.research && (
              <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-zinc-600 dark:text-zinc-300 sm:grid-cols-4">
                <span>探索 {msg.research.rounds} round</span>
                <span>検索 {msg.research.search_calls} 回</span>
                <span>発見 {msg.research.sources_discovered} 件</span>
                <span>採用 {msg.research.sources_selected} 件</span>
                {msg.research.repositories_inspected > 0 && <span>GitHub {msg.research.repositories_inspected} repo</span>}
                {msg.research.coverage?.coverage_score !== undefined && <span>coverage {msg.research.coverage.coverage_score}%</span>}
                {msg.research.citation_metrics?.citation_coverage !== undefined && <span>引用段落 {Math.round(msg.research.citation_metrics.citation_coverage * 100)}%</span>}
                {msg.research.citation_metrics?.section_count !== undefined && <span>完結章 {msg.research.citation_metrics.completed_sections}/{msg.research.citation_metrics.section_count}</span>}
                {msg.research.context_profile?.requested_tokens && (
                  <span>CTX {Math.round(msg.research.context_profile.requested_tokens / 1024)}K {msg.research.context_profile.applied ? "適用" : "未適用"}</span>
                )}
              </div>
            )}
            {(msg.research?.citation_metrics?.possibly_truncated_sections?.length ?? 0) > 0 && (
              <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">未完結の可能性: {msg.research!.citation_metrics!.possibly_truncated_sections!.join("、")}</p>
            )}
          </details>
        )}
        {msg.timeline?.length && msg.messageId ? (
          // OpenCode: 届いた順に「思考 → 道具 → 思考 → …」。思考はひとまとまりごとに別の窓
          <CodeTimeline msg={msg} textEnd={textEnd} onAnswer={(requestId, decision) => onAnswerPermission(msg.messageId!, requestId, decision)} />
        ) : (
          <>
            {/* OpenCode: 道具の実行と許可の確認（タップで出力を開く・許可はボタンで答える） */}
            {msg.messageId && (
              <OpenCodeActivity tools={msg.tools} permissions={msg.permissions}
                onAnswer={(requestId, decision) => onAnswerPermission(msg.messageId!, requestId, decision)} />
            )}
            {/* 思考トレース（推論モデル・折り畳み） */}
            {msg.thinking && (
              <ThinkingBlock text={msg.thinking} thinking={Boolean(!msg.content && msg.streaming)} />
            )}
          </>
        )}
        {msg.streaming && !msg.content && !msg.thinking && !msg.build && (
          <p role="status" aria-label="回答を待っています" className="flex h-7 items-center gap-1">
            {[0, 1, 2].map((dot) => (
              <span key={dot} className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400" style={{ animationDelay: `${dot * 180}ms` }} />
            ))}
          </p>
        )}
        {restText && (
          <div>
            <Suspense fallback={<p className="whitespace-pre-wrap leading-relaxed">{restText}</p>}>
              <MarkdownMessage text={restText} />
            </Suspense>
            {msg.streaming && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-accent-500 align-middle" />}
          </div>
        )}
        {/* 本文を作業の間に出した後も、続いていることが分かるように（道具の後で長く考えている間など） */}
        {msg.streaming && msg.content && !restText && <span aria-hidden data-streaming-cursor className="block h-3.5 w-1.5 animate-pulse bg-accent-500" />}

        {!msg.streaming && msg.messageId && conversationId && (msg.changes?.length ?? 0) > 0 && (
          <OpenCodeChanges conversationId={conversationId} messageId={msg.messageId} changes={msg.changes!} reverted={msg.reverted}
            canRevert={canRevertChanges} onReverted={() => onChangesReverted?.(msg.messageId!)} />
        )}

        {/* 失敗・停止・中断: 本文が無くても、何が起きたかと理由を必ず出す。 */}
        {unsettled && <UnsettledNotice status={unsettled} error={msg.error} partial={!!msg.content} onRetry={onRetry} />}

        {/* 出典リスト（既定は最小化。タップで開閉） */}
        {msg.sources && msg.sources.length > 0 && (
          <details className="rounded-xl border border-zinc-200 bg-white px-2.5 py-2 dark:border-zinc-700 dark:bg-zinc-900">
            <summary className="cursor-pointer text-[11px] font-semibold text-zinc-500 dark:text-zinc-400">
              📚 参照文献（{msg.sources.length} 件）
            </summary>
            <ol className="mt-1.5 space-y-1">
              {msg.sources.map((s, i) => (
                <li key={s.reference_id ?? i} className="flex min-w-0 items-center gap-1.5 text-xs">
                  {s.reference_id && (
                    <span className="shrink-0 rounded-md bg-accent-50 px-1.5 py-0.5 font-mono font-semibold text-accent-700 dark:bg-accent-950/50 dark:text-accent-300">
                      [{s.reference_id}]
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate">
                    <a href={s.url} target="_blank" rel="noreferrer" className="text-accent-600 hover:underline dark:text-accent-400">
                      {s.title || s.url}
                    </a>
                    {s.source && <span className="ml-1 text-zinc-400">({s.source})</span>}
                  </span>
                  {s.reference_id && (
                    <button
                      type="button"
                      onClick={() => onReference(s.reference_id!)}
                      className="h-9 shrink-0 rounded-md px-1.5 font-medium text-zinc-500 hover:bg-zinc-100 hover:text-accent-700 focus:outline-none focus:ring-2 focus:ring-accent-500/40 dark:hover:bg-zinc-800 dark:hover:text-accent-300"
                      aria-label={`${s.reference_id}を入力欄で参照`}
                    >
                      参照
                    </button>
                  )}
                </li>
              ))}
            </ol>
          </details>
        )}

        {/* 生成プレビュー */}
        {msg.gen && (
          <div className="rounded-xl border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
            <p className="text-sm font-medium">⚙️ {msg.gen.name}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1">
              {msg.gen.definition.nodes.map((n, i) => {
                const def = NODE_TYPES[n.type];
                return (
                  <span key={n.id ?? i} className="flex items-center gap-1">
                    {i > 0 && <span className="text-zinc-300 dark:text-zinc-600">→</span>}
                    <span className="inline-flex items-center gap-1 rounded-md bg-zinc-100 px-1.5 py-0.5 text-[11px] dark:bg-zinc-800">
                      {def?.icon ?? "▢"} {n.name || def?.label || n.type}
                    </span>
                  </span>
                );
              })}
            </div>
            {msg.gen.quality && <QualityBadge q={msg.gen.quality} />}
            {msg.gen.warnings.length > 0 && (
              <p className="mt-2 whitespace-pre-wrap rounded-lg bg-amber-50 p-2 text-xs text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">
                {msg.gen.warnings.join("\n")}
              </p>
            )}
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {msg.gen.valid && (
                <button onClick={() => onRegister(msg.gen!)} className="rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700">
                  登録してエディタで開く
                </button>
              )}
              <button
                onClick={() => onAutoBuild(msg.gen!, msg.gen!.valid)}
                className="rounded-lg border border-accent-300 px-3 py-1.5 text-xs font-medium text-accent-700 hover:bg-accent-50 dark:border-accent-700 dark:text-accent-400 dark:hover:bg-accent-600/10"
              >
                🚀 自動ビルド（登録 → 動作確認 → 自動修正）
              </button>
            </div>
          </div>
        )}

        {/* ビルドログ */}
        {msg.build && (
          <div className="rounded-xl border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
            <ul className="space-y-1 text-xs leading-relaxed">
              {msg.build.lines.map((l, i) => (
                <li key={i} className="whitespace-pre-wrap">
                  {l}
                </li>
              ))}
            </ul>
            {!msg.build.done && <p className="mt-1.5 animate-pulse text-[11px] text-zinc-400">実行中...</p>}
            {msg.build.quality && <QualityBadge q={msg.build.quality} />}
            {msg.build.done && msg.build.workflowId != null && (
              <button
                onClick={() => onOpen(msg.build!.workflowId!)}
                className="mt-2 rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700"
              >
                エディタで開く
              </button>
            )}
          </div>
        )}
      </div>
    </div>
    {onBuildFromPlan && (
      <button type="button" data-build-from-plan onClick={onBuildFromPlan}
        className="mt-1 inline-flex min-h-10 items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 text-sm font-semibold text-white shadow-sm hover:bg-accent-700 [@media(pointer:coarse)]:min-h-11">
        <span aria-hidden>🛠️</span>このプランで作る（ビルドに切り替えて送る）
      </button>
    )}
    {!msg.streaming && assistantActions.length > 0 && (msg.content || msg.messageId) && <MessageActions align="start" disabled={actionsDisabled} actions={assistantActions} onSelect={onAction} />}
    </div>
  );
}

/** 最後まで出なかった回答の知らせ。失敗は赤（重大なエラー）、停止・中断は控えめな色。 */
function UnsettledNotice({ status, error, partial, onRetry }: {
  status: "failed" | "canceled" | "interrupted"; error?: string; partial: boolean; onRetry?: () => void;
}) {
  const failed = status === "failed";
  const title = failed
    ? (partial ? "回答の途中で失敗しました" : "回答を生成できませんでした")
    : status === "canceled" ? "回答を停止しました" : "回答が中断されました";
  const detail = error?.trim()
    || (status === "interrupted" ? "サーバーの再起動などで生成が途切れました。" : failed ? "原因は記録されていません。" : "");
  return (
    <div
      role={failed ? "alert" : "status"}
      data-assistant-unsettled={status}
      className={`rounded-2xl border px-3.5 py-3 text-sm leading-6 ${failed
        ? "border-red-200 bg-red-50 text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200"
        : "border-zinc-200 bg-zinc-100/70 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-200"}`}
    >
      <p className="font-semibold">{failed ? "⚠️ " : status === "canceled" ? "⏹ " : "⏸ "}{title}</p>
      {detail && <p className="mt-0.5 whitespace-pre-wrap break-words text-xs leading-relaxed opacity-90 [overflow-wrap:anywhere]">{detail}</p>}
      {onRetry && (
        <button type="button" onClick={onRetry}
          className={`mt-2 inline-flex min-h-11 items-center gap-1.5 rounded-xl border px-3.5 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 ${failed
            ? "border-red-300 bg-white text-red-700 hover:bg-red-100 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200 dark:hover:bg-red-900/40"
            : "border-zinc-300 bg-white text-zinc-800 hover:bg-zinc-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800"}`}>
          <span aria-hidden>↻</span>再試行
        </button>
      )}
    </div>
  );
}

/** 生成ワークフローの品質スコア表示（0-100 + 内訳 + 検証結果）。 */
function QualityBadge({ q }: { q: Quality }) {
  const color =
    q.score >= 85 ? "text-emerald-600 dark:text-emerald-400" :
    q.score >= 60 ? "text-amber-600 dark:text-amber-400" : "text-red-600 dark:text-red-400";
  const bar =
    q.score >= 85 ? "bg-emerald-500" : q.score >= 60 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="mt-2 rounded-lg border border-zinc-200 p-2 dark:border-zinc-700">
      <div className="flex items-center gap-2">
        <span className={`num text-sm font-bold ${color}`}>{q.score}</span>
        <span className="text-[11px] text-zinc-500">/100 · {q.label}</span>
        <div className="ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div className={`h-full rounded-full ${bar}`} style={{ width: `${q.score}%` }} />
        </div>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-[10px] text-zinc-400">内訳と検証結果</summary>
        <ul className="mt-1 space-y-0.5 text-[10px] text-zinc-500">
          {Object.entries(q.breakdown).map(([k, v]) => (
            <li key={k} className="flex justify-between"><span>{k}</span><span className="num">{v}</span></li>
          ))}
          {q.errors.map((e, i) => <li key={`e${i}`} className="text-red-500">⚠️ {e}</li>)}
          {q.warnings.map((w, i) => <li key={`w${i}`} className="text-amber-500">・{w}</li>)}
        </ul>
      </details>
    </div>
  );
}
