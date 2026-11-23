import { Link } from "react-router-dom";
import { KIND_STYLE, relativeTime, useNewsAction, useNewsItems } from "../../api/news";
import { Skeleton } from "../../components/ui";

/** ホームの小さなニュースのカード: おすすめの上位 5 件。 */
export function NewsHomeCard() {
  const { data, isLoading, isError } = useNewsItems({ view: "recommended", kinds: "", q: "", date: "", topic: "", source: "" });
  const act = useNewsAction();
  const items = (data?.pages[0]?.items ?? []).filter((i) => !i.state.hide).slice(0, 5);
  if (isError) return null;
  return (
    <section aria-labelledby="home-news" className="rounded-2xl border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900 md:p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="home-news" className="text-sm font-semibold">ニュース</h2>
        <Link to="/news" className="rounded-lg px-2 py-1 text-xs font-medium text-accent-600 hover:bg-accent-50 dark:hover:bg-accent-600/10">ニュースを開く</Link>
      </div>
      {isLoading ? <Skeleton className="h-24" /> : items.length === 0 ? (
        <p className="text-xs text-zinc-500">まだ記事がありません。ニュースのページで取得できます。</p>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {items.map((item) => (
            <li key={item.id} className="flex items-baseline gap-2 py-1.5 text-sm">
              <span aria-hidden className="shrink-0">{(KIND_STYLE[item.kind] ?? KIND_STYLE.news).icon}</span>
              {item.url ? (
                <a href={item.url} {...(/^https?:/.test(item.url) ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                  onClick={() => act.mutate({ id: item.id, action: "open" })} className="min-w-0 flex-1 truncate hover:text-accent-600">{item.title}</a>
              ) : <span className="min-w-0 flex-1 truncate">{item.title}</span>}
              <span className="shrink-0 text-[11px] text-zinc-400">{relativeTime(item.published_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
