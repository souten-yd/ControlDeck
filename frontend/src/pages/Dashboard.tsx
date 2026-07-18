import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useApps, useOverview } from "../api/hooks";
import { useAuth, useMetrics } from "../stores";
import { formatBps, formatPercent, formatUptime } from "../lib/format";
import { Skeleton, Sparkline, StatusBadge } from "../components/ui";
import type { MetricsSnapshot } from "../types";

export default function DashboardPage() {
  const latest = useMetrics((s) => s.latest);
  const history = useMetrics((s) => s.history);
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
            percent={m?.cpu.percent ?? null}
            values={history.map((h) => h.cpu.percent)}
            temp={m?.cpu.temperature_c != null ? `${m.cpu.temperature_c.toFixed(0)}°C` : undefined}
            fan={m?.cpu.fan_rpm != null ? `${m.cpu.fan_rpm}` : undefined}
          />
          <MetricTile
            label="RAM"
            value={m ? formatPercent(m.memory.percent) : null}
            percent={m?.memory.percent ?? null}
            values={history.map((h) => h.memory.percent)}
            sub={m ? `${(m.memory.used / 1024 ** 3).toFixed(1)} / ${(m.memory.total / 1024 ** 3).toFixed(0)} GB` : undefined}
          />
          <MetricTile
            label="GPU"
            value={m?.gpu ? formatPercent(m.gpu.utilization_percent) : "N/A"}
            percent={m?.gpu?.utilization_percent ?? null}
            values={history.map((h) => h.gpu?.utilization_percent ?? null)}
            temp={m?.gpu?.temperature_c != null ? `${m.gpu.temperature_c.toFixed(0)}°C` : undefined}
            fan={m?.gpu?.fan_rpm != null ? `${m.gpu.fan_rpm}` : undefined}
          />
          <MetricTile
            label="VRAM"
            value={
              m?.gpu?.vram_used_bytes != null && m.gpu.vram_total_bytes
                ? formatPercent((m.gpu.vram_used_bytes / m.gpu.vram_total_bytes) * 100)
                : "N/A"
            }
            percent={
              m?.gpu?.vram_used_bytes != null && m.gpu.vram_total_bytes
                ? (m.gpu.vram_used_bytes / m.gpu.vram_total_bytes) * 100
                : null
            }
            values={history.map((h) =>
              h.gpu?.vram_used_bytes != null && h.gpu.vram_total_bytes
                ? (h.gpu.vram_used_bytes / h.gpu.vram_total_bytes) * 100
                : null,
            )}
            sub={
              m?.gpu?.vram_used_bytes != null && m.gpu.vram_total_bytes
                ? `${(m.gpu.vram_used_bytes / 1024 ** 3).toFixed(1)} / ${(m.gpu.vram_total_bytes / 1024 ** 3).toFixed(0)} GB`
                : undefined
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
              {m?.power.available !== true && m?.power.total_watts_estimated != null && (
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

      {/* PSU 総出力 + 起動中/今日/今月の電気代 */}
      {m && <PowerCard power={m.power} />}

      {/* アクティブアラート */}
      <ActiveAlerts />

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

interface AlertEvent {
  id: number;
  rule_name: string;
  message: string;
  status: string;
  triggered_at: string;
}

// 電気代表示のフォーマット（約○円・kWh）
function fmtYen(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v < 100) return `約${v.toFixed(2)}円`;
  return `約${v.toFixed(1)}円`;
}
function fmtKwh(v: number | null | undefined): string {
  if (v == null) return "—";
  return v < 0.001 ? `${v.toFixed(6)} kWh` : `${v.toFixed(3)} kWh`;
}

function PowerCard({ power }: { power: MetricsSnapshot["power"] }) {
  const psuOk = power.available === true && power.output_power_w != null;
  const tip =
    "HX1500i が計測した DC 出力電力を、設定された PSU 効率でコンセント入力電力へ換算し、" +
    `${power.price_per_kwh_yen}円/kWh で積算した概算です。コンセントのワットチェッカー実測値とは差が出る場合があります。`;
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900" title={tip}>
      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        {/* PSU 総出力 */}
        <div className="min-w-[140px]">
          <p className="text-xs text-zinc-500">PSU 総出力{power.source ? "" : ""}</p>
          {psuOk ? (
            <>
              <p className="num mt-0.5 text-2xl font-semibold text-zinc-800 dark:text-zinc-100">
                {power.output_power_w!.toFixed(0)} <span className="text-base font-normal text-zinc-400">W</span>
              </p>
              <p className="num text-[11px] text-zinc-400">
                コンセント側推定 約{power.estimated_input_power_w != null ? power.estimated_input_power_w.toFixed(0) : "—"} W
              </p>
            </>
          ) : (
            <p className="mt-0.5 text-lg font-medium text-zinc-400">取得不可</p>
          )}
          {psuOk && (power.vrm_temperature_c != null || power.fan_rpm != null) && (
            <p className="num mt-1 text-[10px] text-zinc-400">
              {power.vrm_temperature_c != null && `VRM ${power.vrm_temperature_c}°C`}
              {power.case_temperature_c != null && ` · ケース ${power.case_temperature_c}°C`}
              {power.fan_rpm != null && ` · FAN ${power.fan_rpm} RPM`}
            </p>
          )}
        </div>

        {/* 電気代（起動中/今日/今月） */}
        <div className="flex flex-1 flex-wrap gap-x-6 gap-y-3">
          <CostItem label="今回の起動中" cost={power.session_cost_yen} kwh={power.session_energy_kwh} ok={psuOk} />
          <CostItem label="今日" cost={power.today_cost_yen} kwh={power.today_energy_kwh} ok={psuOk} />
          <CostItem label="今月" cost={power.month_cost_yen} kwh={power.month_energy_kwh} ok={psuOk} />
        </div>
      </div>
      <p className="num mt-3 text-[10px] text-zinc-400">
        {power.price_per_kwh_yen}円/kWh・効率{Math.round(power.psu_efficiency * 100)}%（概算。実測値とは差が出る場合があります）
      </p>
    </section>
  );
}

function CostItem({ label, cost, kwh, ok }: { label: string; cost: number | null; kwh: number | null; ok: boolean }) {
  return (
    <div className="min-w-[92px]">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="num mt-0.5 text-lg font-semibold text-zinc-800 dark:text-zinc-100">{ok ? fmtYen(cost) : "概算不可"}</p>
      {ok && <p className="num text-[10px] text-zinc-400">{fmtKwh(kwh)}</p>}
    </div>
  );
}

function ActiveAlerts() {
  const qc = useQueryClient();
  const canManage = useAuth((s) => s.can)("settings.manage");
  const { data: alerts } = useQuery({
    queryKey: ["alert-events", "active"],
    queryFn: () => api<AlertEvent[]>("/alert-events?active_only=true&limit=10"),
    refetchInterval: 15_000,
  });
  const dismiss = useMutation({
    mutationFn: (eventId?: number) =>
      api(`/alert-events/dismiss${eventId ? `?event_id=${eventId}` : ""}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-events", "active"] }),
  });
  if (!alerts || alerts.length === 0) return null;
  return (
    <section className="rounded-2xl border border-red-200 bg-red-50/60 p-4 dark:border-red-900 dark:bg-red-950/30">
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-red-700 dark:text-red-400">
        <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
        アクティブなアラート（{alerts.length}）
        {canManage && (
          <button
            onClick={() => dismiss.mutate(undefined)}
            className="ml-auto rounded-lg px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-100 dark:text-red-400 dark:hover:bg-red-900/40"
          >
            すべて解除
          </button>
        )}
      </h2>
      <ul className="space-y-1">
        {alerts.map((a) => (
          <li key={a.id} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0">
              <span className="font-medium text-red-700 dark:text-red-400">{a.rule_name}</span>
              <span className="ml-2 num text-xs text-red-600/80 dark:text-red-400/80">{a.message}</span>
            </span>
            {canManage && (
              <button
                onClick={() => dismiss.mutate(a.id)}
                className="shrink-0 text-xs text-red-600 hover:underline dark:text-red-400"
              >
                解除
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 統合メトリクスカード: 使用率 + 変化スパークライン + 温度/FAN を1枚に集約。 */
function MetricTile({
  label,
  value,
  percent,
  values,
  sub,
  temp,
  fan,
}: {
  label: string;
  value: string | null;
  percent: number | null;
  values: (number | null)[];
  sub?: string;
  temp?: string;
  fan?: string;
}) {
  const tone =
    percent == null
      ? "text-zinc-300 dark:text-zinc-600"
      : percent >= 90
        ? "text-red-500"
        : percent >= 70
          ? "text-amber-500"
          : "text-accent-500";
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-xs font-medium text-zinc-400">{label}</p>
        {value === null ? (
          <Skeleton className="h-7 w-14" />
        ) : (
          <p className="num text-2xl font-semibold tracking-tight">{value}</p>
        )}
      </div>
      <div className="mt-2">
        {values.filter((v) => v != null).length >= 2 ? (
          <Sparkline values={values} fill className={tone} />
        ) : (
          <div className="h-7 rounded-md bg-zinc-50 dark:bg-zinc-800/40" />
        )}
      </div>
      <p className="num mt-1.5 flex h-4 items-center gap-2 overflow-hidden text-[11px] text-zinc-400">
        {sub && <span className="truncate">{sub}</span>}
        {temp && <span className="shrink-0">{temp}</span>}
        {fan && (
          <span className="flex shrink-0 items-center gap-1 font-medium text-sky-500 dark:text-sky-400">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" width="0.85em" height="0.85em" aria-hidden>
              <circle cx="12" cy="12" r="2.2" />
              <path d="M12 9.8c0-3.2 1.6-5 3.4-5 1.5 0 2.4 1.2 2.4 2.4 0 1.9-2.6 2.6-5.8 2.6zM12 14.2c0 3.2-1.6 5-3.4 5-1.5 0-2.4-1.2-2.4-2.4 0-1.9 2.6-2.6 5.8-2.6zM9.8 12c-3.2 0-5-1.6-5-3.4 0-1.5 1.2-2.4 2.4-2.4 1.9 0 2.6 2.6 2.6 5.8zM14.2 12c3.2 0 5 1.6 5 3.4 0 1.5-1.2 2.4-2.4 2.4-1.9 0-2.6-2.6-2.6-5.8z" />
            </svg>
            {fan} <span className="font-normal opacity-70">RPM</span>
          </span>
        )}
      </p>
    </div>
  );
}
