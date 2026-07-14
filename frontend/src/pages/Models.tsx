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
interface Settings {
  base_url: string;
  idle_unload_enabled: boolean;
  idle_unload_minutes: number;
  default_keep_alive: string;
  default_model: string;
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
            <button onClick={() => setSettingsOpen(true)} aria-label="設定" className="rounded-xl border border-zinc-300 px-3 py-2 text-sm text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300">⚙</button>
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

function SettingsSheet({ models, onClose }: { models: Model[]; onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["ollama-settings"], queryFn: () => api<Settings>("/models/settings") });
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
    <BottomSheet title="Model 設定" onClose={onClose} wide>
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
        <button onClick={() => save.mutate()} disabled={save.isPending} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          {save.isPending ? "保存中..." : "保存"}
        </button>
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
function L({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>{children}</label>;
}
