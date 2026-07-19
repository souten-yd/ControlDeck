import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { IconChevronLeft, IconPlay, IconSearch, IconStop } from "../components/icons";
import { initialRuntimeValues, RuntimeField, RuntimeOutputView, type RuntimeOutput } from "../features/workflows/RuntimeComponents";
import type { TriggerInputDef } from "../features/workflows/nodeTypes";
import { useToasts } from "../stores";

interface RunnerApp {
  id: number;
  name: string;
  description: string;
  version: number;
  published_at: string;
  enabled: boolean;
  input_count: number;
  output_count: number;
  side_effects: string[];
}

interface RunnerDetail extends RunnerApp {
  input_schema: { "x-control-deck-fields"?: TriggerInputDef[] };
  output_schema: { "x-control-deck-outputs"?: Array<{ name: string; type: string; title: string; description?: string; schema?: unknown }> };
}

interface RunSummary { id: number; status: string; trigger_type: string; started_at: string; finished_at?: string }
interface RunnerExecution {
  id: number;
  workflow_id: number;
  status: string;
  trigger_type: string;
  started_at: string;
  finished_at?: string;
  error: string;
  input: Record<string, unknown>;
  outputs: Record<string, RuntimeOutput>;
  pending_approvals: Array<{ approval_id: string; message: string; approver: string; expires_at?: number }>;
}

const EFFECT_LABEL: Record<string, string> = { read: "ローカル読取", write: "書込み", external: "外部送信 / LLM", process: "プロセス操作" };
const ACTIVE = new Set(["QUEUED", "RUNNING", "WAITING"]);

export default function WorkflowRunnerPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = Number(params.get("workflow") || 0) || null;
  const [query, setQuery] = useState("");
  const { data: apps = [], isLoading } = useQuery({ queryKey: ["workflow-runner"], queryFn: () => api<RunnerApp[]>("/workflow-runner") });
  const filtered = useMemo(() => apps.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(query.toLowerCase())), [apps, query]);
  const select = (id: number | null) => setParams(id ? { workflow: String(id) } : {}, { replace: true });

  useEffect(() => {
    if (!selectedId && apps.length === 1 && window.matchMedia("(min-width: 768px)").matches) select(apps[0].id);
  // URL選択を一度だけ補助するためselectは依存させない
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apps, selectedId]);

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-[1500px] overflow-hidden md:p-4">
      <aside className={`${selectedId ? "hidden md:flex" : "flex"} min-h-0 w-full flex-col border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950 md:w-80 md:shrink-0 md:rounded-l-2xl md:border`}>
        <header className="shrink-0 border-b border-zinc-200 p-4 dark:border-zinc-800">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent-600">Published workflows</p>
          <h1 className="mt-1 text-xl font-semibold">公開アプリ</h1>
          <p className="mt-1 text-xs leading-relaxed text-zinc-500">公開済みの処理を、入力と結果だけで安全に操作します。</p>
          <label className="mt-3 flex min-h-11 items-center gap-2 rounded-xl bg-zinc-100 px-3 dark:bg-zinc-900">
            <IconSearch className="shrink-0 text-zinc-400" />
            <input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="公開アプリを検索" placeholder="名前・説明を検索" className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
          </label>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
          {isLoading ? <p className="p-4 text-sm text-zinc-400">読み込み中…</p> : filtered.length === 0 ? <EmptyApps /> : filtered.map((item) => (
            <button key={item.id} onClick={() => select(item.id)} className="mb-1 min-h-20 w-full rounded-xl p-3 text-left hover:bg-zinc-100 dark:hover:bg-zinc-900">
              <span className="flex items-center gap-2"><strong className="min-w-0 flex-1 truncate text-sm">{item.name}</strong><span className="num rounded-full bg-zinc-100 px-2 py-1 text-[10px] text-zinc-500 dark:bg-zinc-800">v{item.version}</span></span>
              <span className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-zinc-500">{item.description || "説明はありません"}</span>
              <span className="mt-2 block text-[10px] text-zinc-400">入力 {item.input_count} · 出力 {item.output_count}</span>
            </button>
          ))}
        </div>
      </aside>
      <main className={`${selectedId ? "flex" : "hidden md:flex"} min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-zinc-50 dark:bg-zinc-900/50 md:rounded-r-2xl md:border md:border-l-0 md:border-zinc-200 md:dark:border-zinc-800`}>
        {selectedId ? <RunnerWorkspace workflowId={selectedId} onBack={() => select(null)} /> : <div className="grid h-full place-items-center p-8 text-center text-sm text-zinc-400">左から公開アプリを選択してください</div>}
      </main>
    </div>
  );
}

function EmptyApps() {
  return <div className="m-2 rounded-2xl border border-dashed border-zinc-300 p-6 text-center dark:border-zinc-700"><p className="text-sm font-medium">公開アプリはありません</p><p className="mt-1 text-xs text-zinc-400">エディタで検証して公開すると、ここへ表示されます。</p></div>;
}

