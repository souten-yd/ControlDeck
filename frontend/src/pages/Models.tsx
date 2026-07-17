/** LLMモデル管理。runtime選択、取得/登録、ロード、モデル個別設定を一つの画面に統合する。
 * 取得・ローカル登録はサーバー側ジョブで実行され、ブラウザを閉じても継続する。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, wsUrl } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { FilePicker } from "../components/FilePicker";
import { IconFolder, IconPlus, IconSearch, IconTrash } from "../components/icons";

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
}
interface OllamaStatus { available: boolean; version: string; base_url: string }
interface Settings {
  base_url: string;
  idle_unload_enabled: boolean;
  idle_unload_minutes: number;
  default_keep_alive: string;
  default_model: string;
  kv_cache_type: string;
  flash_attention: boolean;
}
interface OllamaEnv { flash_attention: boolean | null; kv_cache_type: string | null; source: string }
interface RuntimePolicy {
  selected_runtime: "ollama" | "llama.cpp";
  selected_backend: "rocm" | "vulkan" | "";
  coexistence: "exclusive" | "coexist";
  idle_unload_enabled: boolean;
  idle_unload_minutes: number;
  max_loaded_models: number;
  default_model_ref: string;
  assistant_name: string;
  chat: { max_output_tokens: number; reasoning: "off" | "auto" | "on"; timeout_seconds: number };
  deep_research: {
    context_auto_switch_enabled: boolean;
    context_tokens: number;
    evidence_context_chars: number;
    auto_resize_managed_runtime: boolean;
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
  runtimes: Array<{ id: string; runtime: "ollama" | "llama.cpp"; backend: string; label: string; available: boolean; installed: boolean; selected: boolean; running?: boolean }>;
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

interface JobInfo {
  id: string;
  kind: string;
  title: string;
  status: string; // running / succeeded / failed / canceled
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
  const pct =
    job.progress?.total && job.progress?.completed
      ? Math.round((job.progress.completed / job.progress.total) * 100)
      : null;
  const label =
    job.status === "succeeded" ? "完了" : job.status === "failed" ? `エラー: ${job.error}` : job.status === "canceled" ? "キャンセル" : job.status === "queued" ? "開始待ち" : job.progress?.status || "処理中...";
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <p className="truncate text-xs text-zinc-500">{label}</p>
      {pct !== null && job.status === "running" && (
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div className="h-full rounded-full bg-accent-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
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
  const [pulling, setPulling] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const { data: status } = useQuery({ queryKey: ["ollama-status"], queryFn: () => api<OllamaStatus>("/models/status"), refetchInterval: 15000 });
  const { data: runtimeEnv } = useQuery({ queryKey: ["runtime-environment"], queryFn: () => api<RuntimeEnvironment>("/models/runtime-environment") });
  const selectedProvider = runtimeEnv?.policy.selected_runtime === "llama.cpp" ? "llama.cpp" : "ollama";
  const { data: models, isLoading } = useQuery({
    queryKey: ["models", selectedProvider],
    queryFn: async () => {
      if (selectedProvider === "ollama") return api<Model[]>("/models");
      const common = await api<Array<{ id: string; name: string; size_bytes: number; loaded: boolean | null; details: Record<string, string> }>>(`/models/providers/${selectedProvider}/models`);
      return common.map((m) => ({ id: m.id, name: m.name, size: m.size_bytes, parameter_size: "", quantization: "", family: "", loaded: !!m.loaded, expires_at: null, vram: null }));
    },
    refetchInterval: 15000,
    enabled: !!runtimeEnv && (selectedProvider !== "ollama" || status?.available !== false),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["models", selectedProvider] });

  const act = async (id: string, action: "load" | "unload") => {
    try {
      await api(`/models/providers/${selectedProvider}/models/${encodeURIComponent(id)}/${action}`, { method: "POST", json: {} });
      show(action === "load" ? "ロードしました" : "アンロードしました");
      refresh();
    } catch (e) {
      show(e instanceof Error ? e.message : "失敗しました", "error");
    }
  };
  const del = useMutation({
    mutationFn: (name: string) => api(`/models/providers/ollama/models/${encodeURIComponent(name)}`, { method: "DELETE" }),
    onSuccess: () => { show("削除しました"); setDeleting(null); refresh(); },
    onError: (e) => show(e instanceof Error ? e.message : "削除失敗", "error"),
  });

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Model</h1>
        <div className="flex items-center gap-2">
          {can("workflows.edit") && (
            <button onClick={() => setSettingsOpen(true)} aria-label="LLM ランタイム設定" title="LLM ランタイム設定（Ollama / llama.cpp）" className="rounded-xl border border-zinc-300 px-3 py-2 text-sm text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300">⚙</button>
          )}
          {can("workflows.edit") && (
            <button onClick={() => selectedProvider === "llama.cpp" ? setSettingsOpen(true) : setPulling(true)} className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700">
              <IconPlus /> {selectedProvider === "llama.cpp" ? "GGUF登録" : "モデル取得"}
            </button>
          )}
        </div>
      </div>
      <p className="mb-4 text-xs text-zinc-400">
        選択中: {selectedProvider === "llama.cpp" ? `llama.cpp / ${runtimeEnv?.policy.selected_backend.toUpperCase()}` : "Ollama"}。モデルの登録・ロード・アンロード・個別設定を管理します。
        {selectedProvider === "ollama" && status && (status.available ? ` · Ollama ${status.version}` : " · Ollama に接続できません")}
      </p>

      <ActiveModelJobs />
      {selectedProvider === "ollama" && status && !status.available ? (
        <div className="rounded-2xl border border-dashed border-amber-300 bg-amber-50 p-6 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
          Ollama（{status.base_url}）に接続できません。<code className="font-mono">ollama serve</code> の起動、または設定でエンドポイントを確認してください。
        </div>
      ) : isLoading ? (
        <Skeleton className="h-24" />
      ) : !models || models.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">モデルがありません。「モデル取得」から追加してください。</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {models.map((m) => (
            <li key={m.id ?? m.name} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex items-center gap-3">
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${m.loaded ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`} title={m.loaded ? "ロード中" : "未ロード"} />
                <button onClick={() => selectedProvider === "llama.cpp" ? setSettingsOpen(true) : setDetail(m.name)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-sm font-semibold">{m.name}</p>
                  <p className="num truncate text-xs text-zinc-400">
                    {selectedProvider === "llama.cpp" ? "llama.cpp" : "Ollama"} · {gb(m.size)}{m.parameter_size && ` · ${m.parameter_size}`}{m.quantization && ` · ${m.quantization}`}
                    {m.loaded && m.vram ? ` · VRAM ${gb(m.vram)}` : ""}
                  </p>
                </button>
                {can("workflows.edit") && (
                  <>
                    <button onClick={() => act(m.id ?? m.name, m.loaded ? "unload" : "load")} className="shrink-0 rounded-xl bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300">
                      {m.loaded ? "アンロード" : "ロード"}
                    </button>
                    {selectedProvider === "ollama" && <button onClick={() => setDeleting(m.name)} aria-label="削除" className="shrink-0 rounded-lg p-2 text-zinc-400 hover:text-red-600"><IconTrash /></button>}
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {pulling && <PullSheet onClose={() => setPulling(false)} onDone={refresh} />}
      {settingsOpen && <SettingsSheet models={models ?? []} onClose={() => setSettingsOpen(false)} />}
      {detail && <DetailSheet model={detail} onClose={() => setDetail(null)} />}
      {deleting && (
        <ConfirmDialog title={`「${deleting}」を削除しますか？`} message="モデルファイルが削除されます。取り消せません。" confirmLabel="削除する" busy={del.isPending} onConfirm={() => del.mutate(deleting)} onClose={() => setDeleting(null)} />
      )}
    </div>
  );
}

function PullSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [tab, setTab] = useState<"registry" | "hf" | "local">("registry");
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
      const r = await api<{ job_id: string }>("/models/pull-jobs", { method: "POST", json: { model: target } });
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
        <HFSearch onPull={start} running={running} />
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
  think?: string;
  num_ctx?: number;
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
  { v: 65536, label: "65,536" }, { v: 131072, label: "131,072" },
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
    queryFn: () => api<ModelConfig>(`/models/${encodeURIComponent(model)}/config`),
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
      api(`/models/${encodeURIComponent(model)}/config?reload=${reload}`, { method: "PUT", json: eff }),
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
  const selCls = "w-full rounded-xl border border-zinc-300 bg-white px-2.5 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-700">
      <p className="px-3 py-2.5 text-xs font-semibold text-zinc-500">このモデルの個別設定</p>
      <div className="space-y-2.5 px-3 pb-3">
        {/* よく使う */}
        <L label="常駐時間 keep_alive"><PresetOrCustom value={eff.keep_alive} presets={KEEPALIVE_PRESETS} numeric={false} placeholder="30m / 1h" onChange={(v) => set("keep_alive", v)} /></L>
        <L label="コンテキスト長 num_ctx（大きいほどVRAM増）"><PresetOrCustom value={eff.num_ctx} presets={CTX_PRESETS} placeholder="8192" onChange={(v) => set("num_ctx", v)} /></L>
        <L label="出力長 num_predict（最大生成トークン）"><PresetOrCustom value={eff.num_predict} presets={PREDICT_PRESETS} placeholder="512" onChange={(v) => set("num_predict", v)} /></L>
        {hasThinking && (
          <L label="思考（推論）think — オフで高速化・レベルで深さ調整">
            <select value={eff.think ?? ""} onChange={(e) => set("think", e.target.value)} className={selCls}>
              <option value="">既定（自動）</option>
              <option value="off">オフ（思考なし・最速）</option>
              <option value="on">オン</option>
              <option value="low">低（浅い思考）</option>
              <option value="medium">中</option>
              <option value="high">高（深い思考）</option>
              <option value="max">最大</option>
            </select>
          </L>
        )}
        <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2.5 dark:border-zinc-700">
          <span className="text-xs">アイドル自動アンロードから除外<span className="block text-[10px] text-zinc-400">常駐させ再ロード待ちをなくす</span></span>
          <input type="checkbox" checked={!!eff.idle_exclude} onChange={(e) => set("idle_exclude", e.target.checked)} className="h-4 w-4" />
        </label>

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

