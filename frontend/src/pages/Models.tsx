/** LLMモデル管理。runtime選択、取得/登録、ロード、モデル個別設定を一つの画面に統合する。
 * 取得・ローカル登録はサーバー側ジョブで実行され、ブラウザを閉じても継続する。 */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, wsUrl } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, DropdownMenu, Skeleton } from "../components/ui";
import { FilePicker } from "../components/FilePicker";
import { IconDots, IconFolder, IconPlus, IconSearch, IconTrash } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { ModelLibraryPanel } from "../features/models/ModelLibraryPanel";
import { ThinkingControl } from "../features/models/ThinkingControl";
import { HuggingFaceDownload } from "../features/models/HuggingFaceDownload";
import { CapacityWidget } from "../features/models/CapacityWidget";
import { deleteLlamaInstance, duplicateLlamaInstance, listLlamaEndpoints, reorderLlamaInstances, type ThinkMode } from "../api/models";
import {
  createLuceboxInstance, deleteLuceboxInstance, getLuceboxStatus, reorderLuceboxInstances,
  updateLuceboxInstance, type LuceboxInstance, type LuceboxInstanceInput, type LuceboxStatus,
} from "../api/lucebox";

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
  /** Lucebox のDFlashドラフトが設定済みか（投機デコードが効く構成か）。 */
  speculative?: boolean;
  port?: number;
}

