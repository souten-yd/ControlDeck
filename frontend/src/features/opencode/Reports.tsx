/** 結果 — UI 検証・精密調査・レビューの報告を新しい順に並べる。
 *
 * 報告は AI がセッションの中で control_deck.report を呼んだ時点で増えるので、
 * 開いている間だけ 10 秒ごとに取り直す（タブが裏にある間は止まる）。 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Skeleton } from "../../components/ui";
import { useAuth } from "../../stores";
import { ReportDetailSheet } from "./ReportDetail";
import { KIND_LABEL, KindIcon, SeverityChips, VerdictBadge, reportImageUrl, reportsKey, timeAgo, type ReportSummary } from "./reportShared";

const FIRST_PAGE = 8;

export function Reports() {
  const canView = useAuth((state) => state.can("workflows.run"));
  const [open, setOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const query = useQuery({
    queryKey: reportsKey,
    queryFn: () => api<{ reports: ReportSummary[] }>("/opencode/reports?limit=50"),
    enabled: canView && open,
    // 既定で、タブが裏にある間は取り直さない。
    refetchInterval: open ? 10_000 : false,
  });
  if (!canView) return null;
  const reports = [...(query.data?.reports ?? [])].sort((a, b) => b.created_at - a.created_at);
  const visible = showAll ? reports : reports.slice(0, FIRST_PAGE);
  return <>
    <details open={open} className="group rounded-2xl border border-zinc-200 px-4 dark:border-zinc-800"
      onToggle={(event) => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open); }}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 py-2 text-sm font-medium [&::-webkit-details-marker]:hidden">
        <span className="flex-1">結果
          {query.data && reports.length > 0 && <span className="ml-1.5 text-xs font-normal tabular-nums text-zinc-500">{reports.length} 件</span>}
        </span>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" className="text-zinc-400 transition-transform group-open:rotate-90" aria-hidden><path d="m9 6 6 6-6 6" /></svg>
      </summary>
      {open && <div className="pb-4">
        {query.isPending && <div className="space-y-2" aria-label="結果を読み込み中"><Skeleton className="h-14" /><Skeleton className="h-14" /></div>}
        {query.isError && <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-zinc-600 dark:text-zinc-300">
          <p className="min-w-0 flex-1 break-words">結果を読み込めませんでした。{query.error.message}</p>
          <button type="button" onClick={() => void query.refetch()} className="min-h-11 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">再試行</button>
        </div>}
        {query.data && reports.length === 0 && (
          <div className="rounded-xl bg-zinc-50 px-3 py-4 text-sm text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
            <p className="font-medium">まだ結果はありません</p>
            <p className="mt-1 text-xs text-zinc-500">「作業の種類」で UI 検証・精密調査・レビューを選んでセッションを始めると、AI が確かめた結果がここに出ます。</p>
          </div>
        )}
        {visible.length > 0 && (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {visible.map((report) => (
              <li key={report.id}>
                <button type="button" onClick={() => setSelected(report.id)}
                  aria-label={`${KIND_LABEL[report.kind] ?? report.kind}の結果「${report.title}」を開く`}
                  className="flex min-h-11 w-full items-start gap-3 rounded-lg py-2.5 text-left hover:bg-zinc-50 dark:hover:bg-zinc-900">
                  <span className="grid h-12 w-12 shrink-0 place-items-center overflow-hidden rounded-lg bg-zinc-100 text-lg text-zinc-500 dark:bg-zinc-800">
                    {report.kind === "ui_verify" && report.thumbnail
                      ? <img src={reportImageUrl(report.id, report.thumbnail)} alt="" loading="lazy" className="h-full w-full object-cover object-top" />
                      : <KindIcon kind={report.kind} />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-start gap-2">
                      <span className="line-clamp-2 min-w-0 flex-1 break-words text-sm font-medium">{report.title || "（無題）"}</span>
                      <VerdictBadge verdict={report.verdict} />
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500">
                      <span>{KIND_LABEL[report.kind] ?? report.kind}</span>
                      <span className="tabular-nums">{timeAgo(report.created_at)}</span>
                      <SeverityChips counts={report.counts} />
                      {report.kind === "research" && report.sources > 0 && <span className="tabular-nums">出典 {report.sources}</span>}
                      {report.kind === "ui_verify" && report.checks > 0 && <span className="tabular-nums">画面 {report.checks}</span>}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {!showAll && reports.length > FIRST_PAGE && (
          <button type="button" onClick={() => setShowAll(true)}
            className="mt-1 min-h-11 w-full rounded-xl text-sm text-accent-700 hover:bg-zinc-50 dark:text-accent-400 dark:hover:bg-zinc-900">
            残り {reports.length - FIRST_PAGE} 件を表示
          </button>
        )}
      </div>}
    </details>
    {selected && <ReportDetailSheet id={selected} onClose={() => setSelected(null)} />}
  </>;
}
