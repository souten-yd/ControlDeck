/** 案件の「分析」: 請求項（無ければ課題・解決手段）に近い公報。先行文献の候補として X/Y/A で登録できる。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { patentsApi } from "../../api/patents";
import { EmptyState, Skeleton, secondaryButtonClass } from "../../components/ui";
import { DocumentSheet } from "../knowledge/DocumentSheet";
import type { TabProps } from "./CaseSections";
import { input } from "./shared";

export function SimilarSection({ c, meta, editable, onSaved, onError }: TabProps) {
  const qc = useQueryClient();
  const [category, setCategory] = useState("A");
  const [open, setOpen] = useState<string | null>(null);
  const version = c.claims[0]?.version ?? 0;
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["patent-similar", c.id, version],
    queryFn: () => patentsApi.similar(c.id),
    staleTime: 60_000,
  });
  const add = useMutation({
    mutationFn: (hit: { ref: string; title: string; snippet: string }) =>
      patentsApi.addRef(c.id, { doc_ref: hit.ref, ref: hit.title, category, relevance: hit.snippet.slice(0, 200) }),
    onSuccess: (next) => { onSaved(next); qc.invalidateQueries({ queryKey: ["patent-similar", c.id] }); },
    onError,
  });
  const registered = new Set(c.refs.map((r) => r.doc_ref).filter(Boolean));
  const results = data?.results ?? [];
  const applicants = new Map<string, number>();
  for (const h of results) for (const a of h.applicants.slice(0, 1)) applicants.set(a, (applicants.get(a) ?? 0) + 1);
  const topApplicants = [...applicants.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);

  if (isLoading) return <Skeleton className="h-40" />;
  if (isError) {
    return <EmptyState actions={<button className={secondaryButtonClass} onClick={() => void refetch()}>もう一度探す</button>}>似た公報を探せませんでした。</EmptyState>;
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-600 dark:text-zinc-300">
        {c.claims.length ? "最新の請求項" : "課題と解決手段"}に近い公報です。先行文献の候補として登録できます。
      </p>
      {results.length === 0 ? (
        <EmptyState actions={<Link to="/patents?tab=docs" className={secondaryButtonClass}>公報を取り込む</Link>}>
          似た公報は見つかりませんでした。公報を取り込むと、ここに候補が出ます。
        </EmptyState>
      ) : (
        <>
          {topApplicants.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-zinc-500">よく出てくる出願人</p>
              <p className="flex flex-wrap gap-1.5">
                {topApplicants.map(([name, n]) => (
                  <span key={name} className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                    {name} <span className="num text-zinc-500">{n}</span>
                  </span>
                ))}
              </p>
            </div>
          )}
          {editable && (
            <label className="block text-xs text-zinc-500">登録するときの分類
              <select className={`${input} mt-1`} value={category} onChange={(e) => setCategory(e.target.value)}>
                {Object.entries(meta.ref_categories).map(([k, label]) => <option key={k} value={k}>{k}: {label}</option>)}
              </select>
            </label>
          )}
          <ul className="space-y-2">
            {results.map((h) => {
              const done = h.registered || registered.has(h.ref);
              const facts = [
                h.applicants.length ? h.applicants[0] + (h.applicants.length > 1 ? ` ほか ${h.applicants.length - 1}` : "") : "",
                h.ipc.slice(0, 2).join("・"), h.date,
              ].filter(Boolean);
              return (
                <li key={h.ref} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
                  <div className="flex items-start gap-2">
                    <button type="button" className="min-w-0 flex-1 rounded-lg text-left hover:text-accent-700 dark:hover:text-accent-300" onClick={() => setOpen(h.ref)}>
                      <span className="block truncate text-sm font-medium">{h.title || "（題名なし）"}</span>
                      {facts.length > 0 && <span className="num mt-0.5 block truncate text-xs text-zinc-500">{facts.join(" · ")}</span>}
                    </button>
                    {done ? (
                      <span className="shrink-0 rounded-full bg-accent-600/15 px-2.5 py-1 text-xs font-medium text-accent-700 dark:text-accent-300">登録済み</span>
                    ) : editable && (
                      <button type="button" className="min-h-11 shrink-0 rounded-xl px-3 text-sm text-accent-700 hover:bg-accent-600/10 disabled:opacity-40 dark:text-accent-300"
                        disabled={add.isPending} onClick={() => add.mutate(h)}>{category} で登録</button>
                    )}
                  </div>
                  {h.snippet && <p className="mt-1 line-clamp-2 text-xs text-zinc-600 dark:text-zinc-400">{h.snippet}</p>}
                </li>
              );
            })}
          </ul>
          {data?.query && (
            <details className="text-xs text-zinc-500">
              <summary className="cursor-pointer">詳細</summary>
              <p className="mt-1">探した言葉: {data.query}</p>
            </details>
          )}
        </>
      )}
      {open && <DocumentSheet docRef={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
