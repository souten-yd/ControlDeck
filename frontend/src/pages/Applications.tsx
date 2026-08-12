import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import {
  flowAppApi,
  type FlowAppExport,
  type FlowAppField,
  type FlowAppFormat,
} from "../api/flowApp";
import type { WorkflowSummary } from "./Workflows";
import { ConfirmDialog, Popover, Skeleton } from "../components/ui";
import { IconDots, IconDownload, IconTrash } from "../components/icons";
import { useAuth, useToasts } from "../stores";

type SheetKind = "workflows" | "guide" | null;

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}


export default function ApplicationsPage() {
  const navigate = useNavigate();
  const can = useAuth((state) => state.can);
  const show = useToasts((state) => state.show);
  const queryClient = useQueryClient();
  const canExport = can("application_builder.edit");
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [sheet, setSheet] = useState<SheetKind>(null);
  const [deleting, setDeleting] = useState<FlowAppExport | null>(null);
  const [format, setFormat] = useState<FlowAppFormat>("pyz");
  const [jobId, setJobId] = useState<string | null>(null);

  const workflowsQuery = useQuery({
    queryKey: ["workflows", "flow-app"],
    queryFn: () => api<WorkflowSummary[]>("/workflows"),
  });
  const workflows = useMemo(() => workflowsQuery.data ?? [], [workflowsQuery.data]);
  const previewQuery = useQuery({
    queryKey: ["flow-app-preview", workflowId],
    queryFn: () => flowAppApi.preview(workflowId as number),
    enabled: workflowId !== null,
  });
  const preview = previewQuery.data;
  const capability = useQuery({ queryKey: ["flow-app-capability"], queryFn: flowAppApi.capability });

  useEffect(() => {
    if (workflowId === null && workflows.length > 0) setWorkflowId(workflows[0].id);
  }, [workflows, workflowId]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["flow-app-preview", workflowId] });
  const exportApp = useMutation({
    mutationFn: () => flowAppApi.create(workflowId as number, format),
    onSuccess: async (created) => {
      if (created.job_id) {
        setJobId(created.job_id);
        show("単一バイナリを作成中です。この画面を閉じても続きます", "info");
        return;
      }
      await invalidate();
      show(`${created.filename} を書き出しました（${formatBytes(created.size)}）`);
    },
    onError: (error) => show(error instanceof Error ? error.message : "書き出しに失敗しました", "error"),
  });

  // 単一バイナリはサーバー側jobで進む。完了したら一覧へ反映する。
  const job = useQuery({
    queryKey: ["flow-app-job", jobId],
    queryFn: () => flowAppApi.job(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      query.state.data && !["queued", "running"].includes(query.state.data.status) ? false : 1500,
  });
  const jobStatus = job.data?.status;
  useEffect(() => {
    if (!jobId || !jobStatus || ["queued", "running"].includes(jobStatus)) return;
    setJobId(null);
    if (jobStatus === "succeeded") {
      void invalidate();
      show("単一バイナリを書き出しました");
    } else {
      show(job.data?.error || "単一バイナリの作成に失敗しました", "error");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobStatus]);
  const removeApp = useMutation({
    mutationFn: (filename: string) => flowAppApi.remove(workflowId as number, filename),
    onSuccess: async () => {
      setDeleting(null);
      await invalidate();
      show("実行ファイルを削除しました");
    },
    onError: (error) => show(error instanceof Error ? error.message : "削除に失敗しました", "error"),
  });

  const selected = workflows.find((item) => item.id === workflowId);
  const blocking = (preview?.diagnostics ?? []).filter((item) => item.severity === "error");
  const warnings = (preview?.diagnostics ?? []).filter((item) => item.severity !== "error");

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden bg-zinc-100 dark:bg-zinc-950">
      <header className="flex h-12 shrink-0 items-center gap-2 px-2 md:px-3">
        <div className="relative min-w-0 max-w-[64%]">
          <button
            type="button"
            aria-haspopup="dialog"
            aria-expanded={sheet === "workflows"}
            onClick={() => setSheet(sheet === "workflows" ? null : "workflows")}
            className="flex min-h-10 w-full min-w-0 items-center gap-1.5 rounded-xl px-2.5 text-left hover:bg-white dark:hover:bg-zinc-900"
          >
            <span className="min-w-0 truncate text-sm font-semibold">{selected?.name ?? "App Studio"}</span>
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 text-zinc-400 transition-transform ${sheet === "workflows" ? "rotate-180" : ""}`} aria-hidden>
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          <Popover open={sheet === "workflows"} label="ワークフロー" onClose={() => setSheet(null)}>
            <p className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">ワークフロー</p>
            {workflows.map((workflow) => (
              <button
                key={workflow.id}
                type="button"
                onClick={() => {
                  setWorkflowId(workflow.id);
                  setSheet(null);
                }}
                aria-current={workflow.id === workflowId}
                className={`mb-1 flex min-h-12 w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left ${
                  workflow.id === workflowId
                    ? "bg-accent-50 text-accent-900 dark:bg-accent-500/15 dark:text-accent-200"
                    : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
                }`}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{workflow.name}</span>
                  <span className="num block truncate text-[11px] text-zinc-400">
                    {workflow.definition.nodes.length} nodes · {workflow.state === "published" ? `v${workflow.published_version}` : "draft"}
                  </span>
                </span>
              </button>
            ))}
            {workflows.length === 0 && (
              <p className="px-2.5 py-4 text-xs leading-relaxed text-zinc-500">
                まだワークフローがありません。Workflows で作成すると、ここから実行ファイルに書き出せます。
              </p>
            )}
          </Popover>
        </div>
        <div className="ml-auto flex items-center gap-1">
          {selected && (
            <button
              type="button"
              onClick={() => navigate(`/workflows/${selected.id}`)}
              className="min-h-10 rounded-xl px-2.5 text-xs font-medium text-zinc-500 hover:bg-white dark:hover:bg-zinc-900"
            >
              編集
            </button>
          )}
          <div className="relative">
            <button
              type="button"
              onClick={() => setSheet(sheet === "guide" ? null : "guide")}
              aria-label="使い方"
              aria-haspopup="dialog"
              aria-expanded={sheet === "guide"}
              className="grid h-10 w-10 place-items-center rounded-xl text-zinc-500 hover:bg-white dark:hover:bg-zinc-900"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" />
              </svg>
            </button>
            <Popover open={sheet === "guide"} label="使い方" align="right" onClose={() => setSheet(null)}>
              <div className="px-2 pb-2 pt-1 text-[12px] leading-relaxed text-zinc-600 dark:text-zinc-300">
                <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">配布用の実行ファイルを作る</p>
                <p className="mt-1.5">
                  選んだワークフローを 1 ファイル（.pyz）に書き出します。配布先に必要なのは python3 だけで、
                  Control Deck も追加ランタイムも不要です。
                </p>
                <pre className="mt-2 overflow-x-auto rounded-xl bg-zinc-100 p-2.5 font-mono text-[11px] dark:bg-zinc-800">{`chmod +x app.pyz
./app.pyz                # GUIを開く
./app.pyz --input '{"text":"hi"}'
./app.pyz --info         # 入出力を確認`}</pre>
                <p className="mt-2">
                  形式は 2 つから選べます。<b>.pyz</b> は配布先に python3 だけあれば動き、1〜2 秒・1MB 未満で作れます。
                  <b>単一バイナリ</b> は配布先に何もインストールせずに動きますが、この端末に「アプリビルド環境」アドオン
                  （設定 → アドオン）が必要で、10MB 前後になります。
                </p>
                <p className="mt-2 text-[11px] text-zinc-400">
                  対応ノード {capability.data?.supportedNodes.length ?? 0} 種。Knowledge・ブラウザ操作・コマンド実行など
                  Control Deck の基盤が要るノードは書き出せません。
                </p>
              </div>
            </Popover>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-24 md:px-3">
        <div className="mx-auto max-w-3xl space-y-3">
          {workflowsQuery.isLoading && <Skeleton className="h-32 rounded-2xl" />}
          {!workflowsQuery.isLoading && workflows.length === 0 && (
            <Card>
              <p className="text-base font-semibold">ワークフローがありません</p>
              <p className="mt-1.5 text-sm text-zinc-500">
                Workflows でフローを作ると、ここから配布用の実行ファイルに書き出せます。
              </p>
              <button
                type="button"
                onClick={() => navigate("/workflows")}
                className="mt-3 min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white"
              >
                Workflows を開く
              </button>
            </Card>
          )}

          {workflowId !== null && previewQuery.isLoading && <Skeleton className="h-40 rounded-2xl" />}

          {preview && (
            <>
              <Card>
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-base font-semibold">{preview.name}</h2>
                    <p className="num mt-0.5 text-[11px] text-zinc-400">
                      {Object.values(preview.nodeTypes).reduce((total, count) => total + count, 0)} nodes ·
                      入力 {preview.inputs.length} · 出力 {preview.outputs.length}
                    </p>
                  </div>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-bold ${
                    preview.portable
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                      : "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300"
                  }`}>
                    {preview.portable ? "書き出せます" : "書き出せません"}
                  </span>
                </div>
                {preview.description && <p className="mt-2 text-sm text-zinc-500">{preview.description}</p>}
                {(preview.inputs.length > 0 || preview.outputs.length > 0) && (
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <FieldList title="入力" fields={preview.inputs} empty="入力なし（そのまま実行）" />
                    <FieldList title="出力" fields={preview.outputs} empty="出力ノードがありません" />
                  </div>
                )}
              </Card>

              {blocking.map((item) => (
                <div key={`${item.code}-${item.path}-${item.message}`} className="rounded-2xl border border-red-200 bg-red-50 p-3.5 text-xs leading-relaxed text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
                  {item.message}
                  {item.suggestedFix && <p className="mt-1 opacity-80">→ {item.suggestedFix}</p>}
                </div>
              ))}
              {warnings.map((item) => (
                <div key={`${item.code}-${item.message}`} className="rounded-2xl border border-amber-200 bg-amber-50 p-3.5 text-xs leading-relaxed text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
                  {item.message}
                  {item.suggestedFix && <p className="mt-1 opacity-80">→ {item.suggestedFix}</p>}
                </div>
              ))}

              <section>
                <h3 className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                  書き出す形式
                </h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {(capability.data?.formats ?? []).map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      onClick={() => setFormat(option.id)}
                      disabled={!option.available}
                      aria-pressed={format === option.id}
                      className={`rounded-2xl border p-3.5 text-left transition disabled:opacity-60 ${
                        format === option.id
                          ? "border-accent-500 bg-accent-50 dark:border-accent-500/60 dark:bg-accent-500/10"
                          : "border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
                      }`}
                    >
                      <p className="text-sm font-semibold">{option.label}</p>
                      <p className="num mt-0.5 text-[11px] text-zinc-400">{option.size} · {option.buildTime}</p>
                      <p className="mt-1.5 text-xs text-zinc-600 dark:text-zinc-300">{option.requires}</p>
                      {!option.available && (
                        <p className="mt-1.5 text-[11px] text-amber-600 dark:text-amber-400">{option.note}</p>
                      )}
                    </button>
                  ))}
                </div>
                {jobId !== null && (
                  <p className="mt-2 animate-pulse rounded-xl bg-accent-50 px-3 py-2 text-xs text-accent-700 dark:bg-accent-500/10 dark:text-accent-300">
                    {job.data?.progress?.status || "単一バイナリを作成中…"} — サーバー側で実行中。この画面を閉じても継続します
                  </p>
                )}
              </section>

              <section>
                <h3 className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                  書き出した実行ファイル
                </h3>
                {preview.exports.length === 0 ? (
                  <Card>
                    <p className="text-sm text-zinc-500">
                      まだありません。下の「書き出す」を押すと 1 ファイルの実行アプリを作ります。
                    </p>
                  </Card>
                ) : (
                  <div className="space-y-2">
                    {preview.exports.map((item) => (
                      <ExportCard
                        key={item.filename}
                        item={item}
                        workflowId={preview.workflowId}
                        canDelete={canExport}
                        onDelete={() => setDeleting(item)}
                      />
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>

      {preview && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <div className="pointer-events-auto flex max-w-full items-center gap-1 rounded-2xl border border-zinc-200/80 bg-white/90 p-1.5 shadow-lg backdrop-blur dark:border-zinc-700/70 dark:bg-zinc-900/90">
            <button
              type="button"
              onClick={() => setSheet("workflows")}
              className="flex min-h-10 min-w-0 items-center gap-2 rounded-xl px-2.5 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <span className="min-w-0 max-w-[8rem] truncate text-xs font-medium sm:max-w-[16rem]">{preview.name}</span>
            </button>
            <span className="h-6 w-px shrink-0 bg-zinc-200 dark:bg-zinc-700" />
            <button
              type="button"
              disabled={!preview.portable || !canExport || exportApp.isPending || jobId !== null}
              onClick={() => exportApp.mutate()}
              className="flex h-10 shrink-0 items-center gap-1.5 rounded-xl bg-accent-600 px-4 text-xs font-semibold text-white hover:bg-accent-700 disabled:opacity-40"
            >
              {exportApp.isPending || jobId !== null ? "書き出し中…" : "書き出す"}
            </button>
            <button
              type="button"
              onClick={() => setSheet("guide")}
              aria-label="使い方"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <IconDots />
            </button>
          </div>
        </div>
      )}

      {deleting && (
        <ConfirmDialog
          title="実行ファイルを削除しますか？"
          message={`${deleting.filename} を削除します。配布済みのコピーには影響しません。`}
          confirmLabel="削除する"
          busy={removeApp.isPending}
          onConfirm={() => removeApp.mutate(deleting.filename)}
          onClose={() => !removeApp.isPending && setDeleting(null)}
        />
      )}
    </div>
  );
}

function Card({ children }: { children: ReactNode }) {
  return <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">{children}</section>;
}

function FieldList({ title, fields, empty }: { title: string; fields: FlowAppField[]; empty: string }) {
  return (
    <div className="rounded-xl bg-zinc-100 px-3 py-2.5 dark:bg-zinc-800/60">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">{title}</p>
      {fields.length === 0 ? (
        <p className="mt-1 text-xs text-zinc-500">{empty}</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {fields.map((field) => (
            <li key={field.name} className="truncate text-xs">
              <span className="font-mono">{field.name}</span>
              <span className="text-zinc-400"> · {field.type}{field.required ? " · 必須" : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ExportCard({
  item, workflowId, canDelete, onDelete,
}: {
  item: FlowAppExport;
  workflowId: number;
  canDelete: boolean;
  onDelete: () => void;
}) {
  const show = useToasts((state) => state.show);
  const command = `./${item.filename}`;
  const isBinary = item.format === "binary";
  return (
    <article className="rounded-2xl border border-zinc-200 bg-white p-3.5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] font-semibold">{item.filename}</p>
          <p className="num mt-0.5 text-[11px] text-zinc-400">
            {formatBytes(item.size)} · {new Date(item.createdAt).toLocaleString("ja-JP")}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
            <span className={`rounded-full px-2 py-0.5 font-semibold ${
              isBinary
                ? "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300"
                : "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300"
            }`}>
              {isBinary ? "単一バイナリ" : ".pyz"}
            </span>
            <span className="text-zinc-400">{item.requires}</span>
          </p>
        </div>
        <a
          href={flowAppApi.downloadUrl(workflowId, item.filename)}
          aria-label="ダウンロード"
          className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-accent-600 hover:bg-accent-50 dark:hover:bg-accent-500/10"
        >
          <IconDownload />
        </a>
        {canDelete && (
          <button
            type="button"
            onClick={onDelete}
            aria-label="削除"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40"
          >
            <IconTrash />
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg bg-zinc-100 px-2.5 py-2 font-mono text-[11px] dark:bg-zinc-800">
          chmod +x {item.filename} && {command}
        </code>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(`chmod +x ${item.filename} && ${command}`);
            show("実行コマンドをコピーしました");
          }}
          className="shrink-0 rounded-lg px-2.5 py-2 text-[11px] font-medium text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          コピー
        </button>
      </div>
      {item.checksum && (
        <p className="num mt-1.5 truncate font-mono text-[10px] text-zinc-400">sha256 {item.checksum}</p>
      )}
    </article>
  );
}
