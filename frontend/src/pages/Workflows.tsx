import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { ConfirmDialog, DropdownMenu, Skeleton } from "../components/ui";
import { IconDots, IconPlay, IconPlus } from "../components/icons";
import { DEFAULT_DEFINITION } from "../features/workflows/nodeTypes";

const WorkflowEditor = lazy(() => import("../features/workflows/WorkflowEditor"));

export interface WorkflowSummary {
  id: number;
  name: string;
  description: string;
  definition: { nodes: unknown[]; edges: unknown[] };
  enabled: boolean;
  last_execution: {
    id: number;
    status: string;
    started_at: string;
    finished_at: string | null;
  } | null;
}

const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  SUCCEEDED: { label: "成功", cls: "text-emerald-600 dark:text-emerald-400" },
  FAILED: { label: "失敗", cls: "text-red-600 dark:text-red-400" },
  RUNNING: { label: "実行中", cls: "text-accent-600 dark:text-accent-400" },
  QUEUED: { label: "待機", cls: "text-zinc-400" },
  CANCELED: { label: "中止", cls: "text-zinc-400" },
  TIMED_OUT: { label: "時間切れ", cls: "text-amber-600 dark:text-amber-400" },
};

export default function WorkflowsPage() {
  const { id } = useParams();
  if (id) {
    return (
      <Suspense
        fallback={
          <div className="grid h-full place-items-center text-sm text-zinc-400">
            エディターを読み込み中...
          </div>
        }
      >
        <WorkflowEditor workflowId={Number(id)} />
      </Suspense>
    );
  }
  return <WorkflowList />;
}

function WorkflowList() {
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState<WorkflowSummary | null>(null);

  const { data: workflows, isLoading } = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api<WorkflowSummary[]>("/workflows"),
    refetchInterval: 10_000,
  });

  const create = useMutation({
    mutationFn: () =>
      api<WorkflowSummary>("/workflows", {
        method: "POST",
        json: { name: "新しいワークフロー", definition: DEFAULT_DEFINITION },
      }),
    onSuccess: (wf) => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${wf.id}`);
    },
    onError: (e) => show(e instanceof Error ? e.message : "作成に失敗しました", "error"),
  });

  const run = useMutation({
    mutationFn: (id: number) => api(`/workflows/${id}/run`, { method: "POST" }),
    onSuccess: () => {
      show("実行を開始しました");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["workflows"] }), 800);
    },
    onError: (e) => show(e instanceof Error ? e.message : "実行に失敗しました", "error"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/workflows/${id}/${enabled ? "enable" : "disable"}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
    onError: (e) => show(e instanceof Error ? e.message : "切替に失敗しました", "error"),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api(`/workflows/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      show("削除しました");
      setDeleting(null);
      qc.invalidateQueries({ queryKey: ["workflows"] });
    },
    onError: (e) => show(e instanceof Error ? e.message : "削除に失敗しました", "error"),
  });

  return (
    <div className="mx-auto max-w-4xl p-4 md:p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">ワークフロー</h1>
        {can("workflows.edit") && (
          <button
            onClick={() => create.mutate()}
            disabled={create.isPending}
            className="hidden items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700 md:flex"
          >
            <IconPlus /> 新規ワークフロー
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : !workflows || workflows.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">
            ワークフローはまだありません。ノードをつないで PC 操作を自動化できます。
          </p>
          {can("workflows.edit") && (
            <button
              onClick={() => create.mutate()}
              className="mt-3 rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700"
            >
              最初のワークフローを作成
            </button>
          )}
        </div>
      ) : (
        <ul className="space-y-3">
          {workflows.map((wf) => {
            const st = wf.last_execution ? STATUS_LABEL[wf.last_execution.status] : null;
            return (
              <li
                key={wf.id}
                className="flex cursor-pointer items-center gap-3 rounded-2xl border border-zinc-200 bg-white p-4 hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
                onClick={() => navigate(`/workflows/${wf.id}`)}
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{wf.name}</p>
                  <p className="mt-0.5 text-xs text-zinc-400">
                    ノード {wf.definition.nodes?.length ?? 0} 個
                    {st && (
                      <>
                        {" · 前回 "}
                        <span className={st.cls}>{st.label}</span>
                      </>
                    )}
                    {wf.enabled && " · スケジュール有効"}
                  </p>
                </div>
                {can("workflows.run") && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      run.mutate(wf.id);
                    }}
                    aria-label={`${wf.name} を実行`}
                    className="flex min-h-11 items-center gap-1.5 rounded-xl bg-accent-50 px-3.5 text-sm font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
                  >
                    <IconPlay />
                    <span className="hidden sm:inline">実行</span>
                  </button>
                )}
                <DropdownMenu
                  ariaLabel={`${wf.name} のメニュー`}
                  trigger={<IconDots />}
                  items={[
                    { label: "編集", onSelect: () => navigate(`/workflows/${wf.id}`) },
                    ...(can("workflows.edit")
                      ? [
                          {
                            label: wf.enabled ? "スケジュール無効化" : "スケジュール有効化",
                            onSelect: () => toggle.mutate({ id: wf.id, enabled: !wf.enabled }),
                          },
                          { label: "削除", danger: true, onSelect: () => setDeleting(wf) },
                        ]
                      : []),
                  ]}
                />
              </li>
            );
          })}
        </ul>
      )}

      {can("workflows.edit") && (
        <button
          onClick={() => create.mutate()}
          aria-label="新規ワークフロー"
          className="fixed bottom-24 right-4 z-20 grid place-items-center rounded-2xl bg-accent-600 p-3.5 text-xl text-white shadow-lg hover:bg-accent-700 md:hidden"
        >
          <IconPlus />
        </button>
      )}

      {deleting && (
        <ConfirmDialog
          title={`「${deleting.name}」を削除しますか？`}
          message="定義と実行履歴が削除されます。この操作は取り消せません。"
          confirmLabel="削除する"
          busy={remove.isPending}
          onConfirm={() => remove.mutate(deleting.id)}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