/** ControlDeckが常駐管理するローカルLLMランタイム。Ollamaは外部プロセス扱い。 */
type LocalRuntime = "llama.cpp" | "lucebox";
const LOCAL_RUNTIMES: LocalRuntime[] = ["llama.cpp", "lucebox"];
const RUNTIME_LABEL: Record<string, string> = {
  "llama.cpp": "llama.cpp", lucebox: "Lucebox", ollama: "Ollama",
};
interface RunningModel { name?: string; model?: string; digest?: string; size_vram?: number; expires_at?: string }
interface OllamaStatus { available: boolean; version: string; base_url: string }
interface RuntimePolicy {
  selected_runtime: "ollama" | LocalRuntime;
  selected_backend: "rocm" | "vulkan" | "rocm10" | "rocm7" | "";
  coexistence: "exclusive" | "coexist";
  idle_unload_enabled: boolean;
  idle_unload_minutes: number;
  max_loaded_models: number;
  gateway_only: boolean;
  drain_timeout_sec: number;
  default_model_ref: string;
  assistant_name: string;
  chat: { timeout_seconds: number };
  deep_research: {
    evidence_context_chars: number;
    max_report_tokens: number;
    timeout_seconds: number;
  };
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
  }>;
  policy: RuntimePolicy;
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
    const connect = () => {
      if (disposed) return;
      ws = new WebSocket(wsUrl("/jobs/stream"));
      ws.onmessage = (event) => {
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
      ws.onclose = () => { if (!disposed) retry = window.setTimeout(connect, 1000); };
    };
    connect();
    return () => { disposed = true; window.clearTimeout(retry); ws?.close(); };
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
function ActiveModelJobs() {
  const { data } = useQuery({
    queryKey: ["model-jobs"],
    queryFn: () => api<JobInfo[]>("/jobs?kind=model."),
  });
  const running = (data ?? []).filter((j) => j.status === "queued" || j.status === "running");
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
  const tab = (params.get("tab") ?? "llm") as "llm" | "embed" | "tts";
  const [pulling, setPulling] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [llamaDetail, setLlamaDetail] = useState<string | null>(null);
  const [luceboxDetail, setLuceboxDetail] = useState<string | null>(null);
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
  // ローカルランタイムを選んでいる間は llama.cpp と Lucebox のモデルを1つの一覧に出す。
  // ゲートウェイが両方を同じアドレスで配るので、片方だけ見えると実態と食い違う。
  const isLocal = selectedRuntime !== "ollama";
  const selectedProvider: "ollama" | "local" = isLocal ? "local" : "ollama";
  const { data: models, isLoading } = useQuery({
    queryKey: ["models", selectedProvider],
    queryFn: async (): Promise<Model[]> => {
      if (!isLocal) return api<Model[]>("/models");
      const fetched = await Promise.all(LOCAL_RUNTIMES.map(async (runtime) => {
        try {
          const common = await api<Array<{ id: string; name: string; size_bytes: number; loaded: boolean | null; details: Record<string, unknown> }>>(
            `/models/providers/${runtime}/models`);
          return common.map((m): Model => ({
            id: m.id, name: m.name, size: m.size_bytes, parameter_size: "", quantization: "",
            family: "", loaded: !!m.loaded, expires_at: null, vram: null,
            vision_enabled: m.details.vision_enabled === true,
            runtime, backend_label: String(m.details.backend_label ?? ""),
            speculative: m.details.speculative === true,
            port: typeof m.details.port === "number" ? m.details.port : undefined,
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
      qc.invalidateQueries({ queryKey: ["lucebox-status"] }),
      isLocal ? Promise.resolve() : qc.invalidateQueries({ queryKey: ["ollama-running"] }),
    ]);
  };

  /** 優先度の入れ替え。自動起動・オンデマンド起動の順序に効く。

   * 順序はランタイム内で閉じている（llama.cpp と Lucebox は別々のカタログを持つ）ため、
   * 同じランタイムの行同士でのみ入れ替える。
   */
  const move = async (model: Model, offset: -1 | 1) => {
    if (!liveModels || !model.runtime || model.runtime === "ollama") return;
    const runtime = model.runtime;
    const siblings = liveModels.filter((item) => item.runtime === runtime);
    const index = siblings.findIndex((item) => (item.id ?? item.name) === (model.id ?? model.name));
    const target = index + offset;
    if (index < 0 || target < 0 || target >= siblings.length) return;
    const order = siblings.map((item) => item.id ?? item.name);
    [order[index], order[target]] = [order[target], order[index]];
    setReordering(true);
    try {
      await (runtime === "lucebox" ? reorderLuceboxInstances(order) : reorderLlamaInstances(order));
      await refresh();
    } catch (e) {
      show(e instanceof Error ? e.message : "並べ替えに失敗しました", "error");
    } finally {
      setReordering(false);
    }
  };

  const removeLocal = useMutation({
    mutationFn: ({ alias, runtime, file }: { alias: string; runtime: LocalRuntime; file: boolean }) =>
      runtime === "lucebox" ? deleteLuceboxInstance(alias, file) : deleteLlamaInstance(alias, file),
    onSuccess: (result) => {
      show(result.gguf_deleted ? "設定とGGUFを削除しました"
           : result.reason ? `設定を削除しました（${result.reason}）` : "設定を削除しました（GGUFは保持）");
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
      await api(`/models/providers/${provider}/models/${encodeURIComponent(id)}/${action}`, { method: "POST", json: {} });
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
      <PageHeader title="Models" actions={<div className="flex items-center gap-2">
          {can("workflows.edit") && (
            <button onClick={() => setSettingsOpen(true)} aria-label="LLM 共通設定" title="LLM 共通設定" className="rounded-xl border border-zinc-300 px-3 py-2 text-sm text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300">⚙</button>
          )}
          {can("workflows.edit") && (
            <button onClick={() => isLocal ? setRegisterOpen(true) : setPulling(true)} className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700">
              <IconPlus /> {isLocal ? "モデル登録" : "モデル取得"}
            </button>
          )}
        </div>} />
      {/* タブ: LLM/VLM・Embed/Reranker・TTS */}
      <div className="mb-3 flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
        {([["llm", "LLM / VLM"], ["embed", "Embed / Reranker"], ["tts", "TTS"]] as const).map(([id, label]) => (
          <button key={id} onClick={() => setParams(id === "llm" ? {} : { tab: id }, { replace: true })}
            className={`flex-1 rounded-lg py-1.5 text-xs font-medium transition ${tab === id ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"}`}>
            {label}
          </button>
        ))}
      </div>
      {tab === "llm" && (
      <p className="mb-4 text-xs text-zinc-400">
        選択中: {isLocal
          ? `${RUNTIME_LABEL[selectedRuntime] ?? selectedRuntime} / ${BACKEND_LABEL[runtimeEnv?.policy.selected_backend ?? ""] ?? runtimeEnv?.policy.selected_backend ?? "-"}`
          : "Ollama"}。モデルの登録・ロード・アンロード・個別設定を管理します。
        {isLocal && "ゲートウェイは llama.cpp と Lucebox の両方を同じアドレスで配ります。"}
        {!isLocal && status && (status.available ? ` · Ollama ${status.version}` : " · Ollama に接続できません")}
      </p>
      )}

      <ActiveModelJobs />
      {tab === "llm" && <div className="mb-3"><CapacityWidget /></div>}
      {tab === "embed" && <EmbedRerankPanel />}
      {tab === "tts" && <TtsPanel />}
      {tab === "llm" && (<>
      {!isLocal && status && !status.available ? (
        <div className="rounded-2xl border border-dashed border-amber-300 bg-amber-50 p-6 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
          Ollama（{status.base_url}）に接続できません。<code className="font-mono">ollama serve</code> の起動、または設定でエンドポイントを確認してください。
        </div>
      ) : isLoading ? (
        <Skeleton className="h-24" />
      ) : !liveModels || liveModels.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">
            モデルがありません。「{isLocal ? "モデル登録" : "モデル取得"}」から追加してください。
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {liveModels.map((m) => {
            const id = m.id ?? m.name;
            const runtime = m.runtime === "ollama" ? undefined : m.runtime;
            const isLlama = runtime === "llama.cpp";
            const isLucebox = runtime === "lucebox";
            const endpoint = isLlama ? endpoints?.find((e) => e.aliases.includes(id)) : undefined;
            // 同じエンドポイントを共有する行は、ロードすると同居モデルが止まることを明示する。
            const shared = endpoint && endpoint.aliases.length > 1;
            const openDetail = () => isLucebox ? setLuceboxDetail(id) : isLlama ? setLlamaDetail(id) : setDetail(m.name);
            return (
            <li key={id} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex items-center gap-2 sm:gap-3">
                {isLocal && can("workflows.edit") && liveModels.filter((item) => item.runtime === runtime).length > 1 && (
                  <div className="flex shrink-0 flex-col">
                    <button type="button" onClick={() => move(m, -1)} disabled={reordering}
                      aria-label={`${m.name}を上へ移動`} title="優先度を上げる（同じランタイム内）"
                      className="grid h-6 w-8 place-items-center rounded text-zinc-400 hover:text-zinc-700 disabled:opacity-25 dark:hover:text-zinc-200">↑</button>
                    <button type="button" onClick={() => move(m, 1)} disabled={reordering}
                      aria-label={`${m.name}を下へ移動`} title="優先度を下げる（同じランタイム内）"
                      className="grid h-6 w-8 place-items-center rounded text-zinc-400 hover:text-zinc-700 disabled:opacity-25 dark:hover:text-zinc-200">↓</button>
                  </div>
                )}
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${m.loaded ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`} title={m.loaded ? "ロード中" : "未ロード"} />
                <button onClick={openDetail}
                  aria-label={isLocal ? `${id}の個別設定を開く` : `${m.name}の詳細を開く`}
                  className="min-w-0 flex-1 text-left">
                  <p className="flex items-center gap-1.5 truncate text-sm font-semibold">
                    {m.name}
                    {/* どのランタイムが載せるモデルかを一覧で見分けられるようにする。 */}
                    {isLucebox && (
                      <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
                        LUCEBOX
                      </span>
                    )}
                    {isLucebox && m.speculative && (
                      <span className="shrink-0 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
                        DFLASH
                      </span>
                    )}
                    {shared && (
                      <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-normal text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                        :{endpoint!.port} 共有
                      </span>
                    )}
                    {m.vision_enabled && (
                      <span className="shrink-0 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-violet-700 dark:bg-violet-950/60 dark:text-violet-300">
                        VISION
                      </span>
                    )}
                  </p>
                  <p className="num truncate text-xs text-zinc-400">
                    {RUNTIME_LABEL[runtime ?? "ollama"] ?? "Ollama"}
                    {m.backend_label ? ` / ${m.backend_label}` : ""} · {gb(m.size)}
                    {m.parameter_size && ` · ${m.parameter_size}`}{m.quantization && ` · ${m.quantization}`}
                    {m.port ? ` · :${m.port}` : ""}
                    {m.loaded && m.vram ? ` · VRAM ${gb(m.vram)}` : ""}
                  </p>
                </button>
                {can("workflows.edit") && (
                  <>
                    <button disabled={acting === id} onClick={() => act(id, m.loaded ? "unload" : "load", shared ? endpoint!.running_alias : "", runtime)} className="shrink-0 rounded-xl bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 disabled:cursor-wait disabled:opacity-60 dark:bg-zinc-800 dark:text-zinc-300">
                      {acting === id ? (m.loaded ? "停止中..." : "ロード中...") : (m.loaded ? "アンロード" : "ロード")}
                    </button>
                    {isLocal && runtime ? (
                      <DropdownMenu
                        ariaLabel={`${m.name}の操作`}
                        trigger={<IconDots />}
                        items={[
                          { label: "詳細設定", onSelect: openDetail },
                          // 複製はエンドポイント共有が前提の機能なので llama.cpp だけ。
                          ...(isLlama ? [{ label: "複製", onSelect: () => setDuplicating(id) }] : []),
                          { label: "削除", danger: true, separated: true,
                            onSelect: () => setLocalDeleting({ alias: id, runtime }) },
                        ]}
                      />
                    ) : (
                      <button onClick={() => setDeleting(m.name)} aria-label="削除" className="shrink-0 rounded-lg p-2 text-zinc-400 hover:text-red-600"><IconTrash /></button>
                    )}
                  </>
                )}
              </div>
            </li>
            );
          })}
        </ul>
      )}
      </>)}

      {pulling && <PullSheet onClose={() => setPulling(false)} onDone={refresh} />}
      {settingsOpen && <SettingsSheet onClose={() => setSettingsOpen(false)} />}
      {detail && <DetailSheet model={detail} onClose={() => setDetail(null)} />}
      {llamaDetail && <LlamaDetailSheet alias={llamaDetail} onClose={() => setLlamaDetail(null)} />}
      {luceboxDetail && <LuceboxDetailSheet alias={luceboxDetail} onClose={() => setLuceboxDetail(null)} />}
      {registerOpen && <ModelRegisterSheet onClose={() => { setRegisterOpen(false); refresh(); }} />}
      {deleting && (
        <ConfirmDialog title={`「${deleting}」を削除しますか？`} message="モデルファイルが削除されます。取り消せません。" confirmLabel="削除する" busy={del.isPending} onConfirm={() => del.mutate(deleting)} onClose={() => setDeleting(null)} />
      )}
      {localDeleting && (
        <ConfirmDialog
          title={`「${localDeleting.alias}」を削除しますか？`}
          message={`${RUNTIME_LABEL[localDeleting.runtime]} のモデル設定と systemd unit を削除します。`}
          confirmLabel="削除する"
          danger
          busy={removeLocal.isPending}
          onConfirm={() => removeLocal.mutate({ ...localDeleting, file: deleteFile })}
          onClose={() => { setLocalDeleting(null); setDeleteFile(false); }}
        >
          <label className="mt-2 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50/60 px-3 py-2.5 dark:border-red-900 dark:bg-red-950/20">
            <input type="checkbox" checked={deleteFile} onChange={(e) => setDeleteFile(e.target.checked)} className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="text-xs">GGUF ファイル本体も削除する
              <span className="block text-[10px] text-zinc-500">
                取り消せません。他のモデル設定が同じファイルを参照している場合は削除されません。
              </span>
            </span>
          </label>
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
      <div className="mb-3 flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
        <button onClick={() => setTab("registry")} className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${tab === "registry" ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>Ollama レジストリ</button>
        <button onClick={() => setTab("hf")} className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${tab === "hf" ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>HuggingFace (GGUF)</button>
        <button onClick={() => setTab("local")} className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${tab === "local" ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>ローカル登録</button>
      </div>

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
          <div className="flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
            {([["direct", "GGUFを直接取得"], ["ollama", "Ollamaへpull"]] as const).map(([id, label]) => (
              <button key={id} onClick={() => setHfMode(id)}
                className={`flex-1 rounded-lg py-1.5 text-[11px] font-medium ${hfMode === id ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>
                {label}
              </button>
            ))}
          </div>
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

/** 選択肢（プリセット）から選ぶ + 末尾の「カスタム」で手動入力するハイブリッド入力。 */
function PresetOrCustom({
  value,
  presets,
  placeholder,
  numeric = true,
  onChange,
}: {
  value: number | string | undefined;
  presets: { v: number | string; label: string }[];
  placeholder?: string;
  numeric?: boolean;
  onChange: (v: number | string | undefined) => void;
}) {
  const isPreset = value !== undefined && value !== "" && presets.some((p) => p.v === value);
  const [custom, setCustom] = useState(!isPreset && value !== undefined && value !== "");
  const sel = "rounded-xl border border-zinc-300 bg-white px-2.5 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <div className="flex gap-1.5">
      <select
        value={custom ? "__custom__" : value === undefined || value === "" ? "" : String(value)}
        onChange={(e) => {
          if (e.target.value === "__custom__") { setCustom(true); return; }
          setCustom(false);
          if (e.target.value === "") onChange(undefined);
          else onChange(numeric ? Number(e.target.value) : e.target.value);
        }}
        className={`${sel} ${custom ? "w-28 shrink-0" : "min-w-0 flex-1"}`}
      >
        <option value="">既定</option>
        {presets.map((p) => <option key={String(p.v)} value={String(p.v)}>{p.label}</option>)}
        <option value="__custom__">カスタム入力…</option>
      </select>
      {custom && (
        <input
          type={numeric ? "number" : "text"}
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : numeric ? Number(e.target.value) : e.target.value)}
          placeholder={placeholder}
          autoFocus
          className={`${sel} min-w-0 flex-1 font-mono text-xs`}
        />
      )}
    </div>
  );
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

const CTX_PRESETS = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144].map((v) => ({ v, label: v.toLocaleString() }));
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
        <L label="常駐時間 keep_alive"><PresetOrCustom value={eff.keep_alive} presets={KEEPALIVE_PRESETS} numeric={false} placeholder="30m / 1h" onChange={(v) => set("keep_alive", v)} /></L>
        <L label="コンテキスト長 num_ctx（大きいほどVRAM増）"><PresetOrCustom value={eff.num_ctx} presets={CTX_PRESETS} placeholder="8192" onChange={(v) => set("num_ctx", v)} /></L>
        <L label="Deep Research専用CTX">
          <PresetOrCustom value={eff.deep_research_num_ctx} presets={CTX_PRESETS} placeholder="例: 262144" onChange={(v) => set("deep_research_num_ctx", v)} />
        </L>
        <p className="text-[10px] leading-relaxed text-zinc-400">
          未設定なら通常CTXをそのまま使用します。異なる場合はDeep Research中だけrequestへ適用し、完了後に通常CTXへ戻します。
        </p>
        <L label="出力長 num_predict（最大生成トークン）"><PresetOrCustom value={eff.num_predict} presets={PREDICT_PRESETS} placeholder="512" onChange={(v) => set("num_predict", v)} /></L>
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
            <L label="温度 temperature（低=堅実 / 高=多様）"><PresetOrCustom value={eff.temperature} presets={TEMP_PRESETS} placeholder="0.7" onChange={(v) => set("temperature", v)} /></L>
            <L label="top_k"><PresetOrCustom value={eff.top_k} presets={TOPK_PRESETS} placeholder="40" onChange={(v) => set("top_k", v)} /></L>
            <L label="top_p"><PresetOrCustom value={eff.top_p} presets={TOPP_PRESETS} placeholder="0.9" onChange={(v) => set("top_p", v)} /></L>
            <L label="min_p"><PresetOrCustom value={eff.min_p} presets={MINP_PRESETS} placeholder="0.05" onChange={(v) => set("min_p", v)} /></L>
            <L label="繰り返し抑制 repeat_penalty"><PresetOrCustom value={eff.repeat_penalty} presets={REPEAT_PRESETS} placeholder="1.1" onChange={(v) => set("repeat_penalty", v)} /></L>
            <L label="GPU オフロード層数 num_gpu"><PresetOrCustom value={eff.num_gpu} presets={GPU_PRESETS} placeholder="-1" onChange={(v) => set("num_gpu", v)} /></L>
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
      show("LLMランタイム設定を適用しました");
    },
    onError: (e) => show(e instanceof Error ? e.message : "ランタイム設定の適用に失敗", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  if (!policy || !runtimeEnv) return null;
  const chooseRuntime = (item: RuntimeEnvironment["runtimes"][number]) => {
    if (!item.installed) return;
    savePolicy.mutate({
      selected_runtime: item.runtime,
      // ollama以外は「同じランタイム内のどのビルドか」を backend に持たせる
      // （llama.cpp は rocm/vulkan、Lucebox は ROCm トラック）。
      selected_backend: item.runtime === "ollama"
        ? "" : item.backend as RuntimePolicy["selected_backend"],
    });
  };
  return (
    <BottomSheet title="LLM 共通設定" onClose={onClose} wide>
      <div className="mb-4 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
        <p className="mb-2 text-xs font-semibold text-zinc-500">このPCで利用するランタイム</p>
        <p className="mb-2 text-[10px] text-zinc-400">{runtimeEnv.platform} · {runtimeEnv.gpu} GPU。利用可能な構成だけを表示しています。</p>
        <div className="grid gap-2 sm:grid-cols-3">
          {runtimeEnv.runtimes.filter((r) => r.available || r.installed).map((item) => {
            const selected = policy.selected_runtime === item.runtime && (item.runtime === "ollama" || policy.selected_backend === item.backend);
            return (
              <button key={item.id} type="button" onClick={() => chooseRuntime(item)} disabled={!item.installed || savePolicy.isPending}
                className={`rounded-xl border p-3 text-left disabled:opacity-50 ${selected ? "border-accent-500 bg-accent-50/60 ring-1 ring-accent-500 dark:bg-accent-600/10" : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-700"}`}>
                <span className="flex items-center gap-1.5 text-sm font-semibold">
                  {item.label}
                  {item.experimental && (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">実験的</span>
                  )}
                </span>
                <span className={`mt-1 block text-[10px] ${selected ? "text-accent-600 dark:text-accent-400" : "text-zinc-400"}`}>
                  {selected ? "● 使用中" : !item.installed ? "導入が必要" : item.running ? "稼働中 · 選択する" : "利用可能 · 選択する"}
                </span>
                {/* ホストROCmとビルドのメジャー不一致など、選ぶ前に知るべきことをその場に出す。 */}
                {item.warning && (
                  <span className="mt-1.5 block rounded-lg bg-amber-50 px-2 py-1.5 text-[10px] leading-relaxed text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                    {item.warning}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        {runtimeEnv.runtimes.some((item) => !item.installed && item.addon_id) && (
          <p className="mt-2 text-[10px] text-zinc-400">
            未導入のランタイムは <a href="/settings" className="text-accent-600 underline dark:text-accent-400">設定 → オプション機能</a> から導入・更新します。
          </p>
        )}
      </div>

      <div className="mb-4 space-y-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
        <p className="text-xs font-semibold text-zinc-500">全ランタイム共通</p>
        <L label="利用方式">
          <select value={policy.coexistence} onChange={(e) => setPolicyCfg({ ...policy, coexistence: e.target.value as RuntimePolicy["coexistence"] })} className={input}>
            <option value="exclusive">排他（推奨・VRAM競合を防ぐ）</option>
            <option value="coexist">共存（上級者向け）</option>
          </select>
        </L>
        {runtimeEnv.amd_gpu && (
          <AmdGpuProfilePanel env={runtimeEnv.amd_gpu} policy={policy} onChange={setPolicyCfg} />
        )}
        <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
          <span className="text-xs">アイドル時に自動アンロード</span>
          <input type="checkbox" checked={policy.idle_unload_enabled} onChange={(e) => setPolicyCfg({ ...policy, idle_unload_enabled: e.target.checked })} className="h-4 w-4" />
        </label>
        {policy.idle_unload_enabled && <L label="共通アイドル時間（分）"><PresetOrCustom value={policy.idle_unload_minutes} presets={[5, 15, 30, 60, 240].map((v) => ({ v, label: `${v}分` }))} placeholder="30" onChange={(v) => setPolicyCfg({ ...policy, idle_unload_minutes: Number(v ?? 30) })} /></L>}
        <L label="全ランタイムの同時ロード上限">
          <PresetOrCustom value={policy.max_loaded_models} presets={[1, 2, 3, 4, 8].map((v) => ({ v, label: `${v}モデル` }))} placeholder="1" onChange={(v) => setPolicyCfg({ ...policy, max_loaded_models: Number(v ?? 1) })} />
        </L>
        <p className="rounded-lg bg-zinc-50 px-2.5 py-2 text-[10px] leading-relaxed text-zinc-500 dark:bg-zinc-800/60">
          画像生成などGPUを使う他の処理は、VRAMが空いていなければシステムRAMへ載ります。
          LLMを一時停止して譲る動作はありません。
        </p>
        <p className="rounded-lg bg-zinc-50 px-2.5 py-2 text-[10px] leading-relaxed text-zinc-500 dark:bg-zinc-800/60">
          思考（reasoning）の設定は各モデルの個別設定へ移動しました。モデルごとに適した深さが
          違うため、共通設定で一律に指定しない方針です。
        </p>
        <div className="space-y-2 rounded-xl border border-violet-200 bg-violet-50/40 p-3 dark:border-violet-900 dark:bg-violet-950/20">
          <p className="text-xs font-semibold text-violet-700 dark:text-violet-300">Deep Research共通設定</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <L label="統合する根拠文字数上限">
              <PresetOrCustom value={policy.deep_research.evidence_context_chars} presets={[30000, 60000, 90000, 150000, 300000].map((v) => ({ v, label: v.toLocaleString() }))} placeholder="90000" onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, evidence_context_chars: Number(v ?? 90000) } })} />
            </L>
            <L label="レポート総出力token上限">
              <PresetOrCustom value={policy.deep_research.max_report_tokens} presets={[8192, 16384, 24576, 32768, 65536, 131072, 262144].map((v) => ({ v, label: v.toLocaleString() }))} placeholder="32768" onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, max_report_tokens: Number(v ?? 32768) } })} />
            </L>
            <L label="Deep Research生成timeout（秒）">
              <PresetOrCustom value={policy.deep_research.timeout_seconds} presets={[300, 600, 1200, 1800, 3600].map((v) => ({ v, label: `${v}秒` }))} placeholder="1800" onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, timeout_seconds: Number(v ?? 1800) } })} />
            </L>
          </div>
          <p className="text-[10px] text-zinc-500">専用CTXは全体へ強制せず、Ollama / llama.cppそれぞれのモデル個別設定で指定します。</p>
        </div>
        <L label="アシスタント表示名"><input value={policy.assistant_name} onChange={(e) => setPolicyCfg({ ...policy, assistant_name: e.target.value })} className={input} /></L>
        <button onClick={() => savePolicy.mutate(policy)} disabled={savePolicy.isPending} className="w-full rounded-xl bg-accent-600 py-2 text-xs font-medium text-white disabled:opacity-40">共通設定を適用</button>
      </div>

      <div className="mb-4">
        <ModelLibraryPanel />
      </div>

    </BottomSheet>
  );
}

function AmdGpuProfilePanel({ env, policy, onChange }: {
  env: NonNullable<RuntimeEnvironment["amd_gpu"]>;
  policy: RuntimePolicy;
  onChange: (next: RuntimePolicy) => void;
}) {
  const gpu = policy.amd_gpu;
  const setGpu = (patch: Partial<RuntimePolicy["amd_gpu"]>) => onChange({ ...policy, amd_gpu: { ...gpu, ...patch } });
  const choose = (profile: "quiet" | "balanced" | "full") => setGpu({ enabled: true, profile, ...env.presets[profile] });
  const labels = { quiet: "静音", balanced: "バランス", full: "フルパワー" } as const;
  return (
    <div className="space-y-2.5 rounded-xl border border-emerald-200 bg-emerald-50/30 p-3 dark:border-emerald-900 dark:bg-emerald-950/20">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">AMD GPU 電力・VRAM静音設定</p>
          <p className="text-[10px] text-zinc-400">{env.bdf} · 現在の電力上限 {env.power.current_watts}W · MCLK {env.memory.levels.find((v) => v.current)?.mhz ?? "N/A"}MHz</p>
        </div>
        <input type="checkbox" checked={gpu.enabled} onChange={(e) => setGpu({ enabled: e.target.checked })} aria-label="AMD GPU設定を有効化" className="h-4 w-4" />
      </div>
      {gpu.enabled && (
        <>
          <div className="grid grid-cols-3 gap-1.5">
            {(Object.keys(labels) as Array<keyof typeof labels>).map((profile) => (
              <button key={profile} type="button" onClick={() => choose(profile)}
                className={`rounded-lg border px-2 py-2 text-xs ${gpu.profile === profile ? "border-accent-500 bg-white font-semibold text-accent-700 dark:bg-zinc-900 dark:text-accent-300" : "border-zinc-200 bg-white/60 text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60"}`}>
                {labels[profile]}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setGpu({ profile: "custom" })} className="text-[11px] font-medium text-accent-600 dark:text-accent-400">カスタム設定</button>
          <p className="rounded-lg bg-white/70 px-2.5 py-2 text-[10px] leading-relaxed text-zinc-500 dark:bg-zinc-900/60">
            {gpu.profile === "quiet" && `静音: ${env.power.min_watts}W・MCLK上限 ${env.memory.levels[Math.max(0, env.memory.levels.length - 2)]?.mhz}MHz（最大から1段だけ低下）。`}
            {gpu.profile === "balanced" && `バランス: ${env.presets.balanced.power_limit_watts}W・MCLK自動。アイドル時は最低周波数へ戻ります。`}
            {gpu.profile === "full" && `フルパワー: 既定${env.power.default_watts}W・MCLK自動。性能優先です。`}
            {gpu.profile === "custom" && "実機が公開する安全範囲内で電力、VRAMクロック、GPUコア上限を個別指定します。"}
          </p>
          {gpu.profile === "custom" && (
            <div className="space-y-2.5">
              <L label={`電力上限 ${gpu.power_limit_watts}W（${env.power.min_watts}〜${env.power.max_watts}W）`}>
                <input type="range" min={env.power.min_watts} max={env.power.max_watts} step={1} value={gpu.power_limit_watts}
                  onChange={(e) => setGpu({ power_limit_watts: Number(e.target.value) })} className="w-full accent-current" />
              </L>
              {env.memory.supported && <L label="VRAMクロック上限（MCLK）">
                <select value={gpu.memory_clock_mode === "auto" ? "auto" : String(gpu.memory_clock_level)}
                  onChange={(e) => e.target.value === "auto" ? setGpu({ memory_clock_mode: "auto", memory_clock_level: 0 }) : setGpu({ memory_clock_mode: "limit", memory_clock_level: Number(e.target.value) })}
                  className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="auto">自動（既定・アイドル時は最低へ低下）</option>
                  {env.memory.levels.map((item) => <option key={item.level} value={item.level}>{item.mhz}MHz 以下</option>)}
                </select>
              </L>}
              {env.core.supported && <L label="GPUコアクロック上限（SCLK）">
                <select value={gpu.core_clock_mode === "auto" ? "auto" : String(gpu.core_clock_level)}
                  onChange={(e) => e.target.value === "auto" ? setGpu({ core_clock_mode: "auto", core_clock_level: 0 }) : setGpu({ core_clock_mode: "limit", core_clock_level: Number(e.target.value) })}
                  className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="auto">自動（既定）</option>
                  {env.core.levels.filter((item) => item.mhz > 0).map((item) => <option key={item.level} value={item.level}>{item.mhz}MHz 以下</option>)}
                </select>
              </L>}
            </div>
          )}
          {!env.helper_installed && <p className="text-[10px] text-amber-700 dark:text-amber-300">適用helperが未登録です。サーバーで ./deck.sh service を実行すると登録されます。</p>}
          <p className="text-[10px] text-zinc-400">設定はサーバーへ保存され、Control Deck経由のチャット・ワークフロー・手動/自動モデル起動前に適用されます。</p>
        </>
      )}
    </div>
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
function L({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>{children}</label>;
}

interface LlamaInstanceConfig {
  model_path: string;
  mmproj_path?: string;
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
  "model_path", "mmproj_path", "port", "alias", "auto_start", "idle_exclude",
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
  "mmproj_path", "n_gpu_layers", "ctx_size", "deep_research_ctx_size", "n_parallel", "flash_attn", "n_predict",
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
    <BottomSheet title={`${alias} · モデル個別設定`} onClose={onClose} wide>
      {!instance ? <p className="text-xs text-zinc-400">読み込み中...</p> : (
        <LlamaInstanceControls
          initial={instance}
          onChanged={() => {
            qc.invalidateQueries({ queryKey: ["llama-status"] });
            qc.invalidateQueries({ queryKey: ["models", "llama.cpp"] });
          }}
        />
      )}
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
  const missing = st.selectable_backends.filter((backend) => !st.installed_backends.includes(backend));

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
function LlamaInstanceControls({ initial, isNew = false, onCancel, onDelete, onChanged }: {
  initial: LlamaInstanceConfig;
  isNew?: boolean;
  onCancel?: () => void;
  onDelete?: () => void;
  onChanged: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [pickerOpen, setPickerOpen] = useState<"model" | "draft" | null>(null);
  const [advanced, setAdvanced] = useState(false);
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
    try {
      await api(isNew ? "/models/llama/instances" : `/models/llama/instances/${encodeURIComponent(originalAlias)}`, {
        method: isNew ? "POST" : "PUT",
        json: llamaInstanceWriteBody(cfg, isNew),
      });
      if (start) {
        await api(`/models/providers/llama.cpp/models/${encodeURIComponent(cfg.alias)}/load`, { method: "POST", json: {} });
        show("保存してllama.cppを起動しました（初回はモデル読み込みに時間がかかります）");
      } else {
        show("モデル個別設定をサーバーへ保存しました");
      }
      onChanged();
    } catch (e) { show(e instanceof Error ? e.message : "保存に失敗", "error"); }
  };
  const stop = async () => {
    try { await api(`/models/providers/llama.cpp/models/${encodeURIComponent(cfg.alias)}/unload`, { method: "POST" }); show("停止しました"); onChanged(); }
    catch (e) { show(e instanceof Error ? e.message : "停止に失敗", "error"); }
  };

  return (
    <div className="space-y-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <div>
        <p className="text-xs font-semibold text-zinc-500">{isNew ? "新しい" : cfg.alias} · llama.cppモデル個別設定</p>
        <p className="mt-0.5 text-[10px] text-zinc-400">GGUFごとに必要なCTX・KV・MTP・MoE設定を保存し、起動時に反映します。</p>
      </div>
      {isNew && <>
        <div className="flex gap-1.5">
          <input value={cfg.model_path} onChange={(e) => chooseModelPath(e.target.value)} placeholder="GGUF ファイルのパス" className={`${input} min-w-0 flex-1 font-mono text-xs`} />
          <button onClick={() => setPickerOpen("model")} aria-label="GGUFを選択" className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">📁</button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <L label="モデル名（alias）"><input value={cfg.alias} onChange={(e) => set("alias", e.target.value)} className={`${input} font-mono`} /></L>
          <L label="待受port"><input type="number" min={1024} max={65535} value={cfg.port} onChange={(e) => set("port", Number(e.target.value))} className={`${input} font-mono`} /></L>
        </div>
      </>}
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
      <div className="grid grid-cols-2 gap-2">
        <L label="コンテキスト長（CTX）"><PresetOrCustom value={cfg.ctx_size} presets={CTX_PRESETS} placeholder="8192" onChange={(v) => set("ctx_size", Number(v ?? 4096))} /></L>
        <L label="Deep Research専用CTX"><PresetOrCustom value={cfg.deep_research_ctx_size || undefined} presets={CTX_PRESETS} placeholder="例: 262144" onChange={(v) => set("deep_research_ctx_size", Number(v ?? 0))} /></L>
        <L label="最大出力トークン"><PresetOrCustom value={cfg.n_predict} presets={PREDICT_PRESETS} placeholder="2048" onChange={(v) => set("n_predict", Number(v ?? 2048))} /></L>
        <L label="GPUオフロード層"><PresetOrCustom value={cfg.n_gpu_layers} presets={GPU_PRESETS.map((p) => p.v === -1 ? { ...p, v: 999, label: "全部 (999)" } : p)} placeholder="999" onChange={(v) => set("n_gpu_layers", Number(v ?? 999))} /></L>
      </div>
      <p className="text-[10px] leading-relaxed text-zinc-400">Deep Research専用CTXが通常CTXと異なる場合、開始前に再ロードし、完了・失敗後は通常CTXへ自動復元します。</p>

      <L label="最大同時リクエスト数（スロット）">
        <input type="number" min={1} max={64} value={cfg.n_parallel}
          onChange={(e) => set("n_parallel", Math.max(1, Math.min(64, Number(e.target.value) || 1)))}
          className={`${input} font-mono`} />
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

      <ThinkingControl
        runtime="llama.cpp"
        mode={cfg.think ?? "auto"}
        budget={cfg.think_budget_tokens ?? 0}
        onChange={(mode, budget) => setCfg((c) => ({ ...c, think: mode, think_budget_tokens: budget }))}
      />
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
                  className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">📁</button>
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

      <button type="button" onClick={() => setAdvanced((value) => !value)} className="text-xs font-medium text-accent-600 dark:text-accent-400">
        {advanced ? "▾ 上級設定を隠す" : "▸ 上級設定（batch・thread・sampling・RAM）"}
      </button>
      {advanced && <div className="space-y-2.5 border-t border-zinc-100 pt-2.5 dark:border-zinc-800">
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
        <Toggle label="PC起動時に自動起動" hint="このinstanceのsystemd user unitをenableします。起動前にGPU profileを適用します" value={cfg.auto_start} onChange={(value) => set("auto_start", value)} />
        <Toggle label="共通アイドル停止から除外" hint="直接endpointを使う外部clientは利用時刻を追跡できないため、常用時は除外を推奨" value={cfg.idle_exclude} onChange={(value) => set("idle_exclude", value)} />
      </div>}
      <div className={`grid gap-1.5 ${isNew ? "grid-cols-2" : "grid-cols-3"}`}>
        <button onClick={() => persist(false)} className="rounded-xl bg-zinc-100 py-2 text-xs font-medium hover:bg-zinc-200 dark:bg-zinc-800">{isNew ? "登録" : "保存"}</button>
        <button onClick={() => persist(true)} className="rounded-xl bg-accent-600 py-2 text-xs font-medium text-white hover:bg-accent-700">保存して起動</button>
        {!isNew && <button onClick={stop} className="rounded-xl bg-zinc-100 py-2 text-xs font-medium hover:bg-zinc-200 dark:bg-zinc-800">停止</button>}
      </div>
      <div className="flex gap-2">
        {isNew && onCancel && <button onClick={onCancel} className="text-xs text-zinc-500">キャンセル</button>}
        {!isNew && onDelete && <button onClick={onDelete} className="ml-auto text-xs text-red-500">この設定を削除</button>}
      </div>
      {cfg.port && (
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

function Toggle({ label, hint, value, disabled = false, onChange }: { label: string; hint?: string; value: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center justify-between gap-3 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
    <span className="text-xs">{label}{hint && <span className="block text-[10px] font-normal text-zinc-400">{hint}</span>}</span>
    <input type="checkbox" checked={value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 shrink-0 disabled:opacity-40" />
  </label>;
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

interface RolePreset {
  id: string; label: string; description: string; role: string; alias: string;
  file_exists: boolean; installed: boolean; loaded: boolean; idle_exclude: boolean;
  runtime_status: string;
}

/** Embed / Reranker タブ: 推奨プリセットのワンタップ導入と稼働管理。 */
function EmbedRerankPanel() {
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const [acting, setActing] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey: ["role-presets"],
    queryFn: () => api<{ presets: RolePreset[] }>("/models/llama/role-presets"),
    refetchInterval: 8000,
  });
  const { data: llamaSt } = useQuery({ queryKey: ["llama-status"], queryFn: () => api<LlamaStatus>("/models/llama/status") });

  const install = async (preset: RolePreset) => {
    try {
      await api(`/models/llama/role-presets/${preset.id}/install-jobs`, { method: "POST", json: {} });
      show(`${preset.label} の導入を開始しました（サーバー側で継続）`, "info");
    } catch (e) { show(e instanceof Error ? e.message : "導入開始に失敗", "error"); }
  };
  const act = async (preset: RolePreset, action: "load" | "unload") => {
    setActing(preset.id);
    try {
      await api(`/models/providers/llama.cpp/models/${encodeURIComponent(preset.alias)}/${action}`, { method: "POST", json: {} });
      show(action === "load" ? "ロードしました" : "アンロードしました");
      qc.invalidateQueries({ queryKey: ["role-presets"] });
    } catch (e) { show(e instanceof Error ? e.message : "失敗しました", "error"); }
    finally { setActing(null); }
  };
  const toggleResident = async (preset: RolePreset, value: boolean) => {
    try {
      await api(`/models/llama/instances/${encodeURIComponent(preset.alias)}`, { method: "PUT", json: { idle_exclude: value } });
      show(value ? "GPU常駐を有効にしました" : "アイドル時に自動アンロードします");
      qc.invalidateQueries({ queryKey: ["role-presets"] });
    } catch (e) { show(e instanceof Error ? e.message : "設定に失敗", "error"); }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-400">
        RAG検索の品質を上げる補助モデルです。RAG利用時に自動ロードされ、アイドルで自動アンロードされます（常駐も選択可）。
      </p>
      {llamaSt && !llamaSt.installed && (
        <div className="rounded-2xl border border-dashed border-amber-300 bg-amber-50 p-4 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
          llama.cpp が未導入です。LLM/VLMタブの「GGUF登録」→ 導入から先にセットアップしてください。
        </div>
      )}
      {(data?.presets ?? []).map((preset) => {
        const running = preset.runtime_status === "RUNNING" || preset.loaded;
        return (
          <div key={preset.id} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-center gap-3">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${running ? "bg-emerald-500" : preset.installed ? "bg-zinc-300 dark:bg-zinc-600" : "bg-zinc-200 dark:bg-zinc-700"}`}
                title={running ? "稼働中" : preset.installed ? "停止中" : "未導入"} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{preset.label}
                  <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${preset.role === "embedding" ? "bg-sky-100 text-sky-700 dark:bg-sky-900/50 dark:text-sky-300" : "bg-violet-100 text-violet-700 dark:bg-violet-900/50 dark:text-violet-300"}`}>
                    {preset.role === "embedding" ? "埋め込み" : "再ランク"}
                  </span>
                </p>
                <p className="mt-0.5 text-xs text-zinc-400">{preset.description}</p>
              </div>
              {can("workflows.edit") && (
                preset.installed ? (
                  <button disabled={acting === preset.id} onClick={() => act(preset, running ? "unload" : "load")}
                    className="shrink-0 rounded-xl bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700 disabled:cursor-wait disabled:opacity-60 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300">
                    {acting === preset.id ? "..." : running ? "アンロード" : "ロード"}
                  </button>
                ) : (
                  <button onClick={() => install(preset)} disabled={!llamaSt?.installed}
                    className="shrink-0 rounded-xl bg-accent-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">
                    導入
                  </button>
                )
              )}
            </div>
            {preset.installed && can("workflows.edit") && (
              <label className="mt-3 flex items-center justify-between rounded-xl border border-zinc-100 px-3 py-2 dark:border-zinc-800">
                <span className="text-xs">GPU常駐（アイドル自動アンロードから除外）
                  <span className="block text-[10px] text-zinc-400">RAGの初回応答を速くする代わりにVRAMを使い続けます</span>
                </span>
                <input type="checkbox" checked={preset.idle_exclude} onChange={(e) => toggleResident(preset, e.target.checked)} className="h-4 w-4" />
              </label>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** TTS タブ（今後対応のプレースホルダー）。 */
function TtsPanel() {
  return (
    <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
      <p className="text-2xl">🔊</p>
      <p className="mt-2 text-sm font-medium">TTS（音声合成）</p>
      <p className="mt-1 text-xs text-zinc-400">今後のアップデートで対応予定です。モデル管理・音声設定はこのタブに追加されます。</p>
    </div>
  );
}

/** モデル登録の入口。llama.cpp と Lucebox で必要な情報が違うので、最初にランタイムを選ばせる。
 *
 * 両者を1つのフォームに混ぜると「ドラフトGGUFは何のためか」「mmprojはどちらで効くか」が
 * 分からなくなる。登録画面の時点で明確に分ける。
 */
function ModelRegisterSheet({ onClose }: { onClose: () => void }) {
  const { data: lucebox } = useQuery({ queryKey: ["lucebox-status"], queryFn: getLuceboxStatus });
  const luceboxUsable = Boolean(lucebox?.installed);
  const [runtime, setRuntime] = useState<LocalRuntime>("llama.cpp");
  return (
    <BottomSheet title="モデル登録" onClose={onClose} wide>
      <div className="mb-4 grid gap-2 sm:grid-cols-2">
        {(LOCAL_RUNTIMES).map((item) => {
          const selected = runtime === item;
          const disabled = item === "lucebox" && !luceboxUsable;
          return (
            <button key={item} type="button" disabled={disabled} onClick={() => setRuntime(item)}
              className={`rounded-xl border p-3 text-left disabled:opacity-50 ${selected ? "border-accent-500 bg-accent-50/60 ring-1 ring-accent-500 dark:bg-accent-600/10" : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-700"}`}>
              <span className="block text-sm font-semibold">
                {RUNTIME_LABEL[item]}
                {item === "lucebox" && (
                  <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">DFLASH</span>
                )}
              </span>
              <span className="mt-1 block text-[10px] leading-relaxed text-zinc-500">
                {item === "llama.cpp"
                  ? "GGUF 1本を登録します。VISION（mmproj）や投機デコードもここで設定します"
                  : disabled
                    ? "未導入です。設定 → オプション機能から Lucebox を導入してください"
                    : `ターゲットGGUF + DFlashドラフトGGUFの組で登録します（${lucebox?.track_label ?? "ROCm 10"}）`}
              </span>
            </button>
          );
        })}
      </div>
      {runtime === "llama.cpp"
        ? <LlamaRuntimePanel registrationOnly />
        : <LuceboxRegisterPanel status={lucebox} onDone={onClose} />}
    </BottomSheet>
  );
}

/** Lucebox の新規モデル登録。推奨値（AMDLucebox の実測プロファイル）を初期値にする。 */
function LuceboxRegisterPanel({ status, onDone }: { status?: LuceboxStatus; onDone: () => void }) {
  const qc = useQueryClient();
  if (!status) return <p className="text-xs text-zinc-400">読み込み中...</p>;
  const usedPorts = status.instances.map((item) => item.port);
  const initial: LuceboxInstance = {
    ...status.defaults,
    alias: "",
    model_path: "",
    draft_path: "",
    port: usedPorts.length ? Math.max(...usedPorts) + 1 : status.defaults.port,
    runtime: "lucebox", role: "llm", loaded: false, runtime_status: "STOPPED",
    unit: "", base_url: "", selected: false,
  };
  return (
    <div className="space-y-3">
      <LuceboxRuntimeSummary status={status} />
      <LuceboxInstanceControls
        key="new"
        initial={initial}
        isNew
        onChanged={() => {
          qc.invalidateQueries({ queryKey: ["lucebox-status"] });
          qc.invalidateQueries({ queryKey: ["models", "local"] });
          onDone();
        }}
      />
    </div>
  );
}

/** 導入済みLuceboxの版・トラック・環境整合を1行で示す。 */
function LuceboxRuntimeSummary({ status }: { status: LuceboxStatus }) {
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
        Lucebox
        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">実験的</span>
        {status.installed
          ? <span className="num text-xs font-normal text-emerald-600 dark:text-emerald-400">{status.tag} · {status.track_label}</span>
          : <span className="text-xs font-normal text-zinc-400">未導入</span>}
      </p>
      <p className="num mt-1 text-[10px] text-zinc-400">
        GPU {status.environment.gfx || "未検出"} · ホストROCm {status.environment.rocm_version || "不明"}
        {status.upstream && ` · upstream ${status.upstream.slice(0, 12)}`}
      </p>
      {status.warning && (
        <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50/60 p-2 text-[10px] leading-relaxed text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {status.warning}
        </p>
      )}
    </div>
  );
}

function LuceboxDetailSheet({ alias, onClose }: { alias: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: status } = useQuery({ queryKey: ["lucebox-status"], queryFn: getLuceboxStatus });
  const instance = status?.instances.find((item) => item.alias === alias);
  return (
    <BottomSheet title={`${alias} · Luceboxモデル個別設定`} onClose={onClose} wide>
      {!status ? <p className="text-xs text-zinc-400">読み込み中...</p>
        : !instance ? <p className="text-xs text-zinc-400">モデル設定が見つかりません</p> : (
        <div className="space-y-3">
          <LuceboxRuntimeSummary status={status} />
          <LuceboxInstanceControls
            initial={instance}
            onChanged={() => {
              qc.invalidateQueries({ queryKey: ["lucebox-status"] });
              qc.invalidateQueries({ queryKey: ["models", "local"] });
            }}
          />
        </div>
      )}
    </BottomSheet>
  );
}

/** Luceboxモデル設定のフォーム。既定値はAMDLucebox READMEの実測プロファイルに合わせる。 */
function LuceboxInstanceControls({ initial, isNew = false, onChanged }: {
  initial: LuceboxInstance;
  isNew?: boolean;
  onChanged: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [picker, setPicker] = useState<"model" | "draft" | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [cfg, setCfg] = useState<LuceboxInstance>({ ...initial });
  const [busy, setBusy] = useState(false);
  const originalAlias = initial.alias;
  const set = <K extends keyof LuceboxInstance>(key: K, value: LuceboxInstance[K]) =>
    setCfg((current) => ({ ...current, [key]: value }));
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  const body = (): LuceboxInstanceInput => {
    const common = {
      model_path: cfg.model_path, draft_path: cfg.draft_path, max_ctx: cfg.max_ctx,
      draft_block_size: cfg.draft_block_size, cache_type_k: cfg.cache_type_k,
      cache_type_v: cfg.cache_type_v, fa_window: cfg.fa_window, ddtree: cfg.ddtree,
      ddtree_budget: cfg.ddtree_budget, default_max_tokens: cfg.default_max_tokens,
      draft_residency: cfg.draft_residency, fast_rollback: cfg.fast_rollback,
      prefer_speculative: cfg.prefer_speculative, agent_turn_cache: cfg.agent_turn_cache,
      auto_start: cfg.auto_start, idle_exclude: cfg.idle_exclude,
    };
    // 識別子（alias / port）は新規登録のときだけ送る。既存の変更は共通設定APIと同じ境界にする。
    return isNew ? { ...common, alias: cfg.alias, port: cfg.port } : common;
  };

  const persist = async (start: boolean) => {
    if (!cfg.model_path) { show("ターゲットGGUFを選択してください", "error"); return; }
    if (isNew && !cfg.alias) { show("モデル名（alias）を入力してください", "error"); return; }
    setBusy(true);
    try {
      if (isNew) await createLuceboxInstance(body());
      else await updateLuceboxInstance(originalAlias, body());
      if (start) {
        await api(`/models/providers/lucebox/models/${encodeURIComponent(cfg.alias)}/load`, { method: "POST", json: {} });
        show("保存してLuceboxを起動しました（ターゲットとドラフトの読み込みに時間がかかります）");
      } else {
        show("Luceboxモデル設定を保存しました");
      }
      onChanged();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存に失敗しました", "error");
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    try {
      await api(`/models/providers/lucebox/models/${encodeURIComponent(cfg.alias)}/unload`, { method: "POST" });
      show("停止しました");
      onChanged();
    } catch (e) { show(e instanceof Error ? e.message : "停止に失敗しました", "error"); }
  };

  return (
    <div className="space-y-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <div>
        <p className="text-xs font-semibold text-zinc-500">{isNew ? "新しい" : cfg.alias} · Luceboxモデル個別設定</p>
        <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-400">
          ターゲットGGUFとDFlashドラフトGGUFの組で動きます。初期値はAMDLuceboxの実測プロファイル
          （ブロック幅16・CTX131072・KV q8_0）です。
        </p>
      </div>
      <L label="ターゲットGGUF">
        <div className="flex gap-1.5">
          <input value={cfg.model_path} onChange={(e) => set("model_path", e.target.value)}
            placeholder="例: /data1tb/LLM/.../Qwen3.8-27B-UD-IQ4_XS.gguf"
            className={`${input} min-w-0 flex-1 font-mono text-xs`} />
          <button onClick={() => setPicker("model")} aria-label="ターゲットGGUFを選択"
            className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">📁</button>
        </div>
      </L>
      <L label="DFlashドラフトGGUF（空なら投機デコードなし）">
        <div className="flex gap-1.5">
          <input value={cfg.draft_path} onChange={(e) => set("draft_path", e.target.value)}
            placeholder="例: /data1tb/LLM/.../qwen38-dflash2-q8_0.gguf"
            className={`${input} min-w-0 flex-1 font-mono text-xs`} />
          <button onClick={() => setPicker("draft")} aria-label="ドラフトGGUFを選択"
            className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">📁</button>
        </div>
      </L>
      {isNew && (
        <div className="grid grid-cols-2 gap-2">
          <L label="モデル名（alias）">
            <input value={cfg.alias} onChange={(e) => set("alias", e.target.value)}
              placeholder="lucebox-qwen38" className={`${input} font-mono`} />
          </L>
          <L label="待受port">
            <input type="number" min={1024} max={65535} value={cfg.port}
              onChange={(e) => set("port", Number(e.target.value))} className={`${input} font-mono`} />
          </L>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <L label="最大CTX">
          <PresetOrCustom value={cfg.max_ctx}
            presets={[8192, 32768, 65536, 131072].map((v) => ({ v, label: v.toLocaleString() }))}
            placeholder="131072" onChange={(v) => set("max_ctx", Number(v ?? 131072))} />
        </L>
        <L label="ドラフトブロック幅">
          <PresetOrCustom value={cfg.draft_block_size}
            presets={[8, 16, 24, 32].map((v) => ({ v, label: String(v) }))}
            placeholder="16" onChange={(v) => set("draft_block_size", Number(v ?? 16))} />
        </L>
        <L label="FAウィンドウ（0=全注意・推奨）">
          <PresetOrCustom value={cfg.fa_window}
            presets={[0, 1024, 2048, 4096].map((v) => ({ v, label: v === 0 ? "0（全注意）" : String(v) }))}
            placeholder="0" onChange={(v) => set("fa_window", Number(v ?? 0))} />
        </L>
      </div>
      {cfg.fa_window > 0 && (
        <p className="rounded-lg border border-amber-200 bg-amber-50/60 p-2 text-[10px] leading-relaxed text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          FAウィンドウが0より大きいと、長いコンテキストでシステムプロンプトとツール定義が
          注意から外れます。OpenCodeなどツールを使う用途では、引数の欠けたツール呼び出しが
          返り会話が止まります。速度差は実測でほぼ無いため、0を推奨します。
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <L label="KVキャッシュ K"><CacheTypeSelect value={cfg.cache_type_k} onChange={(v) => set("cache_type_k", v)} input={input} /></L>
        <L label="KVキャッシュ V"><CacheTypeSelect value={cfg.cache_type_v} onChange={(v) => set("cache_type_v", v)} input={input} /></L>
      </div>
      <div className="space-y-1.5 rounded-xl border border-emerald-200 bg-emerald-50/40 p-2.5 dark:border-emerald-900 dark:bg-emerald-950/20">
        <Toggle
          label="投機デコードを優先（temperature を 0 に固定）"
          hint="DFlash2の検証は厳密グリーディのみです。temperatureが0より大きいと投機デコードが使われず自己回帰へ落ちます。コード・英語向け"
          value={cfg.prefer_speculative}
          onChange={(v) => set("prefer_speculative", v)}
        />
        <p className="text-[10px] leading-relaxed text-zinc-500">
          速度はドラフトの採択長で決まり、生成する内容で大きく変わります。
          実測（Qwen3.8-27B + DFlash2 q8_0、自己回帰は内容によらず約29 tok/s）:
          <span className="mt-1 block font-medium">
            英語コード 92〜152 tok/s（3〜5倍） · 英語の文章 42〜48 tok/s（1.5倍） ·
            <span className="text-amber-700 dark:text-amber-400"> 日本語 23〜25 tok/s（自己回帰より遅い）</span>
          </span>
          {cfg.prefer_speculative
            ? "日本語主体で使うならOFFの方が速く、サンプリングも効きます。"
            : "呼び出し側のtemperatureをそのまま使います（投機デコードは無効）。"}
        </p>
      </div>
      <Toggle label="自動起動" hint="ControlDeck起動時にこのモデルを常駐させます" value={cfg.auto_start} onChange={(v) => set("auto_start", v)} />
      <button type="button" onClick={() => setAdvanced((v) => !v)}
        className="w-full rounded-xl border border-zinc-200 px-3 py-2 text-left text-xs text-zinc-500 dark:border-zinc-700">
        {advanced ? "▾" : "▸"} 詳細設定（DDTree・ドラフト常駐・出力上限）
      </button>
      {advanced && (
        <div className="space-y-2.5 rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
          <Toggle label="DDTree検証" hint="ドラフト候補を木で検証します。ドラフト未設定のときは無視されます" value={cfg.ddtree} onChange={(v) => set("ddtree", v)} />
          {cfg.ddtree && (
            <L label="DDTree予算">
              <PresetOrCustom value={cfg.ddtree_budget}
                presets={[16, 22, 32, 48].map((v) => ({ v, label: String(v) }))}
                placeholder="22" onChange={(v) => set("ddtree_budget", Number(v ?? 22))} />
            </L>
          )}
          <L label="ドラフト常駐">
            <select value={cfg.draft_residency} onChange={(e) => set("draft_residency", e.target.value as LuceboxInstance["draft_residency"])} className={input}>
              <option value="auto">auto（推奨）</option>
              <option value="persistent">persistent（常駐・VRAMを継続確保）</option>
              <option value="request-scoped">request-scoped（要求ごとに読み込む）</option>
            </select>
          </L>
          <L label="既定の出力上限（0はモデル既定）">
            <PresetOrCustom value={cfg.default_max_tokens}
              presets={[0, 2048, 8192, 16000].map((v) => ({ v, label: v === 0 ? "モデル既定" : v.toLocaleString() }))}
              placeholder="0" onChange={(v) => set("default_max_tokens", Number(v ?? 0))} />
          </L>
          <Toggle label="高速ロールバック" hint="投機失敗時の巻き戻しを速くします。既定は有効" value={cfg.fast_rollback} onChange={(v) => set("fast_rollback", v)} />
          <Toggle
            label="エージェント用ターンキャッシュ"
            hint="生成したツール呼び出しの先までprefixキャッシュを延ばします。OpenCodeのようにターンを重ねる用途で、毎ターンの再読み込みが減ります（実測: 3ターン目で12.2秒→7.7秒）"
            value={cfg.agent_turn_cache}
            onChange={(v) => set("agent_turn_cache", v)}
          />
          <Toggle label="共通アイドル停止から除外" hint="直接endpointを使う外部clientは利用時刻を追跡できないため、常用時は除外を推奨" value={cfg.idle_exclude} onChange={(v) => set("idle_exclude", v)} />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => persist(false)} disabled={busy}
          className="min-h-11 flex-1 rounded-xl bg-zinc-100 px-4 text-xs font-medium disabled:opacity-40 dark:bg-zinc-800">保存</button>
        <button onClick={() => persist(true)} disabled={busy}
          className="min-h-11 flex-1 rounded-xl bg-accent-600 px-4 text-xs font-medium text-white disabled:opacity-40">保存して起動</button>
        {!isNew && cfg.loaded && (
          <button onClick={stop} className="min-h-11 rounded-xl border border-zinc-300 px-4 text-xs font-medium dark:border-zinc-700">停止</button>
        )}
      </div>
      {!isNew && (
        <p className="text-[10px] text-zinc-400">
          起動後はエンドポイント <code className="font-mono">http://127.0.0.1:{cfg.port}/v1</code>、
          またはゲートウェイからモデル名 <code className="font-mono">{cfg.alias}</code> で使えます。
        </p>
      )}
      {picker && (
        <FilePicker mode="file"
          title={picker === "model" ? "ターゲットGGUFを選択" : "DFlashドラフトGGUFを選択"}
          initialPath={(picker === "model" ? cfg.model_path : cfg.draft_path) || undefined}
          onSelect={(path) => { set(picker === "model" ? "model_path" : "draft_path", path); setPicker(null); }}
          onClose={() => setPicker(null)} />
      )}
    </div>
  );
}
