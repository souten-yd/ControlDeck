/** LLMモデル管理。runtime選択、取得/登録、ロード、モデル個別設定を一つの画面に統合する。
 * 取得・ローカル登録はサーバー側ジョブで実行され、ブラウザを閉じても継続する。 */
import { DisplayNameField } from "../features/models/DisplayNameField";
import { useEffect, useState } from "react";
import { CTX_PRESETS, CopyText, FileName, LifecycleAlwaysOn, ModelStatusSummary, ThinkingDefaults, PresetOrCustom, SettingsGroup, SheetActions, Toggle, primaryButton, secondaryButton } from "../features/models/formControls";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, wsUrl } from "../api/client";
import { discardSocket, looksAlive, retryDelayMs, watchForReturn } from "../lib/liveConnection";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, DropdownMenu, EmptyState, IconButton, SegmentedTabs, Skeleton } from "../components/ui";
import { FilePicker } from "../components/FilePicker";
import { IconDots, IconFolder, IconPlay, IconPlus, IconSearch, IconSettings, IconStop, IconTrash } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { ModelLibraryPanel } from "../features/models/ModelLibraryPanel";
import { ThinkingControl } from "../features/models/ThinkingControl";
import { HuggingFaceDownload } from "../features/models/HuggingFaceDownload";
import { CapacityWidget } from "../features/models/CapacityWidget";
import { SwapQueueNotice } from "../features/models/SwapQueueNotice";
import { SpeechEnginesPanel } from "../features/models/SpeechEnginesPanel";
import { RetrievalModelsPanel } from "../features/models/RetrievalModelsPanel";
import { FrontierModelsGroup } from "../features/models/FrontierModelsPanel";
import { RadianceDetailSheet, RadianceRegisterPanel, useRadianceState } from "../features/models/RadiancePanel";
import { MayaSDetailSheet, useMayaStatus } from "../features/models/MayaSModelPanel";
import { MayaModelSheet, MayaRegisterPanel } from "../features/models/MayaRegisterPanel";
import { deleteLlamaInstance, duplicateLlamaInstance, listLlamaEndpoints, reorderLlamaInstances, type ThinkMode } from "../api/models";

interface Model {
  id?: string;
  name: string;
  size: number;
  parameter_size: string;
  quantization: string;
  family: string;
  loaded: boolean;
  expires_at: string | null;
  vram: number | null;
  digest?: string;
  vision_enabled?: boolean;
  /** どのランタイムが載せるモデルか。一覧の印と操作先の振り分けに使う。 */
  runtime?: LocalRuntime | "ollama";
  /** ROCm 10 / Vulkan のような、そのランタイム内でのビルド種別。 */
  backend_label?: string;
  port?: number;
  /** 表示用のモデル名（例: Qwen3.8-27B）。ゲートウェイの名前（id）とは別。 */
  display_name?: string;
  /** Maya: エンジンの更新でファイルに新しい版がある（ダウンロードの大きさ GB）。 */
  update_gb?: number;
  update_available?: boolean;
  setup_id?: string;
  folder?: string;
}

interface CompactionPolicy { enabled: boolean; mode: "auto" | "percent"; percent: number; max_tokens: number }

/** 会話を畳み始めるプロンプトの大きさ。backend の app/models_mgmt/compaction.py と同じ式。 */
const COMPACTION_BANDS: Array<[number, number]> = [[32768, 70], [131072, 75], [262144, 80]];
function compactionThreshold(window: number, c: CompactionPolicy): number | null {
  if (!c.enabled || window <= 0) return null;
  let value: number;
  if (c.mode === "percent") value = Math.floor((window * c.percent) / 100);
  else {
    const percent = COMPACTION_BANDS.find(([limit]) => window <= limit)?.[1] ?? 85;
    value = Math.floor((window * percent) / 100);
    if (c.max_tokens) value = Math.min(value, c.max_tokens);
  }
  return value > 0 ? Math.max(2048, value) : null;
}
const DEFAULT_COMPACTION: CompactionPolicy = { enabled: true, mode: "auto", percent: 80, max_tokens: 262144 };

/** ControlDeckが常駐管理するローカルLLMランタイム。Ollamaは外部プロセス扱い。 */
type LocalRuntime = "llama.cpp" | "strata" | "maya-s" | "radiance";
/** 補足文の1項目（「18.6 GB」など）の途中で折り返さない。折り返しは項目の区切りでだけ起こす。 */
const nowrap = (text: string) => text.replace(/ /g, "\u00a0");
const RUNTIME_LABEL: Record<string, string> = {
  "llama.cpp": "llama.cpp", strata: "Strata", "maya-s": "Maya", radiance: "Radiance", ollama: "Ollama",
};
interface RunningModel { name?: string; model?: string; digest?: string; size_vram?: number; expires_at?: string }
interface OllamaStatus { available: boolean; version: string; base_url: string }
interface AddonGpuPolicy {
  mode: "auto" | "swap" | "llm_first";
  restore: boolean;
  restore_delay_sec: number;
  max_lend_sec: number;
}
interface SwapSchedulerPolicy { aging_seconds: number; prefetch: boolean }
const DEFAULT_SWAP_SCHEDULER: SwapSchedulerPolicy = { aging_seconds: 30, prefetch: true };
const DEFAULT_ADDON_GPU: AddonGpuPolicy = { mode: "auto", restore: true, restore_delay_sec: 15, max_lend_sec: 1800 };
/** 降ろした LLM を載せ直すのにかかる時間の実測（R9700）。会話の KV は llama.cpp 以外は失う。 */
const RELOAD_SECONDS: Record<string, string> = {
  "llama.cpp": "約10秒（会話の KV も戻る）", radiance: "約15秒＋読み直し", strata: "約50〜120秒＋読み直し", "maya-s": "約60秒以上＋読み直し",
};
interface RuntimePolicy {
  selected_runtime: "ollama" | LocalRuntime;
  selected_backend: "rocm" | "vulkan" | "";
  coexistence: "exclusive" | "coexist";
  idle_unload_enabled: boolean;
  idle_unload_minutes: number;
  max_loaded_models: number;
  vram_margin_gib: number;
  gateway_only: boolean;
  drain_timeout_sec: number;
  preferred_models?: Partial<Record<"ollama" | LocalRuntime, string>>;
  assistant_name: string;
  chat: { timeout_seconds: number };
  deep_research: {
    auto_from_context?: boolean;
    evidence_context_chars: number;
    max_report_tokens: number;
    timeout_seconds: number;
  };
  compaction: CompactionPolicy;
  addon_gpu?: AddonGpuPolicy;
  swap_scheduler?: SwapSchedulerPolicy;
  amd_gpu: {
    enabled: boolean;
    profile: "quiet" | "balanced" | "full" | "custom";
    power_limit_watts: number;
    memory_clock_mode: "auto" | "minimum" | "limit";
    memory_clock_level: number;
    core_clock_mode: "auto" | "limit";
    core_clock_level: number;
  };
}
interface RuntimeEnvironment {
  platform: string;
  gpu: string;
  runtimes: Array<{
    id: string; runtime: "ollama" | LocalRuntime; backend: string; label: string;
    available: boolean; installed: boolean; selected: boolean; running?: boolean;
    /** ホスト環境との不一致など、選ぶ前に伝えるべき注意。空なら問題なし。 */
    warning?: string;
    /** 導入・更新を担当するアドオンのid。未導入時の導線に使う。 */
    addon_id?: string;
    experimental?: boolean;
    /** 他のモデルと同時にロードできるか。Strata / Maya-S / Radiance は GPU を占有するので false。 */
    coexist?: boolean;
  }>;
  policy: RuntimePolicy;
  startup_models?: Array<{ runtime: "ollama" | LocalRuntime; alias: string; name: string; ready: boolean }>;
  /** チャットモデルごとの 1 リクエストの窓（会話の自動圧縮のプレビュー用） */
  compaction_windows?: Array<{ alias: string; runtime: string; name: string; window: number }>;
  context_budgets?: Array<{
    alias: string; name: string; window: number; output_tokens: number;
    research_evidence_chars: number; research_report_tokens: number; research_max_sources: number;
    search_pages: number; investigate_calls: number; rag_top_k: number; rag_chars: number; handoff_chars: number;
  }>;
  addon_gpu_state?: { lending: boolean; lent: Array<{ runtime: string; alias: string }>; waiting_llm_requests: number };
  amd_gpu: null | {
    bdf: string;
    vram_bytes: number;
    power: { current_watts: number; min_watts: number; max_watts: number; default_watts: number };
    memory: { supported: boolean; performance_level: string; levels: Array<{ level: number; mhz: number; current: boolean }> };
    core: { supported: boolean; levels: Array<{ level: number; mhz: number; current: boolean }> };
    helper_installed: boolean;
    presets: Record<"quiet" | "balanced" | "full", { power_limit_watts: number; memory_clock_mode: "auto" | "limit"; memory_clock_level: number; core_clock_mode: "auto"; core_clock_level: number }>;
  };
}



