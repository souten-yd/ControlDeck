/** 全ランタイム共通のモデル目録。backend `GET /models/catalog` の models（モデルごとのまとめ）を
 *  1 モデル 1 枚のカードにし、量子化はカードを開いて選ぶ。
 *
 *  2026-11-28 利用者「途中から精度が落ちてるし、なんだか目録が変。追加する機能のところ全てやり直して」で
 *  作り直した（量子化ごとの大きなカードが縦に並び、Strata は二重、開発者のメモが出て、押せない行があった）。
 *  大きさ・載るかは、カードを開いたときだけ backend が HF から読む（推測の大きさは書かない）。
 *  取得は共通の `POST /models/acquire-jobs`、停止・再開・取り消しはジョブ API。
 *  途中のファイル（.part）は停止では残し、取り消しで消す（docs/PLAN-model-catalog.md）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConfirmDialog, Skeleton } from "../../components/ui";
import { IconChevronRight } from "../../components/icons";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { useExpert } from "../commander/TeamRoles";
import {
  cancelModelJob, getModelJob, getRuntimeCatalog, getRuntimeCatalogDetails,
  listModelJobs, listRuntimeInstances, resumeModelJob, startRuntimeAcquire, stopModelJob,
  type ModelJob, type RuntimeCatalogEntry, type RuntimeCatalogModel, type RuntimeCatalogVariant,
} from "../../api/models";

export interface RuntimeCatalogProps {
  /** backend の runtime id（llama / radiance / sly-radiance / strata / maya）。 */
  runtime: string;
  /** 取得したファイルを登録画面へ渡す（取得後の登録は各ランタイムの API を使う）。 */
  onRegister?: (path: string, entry: RuntimeCatalogEntry) => void;
  /** 省略時は利用者ごとに保存されたエキスパート設定を使う。 */
  expert?: boolean;
}

