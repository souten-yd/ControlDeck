import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { BottomSheet } from "../../components/ui";
import type { TriggerInputDef } from "./nodeTypes";
import { initialRuntimeValues, RuntimeField, RuntimeOutputView, type RuntimeOutput } from "./RuntimeComponents";

type RunMode = "draft" | "publish";

interface ExecutionDetail {
  id: number;
  status: string;
  error: string;
  outputs: Record<string, RuntimeOutput & { source_node_id?: string }>;
  context: Record<string, { status: string; name?: string; type?: string; output?: unknown; error?: string }>;
}

interface PublishCheck {
  publishable: boolean;
  blocking: string[];
  warnings: string[];
}

const RUNNING = ["QUEUED", "RUNNING", "WAITING_APPROVAL", "WAITING_FORM"];

function stringify(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

/** 実行の入口をこの1枚に集約する。入力 → 実行方法 → 結果だけを見せ、詳細は畳む。 */
export function RunSheet({
  workflowId, inputs, dirty, readOnly, onSave, onStatuses, onClose,
}: {
  workflowId: number;
  inputs: TriggerInputDef[];
  dirty: boolean;
  readOnly: boolean;
  onSave: () => Promise<boolean>;
  onStatuses: (statuses: Record<string, string>) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, unknown>>(() => initialRuntimeValues(inputs));
  const [mode, setMode] = useState<RunMode>(readOnly ? "publish" : "draft");
  const [executionId, setExecutionId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // 公開して実行するときだけ、止まる理由があるかを確認する。
  const check = useQuery({
    queryKey: ["workflow-publish-check", workflowId],
    queryFn: () => api<PublishCheck>(`/workflows/${workflowId}/publish-check`, { method: "POST", json: {} }),
    enabled: mode === "publish" && !readOnly && executionId === null,
  });

  const execution = useQuery({
    queryKey: ["workflow-execution", executionId],
    queryFn: () => api<ExecutionDetail>(`/workflow-executions/${executionId}`),
    enabled: executionId !== null,
    refetchInterval: (query) => (query.state.data && !RUNNING.includes(query.state.data.status) ? false : 1200),
  });
  const detail = execution.data;
  const running = detail === undefined || RUNNING.includes(detail.status);

  // 実行中のノード状態はキャンバスへ返し、別パネルを開かなくても進行が見えるようにする。
  useEffect(() => {
    if (!detail) return;
    onStatuses(Object.fromEntries(Object.entries(detail.context).map(([id, item]) => [id, item.status])));
    if (!RUNNING.includes(detail.status)) {
      void queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail, queryClient, workflowId]);

  const blocking = mode === "publish" ? check.data?.blocking ?? [] : [];
  const required = useMemo(
    () => inputs.filter((input) => input.required && !String(values[input.key] ?? "").trim()),
    [inputs, values],
  );

  const start = async () => {
    setError("");
    setBusy(true);
    try {
      if (!readOnly && dirty && !(await onSave())) return;
      const path = readOnly
        ? `/workflows/${workflowId}/run`
        : mode === "draft"
          ? `/workflows/${workflowId}/test`
          : `/workflows/${workflowId}/validate-publish-run`;
      const started = await api<{ execution_id: number }>(path, { method: "POST", json: { input: values } });
      setExecutionId(started.execution_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "実行を開始できませんでした");
    } finally {
      setBusy(false);
    }
  };

  return (
    <BottomSheet title={executionId === null ? "実行" : "実行結果"} onClose={onClose} sideOnDesktop>
      {executionId === null ? (
        <div className="space-y-4">
          {inputs.length > 0 && (
            <div className="space-y-3">
              {inputs.filter((input) => input.key).map((input) => (
                <RuntimeField
                  key={input.key}
                  input={input}
                  value={values[input.key]}
                  onChange={(next) => setValues((current) => ({ ...current, [input.key]: next }))}
                />
              ))}
            </div>
          )}

          {!readOnly && (
            <div>
              <div className="grid grid-cols-2 gap-1 rounded-2xl bg-zinc-100 p-1 dark:bg-zinc-800">
                {([
                  ["draft", "下書きで試す"],
                  ["publish", "公開して実行"],
                ] as const).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setMode(id)}
                    aria-pressed={mode === id}
                    className={`min-h-11 rounded-xl px-3 text-sm font-medium transition ${
                      mode === id ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 px-1 text-[11px] leading-relaxed text-zinc-500">
                {mode === "draft"
                  ? "いま編集中の内容で実行します。公開版は変わりません。"
                  : "保存した内容を新しいバージョンとして公開してから実行します。"}
              </p>
            </div>
          )}

          {blocking.length > 0 && (
            <div className="rounded-2xl border border-red-200 bg-red-50 p-3 text-xs leading-relaxed text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
              <p className="font-semibold">公開できない問題があります</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {blocking.slice(0, 5).map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}
          {error && (
            <p className="rounded-2xl border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">{error}</p>
          )}

          <button
            type="button"
            onClick={() => void start()}
            disabled={busy || required.length > 0 || blocking.length > 0}
            className="min-h-12 w-full rounded-2xl bg-accent-600 text-sm font-bold text-white hover:bg-accent-700 disabled:opacity-40"
          >
            {busy ? "開始しています…" : required.length > 0 ? `${required[0].label || required[0].key} を入力してください` : "実行する"}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${
              running
                ? "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"
                : detail?.status === "SUCCEEDED"
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                  : "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300"
            }`}>
              {running ? "実行中" : detail?.status === "SUCCEEDED" ? "成功" : "失敗"}
            </span>
            <span className="num text-[11px] text-zinc-400">#{executionId}</span>
          </div>

          {detail?.error && (
            <p className="rounded-2xl border border-red-200 bg-red-50 p-3 text-xs leading-relaxed text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
              {detail.error}
            </p>
          )}

          {!running && detail && Object.entries(detail.outputs).map(([name, output]) => (
            <section key={name} className="rounded-2xl border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="text-xs font-semibold">{output.title || name}</p>
              <RuntimeOutputView output={output} />
            </section>
          ))}
          {!running && detail && Object.keys(detail.outputs).length === 0 && (
            <p className="text-xs text-zinc-500">最終出力はありません。出力ノードを追加すると結果がここに出ます。</p>
          )}

          {detail && (
            <details className="rounded-2xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
              <summary className="cursor-pointer text-xs text-zinc-500">ノードごとの結果</summary>
              <div className="mt-2 space-y-1.5">
                {Object.entries(detail.context).map(([id, item]) => (
                  <div key={id} className="rounded-xl bg-zinc-50 p-2 text-[11px] dark:bg-zinc-900">
                    <div className="flex gap-2">
                      <strong className="min-w-0 flex-1 truncate">{item.name || id}</strong>
                      <span className="shrink-0 text-zinc-400">{item.status}</span>
                    </div>
                    {item.error && <p className="mt-1 text-red-500">{item.error}</p>}
                    {item.output !== undefined && (
                      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] text-zinc-500">{stringify(item.output)}</pre>
                    )}
                  </div>
                ))}
              </div>
            </details>
          )}

          <button
            type="button"
            onClick={() => { setExecutionId(null); setError(""); }}
            className="min-h-12 w-full rounded-2xl border border-zinc-200 text-sm font-semibold dark:border-zinc-700"
          >
            もう一度実行する
          </button>
        </div>
      )}
    </BottomSheet>
  );
}
