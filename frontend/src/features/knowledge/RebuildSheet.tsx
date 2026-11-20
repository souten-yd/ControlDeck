/** ナレッジの作り直し（AI の回答を推論として区別・埋め込みの作り直し・PDF の取り込み直し）。先に件数だけ見せてから実行する。 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { knowledgeApi } from "../../api/knowledge";
import { useToasts } from "../../stores";
import { BottomSheet, Skeleton, primaryButtonClass } from "../../components/ui";

export function RebuildSheet({ onClose }: { onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const [figures, setFigures] = useState(true);
  const { data, isError } = useQuery({ queryKey: ["knowledge-rebuild"], queryFn: knowledgeApi.rebuildPlan, staleTime: 0 });
  const run = useMutation({
    mutationFn: () => knowledgeApi.rebuild(figures),
    onSuccess: () => { show("作り直しを始めました（Jobs で進み具合を見られます）"); onClose(); },
    onError: (e) => show(e instanceof Error ? e.message : String(e), "error"),
  });
  const rows: [string, number][] = data ? [
    ["AI の回答を推論として区別", data.retag_ai_answers],
    ["埋め込みを作り直す", data.reembed],
    ["PDF を新しい取り込みで作り直す", data.reingest_pdf],
  ] : [];
  const total = rows.reduce((n, [, v]) => n + v, 0);
  return (
    <BottomSheet title="作り直し" onClose={onClose}>
      {isError ? <p className="text-sm text-zinc-500">作り直す対象を調べられませんでした。</p> : !data ? <Skeleton className="h-32" /> : (
        <div className="space-y-4">
          <ul className="divide-y divide-zinc-200 rounded-xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
            {rows.map(([label, n]) => (
              <li key={label} className="flex min-h-11 items-center gap-3 px-3 text-sm">
                <span className="min-w-0 flex-1">{label}</span>
                <span className="num shrink-0 text-zinc-600 dark:text-zinc-300">{n} 件</span>
              </li>
            ))}
          </ul>
          {total === 0 ? <p className="text-sm text-zinc-500">作り直すものはありません。</p> : (
            <>
              <label className="flex items-start gap-3 text-sm">
                <input type="checkbox" className="mt-0.5 h-5 w-5 shrink-0 accent-accent-600" checked={figures} onChange={(e) => setFigures(e.target.checked)} />
                <span>図表を画像モデルで読み取る<span className="block text-xs text-zinc-500">この PC のモデルで読みます。時間がかかります。</span></span>
              </label>
              <button className={`${primaryButtonClass} w-full`} disabled={run.isPending} onClick={() => run.mutate()}>作り直す</button>
            </>
          )}
        </div>
      )}
    </BottomSheet>
  );
}