function gb(n: number): string {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(0)} MB`;
}

function ollamaModelKey(value?: string): string {
  const name = (value ?? "").trim().toLowerCase();
  if (!name) return "";
  const leaf = name.split("/").pop() ?? name;
  return leaf.includes(":") ? name : `${name}:latest`;
}

interface JobInfo {
  id: string;
  kind: string;
  title: string;
  status: string; // running / succeeded / failed / canceled
  phase?: string | null;
  wait_reason?: string | null;
  progress: { status?: string; completed?: number | null; total?: number | null };
  error: string;
}

/** 所有者分離済み全体WSでジョブqueryを更新し、1秒pollを使わない。 */
function useModelJobsStream() {
  const qc = useQueryClient();
  useEffect(() => {
    let disposed = false;
    let retry: number | undefined;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let lastMessageAt = Date.now();
    const connect = () => {
      if (disposed) return;
      ws = new WebSocket(wsUrl("/jobs/stream"));
      ws.onmessage = (event) => {
        lastMessageAt = Date.now();
        const data = JSON.parse(event.data);
        if (data.type === "snapshot") {
          const all = data.jobs as JobInfo[];
          qc.setQueryData(["model-jobs"], all.filter((job) => job.kind.startsWith("model.")));
          for (const job of all) qc.setQueryData(["job", job.id], job);
        } else if (data.type === "update") {
          const job = data.job as JobInfo;
          qc.setQueryData(["job", job.id], job);
          if (job.kind.startsWith("model.")) {
            qc.setQueryData<JobInfo[]>(["model-jobs"], (current = []) =>
              [job, ...current.filter((item) => item.id !== job.id)].slice(0, 30),
            );
          }
        }
      };
      ws.onclose = () => { if (!disposed) retry = window.setTimeout(connect, retryDelayMs(attempt++)); };
      ws.onopen = () => { attempt = 0; lastMessageAt = Date.now(); };
    };
    // 携帯で戻ったときに繋ぎ直す。ここには手当てが無く、job の進み具合が
    // 静かに止まったままになっていた。
    const revive = () => {
      if (disposed) return;
      if (ws?.readyState === WebSocket.CONNECTING) return;
      if (ws?.readyState === WebSocket.OPEN && looksAlive(lastMessageAt)) return;
      discardSocket(ws);
      ws = null;
      window.clearTimeout(retry);
      attempt = 0;
      connect();
    };
    const stopWatching = watchForReturn(revive);
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(retry);
      stopWatching();
      discardSocket(ws);
    };
  }, [qc]);
}

/** 初回取得後はページ単位のWebSocketで更新する。 */
function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<JobInfo>(`/jobs/${jobId}`),
    enabled: jobId !== null,
  });
}

function JobProgress({ job }: { job: JobInfo }) {
  const show = useToasts((s) => s.show);
  const canCancel = useAuth((s) => s.can("workflows.edit"));
  const cancel = useMutation({
    mutationFn: () => api(`/jobs/${job.id}/cancel`, { method: "POST" }),
    onError: (error) => show(error instanceof Error ? error.message : "キャンセルできませんでした", "error"),
  });
  const waitLabels: Record<string, string> = {
    device_busy_exclusive: "GPUを使用中の処理が終わるのを待っています",
    insufficient_vram: "GPUメモリが空くのを待っています",
    held_by_other_owner: "別のAIランタイムがGPUを保持しています",
    queue_position: "前の処理が終わるのを待っています",
    model_loading: "AIモデルを読み込んでいます",
    provider_draining: "AIランタイムの処理終了を待っています",
    dependency_pending: "必要なサービスの準備を待っています",
    insufficient_capacity: "このGPUでは必要な容量を確保できません",
    yield_runtime_unknown: "処理時間が未申告のためLLMを退避しません",
    yield_load_cost_unknown: "再ロード実測が不足しているためLLMを退避しません",
    yield_thrash_cost: "処理時間が退避コストに見合わないため待機しています",
    yield_minimum_uptime: "LLMの最低常駐時間が過ぎるまで待機しています",
    yield_thrash_window: "短時間の退避頻発を防ぐため待機しています",
    yield_drain_timeout: "LLM処理を安全に停止できなかったため待機しています",
  };
  const pct =
    job.progress?.total && job.progress?.completed
      ? Math.round((job.progress.completed / job.progress.total) * 100)
      : null;
  const label = job.phase === "waiting_resource"
    ? waitLabels[job.wait_reason ?? ""] ?? "GPUリソースを待っています"
    :
    job.status === "succeeded" ? "完了" : job.status === "failed" ? `エラー: ${job.error}` : job.status === "canceled" ? "キャンセル" : job.status === "queued" ? "開始待ち" : job.progress?.status || "処理中...";
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <p className="truncate text-xs text-zinc-500">{label}</p>
      {pct !== null && job.status === "running" && (
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div className="h-full rounded-full bg-accent-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
      )}
      {job.phase === "waiting_resource" && canCancel && (
        <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}
          className="mt-2 rounded-lg border border-zinc-300 px-2.5 py-1 text-[11px] font-medium hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-600 dark:hover:bg-zinc-800">
          {cancel.isPending ? "キャンセル中…" : "キャンセル"}
        </button>
      )}
      <p className="mt-1 text-[10px] text-zinc-400">サーバー側で実行中 — ブラウザを閉じても継続します</p>
    </div>
  );
}

/** ページ上部: 実行中のモデル系ジョブ（シートやブラウザを閉じても追える） */
function ActiveModelJobs({ tab }: { tab: string }) {
  const { data } = useQuery({
    queryKey: ["model-jobs"],
    queryFn: () => api<JobInfo[]>("/jobs?kind=model."),
  });
  // 埋め込み・reranker の導入（model.preset）は「Embed / Reranker」に、それ以外は LLM の画面に出す
  const running = (data ?? []).filter((j) => (j.status === "queued" || j.status === "running")
    && (tab === "embed") === (j.kind === "model.preset"));
  if (running.length === 0) return null;
  return (
    <div className="mb-3 space-y-2">
      {running.map((j) => (
        <div key={j.id} className="rounded-2xl border border-accent-200 bg-accent-50/40 p-3 dark:border-accent-800 dark:bg-accent-600/10">
          <p className="mb-1 text-xs font-medium">{j.title}</p>
          <JobProgress job={j} />
        </div>
      ))}
    </div>
  );
}

export default function ModelsPage() {
  useModelJobsStream();
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [params, setParams] = useSearchParams();
  // TTS / ASR は ControlDeck 本体のエンジン（2026-11-19 利用者の依頼で 1 つのタブにまとめた）。以前の ?tab=tts もここへ。
  const requestedTab = params.get("tab") === "tts" ? "speech" : (params.get("tab") ?? "llm");
  const tab = (["llm", "embed", "speech", "frontier"].includes(requestedTab) ? requestedTab : "llm") as "llm" | "embed" | "speech" | "frontier";
  const [pulling, setPulling] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [llamaDetail, setLlamaDetail] = useState<string | null>(null);
  const [strataDetail, setStrataDetail] = useState<string | null>(null);
  const [radianceDetail, setRadianceDetail] = useState<string | null>(null);
  const [mayaDetail, setMayaDetail] = useState(false);
  const [mayaModel, setMayaModel] = useState<{ alias: string; title: string; update?: { gb: number; setupId: string; folder: string } } | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [localDeleting, setLocalDeleting] = useState<{ alias: string; runtime: LocalRuntime } | null>(null);
  const [deleteFile, setDeleteFile] = useState(false);
  const [duplicating, setDuplicating] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [reordering, setReordering] = useState(false);
  const [swapConfirm, setSwapConfirm] = useState<{ id: string; running: string } | null>(null);

  const { data: status } = useQuery({ queryKey: ["ollama-status"], queryFn: () => api<OllamaStatus>("/models/status"), refetchInterval: 15000 });
  const { data: runtimeEnv } = useQuery({ queryKey: ["runtime-environment"], queryFn: () => api<RuntimeEnvironment>("/models/runtime-environment") });
  const selectedRuntime = runtimeEnv?.policy.selected_runtime ?? "ollama";
  const isLocal = selectedRuntime !== "ollama";
  const selectedProvider: "ollama" | "local" = isLocal ? "local" : "ollama";
  const { data: models, isLoading } = useQuery({
    queryKey: ["models", selectedProvider],
    queryFn: async (): Promise<Model[]> => {
      if (!isLocal) return api<Model[]>("/models");
      const fetched = await Promise.all((["llama.cpp", "strata", "maya-s", "radiance"] as const).map(async (runtime) => {
        try {
          const common = await api<Array<{ id: string; name: string; size_bytes: number; loaded: boolean | null; details: Record<string, unknown> }>>(
            `/models/providers/${runtime}/models`);
          return common.map((m): Model => ({
            id: m.id, name: m.name, size: m.size_bytes, parameter_size: "",
            quantization: String(m.details.quantization ?? ""),
            display_name: String(m.details.display_name ?? "") || undefined,
            family: "", loaded: !!m.loaded, expires_at: null, vram: null,
            vision_enabled: m.details.vision_enabled === true,
            runtime, backend_label: String(m.details.backend_label ?? ""),
            port: typeof m.details.port === "number" ? m.details.port : undefined,
            update_available: m.details.update_available === true,
            update_gb: typeof m.details.update_gb === "number" ? m.details.update_gb : undefined,
            setup_id: typeof m.details.setup_id === "string" ? m.details.setup_id : undefined,
            folder: typeof m.details.folder === "string" ? m.details.folder : undefined,
          }));
        } catch {
          // 片方が未導入でも、もう片方の一覧は出す。
          return [] as Model[];
        }
      }));
      return fetched.flat();
    },
    refetchInterval: 15000,
    enabled: !!runtimeEnv && (isLocal || status?.available !== false),
  });
  // チャット等からOllamaが暗黙ロードされるため、重いmodel一覧とは別に軽量な/api/psを追跡する。
  // これがないと最大15秒、インジケータと操作ボタンがロード前のまま残る。
  const { data: runningModels } = useQuery({
    queryKey: ["ollama-running"],
    queryFn: () => api<RunningModel[]>("/models/running"),
    enabled: !isLocal && status?.available !== false,
    refetchInterval: 2_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
  });
  const liveModels = models?.map((model) => {
    if (isLocal || runningModels === undefined) return model;
    const key = ollamaModelKey(model.name);
    const digest = model.digest?.toLowerCase();
    const active = runningModels.find((item) =>
      ollamaModelKey(item.name ?? item.model) === key
      || (!!digest && item.digest?.toLowerCase() === digest),
    );
    return { ...model, loaded: !!active, expires_at: active?.expires_at ?? null, vram: active?.size_vram ?? null };
  });
  const { data: endpoints } = useQuery({
    queryKey: ["llama-endpoints"], queryFn: listLlamaEndpoints,
    enabled: isLocal, refetchInterval: 15000,
  });
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["models", selectedProvider] }),
      qc.invalidateQueries({ queryKey: ["llama-endpoints"] }),
      isLocal ? Promise.resolve() : qc.invalidateQueries({ queryKey: ["ollama-running"] }),
    ]);
  };

  /** 優先度の入れ替え。自動起動・オンデマンド起動の順序に効く。

   * 同一llama.cppランタイムのモデルだけを並べ替える。
   */
  const move = async (model: Model, offset: -1 | 1) => {
    if (!liveModels || model.runtime !== "llama.cpp") return;
    const runtime = model.runtime;
    const siblings = liveModels.filter((item) => item.runtime === runtime);
    const index = siblings.findIndex((item) => (item.id ?? item.name) === (model.id ?? model.name));
    const target = index + offset;
    if (index < 0 || target < 0 || target >= siblings.length) return;
    const order = siblings.map((item) => item.id ?? item.name);
    [order[index], order[target]] = [order[target], order[index]];
    setReordering(true);
    try {
      await reorderLlamaInstances(order);
      await refresh();
    } catch (e) {
      show(e instanceof Error ? e.message : "並べ替えに失敗しました", "error");
    } finally {
      setReordering(false);
    }
  };

  const removeLocal = useMutation({
    mutationFn: async ({ alias, runtime, file }: { alias: string; runtime: LocalRuntime; file: boolean }) => {
      if (runtime === "maya-s") {
        const r = await api<{ deleted_files?: number }>(
          `/features/maya-s/models/${encodeURIComponent(alias)}${file ? "?delete_files=true" : ""}`, { method: "DELETE" });
        return file ? { ok: true, gguf_deleted: (r.deleted_files ?? 0) > 0, reason: `モデルのファイル ${r.deleted_files ?? 0} 個を削除` }
          : { ok: true, gguf_deleted: false, reason: "Mayaのモデルファイルは保持" };
      }
      if (runtime === "strata") {
        await api<{ model_files_deleted?: boolean }>(`/models/strata/instances/${encodeURIComponent(alias)}/delete`, { method: "POST", json: {} });
        return { ok: true, gguf_deleted: false, reason: "Strataモデルファイルは保持" };
      }
      return deleteLlamaInstance(alias, file);
    },
    onSuccess: (result) => {
      show(result.gguf_deleted ? "設定とGGUFを削除しました" : result.reason
        ? `設定を削除しました（${result.reason}）` : "設定を削除しました（GGUFは保持）");
      setLocalDeleting(null); setDeleteFile(false); refresh();
    },
    onError: (e) => show(e instanceof Error ? e.message : "削除に失敗しました", "error"),
  });

  const duplicate = useMutation({
    mutationFn: ({ alias, next }: { alias: string; next: string }) => duplicateLlamaInstance(alias, next),
    onSuccess: () => { show("複製しました（同じエンドポイントに追加）"); setDuplicating(null); refresh(); },
    onError: (e) => show(e instanceof Error ? e.message : "複製に失敗しました", "error"),
  });

  const act = async (id: string, action: "load" | "unload", runningOnSameEndpoint = "",
                     runtime: Model["runtime"] = undefined) => {
    // 同じエンドポイントで別モデルが動いていると、ロードでそれが止まる。先に知らせる。
    if (action === "load" && runningOnSameEndpoint && runningOnSameEndpoint !== id) {
      setSwapConfirm({ id, running: runningOnSameEndpoint });
      return;
    }
    setActing(id);
    try {
      const provider = runtime ?? (isLocal ? "llama.cpp" : "ollama");
      if (provider === "maya-s") {
        if (action === "load") {
          const started = await api<{job_id: string}>("/features/maya-s/model/start-jobs",
            {method: "POST", json: {alias: id}});
          show(`Mayaの起動ジョブを開始しました（${started.job_id}）。ジョブ一覧で進捗を確認できます`);
        } else {
          await api("/features/maya-s/model/stop", {method: "POST", json: {alias: id}});
          show("Mayaを停止しました");
          await refresh();
        }
        return;
      }
      if (provider === "radiance") {
        if (action === "load") {
          const started = await api<{ job_id: string }>(
            `/models/radiance/instances/${encodeURIComponent(id)}/start-jobs`, { method: "POST", json: {} });
          show(`Radianceの起動ジョブを開始しました（${started.job_id}）。読み込みには数十秒〜数分かかります`);
        } else {
          await api(`/models/radiance/instances/${encodeURIComponent(id)}/stop`, { method: "POST", json: {} });
          show("Radianceのモデルを停止しました");
          await refresh();
        }
        return;
      }
      if (provider === "strata" && action === "load") {
        const started = await api<{ job_id: string }>(
          `/models/strata/instances/${encodeURIComponent(id)}/start-jobs`,
          { method: "POST", json: {} },
        );
        show(`Strataの初回起動ジョブを開始しました（${started.job_id}）。モデルの取得状況はジョブ一覧で確認できます`);
        return;
      }
      if (provider === "strata" && action === "unload") {
        await api(`/models/strata/instances/${encodeURIComponent(id)}/stop`,
          { method: "POST", json: {} });
      } else {
        await api(`/models/providers/${provider}/models/${encodeURIComponent(id)}/${action}`, { method: "POST", json: {} });
      }
      if (!isLocal) {
        qc.setQueryData<RunningModel[]>(["ollama-running"], (current = []) => action === "load"
          ? [...current.filter((item) => ollamaModelKey(item.name ?? item.model) !== ollamaModelKey(id)), { name: id, model: id }]
          : current.filter((item) => ollamaModelKey(item.name ?? item.model) !== ollamaModelKey(id)));
      }
      show(action === "load" ? "ロードしました" : "アンロードしました");
      await refresh();
    } catch (e) {
      show(e instanceof Error ? e.message : "失敗しました", "error");
    } finally {
      setActing(null);
    }
  };
  const del = useMutation({
    mutationFn: (name: string) => api(`/models/providers/ollama/models/${encodeURIComponent(name)}`, { method: "DELETE" }),
    onSuccess: () => { show("削除しました"); setDeleting(null); refresh(); },
    onError: (e) => show(e instanceof Error ? e.message : "削除失敗", "error"),
  });

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <PageHeader title="Models" actions={can("workflows.edit") && <>
          <IconButton label="LLM 共通設定" onClick={() => setSettingsOpen(true)}><IconSettings /></IconButton>
          {/* ローカルのモデルを足す操作。外部（フロンティア）のタブでは使わない。 */}
          {tab !== "frontier" && tab !== "speech" && (
            <IconButton variant="primary" label={isLocal ? "モデル登録" : "モデル取得"}
              onClick={() => isLocal ? setRegisterOpen(true) : setPulling(true)}><IconPlus /></IconButton>
          )}
        </>} />
      {/* タブ: ローカル（LLM/VLM・Embed/Reranker）と、外部のフロンティアモデル。
          外部は送信先が違うので、ローカルのモデルとは混ぜずに別のタブにする。 */}
      <SegmentedTabs
        className="mb-3"
        value={tab}
        onChange={(id) => setParams(id === "llm" ? {} : { tab: id }, { replace: true })}
        tabs={[["llm", "LLM / VLM"], ["embed", "Embed / Reranker", "Embed"], ["speech", "TTS / ASR"],
          ...(can("settings.manage") ? [["frontier", "Frontier"]] as const : [])] as const}
      />
      {tab === "llm" && (
      <p className="mb-3 px-0.5 text-xs leading-relaxed text-zinc-500">
        使用中のランタイム: {isLocal
          ? `${RUNTIME_LABEL[selectedRuntime] ?? selectedRuntime}${selectedRuntime === "llama.cpp" && runtimeEnv?.policy.selected_backend
            ? ` / ${BACKEND_LABEL[runtimeEnv.policy.selected_backend] ?? runtimeEnv.policy.selected_backend}` : ""}`
          : "Ollama"}。
        {isLocal && " 登録したモデルはすべてゲートウェイの同じアドレスで使えます。"}
        {!isLocal && status && (status.available ? ` · Ollama ${status.version}` : " · Ollama に接続できません")}
      </p>
      )}

      {/* 待ちがあるときだけ出る。ゲートウェイの状態なので、ランタイムの接続の状態に関係なく出す */}
      {tab === "llm" && <SwapQueueNotice />}
      <ActiveModelJobs tab={tab} />
      {tab === "llm" && <div className="mb-3"><CapacityWidget /></div>}
      {tab === "embed" && <EmbedRerankPanel />}
      {tab === "speech" && <SpeechEnginesPanel />}
      {tab === "frontier" && can("settings.manage") && <FrontierModelsGroup />}
      {tab === "llm" && (<>
      {!isLocal && status && !status.available ? (
        <div className="rounded-2xl border border-dashed border-amber-300 bg-amber-50 p-6 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
          Ollama（{status.base_url}）に接続できません。<code className="font-mono">ollama serve</code> の起動、または設定でエンドポイントを確認してください。
        </div>
      ) : isLoading ? (
        <Skeleton className="h-24" />
      ) : !liveModels || liveModels.length === 0 ? (
        <EmptyState>モデルがありません。右上の「＋」から追加してください。</EmptyState>
      ) : (
        <ul className="space-y-2">
          {liveModels.map((m) => {
            const id = m.id ?? m.name;
            const runtime = m.runtime === "ollama" ? undefined : m.runtime;
            const isLlama = runtime === "llama.cpp";
            const endpoint = isLlama ? endpoints?.find((e) => e.aliases.includes(id)) : undefined;
            // 同じエンドポイントを共有する行は、ロードすると同居モデルが止まることを明示する。
            const shared = endpoint && endpoint.aliases.length > 1;
            const openDetail = () => {
              if (runtime === "strata") setStrataDetail(id);
              else if (runtime === "radiance") setRadianceDetail(id);
              else if (runtime === "maya-s" && id === "maya-s") setMayaDetail(true);
              else if (runtime === "maya-s") setMayaModel({ alias: id, title: m.display_name || id,
                update: m.update_available && m.setup_id && m.folder ? { gb: m.update_gb ?? 0, setupId: m.setup_id, folder: m.folder } : undefined });
              else if (isLlama) setLlamaDetail(id);
              else setDetail(m.name);
            };
            return (
            <li key={id} className={`rounded-2xl border bg-white py-2 pl-3.5 pr-2 shadow-sm transition-colors dark:bg-zinc-900 sm:pl-4 ${m.loaded
              ? "border-emerald-300/70 dark:border-emerald-800/70"
              : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700"}`}>
              <div className="flex items-center gap-2 sm:gap-3">
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${m.loaded ? "bg-emerald-500 ring-4 ring-emerald-500/15" : "bg-zinc-300 dark:bg-zinc-600"}`} title={m.loaded ? "ロード中" : "未ロード"} />
                <button onClick={openDetail}
                  aria-label={isLocal ? `${id}の個別設定を開く` : `${m.name}の詳細を開く`}
                  className="min-h-11 min-w-0 flex-1 py-0.5 text-left">
                  <p className="flex items-center gap-1.5 truncate text-sm font-semibold leading-5">
                    <span className="truncate" title={isLocal ? `ゲートウェイでの名前: ${id}` : undefined}>{isLocal ? (m.display_name || id) : m.name}</span>
                    {/* どのランタイムが載せるモデルかを一覧で見分けられるようにする。 */}
                    {shared && (
                      <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-normal text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                        :{endpoint!.port} 共有
                      </span>
                    )}
                    {m.update_available && (
                      <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
                        新しい版あり
                      </span>
                    )}
                    {m.vision_enabled && (
                      <span className="shrink-0 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-violet-700 dark:bg-violet-950/60 dark:text-violet-300">
                        VISION
                      </span>
                    )}
                  </p>
                  <p className="num mt-0.5 line-clamp-2 text-xs leading-4 text-zinc-500 sm:truncate">
                    {isLocal
                      // どのランタイムでも「ランタイム · 量子化 · サイズ」の順にそろえる。
                      ? [runtime === "llama.cpp" && m.backend_label ? `llama.cpp（${m.backend_label.replace(/（.*?）|\(.*?\)/g, "").trim()}）` : RUNTIME_LABEL[runtime ?? "ollama"],
                         m.quantization, m.size ? gb(m.size) : "",
                         m.loaded && m.vram ? `VRAM ${gb(m.vram)}` : ""].filter(Boolean).map(nowrap).join(" · ")
                      : [m.parameter_size, m.quantization, m.size ? gb(m.size) : "",
                         m.loaded && m.vram ? `VRAM ${gb(m.vram)}` : ""].filter(Boolean).map(nowrap).join(" · ")}
                  </p>
                </button>
                {can("workflows.edit") && (
                  <>
                    {/* 携帯は記号だけ、広い画面は記号＋文字（Apps の起動・停止と同じ形）。 */}
                    {(() => {
                      const label = acting === id ? (m.loaded ? "停止中..." : "ロード中...") : (m.loaded ? "アンロード" : "ロード");
                      return (
                        <button disabled={acting === id} aria-label={label} title={label}
                          onClick={() => act(id, m.loaded ? "unload" : "load", shared ? endpoint!.running_alias : "", runtime)}
                          className={`cd-icon-button relative flex h-10 min-w-10 shrink-0 items-center justify-center gap-1.5 rounded-full text-xs font-semibold transition-colors disabled:cursor-wait disabled:opacity-60 sm:px-3.5 ${m.loaded
                            ? "bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
                            : "bg-accent-50 text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-300 dark:hover:bg-accent-600/25"}`}>
                          {m.loaded ? <IconStop className="text-sm" /> : <IconPlay className="text-sm" />}
                          <span className="hidden sm:inline">{label}</span>
                        </button>
                      );
                    })()}
                    {isLocal && runtime === "maya-s" ? (
                      <DropdownMenu ariaLabel={`${id}の操作`} trigger={<IconDots />}
                        items={[{ label: "個別設定", onSelect: openDetail },
                          ...(m.update_available && id !== "maya-s"
                            ? [{ label: `更新（ダウンロード ${m.update_gb ?? 0} GB）`, onSelect: openDetail }] : []),
                          { label: "削除", danger: true, separated: true,
                            onSelect: () => setLocalDeleting({ alias: id, runtime }) }]} />
                    ) : isLocal && runtime ? (
                      <DropdownMenu
                        ariaLabel={`${m.name}の操作`}
                        trigger={<IconDots />}
                        items={[
                          { label: "個別設定", onSelect: openDetail },
                          // 優先度（同じランタイム内の並び）。一覧の小さな矢印をやめてメニューへ。
                          ...(isLlama && !reordering && liveModels.filter((item) => item.runtime === runtime).length > 1
                            ? [{ label: "上へ移動", onSelect: () => move(m, -1) }, { label: "下へ移動", onSelect: () => move(m, 1) }] : []),
                          // 複製はエンドポイント共有が前提の機能なので llama.cpp だけ。
                          ...(isLlama ? [{ label: "複製", onSelect: () => setDuplicating(id) }] : []),
                          { label: "削除", danger: true, separated: true,
                            onSelect: () => setLocalDeleting({ alias: id, runtime }) },
                        ]}
                      />
                    ) : (
                      <IconButton variant="ghost" label="削除" onClick={() => setDeleting(m.name)} className="hover:!text-red-600"><IconTrash /></IconButton>
                    )}
                  </>
                )}
              </div>
            </li>
            );
          })}
        </ul>
      )}
      {/* 外部の API モデル。秘密値と外部送信を扱うので、設定を管理できる人にだけ出す。 */}

      </>)}

      {pulling && <PullSheet onClose={() => setPulling(false)} onDone={refresh} />}
      {settingsOpen && <SettingsSheet onClose={() => setSettingsOpen(false)} />}
      {detail && <DetailSheet model={detail} onClose={() => setDetail(null)} />}
      {llamaDetail && <LlamaDetailSheet alias={llamaDetail} onClose={() => setLlamaDetail(null)} />}
      {strataDetail && <StrataDetailSheet alias={strataDetail} onClose={() => setStrataDetail(null)} />}
      {radianceDetail && <RadianceDetailSheet alias={radianceDetail} onClose={() => setRadianceDetail(null)} />}
      {mayaDetail && <MayaSDetailSheet onClose={() => setMayaDetail(false)} />}
      {mayaModel && <MayaModelSheet alias={mayaModel.alias} title={mayaModel.title} update={mayaModel.update} onClose={() => setMayaModel(null)} />}
      {registerOpen && <ModelRegisterSheet onClose={() => { setRegisterOpen(false); refresh(); }} />}
      {deleting && (
        <ConfirmDialog title={`「${deleting}」を削除しますか？`} message="モデルファイルが削除されます。取り消せません。" confirmLabel="削除する" busy={del.isPending} onConfirm={() => del.mutate(deleting)} onClose={() => setDeleting(null)} />
      )}
      {localDeleting && (
        <ConfirmDialog
          title={`「${localDeleting.alias}」を削除しますか？`}
          message={localDeleting.runtime === "strata" ? "Strataモデル登録だけを削除します。ダウンロードしたモデル本体は保持します。"
            : localDeleting.runtime === "maya-s" ? "Mayaのモデル登録だけを削除します。モデルのファイルは消しません。" : `${RUNTIME_LABEL[localDeleting.runtime]} のモデル設定と systemd unit を削除します。`}
          confirmLabel="削除する"
          danger
          busy={removeLocal.isPending}
          onConfirm={() => removeLocal.mutate({ ...localDeleting, file: deleteFile })}
          onClose={() => { setLocalDeleting(null); setDeleteFile(false); }}
        >
          {localDeleting.runtime === "maya-s" && <label className="mt-2 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50/60 px-3 py-2.5 dark:border-red-900 dark:bg-red-950/20">
            <input type="checkbox" checked={deleteFile} onChange={(e) => setDeleteFile(e.target.checked)} className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="text-xs">モデルのファイルも削除する
              <span className="block text-[10px] text-zinc-500">
                取り消せません。このモデルの一覧に載っているファイルだけを消します。ほかの Maya のモデルが使う共有のファイル（n-gram 表など）は残します。
              </span>
            </span>
          </label>}
          {localDeleting.runtime !== "strata" && localDeleting.runtime !== "maya-s" && <label className="mt-2 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50/60 px-3 py-2.5 dark:border-red-900 dark:bg-red-950/20">
            <input type="checkbox" checked={deleteFile} onChange={(e) => setDeleteFile(e.target.checked)} className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="text-xs">GGUF ファイル本体も削除する
              <span className="block text-[10px] text-zinc-500">
                取り消せません。他のモデル設定が同じファイルを参照している場合は削除されません。
              </span>
            </span>
          </label>}
        </ConfirmDialog>
      )}
      {duplicating && (
        <DuplicateDialog
          alias={duplicating}
          busy={duplicate.isPending}
          onConfirm={(next) => duplicate.mutate({ alias: duplicating, next })}
          onClose={() => setDuplicating(null)}
        />
      )}
      {swapConfirm && (
        <ConfirmDialog
          title={`「${swapConfirm.running}」を停止して切り替えますか？`}
          message={`同じエンドポイントを共有しているため、「${swapConfirm.id}」をロードすると「${swapConfirm.running}」は停止します。接続先（ポート）は変わりません。`}
          confirmLabel="切り替える"
          onConfirm={() => { const target = swapConfirm.id; setSwapConfirm(null); act(target, "load"); }}
          onClose={() => setSwapConfirm(null)}
        />
      )}
    </div>
  );
}

/** モデル設定の複製。既定で同じエンドポイントに載るので、CTX違いの切替に使える。 */
function DuplicateDialog({ alias, busy, onConfirm, onClose }: {
  alias: string; busy: boolean; onConfirm: (next: string) => void; onClose: () => void;
}) {
  const [name, setName] = useState(`${alias}-copy`);
  const valid = /^[A-Za-z0-9._:-]{1,128}$/.test(name) && name !== alias;
  return (
    <ConfirmDialog
      title={`「${alias}」を複製`}
      message="設定を丸ごとコピーします。同じエンドポイントに追加されるので、CTX や思考の設定違いを切り替えて使えます。自動起動は引き継ぎません。"
      confirmLabel="複製する"
      busy={busy}
      disabled={!valid}
      onConfirm={() => onConfirm(name)}
      onClose={onClose}
    >
      <label className="mt-2 block">
        <span className="mb-1 block text-xs font-medium text-zinc-500">新しいモデル名（alias）</span>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
          className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        {!valid && <span className="mt-1 block text-[10px] text-red-500">英数字・._:- で、元と違う名前にしてください</span>}
      </label>
    </ConfirmDialog>
  );
}

function PullSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [tab, setTab] = useState<"registry" | "hf" | "local">("registry");
  const [hfMode, setHfMode] = useState<"direct" | "ollama">("direct");
  const [model, setModel] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useJob(jobId);
  const running = job?.status === "running";

  useEffect(() => {
    if (!job || job.status === "running") return;
    if (job.status === "succeeded") { show(`${job.title} が完了しました`); onDone(); }
    else if (job.status === "failed") show(job.error, "error");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);

  const start = async (name: string) => {
    const target = name.trim();
    if (!target || running) return;
    try {
      const r = await api<{ job_id: string }>("/models/providers/ollama/pull-jobs", { method: "POST", json: { model: target } });
      setJobId(r.job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "開始に失敗しました", "error");
    }
  };

  return (
    <BottomSheet title="モデル取得" onClose={onClose} wide>
      <SegmentedTabs className="mb-3" value={tab} onChange={setTab}
        tabs={[["registry", "Ollama レジストリ"], ["hf", "HuggingFace (GGUF)"], ["local", "ローカル登録"]] as const} />

      {tab === "registry" ? (
        <div className="space-y-2">
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="例: llama3.2  /  qwen2.5:7b  /  nomic-embed-text" className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
          <button onClick={() => start(model)} disabled={running || !model.trim()} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
            {running ? "取得中..." : "取得"}
          </button>
        </div>
      ) : tab === "hf" ? (
        <div className="space-y-3">
          {/* llama.cpp はGGUFを直接置く。Ollamaは自分でpullする。取得経路が違うので分ける。 */}
          <SegmentedTabs value={hfMode} onChange={setHfMode}
            tabs={[["direct", "GGUFを直接取得"], ["ollama", "Ollamaへpull"]] as const} />
          {hfMode === "direct"
            ? <HuggingFaceDownload onStarted={setJobId} />
            : <HFSearch onPull={start} running={running} />}
        </div>
      ) : (
        <LocalRegister onDone={onDone} />
      )}

      {tab !== "local" && job && <div className="mt-3"><JobProgress job={job} /></div>}
    </BottomSheet>
  );
}

/** ローカルにダウンロード済みの GGUF を Ollama モデルとして登録する。 */
function LocalRegister({ onDone }: { onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dir, setDir] = useState("");
  const [files, setFiles] = useState<{ name: string; path: string; size: number; suggest_name: string }[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [selected, setSelected] = useState<string>("");
  const [name, setName] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useJob(jobId);
  const running = job?.status === "running";

  useEffect(() => {
    if (!job || job.status === "running") return;
    if (job.status === "succeeded") { show(`${job.title} が完了しました`); onDone(); }
    else if (job.status === "failed") show(job.error, "error");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);

  const scan = async (path: string) => {
    setDir(path);
    setScanning(true);
    setScanError("");
    setFiles(null);
    setSelected("");
    try {
      const r = await api<{ files: { name: string; path: string; size: number; suggest_name: string }[] }>(
        `/models/gguf-scan?path=${encodeURIComponent(path)}`,
      );
      setFiles(r.files);
      if (r.files.length === 1) {
        setSelected(r.files[0].path);
        setName(r.files[0].suggest_name);
      }
    } catch (e) {
      setScanError(e instanceof Error ? e.message : "スキャンに失敗しました");
    } finally {
      setScanning(false);
    }
  };

  const register = async () => {
    if (!selected || !name.trim() || running) return;
    try {
      const r = await api<{ job_id: string }>("/models/register-jobs", {
        method: "POST",
        json: { name: name.trim(), path: selected },
      });
      setJobId(r.job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "開始に失敗しました", "error");
    }
  };

  return (
    <div className="space-y-2.5">
      <p className="text-[11px] text-zinc-400">
        ダウンロード済みの GGUF ファイルを Ollama に登録します（元ファイルは変更されません）。
        選択できるのは許可ルート（設定 files.allowed_roots）配下のみです。
      </p>

      {/* フォルダ選択 */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setPickerOpen(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-xl border border-zinc-300 px-3 py-2 text-sm font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          <IconFolder className="h-4 w-4 text-amber-500" /> フォルダを選択
        </button>
        <p className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-400">{dir || "未選択"}</p>
      </div>

      {scanning && <p className="text-xs text-zinc-400">スキャン中...</p>}
      {scanError && <p className="text-xs text-red-500">{scanError}</p>}
      {files && files.length === 0 && (
        <p className="rounded-xl border border-dashed border-zinc-300 p-4 text-center text-xs text-zinc-400 dark:border-zinc-700">
          このフォルダに GGUF ファイルは見つかりませんでした（サブフォルダは 3 階層まで検索）
        </p>
      )}

      {/* GGUF 一覧 */}
      {files && files.length > 0 && (
        <ul className="max-h-56 space-y-1.5 overflow-y-auto">
          {files.map((f) => (
            <li key={f.path}>
              <label className={`flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2 ${selected === f.path ? "border-accent-500 bg-accent-50/50 dark:bg-accent-600/10" : "border-zinc-200 dark:border-zinc-700"}`}>
                <input
                  type="radio"
                  name="gguf"
                  checked={selected === f.path}
                  onChange={() => { setSelected(f.path); setName(f.suggest_name); }}
                  className="accent-current"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-xs">{f.name}</p>
                  <p className="num text-[10px] text-zinc-400">{gb(f.size)}</p>
                </div>
              </label>
            </li>
          ))}
        </ul>
      )}

      {/* モデル名 + 登録 */}
      {selected && (
        <>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-zinc-500">登録名（Ollama モデル名。タグは : で指定）</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例: qwen2.5-7b-instruct-q4_k_m"
              className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          <button
            onClick={register}
            disabled={running || !name.trim()}
            className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
          >
            {running ? "登録中..." : "Ollama に登録"}
          </button>
        </>
      )}

      {job && <JobProgress job={job} />}

      {pickerOpen && (
        <FilePicker
          mode="dir"
          title="GGUF のあるフォルダを選択"
          initialPath={dir || undefined}
          onSelect={(p) => { setPickerOpen(false); scan(p); }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}

function HFSearch({ onPull, running }: { onPull: (m: string) => void; running: boolean }) {
  const [q, setQ] = useState("");
  const search = useMutation({
    mutationFn: () => api<{ repo: string; downloads: number; likes: number; pull_hint: string }[]>(`/models/hf-search?q=${encodeURIComponent(q)}`),
  });
  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && q.trim() && search.mutate()} placeholder="HuggingFace で GGUF を検索（例: llama 3 gguf）" className="min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        <button onClick={() => search.mutate()} disabled={!q.trim() || search.isPending} className="shrink-0 rounded-xl bg-accent-600 px-3 text-white disabled:opacity-40"><IconSearch /></button>
      </div>
      <p className="text-[11px] text-zinc-400">量子化違いは <code className="font-mono">:Q4_K_M</code> 等を末尾に付けて取得できます。</p>
      {search.data && (
        <ul className="max-h-72 space-y-1.5 overflow-y-auto">
          {search.data.map((m) => (
            <li key={m.repo} className="flex items-center gap-2 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-xs">{m.repo}</p>
                <p className="num text-[10px] text-zinc-400">⬇ {m.downloads.toLocaleString()} · ♥ {m.likes}</p>
              </div>
              <button onClick={() => onPull(m.pull_hint)} disabled={running} className="shrink-0 rounded-lg bg-zinc-100 px-2.5 py-1 text-xs font-medium disabled:opacity-40 dark:bg-zinc-800">取得</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface ModelConfig {
  keep_alive?: string;
  idle_exclude?: boolean;
  vlm_enabled?: boolean;
  think?: string;
  think_budget_tokens?: number;
  num_ctx?: number;
  deep_research_num_ctx?: number;
  num_predict?: number;
  num_gpu?: number;
  num_batch?: number;
  temperature?: number;
  top_k?: number;
  top_p?: number;
  min_p?: number;
  repeat_penalty?: number;
  seed?: number;
  [k: string]: string | number | boolean | undefined;
}


function DetailSheet({ model, onClose }: { model: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["model-show", model],
    queryFn: () => api<{ parameters: string; details: Record<string, string>; license: string; context_length: number | null; capabilities: string[] }>(`/models/${encodeURIComponent(model)}/show`),
  });
  return (
    <BottomSheet title={model} onClose={onClose} wide>
      {isLoading || !data ? (
        <Skeleton className="h-24" />
      ) : (
        <div className="space-y-3 text-sm">
          <ModelConfigSection model={model} />
          <dl className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {data.context_length && <Row k="コンテキスト長" v={data.context_length.toLocaleString()} />}
            {data.capabilities?.length > 0 && <Row k="機能" v={data.capabilities.join(", ")} />}
            {Object.entries(data.details || {}).map(([k, v]) => <Row key={k} k={k} v={String(v)} />)}
          </dl>
          {data.parameters && (
            <div>
              <p className="mb-1 text-xs font-medium text-zinc-500">既定パラメータ</p>
              <pre className="max-h-40 overflow-auto rounded-lg bg-zinc-50 p-2 font-mono text-[11px] dark:bg-zinc-950">{data.parameters}</pre>
            </div>
          )}
          {data.license && (
            <details>
              <summary className="cursor-pointer text-xs font-medium text-zinc-500">ライセンス</summary>
              <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-zinc-50 p-2 text-[11px] dark:bg-zinc-950">{data.license}</pre>
            </details>
          )}
        </div>
      )}
    </BottomSheet>
  );
}

const PREDICT_PRESETS = [
  { v: -1, label: "無制限 (-1)" }, { v: -2, label: "文脈まで (-2)" },
  { v: 256, label: "256" }, { v: 512, label: "512" }, { v: 1024, label: "1024" },
  { v: 2048, label: "2048" }, { v: 4096, label: "4096" }, { v: 8192, label: "8192" },
  { v: 16384, label: "16,384" }, { v: 32768, label: "32,768" },
  { v: 65536, label: "65,536" }, { v: 131072, label: "131,072" }, { v: 262144, label: "262,144" },
];
const TEMP_PRESETS = [0, 0.2, 0.4, 0.7, 1.0, 1.3].map((v) => ({ v, label: v.toFixed(1) }));
const TOPK_PRESETS = [10, 20, 40, 80, 100].map((v) => ({ v, label: String(v) }));
const TOPP_PRESETS = [0.5, 0.8, 0.9, 0.95, 1.0].map((v) => ({ v, label: v.toFixed(2) }));
const MINP_PRESETS = [0, 0.02, 0.05, 0.1].map((v) => ({ v, label: v.toFixed(2) }));
const REPEAT_PRESETS = [1.0, 1.05, 1.1, 1.2].map((v) => ({ v, label: v.toFixed(2) }));
const GPU_PRESETS = [{ v: -1, label: "全部 (-1)" }, { v: 0, label: "CPUのみ (0)" }, { v: 16, label: "16層" }, { v: 32, label: "32層" }, { v: 48, label: "48層" }];
const KEEPALIVE_PRESETS = [
  { v: "5m", label: "5分" }, { v: "30m", label: "30分" }, { v: "1h", label: "1時間" },
  { v: "4h", label: "4時間" }, { v: "-1", label: "無期限 (-1)" }, { v: "0", label: "使用後すぐ解放 (0)" },
];

/** モデルごとの詳細設定（生成/ロードパラメータ一式）。 */
function ModelConfigSection({ model }: { model: string }) {
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["model-config", model],
    queryFn: () => api<ModelConfig>(`/models/providers/ollama/models/${encodeURIComponent(model)}/config`),
  });
  const { data: caps } = useQuery({
    queryKey: ["model-show", model],
    queryFn: () => api<{ capabilities: string[] }>(`/models/${encodeURIComponent(model)}/show`),
  });
  const [cfg, setCfg] = useState<ModelConfig | null>(null);
  const [open, setOpen] = useState(false);
  const eff = cfg ?? data ?? null;
  const set = (k: keyof ModelConfig, v: number | string | boolean | undefined) => setCfg({ ...(eff ?? {}), [k]: v });

  const saveMut = useMutation({
    mutationFn: (reload: boolean) =>
      api(`/models/providers/ollama/models/${encodeURIComponent(model)}/config?reload=${reload}`, { method: "PUT", json: eff }),
    onSuccess: (_d, reload) => {
      show(reload ? "保存して新しい設定でロードしました" : "モデル設定を保存しました");
      qc.invalidateQueries({ queryKey: ["model-config", model] });
      qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e) => show(e instanceof Error ? e.message : "保存失敗", "error"),
  });
  if (!eff || !can("workflows.edit")) return null;
  const hasThinking = (caps?.capabilities ?? []).includes("thinking");
  // MTP（Multi-Token Prediction）対応判定: capabilities に completion 以外の特殊機能があるかで簡易判定
  const hasMtp = (caps?.capabilities ?? []).some((c) => /mtp|speculat/i.test(c));

  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-700">
      <p className="px-3 py-2.5 text-xs font-semibold text-zinc-500">このモデルの個別設定</p>
      <div className="space-y-2.5 px-3 pb-3">
        {/* よく使う */}
        <L label="常駐時間 keep_alive"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.keep_alive} presets={KEEPALIVE_PRESETS} numeric={false} placeholder="30m / 1h" onChange={(v) => set("keep_alive", v)} /></L>
        <L label="コンテキスト長 num_ctx（大きいほどVRAM増）"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.num_ctx} presets={CTX_PRESETS} placeholder="8192" onChange={(v) => set("num_ctx", v)} /></L>
        <L label="Deep Research専用CTX">
          <PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.deep_research_num_ctx} presets={CTX_PRESETS} placeholder="例: 262144" onChange={(v) => set("deep_research_num_ctx", v)} />
        </L>
        <p className="text-[10px] leading-relaxed text-zinc-400">
          未設定なら通常CTXをそのまま使用します。異なる場合はDeep Research中だけrequestへ適用し、完了後に通常CTXへ戻します。
        </p>
        <L label="出力長 num_predict（最大生成トークン）"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.num_predict} presets={PREDICT_PRESETS} placeholder="512" onChange={(v) => set("num_predict", v)} /></L>
        {hasThinking && (
          <ThinkingControl
            runtime="ollama"
            mode={(eff.think as ThinkMode) ?? "auto"}
            budget={eff.think_budget_tokens ?? 0}
            onChange={(mode, budget) => setCfg({ ...(eff ?? {}), think: mode, think_budget_tokens: budget })}
          />
        )}
        <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2.5 dark:border-zinc-700">
          <span className="text-xs">アイドル自動アンロードから除外<span className="block text-[10px] text-zinc-400">常駐させ再ロード待ちをなくす</span></span>
          <input type="checkbox" checked={!!eff.idle_exclude} onChange={(e) => set("idle_exclude", e.target.checked)} className="h-4 w-4" />
        </label>
        {(caps?.capabilities ?? []).includes("vision") && (
          <label className="flex items-center justify-between rounded-xl border border-violet-200 bg-violet-50/40 px-3 py-2.5 dark:border-violet-900 dark:bg-violet-950/20">
            <span className="text-xs">VLM（画像入力）を有効化<span className="block text-[10px] text-zinc-400">チャットの📎から画像を添付できるようにする</span></span>
            <input type="checkbox" checked={!!eff.vlm_enabled} onChange={(e) => set("vlm_enabled", e.target.checked)} className="h-4 w-4" />
          </label>
        )}

        {/* 詳細（折りたたみ） */}
        <button type="button" onClick={() => setOpen((v) => !v)} className="text-xs font-medium text-accent-600 dark:text-accent-400">
          {open ? "▾ 詳細パラメータを隠す" : "▸ 詳細パラメータ（生成品質・ハードウェア）"}
        </button>
        {open && (
          <div className="space-y-2.5 border-t border-zinc-100 pt-2.5 dark:border-zinc-800">
            <L label="温度 temperature（低=堅実 / 高=多様）"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.temperature} presets={TEMP_PRESETS} placeholder="0.7" onChange={(v) => set("temperature", v)} /></L>
            <L label="top_k"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.top_k} presets={TOPK_PRESETS} placeholder="40" onChange={(v) => set("top_k", v)} /></L>
            <L label="top_p"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.top_p} presets={TOPP_PRESETS} placeholder="0.9" onChange={(v) => set("top_p", v)} /></L>
            <L label="min_p"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.min_p} presets={MINP_PRESETS} placeholder="0.05" onChange={(v) => set("min_p", v)} /></L>
            <L label="繰り返し抑制 repeat_penalty"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.repeat_penalty} presets={REPEAT_PRESETS} placeholder="1.1" onChange={(v) => set("repeat_penalty", v)} /></L>
            <L label="GPU オフロード層数 num_gpu"><PresetOrCustom defaultLabel="既定（モデルの値）" value={eff.num_gpu} presets={GPU_PRESETS} placeholder="-1" onChange={(v) => set("num_gpu", v)} /></L>
            <L label="乱数シード seed（再現性・空=毎回ランダム）">
              <input type="number" value={eff.seed ?? ""} onChange={(e) => set("seed", e.target.value === "" ? undefined : Number(e.target.value))} placeholder="例: 42" className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
            </L>
            <p className="rounded-lg bg-zinc-50 px-2.5 py-2 text-[10px] leading-relaxed text-zinc-400 dark:bg-zinc-800/60">
              KV キャッシュ量子化（メモリ削減）は⚙全体設定にあります（Ollama サーバー環境変数）。
              {hasThinking && " 思考(think)はチャット/LLMノードに反映されます（Ollama 直結時）。"}
              {hasMtp
                ? " このモデルは MTP/推測デコードに対応しています（Ollama が自動適用）。"
                : " MTP（Multi-Token Prediction）は対応モデルで Ollama が自動適用します。個別 API 設定はありません。"}
            </p>
          </div>
        )}

        <div className="flex gap-1.5">
          <button onClick={() => saveMut.mutate(false)} disabled={saveMut.isPending} className="flex-1 rounded-xl bg-zinc-100 py-2 text-xs font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300">
            保存のみ
          </button>
          <button onClick={() => saveMut.mutate(true)} disabled={saveMut.isPending} className="flex-1 rounded-xl bg-accent-600 py-2 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">
            {saveMut.isPending ? "適用中..." : "保存してロード（反映）"}
          </button>
        </div>
        <p className="text-[10px] text-zinc-400">num_ctx / num_gpu 等はロード時に確定します。「保存してロード」で即反映されます。</p>
      </div>
    </div>
  );
}

function SettingsSheet({ onClose }: { onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const { data: runtimeEnv } = useQuery({ queryKey: ["runtime-environment"], queryFn: () => api<RuntimeEnvironment>("/models/runtime-environment") });
  const [policyCfg, setPolicyCfg] = useState<RuntimePolicy | null>(null);
  const policy = policyCfg ?? runtimeEnv?.policy ?? null;
  const savePolicy = useMutation({
    mutationFn: (patch: Partial<RuntimePolicy>) => api<RuntimeEnvironment>("/models/runtime-policy", { method: "PUT", json: patch }),
    onSuccess: (next) => {
      setPolicyCfg(next.policy);
      qc.setQueryData(["runtime-environment"], next);
      qc.invalidateQueries({ queryKey: ["llama-status"] });
      // AI アシスタントなどが Models の選択へすぐ追従するように。
      qc.invalidateQueries({ queryKey: ["llm-endpoints"] });
      show("共通設定を保存しました");
      onClose();
    },
    onError: (e) => show(e instanceof Error ? e.message : "ランタイム設定の適用に失敗", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  if (!policy || !runtimeEnv) return null;
  const chooseRuntime = (item: RuntimeEnvironment["runtimes"][number]) => {
    if (!item.installed) return;
    // Draft only: one 保存 button applies everything, so nothing typed above is lost.
    setPolicyCfg({
      ...policy,
      selected_runtime: item.runtime,
      // ollama以外は「同じランタイム内のどのビルドか」を backend に持たせる
      // （llama.cpp は rocm/vulkan）。
      selected_backend: item.runtime === "ollama"
        ? "" : item.backend as RuntimePolicy["selected_backend"],
    });
  };
  return (
    <BottomSheet title="LLM 共通設定" onClose={onClose} wide>
      <div className="space-y-3">
      <SettingsGroup title="既定のランタイム" description={`${runtimeEnv.platform} · ${runtimeEnv.gpu} GPU。モデル名を指定した要求は、そのモデルのランタイムへ自動で切り替わります`}>
        <div className="grid gap-2 sm:grid-cols-3">
          {runtimeEnv.runtimes.filter((r) => r.available || r.installed).map((item) => {
            const selected = policy.selected_runtime === item.runtime && (item.runtime === "ollama" || policy.selected_backend === item.backend);
            const models = (runtimeEnv.startup_models ?? []).filter((m) => m.runtime === item.runtime);
            const model = policy.preferred_models?.[item.runtime] ?? "";
            return (
              <div key={item.id} role="group" aria-label={`${item.label} のランタイム設定`}
                className={`min-w-0 rounded-xl border ${selected ? "border-accent-500 bg-accent-50/60 ring-1 ring-accent-500 dark:bg-accent-600/10" : "border-zinc-200 dark:border-zinc-700"}`}>
                <button type="button" onClick={() => chooseRuntime(item)} disabled={!item.installed || savePolicy.isPending} aria-pressed={selected}
                  className="w-full rounded-xl p-3 text-left disabled:opacity-50">
                  <span className="flex flex-wrap items-center gap-1.5 text-sm font-semibold">
                    {item.label}
                    {item.experimental && (
                      <span className="shrink-0 whitespace-nowrap rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">実験的</span>
                    )}
                  </span>
                  <span className={`mt-1 block text-[10px] ${selected ? "text-accent-600 dark:text-accent-400" : "text-zinc-400"}`}>
                    {selected ? "● 選択中" : !item.installed ? "導入が必要" : item.running ? "稼働中 · 選択する" : "利用可能 · 選択する"}
                  </span>
                  {/* ホストROCmとビルドのメジャー不一致など、選ぶ前に知るべきことをその場に出す。 */}
                  {item.warning && (
                    <span className="mt-1.5 block rounded-lg bg-amber-50 px-2 py-1.5 text-[10px] leading-relaxed text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                      {item.warning}
                    </span>
                  )}
                </button>
                {models.length > 1 && <div className="px-3 pb-3">
                  <label className="block text-[11px] text-zinc-500">
                    起動するモデル
                    <select aria-label={`${item.label} の起動するモデル`} className={`${input} mt-1 min-w-0 text-xs`}
                      disabled={!item.installed || savePolicy.isPending} value={model}
                      onChange={(e) => setPolicyCfg({ ...policy,
                        preferred_models: { ...policy.preferred_models, [item.runtime]: e.target.value } })}>
                      <option value="">自動</option>
                      {model && !models.some((m) => m.alias === model) && <option value={model}>登録解除済み: {model}</option>}
                      {models.map((m) => <option key={m.alias} value={m.alias} disabled={!m.ready}>
                        {m.name}{m.ready ? "" : "（初回起動の確認が必要）"}
                      </option>)}
                    </select>
                  </label>
                </div>}
              </div>
            );
          })}
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">複数モデルが登録されているランタイムでは、起動するモデルを選べます。初期値は「自動」です。保存後、モデルが未起動のときの利用に反映されます。</p>
        {runtimeEnv.runtimes.some((item) => !item.installed && item.addon_id) && (
          <p className="mt-2 text-[10px] text-zinc-400">
            未導入のランタイムは <Link to="/settings/features" className="text-accent-600 underline dark:text-accent-400">設定 → オプション機能</Link> から導入・更新します。
          </p>
        )}
      </SettingsGroup>
      <SettingsGroup title="起動と停止" description="全ランタイム共通。モデルごとの除外は各モデルの個別設定で指定します">
        <Toggle label="アイドル時に自動アンロード" hint="使われていないモデルを止めて GPU と RAM を空けます"
          value={policy.idle_unload_enabled} onChange={(v) => setPolicyCfg({ ...policy, idle_unload_enabled: v })} />
        {policy.idle_unload_enabled && <L label="止めるまでの時間"><PresetOrCustom value={policy.idle_unload_minutes} presets={[5, 15, 30, 60, 240].map((v) => ({ v, label: `${v}分` }))} placeholder="30" onChange={(v) => setPolicyCfg({ ...policy, idle_unload_minutes: Number(v ?? 30) })} /></L>}
        {runtimeEnv.runtimes.find((item) => item.selected)?.coexist === false ? (
          <p className="text-[10px] leading-relaxed text-zinc-500">
            {RUNTIME_LABEL[policy.selected_runtime] ?? policy.selected_runtime} は GPU を占有するため、他のモデルと同時にはロードしません（起動時に他のモデルを止めます）。
          </p>
        ) : <div className="grid gap-2 sm:grid-cols-2">
          <L label="利用方式">
            <select value={policy.coexistence} onChange={(e) => setPolicyCfg({ ...policy, coexistence: e.target.value as RuntimePolicy["coexistence"] })} className={input}>
              <option value="exclusive">排他（推奨・VRAM競合を防ぐ）</option>
              <option value="coexist">共存（上級者向け）</option>
            </select>
          </L>
          {policy.coexistence === "coexist" && <L label="同時にロードできるモデル数">
            <PresetOrCustom value={policy.max_loaded_models} presets={[1, 2, 3, 4, 8].map((v) => ({ v, label: `${v}モデル` }))} placeholder="1" onChange={(v) => setPolicyCfg({ ...policy, max_loaded_models: Number(v ?? 1) })} />
          </L>}
        </div>}
        <p className="text-[10px] leading-relaxed text-zinc-500">思考（reasoning）の深さはモデルごとに違うため、各モデルの個別設定で指定します。</p>
      </SettingsGroup>
      <SettingsGroup title="GPU メモリ" description="LLM ランタイム共通。次回のモデル起動から適用します">
        <L label="VRAM マージン（GiB）">
          <input aria-label="VRAM マージン（GiB）" type="number" min="0" max="256" step="0.25"
            value={policy.vram_margin_gib ?? 3} className={input}
            onChange={(e) => setPolicyCfg({ ...policy, vram_margin_gib: Number(e.target.value) })} />
        </L>
        <p className="text-[10px] leading-relaxed text-zinc-500">デスクトップや他の GPU 処理のために空ける目安です。RAM の余裕とは別です。Strata・Maya・Radiance・llama.cpp の起動設定へ自動適用し、モデル個別にもっと大きな予約があればそちらを使います。llama.cpp は GPU 層数を自動（999）にする必要があります。実際の空き容量を保証するものではありません。</p>
        <p className="text-[10px] leading-relaxed text-zinc-500">Ollama は外部サービス側の設定が必要です。OLLAMA_GPU_OVERHEAD={Math.ceil((policy.vram_margin_gib ?? 3) * 1024) * 1024 * 1024}（バイト）をサービスの環境変数に設定し、再起動してください。</p>
      </SettingsGroup>
      <CompactionSettingsGroup policy={policy} windows={runtimeEnv.compaction_windows ?? []}
        onChange={(compaction) => setPolicyCfg({ ...policy, compaction })} />
      <AddonGpuSettingsGroup policy={policy} state={runtimeEnv.addon_gpu_state}
        onChange={(addon_gpu) => setPolicyCfg({ ...policy, addon_gpu })} />
      <SwapSchedulerSettingsGroup policy={policy}
        onChange={(swap_scheduler) => setPolicyCfg({ ...policy, swap_scheduler })} />
      <SettingsGroup title="AIアシスタント">
        <L label="アシスタント表示名"><input value={policy.assistant_name} onChange={(e) => setPolicyCfg({ ...policy, assistant_name: e.target.value })} className={input} /></L>
      </SettingsGroup>
      <SettingsGroup title="コンテキストに合わせた量" description="Deep Research・調査・検索・RAG・OpenCode への引き継ぎ" collapsible defaultOpen={false}>
        <p className="text-[10px] leading-relaxed text-zinc-500">
          使うモデルのコンテキスト長から、各機能が LLM に入れる量を自動で決めます。窓が広いほど多く入れますが、
          根拠の本文は 13 万トークン（約 39 万文字）で止めます。長い入力ほど精度が落ちることが分かっているためです
          （RULER・NoLiMa・Context Rot）。RAG は 4〜8 件、回答の分は窓の 10% を空けます。会話の圧縮は上の「会話の自動圧縮」の設定です。
        </p>
        {(runtimeEnv.context_budgets ?? []).length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-[11px] tabular-nums" aria-label="モデルごとの量">
              <thead className="text-zinc-500">
                <tr><th className="py-1 pr-2 font-medium">モデル</th><th className="pr-2 font-medium">窓</th>
                  <th className="pr-2 font-medium">Deep Research 根拠 / レポート</th><th className="pr-2 font-medium">調査の検索</th>
                  <th className="pr-2 font-medium">RAG</th><th className="font-medium">OpenCode への引き継ぎ</th></tr>
              </thead>
              <tbody>
                {(runtimeEnv.context_budgets ?? []).map((b) => (
                  <tr key={b.alias} className="border-t border-zinc-100 dark:border-zinc-800">
                    <td className="py-1 pr-2">{b.name}</td>
                    <td className="pr-2">{(b.window / 1024).toLocaleString()}K</td>
                    <td className="pr-2">{Math.round(b.research_evidence_chars / 1000).toLocaleString()}千字 / {Math.round(b.research_report_tokens / 1024)}K・出典 {b.research_max_sources}</td>
                    <td className="pr-2">{b.investigate_calls} 回・{b.search_pages} 件</td>
                    <td className="pr-2">{b.rag_top_k} 件・{Math.round(b.rag_chars / 1000)}千字</td>
                    <td>{Math.round(b.handoff_chars / 1000)}千字</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SettingsGroup>
      <SettingsGroup title="Deep Research" description="根拠の量・レポートの長さ・時間の上限" collapsible defaultOpen={false}>
        <label className="flex min-h-11 items-center gap-2 text-xs">
          <input type="checkbox" checked={policy.deep_research.auto_from_context ?? true}
            onChange={(e) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, auto_from_context: e.target.checked } })} />
          根拠の量とレポートの長さを、モデルのコンテキスト長から自動で決める（おすすめ）
        </label>
        <div className={"grid grid-cols-1 gap-2 sm:grid-cols-3 " + ((policy.deep_research.auto_from_context ?? true) ? "[&>label:nth-child(-n+2)]:opacity-50" : "")}>
          <L label="根拠の文字数上限">
            <PresetOrCustom value={policy.deep_research.evidence_context_chars} presets={[30000, 60000, 90000, 150000, 300000].map((v) => ({ v, label: v.toLocaleString() }))} placeholder="90000" onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, evidence_context_chars: Number(v ?? 90000) } })} />
          </L>
          <L label="レポートの長さ上限（トークン）">
            <PresetOrCustom value={policy.deep_research.max_report_tokens} presets={[8192, 16384, 24576, 32768, 65536, 131072, 262144].map((v) => ({ v, label: v.toLocaleString() }))} placeholder="32768" onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, max_report_tokens: Number(v ?? 32768) } })} />
          </L>
          <L label="制限時間（秒）">
            <PresetOrCustom value={policy.deep_research.timeout_seconds} presets={[300, 600, 1200, 1800, 3600].map((v) => ({ v, label: `${v}秒` }))} placeholder="1800" onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, timeout_seconds: Number(v ?? 1800) } })} />
          </L>
        </div>
        <p className="text-[10px] text-zinc-500">自動にすると、根拠の文字数とレポートの長さの上限は使いません（上の「コンテキストに合わせた量」の値になります）。専用CTXは全体へ強制せず、各モデルの個別設定で指定します。</p>
      </SettingsGroup>
      <SettingsGroup title="モデルの保存場所" description="GGUF を探すライブラリ" collapsible defaultOpen={false}>
        <ModelLibraryPanel />
      </SettingsGroup>
      <SheetActions>
        <button onClick={() => savePolicy.mutate(withoutGpuSettings(policy))} disabled={savePolicy.isPending} className={primaryButton}>{savePolicy.isPending ? "保存中…" : "保存"}</button>
      </SheetActions>
      </div>
    </BottomSheet>
  );
}


function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-4 py-2">
      <dt className="w-32 shrink-0 text-zinc-400">{k}</dt>
      <dd className="num min-w-0 break-all">{v}</dd>
    </div>
  );
}
/** GPU の電力・静音は設定の「GPU 設定」で変える。ここから古い値で上書きしない */
function withoutGpuSettings(policy: RuntimePolicy): Partial<RuntimePolicy> {
  const { amd_gpu: _gpu, ...rest } = policy;
  return rest;
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>{children}</label>;
}

interface LlamaInstanceConfig {
  model_path: string;
  mmproj_path?: string;
  image_min_tokens?: number;
  port: number;
  alias: string;
  selected?: boolean;
  loaded?: boolean;
  runtime_status?: string;
  base_url?: string;
  unit?: string;
  auto_start: boolean;
  idle_exclude: boolean;
  last_used_at?: string;
  n_gpu_layers: number;
  ctx_size: number;
  deep_research_ctx_size: number;
  n_parallel: number;
  flash_attn: boolean;
  n_predict: number;
  batch_size: number;
  ubatch_size: number;
  cache_type_k: string;
  cache_type_v: string;
  threads: number;
  threads_batch: number;
  mmap: boolean;
  mlock: boolean;
  /** --load-mode。空なら mmap/mlock から組み立てる（古いバイナリ互換）。 */
  load_mode: string;
  /** 種別はバイナリ側で増えるので固定しない（/models/llama/options が正）。 */
  spec_type: string;
  draft_max: number;
  draft_min: number;
  draft_p_min: number;
  /** draft-simple / eagle3 / dflash / dspark で必要なドラフトGGUF。 */
  spec_draft_model_path: string;
  /** ドラフトをVRAMへ載せる層数。-1 は auto。 */
  spec_draft_ngl: number;
  spec_draft_cache_type_k: string;
  spec_draft_cache_type_v: string;
  think: ThinkMode;
  think_budget_tokens: number;
  kv_unified: boolean;
  endpoint_id?: string;
  order?: number;
  cpu_moe: boolean;
  n_cpu_moe: number;
  temperature: number;
  top_k: number;
  top_p: number;
  min_p: number;
  repeat_penalty: number;
  seed: number;
}

interface LlamaOptions {
  /** 稼働バイナリが実際に持つ --xxx 引数。UIはこれに載っているものだけ出す。 */
  flags: string[];
  /** --spec-type が受け付ける値。版ごとに増えるのでバイナリ実物から取る。 */
  spec_types: string[];
  /** 別途ドラフトGGUFが要る種別。 */
  spec_types_needing_draft_model: string[];
  draft_cache_types: string[];
  /** --load-mode の値。--mmap / --mlock はb10793でここへ集約された。 */
  load_modes: string[];
  supports_load_mode: boolean;
}

/** 投機デコード種別の説明。未知の値はそのまま出す（バイナリが増やしても壊れない）。 */
const SPEC_TYPE_LABEL: Record<string, string> = {
  none: "無効（互換性優先）",
  "draft-mtp": "MTP — 対応GGUFのみ・追加モデル不要",
  "draft-dflash": "DFlash — 専用ドラフトGGUFが必要",
  "draft-dspark": "DSpark — 専用ドラフトGGUFが必要",
  "draft-eagle3": "EAGLE-3 — 専用ドラフトGGUFが必要",
  "draft-simple": "Draft simple — 小さめの同系GGUFが必要",
  "ngram-simple": "N-gram simple — 追加モデル不要",
  "ngram-map-k": "N-gram map-k — 追加モデル不要",
  "ngram-map-k4v": "N-gram map-k4v — 追加モデル不要",
  "ngram-mod": "N-gram mod — 追加モデル不要",
  "ngram-cache": "N-gram cache — 追加モデル不要",
};

const LLAMA_INSTANCE_WRITE_KEYS = [
  "model_path", "mmproj_path", "image_min_tokens", "port", "alias", "auto_start", "idle_exclude",
  "n_gpu_layers", "ctx_size", "deep_research_ctx_size", "n_parallel", "flash_attn", "n_predict",
  "batch_size", "ubatch_size", "cache_type_k", "cache_type_v", "threads",
  "threads_batch", "mmap", "mlock", "load_mode", "spec_type", "draft_max", "draft_min", "draft_p_min",
  "spec_draft_model_path", "spec_draft_ngl",
  "spec_draft_cache_type_k", "spec_draft_cache_type_v", "cpu_moe",
  "n_cpu_moe", "temperature", "top_k", "top_p", "min_p", "repeat_penalty", "seed",
  "think", "think_budget_tokens",
  "kv_unified",
] as const satisfies readonly (keyof LlamaInstanceConfig)[];

const LLAMA_PARAMETER_WRITE_KEYS = [
  "mmproj_path", "image_min_tokens", "n_gpu_layers", "ctx_size", "deep_research_ctx_size", "n_parallel", "flash_attn", "n_predict",
  "batch_size", "ubatch_size", "cache_type_k", "cache_type_v", "threads",
  "threads_batch", "mmap", "mlock", "load_mode", "spec_type", "draft_max", "draft_min", "draft_p_min",
  "spec_draft_model_path", "spec_draft_ngl",
  "spec_draft_cache_type_k", "spec_draft_cache_type_v", "cpu_moe",
  "n_cpu_moe", "temperature", "top_k", "top_p", "min_p", "repeat_penalty", "seed",
  "think", "think_budget_tokens",
  "kv_unified",
] as const satisfies readonly (keyof LlamaInstanceConfig)[];

function llamaInstanceWriteBody(config: LlamaInstanceConfig, includeIdentity: boolean): Record<string, unknown> {
  const keys = includeIdentity ? LLAMA_INSTANCE_WRITE_KEYS : LLAMA_PARAMETER_WRITE_KEYS;
  return Object.fromEntries(keys.map((key) => [key, config[key]]));
}

interface LlamaStatus {
  installed: boolean;
  backend: string;
  tag: string;
  base_url: string | null;
  port: number | null;
  model_path: string;
  alias: string;
  experimental: boolean;
  detected_backends: Record<string, boolean>;
  installed_backends: string[];
  selectable_backends: string[];
  instance: LlamaInstanceConfig;
  instances: LlamaInstanceConfig[];
  selected_alias: string;
  health?: { ok: boolean };
}

interface VisionDetection {
  available: boolean;
  candidates: string[];
  suggested_path: string;
  enabled_by_default: false;
}

const BACKEND_LABEL: Record<string, string> = {
  rocm: "ROCm 10 (AMD)", vulkan: "Vulkan (汎用GPU)", cuda: "CUDA (NVIDIA)",
  rocm10: "ROCm 10", rocm7: "ROCm 7.2",
};

function LlamaDetailSheet({ alias, onClose }: { alias: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: st } = useQuery({ queryKey: ["llama-status"], queryFn: () => api<LlamaStatus>("/models/llama/status") });
  const instance = st?.instances.find((item) => item.alias === alias);
  return (
    <BottomSheet title={`${alias} · 個別設定`} onClose={onClose} wide>
      {!instance ? <p className="text-xs text-zinc-400">読み込み中...</p> : (<>
        <div className="mb-3"><DisplayNameField runtime="llama.cpp" alias={alias} /></div>
        <div className="mb-3"><ModelStatusSummary running={!!instance.loaded} rows={[
          ["ランタイム", `llama.cpp${st?.backend ? ` / ${BACKEND_LABEL[st.backend] ?? st.backend}` : ""}`],
          ["モデル", <FileName path={instance.model_path} />],
          ["接続先", <CopyText text={`http://127.0.0.1:${instance.port}/v1`} />],
        ]} /></div>
        <LlamaInstanceControls
          initial={instance}
          onSaved={onClose}
          onChanged={() => {
            qc.invalidateQueries({ queryKey: ["llama-status"] });
            qc.invalidateQueries({ queryKey: ["models", "llama.cpp"] });
          }}
        />
      </>)}
    </BottomSheet>
  );
}

