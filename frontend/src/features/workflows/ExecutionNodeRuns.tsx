import { useEffect, useMemo, useState, type ReactNode } from "react";

export interface ExecutionNodeRun {
  id: number;
  node_id: string;
  node_type: string;
  status: string;
  resolved_inputs: unknown;
  outputs: unknown;
  error: { message?: string; [key: string]: unknown };
  logs: unknown[];
  artifacts: unknown[];
  token_usage: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  elapsed_ms: number | null;
  attempt: number;
  retry_count: number;
  input_size: number;
  output_size: number;
  cache_source: string;
}

function json(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function tokenTotal(usage: Record<string, unknown>): number {
  for (const key of ["total_tokens", "totalTokens", "tokens"]) {
    if (typeof usage[key] === "number") return Number(usage[key]);
  }
  return 0;
}

function DataBlock({ value, empty }: { value: unknown; empty: string }) {
  const isEmpty = value == null
    || (Array.isArray(value) && value.length === 0)
    || (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0);
  return isEmpty
    ? <p className="text-[11px] text-zinc-400">{empty}</p>
    : <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-2 font-mono text-[10px] dark:bg-zinc-950">{json(value)}</pre>;
}

export function ExecutionNodeRuns({
  runs,
  statusClass,
}: {
  runs: ExecutionNodeRun[];
  statusClass: Record<string, string>;
}) {
  const [selectedId, setSelectedId] = useState<number | null>(runs[0]?.id ?? null);
  useEffect(() => {
    if (selectedId === null || !runs.some((run) => run.id === selectedId)) setSelectedId(runs[0]?.id ?? null);
  }, [runs, selectedId]);
  const selected = runs.find((run) => run.id === selectedId) ?? null;
  const timeline = useMemo(() => {
    const points = runs.flatMap((run) => [Date.parse(run.started_at ?? ""), Date.parse(run.finished_at ?? run.started_at ?? "")]).filter(Number.isFinite);
    const start = points.length ? Math.min(...points) : 0;
    const end = points.length ? Math.max(...points) : start + 1;
    const span = Math.max(1, end - start);
    return { start, span };
  }, [runs]);
  const totalTokens = runs.reduce((sum, run) => sum + tokenTotal(run.token_usage), 0);
  const bottleneck = runs.reduce<ExecutionNodeRun | null>(
    (slowest, run) => (run.elapsed_ms ?? -1) > (slowest?.elapsed_ms ?? -1) ? run : slowest,
    null,
  );

  if (runs.length === 0) return <p className="rounded-xl border border-dashed border-zinc-300 p-3 text-xs text-zinc-400 dark:border-zinc-700">ノード実行記録はまだありません。</p>;
  return (
    <section aria-label="ノード実行の観測" className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <Summary label="ノード" value={String(runs.length)} />
        <Summary label="token" value={totalTokens ? totalTokens.toLocaleString() : "—"} />
        <Summary label="最長" value={bottleneck?.elapsed_ms != null ? `${bottleneck.elapsed_ms}ms` : "—"} />
      </div>

      <details className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <summary className="cursor-pointer text-xs font-medium">実行タイムライン</summary>
        <div className="mt-3 space-y-2">
          {runs.map((run) => {
            const started = Date.parse(run.started_at ?? "");
            const finished = Date.parse(run.finished_at ?? run.started_at ?? "");
            const left = Number.isFinite(started) ? Math.max(0, ((started - timeline.start) / timeline.span) * 100) : 0;
            const width = Number.isFinite(finished) && Number.isFinite(started)
              ? Math.max(2, ((Math.max(finished, started) - started) / timeline.span) * 100) : 2;
            return <div key={run.id} className="grid grid-cols-[5rem_1fr] items-center gap-2 text-[10px]"><code className="truncate">{run.node_id}</code><div className="relative h-3 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><span title={`${run.elapsed_ms ?? 0}ms`} className={`absolute top-0 h-full rounded-full ${run.status === "FAILED" ? "bg-red-500" : run.status === "SUCCEEDED" ? "bg-emerald-500" : "bg-accent-500"}`} style={{ left: `${Math.min(left, 98)}%`, width: `${Math.min(width, 100 - Math.min(left, 98))}%` }} /></div></div>;
          })}
        </div>
      </details>

      <ul className="space-y-1.5" aria-label="ノード実行一覧">
        {runs.map((run) => (
          <li key={run.id}>
            <button type="button" aria-label={`${run.node_id} ${run.status}`} onClick={() => setSelectedId(run.id)} aria-pressed={selectedId === run.id} className={`min-h-11 w-full rounded-xl border p-2.5 text-left ${selectedId === run.id ? "border-accent-500 bg-accent-50/50 dark:bg-accent-950/20" : "border-zinc-200 dark:border-zinc-800"}`}>
              <span className="flex min-w-0 items-center gap-2 text-xs"><code className="min-w-0 flex-1 truncate font-mono">{run.node_id}</code><span className={statusClass[run.status] ?? "text-zinc-400"}>{run.status}</span></span>
              <span className="mt-1 block truncate text-[10px] text-zinc-400">{run.node_type || "unknown"}{run.elapsed_ms !== null ? ` · ${run.elapsed_ms}ms` : ""}{run.retry_count ? ` · retry ${run.retry_count}` : ""}{run.cache_source ? ` · cache ${run.cache_source}` : ""} · in {bytes(run.input_size)} / out {bytes(run.output_size)}</span>
            </button>
          </li>
        ))}
      </ul>

      {selected && (
        <article aria-label={`ノード ${selected.node_id} の詳細`} className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="flex min-w-0 items-center gap-2"><strong className="min-w-0 flex-1 truncate text-xs">{selected.node_id}</strong><span className="text-[10px] text-zinc-400">attempt {selected.attempt || 1}</span></div>
          <Inspect title="実入力"><DataBlock value={selected.resolved_inputs} empty="実入力の記録はありません" /></Inspect>
          <Inspect title="実出力"><DataBlock value={selected.outputs} empty="実出力の記録はありません" /></Inspect>
          <Inspect title="ログ"><DataBlock value={selected.logs} empty="このノードが保存したログはありません" /></Inspect>
          <Inspect title="エラー" open={selected.status === "FAILED" || selected.status === "TIMED_OUT"}><DataBlock value={selected.error} empty="エラーはありません" /></Inspect>
          <Inspect title="token"><DataBlock value={selected.token_usage} empty="token使用量の記録はありません" /></Inspect>
          <Inspect title="アーティファクト"><DataBlock value={selected.artifacts} empty="アーティファクト参照はありません" /></Inspect>
        </article>
      )}
    </section>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 rounded-xl bg-zinc-50 p-2 text-center dark:bg-zinc-950"><p className="truncate text-[9px] text-zinc-400">{label}</p><p className="num truncate text-xs font-semibold">{value}</p></div>;
}

function Inspect({ title, open = false, children }: { title: string; open?: boolean; children: ReactNode }) {
  return <details open={open} className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-800"><summary className="cursor-pointer text-[11px] font-medium">{title}</summary><div className="mt-2">{children}</div></details>;
}
