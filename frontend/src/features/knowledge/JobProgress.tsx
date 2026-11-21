/** サーバー側で回る Job の進捗表示。取り込み・知識エッセンスの抽出など画面を閉じても続く処理で使う。
 *  pages/Knowledge.tsx にあった RagJobProgress をそのまま移した（知識エッセンスの抽出でも使うため共通へ）。 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export function JobProgress({ jobId, onFinished }: {
  jobId: string;
  onFinished: (job: { status: string; error: string; result?: Record<string, unknown> }) => void;
}) {
  const { data: job } = useQuery({
    queryKey: ["rag-job", jobId],
    queryFn: () => api<{ status: string; error: string; result?: Record<string, unknown>; progress?: { status?: string; completed?: number | null; total?: number | null } }>(`/jobs/${jobId}`),
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.status) ? false : 1200),
  });
  const status = job?.status;
  useEffect(() => {
    if (job && status && !["queued", "running"].includes(status)) onFinished(job);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);
  if (!job) return null;
  const pct = job.progress?.total ? Math.round(((job.progress.completed ?? 0) / job.progress.total) * 100) : null;
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <p className="truncate text-xs text-zinc-500">
        {job.status === "queued" ? "開始待ち" : job.progress?.status || "処理中..."}
        {pct !== null && ` · ${pct}%`}
      </p>
      {pct !== null && (
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div className="h-full rounded-full bg-accent-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
      )}
      <p className="mt-1 text-[10px] text-zinc-400">サーバー側で実行中 — ブラウザを閉じても継続します</p>
    </div>
  );
}
