import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { ConfirmDialog } from "../../components/ui";

interface CpuReranker {
  id: string; name: string; installed: boolean; installing: boolean; job_id: string | null; model: string; model_bytes: number; device: string;
  runtime: string; accuracy: string; speed: string; memory: string; license: string; error: string; running: boolean;
}

interface RetrievalState {
  cpu_rerankers: CpuReranker[];
  gpu_reranker_installed: boolean;
  selection: { chat: string; knowledge: string };
  choices: string[];
  in_use: { chat: string; knowledge: string };
}

interface JobInfo { id: string; status: string; progress: { status?: string; completed?: number | null; total?: number | null }; error: string }

const SHORT: Record<string, string> = {
  "xsmall-reranker": "xsmall（CPU・速い）",
  "ruri-reranker": "ruri（CPU・高精度）",
  "ruri-reranker-gpu": "ruri（GPU・高精度・速い）",
  gpu: "llama.cpp の reranker（Qwen3-Reranker-4B など）",
  none: "並べ替えない（検索の順）",
};

const USES: { key: "chat" | "knowledge"; label: string; hint: string }[] = [
  { key: "chat", label: "会話の資料", hint: "AI アシスタントの会話で資料を探すとき。速さ優先（おすすめ: ruri の GPU、無ければ xsmall）" },
  { key: "knowledge", label: "Knowledge・Workflow", hint: "Knowledge の検索と Workflow の RAG。精度優先（おすすめ: ruri の GPU、無ければ CPU の ruri）" },
];

/** CPU の reranker（タップ 1 つで導入）と、使う reranker の選択。
 * 2026-11-20 利用者「Models タブで導入できるように。どのモデルを使うか選べるように」。 */
export function RetrievalModelsPanel() {
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const [removing, setRemoving] = useState<CpuReranker | null>(null);
  const { data } = useQuery({
    queryKey: ["retrieval-models"],
    queryFn: () => api<RetrievalState>("/models/retrieval"),
    refetchInterval: Object.keys(jobs).length ? 3000 : 15000,
  });
  const editable = can("workflows.edit");

  const install = async (model: CpuReranker) => {
    try {
      const res = await api<{ job_id: string }>(`/models/retrieval/cpu/${model.id}/install-jobs`, { method: "POST", json: {} });
      setJobs((prev) => ({ ...prev, [model.id]: res.job_id }));
      show(`${model.name} の導入を始めました（サーバー側で続きます）`, "info");
    } catch (e) { show(e instanceof Error ? e.message : "導入を始められませんでした", "error"); }
  };
  const uninstall = async (model: CpuReranker) => {
    try {
      await api(`/models/retrieval/cpu/${model.id}/uninstall`, { method: "POST", json: {} });
      show(`${model.name} をアンインストールしました`);
      void qc.invalidateQueries({ queryKey: ["retrieval-models"] });
    } catch (e) { show(e instanceof Error ? e.message : "外せませんでした", "error"); }
  };
  const select = async (key: "chat" | "knowledge", value: string) => {
    try {
      await api("/models/retrieval/selection", { method: "PUT", json: { [key]: value } });
      void qc.invalidateQueries({ queryKey: ["retrieval-models"] });
    } catch (e) { show(e instanceof Error ? e.message : "選べませんでした", "error"); }
  };

  if (!data) return null;
  const available = (choice: string) => choice === "auto" || choice === "none"
    || (choice === "gpu" ? data.gpu_reranker_installed : data.cpu_rerankers.some((m) => m.id === choice && m.installed));
  const label = (choice: string) => choice === "auto" ? "自動（おすすめ）" : SHORT[choice] ?? choice;

  return (
    <div className="space-y-3" data-retrieval-models>
      <div className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
        <p className="text-sm font-semibold">使う reranker</p>
        <p className="mt-0.5 text-xs text-zinc-400">検索の候補を、質問との関係の深さで並べ直すモデルです。自動は、ruri の GPU → CPU のもの → llama.cpp の reranker の順に、入っているものを使います。埋め込みは bge-m3（GPU）を使います。</p>
        <div className="mt-3 space-y-3">
          {USES.map((use) => (
            <label key={use.key} className="block">
              <span className="text-xs font-medium">{use.label}</span>
              <span className="block text-[11px] text-zinc-400">{use.hint}</span>
              <select value={data.selection[use.key]} disabled={!editable} onChange={(e) => void select(use.key, e.target.value)}
                className="mt-1 w-full rounded-xl border border-zinc-200 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                {data.choices.map((choice) => (
                  <option key={choice} value={choice} disabled={!available(choice)}>
                    {label(choice)}{available(choice) ? "" : "（未導入）"}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[11px] text-zinc-500" data-in-use={use.key}>いま使っているもの: {
                data.in_use[use.key] === "gpu" && !data.gpu_reranker_installed ? "なし（reranker が未導入のため検索の順）"
                  : SHORT[data.in_use[use.key]] ?? data.in_use[use.key]}</span>
            </label>
          ))}
        </div>
      </div>
      {data.cpu_rerankers.map((model) => (
        <CpuRerankerCard key={model.id} model={model} jobId={jobs[model.id] ?? model.job_id ?? undefined} editable={editable}
          onInstall={() => void install(model)} onRemove={() => setRemoving(model)}
          onJobDone={() => { setJobs((prev) => { const next = { ...prev }; delete next[model.id]; return next; }); void qc.invalidateQueries({ queryKey: ["retrieval-models"] }); }} />
      ))}
      {removing && (
        <ConfirmDialog title={`${removing.name} をアンインストールしますか？`}
          message="使わなくなります。変換済みのモデルは残すので、入れ直すときに取り直しません。" confirmLabel="アンインストール"
          onConfirm={() => { void uninstall(removing); setRemoving(null); }} onClose={() => setRemoving(null)} />
      )}
    </div>
  );
}

