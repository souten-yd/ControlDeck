import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { IconChevronLeft, IconPlay, IconStop } from "../components/icons";
import { initialRuntimeValues, RuntimeField, RuntimeOutputView, type RuntimeOutput } from "../features/workflows/RuntimeComponents";
import { ApprovalResponseFields, missingRequiredFormFields, type ApprovalFormSchema } from "../features/workflows/ApprovalResponseFields";
import type { TriggerInputDef } from "../features/workflows/nodeTypes";
import { useToasts } from "../stores";
interface RunnerDetail {
  id: number;
  name: string;
  description: string;
  version: number;
  published_at: string;
  enabled: boolean;
  input_count: number;
  output_count: number;
  side_effects: string[];
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
  pending_approvals: Array<{ approval_id: string; interaction_type?: "approval" | "form"; message: string; approver: string; expires_at?: string | null; form_schema?: ApprovalFormSchema }>;
}

const EFFECT_LABEL: Record<string, string> = { read: "ローカル読取", write: "書込み", external: "外部送信 / LLM", process: "プロセス操作" };
const ACTIVE = new Set(["QUEUED", "RUNNING", "WAITING"]);

export default function WorkflowRunnerPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const selectedId = Number(params.get("workflow") || 0) || null;
  if (!selectedId) return <Navigate to="/workflows" replace />;

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-[1180px] overflow-hidden md:p-4">
      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-zinc-50 dark:bg-zinc-900/50 md:rounded-2xl md:border md:border-zinc-200 md:dark:border-zinc-800">
        <RunnerWorkspace workflowId={selectedId} onBack={() => navigate("/workflows")} />
      </main>
    </div>
  );
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
      show(path === "cancel" ? "実行を停止しました" : "入力操作を送信しました");
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
  if (detailError || !detail) return <div className="grid h-full place-items-center p-6"><div className="max-w-sm text-center"><p className="text-sm font-semibold">公開アプリを開けません</p><p className="mt-2 text-xs leading-relaxed text-zinc-500">削除または非公開になった可能性があります。ワークフローの公開状態を確認してください。</p><button onClick={onBack} className="mt-4 min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white">Back to Workflows</button></div></div>;
  return <>
    <header className="flex min-h-16 shrink-0 items-center gap-2 border-b border-zinc-200 bg-white px-3 dark:border-zinc-800 dark:bg-zinc-950 md:px-5">
      <button onClick={onBack} aria-label="Back to Workflows" className="grid h-11 w-11 shrink-0 place-items-center rounded-xl hover:bg-zinc-100 dark:hover:bg-zinc-900"><IconChevronLeft /></button>
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
          {execution && <ExecutionCard execution={execution} onApproval={(approvalId, approve, response) => void action("approval", { approval_id: approvalId, approve, response })} />}
        </div>
        <aside className="space-y-4">
          <section className="rounded-2xl bg-white p-4 shadow-sm dark:bg-zinc-950"><h3 className="text-sm font-semibold">このアプリの契約</h3>{detail.side_effects.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{detail.side_effects.map((item) => <span key={item} className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">{EFFECT_LABEL[item] || item}</span>)}</div>}<div className="mt-3 space-y-2">{expected.length ? expected.map((item) => <div key={item.name} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex gap-2"><strong className="min-w-0 flex-1 text-xs">{item.title || item.name}</strong><code className="text-[10px] text-zinc-400">{item.type}</code></div>{item.description && <p className="mt-1 text-[10px] text-zinc-400">{item.description}</p>}</div>) : <p className="text-xs text-zinc-400">出力契約はありません。</p>}</div></section>
          <section className="rounded-2xl bg-white p-4 shadow-sm dark:bg-zinc-950"><h3 className="text-sm font-semibold">最近の実行</h3><div className="mt-2 space-y-1">{runs.length ? runs.map((item) => <div key={item.id} className="flex items-center gap-2 rounded-xl px-2 py-2 hover:bg-zinc-50 dark:hover:bg-zinc-900"><button onClick={() => void loadRun(item.id)} className="min-h-11 min-w-0 flex-1 text-left"><span className="block text-xs font-medium">#{item.id} · {statusLabel(item.status)}</span><span className="text-[10px] text-zinc-400">{new Date(item.started_at).toLocaleString("ja-JP")}</span></button><button onClick={() => void loadRun(item.id, true)} className="min-h-11 rounded-lg px-2 text-[10px] text-accent-600">入力を再利用</button></div>) : <p className="py-3 text-xs text-zinc-400">実行履歴はありません。</p>}</div></section>
        </aside>
      </div>
    </div>
  </>;
}

function ExecutionCard({ execution, onApproval }: { execution: RunnerExecution; onApproval: (id: string, approve: boolean, response: Record<string, unknown>) => void }) {
  const active = ACTIVE.has(execution.status);
  const [approvalResponses, setApprovalResponses] = useState<Record<string, Record<string, unknown>>>({});
  return <section aria-live="polite" className="rounded-2xl bg-white p-4 shadow-sm dark:bg-zinc-950"><div className="flex items-center gap-2"><h3 className="min-w-0 flex-1 text-sm font-semibold">実行 #{execution.id}</h3><span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${execution.status === "SUCCEEDED" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : active ? "bg-accent-50 text-accent-700 dark:bg-accent-950/40 dark:text-accent-300" : "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300"}`}>{statusLabel(execution.status)}</span></div>{active && <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><div className="h-full w-2/3 animate-pulse rounded-full bg-accent-500" /></div>}{execution.error && <p className="mt-3 rounded-xl bg-red-50 p-3 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-300">{execution.error}</p>}
    {execution.pending_approvals.map((approval) => {
      const isForm = approval.interaction_type === "form";
      const response = approvalResponses[approval.approval_id] ?? {};
      const missing = missingRequiredFormFields(approval.form_schema, response);
      return <div key={approval.approval_id} className="mt-3 rounded-xl border border-amber-300 bg-amber-50/60 p-3 dark:border-amber-800 dark:bg-amber-950/20">
        <p className="text-xs font-semibold">{isForm ? "入力が必要です" : "承認が必要です"}</p>
        <p className="mt-1 text-sm leading-relaxed">{approval.message}</p>
        {approval.approver && <p className="mt-1 text-[10px] text-zinc-500">{isForm ? "担当" : "承認者"}: {approval.approver}</p>}
        {approval.expires_at && <p className="mt-1 text-[10px] text-zinc-500">期限: {new Date(approval.expires_at).toLocaleString("ja-JP")}</p>}
        <ApprovalResponseFields idPrefix={`runner-interaction-${approval.approval_id}`} schema={approval.form_schema} value={response} onChange={(next) => setApprovalResponses((current) => ({ ...current, [approval.approval_id]: next }))} />
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button onClick={() => onApproval(approval.approval_id, false, response)} className="min-h-11 rounded-xl border border-zinc-300 text-xs dark:border-zinc-700">{isForm ? "キャンセル" : "却下"}</button>
          <button disabled={missing.length > 0} onClick={() => onApproval(approval.approval_id, true, response)} className="min-h-11 rounded-xl bg-accent-600 text-xs font-semibold text-white disabled:opacity-40">{isForm ? "送信" : "承認"}</button>
        </div>
        {missing.length > 0 && <p className="mt-2 text-[10px] text-red-600">必須入力: {missing.join("、")}</p>}
      </div>;
    })}
    {!active && <div className="mt-4 space-y-3">{Object.keys(execution.outputs).length ? Object.entries(execution.outputs).map(([name, output]) => <article key={name} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex gap-2"><strong className="min-w-0 flex-1 text-xs">{output.title || name}</strong><code className="text-[10px] text-zinc-400">{output.type}</code></div>{output.description && <p className="mt-1 text-[10px] text-zinc-400">{output.description}</p>}<RuntimeOutputView output={output} /></article>) : <p className="text-xs text-zinc-400">最終出力はありません。</p>}</div>}
  </section>;
}

function statusLabel(status: string) {
  return ({ QUEUED: "待機中", RUNNING: "実行中", WAITING: "入力待ち", SUCCEEDED: "成功", FAILED: "失敗", CANCELED: "停止", TIMED_OUT: "時間切れ" } as Record<string, string>)[status] || status;
}
