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
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, wsUrl } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { IconX } from "../../components/icons";
import { NODE_TYPES } from "./nodeTypes";
import type { WorkflowSummary } from "../../pages/Workflows";

type Mode = "chat" | "web" | "academic" | "deep" | "gen" | "run";

const MODES: { id: Mode; icon: string; label: string; hint: string; needsEdit?: boolean }[] = [
  { id: "chat", icon: "💬", label: "チャット", hint: "LLM と自由に対話します" },
  { id: "web", icon: "🌐", label: "Web検索", hint: "Web 検索（DuckDuckGo / SearXNG）の結果を根拠に回答します" },
  { id: "academic", icon: "🎓", label: "学術検索", hint: "学術ソース串刺し検索（OpenAlex/arXiv 等）を根拠に回答します" },
  { id: "deep", icon: "🔬", label: "Deepサーチ", hint: "テーマを分解して収集し、引用付きレポートを生成します（数分かかります）" },
  { id: "gen", icon: "⚙️", label: "フロー生成", hint: "やりたいことを書くと、ワークフローを自動生成 → 登録 → 動作確認 → 修正まで行います", needsEdit: true },
  { id: "run", icon: "▶", label: "フロー実行", hint: "既存のワークフローをチャットから実行し、結果を表示します" },
];

interface SourceItem { title: string; url: string; snippet?: string; source?: string }
interface GenData { name: string; definition: { nodes: { id: string; type: string; name?: string }[]; edges: unknown[] }; valid: boolean; warnings: string[]; goal: string }
interface BuildState { lines: string[]; status: string; workflowId?: number; done: boolean }

interface Msg {
  role: "user" | "assistant";
  content: string;
  kind?: "text" | "sources" | "gen" | "build" | "run";
  sources?: SourceItem[];
  gen?: GenData;
  build?: BuildState;
  streaming?: boolean;
}

const LS_KEY = "cd-assistant-settings";
const LS_HISTORY = "cd-assistant-history";

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

function loadHistory(): Msg[] {
  try {
    const arr = JSON.parse(localStorage.getItem(LS_HISTORY) || "[]");
    // 復元時は途中状態を確定させる（streaming やビルド中フラグを落とす）
    return (Array.isArray(arr) ? arr : []).map((m: Msg) => ({
      ...m,
      streaming: false,
      build: m.build ? { ...m.build, done: true } : undefined,
    }));
  } catch {
    return [];
  }
}

