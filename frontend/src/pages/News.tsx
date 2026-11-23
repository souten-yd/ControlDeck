import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { SegmentedTabs, Skeleton, primaryButtonClass, secondaryButtonClass } from "../components/ui";
import { relativeTime, useNewsItems, useNewsSources, useRefreshNews, type NewsItem, type NewsView } from "../api/news";
import { useToasts } from "../stores";
import { NewsCard } from "../features/news/NewsCard";
import { InterestPanel, TrendsPanel } from "../features/news/NewsAside";
import { ArchiveDays } from "../features/news/NewsArchive";

type Tab = "recommended" | "latest" | "archive";
const TABS = [["recommended", "おすすめ"], ["latest", "新着"], ["archive", "アーカイブ"]] as const;
// フォーラムの chip は Reddit も含む（どちらも「人の話し合い」）
const CHIPS = [["", "すべて"], ["release", "更新"], ["news", "ニュース"], ["model", "モデル"], ["forum,reddit", "フォーラム"], ["event", "出来事"]] as const;

function CardGrid({ items, hideRemoves, onTopic }: { items: NewsItem[]; hideRemoves: boolean; onTopic: (t: string) => void }) {
  const [first, ...rest] = items;
  if (!first) return null;
  return (
    <div className="space-y-3">
      <NewsCard item={first} hero hideRemoves={hideRemoves} onTopic={onTopic} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {rest.map((item) => <NewsCard key={item.id} item={item} hideRemoves={hideRemoves} onTopic={onTopic} />)}
      </div>
    </div>
  );
}

function GridSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="読み込み中">
      <Skeleton className="h-56 rounded-2xl" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-60 rounded-2xl" />)}</div>
    </div>
  );
}

