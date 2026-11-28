/** 全ランタイム共通のモデル目録。backend `GET /models/catalog` の行だけをカードにする。
 *
 *  URL・大きさ・sha は目録に無い限り書かない（未確認の行は「取得時に確認」と出す）。
 *  取得は共通の `POST /models/acquire-jobs`、停止・再開・取り消しはジョブ API。
 *  途中のファイル（.part）は停止では残し、取り消しで消す（docs/PLAN-model-catalog.md）。
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Skeleton } from "../../components/ui";
import { useToasts } from "../../stores";
import { useExpert } from "../commander/TeamRoles";
import {
  cancelModelJob, getModelJob, getRuntimeCatalog, getRuntimeCatalogFiles,
  listRuntimeInstances, resumeModelJob, startRuntimeAcquire, stopModelJob,
  type ModelJob, type ModelLibrary, type RuntimeCatalogEntry,
} from "../../api/models";

export interface RuntimeCatalogProps {
  /** backend の runtime id（llama / radiance / sly-radiance / strata / maya）。 */
  runtime: string;
  /** 取得したファイルを登録画面へ渡す（取得後の登録は各ランタイムの API を使う）。 */
  onRegister?: (path: string, entry: RuntimeCatalogEntry) => void;
  /** 省略時は利用者ごとに保存されたエキスパート設定を使う。 */
  expert?: boolean;
}

const FIT_LABEL: Record<string, string> = {
  fits: "載る", tight: "ぎりぎり", no: "載らない", unknown: "",
};

