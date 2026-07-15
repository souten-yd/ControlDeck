/** Model（Ollama）管理。取得(HF含む)/削除/ロード/アンロード/詳細/設定/自動アンロード。
 * 取得・ローカル登録はサーバー側ジョブで実行され、ブラウザを閉じても継続する。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { FilePicker } from "../components/FilePicker";
import { IconFolder, IconPlus, IconSearch, IconTrash } from "../components/icons";

interface Model {
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
interface LLMProvider {
  id: string; provider: string; name: string; base_url: string; managed: boolean;
  installed: boolean | null; experimental: boolean; available: boolean; models: string[];
}
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

/** サーバー側ジョブのポーリング（1 秒間隔・終了で停止） */
function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<JobInfo>(`/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && q.state.data.status !== "running" ? false : 1000),
  });
}

function JobProgress({ job }: { job: JobInfo }) {
  const pct =
    job.progress?.total && job.progress?.completed
      ? Math.round((job.progress.completed / job.progress.total) * 100)
      : null;
  const label =
    job.status === "succeeded" ? "完了" : job.status === "failed" ? `エラー: ${job.error}` : job.status === "canceled" ? "キャンセル" : job.progress?.status || "処理中...";
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
    refetchInterval: 2000,
  });
  const running = (data ?? []).filter((j) => j.status === "running");
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
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [pulling, setPulling] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const { data: status } = useQuery({ queryKey: ["ollama-status"], queryFn: () => api<OllamaStatus>("/models/status"), refetchInterval: 15000 });
  const { data: models, isLoading } = useQuery({
    queryKey: ["models"],
    queryFn: () => api<Model[]>("/models"),
    refetchInterval: 5000,
    enabled: status?.available !== false,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["models"] });

  const act = async (name: string, action: "load" | "unload") => {
    try {
      await api(`/models/${encodeURIComponent(name)}/${action}`, { method: "POST", json: {} });
      show(action === "load" ? "ロードしました" : "アンロードしました");
      refresh();
    } catch (e) {
      show(e instanceof Error ? e.message : "失敗しました", "error");
    }
  };
  const del = useMutation({
    mutationFn: (name: string) => api(`/models/${encodeURIComponent(name)}`, { method: "DELETE" }),
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
            <button onClick={() => setPulling(true)} className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700">
              <IconPlus /> モデル取得
            </button>
          )}
        </div>
      </div>
      <p className="mb-4 text-xs text-zinc-400">
        Ollama のモデル管理。取得（Ollama / HuggingFace GGUF）・ロード・アンロード・削除・詳細設定。
        {status && (status.available ? ` · Ollama ${status.version}` : " · Ollama に接続できません")}
      </p>

      <ActiveModelJobs />
      {status && !status.available ? (
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
            <li key={m.name} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex items-center gap-3">
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${m.loaded ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`} title={m.loaded ? "ロード中" : "未ロード"} />
                <button onClick={() => setDetail(m.name)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-sm font-semibold">{m.name}</p>
                  <p className="num truncate text-xs text-zinc-400">
                    {gb(m.size)}{m.parameter_size && ` · ${m.parameter_size}`}{m.quantization && ` · ${m.quantization}`}
                    {m.loaded && m.vram ? ` · VRAM ${gb(m.vram)}` : ""}
                  </p>
                </button>
                {can("workflows.edit") && (
                  <>
                    <button onClick={() => act(m.name, m.loaded ? "unload" : "load")} className="shrink-0 rounded-xl bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300">
                      {m.loaded ? "アンロード" : "ロード"}
                    </button>
                    <button onClick={() => setDeleting(m.name)} aria-label="削除" className="shrink-0 rounded-lg p-2 text-zinc-400 hover:text-red-600"><IconTrash /></button>
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

const CTX_PRESETS = [2048, 4096, 8192, 16384, 32768, 65536, 131072].map((v) => ({ v, label: v.toLocaleString() }));
const PREDICT_PRESETS = [
  { v: -1, label: "無制限 (-1)" }, { v: -2, label: "文脈まで (-2)" },
  { v: 256, label: "256" }, { v: 512, label: "512" }, { v: 1024, label: "1024" },
  { v: 2048, label: "2048" }, { v: 4096, label: "4096" },
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
  const [tab, setTab] = useState<"ollama" | "llama">("ollama");
  const { data } = useQuery({ queryKey: ["ollama-settings"], queryFn: () => api<Settings>("/models/settings") });
  const { data: providers = [] } = useQuery({ queryKey: ["llm-providers"], queryFn: () => api<LLMProvider[]>("/models/providers") });
  const [cfg, setCfg] = useState<Settings | null>(null);
  const eff = cfg ?? data ?? null;
  const save = useMutation({
    mutationFn: () => api("/models/settings", { method: "PUT", json: eff }),
    onSuccess: () => { show("設定を保存しました"); qc.invalidateQueries({ queryKey: ["ollama-settings"] }); onClose(); },
    onError: (e) => show(e instanceof Error ? e.message : "保存失敗", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  if (!eff) return null;
  return (
    <BottomSheet title="LLM ランタイム設定" onClose={onClose} wide>
      {/* ランタイム切替タブ（Ollama / llama.cpp を1か所に統合） */}
      <div className="mb-3 flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
        {([["ollama", "Ollama"], ["llama", "llama.cpp"]] as const).map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${tab === key ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>
            {label}
          </button>
        ))}
      </div>
      {providers.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5" aria-label="検出済みLLMプロバイダー">
          {providers.map((provider) => (
            <span key={provider.id + provider.base_url} title={provider.base_url}
              className="rounded-lg bg-zinc-100 px-2 py-1 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
              <span className={provider.available ? "text-emerald-500" : "text-zinc-400"}>●</span>{" "}
              {provider.name}{provider.models.length > 0 ? ` · ${provider.models.length}モデル` : ""}
            </span>
          ))}
        </div>
      )}

      {tab === "llama" ? (
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

interface LlamaStatus {
  installed: boolean;
  backend: string;
  tag: string;
  base_url: string | null;
  port: number | null;
  model_path: string;
  experimental: boolean;
  detected_backends: Record<string, boolean>;
  installed_backends: string[];
  selectable_backends: string[];
  health?: { ok: boolean };
}

const BACKEND_LABEL: Record<string, string> = { rocm: "ROCm (AMD)", vulkan: "Vulkan (汎用GPU)", cuda: "CUDA (NVIDIA)" };

/** llama.cpp ランタイムの管理 UI（LLM ランタイム設定タブ内で使う中身）。 */
function LlamaRuntimePanel() {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useJob(jobId);
  const { data: st } = useQuery({
    queryKey: ["llama-status"],
    queryFn: () => api<LlamaStatus>("/models/llama/status"),
    refetchInterval: (q) => (q.state.data?.installed ? 8000 : false),
  });

  useEffect(() => {
    if (job && job.status !== "running") {
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
  const switchTo = async (backend: string) => {
    try { await api("/models/llama/switch", { method: "POST", json: { backend } }); show(`${BACKEND_LABEL[backend]} に切り替えました`); qc.invalidateQueries({ queryKey: ["llama-status"] }); }
    catch (e) { show(e instanceof Error ? e.message : "切り替え失敗", "error"); }
  };

  if (!st) return <p className="text-xs text-zinc-400">読み込み中...</p>;
  const selectable = st.selectable_backends;

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
      <p className="text-xs text-zinc-500">
        この PC で使えるバックエンドを検出して表示しています。CUDA(NVIDIA) は当面 Ollama をご利用ください。
      </p>
      <div className="flex flex-wrap gap-2">
        {selectable.length === 0 && <span className="text-xs text-zinc-400">対応 GPU バックエンドが検出されませんでした</span>}
        {selectable.map((b) => {
          const installed = st.installed_backends.includes(b);
          const current = st.backend === b;
          return (
            <div key={b} className={`flex items-center gap-2 rounded-xl border px-3 py-2 ${current ? "border-accent-500 bg-accent-50/50 dark:bg-accent-600/10" : "border-zinc-200 dark:border-zinc-700"}`}>
              <div>
                <p className="text-xs font-medium">{BACKEND_LABEL[b] ?? b}</p>
                <p className="text-[10px] text-zinc-400">
                  {current ? "使用中" : installed ? "導入済み" : "未導入"}
                  {st.detected_backends[b] ? " · 対応" : ""}
                </p>
              </div>
              {current ? (
                <span className="text-emerald-500">✓</span>
              ) : installed ? (
                <button onClick={() => switchTo(b)} className="rounded-lg bg-zinc-100 px-2.5 py-1 text-[11px] font-medium hover:bg-zinc-200 dark:bg-zinc-800">切替</button>
              ) : (
                <button onClick={() => install(b)} disabled={job?.status === "running"} className="rounded-lg bg-accent-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-accent-700 disabled:opacity-40">導入</button>
              )}
            </div>
          );
        })}
      </div>
      {job && job.status === "running" && <JobProgress job={job} />}
      {st.installed && <LlamaInstanceControls st={st} onChanged={() => qc.invalidateQueries({ queryKey: ["llama-status"] })} />}
    </div>
  );
}

/** llama.cpp のモデル起動設定と起動/停止。 */
function LlamaInstanceControls({ st, onChanged }: { st: LlamaStatus; onChanged: () => void }) {
  const show = useToasts((s) => s.show);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [modelPath, setModelPath] = useState(st.model_path);
  const [ngl, setNgl] = useState<string>("999");
  const [ctx, setCtx] = useState<string>("4096");
  const [flash, setFlash] = useState(false);
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  const saveAndStart = async () => {
    if (!modelPath) { show("モデルファイルを選択してください", "error"); return; }
    try {
      await api("/models/llama/config", { method: "PUT", json: { model_path: modelPath, n_gpu_layers: Number(ngl), ctx_size: Number(ctx), flash_attn: flash } });
      await api("/models/llama/start", { method: "POST" });
      show("llama.cpp を起動しました（初回はモデル読み込みに時間がかかります）");
      onChanged();
    } catch (e) { show(e instanceof Error ? e.message : "起動に失敗", "error"); }
  };
  const stop = async () => {
    try { await api("/models/llama/stop", { method: "POST" }); show("停止しました"); onChanged(); }
    catch (e) { show(e instanceof Error ? e.message : "停止に失敗", "error"); }
  };

  return (
    <div className="space-y-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <p className="text-xs font-semibold text-zinc-500">モデルを起動</p>
      <div className="flex gap-1.5">
        <input value={modelPath} onChange={(e) => setModelPath(e.target.value)} placeholder="GGUF ファイルのパス" className={`${input} min-w-0 flex-1 font-mono text-xs`} />
        <button onClick={() => setPickerOpen(true)} className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">📁</button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <L label="GPU 層数 (999=全部)"><input type="number" value={ngl} onChange={(e) => setNgl(e.target.value)} className={input} /></L>
        <L label="コンテキスト長"><input type="number" value={ctx} onChange={(e) => setCtx(e.target.value)} className={input} /></L>
      </div>
      <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
        <span className="text-xs">Flash Attention</span>
        <input type="checkbox" checked={flash} onChange={(e) => setFlash(e.target.checked)} className="h-4 w-4" />
      </label>
      <div className="flex gap-1.5">
        <button onClick={saveAndStart} className="flex-1 rounded-xl bg-accent-600 py-2 text-xs font-medium text-white hover:bg-accent-700">起動</button>
        <button onClick={stop} className="flex-1 rounded-xl bg-zinc-100 py-2 text-xs font-medium hover:bg-zinc-200 dark:bg-zinc-800">停止</button>
      </div>
      {st.base_url && (
        <p className="text-[10px] text-zinc-400">
          起動後はエンドポイント <code className="font-mono">{st.base_url}</code> をチャット/ワークフローの LLM 設定に指定して使えます。
        </p>
      )}
      {pickerOpen && (
        <FilePicker mode="file" title="GGUF モデルを選択" initialPath={modelPath || undefined}
          onSelect={(p) => { setModelPath(p); setPickerOpen(false); }} onClose={() => setPickerOpen(false)} />
      )}
    </div>
  );
}
