/** オプション機能の自動更新（1 日 1 回、決めた時刻に）と「今すぐすべて更新」。
 *
 * 2026-11-20 利用者「オプション機能を 1 日 1 回ぐらい時間を決めて自動でバージョンアップできるように。
 * ボタンと時間設定を付けて」。判断と更新はサーバー（backend/app/features/auto_update.py）。
 * 時刻はサーバーの時計。使われている間は待ち、空いたらその日のうちに 1 回だけ更新する。 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

interface AutoUpdateResult {
  id: string; name: string; before: string; after: string;
  status: "updated" | "current" | "failed"; message: string;
}
interface AutoUpdateState {
  enabled: boolean;
  time: string;
  running: boolean;
  last: { started_at?: number; finished_at?: number | null; trigger?: string; results?: AutoUpdateResult[] };
}

const when = (seconds?: number | null) => seconds
  ? new Date(seconds * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
  : "";

export function FeatureAutoUpdate({ busy }: { busy: boolean }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey: ["feature-auto-update"],
    queryFn: () => api<AutoUpdateState>("/features/auto-update"),
    refetchInterval: (q) => (jobId || q.state.data?.running ? 2000 : 60_000),
  });
  const [time, setTime] = useState("");
  useEffect(() => { if (data && !time) setTime(data.time); }, [data, time]);
  const { data: job } = useQuery({
    queryKey: ["feature-job", jobId],
    queryFn: () => api<{ status: string; error: string; progress?: { status?: string } }>(`/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.status) ? false : 1500),
  });
  useEffect(() => {
    if (!jobId || !job || ["queued", "running"].includes(job.status)) return;
    setJobId(null);
    qc.invalidateQueries({ queryKey: ["feature-auto-update"] });
    qc.invalidateQueries({ queryKey: ["features"] });
    qc.invalidateQueries({ queryKey: ["feature-release-status"] });
    if (job.status !== "succeeded") show(job.error || "一括更新に失敗しました", "error");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);

  const save = async (patch: Partial<Pick<AutoUpdateState, "enabled" | "time">>) => {
    try {
      const next = await api<AutoUpdateState>("/features/auto-update", { method: "PUT", json: patch });
      qc.setQueryData(["feature-auto-update"], next);
      show(next.enabled ? `毎日 ${next.time} から自動で更新します` : "自動更新を止めました");
    } catch (e) {
      show(e instanceof Error ? e.message : "保存に失敗しました", "error");
    }
  };
  const runNow = async () => {
    try {
      const r = await api<{ job_id: string }>("/features/auto-update/run-jobs", { method: "POST", json: {} });
      setJobId(r.job_id);
      qc.invalidateQueries({ queryKey: ["feature-auto-update"] });
    } catch (e) {
      show(e instanceof Error ? e.message : "一括更新を始められませんでした", "error");
    }
  };

  const running = jobId !== null || Boolean(data?.running);
  const results = data?.last?.results ?? [];
  const updated = results.filter((r) => r.status === "updated");
  const failed = results.filter((r) => r.status === "failed");
  const btn = "min-h-10 rounded-xl px-3.5 text-xs font-medium disabled:opacity-40";
  return (
    <div className="mb-4 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-feature-auto-update>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <label className="flex min-h-10 items-center gap-2 text-sm font-semibold">
          <input type="checkbox" className="h-5 w-5 accent-current" checked={data?.enabled ?? false} disabled={!data}
            onChange={(e) => void save({ enabled: e.target.checked })} aria-label="オプション機能を毎日自動で更新する" />
          毎日自動で更新
        </label>
        <label className="flex items-center gap-1.5 text-xs text-zinc-500">
          時刻
          <input type="time" value={time} disabled={!data} aria-label="自動更新の時刻"
            onChange={(e) => setTime(e.target.value)}
            onBlur={() => { if (data && time && time !== data.time) void save({ time }); }}
            className="min-h-10 rounded-lg border border-zinc-200 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        </label>
        <button onClick={() => void runNow()} disabled={!data || running || busy}
          className={`${btn} ml-auto border border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800`}>
          {running ? "更新中…" : "今すぐすべて更新"}
        </button>
      </div>
      <p className="mt-1 text-[11px] text-zinc-400">
        ControlDeck が導入した機能を、サーバーの時計でこの時刻を過ぎたら 1 日 1 回まとめて更新します。使用中（推論・Job・OpenCode・GPU の貸出）は待ち、空いたら始めます。
      </p>
      {running && (
        <p className="mt-2 animate-pulse text-[11px] text-zinc-400">
          {job?.progress?.status || "確認・更新中…"} — サーバー側で実行中。この画面を閉じても継続します
        </p>
      )}
      {!running && data?.last?.finished_at && (
        <p className="mt-2 text-[11px] text-zinc-500" data-auto-update-last>
          前回 {when(data.last.finished_at)}（{data.last.trigger === "schedule" ? "自動" : "手動"}）:{" "}
          {updated.length === 0 && failed.length === 0 ? "すべて最新でした"
            : [updated.map((r) => `${r.name} ${r.before || "?"} → ${r.after}`).join("、"),
              failed.length ? `失敗 ${failed.map((r) => `${r.name}（${r.message}）`).join("、")}` : ""].filter(Boolean).join(" / ")}
        </p>
      )}
    </div>
  );
}