/** llama.cpp GGUFの新規登録。共通設定や既存モデル個別設定は扱わない。 */
function LlamaRuntimePanel({ registrationOnly = false }: { registrationOnly?: boolean }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [creating, setCreating] = useState(registrationOnly);
  const [deleting, setDeleting] = useState<string | null>(null);
  const { data: job } = useJob(jobId);
  const { data: st } = useQuery({
    queryKey: ["llama-status"],
    queryFn: () => api<LlamaStatus>("/models/llama/status"),
    refetchInterval: (q) => (q.state.data?.installed ? 15000 : false),
  });

  useEffect(() => {
    if (job && job.status !== "running" && job.status !== "queued") {
      if (job.status === "succeeded") { show("llama.cpp を導入しました"); qc.invalidateQueries({ queryKey: ["llama-status"] }); }
      else if (job.status === "failed") show(job.error, "error");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);

  const install = async (backend: string) => {
    try {
      const r = await api<{ job_id: string }>("/models/llama/install-jobs", { method: "POST", json: { backend } });
      setJobId(r.job_id);
    } catch (e) { show(e instanceof Error ? e.message : "開始に失敗", "error"); }
  };
  if (!st) return <p className="text-xs text-zinc-400">読み込み中...</p>;
  // Vulkan は導入先として出さない（ROCm に統一）。導入済みなら一覧には残る。
  const missing = st.selectable_backends.filter((backend) => backend !== "vulkan" && !st.installed_backends.includes(backend));

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold">llama.cpp</span>
        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">実験的</span>
        {st.installed ? (
          <span className="text-xs text-emerald-600 dark:text-emerald-400">
            {BACKEND_LABEL[st.backend] ?? st.backend} 導入済み{st.health?.ok ? " · 稼働中" : ""}
          </span>
        ) : (
          <span className="text-xs text-zinc-400">未導入</span>
        )}
      </div>
      {missing.length > 0 && <details className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
        <summary className="cursor-pointer text-xs font-medium text-zinc-500">別backendを追加導入</summary>
        <div className="mt-2 flex flex-wrap gap-2">
          {missing.map((backend) => <button key={backend} onClick={() => install(backend)} disabled={job?.status === "running"}
            className="rounded-lg bg-zinc-100 px-2.5 py-1.5 text-[11px] font-medium dark:bg-zinc-800">
            {BACKEND_LABEL[backend] ?? backend} を導入
          </button>)}
        </div>
      </details>}
      {job && (job.status === "running" || job.status === "queued") && <JobProgress job={job} />}
      {st.installed && registrationOnly && creating && (
        <LlamaInstanceControls
          key="new"
          initial={{ ...st.instance, model_path: "", mmproj_path: "", alias: "", port: Math.max(8080, ...st.instances.map((item) => item.port)) + (st.instances.length ? 1 : 0), auto_start: false, idle_exclude: false }}
          isNew
          onCancel={() => setCreating(false)}
          onChanged={() => { setCreating(false); qc.invalidateQueries({ queryKey: ["llama-status"] }); qc.invalidateQueries({ queryKey: ["models", "llama.cpp"] }); }}
        />
      )}
      {st.installed && registrationOnly && !creating && (
        <p className="rounded-xl bg-emerald-50 p-3 text-xs text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">GGUF設定を登録しました。この画面を閉じるとモデル一覧へ反映されます。</p>
      )}
      {st.installed && !registrationOnly && (
        <div className="space-y-2.5">
          <div className="flex gap-2">
            <select
              value={creating ? "__new__" : st.selected_alias}
              onChange={async (event) => {
                if (event.target.value === "__new__") { setCreating(true); return; }
                await api(`/models/llama/instances/${encodeURIComponent(event.target.value)}/select`, { method: "POST", json: {} });
                setCreating(false);
                qc.invalidateQueries({ queryKey: ["llama-status"] });
              }}
              className="min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-900"
            >
              {st.instances.map((instance) => (
                <option key={instance.alias} value={instance.alias}>{instance.loaded ? "● " : "○ "}{instance.alias} · :{instance.port}</option>
              ))}
              {creating && <option value="__new__">新しいGGUF設定</option>}
            </select>
            <button onClick={() => setCreating(true)} className="shrink-0 rounded-xl bg-zinc-100 px-3 py-2 text-xs font-medium dark:bg-zinc-800">+ GGUF設定</button>
          </div>
          {creating ? (
            <LlamaInstanceControls
              key="new"
              initial={{ ...st.instance, model_path: "", alias: "", port: Math.max(8080, ...st.instances.map((item) => item.port)) + (st.instances.length ? 1 : 0), auto_start: false, idle_exclude: false }}
              isNew
              onCancel={() => setCreating(false)}
              onChanged={() => { setCreating(false); qc.invalidateQueries({ queryKey: ["llama-status"] }); qc.invalidateQueries({ queryKey: ["models", "llama.cpp"] }); }}
            />
          ) : st.instances.length > 0 ? (
            <LlamaInstanceControls
              key={st.selected_alias}
              initial={st.instances.find((item) => item.alias === st.selected_alias) ?? st.instance}
              onDelete={() => setDeleting(st.selected_alias)}
              onChanged={() => { qc.invalidateQueries({ queryKey: ["llama-status"] }); qc.invalidateQueries({ queryKey: ["models", "llama.cpp"] }); }}
            />
          ) : (
            <button onClick={() => setCreating(true)} className="w-full rounded-xl border border-dashed border-zinc-300 py-5 text-xs text-zinc-500 dark:border-zinc-700">最初のGGUF設定を追加</button>
          )}
        </div>
      )}
      {deleting && <ConfirmDialog
        title={`「${deleting}」の設定を削除しますか？`}
        message="systemd unitと設定だけを削除します。GGUFファイル本体は削除しません。"
        confirmLabel="設定を削除"
        onConfirm={async () => {
          try {
            await api(`/models/llama/instances/${encodeURIComponent(deleting)}/delete`, { method: "POST", json: {} });
            show("設定を削除しました（GGUF本体は保持）"); setDeleting(null);
            qc.invalidateQueries({ queryKey: ["llama-status"] }); qc.invalidateQueries({ queryKey: ["models", "llama.cpp"] });
          } catch (error) { show(error instanceof Error ? error.message : "削除に失敗", "error"); }
        }}
        onClose={() => setDeleting(null)}
      />}
    </div>
  );
}

/** llama.cpp のモデル起動設定と起動/停止。 */
function LlamaInstanceControls({ initial, isNew = false, onCancel, onDelete, onChanged, onSaved }: {
  initial: LlamaInstanceConfig;
  isNew?: boolean;
  onCancel?: () => void;
  onDelete?: () => void;
  onChanged: () => void;
  /** Called after a successful save (the detail sheet closes itself here). */
  onSaved?: () => void;
}) {
  const [busy, setBusy] = useState<"save" | "start" | null>(null);
  const running = !isNew && !!initial.loaded;
  const show = useToasts((s) => s.show);
  const [pickerOpen, setPickerOpen] = useState<"model" | "draft" | null>(null);
  const [cfg, setCfg] = useState<LlamaInstanceConfig>({ ...initial });
  const [visionModelPath, setVisionModelPath] = useState(initial.model_path);
  const originalAlias = initial.alias;
  const { data: optionData } = useQuery({
    queryKey: ["llama-options"],
    queryFn: () => api<LlamaOptions>("/models/llama/options"),
  });
  const flags = new Set(optionData?.flags ?? []);
  const specTypes = optionData?.spec_types ?? ["none"];
  const needsDraftModel = new Set(optionData?.spec_types_needing_draft_model ?? []);
  const draftCacheTypes = optionData?.draft_cache_types ?? [];
  const loadModes = optionData?.load_modes ?? [];
  const supportsLoadMode = optionData?.supports_load_mode ?? false;
  const set = <K extends keyof typeof cfg>(key: K, value: (typeof cfg)[K]) => setCfg((current) => ({ ...current, [key]: value }));
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  // 登録のときだけ判定していたので、既に登録したモデルの VISION を後から
  // 入り切りできなかった。mmproj は起動引数なので、設定を持てば足りる。
  useEffect(() => {
    const timer = window.setTimeout(() => setVisionModelPath(cfg.model_path), 300);
    return () => window.clearTimeout(timer);
  }, [cfg.model_path]);
  const visionDetection = useQuery({
    queryKey: ["llama-vision-detection", visionModelPath],
    queryFn: () => api<VisionDetection>(`/models/llama/vision-detection?model_path=${encodeURIComponent(visionModelPath)}`),
    enabled: visionModelPath.toLowerCase().endsWith(".gguf"),
    retry: false,
  });
  const chooseModelPath = (path: string) => {
    setCfg((current) => ({ ...current, model_path: path, mmproj_path: "" }));
  };

  const persist = async (start: boolean) => {
    if (!cfg.model_path) { show("モデルファイルを選択してください", "error"); return; }
    setBusy(start ? "start" : "save");
    try {
      await api(isNew ? "/models/llama/instances" : `/models/llama/instances/${encodeURIComponent(originalAlias)}`, {
        method: isNew ? "POST" : "PUT",
        json: llamaInstanceWriteBody(cfg, isNew),
      });
      if (start) {
        const model = encodeURIComponent(cfg.alias);
        // A running model keeps its old launch arguments until it is restarted.
        if (running) await api(`/models/providers/llama.cpp/models/${model}/unload`, { method: "POST" });
        await api(`/models/providers/llama.cpp/models/${model}/load`, { method: "POST", json: {} });
        show(running ? "保存して再起動しました" : "保存して起動しました（初回はモデル読み込みに時間がかかります）");
      } else {
        show(isNew ? "登録しました" : "保存しました");
      }
      onChanged();
      onSaved?.();
    } catch (e) { show(e instanceof Error ? e.message : "保存に失敗", "error"); }
    finally { setBusy(null); }
  };

  return (
    <div className="space-y-3">
      {isNew && <SettingsGroup title="モデル" description="GGUFファイルと、ゲートウェイで使う名前">
        <div className="flex gap-1.5">
          <input value={cfg.model_path} onChange={(e) => chooseModelPath(e.target.value)} placeholder="GGUF ファイルのパス" className={`${input} min-w-0 flex-1 font-mono text-xs`} />
          <button onClick={() => setPickerOpen("model")} aria-label="GGUFを選択" className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700"><IconFolder className="h-4 w-4" /></button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <L label="モデル名（alias）"><input value={cfg.alias} onChange={(e) => set("alias", e.target.value)} className={`${input} font-mono`} /></L>
          <L label="待受port"><input type="number" min={1024} max={65535} value={cfg.port} onChange={(e) => set("port", Number(e.target.value))} className={`${input} font-mono`} /></L>
        </div>
      </SettingsGroup>}
      <SettingsGroup title="基本" description="扱える長さと、画像入力の有無">
        <div className="grid gap-2 sm:grid-cols-2">
          <L label="コンテキスト長（CTX）"><PresetOrCustom value={cfg.ctx_size} presets={CTX_PRESETS} placeholder="8192" onChange={(v) => set("ctx_size", Number(v ?? 4096))} /></L>
          <L label="最大出力トークン"><PresetOrCustom value={cfg.n_predict} presets={PREDICT_PRESETS} placeholder="2048" onChange={(v) => set("n_predict", Number(v ?? 2048))} /></L>
        </div>
        <div className="space-y-1.5 rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
          <Toggle
            label="VISION機能"
            hint="同じフォルダのmmprojを使います。既定は無効。あとから切り替えられ、次の起動から反映します"
            value={Boolean(cfg.mmproj_path)}
            disabled={!visionDetection.data?.available && !cfg.mmproj_path}
            onChange={(enabled) => set("mmproj_path", enabled ? visionDetection.data?.suggested_path ?? "" : "")}
          />
          {visionDetection.isFetching ? (
            <p className="text-[10px] text-zinc-400">同じフォルダのmmprojを確認中...</p>
          ) : visionDetection.data?.available ? (
            <p className="break-all text-[10px] text-emerald-600 dark:text-emerald-400">
              VISION対応候補を検出: {visionDetection.data.suggested_path}
              {visionDetection.data.candidates.length > 1 && `（ほか${visionDetection.data.candidates.length - 1}件）`}
            </p>
          ) : visionDetection.isError ? (
            <p className="text-[10px] text-amber-600 dark:text-amber-400">モデルファイルを確認できないためVISION判定を完了できません。</p>
          ) : cfg.model_path ? (
            <p className="text-[10px] text-zinc-400">同じフォルダにmmprojがないためVISIONは利用できません。</p>
          ) : (
            <p className="text-[10px] text-zinc-400">GGUFを選ぶとVISION対応を判定します。</p>
          )}
        </div>
      </SettingsGroup>
      <SettingsGroup title="起動と停止" description="アイドル時の自動アンロードは共通設定で切り替えます">
        <LifecycleAlwaysOn />
        <Toggle label="PC起動時に自動で起動" hint="PC を起動したらロードします（GPU の電力設定を先に適用）" value={cfg.auto_start} onChange={(value) => set("auto_start", value)} />
        <Toggle label="アイドルでも止めない（常駐）" hint="共通設定の「アイドル時に自動アンロード」の対象から外します。ゲートウェイを通さず直接つなぐアプリから使うときは常駐を推奨します" value={cfg.idle_exclude} onChange={(value) => set("idle_exclude", value)} />
      </SettingsGroup>
      <SettingsGroup title="思考">
      <ThinkingControl
        runtime="llama.cpp"
        mode={cfg.think ?? "auto"}
        budget={cfg.think_budget_tokens ?? 0}
        onChange={(mode, budget) => setCfg((c) => ({ ...c, think: mode, think_budget_tokens: budget }))}
      />
      </SettingsGroup>
      <SettingsGroup title="性能" description="GPU・同時実行・KVキャッシュ・投機デコード" collapsible defaultOpen={isNew}>
        <L label="GPUオフロード層"><PresetOrCustom value={cfg.n_gpu_layers} presets={GPU_PRESETS.map((p) => p.v === -1 ? { ...p, v: 999, label: "全部 (999)" } : p)} placeholder="999" onChange={(v) => set("n_gpu_layers", Number(v ?? 999))} /></L>
      <L label="同時実行数（スロット）">
        <input type="number" min={1} max={64} value={cfg.n_parallel}
          onChange={(e) => set("n_parallel", Math.max(1, Math.min(64, Number(e.target.value) || 1)))}
          className={`${input} font-mono`} />
        <span className="text-[11px] text-zinc-500">同時に処理するほど合計が速くなります（実測 Qwen3.8-27B: 1件 66 / 4件同時 合計 195 tok/s）。OpenCode のサブエージェントを並べるなら 2〜4。</span>
      </L>
      <Toggle label="KVを共有プールにする（推奨）"
        hint="1本で大きく使う／複数本で分け合う を同じ設定のまま切り替えられます"
        value={cfg.kv_unified ?? true} onChange={(value) => set("kv_unified", value)} />
      <p className="rounded-lg bg-zinc-50 px-2.5 py-2 text-[10px] leading-relaxed text-zinc-500 dark:bg-zinc-800/60">
        {cfg.kv_unified ?? true ? (<>
          CTX {cfg.ctx_size.toLocaleString()} は<strong>全体で共有するプール</strong>です。
          1本で最大 {cfg.ctx_size.toLocaleString()} まで使え、混雑時は最大 {cfg.n_parallel} 本が
          プールを分け合います（例: 5,000 + 1,000 のような非対称な配分も可）。
          プールが尽きたリクエストは空くまで待ってから実行します。
        </>) : (<>
          CTX {cfg.ctx_size.toLocaleString()} を{cfg.n_parallel}分割し、
          1リクエストあたり <strong>{Math.floor(cfg.ctx_size / Math.max(1, cfg.n_parallel)).toLocaleString()}</strong> 固定になります。
          1本で大きく使いたい場合は共有プールを有効にしてください。
        </>)}
      </p>

      {flags.has("--flash-attn") && <Toggle label="Flash Attention" hint="KVキャッシュ削減と速度改善。量子化KVでは有効化を推奨" value={cfg.flash_attn} onChange={(value) => set("flash_attn", value)} />}
      {(flags.has("--cache-type-k") || flags.has("--cache-type-v")) && <div className="grid grid-cols-2 gap-2">
        <L label="Kキャッシュ量子化"><CacheTypeSelect value={cfg.cache_type_k} onChange={(value) => set("cache_type_k", value)} input={input} /></L>
        <L label="Vキャッシュ量子化"><CacheTypeSelect value={cfg.cache_type_v} onChange={(value) => set("cache_type_v", value)} input={input} /></L>
      </div>}

      {flags.has("--spec-type") && <div className="space-y-2 rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
        <L label="投機デコード">
          <select value={cfg.spec_type} onChange={(e) => set("spec_type", e.target.value)} className={input}>
            {/* 選択肢は稼働バイナリが受け付ける値そのもの。版を上げると自動で増える。 */}
            {specTypes.map((value) => (
              <option key={value} value={value}>{SPEC_TYPE_LABEL[value] ?? value}</option>
            ))}
          </select>
        </L>
        {cfg.spec_type !== "none" && <>
          <L label="先読みトークン上限">
            <PresetOrCustom value={cfg.draft_max} presets={[2, 3, 4, 6, 8].map((v) => ({ v, label: String(v) }))}
              placeholder="4" onChange={(v) => set("draft_max", Number(v ?? 4))} />
          </L>
          <p className="text-[10px] leading-relaxed text-zinc-500">
            大きいほど速いわけではありません。外した分の検証コストが効くため、
            実測（Qwen3.8-27B + MTP）では8以上で投機なしより遅くなり、4前後が頭打ちでした。
          </p>
          {needsDraftModel.has(cfg.spec_type) && <>
            <L label="ドラフトGGUF（この方式では必須）">
              <div className="flex gap-1.5">
                <input value={cfg.spec_draft_model_path} onChange={(e) => set("spec_draft_model_path", e.target.value)}
                  placeholder="ドラフトモデルのGGUFパス" className={`${input} min-w-0 flex-1 font-mono text-xs`} />
                <button onClick={() => setPickerOpen("draft")} aria-label="ドラフトGGUFを選択"
                  className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700"><IconFolder className="h-4 w-4" /></button>
              </div>
            </L>
            {!cfg.spec_draft_model_path && (
              <p className="text-[10px] text-amber-600 dark:text-amber-400">
                ドラフトGGUFを指定しないと起動に失敗します。ターゲットと同じトークナイザのモデルを選んでください。
              </p>
            )}
            <L label="ドラフトのGPU層数（-1でauto）">
              <PresetOrCustom value={cfg.spec_draft_ngl} presets={[-1, 0, 99].map((v) => ({ v, label: v === -1 ? "auto" : v === 0 ? "CPUのみ" : "全層" }))}
                placeholder="-1" onChange={(v) => set("spec_draft_ngl", Number(v ?? -1))} />
            </L>
          </>}
          <details className="rounded-lg bg-zinc-50 p-2 dark:bg-zinc-800/60">
            <summary className="cursor-pointer text-[11px] text-zinc-500">受け入れ条件とドラフト側KV</summary>
            <div className="mt-2 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <L label="先読みの下限">
                  <PresetOrCustom value={cfg.draft_min} presets={[0, 1, 2, 4].map((v) => ({ v, label: String(v) }))}
                    placeholder="0" onChange={(v) => set("draft_min", Number(v ?? 0))} />
                </L>
                <L label="採用する最小確率">
                  <PresetOrCustom value={cfg.draft_p_min} presets={[0, 0.1, 0.4, 0.75].map((v) => ({ v, label: String(v) }))}
                    placeholder="0" onChange={(v) => set("draft_p_min", Number(v ?? 0))} />
                </L>
              </div>
              {draftCacheTypes.length > 0 && needsDraftModel.has(cfg.spec_type) && (
                <div className="grid grid-cols-2 gap-2">
                  <L label="ドラフトKV K（空=同じ）">
                    <select value={cfg.spec_draft_cache_type_k} onChange={(e) => set("spec_draft_cache_type_k", e.target.value)} className={input}>
                      <option value="">ターゲットと同じ</option>
                      {draftCacheTypes.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  </L>
                  <L label="ドラフトKV V（空=同じ）">
                    <select value={cfg.spec_draft_cache_type_v} onChange={(e) => set("spec_draft_cache_type_v", e.target.value)} className={input}>
                      <option value="">ターゲットと同じ</option>
                      {draftCacheTypes.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  </L>
                </div>
              )}
              <p className="text-[10px] leading-relaxed text-zinc-500">
                下限と最小確率は0でバイナリ既定。効果は生成内容に依存し、ドラフトの当たりが悪いと
                投機なしより遅くなることがあります。
              </p>
            </div>
          </details>
        </>}
        {cfg.spec_type === "draft-mtp" && <p className="text-[10px] text-amber-600 dark:text-amber-400">MTP層を含まないモデルでは起動に失敗するため、その場合は無効へ戻してください。</p>}
      </div>}

      {(flags.has("--cpu-moe") || flags.has("--n-cpu-moe")) && <div className="rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
        <Toggle label="MoE expertをCPUへ配置" hint="VRAMを節約する代わりに生成速度が低下します" value={cfg.cpu_moe} onChange={(value) => set("cpu_moe", value)} />
        {!cfg.cpu_moe && <L label="CPUへ置く先頭MoE層数（0=無効）"><PresetOrCustom value={cfg.n_cpu_moe} presets={[0, 8, 16, 24, 32].map((v) => ({ v, label: String(v) }))} placeholder="0" onChange={(v) => set("n_cpu_moe", Number(v ?? 0))} /></L>}
      </div>}

      </SettingsGroup>
      <SettingsGroup title="詳細" description="Deep Research専用CTX・batch・thread・サンプリング・読み込み方" collapsible defaultOpen={false}>
        <div className="grid gap-2 sm:grid-cols-2">
          <L label="Deep Research専用CTX"><PresetOrCustom value={cfg.deep_research_ctx_size || undefined} presets={CTX_PRESETS} placeholder="例: 262144" onChange={(v) => set("deep_research_ctx_size", Number(v ?? 0))} /></L>
        </div>
      <p className="text-[10px] leading-relaxed text-zinc-400">Deep Research専用CTXが通常CTXと異なる場合、開始前に再ロードし、完了・失敗後は通常CTXへ自動復元します。</p>
        <div className="grid grid-cols-2 gap-2">
          <L label="batch size"><PresetOrCustom value={cfg.batch_size} presets={[512, 1024, 2048, 4096].map((v) => ({ v, label: String(v) }))} placeholder="2048" onChange={(v) => set("batch_size", Number(v ?? 2048))} /></L>
          <L label="ubatch size"><PresetOrCustom value={cfg.ubatch_size} presets={[128, 256, 512, 1024].map((v) => ({ v, label: String(v) }))} placeholder="512" onChange={(v) => set("ubatch_size", Number(v ?? 512))} /></L>
          <L label="生成thread（-1=自動）"><PresetOrCustom value={cfg.threads} presets={[-1, 4, 8, 12, 16].map((v) => ({ v, label: String(v) }))} placeholder="-1" onChange={(v) => set("threads", Number(v ?? -1))} /></L>
          <L label="batch thread（-1=自動）"><PresetOrCustom value={cfg.threads_batch} presets={[-1, 4, 8, 12, 16].map((v) => ({ v, label: String(v) }))} placeholder="-1" onChange={(v) => set("threads_batch", Number(v ?? -1))} /></L>
          <L label="temperature"><PresetOrCustom value={cfg.temperature} presets={TEMP_PRESETS} placeholder="0.8" onChange={(v) => set("temperature", Number(v ?? 0.8))} /></L>
          <L label="top-k"><PresetOrCustom value={cfg.top_k} presets={TOPK_PRESETS} placeholder="40" onChange={(v) => set("top_k", Number(v ?? 40))} /></L>
          <L label="top-p"><PresetOrCustom value={cfg.top_p} presets={TOPP_PRESETS} placeholder="0.95" onChange={(v) => set("top_p", Number(v ?? 0.95))} /></L>
          <L label="min-p"><PresetOrCustom value={cfg.min_p} presets={MINP_PRESETS} placeholder="0.05" onChange={(v) => set("min_p", Number(v ?? 0.05))} /></L>
          <L label="repeat penalty"><PresetOrCustom value={cfg.repeat_penalty} presets={REPEAT_PRESETS} placeholder="1.0" onChange={(v) => set("repeat_penalty", Number(v ?? 1))} /></L>
          <L label="seed（-1=ランダム）"><input type="number" value={cfg.seed} onChange={(e) => set("seed", Number(e.target.value))} className={input} /></L>
        </div>
        <L label="VLM用 mmproj（GGUF・空で無効）">
          <input value={cfg.mmproj_path ?? ""} onChange={(e) => set("mmproj_path", e.target.value)} placeholder="multimodal projector（*.mmproj*.gguf）のパス" className={`${input} font-mono text-xs`} />
        </L>
        <p className="text-[10px] leading-relaxed text-zinc-400">mmprojを設定すると画像入力（VLM）が有効になり、チャットの📎から画像を添付できます。</p>
        {cfg.mmproj_path ? (
          <div className="space-y-1.5">
            <L label="画像1枚あたりの最小トークン数（--image-min-tokens・0=モデル既定に任せる）">
              <input
                type="number" min={0} max={16384}
                value={cfg.image_min_tokens ?? 1024}
                onChange={(e) => set("image_min_tokens", Number(e.target.value))}
                className={input}
              />
            </L>
            <p className="text-[10px] leading-relaxed text-zinc-400">
              モデル既定のままだと、画面の写しのような横長の絵は割り当てが薄くなります（960×540で約480トークン）。
              Qwen-VL系は位置関係を読ませるなら1024以上が要ると起動時に警告するため、既定を1024にしています。
              上げるとそのぶん文脈を消費します。画像を読ませないモデルは0にして外してください。
            </p>
          </div>
        ) : null}
        {supportsLoadMode ? (
          <div className="space-y-1.5">
            <L label="モデルの読み込み方（--load-mode）">
              <select value={cfg.load_mode} onChange={(e) => set("load_mode", e.target.value)} className={input}>
                <option value="">既存設定から自動（mmap / mlock の指定を引き継ぐ）</option>
                {loadModes.map((value) => (
                  <option key={value} value={value}>
                    {value === "auto" ? "auto（既定・mmap）"
                      : value === "none" ? "none（mmapしない・初回起動が遅い）"
                      : value === "mmap" ? "mmap（page cacheを使う）"
                      : value === "mlock" ? "mlock（RAMへ固定）"
                      : value === "mmap+mlock" ? "mmap+mlock"
                      : value === "dio" ? "dio（DirectIO・対応環境のみ）" : value}
                  </option>
                ))}
              </select>
            </L>
            <p className="text-[10px] leading-relaxed text-zinc-500">
              全層をGPUへ載せている場合、生成速度は変わりません（実測差なし）。効くのは起動時間で、
              cold（page cache無し）で auto/mmap 11.1秒 に対し none 12.9秒、warm では 2.9秒 対 4.8秒でした。
              旧 <code className="font-mono">--mmap</code> / <code className="font-mono">--mlock</code> は
              このバイナリでは deprecated です。
            </p>
          </div>
        ) : (<>
          <Toggle label="mmapでモデルを読む" hint="通常はON。OSのpage cacheを利用します" value={cfg.mmap} onChange={(value) => set("mmap", value)} />
          <Toggle label="モデルをRAMへ固定（mlock）" hint="swapを防ぎますが、十分なRAMが必要です" value={cfg.mlock} onChange={(value) => set("mlock", value)} />
        </>)}
      </SettingsGroup>
      <SheetActions note={running ? "稼働中です。「起動と停止」以外は次の起動から反映されます。" : undefined}>
        <button onClick={() => persist(true)} disabled={busy !== null} className={secondaryButton}>
          {busy === "start" ? "処理中…" : isNew ? "登録して起動" : running ? "保存して再起動" : "保存して起動"}
        </button>
        <button onClick={() => persist(false)} disabled={busy !== null} className={primaryButton}>
          {busy === "save" ? "保存中…" : isNew ? "登録" : "保存"}
        </button>
      </SheetActions>
      <div className="flex gap-2">
        {isNew && onCancel && <button onClick={onCancel} className="min-h-11 text-xs text-zinc-500">キャンセル</button>}
        {!isNew && onDelete && <button onClick={onDelete} className="ml-auto min-h-11 text-xs text-red-500">この設定を削除</button>}
      </div>
      {isNew && cfg.port && (
        <p className="text-[10px] text-zinc-400">
          起動後はエンドポイント <code className="font-mono">http://127.0.0.1:{cfg.port}/v1</code> をチャット/ワークフローの LLM 設定に指定して使えます。
        </p>
      )}
      {pickerOpen && (
        <FilePicker mode="file"
          title={pickerOpen === "draft" ? "ドラフト GGUF を選択" : "GGUF モデルを選択"}
          initialPath={(pickerOpen === "draft" ? cfg.spec_draft_model_path : cfg.model_path) || undefined}
          onSelect={(p) => {
            if (pickerOpen === "draft") set("spec_draft_model_path", p);
            else chooseModelPath(p);
            setPickerOpen(null);
          }}
          onClose={() => setPickerOpen(null)} />
      )}
    </div>
  );
}


function CacheTypeSelect({ value, onChange, input }: { value: string; onChange: (value: string) => void; input: string }) {
  return <select value={value} onChange={(e) => onChange(e.target.value)} className={input}>
    <option value="f16">f16（最高精度）</option>
    <option value="bf16">bf16</option>
    <option value="q8_0">q8_0（約1/2・推奨）</option>
    <option value="q4_0">q4_0（約1/4）</option>
    <option value="f32">f32（最大）</option>
  </select>;
}


/** Embed / Reranker タブ: 埋め込み・reranker の導入・選択・稼働管理（中身は RetrievalModelsPanel）。 */
function EmbedRerankPanel() {
  const { data: llamaSt } = useQuery({ queryKey: ["llama-status"], queryFn: () => api<LlamaStatus>("/models/llama/status") });
  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-400">
        RAG 検索の品質を上げる補助モデルです。使うときに自動で載り、しばらく使われなければ止まります（常駐も選べます）。
      </p>
      {llamaSt && !llamaSt.installed && (
        <div className="rounded-2xl border border-dashed border-amber-300 bg-amber-50 p-4 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
          llama.cpp が未導入です。GPU（llama.cpp）の埋め込み・reranker を使うには、LLM/VLM タブの「GGUF登録」→ 導入から先にセットアップしてください。
        </div>
      )}
      <RetrievalModelsPanel llamaInstalled={Boolean(llamaSt?.installed)} />
    </div>
  );
}

/** Register only models explicitly requested by the user. */
function ModelRegisterSheet({ onClose }: { onClose: () => void }) {
  const [runtime, setRuntime] = useState<LocalRuntime>("llama.cpp");
  const { data: strataState } = useQuery({
    queryKey: ["strata-instances"],
    queryFn: () => api<{ runtime: { installed: boolean }; instances: Array<{alias: string}> }>("/models/strata/instances"),
  });
  const { data: radianceState } = useRadianceState();
  const { data: mayaStatus } = useMayaStatus();
  return (
    <BottomSheet title="モデル登録" onClose={onClose} wide>
      <div className="mb-3 flex flex-wrap gap-2">
        {(["llama.cpp", "strata", "radiance", "maya-s"] as const).map((choice) => (
          <button key={choice} type="button" onClick={() => setRuntime(choice)}
            disabled={(choice === "strata" && !strataState?.runtime.installed) || (choice === "radiance" && !radianceState?.runtime.installed)
              || (choice === "maya-s" && !mayaStatus?.installed)}
            className={`rounded-xl border px-4 py-2 text-sm disabled:opacity-40 ${runtime === choice ? "border-accent-500" : "border-zinc-400"}`}>
            {RUNTIME_LABEL[choice]}
          </button>
        ))}
      </div>
      {runtime === "llama.cpp"
        ? <LlamaRuntimePanel registrationOnly />
        : runtime === "radiance" ? <RadianceRegisterPanel onDone={onClose} />
        : runtime === "maya-s" ? <MayaRegisterPanel onDone={onClose} />
        : <StrataRegisterPanel onDone={onClose} />}
    </BottomSheet>
  );
}

type StrataFamily = "qwen" | "unsloth" | "deepseek41";
// Must match backend strata_gateway._FAMILIES.
const STRATA_SIZES: Record<StrataFamily, string[]> = {
  qwen: ["q2_0", "iq2_xs", "iq3_xxs", "iq3_s"],
  unsloth: ["ud-iq4_xs", "ud-q4_k_xl"],
  deepseek41: ["ista-das-coder", "ist-das-2.00bpw"],
};
type StrataSettings = { alias: string; family: StrataFamily; model: string;
  context: string; port: number; model_path?: string; kv?: string; vision?: string;
  parallel?: number; drafting?: string; loaded?: boolean; idle_exclude?: boolean; auto_start?: boolean;
  reasoning_effort?: string; thinking_budget?: number; kv_resident?: number | null; ram_keep_gib?: number | null; engine_settings?: Record<string, string> };

// Strata は "<N>k" を受け付け、学習長 256K を超えると yarn を自動で付ける。
const STRATA_CTX_PRESETS = ["8k", "16k", "32k", "64k", "128k", "256k", "512k", "768k", "1024k"].map((v) => ({ v, label: v }));

type StrataMemoryEstimate = {
  supported: boolean; note: string; context_tokens: number; parallel: number;
  model: { disk_bytes: number | null; expert_bytes: number | null };
  hardware: { ram_total: number; ram_available: number; gpu: { device: string; total: number; available: number } | null };
  kv_ram_bytes?: number; kv_ram_min_bytes?: number; kv_vram_min_bytes?: number; kv_vram_max_bytes?: number;
  full_ram_estimate_bytes?: number | null; full_ram_context_tokens?: number | null; full_ram_parallel?: number | null; vram_budget_fits?: boolean | null;
};
const strataGiB = (value: number | null | undefined) => value == null ? "不明" : `${(value / 2 ** 30).toFixed(2)} GiB`;
function StrataMemoryGuide({ estimate, error, alias }: { estimate?: StrataMemoryEstimate; error: boolean; alias?: string }) {
  const observed = useQuery<{ active: boolean; status: string; note: string; processes?: Array<{ category: string; ram_bytes: number; vram_bytes: number | null }>; rows: Array<{ category: string; location: string; bytes: number | null; source: string }> }>({
    queryKey: ["strata-memory", alias], queryFn: () => api(`/models/strata/instances/${encodeURIComponent(alias!)}/memory`), enabled: !!alias, refetchInterval: 5000,
  });
  return <div className="space-y-3 rounded-xl bg-zinc-100 p-3 text-xs dark:bg-zinc-800" aria-label="Strataメモリ目安">
    <p className="font-medium">設定時の概算</p>
    {error ? <p>概算を取得できません。入力値を確認してください。</p> : !estimate ? <p>計算中…</p> : <>
      <dl className="grid grid-cols-2 gap-2 tabular-nums">
        <dt>GGUF（ディスク）</dt><dd>{strataGiB(estimate.model.disk_bytes)}</dd>
        <dt>エキスパート本体</dt><dd>{strataGiB(estimate.model.expert_bytes)}</dd>
        <dt>RAM 空き / 全体</dt><dd>{strataGiB(estimate.hardware.ram_available)} / {strataGiB(estimate.hardware.ram_total)}</dd>
        <dt>VRAM 空き / 全体</dt><dd>{strataGiB(estimate.hardware.gpu?.available)} / {strataGiB(estimate.hardware.gpu?.total)}</dd>
        {estimate.supported && <>
          <dt>KV RAM概算</dt><dd>{estimate.kv_ram_min_bytes !== estimate.kv_ram_bytes ? `${strataGiB(estimate.kv_ram_min_bytes)}〜` : ""}{strataGiB(estimate.kv_ram_bytes)}</dd>
          <dt>KV・セッション VRAM概算</dt><dd>{strataGiB(estimate.kv_vram_min_bytes)}〜{strataGiB(estimate.kv_vram_max_bytes)}</dd>
          <dt>RAM小計（VLM等は別途）</dt><dd>{strataGiB(estimate.full_ram_estimate_bytes)}</dd>
          <dt>RAM常駐できる長さの目安 / 会話</dt><dd>{estimate.full_ram_context_tokens == null ? "不明（対応するpack情報なし）" : `${Math.floor(estimate.full_ram_context_tokens / 1024)}K × ${estimate.parallel}会話`}</dd>
          <dt>RAM常駐での並列数目安</dt><dd>{estimate.full_ram_parallel == null ? "不明（KV保持長の明示・対応pack情報が必要）" : `最大 ${estimate.full_ram_parallel} 会話`}</dd>
          <dt>VRAM目安</dt><dd>{estimate.vram_budget_fits == null ? "不明" : estimate.vram_budget_fits ? "計算上は予算内" : "不足する可能性（自動設定では幅あり）"}</dd>
        </>}
      </dl>
      {estimate.vram_budget_fits === false && <p>VRAMが概算の予算を超えています。KVのVRAM保持長や並列数を下げてください。</p>}
      <p className="text-[10px] text-zinc-500">{estimate.note}</p>
      <p className="text-[10px] text-zinc-500">VRAMはモデル・VLM・投機・作業用に6GiB、エキスパートキャッシュに2GiBを仮置き。RAMに収まらない場合はSSDへのアクセスで遅くなることがあります。稼働中は現在の空き容量が減るため、再起動時の目安と異なります。</p>
    </>}
    {alias && <>
      <p className="font-medium">起動中の確保内訳（RAM / VRAM）</p>
      {observed.isError ? <p>内訳を取得できません</p> : !observed.data ? <p>取得中…</p> : !observed.data.active ? <p>停止中。起動すると表示します。</p> : <>
        <p>{observed.data.status}</p>
        {!!observed.data.processes?.length && <>
          <p className="font-medium">現在のプロセス使用量（内訳と重複・合算不可）</p>
          {observed.data.processes.map((process, i) => <div key={i} className="space-y-1 border-b border-zinc-200 py-1 dark:border-zinc-700">
            <p>{process.category}</p><p className="tabular-nums">RAM（RSS） {strataGiB(process.ram_bytes)} / VRAM {strataGiB(process.vram_bytes)}</p>
          </div>)}
        </>}
        <p className="font-medium">用途別の確保量（起動ログ）</p>
        <div className="overflow-x-auto"><table className="w-full text-left tabular-nums"><thead><tr><th>用途</th><th>配置</th><th>確保量</th></tr></thead><tbody>
          {observed.data.rows.map((row, i) => <tr key={i} title={row.source}><td className="py-1">{row.category}</td><td>{row.location}</td><td className="whitespace-nowrap">{strataGiB(row.bytes)}</td></tr>)}
        </tbody></table></div>
        <p className="text-[10px] text-zinc-500">{observed.data.note}</p>
      </>}
    </>}
  </div>;
}

function StrataRegisterPanel({ onDone, initial, running = false }: { onDone: () => void; initial?: StrataSettings; running?: boolean }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [alias, setAlias] = useState(initial?.alias ?? "qwen-flash-next");
  // Default to the measured best fit for an R9700 + 92GB RAM: UD-IQ4_XS at its native 256K.
  const [family, setFamily] = useState<StrataFamily>(initial?.family ?? "unsloth");
  const [model, setModel] = useState(initial?.model ?? "ud-iq4_xs");
  const [context, setContext] = useState(initial?.context ?? "256k");
  const [port, setPort] = useState(String(initial?.port ?? 8095));
  const [modelPath, setModelPath] = useState(initial?.model_path ?? "");
  const [kv, setKv] = useState(initial?.kv ?? "int8");
  const [vision, setVision] = useState(initial?.vision ?? "on");
  const [parallel, setParallel] = useState(initial?.parallel ?? 1);
  const [drafting, setDrafting] = useState(initial?.drafting ?? "auto");
  const [kvResident, setKvResident] = useState(String(initial?.kv_resident ?? initial?.engine_settings?.["kv-resident"] ?? ""));
  const [ramKeep, setRamKeep] = useState(String(initial?.ram_keep_gib ?? initial?.engine_settings?.STRATA_RAM_KEEP_GIB ?? initial?.engine_settings?.["ram-keep-gib"] ?? ""));
  const memoryConfig = { alias, family, model, context, port: Number(port), model_path: modelPath.trim(), kv, vision, parallel, drafting,
    kv_resident: family === "deepseek41" || kvResident === "" ? null : Number(kvResident),
    ram_keep_gib: family === "deepseek41" || ramKeep === "" ? null : Number(ramKeep) };
  const [estimateConfig, setEstimateConfig] = useState(memoryConfig);
  const memoryConfigKey = JSON.stringify(memoryConfig);
  useEffect(() => { const timer = setTimeout(() => setEstimateConfig(JSON.parse(memoryConfigKey)), 400); return () => clearTimeout(timer); }, [memoryConfigKey]);
  const memoryEstimate = useQuery<StrataMemoryEstimate>({ queryKey: ["strata-memory-estimate", estimateConfig],
    queryFn: () => api("/models/strata/memory-estimate", { method: "POST", json: estimateConfig }), retry: false,
    enabled: /^[1-9][0-9]{0,3}k$/.test(estimateConfig.context), staleTime: 5000, refetchInterval: 5000 });
  const [idleExclude, setIdleExclude] = useState(initial?.idle_exclude ?? false);
  const [autoStart, setAutoStart] = useState(initial?.auto_start ?? false);
  const [reasoningEffort, setReasoningEffort] = useState(initial?.reasoning_effort ?? "");
  const [thinkingBudget, setThinkingBudget] = useState(initial?.thinking_budget ?? 0);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try {
      await api(initial ? `/models/strata/instances/${encodeURIComponent(initial.alias)}` : "/models/strata/instances", {
        method: initial ? "PUT" : "POST", json: { alias, family, model, context, port: Number(port), model_path: modelPath.trim(), kv, vision, parallel, drafting, idle_exclude: idleExclude, auto_start: autoStart,
          reasoning_effort: reasoningEffort, thinking_budget: thinkingBudget, kv_resident: memoryConfig.kv_resident, ram_keep_gib: memoryConfig.ram_keep_gib },
      });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["models", "local"] }),
        qc.invalidateQueries({ queryKey: ["strata-instances"] }),
      ]);
      show(initial ? "Strataモデルの設定を保存しました" : "Strataモデルを登録しました。「ロード」でモデルの準備と起動を開始できます");
      onDone();
    } catch (e) {
      show(e instanceof Error ? e.message : "Strataモデル登録に失敗しました", "error");
    } finally { setSaving(false); }
  };
  const [picker, setPicker] = useState(false);
  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  const select = (label: string, value: string, setter: (v: string) => void, choices: readonly (readonly [string, string])[]) =>
    <label className="block text-xs">{label}
      <select aria-label={`Strata${label}`} value={value} disabled={running} onChange={(e) => setter(e.target.value)} className={field}>
        {choices.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </label>;
  return <div className="space-y-3">
    {!initial && <p className="text-xs text-zinc-500">登録だけではモデルをダウンロードしません。Strataはローカル限定で起動し、共通ゲートウェイに接続します。</p>}
    <SettingsGroup title="モデル" description="系列・量子化と、手元のGGUF（空欄なら初回ロード時に取得）">
      {!initial && <label className="block text-xs">モデル別名（ゲートウェイで使う名前）
        <input aria-label="Strataモデル別名" value={alias} onChange={(e) => setAlias(e.target.value)} className={`${field} font-mono`} />
      </label>}
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-xs">モデル系列
          <select aria-label="Strataモデル系列" value={family} disabled={running} onChange={(e) => {
            const next = e.target.value as StrataFamily;
            setFamily(next); setModel(STRATA_SIZES[next][0]);
          }} className={field}>
            <option value="qwen">Qwen Flash-Next（GSQ-RCO）</option>
            <option value="unsloth">Qwen Flash-Next（Unsloth）</option>
            <option value="deepseek41">DeepSeek V4.1 Flash</option>
          </select>
        </label>
        <label className="block text-xs">量子化
          <select aria-label="Strataモデル量子化" value={model} disabled={running} onChange={(e) => setModel(e.target.value)} className={field}>
            {STRATA_SIZES[family].map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      </div>
      <label className="block text-xs">既存GGUFのパス（分割モデルは先頭ファイル・任意）
        <div className="flex gap-2">
          <input aria-label="Strata既存GGUFパス" value={modelPath} disabled={running} onChange={(e) => setModelPath(e.target.value)}
            placeholder="/data1tb/LLM/.../model-00001-of-00002.gguf" className={`${field} min-w-0 flex-1 font-mono text-xs`} />
          <button type="button" disabled={running} onClick={() => setPicker(true)} aria-label="GGUFを選ぶ"
            className="mt-1 grid min-h-11 min-w-11 place-items-center rounded-xl border border-zinc-300 disabled:opacity-40 dark:border-zinc-700"><IconFolder className="h-4 w-4" /></button>
        </div>
      </label>
    </SettingsGroup>
    <SettingsGroup title="基本" description="扱える長さと、画像入力の有無">
      <label className="block text-xs">コンテキスト長（4k〜1024k）
        <div className="mt-1"><PresetOrCustom ariaLabel="Strataコンテキスト" value={context} presets={STRATA_CTX_PRESETS} numeric={false}
          placeholder="96k" disabled={running} onChange={(v) => setContext(String(v ?? "32k").trim().toLowerCase())} /></div>
        <span className="text-[10px] text-zinc-500">Qwen系は256kを超えるとyarnで拡張します（精度は下がり得ます）。</span>
      </label>
      {select("画像入力", vision, setVision, [["on", "自動（推奨）"], ["gpu", "GPUで処理"], ["cpu", "CPUで処理"], ["off", "使わない"]])}
    </SettingsGroup>
    <SettingsGroup title="起動と停止" description="アイドル時の自動アンロードは共通設定で切り替えます">
      <LifecycleAlwaysOn />
      <Toggle label="PC起動時に自動で起動" hint="ControlDeck が立ち上がったらロードします（一度ロード済みのモデルだけ）。Maya にも付けた場合は Maya が優先です"
        value={autoStart} onChange={setAutoStart} />
      <Toggle label="アイドルでも止めない（常駐）" hint="共通設定の「アイドル時に自動アンロード」の対象から外します"
        value={idleExclude} onChange={setIdleExclude} />
    </SettingsGroup>
    <SettingsGroup title="思考" description="要求が深さを指定しないときの既定">
      <ThinkingDefaults live effort={reasoningEffort} budget={thinkingBudget}
        onChange={(effort, budget) => { setReasoningEffort(effort); setThinkingBudget(budget); }} />
    </SettingsGroup>
    <SettingsGroup title="同時実行とメモリ" description="コンテキストは1会話ごとの上限。KVは会話ごとに独立して確保します">
      <div className="grid gap-2 sm:grid-cols-2">
        {select("KVキャッシュ", kv, setKv, [["int8", "int8（推奨）"], ["k8v4", "k8v4"], ["q4_0", "q4_0（省メモリ）"], ["fp16", "fp16（最高精度）"]])}
        {select("投機デコード", drafting, setDrafting, [["auto", "自動（推奨）"], ["mtp", "MTP"], ["dflash2", "DFlash2"], ["off", "使わない"]])}
      </div>
      <label className="block text-xs">同時実行数（スロット）
        <input aria-label="Strata同時実行数" type="number" min={1} max={8} value={parallel} disabled={running}
          onChange={(e) => setParallel(Number(e.target.value))} className={field} />
        <span className="text-[11px] text-zinc-500">待たせずに同時に受けますが、合計の速さはほぼ増えません（実測: 1件 102 / 4件同時 合計 106 tok/s）。並列数を増やすほどKVと会話セッションのメモリが増えます（コンテキスト長を並列数で分ける方式ではありません）。</span>
      </label>
      {family !== "deepseek41" && <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-xs">KVのVRAM保持長（トークン）
          <input aria-label="StrataKVのVRAM保持長" type="number" min={4096} max={1048576} step={1024} placeholder="自動" value={kvResident} disabled={running} onChange={(e) => setKvResident(e.target.value)} className={field} />
          <span className="text-[10px] text-zinc-500">空欄は自動。例: 32768。残りのKVはRAMから転送します。</span>
        </label>
        <label className="block text-xs">RAM余裕（GiB）
          <input aria-label="StrataRAM余裕" type="number" min={0} max={256} step={1} placeholder="自動" value={ramKeep} disabled={running} onChange={(e) => setRamKeep(e.target.value)} className={field} />
          <span className="text-[10px] text-zinc-500">起動時に残す余裕の目標値。実行中の使用量の上限ではありません。</span>
        </label>
      </div>}
      <StrataMemoryGuide estimate={memoryEstimate.data} error={memoryEstimate.isError} alias={initial?.alias} />
    </SettingsGroup>
    <SettingsGroup title="詳細" description="待受ポート" collapsible defaultOpen={false}>
      <label className="block text-xs">ポート
        <input aria-label="Strataポート" type="number" min={8081} max={65535} value={port} disabled={running}
          onChange={(e) => setPort(e.target.value)} className={`${field} font-mono`} />
      </label>
    </SettingsGroup>
    <SheetActions>
      <button disabled={saving} onClick={() => void save()} className={primaryButton}>
        {saving ? "保存中…" : initial ? "保存" : "登録"}
      </button>
    </SheetActions>
    {picker && (
      <FilePicker mode="file" title="Strata の GGUF を選択" initialPath={modelPath || "/data1tb/LLM"}
        onSelect={(path) => { setModelPath(path); setPicker(false); }} onClose={() => setPicker(false)} />
    )}
  </div>;
}

function StrataDetailSheet({ alias, onClose }: { alias: string; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ["strata-instances"], queryFn: () => api<{
      runtime: { installed: boolean; models_dir: string };
      instances: StrataSettings[];
    }>("/models/strata/instances"), refetchInterval: 5000,
  });
  const item = data?.instances.find((model) => model.alias === alias);
  return <BottomSheet title={`${alias} · 個別設定`} onClose={onClose} wide>
    {item ? <div className="space-y-3">
      <DisplayNameField runtime="strata" alias={alias} />
      <ModelStatusSummary running={!!item.loaded} lockedNote={item.loaded ? "稼働中は「起動と停止」と「思考」だけ変更できます。ほかはアンロードしてから変更してください。" : undefined} rows={[
        ["ランタイム", "Strata"],
        ["モデル", item.model_path ? <FileName path={item.model_path} /> : `${item.family} / ${item.model}（初回ロード時に取得）`],
        ["接続先", <CopyText text={`http://127.0.0.1:${item.port}/v1`} />],
      ]} />
      {/* key is the alias only: a load finishing mid-edit must not wipe the form. */}
      <StrataRegisterPanel key={alias} initial={item} running={!!item.loaded} onDone={onClose} />
    </div> : <p className="text-sm text-zinc-500">モデルを読み込み中です</p>}
  </BottomSheet>;
}


