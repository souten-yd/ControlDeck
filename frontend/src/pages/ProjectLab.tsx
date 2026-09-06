import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../stores";
import {
  projectLabApi,
  type ProjectLabArtifact,
  type ProjectLabDetail,
  type ProjectLabRun,
  type ProjectLabSummary,
  type ProjectLabPublishState,
} from "../api/projectLab";
import { CodeViewer } from "../features/projectlab/CodeViewer";
import { BottomSheet, Popover, Skeleton } from "../components/ui";
import { IconDots, IconDownload, IconPlay, IconRestart, IconSearch, IconStop, IconX } from "../components/icons";
import { useToasts } from "../stores";
import { ContextActionsMenu } from "../features/addons/ContextActionsMenu";

type SheetKind = "projects" | "files" | "info" | "runs" | null;

const TEXTUAL: ProjectLabArtifact["kind"][] = ["code", "text", "markdown", "log", "json", "table"];
const ACTIVE_STATES = ["QUEUED", "RUNNING"];

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

const KIND_TONES: Record<string, string> = {
  html: "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300",
  code: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  image: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  table: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  json: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  markdown: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300",
  pdf: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300",
  audio: "bg-pink-100 text-pink-700 dark:bg-pink-500/15 dark:text-pink-300",
  video: "bg-pink-100 text-pink-700 dark:bg-pink-500/15 dark:text-pink-300",
};

function KindBadge({ artifact, size = "sm" }: { artifact: ProjectLabArtifact; size?: "sm" | "xs" }) {
  const extension = (artifact.name.split(".").pop() ?? "?").toUpperCase().slice(0, 4);
  const tone = KIND_TONES[artifact.kind] ?? "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300";
  return (
    <span className={`grid shrink-0 place-items-center rounded-lg font-semibold ${tone} ${size === "sm" ? "h-9 w-9 text-[9px]" : "h-6 w-8 text-[8px]"}`}>
      {extension}
    </span>
  );
}

/** 成果物のうち最初に見せるべきものを選ぶ（HTML → 画像 → それ以外の順）。 */
function pickDefaultArtifact(artifacts: ProjectLabArtifact[]): ProjectLabArtifact | undefined {
  return artifacts.find((item) => item.name.toLowerCase() === "index.html")
    ?? artifacts.find((item) => item.kind === "html")
    ?? artifacts.find((item) => item.kind === "image")
    ?? artifacts[0];
}

