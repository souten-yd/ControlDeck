/** 案件の「ウォッチ」: 公開データ（EPO・USPTO・OpenAlex など）を決まった間隔で検索し、新しく見つかった公報を候補に出す。
 *  候補は「先行文献に登録」（X/Y/A）か「対象外」に振り分ける。検索語は外部へ送られるので、保存の前に必ず確認を取る
 *  （2026-11-21 R3-2。backend/app/patents/watch.py）。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { patentsApi, type Watch, type WatchHit } from "../../api/patents";
import { useToasts } from "../../stores";
import { BottomSheet, ConfirmDialog, DropdownMenu, EmptyState, Skeleton, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { IconDots } from "../../components/icons";
import { DocumentSheet } from "../knowledge/DocumentSheet";
import type { TabProps } from "./CaseSections";
import { BlockedNotice, OutboundNote, SourceSelect, firstReady, useSources } from "./Sources";
import { blockedOf, errorText, input, type BlockedQuery } from "./shared";

const INTERVALS: [number, string][] = [[6, "6 時間"], [24, "1 日"], [168, "1 週間"], [720, "30 日"]];
const intervalLabel = (h: number) => INTERVALS.find(([v]) => v === h)?.[1] ?? `${h} 時間`;
const HIT_ORDER: Record<string, number> = { new: 0, relevant: 1, registered: 2, excluded: 3 };
const HIT_LABEL: Record<string, string> = { new: "新着", relevant: "関連", registered: "登録済み", excluded: "対象外" };

function when(t: number | null) {
  return t ? new Date(t * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

export function WatchSection({ c, meta, editable, onError }: TabProps) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const key = ["patent-watches", c.id];
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: key, queryFn: () => patentsApi.watches(c.id) });
  const [form, setForm] = useState<Watch | "new" | null>(null);
  const [deleting, setDeleting] = useState<Watch | null>(null);
  const [category, setCategory] = useState("A");
  const [open, setOpen] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: key });
  const run = useMutation({ mutationFn: (w: Watch) => patentsApi.runWatch(c.id, w.id), onSuccess: () => show("取得を始めました"), onError });
  const toggle = useMutation({
    mutationFn: (w: Watch) => patentsApi.updateWatch(c.id, w.id, {
      source: w.source, query: w.query, interval_hours: w.interval_hours, enabled: !w.enabled,
    }),
    onSuccess: refresh, onError,
  });
  const remove = useMutation({
    mutationFn: (w: Watch) => patentsApi.removeWatch(c.id, w.id),
    onSuccess: () => { setDeleting(null); show("削除しました"); refresh(); }, onError,
  });
  const decide = useMutation({
    mutationFn: ({ hit, status }: { hit: WatchHit; status: string }) =>
      patentsApi.decideHit(c.id, hit.id, status === "registered" ? { status, category } : { status }),
    onSuccess: (_, { status }) => {
      refresh();
      if (status === "registered") { qc.invalidateQueries({ queryKey: ["patent", c.id] }); show("先行文献に登録しました"); }
    },
    onError,
  });

  if (isLoading) return <Skeleton className="h-40" />;
  if (isError || !data) {
    return <EmptyState actions={<button className={secondaryButtonClass} onClick={() => void refetch()}>もう一度読み込む</button>}>ウォッチを読み込めませんでした。</EmptyState>;
  }
  const hits = [...data.hits].sort((a, b) => (HIT_ORDER[a.status] ?? 9) - (HIT_ORDER[b.status] ?? 9) || b.created - a.created);
  const pending = hits.filter((h) => h.status === "new" || h.status === "relevant");
  const done = hits.filter((h) => h.status === "registered" || h.status === "excluded");

  return (
    <div className="space-y-5">
      <section>
        <div className="mb-2 flex items-center gap-2">
          <h3 className="flex-1 text-xs font-semibold text-zinc-500">ウォッチ</h3>
          {editable && <button type="button" className={secondaryButtonClass} onClick={() => setForm("new")}>ウォッチを追加</button>}
        </div>
        {data.watches.length === 0 ? (
          <p className="text-sm text-zinc-500">まだありません。検索語と取得先を決めて追加します。</p>
        ) : (
          <ul className="space-y-2">
            {data.watches.map((w) => (
              <li key={w.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-sm font-medium">{w.query}</p>
                    <p className="mt-0.5 text-xs text-zinc-500">{w.source_label} · {intervalLabel(w.interval_hours)}ごと</p>
                    <p className="mt-1 flex flex-wrap gap-1.5 text-xs">
                      {!w.enabled && <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">止めています</span>}
                      <span className="num min-w-0 break-words text-zinc-500">{w.last_run ? `前回 ${when(w.last_run)}${w.last_status ? ` · ${w.last_status}` : ""}` : "まだ動いていません"}</span>
                    </p>
                  </div>
                  {editable && (
                    <DropdownMenu ariaLabel="ウォッチの操作" trigger={<IconDots />} items={[
                      { label: "いま取得", onSelect: () => run.mutate(w) },
                      { label: "直す", onSelect: () => setForm(w) },
                      { label: w.enabled ? "止める" : "再開する", onSelect: () => toggle.mutate(w) },
                      { label: "削除", danger: true, separated: true, onSelect: () => setDeleting(w) },
                    ]} />
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold text-zinc-500">見つかった公報{pending.length > 0 && <span className="num font-normal">（未処理 {pending.length}）</span>}</h3>
        {pending.length === 0 ? <p className="text-sm text-zinc-500">未処理の候補はありません。</p> : (
          <>
            {editable && (
              <label className="mb-2 block text-xs text-zinc-500">登録するときの分類
                <select className={`${input} mt-1`} value={category} onChange={(e) => setCategory(e.target.value)}>
                  {Object.entries(meta.ref_categories).map(([k, label]) => <option key={k} value={k}>{k}: {label}</option>)}
                </select>
              </label>
            )}
            <ul className="space-y-2">
              {pending.map((h) => (
                <li key={h.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
                  <button type="button" className="block w-full min-w-0 rounded-lg text-left hover:text-accent-700 dark:hover:text-accent-300" onClick={() => setOpen(h.doc_ref)}>
                    <span className="flex items-center gap-2">
                      {h.status === "new" && <span className="shrink-0 rounded-full bg-accent-600/15 px-2 py-0.5 text-[11px] font-medium text-accent-700 dark:text-accent-300">新着</span>}
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">{h.title || "（題名なし）"}</span>
                    </span>
                    <span className="num mt-0.5 block text-xs text-zinc-500">{when(h.created)}</span>
                  </button>
                  {editable && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button type="button" className={`${primaryButtonClass} flex-1 sm:flex-none`} disabled={decide.isPending}
                        aria-label={`先行文献に登録（${category}）`} onClick={() => decide.mutate({ hit: h, status: "registered" })}>
                        <span className="sm:hidden">登録（{category}）</span><span className="hidden sm:inline">先行文献に登録（{category}）</span>
                      </button>
                      <button type="button" className={`${secondaryButtonClass} flex-1 sm:flex-none`} disabled={decide.isPending}
                        onClick={() => decide.mutate({ hit: h, status: "excluded" })}>対象外</button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        {done.length > 0 && (
          <div className="mt-3">
            <button type="button" className="min-h-11 text-xs text-zinc-500 hover:underline" aria-expanded={showDone} onClick={() => setShowDone(!showDone)}>
              {showDone ? "処理済みを閉じる" : `処理済み（${done.length}）`}
            </button>
            {showDone && (
              <ul className="space-y-1">
                {done.map((h) => (
                  <li key={h.id}>
                    <button type="button" className="flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800" onClick={() => setOpen(h.doc_ref)}>
                      <span className="min-w-0 flex-1 truncate">{h.title || "（題名なし）"}</span>
                      <span className="shrink-0 text-xs text-zinc-500">{HIT_LABEL[h.status] ?? h.status}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {form && <WatchForm caseId={c.id} watch={form === "new" ? null : form} onClose={() => setForm(null)} onSaved={() => { setForm(null); refresh(); }} onError={onError} />}
      {deleting && (
        <ConfirmDialog title="ウォッチを削除しますか？" message="このウォッチの定期の取得をやめます。取り込んだ公報と、先行文献に登録したものは残ります。"
          confirmLabel="削除する" busy={remove.isPending} onConfirm={() => remove.mutate(deleting)} onClose={() => setDeleting(null)} />
      )}
      {open && <DocumentSheet docRef={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** 追加・直す。外へ送る検索語は保存の時と実行のたびにサーバーが自動で検査する。 */
