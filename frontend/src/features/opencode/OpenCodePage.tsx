import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, wsUrl } from "../../api/client";
import { FilePicker } from "../../components/FilePicker";
import { useToasts } from "../../stores";

interface FeatureState {
  id: string; installed: boolean; managed: boolean; enabled: boolean;
  version: string; health: string; executable: string;
}
interface Settings { base_url: string; model: string; project_path: string }
interface Status { feature: FeatureState; settings: Settings }
interface Job {
  id: string; kind: string; status: string; error: string;
  progress?: { status?: string }; result?: { output?: string; events?: number };
}

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900";

export default function OpenCodePage() {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["opencode-status"], queryFn: () => api<Status>("/opencode/status"), staleTime: 30_000 });
  const [form, setForm] = useState<Settings>({ base_url: "", model: "", project_path: "" });
  const [operation, setOperation] = useState("analyze");
  const [instruction, setInstruction] = useState("");
  const [picker, setPicker] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => { if (data) setForm(data.settings); }, [data]);
  useEffect(() => {
    if (!jobId) return;
    void api<Job>(`/jobs/${jobId}`).then(setJob).catch(() => undefined);
    let disposed = false;
    let retry: number | undefined;
    let ws: WebSocket | null = null;
    const connect = () => {
      if (disposed) return;
      ws = new WebSocket(wsUrl("/jobs/stream"));
      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type === "update" && payload.job?.id === jobId) setJob(payload.job as Job);
        if (payload.type === "snapshot") {
          const found = (payload.jobs as Job[]).find((item) => item.id === jobId);
          if (found) setJob(found);
        }
      };
      ws.onclose = () => { if (!disposed) retry = window.setTimeout(connect, 1500); };
    };
    connect();
    return () => { disposed = true; window.clearTimeout(retry); ws?.close(); };
  }, [jobId]);

  const save = useMutation({
    mutationFn: () => api<Settings>("/opencode/settings", { method: "PUT", json: form }),
    onSuccess: (settings) => { setForm(settings); qc.invalidateQueries({ queryKey: ["opencode-status"] }); show("OpenCode設定を保存しました"); },
    onError: (error) => show(error instanceof Error ? error.message : "設定保存に失敗", "error"),
  });
  const run = useMutation({
    mutationFn: () => api<{ job_id: string }>("/opencode/run", {
      method: "POST", json: { ...form, operation, instruction },
    }),
    onSuccess: ({ job_id }) => { setJobId(job_id); setJob(null); show("OpenCodeジョブを開始しました", "info"); },
    onError: (error) => show(error instanceof Error ? error.message : "実行開始に失敗", "error"),
  });
  const busy = job?.status === "queued" || job?.status === "running";

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4 pb-24 sm:p-6">
      <header>
        <h1 className="text-xl font-semibold">OpenCode</h1>
        <p className="mt-1 text-xs text-zinc-500">オプトインのcoding agent。実行はsystemd user unitで分離されます。</p>
      </header>
      <section className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><p className="text-sm font-medium">{data?.feature.version || "確認中"}</p><p className="text-xs text-zinc-400">{data?.feature.executable}</p></div>
          <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400">有効 · {data?.feature.health}</span>
        </div>
      </section>
      <section className="grid gap-3 rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800 sm:grid-cols-2">
        <label className="text-xs text-zinc-500">LLM endpoint<input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} className={`${input} mt-1 font-mono`} /></label>
        <label className="text-xs text-zinc-500">モデル<input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} className={`${input} mt-1 font-mono`} /></label>
        <label className="text-xs text-zinc-500 sm:col-span-2">プロジェクト
          <div className="mt-1 flex gap-2"><input value={form.project_path} onChange={(e) => setForm({ ...form, project_path: e.target.value })} className={`${input} min-w-0 font-mono`} /><button onClick={() => setPicker(true)} className="shrink-0 rounded-xl border border-zinc-300 px-3 text-xs dark:border-zinc-700">選択</button></div>
        </label>
        <button onClick={() => save.mutate()} disabled={save.isPending} className="rounded-xl border border-accent-500 py-2 text-sm text-accent-600 disabled:opacity-50 sm:col-span-2">設定を保存</button>
      </section>
      <section className="space-y-3 rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
        <label className="block text-xs text-zinc-500">操作<select value={operation} onChange={(e) => setOperation(e.target.value)} className={`${input} mt-1`}><option value="analyze">分析（変更なし）</option><option value="implement">実装</option><option value="fix">不具合修正</option><option value="test">テスト</option><option value="review">レビュー（変更なし）</option></select></label>
        <label className="block text-xs text-zinc-500">指示<textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={6} className={`${input} mt-1 resize-y`} placeholder="調査・実装してほしい内容" /></label>
        <button onClick={() => run.mutate()} disabled={run.isPending || busy || !instruction.trim() || !form.project_path} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white disabled:opacity-40">{busy ? "実行中" : "OpenCodeで実行"}</button>
      </section>
      {job && <section className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800"><div className="flex items-center justify-between gap-2"><p className="text-sm font-medium">{job.status === "succeeded" ? "完了" : job.status === "failed" ? "失敗" : job.progress?.status || job.status}</p>{busy && <button onClick={() => api(`/jobs/${job.id}/cancel`, { method: "POST" })} className="text-xs text-red-500">キャンセル</button>}</div>{job.error && <p className="mt-2 text-xs text-red-500">{job.error}</p>}{job.result?.output && <pre className="mt-3 max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-xl bg-zinc-950 p-3 text-xs text-zinc-100">{job.result.output}</pre>}</section>}
      {picker && <FilePicker mode="dir" title="プロジェクトを選択" initialPath={form.project_path || undefined} onSelect={(path) => { setForm({ ...form, project_path: path }); setPicker(false); }} onClose={() => setPicker(false)} />}
    </div>
  );
}