export default function ProjectLabPage() {
  const show = useToasts((state) => state.show);
  const queryClient = useQueryClient();
  const can = useAuth((state) => state.can);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [artifactPath, setArtifactPath] = useState<string | null>(null);
  const [sheet, setSheet] = useState<SheetKind>(null);
  const [sourceMode, setSourceMode] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  // 既定は実寸。枠と同じ幅で描画するとページ側のmedia queryとタッチ判定が実機どおりに働く。
  // 「全体」はPC幅1024pxで描いて縮小する全景モードで、操作より確認を優先したいとき用。
  const [fitWidth, setFitWidth] = useState(false);
  // 外部CDNの読み込みは既定で遮断し、利用者が明示的に許可したファイルだけ通す。
  const [externalAllowed, setExternalAllowed] = useState<Record<string, boolean>>({});
  const [openRun, setOpenRun] = useState<number | null>(null);

  const projectsQuery = useQuery({ queryKey: ["project-lab"], queryFn: projectLabApi.list });
  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);
  const detailQuery = useQuery({
    queryKey: ["project-lab", projectId],
    queryFn: () => projectLabApi.detail(projectId as string),
    enabled: projectId !== null,
  });
  const detail = detailQuery.data;
  const settingsQuery = useQuery({ queryKey: ["project-lab-settings"], queryFn: projectLabApi.settings });
  const allowExternalAlways = settingsQuery.data?.allow_external_preview === true;
  // 置き場は設定で変わるので、案内文はサーバーが返す実際のパスを出す。
  const projectRoot = settingsQuery.data?.project_root ?? "プロジェクトフォルダ";
  const saveSettings = useMutation({
    mutationFn: (allow: boolean) => projectLabApi.saveSettings({ allow_external_preview: allow }),
    onSuccess: async (settings) => {
      queryClient.setQueryData(["project-lab-settings"], settings);
      setReloadToken((value) => value + 1);
      show(settings.allow_external_preview ? "外部CDNを常に許可します" : "外部CDNの読み込みを遮断します");
    },
    onError: (error) => show(error instanceof Error ? error.message : "設定を保存できません", "error"),
  });

  const runsQuery = useQuery({
    queryKey: ["project-lab-runs", projectId],
    queryFn: () => projectLabApi.runs(projectId as string),
    enabled: projectId !== null,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((run) => ACTIVE_STATES.includes(run.status)) ? 1500 : false,
  });
  const runs = useMemo(() => runsQuery.data ?? [], [runsQuery.data]);
  const activeRun = runs.find((run) => ACTIVE_STATES.includes(run.status)) ?? null;

  useEffect(() => {
    if (projectId === null && projects.length > 0) setProjectId(projects[0].id);
  }, [projects, projectId]);
  useEffect(() => {
    setArtifactPath(null);
    setSourceMode(false);
  }, [projectId]);

  const artifacts = useMemo(() => detail?.artifacts ?? [], [detail]);
  const artifact = useMemo(
    () => artifacts.find((item) => item.path === artifactPath) ?? pickDefaultArtifact(artifacts),
    [artifacts, artifactPath],
  );

  const startFileRun = useMutation({
    mutationFn: (target: ProjectLabArtifact) => projectLabApi.startFileRun(projectId as string, target.path),
    onSuccess: (run) => {
      setOpenRun(run.id);
      setSheet("runs");
      void queryClient.invalidateQueries({ queryKey: ["project-lab-runs", projectId] });
    },
    onError: (error) => show(error instanceof Error ? error.message : "実行を開始できません", "error"),
  });
  const startProfileRun = useMutation({
    mutationFn: (profile: { id: string; type: string }) =>
      projectLabApi.startRun(projectId as string, profile.id, profile.type === "web" ? 3600 : 600),
    onSuccess: (run) => {
      setOpenRun(run.id);
      setSheet("runs");
      void queryClient.invalidateQueries({ queryKey: ["project-lab-runs", projectId] });
    },
    onError: (error) => show(error instanceof Error ? error.message : "実行を開始できません", "error"),
  });

  const selectArtifact = (path: string) => {
    setArtifactPath(path);
    setSourceMode(false);
    setSheet(null);
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-zinc-100 dark:bg-zinc-950">
      <header className="flex h-12 shrink-0 items-center gap-2 px-2 md:px-3">
        <div className="relative min-w-0 max-w-[60%]">
          <button
            type="button"
            aria-haspopup="dialog"
            aria-expanded={sheet === "projects"}
            onClick={() => setSheet(sheet === "projects" ? null : "projects")}
            className="flex min-h-10 w-full min-w-0 items-center gap-1.5 rounded-xl px-2.5 text-left hover:bg-white dark:hover:bg-zinc-900"
          >
            <span className="min-w-0 truncate text-sm font-semibold">{detail?.name ?? "Project Lab"}</span>
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 text-zinc-400 transition-transform ${sheet === "projects" ? "rotate-180" : ""}`} aria-hidden>
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          {/* 起点のボタン直下（左上基点）から開く。中央のシートだと視線と操作位置がずれる。 */}
          <Popover open={sheet === "projects"} label="プロジェクト" onClose={() => setSheet(null)}>
            <p className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">プロジェクト</p>
            {projects.map((project) => (
              <ProjectRow
                key={project.id}
                project={project}
                selected={project.id === projectId}
                onSelect={() => {
                  setProjectId(project.id);
                  setSheet(null);
                }}
              />
            ))}
            {projects.length === 0 && (
              <p className="px-2.5 py-4 text-xs leading-relaxed text-zinc-500">{projectRoot} にフォルダを置くと自動で表示されます。</p>
            )}
          </Popover>
        </div>
        <div className="ml-auto flex items-center gap-1">
          {projectId && <ContextActionsMenu contextType="project" resourceId={projectId} />}
          <button
            type="button"
            onClick={() => setSheet("runs")}
            aria-label="実行ログ"
            className="relative grid h-10 w-10 place-items-center rounded-xl text-zinc-500 hover:bg-white dark:hover:bg-zinc-900"
          >
            <IconPlay />
            {activeRun && <span className="absolute right-2 top-2 h-2 w-2 animate-pulse rounded-full bg-emerald-500" />}
          </button>
          <div className="relative">
            <button
              type="button"
              onClick={() => setSheet(sheet === "info" ? null : "info")}
              aria-label="プロジェクト情報"
              aria-haspopup="dialog"
              aria-expanded={sheet === "info"}
              className="grid h-10 w-10 place-items-center rounded-xl text-zinc-500 hover:bg-white dark:hover:bg-zinc-900"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" />
              </svg>
            </button>
            <Popover open={sheet === "info" && detail !== undefined} label="プロジェクト情報" align="right" onClose={() => setSheet(null)}>
              {detail && (
                <InfoPanel
                  detail={detail}
                  busy={startProfileRun.isPending}
                  onRunProfile={(profile) => startProfileRun.mutate(profile)}
                  allowExternal={allowExternalAlways}
                  onToggleExternal={(allow) => saveSettings.mutate(allow)}
                  canExport={can("project_lab.export")}
                  canPublish={can("project_lab.publish")}
                />
              )}
            </Popover>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 gap-3 px-2 pb-2 md:px-3 md:pb-3">
        <aside className="hidden w-72 shrink-0 flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900 md:flex">
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            <p className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">プロジェクト</p>
            {projectsQuery.isLoading && <Skeleton className="mx-2 h-14 rounded-xl" />}
            {projects.map((project) => (
              <ProjectRow key={project.id} project={project} selected={project.id === projectId} onSelect={() => setProjectId(project.id)} />
            ))}
            {projects.length > 0 && (
              <>
                <p className="px-2 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">ファイル</p>
                {artifacts.map((item) => (
                  <FileRow key={item.path} artifact={item} selected={item.path === artifact?.path} onSelect={() => selectArtifact(item.path)} />
                ))}
                {artifacts.length === 0 && <p className="px-2 text-xs text-zinc-400">表示できるファイルがありません</p>}
              </>
            )}
          </div>
        </aside>

        <section className="relative min-h-0 min-w-0 flex-1 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
          <Stage
            projectRoot={projectRoot}
            projects={projects}
            loading={projectsQuery.isLoading || (projectId !== null && detailQuery.isLoading)}
            error={projectsQuery.error ?? detailQuery.error}
            detail={detail}
            artifact={artifact}
            sourceMode={sourceMode}
            reloadToken={reloadToken}
            fit={fitWidth}
            externalAllowed={allowExternalAlways || (artifact ? externalAllowed[artifact.path] === true : false)}
            onAllowExternal={() => {
              if (!artifact) return;
              setExternalAllowed((current) => ({ ...current, [artifact.path]: true }));
              setReloadToken((value) => value + 1);
            }}
          />
          {artifact && detail && (
            <Dock
              detail={detail}
              artifact={artifact}
              sourceMode={sourceMode}
              fit={fitWidth}
              onToggleFit={() => setFitWidth((value) => !value)}
              running={startFileRun.isPending || Boolean(activeRun)}
              onFiles={() => setSheet("files")}
              onReload={() => setReloadToken((value) => value + 1)}
              onToggleSource={() => setSourceMode((value) => !value)}
              onRun={() => startFileRun.mutate(artifact)}
              onRuns={() => setSheet("runs")}
              onInfo={() => setSheet("info")}
            />
          )}
        </section>
      </div>

      {sheet === "files" && detail && (
        <FilesSheet artifacts={artifacts} selected={artifact?.path ?? null} onSelect={selectArtifact} onClose={() => setSheet(null)} />
      )}

      {sheet === "runs" && (
        <RunsSheet
          projectId={projectId}
          runs={runs}
          openRun={openRun}
          onOpenRun={setOpenRun}
          onClose={() => setSheet(null)}
        />
      )}
    </div>
  );
}


function ProjectRow({ project, selected, onSelect }: { project: ProjectLabSummary; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      className={`mb-1 flex min-h-12 w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition ${
        selected ? "bg-accent-50 text-accent-900 dark:bg-accent-500/15 dark:text-accent-200" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
      }`}
    >
      <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg text-xs font-bold ${selected ? "bg-accent-600 text-white" : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"}`}>
        {project.name.slice(0, 1).toUpperCase()}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{project.name}</span>
        <span className="num block truncate text-[11px] text-zinc-400">
          {project.artifactCount} files
          {project.git ? ` · ${project.git.branch}${project.git.dirty ? " *" : ""}` : ""}
          {project.technologies.length ? ` · ${project.technologies.slice(0, 3).join(" ")}` : ""}
        </span>
      </span>
    </button>
  );
}

