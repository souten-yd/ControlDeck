import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { IconX } from "../../components/icons";
import type { TriggerInputDef } from "./nodeTypes";

interface DefinitionNode {
  id: string;
  type: string;
  name?: string;
  config?: Record<string, unknown>;
}

interface Definition {
  nodes: DefinitionNode[];
  edges: Array<{ source: string; target: string; branch?: string | null }>;
}

interface PreviewResult {
  valid: boolean;
  dry_run: true;
  errors: string[];
  warnings: string[];
  notice: string;
  input: Record<string, unknown>;
  summary: {
    nodes: number;
    reachable: number;
    side_effects: Record<string, number>;
    quality?: { score: number; label: string };
  };
  plan: Array<{
    id: string;
    name: string;
    type: string;
    wave: number | null;
    status: string;
    side_effect: string;
    capabilities: string[];
  }>;
}

interface ExecutionSummary {
  id: number;
  status: string;
  trigger_type: string;
  started_at: string;
}

interface ExecutionDetail {
  id: number;
  status: string;
  error: string;
  input: Record<string, unknown>;
  outputs: Record<string, { type: string; value: unknown; source_node_id: string }>;
  context: Record<string, {
    status: string;
    name?: string;
    type?: string;
    output?: unknown;
    error?: string;
    attempts?: number;
    started_at?: string;
    finished_at?: string;
  }>;
}

const SIDE_EFFECT_LABEL: Record<string, string> = {
  none: "なし",
  read: "読取",
  write: "書込",
  external: "外部送信",
  process: "プロセス実行",
};