const ADDON_GPU_MODES: Array<{ v: AddonGpuPolicy["mode"]; label: string; hint: string }> = [
  { v: "auto", label: "自動（推奨）", hint: "LLM が推論していなければ降ろして GPU で生成し、終わったら載せ直します。推論中（OpenCode のサブエージェントなど）は待たずに RAM で生成します。" },
  { v: "swap", label: "GPU 優先（入れ替え）", hint: "必ず GPU で生成します。走っている推論が終わるのを待って LLM を降ろし、生成中に来た LLM の要求は終わるまで待たせます。" },
  { v: "llm_first", label: "LLM 優先（降ろさない）", hint: "LLM は降ろしません。画像・音楽は RAM（CPU）で生成するか、GPU が空くまで待ちます。会話は止まりませんが生成は遅くなります。" },
];

/** MediaForge / SonicForge が GPU を要るとき、常駐 LLM をどう譲るか。 */
function AddonGpuSettingsGroup({ policy, state, onChange }: {
  policy: RuntimePolicy;
  state: RuntimeEnvironment["addon_gpu_state"];
  onChange: (value: AddonGpuPolicy) => void;
}) {
  const g = policy.addon_gpu ?? DEFAULT_ADDON_GPU;
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  const set = (patch: Partial<AddonGpuPolicy>) => onChange({ ...g, ...patch });
  const mode = ADDON_GPU_MODES.find((m) => m.v === g.mode) ?? ADDON_GPU_MODES[0];
  const reload = RELOAD_SECONDS[policy.selected_runtime];
  return (
    <SettingsGroup title="Add-on への GPU の譲り方" description="画像（MediaForge）や音楽（SonicForge）の生成が GPU を要るとき、常駐している LLM をどうするか">
      <L label="方針">
        <select value={g.mode} onChange={(e) => set({ mode: e.target.value as AddonGpuPolicy["mode"] })} className={input}>
          {ADDON_GPU_MODES.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}
        </select>
      </L>
      <p className="text-[10px] leading-relaxed text-zinc-500">{mode.hint}どの方針でも、走っている推論は切りません。</p>
      {g.mode !== "llm_first" && (
        <>
          <Toggle label="生成が終わったら LLM を載せ直す" hint="切ると、次に LLM を使うときに載せ直します"
            value={g.restore} onChange={(v) => set({ restore: v })} />
          <div className="grid gap-2 sm:grid-cols-2">
            <L label="載せ直すまでの間">
              <PresetOrCustom value={g.restore_delay_sec} presets={[0, 15, 30, 60, 120].map((v) => ({ v, label: `${v}秒` }))} placeholder="15"
                onChange={(v) => set({ restore_delay_sec: Math.max(0, Math.min(600, Number(v ?? 15))) })} />
            </L>
            <L label="貸す時間の上限">
              <PresetOrCustom value={g.max_lend_sec} presets={[600, 1800, 3600, 7200].map((v) => ({ v, label: `${v / 60}分` }))} placeholder="1800"
                onChange={(v) => set({ max_lend_sec: Math.max(60, Math.min(7200, Number(v ?? 1800))) })} />
            </L>
          </div>
        </>
      )}
      <ul className="space-y-0.5 text-[11px] text-zinc-600 dark:text-zinc-300">
        <li>画像 1 枚（FLUX.2 Klein）: GPU を借りて約11秒 ／ LLM が載ったままだと 14〜235 秒</li>
        <li>{RUNTIME_LABEL[policy.selected_runtime] ?? policy.selected_runtime} の載せ直し: {reload ?? "—"}</li>
      </ul>
      <p className="text-[10px] leading-relaxed text-zinc-500">
        目安: 音楽や枚数の多い画像は「自動」か「GPU 優先」が速い。載せ直しが重い Strata / Maya で画像を数枚だけ作るなら、「LLM 優先」の方が全体では早く済みます（画像の差は数秒、載せ直しは約1分）。
      </p>
      {state?.lending && (
        <p className="rounded-lg bg-amber-50 px-2 py-1 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          いま Add-on へ GPU を貸しています（{state.lent.map((x) => x.alias).join("、")}）{state.waiting_llm_requests ? `。LLM の要求 ${state.waiting_llm_requests} 件が待っています` : ""}
        </p>
      )}
    </SettingsGroup>
  );
}

