/** 「別の文献の知識と結ぶ」（K3）: 技術仮説のリンクを作る相手選び。EssenceSection のカードから使う。
 *  相手は K2 の横断検索（承認済みだけが返る）から選ぶ。結ぶ側の文献は選べない。
 *  理由のメモは必須。作っただけでは未確認（承認は「知識のつながり」で別の操作として行う）。
 *  手本: EssenceCross（横断検索の結果をカードで出す形）。 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { knowledgeApi, type CrossItem } from "../../api/knowledge";
import { secondaryButtonClass } from "../../components/ui";
import { useToasts } from "../../stores";

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";

export function LinkMaker({ sourceEssenceRef, sourceItem, excludeDocRef, essenceLabel, onDone }: {
  sourceEssenceRef: string; sourceItem: number; excludeDocRef: string;
  essenceLabel: string; onDone: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CrossItem[] | null>(null);
  const [picked, setPicked] = useState<CrossItem | null>(null);
  const [note, setNote] = useState("");
  const search = useQuery({
    queryKey: ["link-maker-search", query],
    queryFn: () => knowledgeApi.searchEssences(query),
    enabled: query.trim().length > 0,
  });
  const create = useMutation({
    mutationFn: () => knowledgeApi.createLink(
      sourceEssenceRef, sourceItem, picked!.essence_ref, picked!.item_index, note.trim()),
    onSuccess: () => {
      show("結びました。未確認として保存しました（下の「知識のつながり」に出ます）");
      onDone();
    },
    onError: (e) => show(e instanceof Error ? e.message : "結べませんでした", "error"),
  });
  const found = (search.data?.items ?? []).filter((i) => i.doc_ref !== excludeDocRef);
  return (
    <div className="mt-2 space-y-2 rounded-xl bg-zinc-100 p-3 dark:bg-zinc-800/60">
      <p className="text-xs text-zinc-600 dark:text-zinc-300">
        「{essenceLabel}」を、別の文献の承認済みの知識と結びます（技術仮説・未確認として保存）。
      </p>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setItems(found); }}>
        <input className={input} value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="言葉で相手の知識を探す（例: 振動 抑える）" aria-label="相手の知識の検索" />
        <button type="submit" className={`${secondaryButtonClass} min-h-9 px-3 text-xs`} disabled={!query.trim()}>
          探す
        </button>
      </form>
      {items && items.length === 0 && (
        <p className="text-xs text-zinc-500">見つかりませんでした。別の言葉で探すか、相手の文献で知識を承認してください。</p>
      )}
      {items && items.length > 0 && (
        <ul className="max-h-44 space-y-1 overflow-y-auto">
          {items.map((i) => (
            <li key={`${i.essence_ref}:${i.item_index}`}>
              <button aria-label={`相手を選ぶ: ${i.doc_title}（${i.summary}）`} onClick={() => setPicked(i)}
                className={`min-h-11 w-full rounded-xl px-2 py-1.5 text-left text-xs ${
                  picked && picked.essence_ref === i.essence_ref && picked.item_index === i.item_index
                    ? "border border-accent-600"
                    : "hover:bg-zinc-200/60 dark:hover:bg-zinc-700/50"}`}>
                <span className="block truncate font-medium text-accent-700 dark:text-accent-300">{i.doc_title}</span>
                <span className="block truncate text-zinc-600 dark:text-zinc-300">{i.summary}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {picked && (
        <div className="space-y-1.5">
          <p className="text-xs text-zinc-500">結ぶ相手: {picked.doc_title} の「{picked.summary}」</p>
          <textarea className={`${input} min-h-16`} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="なぜ結ぶのか（理由・メモ。必須）" aria-label="結ぶ理由" />
          <div className="flex gap-2">
            <button className={secondaryButtonClass} disabled={!note.trim() || create.isPending}
              onClick={() => create.mutate()}>
              {create.isPending ? "結んでいます..." : "結ぶ"}
            </button>
            <button className={`${secondaryButtonClass} border-none text-zinc-500`} onClick={onDone}>やめる</button>
          </div>
        </div>
      )}
    </div>
  );
}