function RunnerWorkspace({ workflowId, onBack }: { workflowId: number; onBack: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [executionId, setExecutionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const { data: detail, isLoading, error: detailError } = useQuery({ queryKey: ["workflow-runner", workflowId], queryFn: () => api<RunnerDetail>(`/workflow-runner/${workflowId}`), retry: false });
  const fields = useMemo(() => detail?.input_schema["x-control-deck-fields"] ?? [], [detail]);
  const expected = detail?.output_schema["x-control-deck-outputs"] ?? [];
  useEffect(() => { if (detail) { setValues(initialRuntimeValues(fields)); setExecutionId(null); setError(""); } }, [detail, fields]);
  const { data: runs = [] } = useQuery({ queryKey: ["workflow-runner-runs", workflowId], queryFn: () => api<RunSummary[]>(`/workflow-runner/${workflowId}/runs?limit=20`), refetchInterval: executionId ? 1200 : false });
  const { data: execution } = useQuery({
    queryKey: ["workflow-runner-execution", executionId],
    queryFn: () => api<RunnerExecution>(`/workflow-runner/executions/${executionId}`), enabled: executionId !== null,
    refetchInterval: (query) => !query.state.data || ACTIVE.has(query.state.data.status) ? 700 : false,
  });
  useEffect(() => { if (execution && !ACTIVE.has(execution.status)) void qc.invalidateQueries({ queryKey: ["workflow-runner-runs", workflowId] }); }, [execution, qc, workflowId]);
  const run = useMutation({
    mutationFn: () => api<{ execution_id: number }>(`/workflow-runner/${workflowId}/runs`, { method: "POST", json: { input: values } }),
    onSuccess: (result) => { setExecutionId(result.execution_id); setError(""); },
    onError: (reason) => setError(reason instanceof Error ? reason.message : "実行を開始できませんでした"),
  });
  const action = async (path: string, json?: unknown) => {
    if (!executionId) return;
    try {
      await api(`/workflow-runner/executions/${executionId}/${path}`, { method: "POST", json });
      await qc.invalidateQueries({ queryKey: ["workflow-runner-execution", executionId] });
      show(path === "cancel" ? "実行を停止しました" : "承認操作を送信しました");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "操作に失敗しました"); }
  };
  const loadRun = async (id: number, loadInputs = false) => {
    try {
      const item = await api<RunnerExecution>(`/workflow-runner/executions/${id}`);
      setExecutionId(id);
      if (loadInputs) setValues({ ...initialRuntimeValues(fields), ...item.input });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "実行を読み込めませんでした"); }
  };
  const missing = fields.filter((field) => field.required && (values[field.key] === "" || values[field.key] == null || (Array.isArray(values[field.key]) && (values[field.key] as unknown[]).length === 0)));

  if (isLoading) return <div className="grid h-full place-items-center text-sm text-zinc-400">読み込み中…</div>;
  if (detailError || !detail) return <div className="grid h-full place-items-center p-6"><div className="max-w-sm text-center"><p className="text-sm font-semibold">公開アプリを開けません</p><p className="mt-2 text-xs leading-relaxed text-zinc-500">削除または非公開になった可能性があります。ワークフローの公開状態を確認してください。</p><button onClick={onBack} className="mt-4 min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white">公開アプリ一覧へ戻る</button></div></div>;
  return <>
    <header className="flex min-h-16 shrink-0 items-center gap-2 border-b border-zinc-200 bg-white px-3 dark:border-zinc-800 dark:bg-zinc-950 md:px-5">
      <button onClick={onBack} aria-label="公開アプリ一覧へ戻る" className="grid h-11 w-11 shrink-0 place-items-center rounded-xl hover:bg-zinc-100 dark:hover:bg-zinc-900 md:hidden"><IconChevronLeft /></button>
      <div className="min-w-0 flex-1"><h2 className="truncate text-base font-semibold">{detail.name}</h2><p className="truncate text-[10px] text-zinc-400">公開版 v{detail.version} · {new Date(detail.published_at).toLocaleString("ja-JP")}</p></div>
      {execution && ACTIVE.has(execution.status) && <button onClick={() => void action("cancel")} aria-label="実行を停止" className="flex min-h-11 items-center gap-2 rounded-xl border border-zinc-300 px-3 text-xs dark:border-zinc-700"><IconStop className="h-4 w-4" />停止</button>}
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto p-3 pb-[max(1rem,env(safe-area-inset-bottom))] md:p-5">
      <div className="mx-auto grid max-w-5xl gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(300px,.8fr)]">
        <div className="space-y-4">
          {detail.description && <p className="rounded-2xl bg-white p-4 text-sm leading-relaxed text-zinc-600 shadow-sm dark:bg-zinc-950 dark:text-zinc-300">{detail.description}</p>}
          <section className="rounded-2xl bg-white p-4 shadow-sm dark:bg-zinc-950"><h3 className="text-sm font-semibold">入力</h3><div className="mt-3 space-y-4">{fields.length ? fields.map((field) => <RuntimeField key={field.key} input={field} value={values[field.key]} onChange={(value) => setValues((current) => ({ ...current, [field.key]: value }))} />) : <p className="text-xs text-zinc-400">入力なしで実行できます。</p>}</div>
            <button onClick={() => run.mutate()} disabled={run.isPending || missing.length > 0} className="mt-5 flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-40"><IconPlay className="h-4 w-4" />{run.isPending ? "起動中…" : "公開版を実行"}</button>
            {missing.length > 0 && <p className="mt-2 text-[11px] text-red-500">必須入力: {missing.map((field) => field.label || field.key).join("、")}</p>}
          </section>
          {error && <p role="alert" className="rounded-2xl bg-red-50 p-4 text-sm text-red-600 dark:bg-red-950/30 dark:text-red-300">{error}</p>}
          {execution && <ExecutionCard execution={execution} onApproval={(approvalId, approve) => void action("approval", { approval_id: approvalId, approve })} />}
        </div>
        <aside className="space-y-4">
          <section className="rounded-2xl bg-white p-4 shadow-sm dark:bg-zinc-950"><h3 className="text-sm font-semibold">このアプリの契約</h3>{detail.side_effects.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{detail.side_effects.map((item) => <span key={item} className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">{EFFECT_LABEL[item] || item}</span>)}</div>}<div className="mt-3 space-y-2">{expected.length ? expected.map((item) => <div key={item.name} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex gap-2"><strong className="min-w-0 flex-1 text-xs">{item.title || item.name}</strong><code className="text-[10px] text-zinc-400">{item.type}</code></div>{item.description && <p className="mt-1 text-[10px] text-zinc-400">{item.description}</p>}</div>) : <p className="text-xs text-zinc-400">出力契約はありません。</p>}</div></section>
          <section className="rounded-2xl bg-white p-4 shadow-sm dark:bg-zinc-950"><h3 className="text-sm font-semibold">最近の実行</h3><div className="mt-2 space-y-1">{runs.length ? runs.map((item) => <div key={item.id} className="flex items-center gap-2 rounded-xl px-2 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-900"><button onClick={() => void loadRun(item.id)} className="min-h-11 min-w-0 flex-1 text-left"><span className="block text-xs font-medium">#{item.id} · {statusLabel(item.status)}</span><span className="text-[10px] text-zinc-400">{new Date(item.started_at).toLocaleString("ja-JP")}</span></button><button onClick={() => void loadRun(item.id, true)} className="min-h-11 rounded-lg px-2 text-[10px] text-accent-600">入力を再利用</button></div>) : <p className="py-3 text-xs text-zinc-400">実行履歴はありません。</p>}</div></section>
        </aside>
      </div>
    </div>
  </>;
}

function ExecutionCard({ execution, onApproval }: { execution: RunnerExecution; onApproval: (id: string, approve: boolean) => void }) {
  const active = ACTIVE.has(execution.status);
  return <section aria-live="polite" className="rounded-2xl bg-white p-4 shadow-sm dark:bg-zinc-950"><div className="flex items-center gap-2"><h3 className="min-w-0 flex-1 text-sm font-semibold">実行 #{execution.id}</h3><span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${execution.status === "SUCCEEDED" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : active ? "bg-accent-50 text-accent-700 dark:bg-accent-950/40 dark:text-accent-300" : "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300"}`}>{statusLabel(execution.status)}</span></div>{active && <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><div className="h-full w-2/3 animate-pulse rounded-full bg-accent-500" /></div>}{execution.error && <p className="mt-3 rounded-xl bg-red-50 p-3 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-300">{execution.error}</p>}
    {execution.pending_approvals.map((approval) => <div key={approval.approval_id} className="mt-3 rounded-xl border border-amber-300 bg-amber-50/60 p-3 dark:border-amber-800 dark:bg-amber-950/20"><p className="text-xs font-semibold">承認が必要です</p><p className="mt-1 text-sm leading-relaxed">{approval.message}</p>{approval.approver && <p className="mt-1 text-[10px] text-zinc-500">担当: {approval.approver}</p>}<div className="mt-3 grid grid-cols-2 gap-2"><button onClick={() => onApproval(approval.approval_id, false)} className="min-h-11 rounded-xl border border-zinc-300 text-xs dark:border-zinc-700">却下</button><button onClick={() => onApproval(approval.approval_id, true)} className="min-h-11 rounded-xl bg-accent-600 text-xs font-semibold text-white">承認</button></div></div>)}
    {!active && <div className="mt-4 space-y-3">{Object.keys(execution.outputs).length ? Object.entries(execution.outputs).map(([name, output]) => <article key={name} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex gap-2"><strong className="min-w-0 flex-1 text-xs">{output.title || name}</strong><code className="text-[10px] text-zinc-400">{output.type}</code></div>{output.description && <p className="mt-1 text-[10px] text-zinc-400">{output.description}</p>}<RuntimeOutputView output={output} /></article>) : <p className="text-xs text-zinc-400">最終出力はありません。</p>}</div>}
  </section>;
}

function statusLabel(status: string) {
  return ({ QUEUED: "待機中", RUNNING: "実行中", WAITING: "承認待ち", SUCCEEDED: "成功", FAILED: "失敗", CANCELED: "停止", TIMED_OUT: "時間切れ" } as Record<string, string>)[status] || status;
}
