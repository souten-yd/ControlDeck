import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useApps, useOverview } from "../api/hooks";
import { useAuth, useMetrics } from "../stores";
import { formatBps, formatPercent, formatUptime } from "../lib/format";
import { Skeleton, Sparkline, StatusBadge } from "../components/ui";
import type { MetricsSnapshot } from "../types";
import { PageHeader } from "../components/PageHeader";

interface HistorySample {
  timestamp: string;
  cpu_percent: number | null;
  memory_percent: number | null;
  gpu_percent: number | null;
  vram_percent: number | null;
}

interface HistoryResponse {
  resolution: "raw" | "minute" | "hour";
  samples: HistorySample[];
}

const HISTORY_RANGES = [
  [15, "15分"],
  [60, "1時間"],
  [360, "6時間"],
  [1440, "24時間"],
  [10080, "7日"],
  [43200, "30日"],
  [129600, "90日"],
  [525600, "1年"],
] as const;

export default function DashboardPage() {
  const latest = useMetrics((s) => s.latest);
  const history = useMetrics((s) => s.history);
  const [historyMinutes, setHistoryMinutes] = useState(15);
  const [rangeChoice, setRangeChoice] = useState("15");
  const [customMinutes, setCustomMinutes] = useState("15");
  const can = useAuth((s) => s.can);
  const { data: overview, isLoading } = useOverview();
  const { data: apps } = useApps();
  // グラフはサーバー側保持の履歴でシードし、ブラウザを閉じていた間も空白にしない
  const { data: seeded, isFetching: historyFetching } = useQuery({
    queryKey: ["metrics-history", historyMinutes],
    queryFn: () => api<HistoryResponse>(`/system/metrics/history?minutes=${historyMinutes}`),
    enabled: can("system.view"),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const firstLiveTs = history[0]?.timestamp ?? "";
  const seedSamples = (seeded?.samples ?? []).filter((s) => !firstLiveTs || s.timestamp < firstLiveTs);
  const cpuValues = [...seedSamples.map((s) => s.cpu_percent), ...history.map((h) => h.cpu.percent)];
  const ramValues = [...seedSamples.map((s) => s.memory_percent), ...history.map((h) => h.memory.percent)];
  const gpuValues = [...seedSamples.map((s) => s.gpu_percent), ...history.map((h) => h.gpu?.utilization_percent ?? null)];
  const vramValues = [
    ...seedSamples.map((s) => s.vram_percent),
    ...history.map((h) =>
      h.gpu?.vram_used_bytes != null && h.gpu.vram_total_bytes
        ? (h.gpu.vram_used_bytes / h.gpu.vram_total_bytes) * 100
        : null,
    ),
  ];

  const m: MetricsSnapshot | null =
    latest ??
    ((overview?.metrics && "cpu" in overview.metrics
      ? (overview.metrics as MetricsSnapshot)
      : null));

  const running = apps?.filter((a) => a.runtime.status === "RUNNING") ?? [];
  const failed = apps?.filter((a) => a.runtime.status === "FAILED") ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-6">
      <PageHeader title="Home" description="PCとControlDeckの現在の状態をまとめて確認します。" className="mb-0" />
      {/* サマリーメトリクス */}
      <section aria-label="システムサマリー">
        <div className="mb-2 flex min-h-11 flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <label htmlFor="metrics-range" className="text-xs font-medium text-zinc-500">履歴期間</label>
            <select
              id="metrics-range"
              value={rangeChoice}
              onChange={(event) => {
                const choice = event.target.value;
                setRangeChoice(choice);
                if (choice !== "custom") setHistoryMinutes(Number(choice));
              }}
              className="h-11 rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            >
              {HISTORY_RANGES.map(([minutes, label]) => <option key={minutes} value={minutes}>{label}</option>)}
              <option value="custom">任意…</option>
            </select>
          </div>
          {rangeChoice === "custom" ? (
            <form
              className="flex items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                const minutes = Math.min(525600, Math.max(15, Number(customMinutes) || 15));
                setCustomMinutes(String(minutes));
                setHistoryMinutes(minutes);
              }}
            >
              <label htmlFor="metrics-custom-minutes" className="sr-only">任意の履歴期間（分）</label>
              <input
                id="metrics-custom-minutes"
                type="number"
                min={15}
                max={525600}
                value={customMinutes}
                onChange={(event) => setCustomMinutes(event.target.value)}
                className="h-11 w-28 rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              />
              <span className="text-xs text-zinc-500">分</span>
              <button type="submit" className="h-11 rounded-xl bg-accent-600 px-3 text-sm font-medium text-white hover:bg-accent-700">適用</button>
            </form>
          ) : (
            <span className="text-xs text-zinc-400" aria-live="polite">
              {historyFetching ? "履歴を取得中…" : seeded ? `${seeded.resolution === "raw" ? "生データ" : seeded.resolution === "minute" ? "1分平均" : "1時間平均"} · ${seeded.samples.length}点` : ""}
            </span>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricTile
            label="CPU"
            value={m ? formatPercent(m.cpu.percent) : null}
            percent={m?.cpu.percent ?? null}
            values={cpuValues}
            temp={m?.cpu.temperature_c != null ? `${m.cpu.temperature_c.toFixed(0)}°C` : undefined}
            fan={m?.cpu.fan_rpm != null ? `${m.cpu.fan_rpm}` : undefined}
          />
          <MetricTile
            label="RAM"
            value={m ? formatPercent(m.memory.percent) : null}
            percent={m?.memory.percent ?? null}
            values={ramValues}
            sub={m ? `${(m.memory.used / 1024 ** 3).toFixed(1)} / ${(m.memory.total / 1024 ** 3).toFixed(0)} GB` : undefined}
          />
          <MetricTile
            label="GPU"
            value={m?.gpu ? formatPercent(m.gpu.utilization_percent) : "N/A"}
            percent={m?.gpu?.utilization_percent ?? null}
            values={gpuValues}
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
            values={vramValues}
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
      {/* PSU出力と電気代を同じ高さのstatとして横一列に並べ、空欄を作らない */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <div>
          <p className="text-xs text-zinc-500">PSU 総出力</p>
          {psuOk ? (
            <>
              <p className="num mt-0.5 text-2xl font-semibold tracking-tight text-zinc-800 dark:text-zinc-100">
                {power.output_power_w!.toFixed(0)} <span className="text-base font-normal text-zinc-400">W</span>
              </p>
              <p className="num text-[10px] text-zinc-400">
                コンセント側 約{power.estimated_input_power_w != null ? power.estimated_input_power_w.toFixed(0) : "—"} W
              </p>
            </>
          ) : (
            <p className="mt-0.5 text-lg font-medium text-zinc-400">取得不可</p>
          )}
        </div>
        <CostItem label="今回の起動中" cost={power.session_cost_yen} kwh={power.session_energy_kwh} ok={psuOk} />
        <CostItem label="今日" cost={power.today_cost_yen} kwh={power.today_energy_kwh} ok={psuOk} />
        <CostItem label="今月" cost={power.month_cost_yen} kwh={power.month_energy_kwh} ok={psuOk} />
      </div>
      {/* 温度/FAN と単価情報は下端の1行に集約（FANはスカイブルーで区別） */}
      <div className="num mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-zinc-100 pt-2.5 text-[10px] text-zinc-400 dark:border-zinc-800">
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {psuOk && power.vrm_temperature_c != null && <span>VRM {power.vrm_temperature_c}°C</span>}
          {psuOk && power.case_temperature_c != null && <span>ケース {power.case_temperature_c}°C</span>}
          {psuOk && power.fan_rpm != null && (
            <span className="font-medium text-sky-500 dark:text-sky-400">FAN {power.fan_rpm} RPM</span>
          )}
        </span>
        <span>{power.price_per_kwh_yen}円/kWh・効率{Math.round(power.psu_efficiency * 100)}%（概算）</span>
      </div>
    </section>
  );
}

function CostItem({ label, cost, kwh, ok }: { label: string; cost: number | null; kwh: number | null; ok: boolean }) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="num mt-0.5 text-xl font-semibold tracking-tight text-zinc-800 dark:text-zinc-100">{ok ? fmtYen(cost) : "概算不可"}</p>
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