function CpuRerankerCard({ model, jobId, editable, onInstall, onRemove, onJobDone }: {
  model: CpuReranker; jobId?: string; editable: boolean; onInstall: () => void; onRemove: () => void; onJobDone: () => void;
}) {
  const { data: job } = useQuery({
    queryKey: ["job", jobId], enabled: Boolean(jobId), refetchInterval: 2000,
    queryFn: async () => {
      const value = await api<JobInfo>(`/jobs/${jobId}`);
      if (value.status !== "running" && value.status !== "queued") onJobDone();
      return value;
    },
  });
  const busy = model.installing || (job && (job.status === "running" || job.status === "queued"));
  const progress = job?.progress;
  const percent = progress?.total ? Math.round(((progress.completed ?? 0) / progress.total) * 100) : null;
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900" id={`retrieval-${model.id}`}>
      <div className="flex items-center gap-3">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${model.running ? "bg-emerald-500" : model.installed ? "bg-zinc-300 dark:bg-zinc-600" : "bg-zinc-200 dark:bg-zinc-700"}`}
          title={model.running ? "稼働中" : model.installed ? "導入済み" : "未導入"} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{model.name}
            <span className="ml-2 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-900/50 dark:text-violet-300">{model.device === "gpu" ? "再ランク・GPU" : "再ランク・CPU"}</span>
          </p>
          <p className="mt-0.5 text-xs text-zinc-400">{model.accuracy}。{model.speed}。{model.memory}</p>
        </div>
        {editable && (model.installed ? (
          <button onClick={onRemove} className="shrink-0 rounded-xl px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40">アンインストール</button>
        ) : (
          <button onClick={onInstall} disabled={Boolean(busy)}
            className="shrink-0 rounded-xl bg-accent-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">
            {busy ? "導入中…" : "導入"}
          </button>
        ))}
      </div>
      {busy && progress?.status && (
        <p className="mt-2 text-[11px] text-zinc-500">{progress.status}{percent !== null ? `（${percent}%）` : ""}</p>
      )}
      {job?.status === "failed" && <p className="mt-2 text-[11px] text-red-600">導入に失敗しました: {job.error}</p>}
    </div>
  );
}
