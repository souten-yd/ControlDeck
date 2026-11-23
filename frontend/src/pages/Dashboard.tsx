import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useApps, useOverview } from "../api/hooks";
import { METRICS_GAP_MS, useAuth, useMetrics } from "../stores";
import { formatBps, formatPercent, formatUptime } from "../lib/format";
import { Skeleton, Sparkline, StatusBadge } from "../components/ui";
import type { MetricsSnapshot } from "../types";
import { PageHeader } from "../components/PageHeader";
import { AgentToolWidget } from "../features/addons/AgentToolWidget";
import { PendingApprovals } from "../features/approvals/PendingApprovals";
import { LlmHomeCard } from "../features/models/LlmHomeCard";
import { NewsHomeCard } from "../features/news/NewsHomeCard";
import { HomeNoticeLine } from "../features/alerts/HomeNoticeLine";
import { readHistoryMinutes } from "../lib/homeHistory";

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

export default function DashboardPage() {
  const latest = useMetrics((s) => s.latest);
  const history = useMetrics((s) => s.history);
  // 履歴の長さは 設定 → 表示 で決める（Home には選択の行を置かない）
  const [historyMinutes] = useState(readHistoryMinutes);
  const can = useAuth((s) => s.can);
  const { data: overview, isLoading } = useOverview();
  const { data: apps } = useApps();
  // グラフはサーバー側保持の履歴でシードし、ブラウザを閉じていた間も空白にしない
  // 閉じていた間（受け取りが途切れた間）は、途切れるたびに読み直して埋める（stores の gapEpoch）
  const gapEpoch = useMetrics((s) => s.gapEpoch);
  const { data: seeded, isFetching: historyFetching, refetch: refetchHistory } = useQuery({
    queryKey: ["metrics-history", historyMinutes, gapEpoch],
    placeholderData: keepPreviousData,
    queryFn: () => api<HistoryResponse>(`/system/metrics/history?minutes=${historyMinutes}`),
    enabled: can("system.view"),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const firstLiveTs = history[0]?.timestamp ?? "";
  // Home を離れていた間も受け取りは続くが、手元に残るのは直近 120 点（4 分）だけ。前に読んだ履歴の終わりと
  // 手元の最初の点の間が空いていたら読み直す（同じ理由で何度も読まないよう 30 秒に 1 回まで）
  const lastSeedTs = seeded?.resolution === "raw" ? seeded.samples[seeded.samples.length - 1]?.timestamp ?? "" : "";
  const refetchedAt = useRef(0);
  useEffect(() => {
    if (!lastSeedTs || !firstLiveTs || historyFetching) return;
    if (Date.parse(firstLiveTs) - Date.parse(lastSeedTs) <= METRICS_GAP_MS) return;
    if (Date.now() - refetchedAt.current < 30_000) return;
    refetchedAt.current = Date.now();
    void refetchHistory();
  }, [lastSeedTs, firstLiveTs, historyFetching, refetchHistory]);
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

  const vramNow =
    m?.gpu?.vram_used_bytes != null && m.gpu.vram_total_bytes
      ? (m.gpu.vram_used_bytes / m.gpu.vram_total_bytes) * 100
      : null;

  const running = apps?.filter((a) => a.runtime.status === "RUNNING") ?? [];
  const failed = apps?.filter((a) => a.runtime.status === "FAILED") ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-2.5 p-3 md:space-y-4 md:p-6">
      {/* 題名は殻の上の行（☰ の右）に出る。ページの中には場所を取らない。 */}
      <PageHeader title="Home" className="mb-0" />
      <HomeNoticeLine />
      <PendingApprovals />
      {/* サマリーメトリクス */}
      <section aria-label="システムサマリー">
        {/* モバイル（768px 未満）: CPU+RAM、GPU+VRAM を 1 枚に 2 本の線で出し、横 1 段にする（利用者 2026-11-23） */}
        <div className="grid grid-cols-2 gap-2 md:hidden" data-testid="metrics-dual">
          <DualTile
            a={{ label: "CPU", percent: m?.cpu.percent ?? null, values: cpuValues, tone: "text-accent-500", dot: "bg-accent-500" }}
            b={{ label: "RAM", percent: m?.memory.percent ?? null, values: ramValues, tone: "text-violet-500", dot: "bg-violet-500" }}
            loading={!m}
            notes={m ? [
              m.cpu.temperature_c != null ? `${m.cpu.temperature_c.toFixed(0)}°C` : "",
              m.cpu.fan_rpm != null ? `✻${m.cpu.fan_rpm}` : "",
              `${(m.memory.used / 1024 ** 3).toFixed(0)}/${(m.memory.total / 1024 ** 3).toFixed(0)}GB`,
            ] : []}
          />
          <DualTile
            a={{ label: "GPU", percent: m?.gpu?.utilization_percent ?? null, values: gpuValues, tone: "text-accent-500", dot: "bg-accent-500" }}
            b={{ label: "VRAM", percent: vramNow, values: vramValues, tone: "text-violet-500", dot: "bg-violet-500" }}
            loading={!m}
            notes={m?.gpu ? [
              m.gpu.temperature_c != null ? `${m.gpu.temperature_c.toFixed(0)}°C` : "",
              m.gpu.fan_rpm != null ? `✻${m.gpu.fan_rpm}` : "",
              m.gpu.vram_used_bytes != null && m.gpu.vram_total_bytes
                ? `${(m.gpu.vram_used_bytes / 1024 ** 3).toFixed(0)}/${(m.gpu.vram_total_bytes / 1024 ** 3).toFixed(0)}GB` : "",
            ] : ["GPU なし"]}
          />
        </div>
        <div className="hidden grid-cols-4 gap-3 md:grid">
          <MetricTile
            label="CPU"
            value={m ? formatPercent(m.cpu.percent) : null}
            percent={m?.cpu.percent ?? null}
            values={cpuValues}
            temp={m?.cpu.temperature_c != null ? `${m.cpu.temperature_c.toFixed(0)}°C` : undefined}
            fan={m?.cpu.fan_rpm != null ? `${m.cpu.fan_rpm}` : undefined}
            fanPercent={m?.cpu.fan_percent ?? undefined}
          />
          <MetricTile
            label="RAM"
            value={m ? formatPercent(m.memory.percent) : null}
            percent={m?.memory.percent ?? null}
            values={ramValues}
            sub={m ? `${(m.memory.used / 1024 ** 3).toFixed(1)} / ${(m.memory.total / 1024 ** 3).toFixed(0)} GB${
              m.memory.mapped_files && m.memory.mapped_files > 4 * 1024 ** 3 ? `（うちモデル ${(m.memory.mapped_files / 1024 ** 3).toFixed(0)}）` : ""}` : undefined}
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
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 px-1 text-[11px] text-zinc-500 md:mt-3 md:gap-x-5 md:gap-y-1 md:text-xs">
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


      {/* Add-on ツール（MCP）の利用状況。MediaForge や SonicForge は agent から
          呼ばれると GPU も時間も使うが、呼び出しは agent の中で完結するので、
          Home からは何も起きていないように見えていた。一度も呼ばれていなければ
          widget 側で出さない。 */}
      {can("workflows.run") && <AgentToolWidget />}

      {/* LLM の利用状況（ローカル + OpenRouter）。Models 画面へ行かなくても、
          いま動いているもの・速さ・使った額が分かるようにする。どちらも無ければ出さない。 */}
      <LlmHomeCard />

      {/* ニュース（おすすめの上位 5 件）。専門のページは /news */}
      <NewsHomeCard />

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
    <section className="rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900 md:p-4" title={tip}>
      {/* PSU出力と電気代を同じ高さのstatとして横一列に並べ、空欄を作らない */}
      <div className="grid grid-cols-4 gap-x-2 md:gap-x-6">
        <div>
          <p className="truncate text-[10px] text-zinc-500 md:text-xs">PSU 総出力</p>
          {psuOk ? (
            <>
              <p className="num whitespace-nowrap text-[15px] font-semibold tracking-tight text-zinc-800 dark:text-zinc-100 md:mt-0.5 md:text-2xl">
                {power.output_power_w!.toFixed(0)} <span className="text-[11px] font-normal text-zinc-400 md:text-base">W</span>
              </p>
              <p className="num truncate text-[10px] text-zinc-400">
                <span className="md:hidden">入力 約</span><span className="hidden md:inline">コンセント側 約</span>{power.estimated_input_power_w != null ? power.estimated_input_power_w.toFixed(0) : "—"} W
              </p>
            </>
          ) : (
            <p className="mt-0.5 text-sm font-medium text-zinc-400 md:text-lg">取得不可</p>
          )}
        </div>
        <CostItem label="今回の起動中" cost={power.session_cost_yen} kwh={power.session_energy_kwh} ok={psuOk} />
        <CostItem label="今日" cost={power.today_cost_yen} kwh={power.today_energy_kwh} ok={psuOk} />
        <CostItem label="今月" cost={power.month_cost_yen} kwh={power.month_energy_kwh} ok={psuOk} />
      </div>
      {/* 温度/FAN と単価情報は下端の1行に集約（FANはスカイブルーで区別） */}
      <div className="num mt-1.5 flex items-center gap-x-2 overflow-x-auto whitespace-nowrap border-t border-zinc-100 pt-1.5 text-[10px] text-zinc-400 dark:border-zinc-800 md:mt-3 md:pt-2.5">
        {psuOk && power.vrm_temperature_c != null && <span className="shrink-0" title="VRM温度">VRM {power.vrm_temperature_c}°</span>}
        {psuOk && power.case_temperature_c != null && <span className="shrink-0" title="ケース内温度">ケース {power.case_temperature_c}°</span>}
        {psuOk && power.fan_rpm != null && (
          // 0rpm は故障ではなく zero-RPM mode のことがある。回転数だけでは
          // 区別がつかないので、指令値の割合を併記する。
          <span
            className="shrink-0 font-medium text-sky-500 dark:text-sky-400"
            title={power.fan_percent != null
              ? `PSUファン 回転数 ${power.fan_rpm.toLocaleString()}rpm・指令 ${power.fan_percent}%`
              : "PSUファン回転数"}
          >
            ✻ {power.fan_rpm.toLocaleString()}rpm
          </span>
        )}
        <span className="ml-auto shrink-0" title={`電力量単価 ${power.price_per_kwh_yen}円/kWh・PSU効率${Math.round(power.psu_efficiency * 100)}%（概算）`}>
          ¥{power.price_per_kwh_yen}/kWh · η{Math.round(power.psu_efficiency * 100)}%
        </span>
      </div>
    </section>
  );
}

function CostItem({ label, cost, kwh, ok }: { label: string; cost: number | null; kwh: number | null; ok: boolean }) {
  return (
    <div>
      <p className="truncate text-[10px] text-zinc-500 md:text-xs">{label}</p>
      <p className="num whitespace-nowrap text-[13px] font-semibold tracking-tight text-zinc-800 dark:text-zinc-100 md:mt-0.5 md:text-xl">{ok ? fmtYen(cost) : "概算不可"}</p>
      {ok && <p className="num truncate text-[10px] text-zinc-400">{fmtKwh(kwh)}</p>}
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

interface DualLine { label: string; percent: number | null; values: (number | null)[]; tone: string; dot: string }

/** モバイル用: 2 つの使用率を 1 枚・2 本の線で出す。凡例は色つきの点＋「CPU 9%」の小さな数字（色だけに頼らず名前も書く）。 */
function DualTile({ a, b, notes, loading }: { a: DualLine; b: DualLine; notes: string[]; loading: boolean }) {
  const legend = (l: DualLine) => (
    <span className="flex min-w-0 items-center gap-1">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${l.dot}`} aria-hidden />
      <span className="text-[10px] font-medium text-zinc-500">{l.label}</span>
      <span className="num text-[13px] font-semibold tracking-tight">{loading ? "—" : l.percent != null ? formatPercent(l.percent) : "N/A"}</span>
    </span>
  );
  const enough = (l: DualLine) => l.values.filter((v) => v != null).length >= 2;
  return (
    <div className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-baseline justify-between gap-1">{legend(a)}{legend(b)}</div>
      <div className="relative mt-1 h-9 rounded-md bg-zinc-50 dark:bg-zinc-800/40">
        {enough(a) && <div className="absolute inset-0"><Sparkline values={a.values} fill className={a.tone} heightClass="h-9" /></div>}
        {enough(b) && <div className="absolute inset-0"><Sparkline values={b.values} fill className={b.tone} heightClass="h-9" /></div>}
      </div>
      <p className="num mt-0.5 flex h-3.5 items-center gap-1.5 overflow-hidden whitespace-nowrap text-[10px] text-zinc-400">
        {notes.filter(Boolean).map((n, i) => <span key={i} className={n.startsWith("✻") ? "shrink-0 font-medium text-sky-500 dark:text-sky-400" : "truncate"}>{n}</span>)}
      </p>
    </div>
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
  fanPercent,
}: {
  label: string;
  value: string | null;
  percent: number | null;
  values: (number | null)[];
  sub?: string;
  temp?: string;
  fan?: string;
  fanPercent?: number;
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
    <div className="rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900 md:p-4">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[11px] font-medium text-zinc-400 md:text-xs">{label}</p>
        {value === null ? (
          <Skeleton className="h-6 w-12 md:h-7 md:w-14" />
        ) : (
          <p className="num text-lg font-semibold tracking-tight md:text-2xl">{value}</p>
        )}
      </div>
      <div className="mt-1 md:mt-2">
        {values.filter((v) => v != null).length >= 2 ? (
          <Sparkline values={values} fill className={tone} heightClass="h-5 md:h-7" />
        ) : (
          <div className="h-5 rounded-md bg-zinc-50 dark:bg-zinc-800/40 md:h-7" />
        )}
      </div>
      <p className="num mt-1 flex h-4 items-center gap-2 overflow-hidden text-[10px] text-zinc-400 md:mt-1.5 md:text-[11px]">
        {sub && <span className="truncate">{sub}</span>}
        {temp && <span className="shrink-0">{temp}</span>}
        {fan && (
          <span className="flex shrink-0 items-center gap-1 font-medium text-sky-500 dark:text-sky-400">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" width="0.85em" height="0.85em" aria-hidden>
              <circle cx="12" cy="12" r="2.2" />
              <path d="M12 9.8c0-3.2 1.6-5 3.4-5 1.5 0 2.4 1.2 2.4 2.4 0 1.9-2.6 2.6-5.8 2.6zM12 14.2c0 3.2-1.6 5-3.4 5-1.5 0-2.4-1.2-2.4-2.4 0-1.9 2.6-2.6 5.8-2.6zM9.8 12c-3.2 0-5-1.6-5-3.4 0-1.5 1.2-2.4 2.4-2.4 1.9 0 2.6 2.6 2.6 5.8zM14.2 12c3.2 0 5 1.6 5 3.4 0 1.5-1.2 2.4-2.4 2.4-1.9 0-2.6-2.6-2.6-5.8z" />
            </svg>
            {fan} <span className="font-normal opacity-70">RPM</span>
            {/* 0 rpm と「止まっている」は違う。指令値があるなら添える。 */}
            {fanPercent != null && <span className="font-normal opacity-70">· {fanPercent}%</span>}
          </span>
        )}
      </p>
    </div>
  );
}