function WatchForm({ caseId, watch, onClose, onSaved, onError }: {
  caseId: number; watch: Watch | null; onClose: () => void; onSaved: () => void; onError: (e: unknown) => void;
}) {
  const show = useToasts((s) => s.show);
  const { data } = useSources();
  const sources = data?.sources ?? [];
  const [source, setSource] = useState(watch?.source ?? "");
  const [query, setQuery] = useState(watch?.query ?? "");
  const [hours, setHours] = useState(watch?.interval_hours ?? 168);
  const [blocked, setBlocked] = useState<BlockedQuery | null>(null);
  const chosenId = source || firstReady(sources);
  const chosen = sources.find((s) => s.id === chosenId);
  const save = useMutation({
    mutationFn: (q: string) => {
      const body = { source: chosenId, query: q.trim(), interval_hours: hours, enabled: watch?.enabled ?? true };
      return watch ? patentsApi.updateWatch(caseId, watch.id, body) : patentsApi.createWatch(caseId, body);
    },
    onSuccess: () => { show(watch ? "保存しました" : "ウォッチを追加しました"); onSaved(); },
    onError: (e) => {
      const b = blockedOf(e);
      if (b) setBlocked(b); else onError(new Error(errorText(e)));
    },
  });
  const ok = Boolean(chosen && (chosen.ready || chosen.id === watch?.source) && query.trim());
  return (
    <BottomSheet title={watch ? "ウォッチを直す" : "ウォッチを追加"} onClose={onClose}>
      {!data ? <Skeleton className="h-40" /> : (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (ok) { setBlocked(null); save.mutate(query); } }}>
          <label className="block text-xs font-medium text-zinc-500">取得先
            <div className="mt-1"><SourceSelect sources={sources} value={chosenId} onChange={setSource} /></div>
          </label>
          <label className="block text-xs font-medium text-zinc-500">検索語
            <input className={`${input} mt-1`} value={query} maxLength={500} placeholder={chosen?.hint} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label className="block text-xs font-medium text-zinc-500">間隔
            <select className={`${input} mt-1`} value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {INTERVALS.map(([v, label]) => <option key={v} value={v}>{label}ごと</option>)}
            </select>
          </label>
          <OutboundNote />
          {blocked && <BlockedNotice blocked={blocked} busy={save.isPending}
            onUse={(q) => { setQuery(q); setBlocked(null); save.mutate(q); }} />}
          <button className={`${primaryButtonClass} w-full`} disabled={!ok || save.isPending}>{watch ? "保存" : "追加"}</button>
        </form>
      )}
    </BottomSheet>
  );
}
