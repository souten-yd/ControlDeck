import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useOverview } from "../api/hooks";
import { useMetrics } from "../stores";
import { formatBps, formatBytes, formatUptime } from "../lib/format";
import { Skeleton } from "../components/ui";

interface DiskInfo {
  device: string;
  mountpoint: string;
  fstype: string;
  total: number;
  used: number;
  percent: number;
}

interface NetInfo {
  interface: string;
  ips: string[];
  is_up: boolean;
  speed_mbps: number | null;
  bytes_recv: number;
  bytes_sent: number;
}

interface ProcInfo {
  pid: number;
  name: string;
  username: string;
  cpu_percent: number;
  memory_bytes: number;
}

export default function SystemPage() {
  const { data: overview, isLoading } = useOverview();
  const latest = useMetrics((s) => s.latest);
  const { data: disks } = useQuery({
    queryKey: ["disks"],
    queryFn: () => api<DiskInfo[]>("/system/disk"),
    refetchInterval: 30_000,
  });
  const { data: nets } = useQuery({
    queryKey: ["network"],
    queryFn: () => api<NetInfo[]>("/system/network"),
    refetchInterval: 15_000,
  });
  const { data: procs } = useQuery({
    queryKey: ["processes"],
    queryFn: () => api<ProcInfo[]>("/system/processes"),
    refetchInterval: 10_000,
  });

  const host = overview?.host;
  const m = latest;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-6">
      <h1 className="text-lg font-semibold">システム</h1>

      {/* ホスト情報 */}
      <Section title="ホスト">
        {isLoading || !host ? (
          <Skeleton className="h-24" />
        ) : (
          <dl className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            <Row k="ホスト名" v={host.hostname} />
            <Row k="OS" v={host.os} />
            <Row k="カーネル" v={host.kernel} />
            <Row k="タイムゾーン" v={host.timezone} />
            <Row k="稼働時間" v={formatUptime(host.uptime_seconds)} />
            <Row k="起動時刻" v={new Date(host.boot_time).toLocaleString("ja-JP")} />
          </dl>
        )}
      </Section>

      {/* CPU 詳細 */}
      {m && (
        <Section title="CPU">
          <dl className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            <Row k="使用率" v={`${m.cpu.percent.toFixed(1)}%`} />
            <Row k="コア数" v={String(m.cpu.cores)} />
            <Row
              k="ロードアベレージ"
              v={m.cpu.load.map((l) => l.toFixed(2)).join(" / ")}
            />
            <Row
              k="クロック"
              v={m.cpu.freq_mhz ? `${(m.cpu.freq_mhz / 1000).toFixed(2)} GHz` : "N/A"}
            />
            <Row
              k="温度"
              v={m.cpu.temperature_c != null ? `${m.cpu.temperature_c.toFixed(0)}°C` : "N/A"}
            />
          </dl>
          <div className="mt-3 flex flex-wrap gap-1">
            {m.cpu.per_cpu.map((p, i) => (
              <div
                key={i}
                title={`Core ${i}: ${p.toFixed(0)}%`}
                className="h-8 w-3 overflow-hidden rounded-sm bg-zinc-100 dark:bg-zinc-800"
              >
                <div
                  className={`w-full ${p >= 90 ? "bg-red-500" : "bg-accent-500"}`}
                  style={{ height: `${p}%`, marginTop: `${100 - p}%` }}
                />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* GPU 詳細 */}
      {m?.gpu && (
        <Section title={`GPU — ${m.gpu.name}`}>
          <dl className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            <Row
              k="使用率"
              v={m.gpu.utilization_percent != null ? `${m.gpu.utilization_percent.toFixed(0)}%` : "N/A"}
            />
            <Row
              k="VRAM"
              v={
                m.gpu.vram_used_bytes != null && m.gpu.vram_total_bytes
                  ? `${formatBytes(m.gpu.vram_used_bytes)} / ${formatBytes(m.gpu.vram_total_bytes)}`
                  : "N/A"
              }
            />
            <Row
              k="温度"
              v={m.gpu.temperature_c != null ? `${m.gpu.temperature_c.toFixed(0)}°C` : "N/A"}
            />
            <Row
              k="Hotspot"
              v={m.gpu.hotspot_c != null ? `${m.gpu.hotspot_c.toFixed(0)}°C` : "N/A"}
            />
            <Row
              k="消費電力"
              v={
                m.gpu.power_watts != null
                  ? `${m.gpu.power_watts.toFixed(0)} W${m.gpu.power_cap_watts ? ` / ${m.gpu.power_cap_watts.toFixed(0)} W` : ""}`
                  : "N/A"
              }
            />
          </dl>
        </Section>
      )}

      {/* ディスク */}
      <Section title="ディスク">
        {!disks ? (
          <Skeleton className="h-16" />
        ) : (
          <ul className="space-y-3">
            {disks.map((d) => (
              <li key={d.mountpoint} className="text-sm">
                <div className="mb-1 flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate font-mono text-xs">{d.mountpoint}</span>
                  <span className="num shrink-0 text-xs text-zinc-400">
                    {formatBytes(d.used)} / {formatBytes(d.total)}（{d.percent.toFixed(0)}%）
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
                  <div
                    className={`h-full rounded-full ${d.percent >= 90 ? "bg-red-500" : "bg-accent-500"}`}
                    style={{ width: `${d.percent}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* ネットワーク */}
      <Section title="ネットワーク">
        {!nets ? (
          <Skeleton className="h-16" />
        ) : (
          <ul className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
            {nets.map((n) => (
              <li key={n.interface} className="flex items-center gap-3 py-2">
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${n.is_up ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-700"}`}
                  aria-label={n.is_up ? "アップ" : "ダウン"}
                />
                <span className="w-24 shrink-0 font-mono text-xs">{n.interface}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-400">
                  {n.ips.join(", ") || "—"}
                </span>
                <span className="num hidden text-xs text-zinc-400 sm:block">
                  ↓{formatBytes(n.bytes_recv)} ↑{formatBytes(n.bytes_sent)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {m && (
          <p className="num mt-2 text-xs text-zinc-400">
            現在: ↓ {formatBps(m.io.net_rx_bps)} / ↑ {formatBps(m.io.net_tx_bps)}
          </p>
        )}
      </Section>

      {/* Control Deck 自己状態 */}
      <SelfStatusSection />

      {/* 上位プロセス */}
      <Section title="上位プロセス（CPU）">
        {!procs ? (
          <Skeleton className="h-24" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-xs text-zinc-400">
                  <th className="py-1.5 pr-4 font-medium">PID</th>
                  <th className="py-1.5 pr-4 font-medium">名前</th>
                  <th className="py-1.5 pr-4 font-medium">CPU</th>
                  <th className="py-1.5 font-medium">RAM</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {procs.map((p) => (
                  <tr key={p.pid}>
                    <td className="num py-1.5 pr-4 text-xs text-zinc-400">{p.pid}</td>
                    <td className="max-w-[40vw] truncate py-1.5 pr-4">{p.name}</td>
                    <td className="num py-1.5 pr-4">{p.cpu_percent.toFixed(0)}%</td>
                    <td className="num py-1.5">{formatBytes(p.memory_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

interface SelfStatus {
  watchdog_enabled: boolean;
  checks: Record<string, { ok: boolean; detail: string }>;
  maintenance: { last_run_at: string | null; last_results: Record<string, { ok: boolean }> };
}

const CHECK_LABELS: Record<string, string> = {
  database: "データベース",
  metrics_collector: "メトリクス収集",
  workflow_scheduler: "スケジューラー",
};

function SelfStatusSection() {
  const { data } = useQuery({
    queryKey: ["self-status"],
    queryFn: () => api<SelfStatus>("/system/self-status"),
    refetchInterval: 30_000,
  });
  if (!data) return null;
  return (
    <Section title="Control Deck 自己診断">
      <ul className="space-y-1.5 text-sm">
        <li className="flex items-center gap-2">
          <StatusDot ok={data.watchdog_enabled} warn={!data.watchdog_enabled} />
          systemd ウォッチドッグ
          <span className="text-xs text-zinc-400">
            {data.watchdog_enabled ? "有効（ハング時自動再起動）" : "無効（./deck.sh service で有効化）"}
          </span>
        </li>
        {Object.entries(data.checks).map(([key, c]) => (
          <li key={key} className="flex items-center gap-2">
            <StatusDot ok={c.ok} />
            {CHECK_LABELS[key] ?? key}
            <span className="text-xs text-zinc-400">{c.detail}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-zinc-400">
        自己メンテナンス（ログローテーション / セッション整理 / DB 最適化）:{" "}
        {data.maintenance.last_run_at
          ? `最終実行 ${new Date(data.maintenance.last_run_at).toLocaleString("ja-JP")}`
          : "起動 5 分後に初回実行されます"}
      </p>
    </Section>
  );
}

function StatusDot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  const cls = ok ? "bg-emerald-500" : warn ? "bg-amber-500" : "bg-red-500";
  return <span className={`h-2 w-2 shrink-0 rounded-full ${cls}`} aria-hidden />;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="mb-3 text-sm font-semibold text-zinc-500">{title}</h2>
      {children}
    </section>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4 sm:justify-start">
      <dt className="w-32 shrink-0 text-zinc-400">{k}</dt>
      <dd className="num min-w-0 break-all text-right sm:text-left">{v}</dd>
    </div>
  );
}
