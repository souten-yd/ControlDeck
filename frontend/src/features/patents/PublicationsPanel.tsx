/** /patents の「公報」: 特許の分野の公報・論文を探して開く。取り込みもここから。 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { knowledgeApi, RELATION_LABEL, type FoundDocument } from "../../api/knowledge";
import { useAuth, useToasts } from "../../stores";
import { EmptyState, IconButton, secondaryButtonClass } from "../../components/ui";
import { IconSearch } from "../../components/icons";
import { DocumentSheet } from "../knowledge/DocumentSheet";
import { LiteratureSheet } from "./LiteratureSheet";
import { errorText, input } from "./shared";

export function PublicationsPanel({ importing, onImport, onImportClose }: { importing: boolean; onImport: () => void; onImportClose: () => void }) {
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<FoundDocument[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const find = useMutation({
    mutationFn: () => knowledgeApi.find(query.trim(), "patents"),
    onSuccess: (r) => setResults(r.results),
    onError: (e) => show(errorText(e), "error"),
  });
  return (
    <div className="space-y-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim()) find.mutate(); }}>
        <input className={input} value={query} onChange={(e) => setQuery(e.target.value)} aria-label="公報を探す"
          placeholder="言葉・公報番号で探す（例: ゲート駆動 損失、特開2026-116114）" />
        <IconButton label="探す" variant="primary" type="submit" disabled={!query.trim() || find.isPending}><IconSearch /></IconButton>
      </form>
      {can("workflows.edit") && (
        <button className={`${secondaryButtonClass} w-full sm:w-auto`} onClick={onImport}>公報・論文を取り込む</button>
      )}
      {results === null ? (
        <p className="text-xs text-zinc-500">取り込んだ公報・論文から探します。開くと本文・図・書誌を見られます。</p>
      ) : results.length === 0 ? (
        <EmptyState>見つかりませんでした。言葉を変えるか、公報を取り込んでください。</EmptyState>
      ) : (
        <ul className="space-y-2">
          {results.map((d) => (
            <li key={d.ref}>
              <button onClick={() => setOpen(d.ref)} className="w-full rounded-2xl border border-zinc-200 bg-white p-3 text-left shadow-sm hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700">
                <span className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold">{d.title || "（題名なし）"}</span>
                  {RELATION_LABEL[d.relation] && <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{RELATION_LABEL[d.relation]}</span>}
                </span>
                {d.passages[0] && <span className="mt-1 line-clamp-2 block text-xs text-zinc-600 dark:text-zinc-400">{d.passages[0].text}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && <DocumentSheet docRef={open} onClose={() => setOpen(null)} />}
      {importing && <LiteratureSheet onClose={onImportClose} />}
    </div>
  );
}
