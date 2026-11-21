/** 知識の横断（K2）: 承認済みの知識エッセンスを文献ごとに横断して探し、別の文献 2〜3 件で比較する。
 *  検索の欄と種類の絞り込み → 結果のカード（文献の題名から文献の画面へ行ける・引用は折りたたみ・
 *  承認の日時）→「比較に加える」（別の文献 2〜3 件まで）→「比較する」で種類ごとの表。
 *  比較は並べて見せるだけ（AI の判断は足さない。backend/app/knowledge/cross.py と対になる）。
 *  何も承認されていないときは、文献の画面で抽出・承認するよう案内する。
 *  手本: KnowledgeSearch（検索の形・DocumentSheet を開く導線）と EssenceSection（カード・引用の折りたたみ）。 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { knowledgeApi, ESSENCE_KINDS, type CrossCompareResult, type CrossItem } from "../../api/knowledge";
import { EmptyState, IconButton, secondaryButtonClass } from "../../components/ui";
import { IconSearch } from "../../components/icons";
import { useToasts } from "../../stores";
import { DocumentSheet } from "./DocumentSheet";

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const MAX_COMPARE = 3;

export function EssenceCross() {
  const show = useToasts((s) => s.show);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [items, setItems] = useState<CrossItem[] | null>(null);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [kinds, setKinds] = useState<Record<string, string>>(ESSENCE_KINDS);
  const [last, setLast] = useState<{ q: string; kind: string } | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [compare, setCompare] = useState<CrossCompareResult | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const search = useMutation({
    // 続き（cursor > 0）は「最後に検索した条件」で取る。検索欄の現在値を変えても、
    // 表示中の結果の続きに混ざらないようにする。
    mutationFn: (args: { q: string; kind: string; cursor: number }) =>
      knowledgeApi.searchEssences(args.q, args.kind, args.cursor),
    onSuccess: (r, args) => {
      setKinds(r.kinds);
      setItems(args.cursor > 0 && items !== null ? [...items, ...r.items] : r.items);
      setNextCursor(r.next_cursor);
      setLast({ q: args.q, kind: args.kind });
    },
    onError: (e) => show(e instanceof Error ? e.message : String(e), "error"),
  });
  const searchFrom = (q: string, kind: string, cursor: number) =>
    search.mutate({ q, kind, cursor });
  const runCompare = useMutation({
    mutationFn: () => knowledgeApi.compareEssences(selected),
    onSuccess: (r) => setCompare(r),
    onError: (e) => show(e instanceof Error ? e.message : String(e), "error"),
  });

  const toggleCompare = (item: CrossItem) => {
    setCompare(null);  // 検索・選択状態が変わったときは古い比較を破棄する
    if (selected.includes(item.doc_ref)) {
      setSelected(selected.filter((r) => r !== item.doc_ref));
      return;
    }
    if (selected.length >= MAX_COMPARE) {
      show(`比較は別の文献 ${MAX_COMPARE} 件までです。外すものを選んでください`);
      return;
    }
    setSelected([...selected, item.doc_ref]);
    setTitles({ ...titles, [item.doc_ref]: item.doc_title });
  };

  return (
    <div className="space-y-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim()) searchFrom(query.trim(), kind, 0); }}>
        <input className={input} value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="言葉で知識を探す（例: 温度 上昇、振動 抑える）" />
        <IconButton label="探す" variant="primary" type="submit" disabled={!query.trim() || search.isPending}><IconSearch /></IconButton>
      </form>
      <div className="flex flex-wrap items-center gap-2">
        <select value={kind} onChange={(e) => setKind(e.target.value)}
          aria-label="種類の絞り込み" className={`${input} max-w-56`}>
          <option value="">すべての種類</option>
          {Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>

      {items && items.length === 0 && nextCursor === null && (
        <EmptyState>
          見つかりませんでした。文献の画面で知識を抽出し、承認すると、ここで横断して探せます。
          まだ承認していなければ、まず「探す」で文献を開いて知識を承認してください。
        </EmptyState>
      )}
      {items && items.length > 0 && (
        <ul className="space-y-2">
          {items.map((item, i) => (
            <ItemCard key={`${item.essence_ref}:${item.item_index}:${i}`} item={item} kinds={kinds}
              selected={selected.includes(item.doc_ref)} onToggle={() => toggleCompare(item)} onOpen={() => setOpen(item.doc_ref)} />
          ))}
        </ul>
      )}
      {items && nextCursor !== null && (
        <div className="flex justify-center">
          <button className={secondaryButtonClass}
            disabled={search.isPending || last === null}
            onClick={() => last && searchFrom(last.q, last.kind, nextCursor)}>
            {search.isPending ? "読み込み中..." : "もっと見る"}
          </button>
        </div>
      )}

      {selected.length > 0 && (
        <div className="rounded-2xl border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
          <p className="text-xs text-zinc-500">
            比較に加えた文献 <span className="num">{selected.length}/{MAX_COMPARE}</span>
            <span className="ml-2 inline-flex flex-wrap gap-1">
              {selected.map((ref) => (
                <button key={ref} onClick={() => { setCompare(null); setSelected(selected.filter((r) => r !== ref)); }}
                  className="min-h-9 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700">
                  {titles[ref] ?? ref} ✕
                </button>
              ))}
            </span>
          </p>
          <div className="mt-2">
            <button className={secondaryButtonClass} disabled={selected.length < 2 || runCompare.isPending}
              onClick={() => runCompare.mutate()}>
              {runCompare.isPending ? "並べています..." : "比較する（別の文献 2〜3 件）"}
            </button>
            {selected.length < 2 && <p className="mt-1 text-xs text-zinc-400">比較には別の文献 2 件以上を選んでください。</p>}
          </div>
        </div>
      )}

      {compare && <CompareTable result={compare} onOpen={setOpen} />}

      {open && <DocumentSheet docRef={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ItemCard({ item, kinds, selected, onToggle, onOpen }: {
  item: CrossItem; kinds: Record<string, string>; selected: boolean; onToggle: () => void; onOpen: () => void;
}) {
  return (
    <li className="rounded-2xl border border-zinc-200 bg-white p-3 text-sm shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={onOpen} className="min-w-0 flex-1 text-left">
          <span className="truncate font-semibold text-accent-700 dark:text-accent-300">{item.doc_title}</span>
        </button>
        <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {kinds[item.kind] ?? item.kind}
        </span>
        <span className="num shrink-0 text-[11px] text-zinc-400">
          {item.reviewed_at ? `承認 ${new Date(item.reviewed_at * 1000).toLocaleString("ja-JP")}` : ""}
        </span>
      </div>
      <p className="mt-1.5 break-words font-medium leading-relaxed">{item.summary}</p>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-xs text-accent-700 dark:text-accent-300">引用</summary>
        <blockquote className="mt-1 whitespace-pre-wrap break-words border-l-2 border-zinc-300 pl-2 text-xs leading-relaxed text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
          {item.quote}
        </blockquote>
      </details>
      <div className="mt-2">
        <button className={selected
          ? "min-h-11 w-full rounded-xl border border-accent-600 px-3 text-sm font-medium text-accent-700 dark:text-accent-300"
          : `${secondaryButtonClass} w-full`} onClick={onToggle}>
          {selected ? "✓ 比較に加えてある（外す）" : "比較に加える"}
        </button>
      </div>
    </li>
  );
}

/** 種類ごとの表。320px では横スクロール（縦積みにすると文献の対応が読み取れなくなるため）。 */
function CompareTable({ result, onOpen }: { result: CrossCompareResult; onOpen: (ref: string) => void }) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-zinc-500">
        比較は承認済みの知識を並べて見せるだけです（AI の判断は加えていません）。
      </p>
      {result.rows.length === 0 ? (
        <EmptyState>選んだ文献には、まだ承認済みの知識がありません。文献の画面で知識を承認してください。</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-zinc-200 dark:border-zinc-800">
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-left text-xs text-zinc-500 dark:border-zinc-800">
                <th className="w-24 px-3 py-2 font-semibold">種類</th>
                {result.documents.map((d) => (
                  <th key={d.ref} className="px-3 py-2 font-semibold">
                    <button onClick={() => onOpen(d.ref)} className="min-h-9 text-left text-accent-700 dark:text-accent-300">
                      {d.title}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row) => (
                <tr key={row.kind} className="border-b border-zinc-100 align-top last:border-b-0 dark:border-zinc-800/60">
                  <th className="px-3 py-2 text-left text-xs font-semibold text-zinc-500">{row.label}</th>
                  {row.cells.map((cell, i) => (
                    <td key={i} className="px-3 py-2">
                      {cell.length === 0 ? (
                        <span className="text-xs text-zinc-400">—</span>
                      ) : cell.map((item, j) => (
                        <div key={j} className="mb-2 last:mb-0">
                          <p className="break-words font-medium leading-relaxed">{item.summary}</p>
                          <details className="mt-0.5">
                            <summary className="cursor-pointer text-xs text-accent-700 dark:text-accent-300">引用</summary>
                            <blockquote className="mt-1 whitespace-pre-wrap break-words border-l-2 border-zinc-300 pl-2 text-xs leading-relaxed text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
                              {item.quote}
                            </blockquote>
                          </details>
                        </div>
                      ))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