export default function AssistantChat({ onClose }: { onClose: () => void }) {
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);
  const navigate = useNavigate();

  const saved = useRef(loadSettings()).current;
  const [mode, setMode] = useState<Mode>("chat");
  const [messages, setMessages] = useState<Msg[]>(loadHistory);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [baseUrl, setBaseUrl] = useState<string>(saved.baseUrl || "http://127.0.0.1:11434/v1");
  const [model, setModel] = useState<string>(saved.model || "");
  const [engine, setEngine] = useState<string>(saved.engine || "duckduckgo");
  const [searxngUrl, setSearxngUrl] = useState<string>(saved.searxngUrl || "");
  const [runTarget, setRunTarget] = useState<number | "">("");
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem(LS_KEY, JSON.stringify({ baseUrl, model, engine, searxngUrl }));
  }, [baseUrl, model, engine, searxngUrl]);

  // SearXNG は基本停止・使う時だけ起動。検索系モード + SearXNG 選択時に先読み起動して
  // 実際の検索でコールドスタート（2〜3 秒）を待たずに済むようにする
  useEffect(() => {
    if ((mode === "web" || mode === "deep") && engine === "searxng" && !searxngUrl) {
      api("/chat/searxng-warmup", { method: "POST" }).catch(() => {});
    }
  }, [mode, engine, searxngUrl]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    // 履歴を保存（直近 60 件・ストリーミング中フラグは保存しない）
    try {
      localStorage.setItem(
        LS_HISTORY,
        JSON.stringify(messages.slice(-60).map((m) => ({ ...m, streaming: false }))),
      );
    } catch {
      /* 容量超過などは無視 */
    }
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
    queryFn: () => api<{ base_url: string; models: string[] }[]>("/workflows/llm-endpoints"),
    staleTime: 60_000,
    enabled: can("workflows.edit"),
  });
  useEffect(() => {
    if (!model && endpoints?.length && endpoints[0].models.length) {
      setBaseUrl(endpoints[0].base_url);
      setModel(endpoints[0].models[0]);
    }
  }, [endpoints, model]);

  // フロー実行モード用のワークフロー一覧
  const { data: workflows } = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api<WorkflowSummary[]>("/workflows"),
    enabled: mode === "run",
  });

  const append = (m: Msg) => setMessages((prev) => [...prev, m]);
  const patchLast = (fn: (m: Msg) => Msg) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

  /** WS 経由で LLM 応答をストリーミングし、最後のメッセージへ書き足す。 */
  const streamLLM = (history: { role: string; content: string }[]) =>
    new Promise<void>((resolve) => {
      const ws = new WebSocket(wsUrl("/chat/stream"));
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ messages: history, base_url: baseUrl, model }));
      ws.onmessage = (ev) => {
        const data = JSON.parse(ev.data);
        if (data.type === "delta") patchLast((m) => ({ ...m, content: m.content + data.content }));
        else if (data.type === "error") patchLast((m) => ({ ...m, content: m.content + `\n⚠️ ${data.message}` }));
      };
      ws.onclose = () => {
        patchLast((m) => ({ ...m, streaming: false }));
        resolve();
      };
      ws.onerror = () => ws.close();
    });

  const textHistory = (extra: Msg[]) =>
    [...messages, ...extra]
      .filter((m) => (m.kind ?? "text") === "text" || m.kind === "sources")
      .slice(-12)
      .map((m) => ({ role: m.role, content: m.content }));

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    if (mode === "run" && runTarget === "") {
      show("実行するワークフローを選択してください", "error");
      return;
    }
    setInput("");
    setBusy(true);
    const userMsg: Msg = { role: "user", content: text };
    append(userMsg);
    try {
      if (mode === "chat") {
        append({ role: "assistant", content: "", streaming: true });
        await streamLLM([
          { role: "system", content: "あなたは Control Deck の AI アシスタントです。日本語で簡潔に答えてください。" },
          ...textHistory([userMsg]),
        ]);
      } else if (mode === "web" || mode === "academic") {
        append({ role: "assistant", content: "🔎 検索中...", streaming: true });
        const res = await api<{ results: SourceItem[] }>("/chat/search", {
          method: "POST",
          json: { query: text, mode, engine, searxng_url: searxngUrl, base_url: baseUrl, model },
        });
        const sources = (res.results || []).slice(0, 10);
        if (!sources.length) {
          patchLast((m) => ({ ...m, content: "検索結果が見つかりませんでした。", streaming: false }));
          return;
        }
        patchLast((m) => ({ ...m, content: "", kind: "sources", sources }));
        const ctx = sources
          .map((s, i) => `[${i + 1}] ${s.title}\n${s.snippet || ""}\n${s.url}`)
          .join("\n\n");
        await streamLLM([
          {
            role: "system",
            content:
              "以下の検索結果を根拠に日本語で回答してください。主張には [番号] で出典を付けること。検索結果にない内容は推測と明示すること。\n\n" +
              ctx,
          },
          { role: "user", content: text },
        ]);
      } else if (mode === "deep") {
        append({ role: "assistant", content: "🔬 Deep サーチ中...（分解 → 検索 → 本文収集 → 統合。数分かかることがあります）", streaming: true });
        const res = await api<{ report: string; sources: { n: number; title: string; url: string }[] }>(
          "/chat/search",
          {
            method: "POST",
            json: { query: text, mode: "deep", engine, searxng_url: searxngUrl, base_url: baseUrl, model },
          },
        );
        patchLast((m) => ({
          ...m,
          content: res.report,
          kind: "sources",
          sources: res.sources.map((s) => ({ title: `[${s.n}] ${s.title}`, url: s.url })),
          streaming: false,
        }));
      } else if (mode === "gen") {
        append({ role: "assistant", content: "⚙️ ワークフローを設計中...", streaming: true });
        const res = await api<Omit<GenData, "goal">>("/chat/generate-workflow", {
          method: "POST",
          json: { goal: text, base_url: baseUrl, model },
        });
        patchLast((m) => ({
          ...m,
          content: res.valid
            ? `「${res.name}」の設計ができました。内容を確認して登録してください。`
            : `設計しましたが検証で問題が見つかりました。「自動ビルド」なら修正しながら登録まで進められます。`,
          kind: "gen",
          gen: { ...res, goal: text },
          streaming: false,
        }));
      } else if (mode === "run") {
        const wf = workflows?.find((w) => w.id === runTarget);
        append({ role: "assistant", content: `▶ 「${wf?.name ?? runTarget}」を実行中...`, kind: "run", streaming: true });
        const { execution_id } = await api<{ execution_id: number }>(`/workflows/${runTarget}/run`, {
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
      setBusy(false);
    }
  };

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
          { status: d.status, workflowId: d.workflow_id ?? undefined, done: true },
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

  const currentMode = MODES.find((m) => m.id === mode)!;
  const modelOptions =
    endpoints?.flatMap((ep) => ep.models.map((m) => ({ base: ep.base_url, model: m }))) ?? [];

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-[2px] sm:items-center"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label="AI アシスタント"
        className="flex h-[94dvh] w-screen flex-col rounded-t-2xl bg-white shadow-xl dark:bg-zinc-900 sm:h-[88dvh] sm:w-[760px] sm:rounded-2xl"
      >
        {/* ヘッダー */}
        <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-800">
          <h2 className="text-base font-semibold">✨ AI アシスタント</h2>
          {messages.length > 0 && (
            <button
              onClick={() => {
                setMessages([]);
                localStorage.removeItem(LS_HISTORY);
              }}
              disabled={busy}
              className="rounded-lg px-2 py-1.5 text-xs text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-50 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
              title="会話履歴をクリア"
            >
              🗑 クリア
            </button>
          )}
          <button
            onClick={() => setShowSettings((v) => !v)}
            className={`ml-auto rounded-lg px-2.5 py-1.5 text-xs font-medium ${
              showSettings ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-400" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            }`}
          >
            {model ? `🧩 ${model}` : "⚙ 設定"}
          </button>
          <button onClick={onClose} aria-label="閉じる" className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <IconX />
          </button>
        </div>

        {/* 設定パネル */}
        {showSettings && (
          <div className="grid gap-2.5 border-b border-zinc-200 bg-zinc-50/60 px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-800/40 sm:grid-cols-2">
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
                  className="w-full rounded-lg border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-900"
                >
                  {modelOptions.map((o) => (
                    <option key={`${o.base}|${o.model}`} value={`${o.base}|${o.model}`}>
                      {o.model} — {o.base}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="flex gap-1.5">
                  <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://127.0.0.1:11434/v1" className="w-1/2 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-900" />
                  <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="llama3.2" className="w-1/2 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-900" />
                </div>
              )}
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-500">Web 検索エンジン</span>
              <div className="flex gap-1.5">
                <select value={engine} onChange={(e) => setEngine(e.target.value)} className="rounded-lg border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="duckduckgo">DuckDuckGo</option>
                  <option value="searxng">SearXNG</option>
                </select>
                {engine === "searxng" && (
                  <input value={searxngUrl} onChange={(e) => setSearxngUrl(e.target.value)} placeholder="空 = ローカル既定 (127.0.0.1:8888)" className="flex-1 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 dark:border-zinc-700 dark:bg-zinc-900" />
                )}
              </div>
            </label>
          </div>
        )}

        {/* モード切替 */}
        <div className="flex gap-1.5 overflow-x-auto border-b border-zinc-200 px-4 py-2 dark:border-zinc-800">
          {MODES.filter((m) => !m.needsEdit || can("workflows.edit")).map((m) => (
            <button
              key={m.id}
              onClick={() => setMode(m.id)}
              title={m.hint}
              className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium transition ${
                mode === m.id
                  ? "bg-accent-600 text-white"
                  : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
              }`}
            >
              {m.icon} {m.label}
            </button>
          ))}
        </div>

        {/* メッセージ */}
        <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {messages.length === 0 && (
            <div className="mx-auto max-w-md pt-10 text-center">
              <p className="text-3xl">{currentMode.icon}</p>
              <p className="mt-2 text-sm font-medium">{currentMode.label}</p>
              <p className="mt-1 text-xs leading-relaxed text-zinc-400">{currentMode.hint}</p>
              {mode === "gen" && (
                <p className="mt-3 rounded-xl bg-zinc-50 p-3 text-left text-xs leading-relaxed text-zinc-500 dark:bg-zinc-800/60 dark:text-zinc-400">
                  例:「毎朝 8 時に arXiv で LLM の論文を検索して要約を Discord に送る」
                  「URL を入力すると本文を要約してナレッジに登録するフロー」
                </p>
              )}
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble key={i} msg={m} onRegister={registerOnly} onAutoBuild={autoBuild} onOpen={(id) => { onClose(); navigate(`/workflows/${id}`); }} />
          ))}
        </div>

        {/* 入力欄 */}
        <div className="border-t border-zinc-200 px-4 py-3 dark:border-zinc-800 safe-bottom">
          {mode === "run" && (
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
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={input.includes("\n") ? 3 : 1}
              placeholder={
                mode === "gen"
                  ? "作りたいワークフローを日本語で説明..."
                  : mode === "run"
                    ? "ワークフローへの入力（{{trigger.message}}）..."
                    : "メッセージを入力..."
              }
              className="max-h-32 flex-1 resize-none rounded-xl border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900"
            />
            <button
              onClick={send}
              disabled={busy || !input.trim()}
              className="rounded-xl bg-accent-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50"
            >
              {busy ? "…" : "送信"}
            </button>
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
}: {
  msg: Msg;
  onRegister: (g: GenData) => void;
  onAutoBuild: (g: GenData, useExisting: boolean) => void;
  onOpen: (id: number) => void;
}) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent-600 px-3.5 py-2.5 text-sm text-white">
          {msg.content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[92%] space-y-2 rounded-2xl rounded-bl-md border border-zinc-200 bg-zinc-50/60 px-3.5 py-2.5 text-sm dark:border-zinc-800 dark:bg-zinc-800/40 sm:max-w-[85%]">
        {msg.content && (
          <p className="whitespace-pre-wrap leading-relaxed">
            {msg.content}
            {msg.streaming && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-accent-500 align-middle" />}
          </p>
        )}

        {/* 出典リスト */}
        {msg.sources && msg.sources.length > 0 && (
          <div className="rounded-xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-700 dark:bg-zinc-900">
            <p className="mb-1 text-[11px] font-semibold text-zinc-400">出典（{msg.sources.length} 件）</p>
            <ol className="space-y-1">
              {msg.sources.map((s, i) => (
                <li key={i} className="truncate text-xs">
                  <a href={s.url} target="_blank" rel="noreferrer" className="text-accent-600 hover:underline dark:text-accent-400">
                    {s.title || s.url}
                  </a>
                  {s.source && <span className="ml-1 text-zinc-400">({s.source})</span>}
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
