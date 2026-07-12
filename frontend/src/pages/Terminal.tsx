import { lazy, Suspense, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useToasts } from "../stores";
import { ConfirmDialog, Skeleton } from "../components/ui";
import { IconPlus, IconTrash } from "../components/icons";

const XtermView = lazy(() => import("../features/terminal/XtermView"));

interface TerminalSession {
  id: string;
  name: string;
  created_at: number;
  attached: boolean;
  persistent: boolean;
}

export default function TerminalPage() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [active, setActive] = useState<string | null>(null);
  const [killing, setKilling] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["terminals"],
    queryFn: () => api<{ tmux: boolean; sessions: TerminalSession[] }>("/terminals"),
    refetchInterval: active ? false : 10_000,
  });

  const create = async () => {
    try {
      const s = await api<{ id: string }>("/terminals", { method: "POST" });
      qc.invalidateQueries({ queryKey: ["terminals"] });
      setActive(s.id);
    } catch (e) {
      show(e instanceof Error ? e.message : "セッション作成に失敗しました", "error");
    }
  };

  const kill = async (id: string) => {
    try {
      await api(`/terminals/${id}`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["terminals"] });
      if (active === id) setActive(null);
      show("セッションを終了しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "終了に失敗しました", "error");
    }
    setKilling(null);
  };

  // 接続中は全画面ターミナル
  if (active) {
    return (
      <Suspense fallback={<div className="grid h-full place-items-center text-sm text-zinc-400">ターミナルを読み込み中...</div>}>
        <XtermView
          sessionId={active}
          sessions={data?.sessions ?? []}
          onSwitch={setActive}
          onExit={() => {
            setActive(null);
            qc.invalidateQueries({ queryKey: ["terminals"] });
          }}
        />
      </Suspense>
    );
  }

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">ターミナル</h1>
        <button
          onClick={create}
          className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700"
        >
          <IconPlus /> 新規セッション
        </button>
      </div>

      {data && !data.tmux && (
        <p className="mb-4 rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
          tmux が未インストールのため、セッションはバックエンド再起動で失われます。
          永続化するには <code className="font-mono">sudo apt install tmux</code> を実行してください。
        </p>
      )}

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : !data || data.sessions.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">アクティブなセッションはありません</p>
          <button
            onClick={create}
            className="mt-3 rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700"
          >
            セッションを開始
          </button>
        </div>
      ) : (
        <ul className="divide-y divide-zinc-100 overflow-hidden rounded-2xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {data.sessions.map((s) => (
            <li key={s.id} className="flex items-center gap-3 bg-white px-4 py-3 dark:bg-zinc-900">
              <button onClick={() => setActive(s.id)} className="min-w-0 flex-1 text-left">
                <p className="truncate font-mono text-sm">{s.name}</p>
                <p className="text-xs text-zinc-400">
                  {s.created_at ? new Date(s.created_at * 1000).toLocaleString("ja-JP") : ""}
                  {s.attached && " · 接続中"}
                  {s.persistent && " · 永続 (tmux)"}
                </p>
              </button>
              <button
                onClick={() => setActive(s.id)}
                className="rounded-xl bg-accent-50 px-3.5 py-2 text-sm font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
              >
                接続
              </button>
              <button
                onClick={() => setKilling(s.id)}
                aria-label={`${s.name} を終了`}
                className="rounded-lg p-2 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40"
              >
                <IconTrash />
              </button>
            </li>
          ))}
        </ul>
      )}

      {killing && (
        <ConfirmDialog
          title="セッションを終了しますか？"
          message="実行中のプロセスはすべて終了します。この操作は取り消せません。"
          confirmLabel="終了する"
          onConfirm={() => kill(killing)}
          onClose={() => setKilling(null)}
        />
      )}
    </div>
  );
}
