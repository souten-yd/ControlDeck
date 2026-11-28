/** Models のメイン画面の「ダウンロード中」。走っている・止めているモデルの取得をまとめて出す。
 *
 *  2026-11-28 利用者「ダウンロード中のものはまとめてメインのところに表示できるように。ダウンロード速度、容量の
 *  最大と今の量、ファイルがあるなら何分の何か、が分かるように」。各カードは AcquireJobCard（今の量 / 全体・速度・
 *  残り時間・何個目のファイルか・止める・再開・取り消し）。画面を閉じても取得はサーバーで続く。
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listModelJobs, type ModelJob } from "../../api/models";
import { AcquireJobCard } from "./RuntimeCatalog";

/** モデルの取得の Job（HF の共通取得・Maya のダウンロードして登録・Ollama の pull） */
export const DOWNLOAD_KINDS = ["model.hf_download", "model.maya-s.download", "model.pull"];

const active = (job: ModelJob) =>
  DOWNLOAD_KINDS.includes(job.kind)
  && (job.status === "running" || job.status === "queued" || (job.status === "interrupted" && !!job.can_resume));

export function DownloadsPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["model-downloads"], queryFn: () => listModelJobs("model."),
    refetchInterval: 3000, retry: false,
  });
  const jobs = (data ?? []).filter(active);
  if (!jobs.length) return null;
  const refresh = () => void qc.invalidateQueries({ queryKey: ["model-downloads"] });
  return (
    <section className="mb-3 space-y-2 rounded-2xl border border-accent-200 bg-accent-50/40 p-3 dark:border-accent-900 dark:bg-accent-950/20"
      aria-label="ダウンロード中" data-model-downloads>
      <p className="flex items-center gap-2 text-sm font-semibold">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent-500" aria-hidden />
        ダウンロード中 <span className="text-xs font-normal text-zinc-500">{jobs.length} 件</span>
      </p>
      {jobs.map((job) => (
        <AcquireJobCard key={job.id} jobId={job.id} title={job.title} restored
          onFinished={refresh} onResumed={refresh} onDismiss={refresh} />
      ))}
    </section>
  );
}
