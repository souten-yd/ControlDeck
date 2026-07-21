import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applicationBuilderApi,
  type ApplicationBuild,
  type ApplicationProject,
  type CapabilityCatalog,
} from "../../api/applicationBuilder";
import { ConfirmDialog, DropdownMenu, type MenuItem } from "../../components/ui";
import { IconDots, IconDownload, IconStop, IconTest } from "../../components/icons";
import { useToasts } from "../../stores";

const ACTIVE = new Set<ApplicationBuild["status"]>([
  "queued", "preparing", "generating", "restoring", "building", "testing", "canceling",
]);
const TERMINAL = new Set<ApplicationBuild["status"]>([
  "completed", "failed", "cancelled", "timed_out", "interrupted",
]);
const PHASES: ApplicationBuild["status"][] = ["queued", "generating", "restoring", "building", "testing", "completed"];
const LABELS: Record<ApplicationBuild["status"], string> = {
  queued: "Queued", preparing: "Preparing", generating: "Generating", restoring: "Restoring",
  building: "Building", testing: "Testing", canceling: "Canceling", completed: "Completed",
  failed: "Failed", cancelled: "Cancelled", timed_out: "Timed out", interrupted: "Interrupted",
};

export function BuildPanel({
  project,
  targetId,
  capabilities,
}: {
  project: ApplicationProject;
  targetId: string;
  capabilities?: CapabilityCatalog;
}) {
  const queryClient = useQueryClient();
  const show = useToasts((state) => state.show);
  const [openLogs, setOpenLogs] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ApplicationBuild | null>(null);
  const builds = useQuery({
    queryKey: ["application-builds", project.id],
    queryFn: () => applicationBuilderApi.builds(project.id),
    refetchInterval: (query) => query.state.data?.some((item) => ACTIVE.has(item.status)) ? 900 : false,
  });
  const start = useMutation({
    mutationFn: () => applicationBuilderApi.startBuild(project.id, targetId),
    onSuccess: async (build) => {
      show(`Build #${build.id}を開始しました`);
      await queryClient.invalidateQueries({ queryKey: ["application-builds", project.id] });
    },
    onError: (reason) => show(errorMessage(reason, "Buildを開始できませんでした"), "error"),
  });
  const cancel = useMutation({
    mutationFn: (build: ApplicationBuild) => applicationBuilderApi.cancelBuild(build.id),
    onSuccess: async (build) => {
      show(`Build #${build.id}をキャンセルしました`);
      await queryClient.invalidateQueries({ queryKey: ["application-builds", project.id] });
    },
    onError: (reason) => show(errorMessage(reason, "Buildをキャンセルできませんでした"), "error"),
  });
  const remove = useMutation({
    mutationFn: (build: ApplicationBuild) => applicationBuilderApi.removeBuild(build.id),
    onSuccess: async (_, build) => {
      setDeleteTarget(null);
      if (openLogs === build.id) setOpenLogs(null);
      show(`Build #${build.id}の履歴と成果物を削除しました`);
      await queryClient.invalidateQueries({ queryKey: ["application-builds", project.id] });
    },
    onError: (reason) => show(errorMessage(reason, "Buildを削除できませんでした"), "error"),
  });
  const logQuery = useQuery({
    queryKey: ["application-build-log", openLogs],
    queryFn: () => applicationBuilderApi.buildLogs(openLogs!),
    enabled: openLogs !== null,
    refetchInterval: (query) => query.state.data && ACTIVE.has(query.state.data.status) ? 1200 : false,
  });

  const history = builds.data ?? [];
  const activeBuild = history.find((item) => ACTIVE.has(item.status));
  const available = Boolean(capabilities?.build.available);
  const disabledReason = !targetId
    ? "先にTargetを保存してください。"
    : capabilities && !available
      ? capabilities.build.note
      : "Build環境を確認しています…";

  return <section aria-label="Isolated build" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-violet-50 text-violet-600 dark:bg-violet-950/40 dark:text-violet-300"><IconTest /></span>
          <div><h2 className="text-sm font-semibold">Build & test</h2><p className="text-[10px] text-zinc-400">Isolated · network denied</p></div>
        </div>
        <p className="mt-3 text-xs leading-relaxed text-zinc-500">保存済みSourceを一時systemd user unitで復元・ビルド・自己テストします。SDK実行は固定引数、2GBメモリ、15分で制限され、成果物と伏字化ログだけを保持します。</p>
      </div>
      <button
        type="button"
        onClick={() => start.mutate()}
        disabled={!available || !targetId || Boolean(activeBuild) || start.isPending}
        className="min-h-11 w-full rounded-xl bg-violet-600 px-4 text-xs font-semibold text-white shadow-sm transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
      >
        {start.isPending ? "Starting…" : activeBuild ? `Build #${activeBuild.id} running` : "Build & test"}
      </button>
    </div>

    {!available && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"><strong className="block">Local build unavailable</strong><span className="mt-1 block opacity-80">{disabledReason}</span></div>}
    {available && <dl className="mt-3 grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4">
      <BuildFact label="SDK" value={capabilities?.build.sdk ?? ".NET"} />
      <BuildFact label="Isolation" value="systemd user" />
      <BuildFact label="Network" value="Denied" />
      <BuildFact label="Parallel" value={`${capabilities?.build.maxConcurrent ?? 1} max`} />
    </dl>}

    <div className="mt-5 border-t border-zinc-100 pt-4 dark:border-zinc-800">
      <div className="flex items-center justify-between gap-3"><h3 className="text-xs font-semibold">Build history</h3>{builds.isFetching && <span role="status" className="text-[10px] text-zinc-400">Updating…</span>}</div>
      {builds.isError ? <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{errorMessage(builds.error, "履歴を読み込めませんでした")}</p>
        : history.length === 0 ? <p className="mt-3 rounded-xl bg-zinc-50 p-3 text-xs text-zinc-500 dark:bg-zinc-950">まだBuildはありません。Sourceの確認後、上の主操作から開始できます。</p>
          : <div className="mt-3 space-y-3">{history.map((build) => <BuildCard
            key={build.id}
            build={build}
            logs={openLogs === build.id ? logQuery.data?.logs : undefined}
            logsLoading={openLogs === build.id && logQuery.isLoading}
            onToggleLogs={() => setOpenLogs((current) => current === build.id ? null : build.id)}
            onCancel={() => cancel.mutate(build)}
            onDelete={() => setDeleteTarget(build)}
            cancelBusy={cancel.isPending && cancel.variables?.id === build.id}
          />)}</div>}
    </div>
    {deleteTarget && <ConfirmDialog
      title={`Build #${deleteTarget.id}を削除`}
      message="このBuild履歴、ログ参照、保存されたSource ZIPと成果物を削除します。元に戻せません。"
      confirmLabel="Buildを削除"
      busy={remove.isPending}
      onConfirm={() => remove.mutate(deleteTarget)}
      onClose={() => !remove.isPending && setDeleteTarget(null)}
    />}
  </section>;
}

function BuildCard({ build, logs, logsLoading, onToggleLogs, onCancel, onDelete, cancelBusy }: {
  build: ApplicationBuild; logs?: string; logsLoading: boolean;
  onToggleLogs: () => void; onCancel: () => void; onDelete: () => void; cancelBusy: boolean;
}) {
  const active = ACTIVE.has(build.status);
  const completed = build.status === "completed";
  const progress = completed ? 100 : Math.max(8, (PHASES.indexOf(build.status) + 1) * 17);
  const menu: MenuItem[] = [{ label: logs === undefined ? "Show logs" : "Hide logs", onSelect: onToggleLogs }];
  if (TERMINAL.has(build.status)) menu.push({ label: "Delete build", danger: true, onSelect: onDelete });
  return <article className="overflow-hidden rounded-2xl border border-zinc-200 bg-zinc-50/70 dark:border-zinc-800 dark:bg-zinc-950/50">
    <div className="p-3">
      <div className="flex items-start gap-2">
        <span aria-hidden="true" className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${statusDot(build.status)} ${active ? "animate-pulse" : ""}`} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2"><strong className="text-xs">Build #{build.id}</strong><span className={`text-[10px] font-semibold ${statusText(build.status)}`}>{LABELS[build.status]}</span></div>
          <p className="mt-0.5 truncate text-[10px] text-zinc-400">{build.targetId} · {formatDate(build.createdAt)}</p>
        </div>
        {active && <button type="button" onClick={onCancel} disabled={cancelBusy || build.status === "canceling"} className="flex min-h-11 items-center gap-1.5 rounded-xl border border-zinc-300 px-3 text-[10px] font-semibold text-zinc-600 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300"><IconStop />{cancelBusy ? "Stopping…" : "Cancel"}</button>}
        <DropdownMenu ariaLabel={`Build #${build.id} actions`} trigger={<IconDots />} items={menu} />
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800" role="progressbar" aria-label={`Build #${build.id} progress`} aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}><div className={`h-full rounded-full transition-all duration-300 ${completed ? "bg-emerald-500" : active ? "bg-violet-500" : "bg-zinc-400"}`} style={{ width: `${progress}%` }} /></div>
      {build.error && <p role="alert" className="mt-3 rounded-lg bg-red-50 p-2 text-[10px] leading-relaxed text-red-700 dark:bg-red-950/30 dark:text-red-300">{build.error}</p>}
      {build.artifacts.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{build.artifacts.map((artifact) => <a key={artifact.id} href={applicationBuilderApi.artifactUrl(build.id, artifact.id)} download className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-xl border border-zinc-300 bg-white px-3 text-[10px] font-semibold hover:border-violet-400 dark:border-zinc-700 dark:bg-zinc-900"><IconDownload className="shrink-0" /><span className="truncate">{artifact.kind === "source" ? "Source ZIP" : artifact.path.split("/").slice(-1)[0]}</span><span className="shrink-0 text-zinc-400">{formatBytes(artifact.size)}</span></a>)}</div>}
    </div>
    {(logs !== undefined || logsLoading) && <div className="border-t border-zinc-200 bg-zinc-950 p-3 dark:border-zinc-800"><div className="mb-2 flex items-center justify-between"><strong className="text-[10px] text-zinc-300">Build log · last 2,000 lines</strong><button type="button" onClick={onToggleLogs} className="min-h-11 px-2 text-[10px] text-zinc-400">Close</button></div><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-zinc-300">{logsLoading ? "Loading…" : logs || "No log output."}</pre></div>}
  </article>;
}

function BuildFact({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl bg-zinc-50 p-2 dark:bg-zinc-950"><dt className="text-zinc-400">{label}</dt><dd className="mt-1 truncate font-semibold">{value}</dd></div>;
}

function statusDot(status: ApplicationBuild["status"]): string {
  if (status === "completed") return "bg-emerald-500";
  if (["failed", "timed_out"].includes(status)) return "bg-red-500";
  if (ACTIVE.has(status)) return "bg-violet-500";
  return "bg-amber-500";
}
function statusText(status: ApplicationBuild["status"]): string {
  if (status === "completed") return "text-emerald-600 dark:text-emerald-400";
  if (["failed", "timed_out"].includes(status)) return "text-red-600 dark:text-red-400";
  if (ACTIVE.has(status)) return "text-violet-600 dark:text-violet-300";
  return "text-amber-600 dark:text-amber-300";
}
function formatDate(value: string): string { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function formatBytes(value: number): string { return value < 1024 ? `${value} B` : value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`; }
function errorMessage(reason: unknown, fallback: string): string { return reason instanceof Error ? reason.message : fallback; }