function FileRow({ artifact, selected, onSelect }: { artifact: ProjectLabArtifact; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      className={`mb-1 flex min-h-12 w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition ${
        selected ? "bg-accent-50 text-accent-900 dark:bg-accent-500/15 dark:text-accent-200" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
      }`}
    >
      <KindBadge artifact={artifact} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{artifact.name}</span>
        <span className="num block truncate text-[11px] text-zinc-400">{formatBytes(artifact.size)} · {artifact.path}</span>
      </span>
      {artifact.runnable && (
        <span className="shrink-0 rounded-md bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">RUN</span>
      )}
    </button>
  );
}

function Stage({
  projects, loading, error, detail, artifact, sourceMode, reloadToken, fit, externalAllowed,
  onAllowExternal, projectRoot,
}: {
  projects: ProjectLabSummary[];
  loading: boolean;
  error: unknown;
  detail: ProjectLabDetail | undefined;
  artifact: ProjectLabArtifact | undefined;
  sourceMode: boolean;
  reloadToken: number;
  fit: boolean;
  externalAllowed: boolean;
  onAllowExternal: () => void;
  projectRoot: string;
}) {
  if (loading) return <div className="grid h-full place-items-center"><Skeleton className="h-24 w-48 rounded-2xl" /></div>;
  if (error) {
    return (
      <Centered>
        <p className="text-sm text-red-600 dark:text-red-400">{error instanceof Error ? error.message : "読み込みに失敗しました"}</p>
      </Centered>
    );
  }
  if (projects.length === 0) {
    return (
      <Centered>
        <p className="text-base font-semibold">プロジェクトがありません</p>
        <p className="mt-1.5 text-sm text-zinc-500">{projectRoot} 直下にフォルダを置くと自動で検出します。実行はボタンを押したときだけ行われます。</p>
      </Centered>
    );
  }
  if (!detail || !artifact) {
    return (
      <Centered>
        <p className="text-base font-semibold">表示できるファイルがありません</p>
        <p className="mt-1.5 text-sm text-zinc-500">HTML・画像・CSV・JSON・Markdown・PDF・音声・動画・Python・JavaScript・CSS などを検出します。</p>
      </Centered>
    );
  }
  return (
    <ArtifactView
      detail={detail} artifact={artifact} sourceMode={sourceMode} reloadToken={reloadToken} fit={fit}
      externalAllowed={externalAllowed} onAllowExternal={onAllowExternal}
    />
  );
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center p-8"><div className="max-w-sm text-center">{children}</div></div>;
}