/** モデルの載せ替えの調停。別のモデルの要求がどれだけ早く優先されるか・次のモデルの先読み。 */
function SwapSchedulerSettingsGroup({ policy, onChange }: {
  policy: RuntimePolicy;
  onChange: (value: SwapSchedulerPolicy) => void;
}) {
  const g = policy.swap_scheduler ?? DEFAULT_SWAP_SCHEDULER;
  const set = (patch: Partial<SwapSchedulerPolicy>) => onChange({ ...g, ...patch });
  return (
    <SettingsGroup title="モデルの載せ替え" description="別々のモデルの要求が重なったときの順番。要求は断らず、待たせてから載せ替えます">
      <L label="優先度の上がる速さ（秒）">
        <PresetOrCustom value={g.aging_seconds} presets={[10, 30, 60, 120, 300].map((v) => ({ v, label: `${v}秒` }))} placeholder="30"
          onChange={(v) => set({ aging_seconds: Math.max(5, Math.min(600, Number(v ?? 30))) })} />
      </L>
      <p className="text-[10px] leading-relaxed text-zinc-500">
        いま載っているモデルを続ける点数と、待っている別のモデルの点数（待っている数 ＋ 最も古い待ち秒 ÷ この秒数、を載せ替えの見込み秒で割る）を比べます。
        小さいほど、少ない待ちでも早く載せ替えます。実行中の要求は切りません。
      </p>
      <Toggle label="次のモデルの重みを先に読む" hint="メモリに余裕（重み ＋ 8 GiB）があるときだけ、載せ替えの待ちを短くします"
        value={g.prefetch} onChange={(v) => set({ prefetch: v })} />
    </SettingsGroup>
  );
}

