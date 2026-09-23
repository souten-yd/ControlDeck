/** Existing headless Jobs, including agents that survived a Host restart. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

interface Run {
  id: string;
  kind: string;
  title: string;
  status: string;
  phase?: string | null;
  error: string;
  progress?: { status?: string };
}
const queryKey = ["opencode-background-runs"];
const labels: Record<string, string> = {
  queued: "開始待ち", running: "実行中", succeeded: "完了",
  failed: "失敗", canceled: "停止済み", interrupted: "中断",
};

export function BackgroundRuns() {
  const [open, setOpen] = useState(false);
  const [stopping, setStopping] = useState<Run | null>(null);
  const canView = useAuth((state) => state.can("workflows.run"));
  const canStop = useAuth((state) => state.can("workflows.edit"));
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const query = useQuery({
    queryKey,
    queryFn: () => api<Run[]>("/jobs?kind=opencode.run&limit=100"),
    enabled: canView && open,
    refetchInterval: open ? 5000 : false,
  });
  const stop = useMutation({
    mutationFn: (id: string) => api(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
    onSuccess: () => {
      setStopping(null);
      qc.invalidateQueries({ queryKey });
      show("停止を要求しました");
    },
    onError: (error) => {
      setStopping(null);
      qc.invalidateQueries({ queryKey });
      show(error instanceof Error ? error.message : "停止できませんでした", "error");
    },
  });
  if (!canView) return null;
  return (
    <details className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800"
      onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="min-h-11 cursor-pointer py-2 text-sm font-medium">バックグラウンドの実行</summary>
      {open && <div className="space-y-3 pt-2">
        <p className="text-xs text-zinc-500">APIから依頼されたOpenCodeの実行を確認・停止できます。最近の100件を表示します。</p>
        {query.isPending && <p className="text-sm text-zinc-500">実行状態を確認中…</p>}
        {query.isError && <p role="alert" className="break-words text-sm text-red-600 dark:text-red-400">
          {query.error.message} — 実行状態を再確認できません。
        </p>}
        {query.data?.length === 0 && <p className="text-sm text-zinc-500">実行履歴はありません。</p>}
        <ul className="space-y-2">
          {query.data?.filter((run) => run.kind === "opencode.run").map((run) => {
            const active = run.status === "running" || run.status === "queued";
            const external = run.phase?.startsWith("external_");
            const label = external ? run.progress?.status : labels[run.status] ?? run.status;
            return <li key={run.id} className="space-y-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-900">
              <p className="break-words text-sm font-medium">{run.title || "OpenCode"}</p>
              <p className="break-words text-xs text-zinc-500">{label}</p>
              {run.error && <p className="break-words text-xs text-zinc-500">{run.error}</p>}
              {active && canStop && <button type="button" disabled={stop.isPending || query.isError}
                aria-label={`${run.title || "OpenCode"} を停止`}
                onClick={() => setStopping(run)}
                className="min-h-11 rounded-lg border border-zinc-300 px-3 py-2 text-sm disabled:opacity-40 dark:border-zinc-700">
                停止
              </button>}
            </li>;
          })}
        </ul>
      </div>}
      {stopping && <ConfirmDialog title="OpenCodeの実行を停止しますか？"
        message={`「${stopping.title || "OpenCode"}」の処理を停止します。作成済みのファイルは削除しません。`}
        confirmLabel={stop.isPending ? "停止中…" : "停止する"}
        onConfirm={() => { if (!stop.isPending) stop.mutate(stopping.id); }} onClose={() => setStopping(null)} />}
    </details>
  );
}
