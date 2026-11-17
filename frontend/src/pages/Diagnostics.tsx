/** 診断: ControlDeck の不調（再起動・OOM・詰まり・遅い API・エラー）を 1 画面で見て、
 * AI に渡せるレポートにする。自己診断とテストの実行もここから。 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { SegmentedTabs, Skeleton } from "../components/ui";
import { useToasts } from "../stores";
import { writeClipboard } from "../lib/clipboard";

type Status = "ok" | "warn" | "fail";
interface Check { name: string; status: Status; detail: string; seconds: number }
interface Restart { ts: number; cause: string; details: string[] }
interface Oom { ts: number; process: string; unit: string; anon_rss_gb: number; shmem_rss_gb: number }
interface UnitFailure { ts: number; unit: string; kind: string; message: string }
interface Stall { where: string; count: number; total_seconds: number; max_seconds: number; stack?: string[] }
interface RouteStat { route: string; count: number; p50: number; p95: number; max: number }
interface ErrorRow { level: string; logger: string; message: string; count: number; last: number; traceback: string }
interface Proc { name: string; rss_gb: number; unit: string }
interface Summary {
  generated_at: number;
  selftest: { overall: Status; checks: Check[] };
  journal: { hours: number; restarts: Restart[]; unit_failures: UnitFailure[]; oom_kills: Oom[] };
  errors: ErrorRow[];
  loop_stalls: Stall[];
  route_stats: RouteStat[];
  memory_now: { available_gb: number; total_gb: number; swap_used_gb: number; vram: { card: string; used_gb: number; total_gb: number }[]; top: Proc[] };
  memory_low: { ts: number; available_gb: number; top: Proc[] }[];
  failed_jobs: { ts: number; kind: string; title: string; error: string }[];
  tests?: { summary?: string; exit_code?: number; failed?: string[] } | null;
}

const when = (ts: number) => new Date(ts * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const TONE: Record<Status, string> = {
  ok: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
  warn: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400",
  fail: "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400",
};
const LABEL: Record<Status, string> = { ok: "正常", warn: "注意", fail: "異常" };

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        {title}
        {count !== undefined && <span className="rounded-full bg-zinc-100 px-2 text-[11px] font-medium text-zinc-500 dark:bg-zinc-800">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

const Empty = ({ text = "記録なし" }: { text?: string }) => <p className="text-xs text-zinc-500">{text}</p>;

export default function DiagnosticsPage() {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [hours, setHours] = useState<"6" | "24" | "72">("24");
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: ["diagnostics", hours],
    queryFn: () => api<Summary>(`/diagnostics/summary?hours=${hours}`),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const [testJob, setTestJob] = useState<string | null>(null);
  const [selection, setSelection] = useState("");
  const [busy, setBusy] = useState<"" | "selftest" | "report">("");

  const runSelftest = async () => {
    setBusy("selftest");
    try {
      await api("/diagnostics/selftest", { method: "POST" });
      await refetch();
    } catch (e) {
      show(e instanceof Error ? e.message : "自己診断に失敗しました", "error");
    } finally {
      setBusy("");
    }
  };
  const fetchReport = async () => {
    const response = await fetch(`/api/v1/diagnostics/report?hours=${hours}&format=md`, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`レポートを作れませんでした (${response.status})`);
    return response.text();
  };
  const copyReport = async () => {
    setBusy("report");
    try {
      const text = await fetchReport();
      const ok = await writeClipboard(text);
      show(ok ? `レポートをコピーしました（${text.length.toLocaleString()} 文字）。AI に貼り付けて相談できます` : "コピーできませんでした", ok ? undefined : "error");
    } catch (e) {
      show(e instanceof Error ? e.message : "レポートを作れませんでした", "error");
    } finally {
      setBusy("");
    }
  };
  const downloadReport = async () => {
    setBusy("report");
    try {
      const text = await fetchReport();
      const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `controldeck-diagnostics-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "")}.md`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      show(e instanceof Error ? e.message : "レポートを作れませんでした", "error");
    } finally {
      setBusy("");
    }
  };
  const startTests = async () => {
    try {
      const res = await api<{ job_id: string }>("/diagnostics/tests", { method: "POST", json: { selection } });
      setTestJob(res.job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "テストを始められませんでした", "error");
    }
  };

  const restarts = data?.journal.restarts ?? [];
  const abnormal = restarts.filter((r) => r.cause !== "更新" && r.cause !== "手動・通常の再起動");
  const stopHangs = abnormal.filter((r) => r.cause.startsWith("終了処理")).length;
  const failures = Object.entries((data?.journal.unit_failures ?? [])
    .filter((f) => f.kind !== "exited")
    .reduce<Record<string, { unit: string; kind: string; count: number; last: number }>>((acc, f) => {
      const key = `${f.unit}|${f.kind}`;
      acc[key] = acc[key] ? { ...acc[key], count: acc[key].count + 1, last: Math.max(acc[key].last, f.ts) } : { unit: f.unit, kind: f.kind, count: 1, last: f.ts };
      return acc;
    }, {})).map(([, v]) => v).sort((a, b) => b.count - a.count);

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4 pb-24 sm:p-6">
      <PageHeader title="Diagnostics" description="再起動・OOM・応答の詰まり・遅い API・エラーを記録し、AI に渡せるレポートにします" />

      <div className="flex flex-wrap items-center gap-2">
        <SegmentedTabs value={hours} onChange={(value) => setHours(value as "6" | "24" | "72")}
          tabs={[["6", "6時間"], ["24", "24時間"], ["72", "3日"]] as const} />
        <button type="button" onClick={() => void refetch()} disabled={isFetching}
          className="min-h-11 rounded-xl px-3 text-sm text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-300 dark:hover:bg-zinc-800">
          {isFetching ? "更新中…" : "更新"}
        </button>
        <div className="ml-auto flex flex-wrap gap-2">
          <button type="button" onClick={() => void copyReport()} disabled={busy !== ""}
            className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-50">
            {busy === "report" ? "作成中…" : "AI 用レポートをコピー"}
          </button>
          <button type="button" onClick={() => void downloadReport()} disabled={busy !== ""}
            className="min-h-11 rounded-xl border border-zinc-300 px-4 text-sm font-medium hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
            ダウンロード
          </button>
        </div>
      </div>
      <p className="text-[11px] text-zinc-500">
        レポートは鍵・トークン・メールアドレスを伏せてあります。AI アシスタント（OpenCode）は道具 <code className="break-all">control_deck.diagnostics</code> で同じ内容を直接読めます。
      </p>

      {error && !data && (
        <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400">
          診断を読み込めませんでした: {error instanceof Error ? error.message : String(error)}
        </p>
      )}
      {!data ? (!error && <Skeleton className="h-64" />) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 [&>*]:min-w-0">
          <Section title="自己診断">
            <div className="mb-2 flex items-center gap-2">
              <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${TONE[data.selftest.overall]}`}>{LABEL[data.selftest.overall]}</span>
              <button type="button" onClick={() => void runSelftest()} disabled={busy !== ""}
                className="ml-auto min-h-11 rounded-xl px-3 text-sm font-medium text-accent-700 hover:bg-accent-50 disabled:opacity-50 dark:text-accent-400 dark:hover:bg-accent-600/15">
                {busy === "selftest" ? "実行中…" : "もう一度"}
              </button>
            </div>
            <ul className="space-y-1.5">
              {data.selftest.checks.map((c) => (
                <li key={c.name} className="flex items-start gap-2 text-xs">
                  <span className={`mt-0.5 shrink-0 rounded px-1.5 text-[10px] font-semibold ${TONE[c.status]}`}>{LABEL[c.status]}</span>
                  <span className="min-w-0"><span className="font-medium">{c.name}</span> <span className="text-zinc-500">{c.detail}</span></span>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="本体の再起動" count={restarts.length}>
            <p className="mb-1.5 text-xs text-zinc-500">
              更新 {restarts.filter((r) => r.cause === "更新").length} 回 · 手動など {restarts.filter((r) => r.cause === "手動・通常の再起動").length} 回 · 異常 {abnormal.length} 回{stopHangs ? `（うち終了処理の詰まり ${stopHangs}）` : ""}（直近 {Math.round(data.journal.hours)} 時間）
            </p>
            {abnormal.length ? (
              <ul className="space-y-1 text-xs">
                {abnormal.slice(-8).reverse().map((r) => (
                  <li key={r.ts}><span className="tabular-nums text-zinc-500">{when(r.ts)}</span> <span className="font-medium text-red-600 dark:text-red-400">{r.cause}</span></li>
                ))}
              </ul>
            ) : <Empty text="更新以外の再起動はありません" />}
          </Section>

          <Section title="OOM で強制終了" count={data.journal.oom_kills.length}>
            {data.journal.oom_kills.length ? (
              <ul className="space-y-1 text-xs">
                {data.journal.oom_kills.slice().reverse().map((o) => (
                  <li key={`${o.ts}-${o.process}`} className="break-words">
                    <span className="tabular-nums text-zinc-500">{when(o.ts)}</span> <span className="font-medium">{o.process}</span>
                    {" "}<span className="text-zinc-500">{o.anon_rss_gb + o.shmem_rss_gb > 0 ? `${(o.anon_rss_gb + o.shmem_rss_gb).toFixed(1)} GiB` : ""} · {o.unit}</span>
                  </li>
                ))}
              </ul>
            ) : <Empty text="ありません" />}
            {data.memory_low.length > 0 && (
              <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">空きメモリが少なかった時刻: {data.memory_low.slice(-3).map((m) => `${when(m.ts)}（${m.available_gb} GiB）`).join("、")}</p>
            )}
          </Section>

          <Section title="常駐の異常終了" count={failures.length}>
            {failures.length ? (
              <ul className="space-y-1 text-xs">
                {failures.slice(0, 10).map((f) => (
                  <li key={`${f.unit}-${f.kind}`} className="break-all">
                    <span className="font-medium">{f.unit}</span> <span className="text-zinc-500">{f.kind} × {f.count}（最後 {when(f.last)}）</span>
                  </li>
                ))}
              </ul>
            ) : <Empty text="ありません" />}
          </Section>

          <Section title="応答の詰まり（画面が遅くなる原因）" count={data.loop_stalls.length}>
            {data.loop_stalls.length ? (
              <ul className="space-y-2 text-xs">
                {data.loop_stalls.slice(0, 6).map((s) => (
                  <li key={s.where}>
                    <p className="break-all font-mono text-[11px]">{s.where}</p>
                    <p className="text-zinc-500">{s.count} 回 · 計 {s.total_seconds.toFixed(1)} 秒 · 最長 {s.max_seconds.toFixed(1)} 秒</p>
                  </li>
                ))}
              </ul>
            ) : <Empty text="1 秒以上の詰まりはありません" />}
          </Section>

          <Section title="遅い API（p95 の大きい順）">
            {data.route_stats.length ? (
              <ul className="space-y-1 text-xs">
                {data.route_stats.slice(0, 8).map((r) => (
                  <li key={r.route} className="flex gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-[11px]" title={r.route}>{r.route}</span>
                    <span className={`shrink-0 tabular-nums ${r.p95 >= 2 ? "text-red-600 dark:text-red-400" : "text-zinc-500"}`}>p95 {r.p95}s</span>
                  </li>
                ))}
              </ul>
            ) : <Empty text="まだ集計がありません（再起動後から数えます）" />}
          </Section>

          <Section title="警告・エラー" count={data.errors.length}>
            {data.errors.length ? (
              <ul className="space-y-1.5 text-xs">
                {data.errors.slice(0, 10).map((e) => (
                  <li key={`${e.logger}-${e.message}`} className="break-words">
                    <span className={`mr-1 rounded px-1 text-[10px] font-semibold ${e.level === "WARNING" ? TONE.warn : TONE.fail}`}>{e.level}</span>
                    {e.message} <span className="text-zinc-500">× {e.count}（{when(e.last)}）</span>
                  </li>
                ))}
              </ul>
            ) : <Empty />}
          </Section>

          <Section title="メモリ（いま）">
            <p className="mb-1.5 text-xs">
              空き <span className="font-semibold">{data.memory_now.available_gb} GiB</span> / {data.memory_now.total_gb} GiB · swap {data.memory_now.swap_used_gb} GiB
              {data.memory_now.vram.map((g) => ` · VRAM ${g.used_gb}/${g.total_gb} GiB`).join("")}
            </p>
            <ul className="space-y-0.5 text-xs">
              {data.memory_now.top.slice(0, 6).map((p, i) => (
                <li key={i} className="flex gap-2"><span className="w-14 shrink-0 text-right tabular-nums">{p.rss_gb} GiB</span><span className="min-w-0 truncate">{p.name} <span className="text-zinc-500">{p.unit}</span></span></li>
              ))}
            </ul>
          </Section>

          <Section title="失敗したジョブ" count={data.failed_jobs.length}>
            {data.failed_jobs.length ? (
              <ul className="space-y-1 text-xs">
                {data.failed_jobs.slice(0, 6).map((j, i) => (
                  <li key={i} className="break-words"><span className="tabular-nums text-zinc-500">{when(j.ts)}</span> {j.title}: <span className="text-red-600 dark:text-red-400">{j.error.slice(0, 160)}</span></li>
                ))}
              </ul>
            ) : <Empty text="ありません" />}
          </Section>

          <Section title="テスト">
            <p className="mb-2 text-xs text-zinc-500">backend のテストを本番のデータに触らずに流します（全部で約 2 分）。空欄なら全部、またはファイル名やテスト名を空白区切りで。</p>
            <div className="flex gap-2">
              <input value={selection} onChange={(e) => setSelection(e.target.value)} placeholder="例: tests/test_chat_persist.py"
                aria-label="実行するテスト"
                className="min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3 py-2 text-base outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
              <button type="button" onClick={() => void startTests()} disabled={!!testJob}
                className="min-h-11 shrink-0 rounded-xl bg-zinc-900 px-4 text-sm font-semibold text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900">実行</button>
            </div>
            {testJob && <TestJob jobId={testJob} onDone={() => { setTestJob(null); void qc.invalidateQueries({ queryKey: ["diagnostics"] }); }} />}
            {!testJob && data.tests?.summary && <p className="mt-2 text-xs">前回: {data.tests.summary}</p>}
          </Section>
        </div>
      )}
    </div>
  );
}

function TestJob({ jobId, onDone }: { jobId: string; onDone: () => void }) {
  const { data: job } = useQuery({
    queryKey: ["diagnostics-test-job", jobId],
    queryFn: () => api<{ status: string; error: string; result?: { summary?: string; failed?: string[]; tail?: string } }>(`/jobs/${jobId}`),
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.status) ? false : 2000),
  });
  const finished = job && !["queued", "running"].includes(job.status);
  useEffect(() => {
    if (finished) {
      const timer = window.setTimeout(onDone, 30_000);
      return () => window.clearTimeout(timer);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);
  if (!job) return null;
  return (
    <div className="mt-2 rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-700" role="status">
      {!finished ? <p className="animate-pulse text-zinc-500">テストを実行中…（ブラウザを閉じても続きます）</p> : (
        <>
          <p className="font-medium">{job.result?.summary || job.error || job.status}</p>
          {(job.result?.failed ?? []).slice(0, 10).map((line) => <p key={line} className="break-all text-red-600 dark:text-red-400">{line}</p>)}
        </>
      )}
    </div>
  );
}