function initialValues(inputs: TriggerInputDef[]): Record<string, unknown> {
  return Object.fromEntries(
    inputs
      .filter((input) => input.key)
      .map((input) => [input.key, input.default ?? (input.type === "boolean" ? false : "")]),
  );
}

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function PreviewWorkspace({
  workflowId,
  definition,
  inputs,
  dirty,
  onSave,
  onExecution,
  onClose,
}: {
  workflowId: number;
  definition: Definition;
  inputs: TriggerInputDef[];
  dirty: boolean;
  onSave: () => Promise<void>;
  onExecution: (executionId: number) => void;
  onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() => initialValues(inputs));
  const [mode, setMode] = useState<"safe" | "test">("safe");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [executionId, setExecutionId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => setValues((current) => ({ ...initialValues(inputs), ...current })), [inputs]);

  const { data: executions } = useQuery({
    queryKey: ["executions", workflowId],
    queryFn: () => api<ExecutionSummary[]>(`/workflow-executions?workflow_id=${workflowId}&limit=10`),
    refetchInterval: (query) =>
      executionId !== null && !query.state.data?.some((item) => item.id === executionId) ? 1000 : false,
  });
  const { data: execution } = useQuery({
    queryKey: ["execution-preview", executionId],
    queryFn: () => api<ExecutionDetail>(`/workflow-executions/${executionId}`),
    enabled: executionId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return !status || ["QUEUED", "RUNNING", "WAITING"].includes(status) ? 800 : false;
    },
  });

  const expectedOutputs = useMemo(
    () => definition.nodes.flatMap((node) => {
      if (node.type === "signal.display") {
        return [{
          name: String(node.config?.signal || node.id),
          type: "text",
          source: node.name || node.id,
          schema: { type: "string" },
        }];
      }
      if (node.type === "output.render") {
        return [{
          name: String(node.config?.name || node.id),
          type: String(node.config?.renderer || "auto"),
          source: node.name || node.id,
          schema: node.config?.schema || {},
        }];
      }
      return [];
    }),
    [definition.nodes],
  );

  const missing = inputs.filter((input) => input.required && (values[input.key] === "" || values[input.key] == null));

  const execute = async () => {
    setBusy(true);
    setError("");
    try {
      if (mode === "safe") {
        setExecutionId(null);
        setPreview(await api<PreviewResult>("/workflows/preview-definition", {
          method: "POST",
          json: { definition, input: values },
        }));
      } else {
        if (dirty) await onSave();
        const started = await api<{ execution_id: number }>(`/workflows/${workflowId}/test`, {
          method: "POST",
          json: { input: values },
        });
        setPreview(null);
        setExecutionId(started.execution_id);
        onExecution(started.execution_id);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "実行に失敗しました");
    } finally {
      setBusy(false);
    }
  };

  const loadInputs = async (id: number) => {
    setError("");
    try {
      const loaded = await api<{ input: Record<string, unknown> }>(
        `/workflows/${workflowId}/executions/${id}/load-inputs`,
        { method: "POST" },
      );
      setValues({ ...initialValues(inputs), ...loaded.input });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "入力の読込に失敗しました");
    }
  };

  return (
    <aside
      aria-label="実行プレビュー"
      className="absolute inset-x-2 bottom-2 top-2 z-30 flex flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 sm:left-auto sm:w-[min(520px,calc(100%-2rem))]"
    >
      <header className="flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">実行プレビュー</h2>
          <p className="text-[10px] text-zinc-400">入力、予定操作、最終出力を同じ画面で確認</p>
        </div>
        <button onClick={onClose} aria-label="プレビューを閉じる" className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"><IconX /></button>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <section aria-labelledby="preview-input-heading">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 id="preview-input-heading" className="text-xs font-semibold">入力</h3>
            {executions && executions.length > 0 && (
              <select
                aria-label="過去実行の入力を読み込む"
                defaultValue=""
                onChange={(event) => {
                  const id = Number(event.target.value);
                  if (id) void loadInputs(id);
                  event.target.value = "";
                }}
                className="min-w-0 max-w-52 rounded-lg border border-zinc-300 bg-white px-2 py-1 text-[11px] dark:border-zinc-700 dark:bg-zinc-900"
              >
                <option value="">過去の入力を読込…</option>
                {executions.map((item) => (
                  <option key={item.id} value={item.id}>#{item.id} {new Date(item.started_at).toLocaleString("ja-JP")}</option>
                ))}
              </select>
            )}
          </div>
          {inputs.length === 0 ? (
            <p className="rounded-xl border border-dashed border-zinc-300 p-3 text-xs text-zinc-400 dark:border-zinc-700">入力フィールドは定義されていません。</p>
          ) : (
            <div className="space-y-3">
              {inputs.map((input) => (
                <PreviewField key={input.key} input={input} value={values[input.key]} onChange={(value) => setValues((current) => ({ ...current, [input.key]: value }))} />
              ))}
            </div>
          )}
        </section>

        <section>
          <h3 className="mb-2 text-xs font-semibold">実行モード</h3>
          <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="実行モード">
            <ModeButton active={mode === "safe"} title="安全プレビュー" description="executorを呼ばない" onClick={() => setMode("safe")} />
            <ModeButton active={mode === "test"} title="通常テスト実行" description="保存後に実行する" onClick={() => setMode("test")} />
          </div>
          <p className="mt-1.5 text-[10px] text-zinc-400">選択ノードまで／ノードから再実行／mock は再現性Phaseで追加します。</p>
        </section>

        <section>
          <h3 className="mb-2 text-xs font-semibold">想定される最終出力</h3>
          {expectedOutputs.length === 0 ? (
            <p className="rounded-xl bg-amber-50 p-2.5 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">出力ノードがありません。</p>
          ) : expectedOutputs.map((output) => (
            <div key={`${output.source}-${output.name}`} className="mb-1.5 rounded-xl border border-zinc-200 p-2.5 text-xs dark:border-zinc-700">
              <div className="flex items-center gap-2"><strong className="min-w-0 flex-1 truncate">{output.name}</strong><code className="text-[10px] text-zinc-400">{output.type}</code></div>
              <p className="mt-1 text-[10px] text-zinc-400">出力元: {output.source} · schema: {stringify(output.schema)}</p>
            </div>
          ))}
        </section>

        {preview && (
          <>
            <section>
              <h3 className="mb-2 text-xs font-semibold">副作用</h3>
              {Object.keys(preview.summary.side_effects).length === 0 ? <p className="text-xs text-emerald-600">なし</p> : (
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(preview.summary.side_effects).map(([kind, count]) => <span key={kind} className="rounded-full bg-amber-100 px-2 py-1 text-[10px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">{SIDE_EFFECT_LABEL[kind] ?? kind}: {count}</span>)}
                </div>
              )}
            </section>
            <section>
              <h3 className="mb-2 text-xs font-semibold">安全プレビュー結果</h3>
              <ResultNotice ok={preview.valid} title={preview.valid ? "実行可能な定義です" : "修正が必要です"} detail={preview.notice} />
              <IssueList errors={preview.errors} warnings={preview.warnings} />
              <details className="mt-2"><summary className="cursor-pointer text-xs text-zinc-500">ノードごとの実行予定 ({preview.summary.reachable}/{preview.summary.nodes})</summary><NodePlan plan={preview.plan} /></details>
            </section>
          </>
        )}

        {execution && (
          <ExecutionResult execution={execution} />
        )}
        {error && <p role="alert" className="rounded-xl bg-red-50 p-3 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-400">{error}</p>}
      </div>

      <footer className="shrink-0 border-t border-zinc-200 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] dark:border-zinc-800">
        <button
          onClick={() => void execute()}
          disabled={busy || missing.length > 0}
          className="min-h-11 w-full rounded-xl bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
        >
          {busy ? "準備中…" : mode === "safe" ? "安全プレビューを実行" : "テスト実行"}
        </button>
        {missing.length > 0 && <p className="mt-1 text-[10px] text-red-500">必須入力: {missing.map((item) => item.label || item.key).join("、")}</p>}
      </footer>
    </aside>
  );
}