const FIT: Record<string, { label: string; tone: string }> = {
  fits: { label: "載る", tone: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300" },
  slow: { label: "遅い", tone: "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300" },
  tight: { label: "ぎりぎり", tone: "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300" },
  no: { label: "載らない", tone: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400" },
};
/** 大きさ（GB = 10^9 バイト。Strata・Maya・HF の表示と同じ単位） */
const gib = (value: number) => value >= 10 ? `${Math.round(value)} GB` : `${value.toFixed(1)} GB`;
const rateText = (perSec: number) => perSec >= 1e6
  ? `${(perSec / 1e6).toFixed(1)} MB/s` : `${(perSec / 1e3).toFixed(0)} KB/s`;
const etaText = (seconds: number) => seconds < 60
  ? `残り約${Math.ceil(seconds)}秒` : `残り約${Math.ceil(seconds / 60)}分`;
const dirname = (path: string) => path.replace(/\/[^/]*$/, "");
const terminal = (status: string) => !["queued", "running"].includes(status);

interface JobEntry {
  id: string;
  entry: RuntimeCatalogEntry;
  /** 再表示したジョブは最初の状態でトーストを出さない。 */
  restored?: boolean;
}

const DOC_SUFFIX = /\.(md|txt|json|yaml|yml|py|html|pdf|part|lock|gitattributes)$/i;

/** 取得した中から実際に登録できる重みファイルを選ぶ（README・config・index を先頭にしない）。 */
export function pickWeightFile(
  files: Array<{ path: string; size: number }>, format: string,
): string | null {
  const weights = files.filter((file) => {
    const name = file.path.split("/").pop() ?? file.path;
    if (DOC_SUFFIX.test(name) || /^(readme|license|config|tokenizer|special_tokens_map)/i.test(name)) return false;
    if (format === "gguf") return /\.gguf$/i.test(name);
    if (format === "safetensors") return /\.safetensors$/i.test(name);
    if (format === "rad") return /\.rad$/i.test(name);
    return true;
  });
  if (!weights.length) return null;
  // 分割モデルは先頭（00001-of-000NN）を登録の入口にする。単一は渡された順が先のもの
  // （scheme の files 一覧順。大きさが分かる呼び出しでは size の大きいものが先に来る）。
  const first = weights.filter((file) => /0000?1-of-0*\d+/i.test(file.path));
  const pool = first.length ? first : weights;
  return [...pool].sort((a, b) => b.size - a.size)[0]?.path ?? null;
}

/** 取得結果（README や config を含む全ファイル）から登録の入口だけを残す。 */
const weightPath = (entry: RuntimeCatalogEntry, paths: string[]) =>
  pickWeightFile(paths.map((path) => ({ path, size: 0 })), entry.format) ?? paths[0] ?? "";

/** 取得ジョブの進み具合。速度と残り時間は進捗の増分から画面側で計算する（backend は completed/total を出す）。 */
function AcquireJobCard({ jobId, title, restored, onFinished, onResumed, onDismiss }: {
  jobId: string;
  title: string;
  restored?: boolean;
  onFinished: (job: ModelJob, restored: boolean) => void;
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
  const [confirmCancel, setConfirmCancel] = useState(false);
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
      onFinished(job, !!restored);
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
  return <>
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-acquire-job={jobId}>
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
        {/* 停止済み（interrupted + can_resume）も取り消せる。再開の記録と .part を消す破壊操作なので確認を挟む。 */}
        {(job.status === "running" || job.status === "queued" || (job.status === "interrupted" && !!job.can_resume)) && (
          <button type="button" disabled={cancel.isPending} onClick={() => setConfirmCancel(true)}
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
    </div>
    {confirmCancel && <ConfirmDialog title="取得を取り消す"
      message={`${title} の取得を取り消します。途中のファイル（.part）は削除され、続きから再開できなくなります。`}
      confirmLabel="取り消す"
      onConfirm={() => { setConfirmCancel(false); cancel.mutate(); }}
      onClose={() => setConfirmCancel(false)} />}
  </>;
}

function FitPill({ fit }: { fit: string }) {
  const info = FIT[fit];
  if (!info) return null;
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${info.tone}`}>{info.label}</span>;
}

function Tag({ children, tone = "zinc" }: { children: React.ReactNode; tone?: "zinc" | "amber" | "accent" }) {
  const cls = tone === "amber" ? "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200"
    : tone === "accent" ? "bg-accent-100 text-accent-700 dark:bg-accent-900/40 dark:text-accent-200"
      : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300";
  return <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${cls}`}>{children}</span>;
}

/** カードの 2 行目: 説明・種類の数・大きさの幅・載るか（分かる分だけ）。 */
function summary(model: RuntimeCatalogModel): string {
  const parts: string[] = [];
  if (model.description) parts.push(model.description);
  if (model.variants.length > 1) parts.push(`${model.variants.length} 種類`);
  const min = model.size_min_gib;
  const max = model.size_max_gib;
  if (min != null && max != null) parts.push(min === max ? `約 ${gib(min)}` : `約 ${gib(min)}〜${gib(max)}`);
  return parts.join(" · ");
}

function bestFit(variants: Array<{ fit: string; supported: boolean }>): string {
  const fits = variants.filter((v) => v.supported).map((v) => v.fit);
  if (fits.includes("fits")) return "fits";
  if (fits.includes("slow")) return "slow";
  if (fits.includes("tight")) return "tight";
  if (fits.length && fits.every((f) => f === "no")) return "no";
  return "unknown";
}

interface CardProps {
  runtime: string;
  /** 「無検閲だけ」で絞っているとき、カードを開いたら無検閲の量子化を先に見せる */
  preferUncensored?: boolean;
  downloadRoot?: string;
  model: RuntimeCatalogModel;
  entries: RuntimeCatalogEntry[];
  expert: boolean;
  hardware?: { ram_total_gib: number; vram_total_gib: number };
  jobs: JobEntry[];
  registeredPaths: Set<string>;
  onStartJob: (job: JobEntry) => void;
  onJobDone: (jobId: string) => void;
  onJobResumed: (oldId: string, job: JobEntry) => void;
  onRegister?: (path: string, entry: RuntimeCatalogEntry) => void;
}

function ModelCard({ runtime, model, entries, expert, hardware, jobs, registeredPaths, onStartJob, onJobDone,
  onJobResumed, onRegister, preferUncensored, downloadRoot }: CardProps) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);   // 利用者が選んだ量子化（選ぶまでは「この PC 向け」）
  const [library, setLibrary] = useState("");
  const [busy, setBusy] = useState(false);
  // 系列 → 検閲あり／無検閲 → 量子化（2026-11-28 利用者「大きな種類を選ばせ、検閲・無検閲、量子化を選べるように」）
  const both = !!model.has_uncensored && !!model.has_censored;
  const [mode, setMode] = useState<"censored" | "uncensored">(preferUncensored && model.has_uncensored ? "uncensored" : "censored");
  const canFetchSomething = model.variants.some((v) => v.acquire === "hf" && v.supported);
  const { data: details, isFetching, isError } = useQuery({
    queryKey: ["runtime-catalog-details", runtime, model.id],
    queryFn: () => getRuntimeCatalogDetails(runtime, model.id),
    enabled: open && canFetchSomething, staleTime: 5 * 60_000, retry: false,
  });
  // 目録の値に、開いたときに読んだ大きさ・載るか・取得済みを重ねる
  const variants: Array<RuntimeCatalogVariant & { local_path: string }> = model.variants.map((v) => {
    const d = details?.variants.find((item) => item.id === v.id);
    return d ? { ...v, size_gib: d.size_gib ?? v.size_gib, fit: d.fit, supported: d.supported, reason: d.reason || v.reason,
      state: d.local_path && registeredPaths.has(d.local_path) ? "registered" : d.state, local_path: d.local_path }
      : { ...v, local_path: "" };
  });
  const shown = both ? variants.filter((v) => (mode === "uncensored") === v.uncensored) : variants;
  // この PC 向け: ランタイム本体のおすすめ（Strata）。無ければ、載るうちで一番大きい（精度の高い）量子化
  const recommendedId = (() => {
    const own = shown.find((v) => v.recommended && v.supported);
    if (own) return own.id;
    const usable = shown.filter((v) => v.supported && v.fit === "fits" && (v.size_gib ?? v.memory_gib) != null);
    // 目録が「標準」と言う量子化（Sly の heretic MXFP4 など）が載るなら、それを先に選ぶ
    const standard = usable.find((v) => v.label.includes("標準"));
    if (standard) return standard.id;
    if (!usable.length) return shown[0]?.id;
    return usable.reduce((a, b) => ((b.memory_gib ?? b.size_gib ?? 0) > (a.memory_gib ?? a.size_gib ?? 0) ? b : a)).id;
  })();
  const selected = picked && shown.some((v) => v.id === picked) ? picked : recommendedId ?? shown[0]?.id;
  const setSelected = (id: string) => setPicked(id);
  const variant = shown.find((v) => v.id === selected) ?? shown[0];
  const entry = entries.find((item) => item.id === variant?.id);
  const myJobs = jobs.filter((job) => model.variants.some((v) => v.id === job.entry.id));
  const running = myJobs.some((job) => job.entry.id === variant?.id);
  const anyDownloaded = variants.some((v) => v.state !== "available");
  const fit = bestFit(variants);
  const libraries = (details?.libraries ?? []).filter((item) => item.mounted);
  const ram = hardware?.ram_total_gib;
  const vram = hardware?.vram_total_gib;

  const start = async () => {
    if (!variant || !entry) return;
    setBusy(true);
    try {
      const result = await startRuntimeAcquire({ runtime, entry_id: variant.id, library_id: library, files: [] });
      onStartJob({ id: result.job_id, entry });
      show("取得を始めました（画面を閉じても続きます）");
    } catch (e) {
      show(e instanceof Error ? e.message : "取得を始められませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  // Maya は本体の「ダウンロードして登録」（Maya の量子化・n-gram 表・DSpark などをそろえて登録まで）
  const startMaya = async () => {
    if (!variant || !entry || !downloadRoot) return;
    setBusy(true);
    try {
      const folder = `${downloadRoot.replace(/\/+$/, "")}/${variant.folder_name || variant.repo.split("/").pop()}`;
      const result = await api<{ job_id: string }>("/features/maya-s/models/download-jobs",
        { method: "POST", json: { setup_id: variant.id, folder, create_folder: true } });
      onStartJob({ id: result.job_id, entry });
      show("取得と登録を始めました（画面を閉じても続きます）");
    } catch (e) {
      show(e instanceof Error ? e.message : "取得を始められませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  const action = (() => {
    if (!variant) return null;
    if (!variant.supported) return { label: variant.reason || "このランタイムでは使えません", disabled: true, run: () => {} };
    if (variant.state === "registered") return { label: "登録済み", disabled: true, run: () => {} };
    if (variant.acquire === "runtime") {
      return { label: "登録する（初回の起動で取得）", disabled: !onRegister || !entry,
        run: () => entry && onRegister?.("", entry) };
    }
    if (variant.state === "downloaded" && variant.local_path) {
      return { label: "登録へ進む（取得済み）", disabled: !onRegister || !entry,
        run: () => entry && onRegister?.(variant.local_path, entry) };
    }
    if (variant.acquire === "maya") {
      return { label: busy ? "始めています…" : variant.size_gib != null ? `取得して登録（約 ${gib(variant.size_gib)}）` : "取得して登録",
        disabled: busy || running || !downloadRoot, run: () => void startMaya() };
    }
    if (variant.acquire === "hf") {
      return { label: busy ? "始めています…" : variant.size_gib != null ? `取得する（約 ${gib(variant.size_gib)}）` : "取得する",
        disabled: busy || running, run: () => void start() };
    }
    return { label: "目録から取得できません（手元のファイルから登録してください）", disabled: true, run: () => {} };
  })();

  return (
    <li className={`overflow-hidden rounded-2xl border bg-white transition-colors dark:bg-zinc-900 ${open
      ? "border-accent-300 shadow-sm dark:border-accent-800" : "border-zinc-200 dark:border-zinc-800"}`}
      data-catalog-model={model.id}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left">
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="min-w-0 break-words text-sm font-semibold">{model.name}</span>
            {model.recommended && <Tag tone="accent">おすすめ</Tag>}
            {model.uncensored && <Tag tone="amber">無検閲</Tag>}
            {model.gated && <Tag>要トークン</Tag>}
            {(model.tags ?? []).filter((t) => t !== "画像も読む" || runtime !== "strata").map((t) => <Tag key={t}>{t}</Tag>)}
            {model.has_uncensored && !model.uncensored && <Tag tone="amber">無検閲あり</Tag>}
            {anyDownloaded && <Tag tone="accent">取得済み</Tag>}
          </span>
          {summary(model) && <span className="mt-0.5 block text-xs text-zinc-500 dark:text-zinc-400">{summary(model)}</span>}
        </span>
        <FitPill fit={fit} />
        <IconChevronRight aria-hidden className={`h-5 w-5 shrink-0 text-zinc-400 ${open ? "rotate-90" : ""}`} />
      </button>

      {open && (
        <div className="space-y-3 border-t border-zinc-100 px-4 pb-4 pt-3 dark:border-zinc-800">
          {both && (
            <div role="tablist" aria-label="検閲" className="grid grid-cols-2 gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
              {(["censored", "uncensored"] as const).map((m) => (
                <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => setMode(m)}
                  className={`min-h-10 rounded-lg text-sm ${mode === m
                    ? "bg-white font-semibold shadow-sm dark:bg-zinc-950" : "text-zinc-600 dark:text-zinc-300"}`}>
                  {m === "censored" ? "通常" : "無検閲"}
                </button>
              ))}
            </div>
          )}
          <div role="radiogroup" aria-label={`${model.name} の量子化`} className="space-y-1.5">
            {shown.map((v) => {
              const active = v.id === variant?.id;
              return (
                <button key={v.id} type="button" role="radio" aria-checked={active} onClick={() => setSelected(v.id)}
                  data-catalog-variant={v.id}
                  className={`flex min-h-11 w-full items-center gap-3 rounded-xl border px-3 py-2 text-left ${active
                    ? "border-accent-500 bg-accent-50 dark:border-accent-600 dark:bg-accent-950/30"
                    : "border-zinc-200 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/50"}`}>
                  <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border-2 ${active ? "border-accent-600" : "border-zinc-300 dark:border-zinc-600"}`}>
                    {active && <span className="h-2 w-2 rounded-full bg-accent-600" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className={`text-sm ${v.supported ? "" : "text-zinc-400"}`}>
                        {/* 量子化の名前（UD-Q4_K_XL など）はハイフンで折らない。折るのは語の間だけ */}
                        {v.label.split(" ").map((word, i) => <span key={i}>{i ? " " : ""}<span className="whitespace-nowrap">{word}</span></span>)}
                      </span>
                      {v.uncensored && !model.uncensored && !both && <Tag tone="amber">無検閲</Tag>}
                      {v.id === recommendedId && shown.length > 1 && v.fit === "fits" && <Tag>この PC 向け</Tag>}
                      {v.state === "downloaded" && <Tag tone="accent">取得済み</Tag>}
                      {v.state === "registered" && <Tag tone="accent">登録済み</Tag>}
                    </span>
                    {/* 容量（ダウンロード）と必要メモリ（ランタイム本体の目安）を 1 行に */}
                    <span className="num mt-0.5 block text-[11px] text-zinc-500">
                      {/* 数字と単位の途中で折らない（区切りの「·」の後ろだけで折る） */}
                      {v.size_gib != null ? <span className="whitespace-nowrap">容量 {gib(v.size_gib)}</span>
                        : isFetching && v.acquire === "hf" ? "容量を読んでいます…" : ""}
                      {v.memory_gib != null && <> · <span className="whitespace-nowrap">メモリ {gib(v.memory_gib)}</span></>}
                      {v.maker && <> · <span className="whitespace-nowrap">{v.maker} の版</span></>}
                    </span>
                    {(v.note || (!v.supported && v.reason)) && (
                      <span className="mt-0.5 block text-[11px] text-zinc-500">{v.supported ? v.note : v.reason}</span>
                    )}
                  </span>
                  <FitPill fit={v.supported ? v.fit : ""} />
                </button>
              );
            })}
          </div>
          {isError && <p className="text-[11px] text-zinc-500">大きさを読めませんでした。量子化はそのまま選べます。</p>}

          {variant?.supported && variant.fit === "slow" && (
            <p className="text-[11px] text-amber-700 dark:text-amber-300">GPU に収まらない分を RAM や SSD から読むので、遅くなります。</p>
          )}
          {variant?.supported && variant.fit === "no" && ram != null && vram != null && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              この PC のメモリ（RAM {Math.round(ram)} GB＋VRAM {Math.round(vram)} GB）では足りない見込みです。小さい量子化を選んでください。
            </p>
          )}
          {variant?.acquire === "runtime" && variant.supported && (
            <p className="text-[11px] text-zinc-500">登録すると、最初に起動したときに Strata がモデルを取得します{variant.size_gib != null && `（約 ${gib(variant.size_gib)}）`}。</p>
          )}
          {variant?.acquire === "hf" && variant.state === "available" && libraries.length > 1 && (
            <label className="block text-xs text-zinc-600 dark:text-zinc-300">保存先
              <select aria-label="取得の保存先" value={library} onChange={(e) => setLibrary(e.target.value)}
                className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                <option value="">既定のライブラリ</option>
                {libraries.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
          )}

          {myJobs.map(({ id, entry: jobEntry, restored }) => (
            <AcquireJobCard key={id} jobId={id} title={`${model.name} ${variants.find((v) => v.id === jobEntry.id)?.label ?? ""}`}
              restored={restored}
              onFinished={(job, wasRestored) => {
                if (job.status === "interrupted") {
                  if (!wasRestored) show("停止しました。続きから再開できます", "info");
                  return;
                }
                const files = (job.result?.files as string[] | undefined) ?? [];
                const weight = weightPath(jobEntry, files);
                void qc.invalidateQueries({ queryKey: ["runtime-catalog-details", runtime, model.id] });
                onJobDone(id);
                if (job.status !== "succeeded" || !weight) {
                  if (!wasRestored && job.status !== "canceled") show(job.error || "取得できませんでした", "error");
                  return;
                }
                if (!wasRestored) {
                  show(`${model.name} を取得しました。続けて設定を決めます`);
                  onRegister?.(jobEntry.format === "safetensors" ? dirname(weight) : weight, jobEntry);
                }
              }}
              onDismiss={() => onJobDone(id)}
              onResumed={(newId) => onJobResumed(id, { id: newId, entry: jobEntry })} />
          ))}

          {action && !running && (
            <button type="button" disabled={action.disabled} onClick={action.run} data-testid="catalog-primary"
              className="min-h-12 w-full rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-accent-700 disabled:bg-zinc-200 disabled:text-zinc-500 disabled:shadow-none dark:disabled:bg-zinc-800 dark:disabled:text-zinc-400">
              {action.label}
            </button>
          )}

          {expert && (
            <div className="space-y-1 rounded-xl bg-zinc-50 p-3 text-[11px] text-zinc-500 dark:bg-zinc-800/50" data-catalog-expert>
              {variant?.repo && <p className="break-all font-mono">{variant.repo}</p>}
              {variant?.files.length ? <p className="break-all font-mono">{variant.files.join(", ")}</p> : null}
              {variant?.local_path && <p className="break-all font-mono">{variant.local_path}</p>}
              {model.note_expert && <p>{model.note_expert}</p>}
              <p className="font-mono">{variant?.id}</p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function RuntimeCatalog({ runtime, onRegister, expert: expertProp }: RuntimeCatalogProps) {
  const storedExpert = useExpert();
  const expert = expertProp ?? storedExpert;
  const [jobs, setJobs] = useState<JobEntry[]>([]);
  const [query, setQuery] = useState("");
  const [hideNoFit, setHideNoFit] = useState(true);
  const [onlyUncensored, setOnlyUncensored] = useState(false);

  const { data: catalog, isLoading, error } = useQuery({
    queryKey: ["runtime-catalog"], queryFn: getRuntimeCatalog, staleTime: 60_000,
  });
  const { data: instances } = useQuery({
    queryKey: ["runtime-instances"], queryFn: listRuntimeInstances, staleTime: 30_000,
  });
  // 画面を開き直しても、サーバー側で止まっている取得を再表示する（新しく取得はしない）。
  const { data: savedJobs } = useQuery({
    queryKey: ["runtime-acquire-jobs"], queryFn: () => listModelJobs("model.hf_download"),
    staleTime: 30_000, retry: false,
  });
  const block = catalog?.runtimes.find((item) => item.id === runtime);
  const entries = useMemo(() => block?.entries ?? [], [block]);
  const models = useMemo(() => block?.models ?? [], [block]);
  const instanceRuntime = runtime === "llama" ? "llama.cpp" : runtime;
  const registeredPaths = useMemo(() => new Set((instances?.instances ?? [])
    .filter((item) => item.runtime === instanceRuntime).flatMap((item) => [item.model_path, dirname(item.model_path)])),
  [instances, instanceRuntime]);

  useEffect(() => {
    // 同じ名前の行が別ランタイムにあっても取り違えないよう repo で照合する（無ければ title）。
    const restored: JobEntry[] = (savedJobs ?? []).flatMap((job) => {
      if (job.kind !== "model.hf_download" || ["succeeded", "canceled", "failed"].includes(job.status)) return [];
      const repo = (job.result as { repo?: string } | undefined)?.repo;
      const match = entries.find((entry) => (repo ? repo === entry.repo : job.title.includes(entry.name)));
      return match ? [{ id: job.id, entry: match, restored: true }] : [];
    });
    if (restored.length) {
      setJobs((current) => {
        const known = new Set(current.map((item) => item.id));
        return [...restored.filter((item) => !known.has(item.id)), ...current];
      });
    }
  }, [savedJobs, entries]);

  const shown = models.filter((model) => {
    if (query.trim() && !`${model.name} ${model.description} ${model.variants.map((v) => v.label).join(" ")}`
      .toLowerCase().includes(query.trim().toLowerCase())) return false;
    if (onlyUncensored && !model.uncensored && !model.variants.some((v) => v.uncensored)) return false;
    if (hideNoFit && bestFit(model.variants) === "no") return false;
    return true;
  });
  const hidden = hideNoFit ? models.filter((model) => bestFit(model.variants) === "no").length : 0;
  const anyUncensored = models.some((model) => model.uncensored || model.variants.some((v) => v.uncensored));

  const chip = (on: boolean) => `min-h-9 rounded-full border px-3 text-xs ${on
    ? "border-accent-600 bg-accent-600 text-white"
    : "border-zinc-300 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"}`;

  return (
    <div className="space-y-3" data-runtime-catalog={runtime} data-expert={expert ? "on" : "off"}>
      {error && <p className="text-xs text-red-600">{error instanceof Error ? error.message : "目録を読めませんでした"}</p>}
      {isLoading && <Skeleton className="h-20" />}
      {block && !block.installed && (
        <p className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          このランタイムはまだ導入されていません。導入してから登録してください。
        </p>
      )}
      {models.length > 0 && block?.download_root && (
        <p className="text-[11px] text-zinc-500" data-download-root>
          取得先 <span className="break-all font-mono">{block.download_root}</span>（LLM 共通設定の「モデルの保存場所」の既定。そこで変えられます）
        </p>
      )}
      {models.length > 0 && (
        <div className="space-y-2">
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="モデル名で探す"
            aria-label="モデル名で探す"
            className="min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
          <div className="flex flex-wrap items-center gap-1.5">
            <button type="button" aria-pressed={hideNoFit} onClick={() => setHideNoFit(!hideNoFit)} className={chip(hideNoFit)}>
              載らないものを隠す
            </button>
            {anyUncensored && (
              <button type="button" aria-pressed={onlyUncensored} onClick={() => setOnlyUncensored(!onlyUncensored)} className={chip(onlyUncensored)}>
                無検閲だけ
              </button>
            )}
            <span className="ml-auto text-[11px] text-zinc-500">
              {shown.length} 件{hidden > 0 && `（載らない ${hidden} 件を隠しています）`}
            </span>
          </div>
        </div>
      )}
      {!isLoading && models.length === 0 && (
        <p className="rounded-xl border border-dashed border-zinc-200 p-3 text-xs text-zinc-500 dark:border-zinc-700">
          このランタイムにはおすすめの目録がありません。「手元のファイルから」を使ってください。
        </p>
      )}
      <ul className="space-y-2">
        {shown.map((model) => (
          <ModelCard key={model.id} runtime={runtime} model={model} entries={entries} expert={expert}
            hardware={catalog?.hardware} jobs={jobs} registeredPaths={registeredPaths}
            onStartJob={(job) => setJobs((current) => [job, ...current])}
            onJobDone={(jobId) => setJobs((current) => current.filter((item) => item.id !== jobId))}
            onJobResumed={(oldId, job) => setJobs((current) => [job, ...current.filter((item) => item.id !== oldId)])}
            onRegister={onRegister} preferUncensored={onlyUncensored} downloadRoot={block?.download_root} />
        ))}
      </ul>
      {block?.catalog_ref && expert && (
        <p className="text-[11px] text-zinc-500">全部の候補はランタイム本体の目録にあります（<span className="font-mono">{block.catalog_ref}</span>）。</p>
      )}
    </div>
  );
}