function SettingsSheet({ models, onClose }: { models: Model[]; onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["ollama-settings"], queryFn: () => api<Settings>("/models/settings") });
  const { data: runtimeEnv } = useQuery({ queryKey: ["runtime-environment"], queryFn: () => api<RuntimeEnvironment>("/models/runtime-environment") });
  const [cfg, setCfg] = useState<Settings | null>(null);
  const [policyCfg, setPolicyCfg] = useState<RuntimePolicy | null>(null);
  const eff = cfg ?? data ?? null;
  const policy = policyCfg ?? runtimeEnv?.policy ?? null;
  const save = useMutation({
    mutationFn: () => api("/models/settings", { method: "PUT", json: eff }),
    onSuccess: () => { show("設定を保存しました"); qc.invalidateQueries({ queryKey: ["ollama-settings"] }); onClose(); },
    onError: (e) => show(e instanceof Error ? e.message : "保存失敗", "error"),
  });
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
  if (!eff || !policy || !runtimeEnv) return null;
  const chooseRuntime = (item: RuntimeEnvironment["runtimes"][number]) => {
    if (!item.installed) return;
    savePolicy.mutate({
      selected_runtime: item.runtime,
      selected_backend: item.runtime === "llama.cpp" ? item.backend as "rocm" | "vulkan" : "",
    });
  };
  return (
    <BottomSheet title="LLM ランタイム設定" onClose={onClose} wide>
      <div className="mb-4 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
        <p className="mb-2 text-xs font-semibold text-zinc-500">このPCで利用するランタイム</p>
        <p className="mb-2 text-[10px] text-zinc-400">{runtimeEnv.platform} · {runtimeEnv.gpu} GPU。利用可能な構成だけを表示しています。</p>
        <div className="grid gap-2 sm:grid-cols-3">
          {runtimeEnv.runtimes.filter((r) => r.available).map((item) => {
            const selected = policy.selected_runtime === item.runtime && (item.runtime === "ollama" || policy.selected_backend === item.backend);
            return (
              <button key={item.id} type="button" onClick={() => chooseRuntime(item)} disabled={!item.installed || savePolicy.isPending}
                className={`rounded-xl border p-3 text-left disabled:opacity-50 ${selected ? "border-accent-500 bg-accent-50/60 ring-1 ring-accent-500 dark:bg-accent-600/10" : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-700"}`}>
                <span className="block text-sm font-semibold">{item.label}</span>
                <span className={`mt-1 block text-[10px] ${selected ? "text-accent-600 dark:text-accent-400" : "text-zinc-400"}`}>
                  {selected ? "● 使用中" : !item.installed ? "導入が必要" : item.running ? "稼働中 · 選択する" : "利用可能 · 選択する"}
                </span>
              </button>
            );
          })}
        </div>
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
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <L label="チャット・ワークフロー生成の出力token上限"><PresetOrCustom value={policy.chat.max_output_tokens} presets={PREDICT_PRESETS.filter((p) => Number(p.v) > 0)} placeholder="4096" onChange={(v) => setPolicyCfg({ ...policy, chat: { ...policy.chat, max_output_tokens: Number(v ?? 4096) } })} /></L>
          <L label="チャット思考">
            <select value={policy.chat.reasoning} onChange={(e) => setPolicyCfg({ ...policy, chat: { ...policy.chat, reasoning: e.target.value as RuntimePolicy["chat"]["reasoning"] } })} className={input}>
              <option value="off">オフ（高速・既定）</option><option value="auto">モデルに任せる</option><option value="on">オン</option>
            </select>
          </L>
        </div>
        <div className="space-y-2 rounded-xl border border-violet-200 bg-violet-50/40 p-3 dark:border-violet-900 dark:bg-violet-950/20">
          <label className="flex items-center justify-between gap-3">
            <span>
              <span className="block text-xs font-semibold text-violet-700 dark:text-violet-300">Deep Research専用CTX</span>
              <span className="mt-0.5 block text-[10px] text-zinc-500">実行時だけ大規模contextを要求します</span>
            </span>
            <input
              type="checkbox"
              checked={policy.deep_research.context_auto_switch_enabled}
              onChange={(e) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, context_auto_switch_enabled: e.target.checked } })}
              className="h-4 w-4"
            />
          </label>
          {policy.deep_research.context_auto_switch_enabled && (
            <>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <L label="要求CTX token">
                  <PresetOrCustom
                    value={policy.deep_research.context_tokens}
                    presets={CTX_PRESETS}
                    placeholder="262144"
                    onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, context_tokens: Number(v ?? 262144) } })}
                  />
                </L>
                <L label="根拠context上限（文字）">
                  <PresetOrCustom
                    value={policy.deep_research.evidence_context_chars}
                    presets={[30000, 60000, 90000, 150000, 300000].map((v) => ({ v, label: v.toLocaleString() }))}
                    placeholder="90000"
                    onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, evidence_context_chars: Number(v ?? 90000) } })}
                  />
                </L>
              </div>
              <label className="flex items-center justify-between rounded-lg border border-violet-200 px-2.5 py-2 dark:border-violet-900">
                <span className="text-[11px]">管理中llama.cppを必要時に再ロード</span>
                <input
                  type="checkbox"
                  checked={policy.deep_research.auto_resize_managed_runtime}
                  onChange={(e) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, auto_resize_managed_runtime: e.target.checked } })}
                  className="h-4 w-4"
                />
              </label>
              <L label="Deep Research生成timeout（秒）">
                <PresetOrCustom
                  value={policy.deep_research.timeout_seconds}
                  presets={[300, 600, 1200, 1800, 3600].map((v) => ({ v, label: `${v}秒` }))}
                  placeholder="1800"
                  onChange={(v) => setPolicyCfg({ ...policy, deep_research: { ...policy.deep_research, timeout_seconds: Number(v ?? 1800) } })}
                />
              </L>
              <p className="text-[10px] text-zinc-500">Ollamaはrequestのnum_ctxへ適用。管理中llama.cppはCTX不足時に再ロードし、失敗時は元設定へ復元します。</p>
            </>
          )}
        </div>
        <L label="アシスタント表示名"><input value={policy.assistant_name} onChange={(e) => setPolicyCfg({ ...policy, assistant_name: e.target.value })} className={input} /></L>
        <button onClick={() => savePolicy.mutate(policy)} disabled={savePolicy.isPending} className="w-full rounded-xl bg-accent-600 py-2 text-xs font-medium text-white disabled:opacity-40">共通設定を適用</button>
      </div>

      {policy.selected_runtime === "llama.cpp" ? (
        <LlamaRuntimePanel />
      ) : (
      <div className="space-y-3">
        <L label="Ollama エンドポイント">
          <input value={eff.base_url} onChange={(e) => setCfg({ ...eff, base_url: e.target.value })} className={`${input} font-mono text-xs`} placeholder="http://127.0.0.1:11434" />
        </L>
        <L label="既定モデル（LLM ノードの候補に使用）">
          <select value={eff.default_model} onChange={(e) => setCfg({ ...eff, default_model: e.target.value })} className={input}>
            <option value="">未設定</option>
            {models.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
          </select>
        </L>
        <L label="ロード時の保持時間 (keep_alive)">
          <input value={eff.default_keep_alive} onChange={(e) => setCfg({ ...eff, default_keep_alive: e.target.value })} className={`${input} font-mono text-xs`} placeholder="5m / 30m / -1(無期限)" />
        </L>
        <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-700">
          <span className="text-sm">アイドル時に自動アンロード</span>
          <input type="checkbox" checked={eff.idle_unload_enabled} onChange={(e) => setCfg({ ...eff, idle_unload_enabled: e.target.checked })} className="h-5 w-5 accent-current" />
        </label>
        {eff.idle_unload_enabled && (
          <L label="アイドル判定（分）— この時間 API 呼び出しが無ければアンロード">
            <input type="number" value={eff.idle_unload_minutes} onChange={(e) => setCfg({ ...eff, idle_unload_minutes: Number(e.target.value) })} className={input} min={1} max={1440} />
          </L>
        )}
        <p className="text-xs text-zinc-400">
          モデルは API から呼び出されると自動ロードされます（Ollama 標準）。上の設定で未使用時の解放を制御できます。
        </p>

        <KvCacheSettings eff={eff} setCfg={setCfg} input={input} />

        <button onClick={() => save.mutate()} disabled={save.isPending} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          {save.isPending ? "保存中..." : "保存"}
        </button>
      </div>
      )}
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

/** KV キャッシュ量子化 / Flash Attention（Ollama サーバー全体・環境変数）。 */
function KvCacheSettings({ eff, setCfg, input }: { eff: Settings; setCfg: (s: Settings) => void; input: string }) {
  const { data: env } = useQuery({ queryKey: ["ollama-env"], queryFn: () => api<OllamaEnv>("/models/ollama-env") });
  // 保存値と実際の稼働環境がずれていれば適用コマンドを案内する
  const applied =
    env && (env.kv_cache_type ?? "f16") === eff.kv_cache_type &&
    (env.flash_attention ?? false) === eff.flash_attention;
  const needsFlash = eff.kv_cache_type !== "f16" && !eff.flash_attention;
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <p className="mb-2 text-xs font-semibold text-zinc-500">KV キャッシュ量子化（サーバー全体・VRAM 削減）</p>
      <div className="space-y-2.5">
        <L label="キャッシュ精度 OLLAMA_KV_CACHE_TYPE">
          <select value={eff.kv_cache_type} onChange={(e) => setCfg({ ...eff, kv_cache_type: e.target.value })} className={input}>
            <option value="f16">f16（既定・最高精度）</option>
            <option value="q8_0">q8_0（VRAM 約1/2・品質ほぼ同等・推奨）</option>
            <option value="q4_0">q4_0（VRAM 約1/4・品質やや低下）</option>
          </select>
        </L>
        <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2.5 dark:border-zinc-700">
          <span className="text-xs">Flash Attention<span className="block text-[10px] text-zinc-400">量子化(q8_0/q4_0)を効かせるには必須</span></span>
          <input type="checkbox" checked={eff.flash_attention} onChange={(e) => setCfg({ ...eff, flash_attention: e.target.checked })} className="h-4 w-4" />
        </label>
        {needsFlash && (
          <p className="rounded-lg bg-amber-50 px-2.5 py-2 text-[11px] text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
            量子化には Flash Attention が必要です。上のスイッチを ON にしてください。
          </p>
        )}
        <div className="rounded-lg bg-zinc-50 px-2.5 py-2 text-[10px] leading-relaxed text-zinc-500 dark:bg-zinc-800/60">
          <p className="mb-1">
            現在の稼働状態: {env?.flash_attention == null && env?.kv_cache_type == null
              ? "既定（f16 / Flash Attention 無効）"
              : `${env?.kv_cache_type ?? "f16"} / Flash Attention ${env?.flash_attention ? "有効" : "無効"}`}
            {applied ? " ✓ 一致" : ""}
          </p>
          {!applied && (
            <>
              <p className="mb-1">これは Ollama サーバー（root 管理）の環境変数です。保存後、下記を実行して適用してください:</p>
              <pre className="overflow-x-auto whitespace-pre rounded bg-zinc-100 p-1.5 font-mono text-[10px] dark:bg-zinc-950">{`sudo systemctl edit ollama
# [Service] に追記:
Environment="OLLAMA_FLASH_ATTENTION=${eff.flash_attention ? "1" : "0"}"
Environment="OLLAMA_KV_CACHE_TYPE=${eff.kv_cache_type}"
sudo systemctl restart ollama`}</pre>
            </>
          )}
        </div>
      </div>
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
  spec_type: "none" | "draft-simple" | "draft-mtp" | "ngram-simple";
  draft_max: number;
  cpu_moe: boolean;
  n_cpu_moe: number;
  temperature: number;
  top_k: number;
  top_p: number;
  min_p: number;
  repeat_penalty: number;
  seed: number;
}

const LLAMA_INSTANCE_WRITE_KEYS = [
  "model_path", "port", "alias", "auto_start", "idle_exclude",
  "n_gpu_layers", "ctx_size", "n_parallel", "flash_attn", "n_predict",
  "batch_size", "ubatch_size", "cache_type_k", "cache_type_v", "threads",
  "threads_batch", "mmap", "mlock", "spec_type", "draft_max", "cpu_moe",
  "n_cpu_moe", "temperature", "top_k", "top_p", "min_p", "repeat_penalty", "seed",
] as const satisfies readonly (keyof LlamaInstanceConfig)[];

function llamaInstanceWriteBody(config: LlamaInstanceConfig): Record<string, unknown> {
  return Object.fromEntries(LLAMA_INSTANCE_WRITE_KEYS.map((key) => [key, config[key]]));
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

const BACKEND_LABEL: Record<string, string> = { rocm: "ROCm (AMD)", vulkan: "Vulkan (汎用GPU)", cuda: "CUDA (NVIDIA)" };

/** llama.cpp ランタイムの管理 UI（LLM ランタイム設定タブ内で使う中身）。 */
function LlamaRuntimePanel() {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
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
      {st.installed && (
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
  const [pickerOpen, setPickerOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [cfg, setCfg] = useState<LlamaInstanceConfig>({ ...initial });
  const originalAlias = initial.alias;
  const { data: optionData } = useQuery({ queryKey: ["llama-options"], queryFn: () => api<{ flags: string[] }>("/models/llama/options") });
  const flags = new Set(optionData?.flags ?? []);
  const set = <K extends keyof typeof cfg>(key: K, value: (typeof cfg)[K]) => setCfg((current) => ({ ...current, [key]: value }));
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  const persist = async (start: boolean) => {
    if (!cfg.model_path) { show("モデルファイルを選択してください", "error"); return; }
    try {
      await api(isNew ? "/models/llama/instances" : `/models/llama/instances/${encodeURIComponent(originalAlias)}`, {
        method: isNew ? "POST" : "PUT",
        json: llamaInstanceWriteBody(cfg),
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
      <div className="flex gap-1.5">
        <input value={cfg.model_path} onChange={(e) => set("model_path", e.target.value)} placeholder="GGUF ファイルのパス" className={`${input} min-w-0 flex-1 font-mono text-xs`} />
        <button onClick={() => setPickerOpen(true)} className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">📁</button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <L label="モデル名（alias）"><input value={cfg.alias} onChange={(e) => set("alias", e.target.value)} className={`${input} font-mono`} /></L>
        <L label="待受port"><input type="number" min={1024} max={65535} value={cfg.port} onChange={(e) => set("port", Number(e.target.value))} className={`${input} font-mono`} /></L>
        <L label="コンテキスト長（CTX）"><PresetOrCustom value={cfg.ctx_size} presets={CTX_PRESETS} placeholder="8192" onChange={(v) => set("ctx_size", Number(v ?? 4096))} /></L>
        <L label="最大出力トークン"><PresetOrCustom value={cfg.n_predict} presets={PREDICT_PRESETS} placeholder="2048" onChange={(v) => set("n_predict", Number(v ?? 2048))} /></L>
        <L label="GPUオフロード層"><PresetOrCustom value={cfg.n_gpu_layers} presets={GPU_PRESETS.map((p) => p.v === -1 ? { ...p, v: 999, label: "全部 (999)" } : p)} placeholder="999" onChange={(v) => set("n_gpu_layers", Number(v ?? 999))} /></L>
      </div>
      {flags.has("--flash-attn") && <Toggle label="Flash Attention" hint="KVキャッシュ削減と速度改善。量子化KVでは有効化を推奨" value={cfg.flash_attn} onChange={(value) => set("flash_attn", value)} />}

      {(flags.has("--cache-type-k") || flags.has("--cache-type-v")) && <div className="grid grid-cols-2 gap-2">
        <L label="Kキャッシュ量子化"><CacheTypeSelect value={cfg.cache_type_k} onChange={(value) => set("cache_type_k", value)} input={input} /></L>
        <L label="Vキャッシュ量子化"><CacheTypeSelect value={cfg.cache_type_v} onChange={(value) => set("cache_type_v", value)} input={input} /></L>
      </div>}

      {flags.has("--spec-type") && <div className="rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
        <L label="推測デコード / MTP">
          <select value={cfg.spec_type} onChange={(e) => set("spec_type", e.target.value as typeof cfg.spec_type)} className={input}>
            <option value="none">無効（互換性優先）</option>
            <option value="draft-mtp">MTP（対応GGUFのみ）</option>
            <option value="draft-simple">Draft simple</option>
            <option value="ngram-simple">N-gram（追加モデル不要）</option>
          </select>
        </L>
        {cfg.spec_type !== "none" && <L label="先読みトークン上限"><PresetOrCustom value={cfg.draft_max} presets={[4, 8, 16, 32, 64].map((v) => ({ v, label: String(v) }))} placeholder="16" onChange={(v) => set("draft_max", Number(v ?? 16))} /></L>}
        {cfg.spec_type === "draft-mtp" && <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">MTP層を含まないモデルでは起動に失敗するため、その場合は無効へ戻してください。</p>}
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
        <Toggle label="mmapでモデルを読む" hint="通常はON。OSのpage cacheを利用します" value={cfg.mmap} onChange={(value) => set("mmap", value)} />
        <Toggle label="モデルをRAMへ固定（mlock）" hint="swapを防ぎますが、十分なRAMが必要です" value={cfg.mlock} onChange={(value) => set("mlock", value)} />
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
        <FilePicker mode="file" title="GGUF モデルを選択" initialPath={cfg.model_path || undefined}
          onSelect={(p) => { set("model_path", p); setPickerOpen(false); }} onClose={() => setPickerOpen(false)} />
      )}
    </div>
  );
}

function Toggle({ label, hint, value, onChange }: { label: string; hint?: string; value: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center justify-between gap-3 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
    <span className="text-xs">{label}{hint && <span className="block text-[10px] font-normal text-zinc-400">{hint}</span>}</span>
    <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 shrink-0" />
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
