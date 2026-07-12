import { Link } from "react-router-dom";
import { useApps, useOverview } from "../api/hooks";
import { useMetrics } from "../stores";
import { formatBps, formatPercent, formatUptime } from "../lib/format";
import { Skeleton, Sparkline, StatusBadge } from "../components/ui";
import type { MetricsSnapshot } from "../types";

export default function DashboardPage() {
  const latest = useMetrics((s) => s.latest);
  const { data: overview, isLoading } = useOverview();
  const { data: apps } = useApps();

  const m: MetricsSnapshot | null =
    latest ??
    ((overview?.metrics && "cpu" in overview.metrics
      ? (overview.metrics as MetricsSnapshot)
      : null));

  const running = apps?.filter((a) => a.runtime.status === "RUNNING") ?? [];
  const failed = apps?.filter((a) => a.runtime.status === "FAILED") ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-6">
      {/* サマリーメトリクス */}
      <section aria-label="システムサマリー">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricTile
            label="CPU"
            value={m ? formatPercent(m.cpu.percent) : null}
            sub={m?.cpu.temperature_c != null ? `${m.cpu.temperature_c.toFixed(0)}°C` : undefined}
            percent={m?.cpu.percent ?? null}
          />
          <MetricTile
            label="RAM"
            value={m ? formatPercent(m.memory.percent) : null}
            sub={m ? `${(m.memory.used / 1024 ** 3).toFixed(1)} / ${(m.memory.total / 1024 ** 3).toFixed(0)} GB` : undefined}
            percent={m?.memory.percent ?? null}
          />
          <MetricTile
            label="GPU"
            value={m?.gpu ? formatPercent(m.gpu.utilization_percent) : "N/A"}
            sub={m?.gpu?.temperature_c != null ? `${m.gpu.temperature_c.toFixed(0)}°C` : undefined}
            percent={m?.gpu?.utilization_percent ?? null}
          />
          <MetricTile
            label="VRAM"
            value={
              m?.gpu?.vram_used_bytes != null && m.gpu.vram_total_bytes
                ? formatPercent((m.gpu.vram_used_bytes / m.gpu.vram_total_bytes) * 100)
                : "N/A"
            }
            sub={
              m?.gpu?.vram_used_bytes != null && m.gpu.vram_total_bytes
                ? `${(m.gpu.vram_used_bytes / 1024 ** 3).toFixed(1)} / ${(m.gpu.vram_total_bytes / 1024 ** 3).toFixed(0)} GB`
                : undefined
            }
            percent={
              m?.gpu?.vram_used_bytes != null && m.gpu.vram_total_bytes
                ? (m.gpu.vram_used_bytes / m.gpu.vram_total_bytes) * 100
                : null
            }
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 px-1 text-xs text-zinc-500">
          {isLoading && !m ? (
            <Skeleton className="h-4 w-64" />
          ) : (
            <>
              <span>
                稼働 <span className="num font-medium text-zinc-700 dark:text-zinc-300">{formatUptime(m?.uptime_seconds ?? overview?.host.uptime_seconds)}</span>
              </span>
              <span>
                実行中アプリ <span className="num font-medium text-zinc-700 dark:text-zinc-300">{running.length}</span>
              </span>
              {m?.power.total_watts_estimated != null && (
                <span>
                  推定電力 <span className="num font-medium text-zinc-700 dark:text-zinc-300">{m.power.total_watts_estimated.toFixed(0)} W</span>
                </span>
              )}
              <span>
                ↓ <span className="num">{formatBps(m?.io.net_rx_bps)}</span> ↑ <span className="num">{formatBps(m?.io.net_tx_bps)}</span>
              </span>
            </>
          )}
        </div>
      </section>

      {/* CPU / RAM スパークライン */}
      <ChartSection />

      {/* 異常アプリ */}
      {failed.length > 0 && (
        <section className="rounded-2xl border border-red-200 bg-red-50/60 p-4 dark:border-red-900 dark:bg-red-950/30">
          <h2 className="mb-2 text-sm font-semibold text-red-700 dark:text-red-400">
            失敗したアプリ
          </h2>
          <ul className="space-y-1">
            {failed.map((a) => (
              <li key={a.id}>
                <Link
                  to={`/logs?app=${a.id}&stream=stderr`}
                  className="text-sm text-red-700 underline-offset-2 hover:underline dark:text-red-400"
                >
                  {a.name} — ログを確認
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 実行中アプリ */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-500">実行中のアプリ</h2>
          <Link
            to="/apps"
            className="text-xs font-medium text-accent-600 hover:underline dark:text-accent-400"
          >
            すべて表示
          </Link>
        </div>
        {running.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-400 dark:border-zinc-700">
            実行中のアプリはありません
          </p>
        ) : (
          <ul className="divide-y divide-zinc-100 overflow-hidden rounded-2xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
            {running.slice(0, 5).map((a) => (
              <li key={a.id} className="flex items-center gap-3 bg-white px-4 py-3 dark:bg-zinc-900">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-100 text-sm font-semibold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  {a.name[0]}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{a.name}</p>
                  <p className="num text-xs text-zinc-400">
                    {formatUptime(a.runtime.uptime_seconds)}
                    {a.runtime.cpu_percent != null && ` · CPU ${a.runtime.cpu_percent.toFixed(0)}%`}
                  </p>
                </div>
                <StatusBadge status={a.runtime.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function MetricTile({
  label,
  value,
  sub,
  percent,
}: {
  label: string;
  value: string | null;
  sub?: string;
  percent: number | null;
}) {
  const barColor =
    percent == null
      ? ""
      : percent >= 90
        ? "bg-red-500"
        : percent >= 70
          ? "bg-amber-500"
          : "bg-accent-500";
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="text-xs font-medium text-zinc-400">{label}</p>
      {value === null ? (
        <Skeleton className="mt-1 h-7 w-16" />
      ) : (
        <p className="num mt-0.5 text-2xl font-semibold tracking-tight">{value}</p>
      )}
      <p className="num mt-0.5 h-4 truncate text-xs text-zinc-400">{sub ?? ""}</p>
      <div className="mt-2 h-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
        {percent != null && (
          <div
            className={`h-full rounded-full transition-[width] duration-300 ${barColor}`}
            style={{ width: `${Math.min(100, percent)}%` }}
          />
        )}
      </div>
    </div>
  );
}

function ChartSection() {
  const history = useMetrics((s) => s.history);
  if (history.length < 2) return null;
  const cpu = history.map((h) => h.cpu.percent);
  const ram = history.map((h) => h.memory.percent);
  const gpu = history.map((h) => h.gpu?.utilization_percent ?? null);
  const hasGpu = gpu.some((v) => v != null);
  return (
    <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <SparkCard label="CPU" values={cpu} />
      <SparkCard label="RAM" values={ram} />
      {hasGpu && <SparkCard label="GPU" values={gpu} />}
    </section>
  );
}

function SparkCard({ label, values }: { label: string; values: (number | null)[] }) {
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs font-medium text-zinc-400">{label}</span>
        <span className="num text-sm font-semibold">
          {values[values.length - 1]?.toFixed(0) ?? "—"}%
        </span>
      </div>
      <Sparkline values={values} className="text-accent-500" />
    </div>
  );
}
