/** ナレッジ全体（資料・特許・規格）を文書の単位で探す。番号の一致・参照の関係もここに出る。 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { knowledgeApi, RELATION_LABEL, type FoundDocument, type KnowledgeScope } from "../../api/knowledge";
import { EmptyState, IconButton, SegmentedTabs } from "../../components/ui";
import { IconSearch } from "../../components/icons";
import { useToasts } from "../../stores";
import { DocumentSheet } from "./DocumentSheet";

const SCOPES: ReadonlyArray<readonly [KnowledgeScope, string]> = [
  ["all", "すべて"], ["general", "資料"], ["patents", "特許"], ["standards", "規格"],
];
const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

export function KnowledgeSearch({ initialScope = "all" }: { initialScope?: KnowledgeScope }) {
  const show = useToasts((s) => s.show);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<KnowledgeScope>(initialScope);
  const [results, setResults] = useState<FoundDocument[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const find = useMutation({
    mutationFn: () => knowledgeApi.find(query.trim(), scope),
    onSuccess: (r) => setResults(r.results),
    onError: (e) => show(e instanceof Error ? e.message : String(e), "error"),
  });
  return (
    <div className="space-y-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim()) find.mutate(); }}>
        <input className={input} value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="言葉・公報番号・規格番号で探す（例: 静電気放電 試験レベル、JIS C 61000-4-2）" />
        <IconButton label="探す" variant="primary" type="submit" disabled={!query.trim() || find.isPending}><IconSearch /></IconButton>
      </form>
      <SegmentedTabs tabs={SCOPES} value={scope} onChange={setScope} />
      {results && (results.length === 0 ? <EmptyState>見つかりませんでした。言葉を変えるか、範囲を「すべて」にしてください。</EmptyState> : (
        <ul className="space-y-2">
          {results.map((d) => (
            <li key={d.ref}>
              <button onClick={() => setOpen(d.ref)} className="w-full rounded-2xl border border-zinc-200 bg-white p-3 text-left shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
                <span className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold">{d.title}</span>
                  {RELATION_LABEL[d.relation] && <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{RELATION_LABEL[d.relation]}</span>}
                </span>
                {d.passages[0] && <span className="mt-1 line-clamp-2 block text-xs text-zinc-600 dark:text-zinc-400">{d.passages[0].text}</span>}
              </button>
            </li>
          ))}
        </ul>
      ))}
      {open && <DocumentSheet docRef={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
