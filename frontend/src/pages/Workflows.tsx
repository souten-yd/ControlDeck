import { lazy, Suspense, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, DropdownMenu, Skeleton } from "../components/ui";
import { IconDots, IconPlay, IconPlus } from "../components/icons";
import { DEFAULT_DEFINITION } from "../features/workflows/nodeTypes";

const WorkflowEditor = lazy(() => import("../features/workflows/WorkflowEditor"));
const SampleBook = lazy(() => import("../features/workflows/SampleBook"));
const AssistantChat = lazy(() => import("../features/workflows/AssistantChat"));

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
  const [showSamples, setShowSamples] = useState(false);
  const [showAssistant, setShowAssistant] = useState(false);
  const [showSecrets, setShowSecrets] = useState(false);
  const importRef = useRef<HTMLInputElement>(null);

  const exportWorkflow = async (wf: WorkflowSummary) => {
    const detail = await api<WorkflowSummary>(`/workflows/${wf.id}`);
    const blob = new Blob(
      [JSON.stringify({ name: detail.name, description: detail.description, definition: detail.definition }, null, 2)],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${detail.name || "workflow"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importWorkflow = (file: File) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const data = JSON.parse(String(reader.result));
        const wf = await api<WorkflowSummary>("/workflows", {
          method: "POST",
          json: {
            name: String(data.name || file.name.replace(/\.json$/i, "")),
            description: String(data.description || "インポート"),
            definition: data.definition ?? data,
          },
        });
        show(`「${wf.name}」をインポートしました`);
        qc.invalidateQueries({ queryKey: ["workflows"] });
        navigate(`/workflows/${wf.id}`);
      } catch (e) {
        show(e instanceof Error ? e.message : "インポートに失敗しました", "error");
      }
    };
    reader.readAsText(file);
  };

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
      <div className="mb-4 flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">ワークフロー</h1>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setShowAssistant(true)}
            className="flex min-h-10 items-center gap-1.5 rounded-xl border border-zinc-200 px-3 py-2 text-sm font-medium hover:border-zinc-300 dark:border-zinc-700 dark:hover:border-zinc-600"
            title="チャット・Web/学術/Deep 検索・ワークフロー自動生成"
          >
            ✨<span className="hidden sm:inline"> アシスタント</span>
          </button>
          <button
            onClick={() => setShowSamples(true)}
            className="flex min-h-10 items-center gap-1.5 rounded-xl border border-zinc-200 px-3 py-2 text-sm font-medium hover:border-zinc-300 dark:border-zinc-700 dark:hover:border-zinc-600"
            title="サンプルワークフロー集とノードリファレンス"
          >
            📖<span className="hidden sm:inline"> サンプルブック</span>
          </button>
          {can("workflows.edit") && (
            <>
              <input
                ref={importRef}
                type="file"
                accept="application/json,.json"
                className="hidden"
                onChange={(e) => { if (e.target.files?.[0]) importWorkflow(e.target.files[0]); e.target.value = ""; }}
              />
              <DropdownMenu
                ariaLabel="ワークフローメニュー"
                trigger={<IconDots />}
                items={[
                  { label: "📥 JSON をインポート", onSelect: () => importRef.current?.click() },
                  { label: "🔑 シークレット管理", onSelect: () => setShowSecrets(true) },
                ]}
              />
              <button
                onClick={() => create.mutate()}
                disabled={create.isPending}
                className="hidden items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700 md:flex"
              >
                <IconPlus /> 新規ワークフロー
              </button>
            </>
          )}
        </div>
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
          <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
            <button
              onClick={() => setShowSamples(true)}
              className="rounded-xl border border-zinc-300 px-4 py-2 text-sm font-medium hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-600"
            >
              📖 サンプルから始める
            </button>
            {can("workflows.edit") && (
              <button
                onClick={() => create.mutate()}
                className="rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700"
              >
                最初のワークフローを作成
              </button>
            )}
          </div>
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
                    { label: "エクスポート (JSON)", onSelect: () => void exportWorkflow(wf) },
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

      {showSecrets && <SecretsSheet onClose={() => setShowSecrets(false)} />}
      {showSamples && (
        <Suspense fallback={null}>
          <SampleBook onClose={() => setShowSamples(false)} />
        </Suspense>
      )}
      {showAssistant && (
        <Suspense fallback={null}>
          <AssistantChat onClose={() => setShowAssistant(false)} />
        </Suspense>
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

/** シークレット管理: {{secrets.名前}} でワークフローから参照する暗号化値。値は表示できない */
function SecretsSheet({ onClose }: { onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const { data: secrets } = useQuery({
    queryKey: ["wf-secrets"],
    queryFn: () => api<{ name: string; updated_at: string }[]>("/workflows-secrets"),
  });
  const put = useMutation({
    mutationFn: () => api(`/workflows-secrets/${encodeURIComponent(name.trim())}`, { method: "PUT", json: { value } }),
    onSuccess: () => {
      show(`「${name.trim()}」を保存しました`);
      setName(""); setValue("");
      qc.invalidateQueries({ queryKey: ["wf-secrets"] });
    },
    onError: (e) => show(e instanceof Error ? e.message : "保存に失敗しました", "error"),
  });
  const del = useMutation({
    mutationFn: (n: string) => api(`/workflows-secrets/${encodeURIComponent(n)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wf-secrets"] }),
    onError: (e) => show(e instanceof Error ? e.message : "削除に失敗しました", "error"),
  });
  const cls = "rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <BottomSheet title="🔑 シークレット管理" onClose={onClose} wide>
      <p className="mb-3 text-xs text-zinc-400">
        API キーなどを暗号化して保存し、ノード設定から <code className="font-mono">{"{{secrets.名前}}"}</code> で参照できます。
        定義 JSON に平文が残らず、エクスポートや共有が安全になります。値は保存後に表示できません。
      </p>
      <div className="mb-4 flex flex-wrap gap-2">
        <input value={name} onChange={(e) => setName(e.target.value.replace(/[^A-Za-z0-9_]/g, ""))} placeholder="名前（例: OPENAI_KEY）" className={`${cls} w-44 font-mono text-xs`} />
        <input value={value} onChange={(e) => setValue(e.target.value)} type="password" placeholder="値" className={`${cls} min-w-0 flex-1 font-mono text-xs`} />
        <button
          onClick={() => put.mutate()}
          disabled={!name.trim() || !value || put.isPending}
          className="rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
        >
          保存
        </button>
      </div>
      {!secrets || secrets.length === 0 ? (
        <p className="py-4 text-center text-xs text-zinc-400">シークレットはまだありません</p>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {secrets.map((s) => (
            <li key={s.name} className="flex items-center gap-2 py-2">
              <code className="min-w-0 flex-1 truncate font-mono text-xs">{"{{secrets." + s.name + "}}"}</code>
              <span className="num text-[10px] text-zinc-400">{new Date(s.updated_at + (s.updated_at.endsWith("Z") ? "" : "Z")).toLocaleDateString("ja-JP")}</span>
              <button onClick={() => del.mutate(s.name)} className="shrink-0 rounded px-2 py-1 text-xs text-red-500 hover:bg-red-50 dark:hover:bg-red-950/40">削除</button>
            </li>
          ))}
        </ul>
      )}
    </BottomSheet>
  );
}