/** デスクトップ幅前提のHTMLでも端が切れないよう、論理幅1024pxで描画して枠幅へ縮小表示する。 */
function HtmlFrame({ name, url, fit }: { name: string; url: string; fit: boolean }) {
  const BASE_WIDTH = 1024;
  const boxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const element = boxRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setBox({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const scale = fit && box.width > 0 ? Math.min(1, box.width / BASE_WIDTH) : 1;
  const scaled = scale < 1;
  return (
    <div ref={boxRef} className="h-full w-full overflow-hidden bg-white">
      <iframe
        title={`${name} preview`}
        src={url}
        sandbox="allow-scripts allow-modals allow-forms allow-popups allow-downloads"
        style={scaled
          ? { width: BASE_WIDTH, height: box.height / scale, transform: `scale(${scale})`, transformOrigin: "top left" }
          : undefined}
        className={`border-0 bg-white ${scaled ? "" : "h-full w-full"}`}
      />
    </div>
  );
}

function ArtifactView({
  detail, artifact, sourceMode, reloadToken, fit, externalAllowed, onAllowExternal,
}: {
  detail: ProjectLabDetail;
  artifact: ProjectLabArtifact;
  sourceMode: boolean;
  reloadToken: number;
  fit: boolean;
  externalAllowed: boolean;
  onAllowExternal: () => void;
}) {
  const url = projectLabApi.artifactUrl(detail.id, artifact.path, { external: externalAllowed });
  const asText = sourceMode || TEXTUAL.includes(artifact.kind);
  const preview = useQuery({
    queryKey: ["project-lab-preview", detail.id, artifact.path],
    queryFn: () => projectLabApi.preview(detail.id, artifact.path),
    enabled: asText,
  });
  // HTMLはtoken付きURLで配信する。sandboxのiframeは不透明originになるため、そこから出る
  // 相対参照（js/css/画像）にはcookieが乗らず、artifactの通常URLだと401になる。
  const previewToken = useQuery({
    queryKey: ["project-lab-preview-token", detail.id],
    queryFn: () => projectLabApi.previewToken(detail.id),
    enabled: !asText && artifact.kind === "html",
    staleTime: 10 * 60_000,
    refetchInterval: 10 * 60_000,
  });

  if (!asText) {
    if (artifact.kind === "html") {
      if (!previewToken.data) {
        return <div className="grid h-full place-items-center"><Skeleton className="h-24 w-48 rounded-2xl" /></div>;
      }
      const previewUrl = projectLabApi.previewUrl(previewToken.data.token, artifact.path,
        { external: externalAllowed });
      return (
        <div className="relative h-full w-full">
          <HtmlFrame key={`${artifact.path}-${reloadToken}`} name={artifact.name} url={previewUrl} fit={fit} />
          {artifact.external && !externalAllowed && (
            <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-center p-3">
              <div className="pointer-events-auto flex max-w-full items-center gap-2 rounded-2xl border border-amber-300 bg-amber-50/95 px-3 py-2 text-[11px] leading-snug text-amber-900 shadow-lg backdrop-blur dark:border-amber-800 dark:bg-amber-950/90 dark:text-amber-200">
                <span className="min-w-0">外部CDNの読み込みを遮断しています。動かない場合はこちら</span>
                <button
                  type="button"
                  onClick={onAllowExternal}
                  className="min-h-9 shrink-0 rounded-xl bg-amber-600 px-3 text-[11px] font-semibold text-white hover:bg-amber-700"
                >
                  許可して再読み込み
                </button>
              </div>
            </div>
          )}
        </div>
      );
    }
    if (artifact.kind === "image") {
      return (
        <div className="grid h-full place-items-center overflow-auto bg-[repeating-conic-gradient(#f4f4f5_0_25%,transparent_0_50%)] bg-[length:20px_20px] p-4 dark:bg-[repeating-conic-gradient(#18181b_0_25%,transparent_0_50%)]">
          <img src={url} alt={artifact.name} className="max-h-full max-w-full object-contain" />
        </div>
      );
    }
    if (artifact.kind === "pdf") return <iframe title={artifact.name} src={url} className="h-full w-full border-0 bg-white" />;
    // Centered は文言用で、grid の place-items-center が中身を内容幅まで縮める。
    // audio は内容幅を持たないので 0 になり、再生器が押せなくなる（携帯で発覚）。
    if (artifact.kind === "audio") {
      return (
        <div className="flex h-full items-center justify-center p-6">
          <audio src={url} controls className="w-full max-w-sm" />
        </div>
      );
    }
    if (artifact.kind === "video") return <div className="grid h-full place-items-center bg-black p-2"><video src={url} controls className="max-h-full max-w-full" /></div>;
  }

  if (preview.isLoading) return <div className="grid h-full place-items-center"><Skeleton className="h-24 w-48 rounded-2xl" /></div>;
  if (artifact.kind === "table" && !sourceMode) return <TableView value={preview.data?.structuredPreview} />;
  const text = preview.data?.previewText;
  if (!text) {
    return (
      <Centered>
        <p className="text-sm text-zinc-500">プレビューできる内容がありません。サイズ上限（256KB）を超えている場合は保存して確認してください。</p>
      </Centered>
    );
  }
  const language = sourceMode && artifact.kind === "html" ? "xml" : artifact.language || artifact.kind;
  return <CodeViewer text={text} language={language} wrap={artifact.kind === "markdown" || artifact.kind === "text"} />;
}

function TableView({ value }: { value: unknown }) {
  const table = value as { headers?: string[]; rows?: string[][]; truncated?: boolean } | null;
  if (!table?.headers?.length) return <Centered><p className="text-sm text-zinc-500">表を解析できませんでした。</p></Centered>;
  return (
    <div className="h-full overflow-auto pb-24">
      <table className="min-w-full border-collapse text-xs">
        <thead className="sticky top-0 z-10">
          <tr>
            {table.headers.map((header, index) => (
              <th key={`${header}-${index}`} className="border-b border-zinc-200 bg-zinc-50 px-3 py-2 text-left font-semibold dark:border-zinc-800 dark:bg-zinc-800">{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows?.map((row, rowIndex) => (
            <tr key={rowIndex} className="odd:bg-zinc-50/60 dark:odd:bg-zinc-800/30">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="num max-w-64 break-words border-b border-zinc-100 px-3 py-1.5 dark:border-zinc-800/60">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.truncated && <p className="px-3 py-2 text-[11px] text-amber-600 dark:text-amber-400">先頭200行だけ表示しています。</p>}
    </div>
  );
}

function Dock({
  detail, artifact, sourceMode, fit, onToggleFit, running, onFiles, onReload, onToggleSource, onRun, onRuns, onInfo,
}: {
  detail: ProjectLabDetail;
  artifact: ProjectLabArtifact;
  sourceMode: boolean;
  fit: boolean;
  onToggleFit: () => void;
  running: boolean;
  onFiles: () => void;
  onReload: () => void;
  onToggleSource: () => void;
  onRun: () => void;
  onRuns: () => void;
  onInfo: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  // プレビュー中のアプリ自身が画面下部にボタンを置くことがあるため、ドックは畳める。
  const [collapsed, setCollapsed] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setMenuOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const items: Array<{ label: string; onSelect: () => void }> = [
    ...(artifact.kind === "html" ? [{ label: sourceMode ? "プレビューに戻す" : "ソースを表示", onSelect: onToggleSource }] : []),
    { label: "実行ログ", onSelect: onRuns },
    { label: "プロジェクト情報", onSelect: onInfo },
  ];

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label="操作パネルを表示"
        className="absolute bottom-3 left-3 grid h-11 w-11 place-items-center rounded-full border border-zinc-200/80 bg-white/90 text-zinc-500 shadow-lg backdrop-blur dark:border-zinc-700/70 dark:bg-zinc-900/90"
      >
        <IconDots />
      </button>
    );
  }

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3">
      <div className="pointer-events-auto flex max-w-full items-center gap-1 rounded-2xl border border-zinc-200/80 bg-white/90 p-1.5 shadow-lg backdrop-blur dark:border-zinc-700/70 dark:bg-zinc-900/90">
        <button
          type="button"
          onClick={onFiles}
          className="flex min-h-10 min-w-0 items-center gap-2 rounded-xl px-2 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <KindBadge artifact={artifact} size="xs" />
          <span className="min-w-0 max-w-[9rem] truncate text-xs font-medium sm:max-w-[16rem]">{artifact.name}</span>
        </button>
        <span className="h-6 w-px shrink-0 bg-zinc-200 dark:bg-zinc-700" />
        {artifact.kind === "html" && !sourceMode && (
          <>
            <button
              type="button"
              onClick={onToggleFit}
              aria-pressed={fit}
              title={fit ? "実寸に戻す（タップ操作は実寸が確実）" : "PC幅1024pxで全景を表示（縮小・操作は実寸推奨）"}
              className={`h-10 shrink-0 rounded-xl px-2.5 text-[11px] font-semibold ${
                fit ? "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              }`}
            >
              {fit ? "全体" : "実寸"}
            </button>
            <button type="button" onClick={onReload} aria-label="プレビューを再読み込み" className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
              <IconRestart />
            </button>
          </>
        )}
        {artifact.runnable && (
          <button
            type="button"
            onClick={onRun}
            disabled={running}
            className="flex h-10 shrink-0 items-center gap-1.5 rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white hover:bg-accent-700 disabled:opacity-40"
          >
            <IconPlay />
            {running ? "実行中" : "実行"}
          </button>
        )}
        <a
          href={projectLabApi.artifactUrl(detail.id, artifact.path, { download: true })}
          aria-label="保存"
          className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <IconDownload />
        </a>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label="操作パネルを隠す"
          title="アプリの操作を邪魔する場合は隠せます"
          className="grid h-10 w-8 shrink-0 place-items-center rounded-xl text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <div className="relative shrink-0" ref={menuRef}>
          <button
            type="button"
            aria-label="その他の操作"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
            className="grid h-10 w-10 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <IconDots />
          </button>
          {menuOpen && (
            <div role="menu" className="absolute bottom-full right-0 z-40 mb-2 w-44 overflow-hidden rounded-xl border border-zinc-200 bg-white py-1 shadow-xl dark:border-zinc-700 dark:bg-zinc-800">
              {items.map((item) => (
                <button
                  key={item.label}
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    item.onSelect();
                  }}
                  className="block w-full px-4 py-2.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-700"
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FilesSheet({
  artifacts, selected, onSelect, onClose,
}: {
  artifacts: ProjectLabArtifact[];
  selected: string | null;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "artifact" | "code">("all");
  const filtered = artifacts.filter((item) => {
    if (filter === "code" && item.kind !== "code") return false;
    if (filter === "artifact" && item.kind === "code") return false;
    return item.path.toLowerCase().includes(query.trim().toLowerCase());
  });
  return (
    <BottomSheet title="ファイル" onClose={onClose} stable>
      <div className="sticky top-0 -mx-5 mb-2 bg-white px-5 pb-2 dark:bg-zinc-900">
        <div className="flex items-center gap-2 rounded-xl bg-zinc-100 px-3 dark:bg-zinc-800">
          <IconSearch className="shrink-0 text-zinc-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="ファイル名で絞り込み"
            aria-label="ファイル名で絞り込み"
            className="min-h-11 w-full bg-transparent text-sm outline-none"
          />
          {query && (
            <button type="button" aria-label="検索条件を消す" onClick={() => setQuery("")} className="shrink-0 text-zinc-400">
              <IconX />
            </button>
          )}
        </div>
        <div className="mt-2 flex gap-1.5">
          {([["all", "すべて"], ["artifact", "成果物"], ["code", "コード"]] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              aria-pressed={filter === value}
              className={`min-h-9 rounded-full px-3 text-xs font-medium ${
                filter === value ? "bg-accent-600 text-white" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {filtered.map((item) => (
        <FileRow key={item.path} artifact={item} selected={item.path === selected} onSelect={() => onSelect(item.path)} />
      ))}
      {filtered.length === 0 && <p className="py-6 text-center text-sm text-zinc-500">該当するファイルがありません</p>}
    </BottomSheet>
  );
}

function InfoPanel({
  detail, busy, onRunProfile, allowExternal, onToggleExternal, canExport, canPublish,
}: {
  detail: ProjectLabDetail;
  busy: boolean;
  onRunProfile: (profile: { id: string; type: string }) => void;
  allowExternal: boolean;
  onToggleExternal: (allow: boolean) => void;
  canExport: boolean;
  canPublish: boolean;
}) {
  const profiles = detail.manifest?.profiles ?? [];
  return (
    <div className="px-2 pb-2 pt-1">
      <p className="text-sm font-semibold">{detail.name}</p>
      <p className="mt-0.5 break-all font-mono text-[11px] text-zinc-400">{detail.path}</p>
      {detail.description && <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">{detail.description}</p>}
      <dl className="mt-3 grid grid-cols-2 gap-2">
        <Fact label="技術" value={detail.technologies.join(" · ") || "未検出"} />
        <Fact label="Git" value={detail.git ? `${detail.git.branch}${detail.git.dirty ? "（変更あり）" : detail.git.dirty === false ? "（clean）" : ""}` : "未使用"} />
        <Fact label="ファイル" value={`${detail.artifacts.length} 件`} />
        <Fact label="実行profile" value={`${profiles.length} 件`} />
      </dl>
      {detail.diagnostics.map((diagnostic) => (
        <p key={`${diagnostic.code}-${diagnostic.message}`} className="mt-2 rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
          <strong>{diagnostic.code}</strong> {diagnostic.message}
        </p>
      ))}
      {profiles.length > 0 && (
        <div className="mt-4 space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">実行profile</h3>
          {profiles.map((profile) => {
            const runnable = ["cli", "test", "web"].includes(profile.type) && profile.command.length > 0 && profile.secretRefs.length === 0;
            return (
              <div key={profile.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="flex items-center gap-2">
                  <strong className="min-w-0 flex-1 truncate text-sm">{profile.label}</strong>
                  <span className="shrink-0 rounded bg-zinc-100 px-2 py-0.5 text-[10px] dark:bg-zinc-800">{profile.type}</span>
                  <button
                    type="button"
                    disabled={!runnable || busy}
                    onClick={() => onRunProfile(profile)}
                    className="min-h-9 shrink-0 rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white disabled:opacity-40"
                  >
                    {profile.type === "web" ? "起動" : "実行"}
                  </button>
                </div>
                <p className="mt-1 break-all font-mono text-[10px] text-zinc-400">{profile.command.join(" ") || "commandなし"}</p>
              </div>
            );
          })}
        </div>
      )}
      <label className="mt-4 flex items-start gap-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
        <input
          type="checkbox"
          checked={allowExternal}
          onChange={(event) => onToggleExternal(event.target.checked)}
          className="mt-0.5 h-5 w-5 shrink-0 accent-current"
        />
        <span className="min-w-0">
          <span className="block text-xs font-medium">外部CDNを常に許可</span>
          <span className="mt-0.5 block text-[11px] leading-relaxed text-zinc-400">
            three.js などをCDNから読み込むページを、毎回の確認なしで表示します。プレビューは常に隔離（sandbox）のままなので、
            Control Deck の情報へは到達できません。
          </span>
        </span>
      </label>
      {canExport && <ExportSection projectId={detail.id} />}
      {canPublish && <PublishSection projectId={detail.id} />}
      <p className="mt-3 text-[11px] leading-relaxed text-zinc-400">
        実行は隔離された systemd user unit（ホームは読み取り専用、書き込みはプロジェクト配下のみ）で行われ、ボタンを押したときだけ開始します。
      </p>
    </div>
  );
}

/** プロジェクトをまとめてZIPで持ち出す。
 *
 * 押したらすぐ落とす、にはしない。ここはソース一式が対象なので、何が入って
 * 何が落ちたかを先に見せる。秘密情報は自動で落とすが、落とした事実が見えないと
 * 「入っているはずの file が無い」としか分からず、逆に落とし漏れにも気づけない。
 */
function ExportSection({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const [showExcluded, setShowExcluded] = useState(false);
  const plan = useQuery({
    queryKey: ["project-lab-export-plan", projectId],
    queryFn: () => projectLabApi.exportPlan(projectId),
    enabled: open,
  });

  return (
    <div className="mt-4 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 text-xs font-medium">プロジェクトをZIPで書き出す</span>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="min-h-9 shrink-0 rounded-xl bg-zinc-100 px-3 text-xs font-semibold hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
        >
          {open ? "閉じる" : "中身を確認"}
        </button>
      </div>
      {!open && (
        <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">
          鍵や認証情報らしき file、node_modules や .git は自動で除外します。
        </p>
      )}
      {open && plan.isPending && <p className="mt-3 text-xs text-zinc-400">確認しています…</p>}
      {open && plan.isError && (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">
          {plan.error instanceof Error ? plan.error.message : "確認に失敗しました"}
        </p>
      )}
      {open && plan.data && (
        <div className="mt-3">
          <p className="text-xs">
            <strong>{plan.data.fileCount} 件</strong>（{formatBytes(plan.data.totalBytes)}）を書き出します。
          </p>
          {plan.data.excluded.length > 0 ? (
            <>
              <button
                type="button"
                onClick={() => setShowExcluded((value) => !value)}
                className="mt-1 text-[11px] text-accent-600 underline underline-offset-2 dark:text-accent-400"
              >
                {plan.data.excluded.length} 件を除外しました{showExcluded ? "（隠す）" : "（内訳を見る）"}
              </button>
              {showExcluded && (
                <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-xl bg-zinc-100 p-2 dark:bg-zinc-800/60">
                  {plan.data.excluded.map((item) => (
                    <li key={item.path} className="text-[11px] leading-relaxed">
                      <span className="break-all font-mono">{item.path}</span>
                      <span className="text-zinc-400"> — {item.reason}</span>
                    </li>
                  ))}
                  {plan.data.excludedTruncated && (
                    <li className="text-[11px] text-zinc-400">… 以降は省略しました</li>
                  )}
                </ul>
              )}
            </>
          ) : (
            <p className="mt-1 text-[11px] text-zinc-400">除外した file はありません。</p>
          )}
          <a
            href={projectLabApi.archiveUrl(projectId)}
            className="mt-3 flex min-h-9 items-center justify-center gap-1.5 rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white hover:bg-accent-700"
          >
            <IconDownload />
            ZIPをダウンロード
          </a>
        </div>
      )}
    </div>
  );
}

/** 公開したページのアドレス。
 *
 * 公開してもアドレスが画面に残らないと、後から開く手段が無い。ここに置いて
 * コピーと移動（開く）をその場でできるようにする。取り下げも同じ場所に置く
 * ——公開した事実とその取り消しは、離して置くと見つからない。
 */
function PublishedAddress({
  state, onUnpublish, busy,
}: {
  state: ProjectLabPublishState;
  onUnpublish: () => void;
  busy: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const { show } = useToasts.getState();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(state.url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 安全でない文脈（平文HTTP）では clipboard API が使えない。
      // 黙って何も起きないと壊れて見えるので、理由を出す。
      show("この接続ではコピーできません。長押しで選択してください", "error");
    }
  };

  return (
    <div className="rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-800">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
          公開中
        </span>
        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] dark:bg-zinc-800">
          {state.visibility}
        </span>
      </div>
      <p className="mt-1 break-all font-mono text-[11px]">{state.url}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={copy}
          className="min-h-9 rounded-xl bg-zinc-100 px-3 text-xs font-semibold hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
        >
          {copied ? "コピーしました" : "URLをコピー"}
        </button>
        <a
          href={state.url}
          target="_blank"
          rel="noreferrer noopener"
          className="flex min-h-9 items-center rounded-xl bg-zinc-100 px-3 text-xs font-semibold hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
        >
          開く
        </a>
        <button
          type="button"
          disabled={busy}
          onClick={() => (confirming ? onUnpublish() : setConfirming(true))}
          className={`min-h-9 rounded-xl px-3 text-xs font-semibold disabled:opacity-40 ${
            confirming
              ? "bg-red-600 text-white hover:bg-red-700"
              : "text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/30"
          }`}
        >
          {busy ? "取り下げ中…" : confirming ? "取り下げる（確定）" : "公開を取り下げる"}
        </button>
      </div>
      {confirming && !busy && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-400">
          ページは 404 になり、公開していた内容も消えます。リポジトリ {state.repository} 自体は残ります。
        </p>
      )}
    </div>
  );
}

/** 静的ホスティング（GitHub Pages）へ公開する。
 *
 * ダウンロードとは別に置く。あちらは手元に落とすだけだが、こちらは押した瞬間に
 * インターネットへ出る。取り消しても索引や cache には残るので、
 *   1. 何が出るかを必ず先に見せる
 *   2. public / private は毎回選ばせる（既定値を持たない）
 * の2つは省かない。
 */
function PublishSection({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const [directory, setDirectory] = useState<string | undefined>(undefined);
  const [repository, setRepository] = useState(projectId);
  const [visibility, setVisibility] = useState<"public" | "private" | "">("");
  const [showExcluded, setShowExcluded] = useState(false);
  const show = useToasts((state) => state.show);

  const plan = useQuery({
    queryKey: ["project-lab-publish-plan", projectId, directory],
    queryFn: () => projectLabApi.publishPlan(projectId, directory),
    enabled: open,
  });
  const queryClient = useQueryClient();
  const unpublish = useMutation({
    mutationFn: () => projectLabApi.unpublish(projectId),
    onSuccess: (result) => {
      show(
        result.repositoryRemains
          ? `公開を取り下げました。リポジトリ ${result.repository} は残っています`
          : "公開を取り下げました",
        "success",
      );
      queryClient.invalidateQueries({ queryKey: ["project-lab-publish-plan", projectId] });
    },
    onError: (error) => show(error instanceof Error ? error.message : "取り下げに失敗しました", "error"),
  });
  const run = useMutation({
    mutationFn: () => projectLabApi.publish(projectId, {
      repository,
      visibility: visibility as "public" | "private",
      directory: directory ?? null,
    }),
    onSuccess: (state) => {
      show(`公開しました: ${state.url}`, "success");
      queryClient.invalidateQueries({ queryKey: ["project-lab-publish-plan", projectId] });
    },
    onError: (error) => show(error instanceof Error ? error.message : "公開に失敗しました", "error"),
  });

  const github = plan.data?.github;
  const blocked = github ? !github.available || !github.loggedIn : false;
  const ready = Boolean(plan.data?.hasIndex) && visibility !== "" && repository.trim() !== "" && !blocked;

  return (
    <div className="mt-3 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 text-xs font-medium">静的サイトとして公開</span>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="min-h-9 shrink-0 rounded-xl bg-zinc-100 px-3 text-xs font-semibold hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
        >
          {open ? "閉じる" : "公開の設定"}
        </button>
      </div>
      {!open && (
        <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">
          GitHub Pages へ公開します。鍵や認証情報らしき file は自動で除外します。
        </p>
      )}
      {open && plan.isPending && <p className="mt-3 text-xs text-zinc-400">確認しています…</p>}
      {open && plan.isError && (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">
          {plan.error instanceof Error ? plan.error.message : "確認に失敗しました"}
        </p>
      )}
      {open && plan.data && (
        <div className="mt-3 space-y-3">
          {plan.data.current && (
            <PublishedAddress
              state={plan.data.current}
              onUnpublish={() => unpublish.mutate()}
              busy={unpublish.isPending}
            />
          )}
          {blocked && (
            <p className="rounded-xl bg-red-50 p-2 text-[11px] text-red-700 dark:bg-red-950/30 dark:text-red-300">
              {github?.available
                ? "gh が GitHub にログインしていません。サーバー上で `gh auth login` を実行してください。"
                : "サーバーに gh CLI がありません。GitHub Pages への公開には gh が必要です。"}
            </p>
          )}

          <label className="block">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">公開ディレクトリ</span>
            <select
              value={plan.data.directory}
              onChange={(event) => setDirectory(event.target.value)}
              className="mt-1 h-10 w-full rounded-xl border border-zinc-200 bg-transparent px-2 text-xs dark:border-zinc-700"
            >
              {plan.data.candidates.map((candidate) => (
                <option key={candidate.directory} value={candidate.directory}>
                  {candidate.directory === "" ? "（プロジェクト直下）" : candidate.directory}
                  {candidate.hasIndex ? " — index.html あり" : " — index.html なし"}
                </option>
              ))}
            </select>
          </label>
          {!plan.data.hasIndex && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              このディレクトリに index.html がありません。ビルド後の出力先を選んでください。
            </p>
          )}

          <label className="block">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">リポジトリ名</span>
            <input
              value={repository}
              onChange={(event) => setRepository(event.target.value)}
              className="mt-1 h-10 w-full rounded-xl border border-zinc-200 bg-transparent px-2 font-mono text-xs dark:border-zinc-700"
            />
            {github?.account && (
              <span className="mt-1 block text-[11px] text-zinc-400">{github.account}/{repository || "…"} に push します</span>
            )}
          </label>

          <fieldset>
            <legend className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">公開範囲</legend>
            <div className="mt-1 space-y-1">
              {(["private", "public"] as const).map((value) => (
                <label key={value} className="flex items-start gap-2 text-[11px]">
                  <input
                    type="radio"
                    name={`visibility-${projectId}`}
                    checked={visibility === value}
                    onChange={() => setVisibility(value)}
                    className="mt-0.5 h-4 w-4 shrink-0 accent-current"
                  />
                  <span>
                    <strong>{value}</strong>
                    <span className="block text-zinc-400">
                      {value === "private"
                        ? "リポジトリは非公開。無料プランでは Pages が有効にできない場合があります。"
                        : "リポジトリごと世界に公開されます。中身を確認してから選んでください。"}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div>
            <p className="text-xs">
              <strong>{plan.data.fileCount} 件</strong>（{formatBytes(plan.data.totalBytes)}）を公開します。
            </p>
            {plan.data.excluded.length > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => setShowExcluded((value) => !value)}
                  className="mt-1 text-[11px] text-accent-600 underline underline-offset-2 dark:text-accent-400"
                >
                  {plan.data.excluded.length} 件を除外しました{showExcluded ? "（隠す）" : "（内訳を見る）"}
                </button>
                {showExcluded && (
                  <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-xl bg-zinc-100 p-2 dark:bg-zinc-800/60">
                    {plan.data.excluded.map((item) => (
                      <li key={item.path} className="text-[11px] leading-relaxed">
                        <span className="break-all font-mono">{item.path}</span>
                        <span className="text-zinc-400"> — {item.reason}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>

          <button
            type="button"
            disabled={!ready || run.isPending}
            onClick={() => run.mutate()}
            className="flex min-h-10 w-full items-center justify-center rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white hover:bg-accent-700 disabled:opacity-40"
          >
            {run.isPending ? "公開しています…" : visibility === "" ? "公開範囲を選んでください" : `${visibility} で公開する`}
          </button>
        </div>
      )}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-zinc-100 px-3 py-2 dark:bg-zinc-800/60">
      <dt className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">{label}</dt>
      <dd className="mt-0.5 break-words text-sm">{value}</dd>
    </div>
  );
}

function RunsSheet({
  projectId, runs, openRun, onOpenRun, onClose,
}: {
  projectId: string | null;
  runs: ProjectLabRun[];
  openRun: number | null;
  onOpenRun: (id: number | null) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const show = useToasts((state) => state.show);
  const current = runs.find((run) => run.id === openRun) ?? runs[0] ?? null;
  const active = current !== null && ACTIVE_STATES.includes(current.status);
  const logs = useQuery({
    queryKey: ["project-lab-run-logs", current?.id],
    queryFn: () => projectLabApi.runLogs(current?.id as number),
    enabled: current !== null,
    refetchInterval: active ? 1500 : false,
  });
  const cancel = useMutation({
    mutationFn: projectLabApi.cancelRun,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["project-lab-runs", projectId] }),
    onError: (error) => show(error instanceof Error ? error.message : "停止できません", "error"),
  });

  return (
    <BottomSheet title="実行" onClose={onClose} stable>
      {runs.length === 0 && <p className="py-6 text-center text-sm text-zinc-500">まだ実行していません。Python / JavaScript ファイルを開いて「実行」を押すと、ここにログが出ます。</p>}
      {runs.length > 0 && (
        <div className="-mx-1 mb-3 flex gap-1.5 overflow-x-auto px-1 pb-1">
          {runs.slice(0, 12).map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => onOpenRun(run.id)}
              aria-pressed={current?.id === run.id}
              className={`min-h-9 shrink-0 rounded-full px-3 text-xs font-medium ${
                current?.id === run.id ? "bg-accent-600 text-white" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
              }`}
            >
              #{run.id} {run.profileId}
            </button>
          ))}
        </div>
      )}
      {current && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <StatusChip status={current.status} />
            <span className="num text-[11px] text-zinc-400">
              {new Date(current.startedAt).toLocaleString("ja-JP")}
              {current.elapsedMs !== null ? ` · ${current.elapsedMs} ms` : " · 実行中"}
              {current.exitCode !== null ? ` · exit ${current.exitCode}` : ""}
            </span>
            {active && (
              <button
                type="button"
                onClick={() => cancel.mutate(current.id)}
                disabled={cancel.isPending}
                className="ml-auto flex min-h-9 items-center gap-1 rounded-xl px-3 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:opacity-40 dark:hover:bg-red-950/40"
              >
                <IconStop /> 停止
              </button>
            )}
          </div>
          <p className="mb-2 break-all font-mono text-[10px] text-zinc-400">{current.command.join(" ")}</p>
          {current.error && <p className="mb-2 rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{current.error}</p>}
          {current.profileType === "web" && current.previewReady && current.previewUrl && (
            <iframe title="Web preview" src={current.previewUrl} sandbox="allow-scripts allow-forms" className="mb-2 h-64 w-full rounded-xl border border-zinc-200 bg-white dark:border-zinc-700" />
          )}
          <pre className="max-h-[46dvh] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-100">
            {logs.isLoading ? "ログを読み込み中..." : logs.data?.logs || "出力はありません"}
          </pre>
          {current.artifacts.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {current.artifacts.map((item) => (
                <span key={item.id} className="rounded-md bg-zinc-100 px-2 py-1 text-[10px] dark:bg-zinc-800">{item.changeType}: {item.path}</span>
              ))}
            </div>
          )}
        </>
      )}
    </BottomSheet>
  );
}

function StatusChip({ status }: { status: ProjectLabRun["status"] }) {
  const tone = status === "SUCCEEDED"
    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
    : ACTIVE_STATES.includes(status)
      ? "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"
      : "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300";
  return <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${tone}`}>{status}</span>;
}