export default function NewsPage() {
  const toast = useToasts((s) => s.show);
  const [tab, setTab] = useState<Tab>("recommended");
  const [kinds, setKinds] = useState("");
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const [topic, setTopic] = useState("");
  const [starred, setStarred] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setQ(draft), 300);
    return () => clearTimeout(t);
  }, [draft]);

  const searching = q.trim() !== "";
  const archiveList = tab === "archive" && !date && !starred && !searching;   // 日付の一覧を見せている間は記事を取らない
  const view: NewsView = starred && tab === "archive" ? "starred" : searching || tab === "archive" ? "latest" : tab;
  const query = { view, kinds, q, date: tab === "archive" ? date : "", topic, source: "" };
  const items = useNewsItems(query, !archiveList);
  const sources = useNewsSources();
  const refresh = useRefreshNews();
  const hideRemoves = tab !== "archive" && !searching;
  const list = useMemo(
    () => (items.data?.pages.flatMap((p) => p.items) ?? []).filter((item) => !(hideRemoves && item.state.hide)),
    [items.data, hideRemoves],
  );
  const failed = (sources.data?.sources ?? []).filter((s) => !s.ok);
  const notes = (sources.data?.sources ?? []).filter((s) => s.skipped);

  const pickTopic = (t: string) => { setTopic(t); setTab((cur) => (cur === "recommended" ? "latest" : cur)); };
  const runRefresh = () => refresh.mutate(undefined, {
    onSuccess: (r) => toast(!r.started ? (r.reason === "running" ? "取得中です。しばらくお待ちください" : "直前に取得したばかりです")
      : `取り直しました（新着 ${r.new ?? 0} 件${r.failed ? `・取れなかったソース ${r.failed} 件` : ""}）`, r.started ? "success" : "info"),
    onError: () => toast("取り直せませんでした", "error"),
  });
  const title = searching ? `「${q.trim()}」の検索結果` : topic ? `話題「${topic}」` : date ? `${date} の記事` : starred ? "保存した記事" : "";

  return (
    <div className="mx-auto max-w-6xl space-y-3 p-3 md:p-6">
      <PageHeader title="News" description="あなた専門のニュース。使っているソフトの更新・技術ニュース・AI モデルの新着・ControlDeck の出来事を集め、よく開くものから好みを学びます。"
        actions={<button type="button" onClick={runRefresh} disabled={refresh.isPending} className={`${secondaryButtonClass} min-h-9 px-3 text-xs`}>{refresh.isPending ? "取得中…" : "取り直す"}</button>} />
      <SegmentedTabs tabs={TABS} value={tab} onChange={(next) => { setTab(next); setDate(""); setStarred(false); }} />
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div role="group" aria-label="種類" className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto pb-1 sm:flex-wrap sm:overflow-visible sm:pb-0">
          {CHIPS.map(([id, label]) => (
            <button key={id} type="button" aria-pressed={kinds === id} onClick={() => setKinds(id)}
              className={`min-h-9 shrink-0 rounded-full border px-3 text-xs font-medium ${kinds === id ? "border-accent-600 bg-accent-600 text-white" : "border-zinc-300 hover:border-zinc-400 dark:border-zinc-700"}`}>{label}</button>
          ))}
        </div>
        <label className="block sm:w-64">
          <span className="sr-only">アーカイブ全体を検索</span>
          <input type="search" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="アーカイブ全体を検索"
            className="h-10 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        </label>
      </div>
      {(failed.length > 0 || notes.length > 0) && (
        <details className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-200">
          <summary className="cursor-pointer">{failed.length > 0 ? `取れなかったソースが ${failed.length} 件あります` : "取得していないソースがあります"}</summary>
          <ul className="mt-1.5 space-y-0.5">
            {failed.map((s) => <li key={s.source_key}>{s.label}: {s.error}</li>)}
            {notes.map((s) => <li key={s.source_key}>{s.label}: {s.skipped}</li>)}
          </ul>
        </details>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <main className="min-w-0 space-y-3" aria-label="記事">
          {(title || (tab === "archive" && !archiveList)) && (
            <div className="flex flex-wrap items-center gap-2">
              {tab === "archive" && !searching && (date || starred) && (
                <button type="button" onClick={() => { setDate(""); setStarred(false); }} className="min-h-9 rounded-lg px-2 text-xs font-medium text-accent-600 hover:bg-accent-50 dark:hover:bg-accent-600/10">← 日付の一覧へ</button>
              )}
              {title && <h2 className="text-sm font-semibold">{title}</h2>}
              {topic && <button type="button" onClick={() => setTopic("")} className="rounded-full border border-zinc-300 px-2 py-0.5 text-[11px] dark:border-zinc-700">話題の絞り込みを外す ×</button>}
            </div>
          )}
          {tab === "archive" && !date && !starred && !searching && (
            <>
              <button type="button" onClick={() => setStarred(true)} className={`${secondaryButtonClass} w-full justify-start`}>☆ 保存した記事を見る</button>
              <ArchiveDays onPick={setDate} />
            </>
          )}
          {!archiveList && (items.isLoading ? <GridSkeleton /> : items.isError ? (
            <p role="alert" className="rounded-2xl border border-red-300 p-4 text-sm text-red-600 dark:border-red-800">記事を読み込めませんでした。</p>
          ) : list.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-zinc-300 p-8 text-center dark:border-zinc-700">
              <p className="text-sm text-zinc-500">{searching || topic || date || starred || kinds ? "該当する記事がありません。" : "まだ記事がありません。"}</p>
              {!searching && !topic && !date && !starred && !kinds && <button type="button" onClick={runRefresh} disabled={refresh.isPending} className={`${primaryButtonClass} mt-3`}>今すぐ取得する</button>}
            </div>
          ) : (
            <>
              <CardGrid items={list} hideRemoves={hideRemoves} onTopic={pickTopic} />
              {items.hasNextPage && (
                <button type="button" onClick={() => items.fetchNextPage()} disabled={items.isFetchingNextPage} className={`${secondaryButtonClass} w-full`}>{items.isFetchingNextPage ? "読み込み中…" : "もっと見る"}</button>
              )}
            </>
          ))}
          {sources.data?.last.finished && <p className="text-[11px] text-zinc-400">最後に取得: {relativeTime(sources.data.last.finished)}</p>}
        </main>
        <aside className="min-w-0 space-y-3" aria-label="あなたの興味と話題の動き">
          <InterestPanel onTopic={pickTopic} />
          <TrendsPanel onTopic={pickTopic} />
        </aside>
      </div>
    </div>
  );
}
