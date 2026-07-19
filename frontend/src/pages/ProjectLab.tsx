import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { projectLabApi, type ProjectLabArtifact, type ProjectLabDetail, type ProjectLabRun } from "../api/projectLab";
import { PageHeader } from "../components/PageHeader";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export default function ProjectLabPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const { data: projects = [], isLoading, error } = useQuery({ queryKey: ["project-lab"], queryFn: projectLabApi.list });
  useEffect(() => {
    if (!selected && projects.length > 0 && matchMedia("(min-width: 768px)").matches) setSelected(projects[0].id);
  }, [projects, selected]);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <PageHeader title="Project Lab" description="~/CodeDEVの開発成果物を自動検出し、安全なread-only previewで評価します。" className="mb-0 shrink-0 border-b border-zinc-200 px-4 py-4 dark:border-zinc-800 md:px-6" />
      <div className="grid min-h-0 flex-1 md:grid-cols-[20rem_minmax(0,1fr)]">
        <aside className={`${selected ? "hidden md:block" : "block"} min-h-0 overflow-y-auto border-r border-zinc-200 p-3 dark:border-zinc-800`} aria-label="CodeDEVプロジェクト一覧">
          {isLoading && <p className="p-3 text-sm text-zinc-400">検出中...</p>}
          {error && <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{error instanceof Error ? error.message : "検出に失敗しました"}</p>}
          {!isLoading && !error && projects.length === 0 && <div className="rounded-xl border border-dashed border-zinc-300 p-5 text-sm text-zinc-500 dark:border-zinc-700"><strong className="block text-zinc-700 dark:text-zinc-200">プロジェクトがありません</strong><span className="mt-1 block">~/CodeDEV直下へproject folderを置くと自動表示されます。実行は自動開始しません。</span></div>}
          <div className="space-y-2">
            {projects.map((project) => <button key={project.id} type="button" onClick={() => setSelected(project.id)} className={`min-h-11 w-full rounded-xl border p-3 text-left ${selected === project.id ? "border-accent-400 bg-accent-50 dark:bg-accent-950/30" : "border-zinc-200 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900"}`}>
              <span className="block truncate text-sm font-semibold">{project.name}</span>
              <span className="mt-1 flex flex-wrap gap-1 text-[10px] text-zinc-500"><span>{project.artifactCount} 成果物</span><span>·</span><span>{project.profileCount} profile</span>{project.git && <><span>·</span><span>{project.git.branch}{project.git.dirty ? " *" : ""}</span></>}</span>
              <span className="mt-1 flex flex-wrap gap-1">{project.technologies.slice(0, 5).map((item) => <span key={item} className="rounded bg-zinc-100 px-1.5 py-0.5 text-[9px] text-zinc-500 dark:bg-zinc-800">{item}</span>)}</span>
            </button>)}
          </div>
        </aside>
        <main className={`${selected ? "block" : "hidden md:block"} min-h-0 min-w-0 overflow-y-auto`}>
          {selected ? <ProjectWorkspace projectId={selected} onBack={() => setSelected(null)} /> : <div className="grid h-full place-items-center p-8 text-sm text-zinc-400">左からprojectを選択してください</div>}
        </main>
      </div>
    </div>
  );
}