const gb = (bytes: number) => bytes >= 1e9
  ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(0)} MB`;
const rateText = (perSec: number) => perSec >= 1e6
  ? `${(perSec / 1e6).toFixed(1)} MB/s` : `${(perSec / 1e3).toFixed(0)} KB/s`;
const etaText = (seconds: number) => seconds < 60
  ? `残り約${Math.ceil(seconds)}秒` : `残り約${Math.ceil(seconds / 60)}分`;
const dirname = (path: string) => path.replace(/\/[^/]*$/, "");
const terminal = (status: string) => !["queued", "running"].includes(status);

interface Pending {
  entry: RuntimeCatalogEntry;
  repo: string;
  files: Array<{ path: string; size: number }>;
  total_bytes: number;
  libraries: ModelLibrary[];
  library_id: string;
}

/** 取得ジョブの進み具合。速度と残り時間は進捗の増分から画面側で計算する（backend は completed/total を出す）。 */
function AcquireJobCard({ jobId, title, onFinished, onResumed, onDismiss }: {
  jobId: string;
  title: string;
  onFinished: (job: ModelJob) => void;
  onResumed: (newJobId: string) => void;
  onDismiss: () => void;
}) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const { data: job } = useQuery({
    queryKey: ["runtime-acquire-job", jobId],
    queryFn: () => getModelJob(jobId),
    refetchInterval: (query) => (query.state.data && terminal(query.state.data.status) ? false : 1200),
  });
  const [rate, setRate] = useState<number | null>(null);
  const sample = useRef<{ at: number; done: number } | null>(null);
  const finished = useRef(false);

  useEffect(() => {
    const done = job?.progress?.completed;
    if (job && typeof done === "number" && job.status === "running") {
      const at = Date.now();
      const prev = sample.current;
      if (prev && done > prev.done && at > prev.at) {
        setRate((done - prev.done) / ((at - prev.at) / 1000));
      }
      sample.current = { at, done };
    } else {
      sample.current = null;
    }
  }, [job?.progress?.completed, job?.status]);

  useEffect(() => {
    if (job && terminal(job.status) && !finished.current) {
      finished.current = true;
      onFinished(job);
    }
  }, [job, onFinished]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["runtime-acquire-job", jobId] });
    void qc.invalidateQueries({ queryKey: ["models", "local"] });
  };
  const fail = (error: unknown, message: string) =>
    show(error instanceof Error ? error.message : message, "error");
  const stop = useMutation({
    mutationFn: () => stopModelJob(jobId),
    onSuccess: (result) => {
      show(result.can_resume ? "停止しました（続きから再開できます）" : "停止しました", "success");
      invalidate();
    },
    onError: (error) => fail(error, "停止できませんでした"),
  });
  const resume = useMutation({
    mutationFn: () => resumeModelJob(jobId),
    onSuccess: (result) => { show("続きから取得します"); onResumed(result.job_id); },
    onError: (error) => fail(error, "再開できませんでした"),
  });
  const cancel = useMutation({
    mutationFn: () => cancelModelJob(jobId),
    onSuccess: () => { show("取り消しました（途中のファイルは削除されます）"); invalidate(); },
    onError: (error) => fail(error, "取り消せませんでした"),
  });

  if (!job) return <p className="text-[11px] text-zinc-400">ジョブを確認中…</p>;
  const done = job.progress?.completed ?? 0;
  const total = job.progress?.total ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
  const eta = rate !== null && total > done ? etaText((total - done) / rate) : null;
  const label = job.status === "succeeded" ? "取得しました"
    : job.status === "failed" ? `失敗: ${job.error}` : job.status === "canceled" ? "取り消しました"
      : job.status === "interrupted" ? "停止中（続きから再開できます）"
        : job.status === "queued" ? "開始待ち" : job.progress?.status || "取得中";
  return <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-acquire-job={jobId}>
    <p className="truncate text-xs">{title} · {label}</p>
    {pct !== null && (
      <>
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div className="h-full rounded-full bg-accent-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
        <p className="num mt-1 text-[11px] text-zinc-500">
          {pct}%{rate !== null && ` · ${rateText(rate)}`}{eta && ` · ${eta}`}
        </p>
      </>
    )}
    <div className="mt-2 flex flex-wrap gap-1.5">
      {job.status === "running" && (
        <button type="button" disabled={stop.isPending} onClick={() => stop.mutate()}
          className="min-h-11 rounded-lg border border-zinc-300 px-3 text-xs dark:border-zinc-600">
          停止
        </button>
      )}
      {job.can_resume && (
        <button type="button" disabled={resume.isPending} onClick={() => resume.mutate()}
          className="min-h-11 rounded-lg bg-accent-600 px-3 text-xs text-white">
          再開
        </button>
      )}
      {(job.status === "running" || job.status === "queued") && (
        <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}
          className="min-h-11 rounded-lg border border-red-300 px-3 text-xs text-red-600 dark:border-red-800">
          取り消し
        </button>
      )}
      {terminal(job.status) && (
        <button type="button" onClick={onDismiss}
          className="min-h-11 rounded-lg border border-zinc-300 px-3 text-xs dark:border-zinc-600">
          閉じる
        </button>
      )}
    </div>
    <p className="mt-1 text-[10px] text-zinc-400">サーバー側で実行中 — 画面を閉じても継続します</p>
  </div>;
}

export function RuntimeCatalog({ runtime, onRegister, expert: expertProp }: RuntimeCatalogProps) {
  const storedExpert = useExpert();
  const expert = expertProp ?? storedExpert;
  const show = useToasts((s) => s.show);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState<Array<{ id: string; entry: RuntimeCatalogEntry }>>([]);
  const [acquired, setAcquired] = useState<Record<string, string[]>>({});

  const { data: catalog, isLoading, error } = useQuery({
    queryKey: ["runtime-catalog"], queryFn: getRuntimeCatalog, staleTime: 60_000,
  });
  const { data: instances } = useQuery({
    queryKey: ["runtime-instances"], queryFn: listRuntimeInstances, staleTime: 30_000,
  });
  const block = catalog?.runtimes.find((item) => item.id === runtime);
  const entries = block?.entries ?? [];

  const open = async (entry: RuntimeCatalogEntry) => {
    setBusy(true);
    try {
      const list = await getRuntimeCatalogFiles(runtime, entry.id);
      setPending({ entry, ...list, library_id: "" });
    } catch (e) {
      show(e instanceof Error ? e.message : "ファイル一覧を取得できませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      const result = await startRuntimeAcquire({
        runtime, entry_id: pending.entry.id, library_id: pending.library_id,
        files: pending.files.map((file) => file.path),
      });
      setJobs((current) => [{ id: result.job_id, entry: pending.entry }, ...current]);
      setPending(null);
      show("取得を始めました（停止・再開できます）");
    } catch (e) {
      show(e instanceof Error ? e.message : "取得を始められませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  const dropJob = (jobId: string) => setJobs((current) => current.filter((item) => item.id !== jobId));
  const onFinished = (jobId: string, entry: RuntimeCatalogEntry) => (job: ModelJob) => {
    // 停止は意図した操作なので失敗扱いにしない。再開できるようカードは残す。
    if (job.status === "interrupted") {
      show("停止しました。続きから再開できます", "info");
      return;
    }
    const files = (job.result?.files as string[] | undefined) ?? [];
    if (job.status !== "succeeded" || !files.length) {
      show(job.error || `${entry.name} を取得できませんでした`, "error");
      dropJob(jobId);
      return;
    }
    setAcquired((current) => ({ ...current, [entry.id]: files }));
    dropJob(jobId);
    show(`${entry.name} を取得しました`);
  };

  const instanceRuntime = runtime === "llama" ? "llama.cpp" : runtime;
  const registered = (entry: RuntimeCatalogEntry) => {
    const paths = acquired[entry.id] ?? [];
    if (!paths.length) return false;
    const candidates = paths.flatMap((path) => [path, dirname(path)]);
    return (instances?.instances ?? []).some((item) =>
      item.runtime === instanceRuntime && candidates.includes(item.model_path));
  };

  return (
    <div className="space-y-3" data-runtime-catalog={runtime} data-expert={expert ? "on" : "off"}>
      {error && <p className="text-xs text-red-600">{error instanceof Error ? error.message : "目録を取得できませんでした"}</p>}
      {isLoading && <Skeleton className="h-20" />}
      {!isLoading && entries.length === 0 && (
        <p className="rounded-xl border border-dashed border-zinc-200 p-3 text-xs text-zinc-500 dark:border-zinc-700">
          {block?.search_ref
            ? "このランタイムには推奨モデルの目録がありません。HF 検索、または下の「ファイルから登録」を使ってください。"
            : "このランタイムには推奨モデルの目録がありません。下の「ファイルから登録」を使ってください。"}
        </p>
      )}
      {block?.catalog_ref && (
        <p className="text-[11px] text-zinc-500">全ての候補はランタイム本体の目録から確認できます
          {expert && <span className="font-mono">（{block.catalog_ref}）</span>}。</p>
      )}
      {block && !block.installed && (
        <p className="text-[11px] text-amber-700 dark:text-amber-300">このランタイムはまだ導入されていません。</p>
      )}

      {pending && (
        <div className="space-y-2 rounded-xl border border-accent-300 p-3 dark:border-accent-800">
          <p className="min-w-0 truncate font-mono text-xs">{pending.repo}</p>
          <p className="num text-[11px] text-zinc-500">
            ファイル {pending.files.length} 件 · 合計 {gb(pending.total_bytes)}（HF から取得時に確認）
          </p>
          {pending.libraries.length > 0 && (
            <label className="block text-xs">保存先
              <select aria-label="取得の保存先" value={pending.library_id}
                onChange={(e) => setPending({ ...pending, library_id: e.target.value })}
                className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                <option value="">既定のライブラリ</option>
                {pending.libraries.filter((library) => library.mounted).map((library) => (
                  <option key={library.id} value={library.id}>{library.label}</option>
                ))}
              </select>
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy} onClick={() => void start()}
              className="min-h-11 flex-1 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white disabled:opacity-40">
              {busy ? "確認中…" : "取得する"}
            </button>
            <button type="button" disabled={busy} onClick={() => setPending(null)}
              className="min-h-11 rounded-xl border border-zinc-300 px-4 text-sm dark:border-zinc-700">やめる</button>
          </div>
        </div>
      )}

      <ul className="space-y-2">
        {entries.map((entry) => {
          const done = registered(entry);
          const canFetch = entry.supported && entry.acquire === "hf" && !!entry.repo;
          return <li key={entry.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"
            data-catalog-entry={entry.id}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="min-w-0 flex-1 text-sm font-medium">{entry.name}</span>
              {entry.uncensored && <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">無検閲寄り</span>}
              {entry.kind === "drafter" && <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] dark:bg-zinc-800">先読み</span>}
              {FIT_LABEL[entry.fit] && <span className="shrink-0 text-[10px] text-zinc-500">{FIT_LABEL[entry.fit]}</span>}
              {!entry.supported && <span className="shrink-0 text-[10px] text-zinc-500">未対応</span>}
            </div>
            <p className="num mt-1 text-[11px] text-zinc-500">
              {entry.quantization || entry.format}
              {entry.size_gib !== null && ` · 約${entry.size_gib} GiB`}
              {entry.size_gib === null && canFetch && " · 大きさは取得時に確認"}
              {entry.memory_gib !== null && ` · 目安 ${entry.memory_gib} GiB`}
            </p>
            {entry.note && <p className="mt-1 text-[11px] text-zinc-500">{entry.note}</p>}
            <div className="mt-2 flex flex-wrap gap-1.5">
              {done && <span className="min-h-11 content-center text-xs text-emerald-600">登録済み</span>}
              {!done && acquired[entry.id]?.[0] && onRegister && (
                <button type="button" onClick={() => {
                  const file = acquired[entry.id][0];
                  // safetensors はフォルダ単位で登録する（Sly は config.json のあるフォルダ）。
                  onRegister(entry.format === "safetensors" ? dirname(file) : file, entry);
                }}
                  className="min-h-11 rounded-lg border border-zinc-300 px-3 text-xs dark:border-zinc-600">
                  登録する
                </button>
              )}
              {canFetch && !done && (
                <button type="button" disabled={busy || !!pending} onClick={() => void open(entry)}
                  className="min-h-11 rounded-lg bg-accent-600 px-3 text-xs font-medium text-white disabled:opacity-40">
                  取得する
                </button>
              )}
              {!canFetch && entry.acquire === "runtime" && (
                <span className="text-[11px] text-zinc-500">初回起動時にランタイムが取得します。登録画面で選んでください。</span>
              )}
            </div>
          </li>;
        })}
      </ul>

      {jobs.map(({ id, entry }) => (
        <AcquireJobCard key={id} jobId={id} title={entry.name}
          onFinished={onFinished(id, entry)}
          onDismiss={() => dropJob(id)}
          onResumed={(newId) => setJobs((current) => [
            { id: newId, entry }, ...current.filter((item) => item.id !== id),
          ])} />
      ))}
    </div>
  );
}