function PreviewField({ input, value, onChange }: { input: TriggerInputDef; value: unknown; onChange: (value: unknown) => void }) {
  const id = `preview-input-${input.key}`;
  const cls = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950";
  const options = (input.options || "").split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
  return (
    <label htmlFor={id} className="block">
      <span className="mb-1 block text-xs font-medium">{input.label || input.key}{input.required ? " *" : ""}</span>
      {input.description && <span className="mb-1 block text-[10px] text-zinc-400">{input.description}</span>}
      {input.type === "paragraph" ? <textarea id={id} rows={3} value={String(value ?? "")} maxLength={input.maxLength} placeholder={input.placeholder} onChange={(event) => onChange(event.target.value)} className={cls} />
        : input.type === "number" ? <input id={id} type="number" value={String(value ?? "")} placeholder={input.placeholder} onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))} className={cls} />
          : input.type === "boolean" ? <input id={id} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} className="h-5 w-5" />
            : input.type === "select" ? <select id={id} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className={cls}><option value="">選択してください</option>{options.map((option) => <option key={option}>{option}</option>)}</select>
              : input.type === "multi_select" ? <select id={id} multiple value={Array.isArray(value) ? value.map(String) : []} onChange={(event) => onChange(Array.from(event.target.selectedOptions, (option) => option.value))} className={cls}>{options.map((option) => <option key={option}>{option}</option>)}</select>
                : input.type === "json" || input.type === "key_value" ? <textarea id={id} rows={4} value={String(value ?? "")} placeholder={input.placeholder || "{}"} onChange={(event) => onChange(event.target.value)} className={`${cls} font-mono text-xs`} />
                  : <input id={id} type={input.type === "date" ? "date" : input.type === "datetime" ? "datetime-local" : input.type === "secret_reference" ? "password" : "text"} value={String(value ?? "")} maxLength={input.maxLength} placeholder={input.placeholder} onChange={(event) => onChange(event.target.value)} className={cls} />}
    </label>
  );
}