/** 会話の自動圧縮（AIアシスタントと OpenCode で共通）。窓の大きさに応じた発火点をモデルごとに見せる。 */
function CompactionSettingsGroup({ policy, windows, onChange }: {
  policy: RuntimePolicy;
  windows: NonNullable<RuntimeEnvironment["compaction_windows"]>;
  onChange: (value: CompactionPolicy) => void;
}) {
  const c = policy.compaction ?? DEFAULT_COMPACTION;
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  const set = (patch: Partial<CompactionPolicy>) => onChange({ ...c, ...patch });
  return (
    <SettingsGroup title="会話の自動圧縮" description="会話が長くなったら古いやり取りを要約して続けます。AIアシスタントと OpenCode に効きます">
      <Toggle label="自動圧縮を有効にする" hint="切ると溢れるまで全文を送り、溢れたときだけ OpenCode が畳みます"
        value={c.enabled} onChange={(v) => set({ enabled: v })} />
      {c.enabled && (
        <div className="grid gap-2 sm:grid-cols-2">
          <L label="圧縮を始める大きさ">
            <select value={c.mode} onChange={(e) => set({ mode: e.target.value as CompactionPolicy["mode"] })} className={input}>
              <option value="auto">自動（コンテキスト長に応じて・推奨）</option>
              <option value="percent">コンテキストに対する割合</option>
            </select>
          </L>
          {c.mode === "percent" ? (
            <L label="割合（%）">
              <PresetOrCustom value={c.percent} presets={[60, 70, 75, 80, 85, 90].map((v) => ({ v, label: `${v}%` }))} placeholder="80"
                onChange={(v) => set({ percent: Math.max(40, Math.min(95, Number(v ?? 80))) })} />
            </L>
          ) : (
            <L label="上限（トークン）">
              <PresetOrCustom value={c.max_tokens} presets={[65536, 131072, 262144, 524288, 0].map((v) => ({ v, label: v ? v.toLocaleString() : "上限なし" }))} placeholder="262144"
                onChange={(v) => set({ max_tokens: Math.max(0, Number(v ?? 262144)) })} />
            </L>
          )}
        </div>
      )}
      {c.enabled && c.mode === "auto" && (
        <p className="text-[10px] leading-relaxed text-zinc-500">
          自動: 〜32K は 70%、〜128K は 75%、〜256K は 80%、それより広い窓は 85%。窓が広くても上限を超えたら畳みます（読み直しが遅くなり、精度も落ちるため）。
        </p>
      )}
      {c.enabled && windows.length > 0 && (
        <ul className="space-y-0.5 text-[11px] text-zinc-600 dark:text-zinc-300">
          {windows.map((w) => {
            const at = compactionThreshold(w.window, c);
            return (
              <li key={w.alias} className="flex flex-wrap justify-between gap-x-2">
                <span className="font-mono">{w.alias}<span className="ml-1 font-sans text-zinc-400">{RUNTIME_LABEL[w.runtime] ?? w.runtime}</span></span>
                <span>{at ? `${at.toLocaleString()} / ${w.window.toLocaleString()} で圧縮（${Math.round((100 * at) / w.window)}%）` : "—"}</span>
              </li>
            );
          })}
        </ul>
      )}
    </SettingsGroup>
  );
}
