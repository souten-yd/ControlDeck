/** AI アシスタント — チャット / Web・学術検索 / Deep サーチ / ワークフロー生成・実行。
 *
 * - チャット: ローカル LLM（OpenAI 互換）とストリーミング対話
 * - Web検索/学術検索: 検索結果を根拠に回答（出典表示、SearXNG 対応）
 * - Deepサーチ: サブ質問分解→収集→引用付きレポート
 * - フロー生成: 目的→定義生成→登録→動作確認→自動修正（ビルドログ表示）
 * - フロー実行: 既存ワークフローをチャットから実行し signal.display を表示
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, wsUrl } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { IconMic, IconSend, IconStop, IconTrash, IconX } from "../../components/icons";
import { NODE_TYPES } from "./nodeTypes";
import type { WorkflowSummary } from "../../pages/Workflows";
import { detectAssistantMode, type AssistantMode as Mode, type AssistantModeChoice } from "./assistantMode";
import { useAssistantAsr } from "./useAssistantAsr";

const MODES: { id: Mode; icon: string; label: string; hint: string; needsEdit?: boolean }[] = [
  { id: "chat", icon: "💬", label: "チャット", hint: "LLM と自由に対話します" },
  { id: "web", icon: "🌐", label: "Web検索", hint: "Web 検索（DuckDuckGo / SearXNG）の結果を根拠に回答します" },
  { id: "academic", icon: "🎓", label: "学術検索", hint: "学術ソース串刺し検索（OpenAlex/arXiv 等）を根拠に回答します" },
  { id: "deep", icon: "🔬", label: "Deepサーチ", hint: "テーマを分解して収集し、引用付きレポートを生成します（数分かかります）" },
  { id: "research", icon: "🧭", label: "複合調査", hint: "LLMがWeb・学術検索を組み合わせ、不足を評価しながら要約します" },
  { id: "gen", icon: "⚙️", label: "フロー生成", hint: "やりたいことを書くと、ワークフローを自動生成 → 登録 → 動作確認 → 修正まで行います", needsEdit: true },
  { id: "run", icon: "▶", label: "フロー実行", hint: "既存のワークフローをチャットから実行し、結果を表示します" },
];

interface SourceItem { reference_id?: string; title: string; url: string; snippet?: string; source?: string; kind?: string }
interface Quality { score: number; label: string; breakdown: Record<string, number>; errors: string[]; warnings: string[] }
interface GenData { name: string; definition: { nodes: { id: string; type: string; name?: string }[]; edges: unknown[] }; valid: boolean; warnings: string[]; goal: string; quality?: Quality }
interface BuildState { lines: string[]; status: string; workflowId?: number; done: boolean; quality?: Quality }
interface ConversationSummary { id: string; title: string; updated_at: string }
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

interface PersistMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  status: string;
  job_id: string | null;
  model: string;
  meta?: { sources?: SourceItem[]; plan?: AssistantPlan; progress?: ResearchProgress[]; research?: DeepResearchResult };
}

interface Msg {
  role: "user" | "assistant";
  content: string;
  kind?: "text" | "sources" | "gen" | "build" | "run";
  sources?: SourceItem[];
  gen?: GenData;
  build?: BuildState;
  thinking?: string;
  streaming?: boolean;
  // 永続チャット（DB 会話）: assistant メッセージの ID と状態
  messageId?: string;
  persistStatus?: string; // generating / completed / failed / interrupted / canceled
  connectionState?: "live" | "reconnecting";
  plan?: AssistantPlan;
  progress?: ResearchProgress[];
  research?: DeepResearchResult;
}

const LS_KEY = "cd-assistant-settings";
const LS_CONV = "cd-chat-conversation"; // 永続チャットの会話 ID（本文は DB に保存）

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

export default function AssistantChat({ onClose }: { onClose: () => void }) {
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const saved = useRef(loadSettings()).current;
  const [modeChoice, setModeChoice] = useState<AssistantModeChoice>("auto");
  const [messages, setMessages] = useState<Msg[]>([]); // 会話本文は DB から復元
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [baseUrl, setBaseUrl] = useState<string>(saved.baseUrl || "http://127.0.0.1:11434/v1");
  const [model, setModel] = useState<string>(saved.model || "");
  // ⚙️で選択中runtimeのendpoint（最後に追従した値）。変わったら手動選択より優先して切り替える。
  const [autoBase, setAutoBase] = useState<string>(saved.autoBase || "");
  const [engine, setEngine] = useState<string>(saved.engine || "duckduckgo");
  const [searxngUrl, setSearxngUrl] = useState<string>(saved.searxngUrl || "");
  const [runTarget, setRunTarget] = useState<number | "">("");
  const [convId, setConvId] = useState<string>(() => localStorage.getItem(LS_CONV) || "");
  const [conversationTitle, setConversationTitle] = useState("新しい会話");
  const [resolvedDecision, setResolvedDecision] = useState<AssistantPlan | null>(null);
  const [routing, setRouting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  const { data: runtimeEnvironment } = useQuery({
    queryKey: ["runtime-environment"],
    queryFn: () => api<{ policy: { assistant_name: string } }>("/models/runtime-environment"),
  });
  const assistantName = runtimeEnvironment?.policy.assistant_name || "AIアシスタント";
  const { data: conversations } = useQuery({
    queryKey: ["chat-conversations"],
    queryFn: () => api<ConversationSummary[]>("/chat/conversations"),
  });

  useEffect(() => {
    const current = conversations?.find((conversation) => conversation.id === convId);
    if (current) setConversationTitle(current.title);
  }, [conversations, convId]);

  // 永続チャット: マウント時に DB 会話を復元し、生成中メッセージがあれば購読を再開する。
  // これにより、生成中にブラウザを閉じても回答はサーバー側で保存され、再度開くと続きが見える。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let id = convId;
      try {
        if (!id) {
          return; // 空の下書きは送信されるまでDBへ登録しない
        }
        const data = await api<{ messages: PersistMsg[] }>(`/chat/conversations/${id}/messages`);
        if (cancelled) return;
        const restored: Msg[] = data.messages.map((m) => ({
          role: m.role,
          content: m.content,
          thinking: m.thinking || undefined,
          messageId: m.id,
          persistStatus: m.status,
          streaming: m.status === "generating",
          sources: m.meta?.sources,
          kind: m.meta?.sources?.length ? "sources" : "text",
          plan: m.meta?.plan,
          progress: m.meta?.progress,
          research: m.meta?.research,
        }));
        if (restored.length > 0) setMessages(restored);
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
    localStorage.setItem(LS_KEY, JSON.stringify({ baseUrl, model, engine, searxngUrl, autoBase }));
  }, [baseUrl, model, engine, searxngUrl, autoBase]);

  // SearXNG は基本停止・使う時だけ起動。検索系モード + SearXNG 選択時に先読み起動して
  // 実際の検索でコールドスタート（2〜3 秒）を待たずに済むようにする
  useEffect(() => {
    const detected = modeChoice === "auto" ? detectAssistantMode(input, [], can("workflows.edit")).mode : modeChoice;
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
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // LLM エンドポイント自動検出
  const { data: endpoints } = useQuery({
    queryKey: ["llm-endpoints"],
    queryFn: () => api<{ base_url: string; models: string[]; managed?: boolean; selected?: boolean }[]>("/workflows/llm-endpoints"),
    staleTime: 60_000,
    enabled: can("workflows.edit"),
  });
  // ⚙️の選択runtime（selected=true）へ追従する。選択が前回追従時から変わったら
  // 保存済みendpointを上書きし、同じ選択のままなら手動指定を尊重する。
  useEffect(() => {
    if (!endpoints?.length) return;
    const preferred = endpoints.find((ep) => ep.selected) ?? endpoints[0];
    if (!preferred) return;
    if (preferred.base_url !== autoBase || !model) {
      setAutoBase(preferred.base_url);
      setBaseUrl(preferred.base_url);
      if (!preferred.models.includes(model)) setModel(preferred.models[0] ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoints]);

  // フロー実行モード用のワークフロー一覧
  const { data: workflows } = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api<WorkflowSummary[]>("/workflows"),
    enabled: can("workflows.run"),
  });

  const autoDecision = detectAssistantMode(input, workflows ?? [], can("workflows.edit"));
  const displayedDecision = input.trim() ? autoDecision : (resolvedDecision ?? autoDecision);
  const effectiveMode: Mode = modeChoice === "auto" ? displayedDecision.mode : modeChoice;

  const append = (m: Msg) => setMessages((prev) => [...prev, m]);
  const patchLast = (fn: (m: Msg) => Msg) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

  /** 永続チャット: assistant メッセージの生成を購読する（切断してもサーバーは継続）。
   * 対象メッセージ（messageId 一致）へ delta/thinking を書き込む。 */
  const streamMessage = (messageId: string) =>
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
        patchMsg((m) => ({
          ...m, content: m.content + content,
          thinking: (m.thinking ?? "") + thinking,
        }));
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
            patchMsg((m) => ({ ...m, content: d.content, thinking: d.thinking || undefined }));
          } else if (d.type === "delta") {
            pendingContent += d.content;
            scheduleFlush();
          } else if (d.type === "thinking") {
            pendingThinking += d.content;
            scheduleFlush();
          } else if (d.type === "sources") patchMsg((m) => ({ ...m, kind: "sources", sources: d.sources }));
          else if (d.type === "plan") patchMsg((m) => ({ ...m, plan: d.plan }));
          else if (d.type === "progress") patchMsg((m) => ({
            ...m, progress: [...(m.progress ?? []), { phase: d.phase, label: d.label, iteration: d.iteration, details: d.details }],
          }));
          else if (d.type === "done") {
            receivedDone = true;
            flush();
            patchMsg((m) => ({ ...m, streaming: false, persistStatus: d.status, connectionState: "live" }));
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

  const openConversation = async (id: string) => {
    if (!id || id === convId || busy) return;
    wsRef.current?.close();
    const data = await api<{ conversation: ConversationSummary; messages: PersistMsg[] }>(`/chat/conversations/${id}/messages`);
    const restored: Msg[] = data.messages.map((message) => ({
      role: message.role,
      content: message.content,
      thinking: message.thinking || undefined,
      messageId: message.id,
      persistStatus: message.status,
      streaming: message.status === "generating",
      sources: message.meta?.sources,
      kind: message.meta?.sources?.length ? "sources" : "text",
      plan: message.meta?.plan,
      progress: message.meta?.progress,
      research: message.meta?.research,
    }));
    localStorage.setItem(LS_CONV, id);
    setConvId(id);
    setConversationTitle(data.conversation.title);
    setMessages(restored);
    const generating = data.messages.find((message) => message.role === "assistant" && message.status === "generating");
    if (generating) {
      setBusy(true);
      streamMessage(generating.id).finally(() => setBusy(false));
    }
  };

  const newConversation = async () => {
    if (busy) return;
    localStorage.removeItem(LS_CONV);
    setConvId("");
    setConversationTitle("新しい会話");
    setMessages([]);
  };

  const renameConversation = async () => {
    const title = conversationTitle.trim();
    if (!convId || !title) return;
    await api(`/chat/conversations/${convId}`, { method: "PATCH", json: { title } });
    show("会話名を変更しました");
    qc.invalidateQueries({ queryKey: ["chat-conversations"] });
  };

  const deleteConversation = async () => {
    if (!convId) return;
    await api(`/chat/conversations/${convId}`, { method: "DELETE" });
    localStorage.removeItem(LS_CONV);
    setConvId("");
    setConversationTitle("新しい会話");
    setMessages([]);
    qc.invalidateQueries({ queryKey: ["chat-conversations"] });
    show("会話を削除しました");
  };

  const send = async (providedText?: string) => {
    const text = (providedText ?? input).trim();
    if (!text || busy) return;
    const decision = detectAssistantMode(text, workflows ?? [], can("workflows.edit"));
    let selectedMode = modeChoice === "auto" ? decision.mode : modeChoice;
    let selectedPlan: AssistantPlan | undefined;
    const selectedRunTarget = runTarget || decision.workflowId || "";
    if (selectedMode === "run" && selectedRunTarget === "") {
      show("実行するワークフローを選択してください", "error");
      return;
    }
    setInput("");
    setBusy(true);
    let buildContinues = false;
    const userMsg: Msg = { role: "user", content: text };
    append(userMsg);
    try {
      if (modeChoice === "auto" && selectedMode !== "gen" && selectedMode !== "run") {
        setRouting(true);
        selectedPlan = await api<AssistantPlan>("/chat/route", {
          method: "POST", json: { content: text, base_url: baseUrl, model },
        });
        selectedMode = selectedPlan.mode;
        setResolvedDecision(selectedPlan);
        setRouting(false);
      }
      if (selectedMode === "chat" || selectedMode === "web" || selectedMode === "academic" || selectedMode === "deep" || selectedMode === "research") {
        // 全て永続パス: サーバー側ジョブで検索/生成し DB 保存。ブラウザを閉じても継続・復元できる。
        let cid = convId;
        if (!cid) {
          const c = await api<{ id: string }>("/chat/conversations", { method: "POST" });
          cid = c.id;
          localStorage.setItem(LS_CONV, cid);
          setConvId(cid);
        }
        const res = await api<{ assistant_message_id: string }>(`/chat/conversations/${cid}/send`, {
          method: "POST",
          json: { content: text, mode: selectedMode, plan: selectedPlan, base_url: baseUrl, model, engine, searxng_url: searxngUrl },
        });
        const hint =
          selectedMode === "deep" ? "🔬 Deep サーチ中...（サーバー側で継続）" :
          selectedMode === "research" ? "🧭 調査計画に沿って複数ソースを確認中..." :
          selectedMode === "web" || selectedMode === "academic" ? "🔎 検索中...（サーバー側で継続）" : "";
        append({ role: "assistant", content: hint, streaming: true, messageId: res.assistant_message_id, persistStatus: "generating" });
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
        const { execution_id } = await api<{ execution_id: number }>(`/workflows/${selectedRunTarget}/run`, {
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
    }
  };

  const asr = useAssistantAsr({
    busy,
    onTranscript: async (text) => {
      setInput(text);
      await send(text);
    },
    onError: (message) => show(message, "error"),
  });

  /** 実行完了までポーリングし、signal.display の値を集めて返す。 */
  const pollExecution = async (execId: number): Promise<string> => {
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const ex = await api<{ status: string; error: string; context: Record<string, { output?: { display?: boolean; signal?: string; value?: string } }> }>(
        `/workflow-executions/${execId}`,
      );
      if (ex.status === "RUNNING") continue;
      const displays = Object.values(ex.context || {})
        .filter((e) => e?.output?.display)
        .map((e) => e.output!.value)
        .filter(Boolean);
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
      onClose();
      navigate(`/workflows/${res.id}`);
    } catch (e) {
      show(e instanceof Error ? e.message : "登録に失敗しました", "error");
    }
  };

  const currentMode = MODES.find((m) => m.id === effectiveMode)!;
  const modelOptions =
    endpoints?.flatMap((ep) => ep.models.map((m) => ({ base: ep.base_url, model: m }))) ?? [];

  return createPortal(
    <div
      className="fixed inset-0 z-50 max-w-full overflow-hidden bg-zinc-100 dark:bg-zinc-950"
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={assistantName}
        className="flex h-[100dvh] w-full min-w-0 max-w-full flex-col overflow-hidden bg-zinc-50 dark:bg-zinc-950"
      >
        {/* ヘッダー */}
        <div className="safe-top flex min-w-0 shrink-0 flex-wrap items-center gap-2 border-b border-zinc-200/80 bg-white/90 px-3 py-2.5 shadow-sm backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/90 sm:flex-nowrap sm:px-5">
          <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent-600 text-white shadow-sm" aria-hidden="true">✦</div>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold">{assistantName}</h2>
              <p className="truncate text-[11px] font-medium text-accent-700 dark:text-accent-300" aria-label="現在の機能" aria-live="polite">
                {modeChoice === "auto" ? routing ? "自動判定: 計画中…" : `自動判定: ${currentMode.label}` : `選択: ${currentMode.label}`}
              </p>
            </div>
          </div>
          <div className="order-last flex min-w-0 basis-full items-center gap-1 sm:order-none sm:w-[26rem] sm:basis-auto">
            <select
              value={modeChoice}
              onChange={(event) => setModeChoice(event.target.value as AssistantModeChoice)}
              aria-label="処理モード"
              title="処理モード"
              className="h-9 w-28 shrink-0 rounded-xl border border-zinc-200 bg-zinc-50 px-2 text-xs font-medium shadow-sm outline-none transition focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20 dark:border-zinc-700 dark:bg-zinc-800 sm:w-32"
            >
              <option value="auto">✦ 自動判定</option>
              {MODES.filter((item) => !item.needsEdit || can("workflows.edit")).map((item) => (
                <option key={item.id} value={item.id}>{item.icon} {item.label}</option>
              ))}
            </select>
            <select
              value={convId}
              onChange={(event) => void openConversation(event.target.value)}
              aria-label="会話を切替"
              className="h-9 min-w-0 flex-1 rounded-xl border border-zinc-200 bg-zinc-50 px-2.5 text-xs shadow-sm outline-none transition focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20 dark:border-zinc-700 dark:bg-zinc-800"
            >
              {!convId && <option value="">履歴を選択</option>}
              {(conversations ?? []).map((conversation) => <option key={conversation.id} value={conversation.id}>{conversation.title}</option>)}
            </select>
            <button
              type="button"
              onClick={() => void deleteConversation()}
              disabled={busy || !convId}
              aria-label="選択中の会話を削除"
              title="選択中の会話を削除"
              className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-zinc-500 transition hover:bg-red-50 hover:text-red-600 focus:outline-none focus:ring-2 focus:ring-red-500/30 disabled:opacity-40 dark:hover:bg-red-950/40"
            >
              <IconTrash className="text-lg" />
            </button>
          </div>
          {messages.length > 0 && (
            <button
              onClick={() => void newConversation()}
              disabled={busy}
              aria-label="新しい会話"
              className="hidden shrink-0 rounded-xl px-2.5 py-2 text-xs font-medium text-zinc-500 hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800 sm:block"
              title="新しい会話を開始"
            >
              ＋ 新規
            </button>
          )}
          <button
            onClick={() => setShowSettings((v) => !v)}
            title={model || "設定"}
            className={`ml-auto flex min-h-11 min-w-0 items-center gap-1 rounded-xl px-3 py-2 text-xs font-medium transition ${
              showSettings ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-400" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            }`}
          >
            <span className="shrink-0">🧩</span>
            <span className="max-w-[9rem] truncate">{model || "設定"}</span>
          </button>
          <button onClick={onClose} aria-label="AIアシスタントを閉じる" className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm font-semibold text-zinc-700 shadow-sm transition hover:border-zinc-400 hover:bg-zinc-100 focus:outline-none focus:ring-2 focus:ring-accent-500/40 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700">
            <IconX className="text-lg" />
            <span className="hidden sm:inline">閉じる</span>
          </button>
        </div>

        {/* 設定パネル */}
        {showSettings && (
          <div className="grid min-w-0 shrink-0 gap-2.5 overflow-x-hidden border-b border-zinc-200 bg-zinc-50/60 px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-800/40 sm:grid-cols-2">
            <label className="block sm:col-span-2">
              <span className="mb-1 block text-xs text-zinc-500">現在の会話名</span>
              <div className="flex min-w-0 flex-wrap gap-1.5 sm:flex-nowrap">
                <input value={conversationTitle} onChange={(event) => setConversationTitle(event.target.value)} maxLength={200}
                  className="min-w-0 basis-full rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:flex-1 sm:basis-auto sm:text-sm" />
                <button onClick={() => void renameConversation()} className="shrink-0 rounded-lg bg-zinc-200 px-3 py-1.5 text-xs font-medium dark:bg-zinc-700">名前を保存</button>
                <button onClick={() => void deleteConversation()} disabled={busy} className="shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-950/40">削除</button>
              </div>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-500">LLM モデル（稼働中サーバーを自動検出）</span>
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
                  {modelOptions.map((o) => (
                    <option key={`${o.base}|${o.model}`} value={`${o.base}|${o.model}`}>
                      {o.model} — {o.base}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="flex min-w-0 gap-1.5">
                  <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://127.0.0.1:11434/v1" className="min-w-0 w-1/2 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
                  <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="llama3.2" className="min-w-0 w-1/2 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
                </div>
              )}
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-500">Web 検索エンジン</span>
              <div className="flex min-w-0 gap-1.5">
                <select value={engine} onChange={(e) => setEngine(e.target.value)} className="rounded-lg border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="duckduckgo">DuckDuckGo</option>
                  <option value="searxng">SearXNG</option>
                </select>
                {engine === "searxng" && (
                  <input value={searxngUrl} onChange={(e) => setSearxngUrl(e.target.value)} placeholder="空 = ローカル既定 (127.0.0.1:8888)" className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
                )}
              </div>
            </label>
          </div>
        )}

        {/* メッセージ */}
        <div
          ref={scrollRef}
          onScroll={(event) => {
            const element = event.currentTarget;
            stickToBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
          }}
          className="min-w-0 flex-1 space-y-4 overflow-x-hidden overflow-y-auto px-3 py-5 sm:px-5"
        >
          {messages.length === 0 && (
            <div className="mx-auto max-w-md pt-10 text-center">
              <p className="text-3xl">{currentMode.icon}</p>
              <p className="mt-2 text-sm font-medium">{assistantName} · {currentMode.label}</p>
              <p className="mt-1 text-xs leading-relaxed text-zinc-400">{currentMode.hint}</p>
              {effectiveMode === "gen" && (
                <p className="mt-3 rounded-xl bg-zinc-50 p-3 text-left text-xs leading-relaxed text-zinc-500 dark:bg-zinc-800/60 dark:text-zinc-400">
                  例:「毎朝 8 時に arXiv で LLM の論文を検索して要約を Discord に送る」
                  「URL を入力すると本文を要約してナレッジに登録するフロー」
                </p>
              )}
            </div>
          )}
          <div className="mx-auto max-w-5xl space-y-4">
            {messages.map((m, i) => (
              <MessageBubble
                key={m.messageId ?? i}
                msg={m}
                onRegister={registerOnly}
                onAutoBuild={autoBuild}
                onOpen={(id) => { onClose(); navigate(`/workflows/${id}`); }}
                onReference={(referenceId) => {
                  setInput((current) => `${current}${current && !current.endsWith(" ") ? " " : ""}[${referenceId}] `);
                }}
              />
            ))}
          </div>
        </div>

        {/* 入力欄 */}
        <div className="safe-bottom min-w-0 max-w-full shrink-0 overflow-x-hidden border-t border-zinc-200 bg-white px-3 py-3 dark:border-zinc-800 dark:bg-zinc-900 sm:px-5">
          <div className="mx-auto max-w-5xl">
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
          <div className="flex w-full min-w-0 items-end gap-1.5 rounded-2xl border border-zinc-300 bg-zinc-50 p-1.5 shadow-sm transition-within focus-within:border-accent-500 focus-within:ring-2 focus-within:ring-accent-500/15 dark:border-zinc-700 dark:bg-zinc-800">
            <button
              type="button"
              onClick={() => void asr.toggle()}
              disabled={busy || !["idle", "error", "listening"].includes(asr.phase)}
              aria-label={asr.listening ? "音声認識を停止" : "音声で入力"}
              aria-pressed={asr.listening}
              title={asr.phase === "installing" ? "音声入力モデルを導入中" : asr.listening ? "停止して認識" : "音声で入力"}
              className={`relative grid h-11 w-11 shrink-0 place-items-center rounded-xl text-lg transition focus:outline-none focus:ring-2 focus:ring-accent-500/40 disabled:opacity-50 ${
                asr.listening ? "bg-red-600 text-white shadow-sm" : "text-zinc-600 hover:bg-white dark:text-zinc-300 dark:hover:bg-zinc-700"
              }`}
            >
              {asr.listening ? <IconStop /> : <IconMic />}
              {asr.listening && <span className="absolute inset-0 -z-10 rounded-xl bg-red-400 opacity-40" style={{ transform: `scale(${1 + asr.level * 0.35})` }} />}
            </button>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={input.includes("\n") ? 3 : 1}
              placeholder={
                effectiveMode === "gen"
                  ? "作りたいワークフローを日本語で説明..."
                  : effectiveMode === "run"
                    ? "ワークフローへの入力（{{trigger.message}}）..."
                    : "メッセージを入力..."
              }
              className="max-h-32 w-0 min-w-0 flex-1 resize-none border-0 bg-transparent px-2 py-2.5 text-base outline-none placeholder:text-zinc-400 sm:text-sm"
            />
            <button
              onClick={() => void send()}
              disabled={busy || !input.trim()}
              aria-label="送信"
              className="flex h-11 shrink-0 items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 text-sm font-semibold text-white shadow-sm hover:bg-accent-700 focus:outline-none focus:ring-2 focus:ring-accent-500/40 disabled:opacity-50"
            >
              {busy ? <span className="animate-pulse">…</span> : <IconSend className="text-base" />}
              <span className="hidden sm:inline">送信</span>
            </button>
          </div>
          <div className="mt-1.5 flex min-h-4 items-center justify-between px-1 text-[11px] text-zinc-500" aria-live="polite">
            <span>{asr.phase === "installing" ? "初回の音声入力モデルを導入中…" : asr.phase === "permission" ? "マイクの許可を待っています…" : asr.phase === "listening" ? "聞いています。1.2秒の無音で送信します" : asr.phase === "transcribing" ? "音声を文字に変換中…" : busy ? "回答中はマイクをミュートしています" : "Enterで送信 · Shift+Enterで改行"}</span>
            {asr.listening && <button onClick={() => asr.stop()} className="font-medium text-red-600">停止</button>}
          </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function MessageBubble({
  msg,
  onRegister,
  onAutoBuild,
  onOpen,
  onReference,
}: {
  msg: Msg;
  onRegister: (g: GenData) => void;
  onAutoBuild: (g: GenData, useExisting: boolean) => void;
  onOpen: (id: number) => void;
  onReference: (referenceId: string) => void;
}) {
  if (msg.role === "user") {
    return (
      <div className="flex min-w-0 justify-end">
        <div className="min-w-0 max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent-600 px-3.5 py-2.5 text-sm text-white [overflow-wrap:anywhere]">
          {msg.content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex min-w-0 justify-start">
      <div className="min-w-0 max-w-[96%] space-y-2 break-words rounded-2xl rounded-bl-md border border-zinc-200 bg-white px-4 py-3 text-sm shadow-sm [overflow-wrap:anywhere] dark:border-zinc-800 dark:bg-zinc-900 sm:max-w-[88%]">
        {msg.connectionState === "reconnecting" && (
          <p className="text-[11px] font-medium text-amber-600 dark:text-amber-400" role="status">接続を復旧しています。生成はサーバー側で継続中です…</p>
        )}
        {(msg.plan || msg.research || (msg.progress && msg.progress.length > 0)) && (
          <details className="rounded-xl border border-accent-200 bg-accent-50/50 px-3 py-2 dark:border-accent-900 dark:bg-accent-950/20" open={msg.streaming}>
            <summary className="cursor-pointer text-xs font-semibold text-accent-700 dark:text-accent-300">
              🧭 {msg.plan?.reason ?? "調査計画"}{msg.streaming ? "（実行中）" : ""}
            </summary>
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
        {/* 思考トレース（推論モデル・折り畳み） */}
        {msg.thinking && (
          <details className="rounded-lg bg-zinc-100/70 px-2.5 py-1.5 dark:bg-zinc-800/70" open={!msg.content && msg.streaming}>
            <summary className="cursor-pointer text-[11px] font-medium text-zinc-500">
              💭 思考プロセス{!msg.content && msg.streaming ? "（考え中…）" : ""}
            </summary>
            <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">{msg.thinking}</p>
          </details>
        )}
        {msg.content && (
          <p className="whitespace-pre-wrap leading-relaxed">
            {msg.content}
            {msg.streaming && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-accent-500 align-middle" />}
          </p>
        )}

        {/* 出典リスト */}
        {msg.sources && msg.sources.length > 0 && (
          <div className="rounded-xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-700 dark:bg-zinc-900">
            <p className="mb-1 text-[11px] font-semibold text-zinc-400">会話内文献（{msg.sources.length} 件）</p>
            <ol className="space-y-1">
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
          </div>
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