function ProjectWorkspace({ projectId, onBack }: { projectId: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["project-lab", projectId], queryFn: () => projectLabApi.detail(projectId) });
  const runsQuery = useQuery({ queryKey: ["project-lab-runs", projectId], queryFn: () => projectLabApi.runs(projectId), refetchInterval: 2000 });
  const startRun = useMutation({
    mutationFn: ({ profileId, profileType }: { profileId: string; profileType: string }) => projectLabApi.startRun(projectId, profileId, profileType === "web" ? 3600 : 600),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["project-lab-runs", projectId] }),
  });
  const [artifactPath, setArtifactPath] = useState<string | null>(null);
  useEffect(() => setArtifactPath(null), [projectId]);
  const selectedArtifact = useMemo(() => data?.artifacts.find((item) => item.path === artifactPath) ?? data?.artifacts[0], [artifactPath, data]);
  if (isLoading) return <p className="p-5 text-sm text-zinc-400">読み込み中...</p>;
  if (error || !data) return <p className="m-4 rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{error instanceof Error ? error.message : "projectを開けません"}</p>;
  return <div className="mx-auto max-w-7xl p-3 pb-[max(1rem,env(safe-area-inset-bottom))] md:p-6">
    <button type="button" onClick={onBack} className="mb-3 min-h-11 rounded-xl px-2 text-sm text-accent-600 md:hidden">← プロジェクト一覧</button>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0"><h2 className="truncate text-xl font-semibold">{data.name}</h2><p className="mt-1 break-all font-mono text-[10px] text-zinc-400">{data.path}</p>{data.description && <p className="mt-2 text-sm text-zinc-500">{data.description}</p>}</div>
      <span className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">成果物preview · 明示実行</span>
    </div>
    {data.diagnostics.map((diagnostic) => <div key={`${diagnostic.code}-${diagnostic.message}`} className="mt-3 rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300"><strong>{diagnostic.code}</strong> {diagnostic.message}</div>)}
    <section className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <InfoCard label="技術" value={data.technologies.join(" · ") || "未検出"} />
      <InfoCard label="Git" value={data.git ? `${data.git.branch}${data.git.dirty ? "（変更あり）" : data.git.dirty === false ? "（clean）" : ""}` : "未使用"} />
      <InfoCard label="実行profile" value={`${data.manifest?.profiles.filter((item) => ["cli", "test", "web"].includes(item.type)).length ?? 0}（CLI / test / Web）`} />
      <InfoCard label="成果物" value={`${data.artifacts.length} files`} />
    </section>
    {data.manifest?.profiles.length ? <section className="mt-4"><h3 className="mb-2 text-sm font-semibold">検出したprofile</h3><div className="grid gap-2 md:grid-cols-2">{data.manifest.profiles.map((profile) => {
      const runnable = ["cli", "test", "web"].includes(profile.type) && profile.command.length > 0 && profile.secretRefs.length === 0;
      return <div key={profile.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex items-center justify-between gap-2"><strong className="text-sm">{profile.label}</strong><span className="rounded bg-zinc-100 px-2 py-1 text-[10px] dark:bg-zinc-800">{profile.type}</span></div><p className="mt-1 break-all font-mono text-[10px] text-zinc-400">{profile.command.length ? profile.command.join(" ") : "commandなし"}</p><div className="mt-2 flex items-center justify-between gap-2"><span className="text-[10px] text-zinc-500">{profile.secretRefs.length ? "Secret注入は後続Phase" : runnable ? profile.type === "web" ? "localhost限定で起動し、安全なproxyで表示" : "隔離されたsystemd user serviceで実行" : "このprofileはpreview専用"}</span><button type="button" disabled={!runnable || startRun.isPending} onClick={() => startRun.mutate({ profileId: profile.id, profileType: profile.type })} className="min-h-11 shrink-0 rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">{profile.type === "web" ? "起動" : "実行"}</button></div></div>;
    })}</div>{startRun.error && <p className="mt-2 rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{startRun.error.message}</p>}</section> : null}
    <RunHistory projectId={projectId} runs={runsQuery.data ?? []} />
    <section className="mt-5"><h3 className="mb-2 text-sm font-semibold">成果物</h3>{data.artifacts.length === 0 ? <p className="rounded-xl border border-dashed border-zinc-300 p-4 text-sm text-zinc-500 dark:border-zinc-700">HTML、画像、CSV、JSON、Markdown、PDF、audio/video、logなどの成果物はまだありません。</p> : <div className="grid min-w-0 gap-3 lg:grid-cols-[17rem_minmax(0,1fr)]"><div className="max-h-96 space-y-1 overflow-y-auto rounded-xl border border-zinc-200 p-2 dark:border-zinc-800">{data.artifacts.map((artifact) => <button key={artifact.path} type="button" onClick={() => setArtifactPath(artifact.path)} className={`min-h-11 w-full rounded-lg px-2.5 py-2 text-left ${selectedArtifact?.path === artifact.path ? "bg-accent-50 text-accent-800 dark:bg-accent-950/30 dark:text-accent-300" : "hover:bg-zinc-50 dark:hover:bg-zinc-900"}`}><span className="block truncate text-xs font-medium">{artifact.name}</span><span className="mt-0.5 block truncate font-mono text-[9px] text-zinc-400">{artifact.kind} · {formatBytes(artifact.size)} · {artifact.path}</span></button>)}</div>{selectedArtifact && <ArtifactPreview project={data} artifact={selectedArtifact} />}</div>}</section>
  </div>;
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><p className="text-[10px] font-medium uppercase tracking-wide text-zinc-400">{label}</p><p className="mt-1 break-words text-sm">{value}</p></div>;
}

function RunHistory({ projectId, runs }: { projectId: string; runs: ProjectLabRun[] }) {
  const queryClient = useQueryClient();
  const [openRun, setOpenRun] = useState<number | null>(null);
  const logs = useQuery({
    queryKey: ["project-lab-run-logs", openRun],
    queryFn: () => projectLabApi.runLogs(openRun as number),
    enabled: openRun !== null,
    refetchInterval: openRun !== null && runs.some((run) => run.id === openRun && ["QUEUED", "RUNNING"].includes(run.status)) ? 1500 : false,
  });
  const cancel = useMutation({
    mutationFn: projectLabApi.cancelRun,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["project-lab-runs", projectId] }),
  });
  if (runs.length === 0) return null;
  return <section className="mt-5">
    <h3 className="mb-2 text-sm font-semibold">実行履歴</h3>
    <div className="space-y-2">{runs.map((run) => {
      const active = ["QUEUED", "RUNNING"].includes(run.status);
      const okay = run.status === "SUCCEEDED";
      return <div key={run.id} className="overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
        <div className="flex min-h-11 flex-wrap items-center gap-2 px-3 py-2">
          <button type="button" onClick={() => setOpenRun(openRun === run.id ? null : run.id)} className="min-h-11 min-w-0 flex-1 text-left">
            <span className="block truncate text-xs font-semibold">#{run.id} · {run.profileId}</span>
            <span className="block text-[10px] text-zinc-500">{new Date(run.startedAt).toLocaleString()} · {run.elapsedMs === null ? "実行中" : `${run.elapsedMs} ms`}</span>
          </button>
          <span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${okay ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : active ? "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" : "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300"}`}>{run.status}</span>
          {active && <button type="button" onClick={() => cancel.mutate(run.id)} disabled={cancel.isPending} className="min-h-11 rounded-xl px-3 text-xs font-medium text-red-600 disabled:opacity-40">停止</button>}
        </div>
        {openRun === run.id && <div className="border-t border-zinc-200 p-3 dark:border-zinc-800">
          {run.error && <p className="mb-2 rounded-lg bg-red-50 p-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{run.error}</p>}
          {run.profileType === "web" && run.previewReady && run.previewUrl && <iframe title={`${run.projectName} Web preview`} src={run.previewUrl} sandbox="allow-scripts allow-forms allow-downloads" className="mb-2 h-[65vh] min-h-80 w-full rounded-lg bg-white" />}
          {run.profileType === "web" && active && !run.previewReady && <p className="mb-2 rounded-lg bg-blue-50 p-2 text-xs text-blue-700 dark:bg-blue-950/30 dark:text-blue-300">Web applicationの待受開始を確認しています…</p>}
          {run.artifacts.length > 0 && <div className="mb-2 flex flex-wrap gap-1">{run.artifacts.map((artifact) => <span key={artifact.id} className="rounded bg-zinc-100 px-2 py-1 text-[10px] dark:bg-zinc-800">{artifact.changeType}: {artifact.path}</span>)}</div>}
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-950 p-3 font-mono text-[11px] text-zinc-100">{logs.isLoading ? "ログを読み込み中..." : logs.data?.logs || "ログはありません"}</pre>
        </div>}
      </div>;
    })}</div>
  </section>;
}

function ArtifactPreview({ project, artifact }: { project: ProjectLabDetail; artifact: ProjectLabArtifact }) {
  const url = projectLabApi.artifactUrl(project.id, artifact.path);
  const download = projectLabApi.artifactUrl(project.id, artifact.path, true);
  const textual = ["table", "json", "markdown", "log", "text"].includes(artifact.kind);
  const { data: preview, isLoading } = useQuery({
    queryKey: ["project-lab-preview", project.id, artifact.path],
    queryFn: () => projectLabApi.preview(project.id, artifact.path),
    enabled: textual,
  });
  return <div className="min-w-0 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
    <div className="flex min-h-11 items-center justify-between gap-2 border-b border-zinc-200 px-3 dark:border-zinc-800"><div className="min-w-0"><strong className="block truncate text-xs">{artifact.name}</strong><span className="block truncate font-mono text-[9px] text-zinc-400">{artifact.mimeType}</span></div><a href={download} className="shrink-0 rounded-lg px-2 py-2 text-xs font-medium text-accent-600">保存</a></div>
    <div className="min-h-64 bg-zinc-50 p-3 dark:bg-zinc-950">
      {textual && isLoading && <p className="text-sm text-zinc-400">previewを読み込み中...</p>}
      {artifact.kind === "html" && <iframe title={`${artifact.name} preview`} src={url} sandbox="" className="h-[60vh] min-h-80 w-full rounded-lg bg-white" />}
      {artifact.kind === "image" && <img src={url} alt={artifact.name} className="mx-auto max-h-[65vh] max-w-full object-contain" />}
      {artifact.kind === "pdf" && <iframe title={`${artifact.name} PDF`} src={url} className="h-[65vh] w-full rounded-lg bg-white" />}
      {artifact.kind === "audio" && <audio src={url} controls className="w-full" />}
      {artifact.kind === "video" && <video src={url} controls className="max-h-[65vh] w-full" />}
      {artifact.kind === "table" && preview && <TablePreview value={preview.structuredPreview} />}
      {artifact.kind === "json" && preview && <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{JSON.stringify(preview.structuredPreview, null, 2) || preview.previewText}</pre>}
      {["markdown", "log", "text"].includes(artifact.kind) && preview && <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">{preview.previewText ?? "preview size上限を超えています。保存して確認してください。"}</pre>}
    </div>
  </div>;
}

function TablePreview({ value }: { value: unknown }) {
  const table = value as { headers?: string[]; rows?: string[][]; truncated?: boolean } | null;
  if (!table?.headers) return <p className="text-sm text-zinc-500">表を解析できませんでした。</p>;
  return <div className="overflow-auto"><table className="min-w-full border-collapse text-xs"><thead><tr>{table.headers.map((header, index) => <th key={`${header}-${index}`} className="border border-zinc-200 bg-zinc-100 px-2 py-1.5 text-left dark:border-zinc-700 dark:bg-zinc-900">{header}</th>)}</tr></thead><tbody>{table.rows?.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} className="max-w-64 break-words border border-zinc-200 px-2 py-1.5 dark:border-zinc-700">{cell}</td>)}</tr>)}</tbody></table>{table.truncated && <p className="mt-2 text-xs text-amber-600">先頭200行だけ表示しています。</p>}</div>;
}
