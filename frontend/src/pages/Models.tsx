/** Model（Ollama）管理。取得(HF含む)/削除/ロード/アンロード/詳細/設定/自動アンロード。 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, wsUrl } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { IconPlus, IconSearch, IconTrash } from "../components/icons";

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
  const [tab, setTab] = useState<"registry" | "hf">("registry");
  const [model, setModel] = useState("");
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [pct, setPct] = useState<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  useEffect(() => () => wsRef.current?.close(), []);

  const start = (name: string) => {
    const target = name.trim();
    if (!target) return;
    setRunning(true); setStatus("接続中..."); setPct(null);
    const ws = new WebSocket(wsUrl("/models/pull"));
    wsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ model: target }));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "progress") {
        setStatus(m.status || "取得中...");
        if (m.total && m.completed) setPct(Math.round((m.completed / m.total) * 100));
      } else if (m.type === "done") {
        setStatus("完了"); setRunning(false); show(`「${target}」を取得しました`); onDone();
      } else if (m.type === "error") {
        setStatus(`エラー: ${m.message}`); setRunning(false); show(m.message, "error");
      }
    };
    ws.onclose = () => setRunning(false);
    ws.onerror = () => { setStatus("接続エラー"); setRunning(false); };
  };

  return (
    <BottomSheet title="モデル取得" onClose={onClose} wide>
      <div className="mb-3 flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
        <button onClick={() => setTab("registry")} className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${tab === "registry" ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>Ollama レジストリ</button>
        <button onClick={() => setTab("hf")} className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${tab === "hf" ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>HuggingFace (GGUF)</button>
      </div>

      {tab === "registry" ? (
        <div className="space-y-2">
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="例: llama3.2  /  qwen2.5:7b  /  nomic-embed-text" className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
          <button onClick={() => start(model)} disabled={running || !model.trim()} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
            {running ? "取得中..." : "取得"}
          </button>
        </div>
      ) : (
        <HFSearch onPull={start} running={running} />
      )}

      {(running || status) && (
        <div className="mt-3 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
          <p className="truncate text-xs text-zinc-500">{status}</p>
          {pct !== null && (
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
              <div className="h-full rounded-full bg-accent-500 transition-all" style={{ width: `${pct}%` }} />
            </div>
          )}
        </div>
      )}
    </BottomSheet>
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