function ModeButton({ active, title, description, onClick }: { active: boolean; title: string; description: string; onClick: () => void }) {
  return <button type="button" role="radio" aria-checked={active} onClick={onClick} className={`min-h-16 rounded-xl border p-2 text-left ${active ? "border-accent-500 bg-accent-50 dark:bg-accent-600/10" : "border-zinc-200 dark:border-zinc-700"}`}><span className="block text-xs font-medium">{title}</span><span className="text-[10px] text-zinc-400">{description}</span></button>;
}

function ResultNotice({ ok, title, detail }: { ok: boolean; title: string; detail: string }) {
  return <div className={`rounded-xl border p-3 ${ok ? "border-emerald-300 bg-emerald-50/50 dark:border-emerald-800 dark:bg-emerald-950/20" : "border-red-300 bg-red-50/50 dark:border-red-800 dark:bg-red-950/20"}`}><p className={`text-xs font-semibold ${ok ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}`}>{ok ? "✓ " : "✕ "}{title}</p><p className="mt-1 text-[10px] text-zinc-500">{detail}</p></div>;
}

function IssueList({ errors, warnings }: { errors: string[]; warnings: string[] }) {
  return <div className="mt-2 space-y-1">{errors.map((item, index) => <p key={`e-${index}`} className="rounded-lg bg-red-50 px-2.5 py-1.5 text-[11px] text-red-600 dark:bg-red-950/30 dark:text-red-400">{item}</p>)}{warnings.map((item, index) => <p key={`w-${index}`} className="rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">{item}</p>)}</div>;
}

function NodePlan({ plan }: { plan: PreviewResult["plan"] }) {
  return <ol className="mt-2 space-y-1.5">{plan.map((item) => <li key={item.id} className="rounded-lg border border-zinc-200 p-2 text-[11px] dark:border-zinc-700"><div className="flex gap-2"><span className="num text-zinc-400">{item.wave ?? "–"}</span><strong className="min-w-0 flex-1 truncate">{item.name}</strong>{item.side_effect !== "none" && <span className="text-amber-600">{SIDE_EFFECT_LABEL[item.side_effect] ?? item.side_effect}</span>}</div><code className="text-[9px] text-zinc-400">{item.type} · {item.status}</code></li>)}</ol>;
}

function ExecutionResult({ execution }: { execution: ExecutionDetail }) {
  const running = ["QUEUED", "RUNNING", "WAITING"].includes(execution.status);
  return <section aria-live="polite"><h3 className="mb-2 text-xs font-semibold">実行結果 <span className="num font-normal text-zinc-400">#{execution.id}</span></h3><ResultNotice ok={execution.status === "SUCCEEDED"} title={running ? "実行中…" : execution.status === "SUCCEEDED" ? "テストに成功しました" : `実行 ${execution.status}`} detail={execution.error || (running ? "ノードの完了を待っています" : "最終出力とノード結果を確認できます")} />{!running && <div className="mt-3 space-y-2">{Object.keys(execution.outputs).length === 0 ? <p className="text-xs text-amber-600">最終出力はありません。</p> : Object.entries(execution.outputs).map(([name, output]) => <article key={name} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><div className="flex gap-2"><strong className="min-w-0 flex-1 text-xs">{name}</strong><code className="text-[10px] text-zinc-400">{output.type}</code></div><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-2 text-xs dark:bg-zinc-950">{stringify(output.value)}</pre></article>)}<details><summary className="cursor-pointer text-xs text-zinc-500">ノードごとの結果 ({Object.keys(execution.context).length})</summary><div className="mt-2 space-y-1.5">{Object.entries(execution.context).map(([id, item]) => <div key={id} className="rounded-lg border border-zinc-200 p-2 text-[11px] dark:border-zinc-700"><div className="flex gap-2"><strong className="min-w-0 flex-1 truncate">{item.name || id}</strong><span>{item.status}</span></div>{item.error && <p className="mt-1 text-red-500">{item.error}</p>}{item.output !== undefined && <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] text-zinc-500">{stringify(item.output)}</pre>}</div>)}</div></details></div>}</section>;
}
