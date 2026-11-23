import { Skeleton, Sparkline } from "../../components/ui";
import { useMuteTopic, useNewsProfile, useNewsTrends } from "../../api/news";

const panel = "rounded-2xl border border-zinc-200 bg-white p-3 shadow-sm dark:border-zinc-800 dark:bg-zinc-900 md:p-4";

/** 重みを文字の大きさ（4 段）にする。 */
function sizeClass(weight: number, top: number): string {
  const ratio = top > 0 ? weight / top : 0;
  return ratio > 0.75 ? "text-base font-semibold" : ratio > 0.5 ? "text-sm font-medium" : ratio > 0.25 ? "text-xs" : "text-[11px]";
}

/** あなたの興味: 開いた・👍・保存した記事とチャットの題から。語ごとに外せる（外した語は点に使わない）。 */
export function InterestPanel({ onTopic }: { onTopic: (topic: string) => void }) {
  const { data, isLoading } = useNewsProfile();
  const mute = useMuteTopic();
  const top = Math.max(...(data?.topics.map((t) => t.weight) ?? [0]));
  return (
    <section className={panel} aria-labelledby="news-interest">
      <h2 id="news-interest" className="text-sm font-semibold">あなたの興味</h2>
      <p className="mt-0.5 text-[11px] text-zinc-500">開いた・👍・保存した記事と、チャットの題（秘密の会話は除く）から。外した語はおすすめに使いません。</p>
      {isLoading ? <Skeleton className="mt-3 h-16" /> : !data || data.topics.length === 0 ? (
        <p className="mt-3 text-xs text-zinc-500">記事を開いたり 👍 を押すと、ここに興味が出ます。</p>
      ) : (
        <ul className="mt-3 flex flex-wrap items-center gap-1.5">
          {data.topics.map((t) => (
            <li key={t.topic} className="flex items-center rounded-full border border-zinc-200 dark:border-zinc-700">
              <button type="button" onClick={() => onTopic(t.topic)} title={`重み ${t.weight}`} className={`rounded-l-full py-0.5 pl-2.5 pr-1 hover:text-accent-600 ${sizeClass(t.weight, top)}`}>{t.topic}</button>
              <button type="button" aria-label={`「${t.topic}」を興味から外す`} disabled={mute.isPending} onClick={() => mute.mutate({ topic: t.topic, muted: true })}
                className="rounded-r-full px-1.5 py-0.5 text-xs text-zinc-400 hover:text-red-500">×</button>
            </li>
          ))}
        </ul>
      )}
      {data && data.sources.length > 0 && (
        <p className="mt-3 text-[11px] text-zinc-500">よく読むソース: {data.sources.slice(0, 4).map((s) => s.source).join("・")}</p>
      )}
      {data && data.avoided_sources.length > 0 && (
        <p className="mt-1 text-[11px] text-zinc-500">下げているソース: {data.avoided_sources.map((s) => s.source).join("・")}</p>
      )}
      {data && data.muted.length > 0 && (
        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">外した語:
          {data.muted.map((word) => (
            <button key={word} type="button" onClick={() => mute.mutate({ topic: word, muted: false })} className="rounded-full border border-dashed border-zinc-300 px-2 py-0.5 line-through hover:no-underline dark:border-zinc-600" aria-label={`「${word}」を戻す`}>{word}</button>
          ))}
        </p>
      )}
    </section>
  );
}

/** 話題の動き: 上位 8 語の 14 日の件数（折れ線）と、ソース別の件数（棒）。 */
export function TrendsPanel({ onTopic }: { onTopic: (topic: string) => void }) {
  const { data, isLoading } = useNewsTrends();
  const biggest = Math.max(...(data?.sources.map((s) => s.count) ?? [1]), 1);
  return (
    <section className={panel} aria-labelledby="news-trends">
      <h2 id="news-trends" className="text-sm font-semibold">話題の動き</h2>
      <p className="mt-0.5 text-[11px] text-zinc-500">この 14 日で記事に多く出た語</p>
      {isLoading ? <Skeleton className="mt-3 h-24" /> : !data || data.topics.length === 0 ? (
        <p className="mt-3 text-xs text-zinc-500">記事が集まると、話題の動きが出ます。</p>
      ) : (
        <>
          <ul className="mt-3 space-y-1.5">
            {data.topics.map((t) => (
              <li key={t.topic} className="flex items-center gap-2">
                <button type="button" onClick={() => onTopic(t.topic)} className="w-24 shrink-0 truncate text-left text-xs hover:text-accent-600 sm:w-28">{t.topic}</button>
                <span className="min-w-0 flex-1 text-accent-500"><Sparkline values={t.counts} max={Math.max(...t.counts, 1)} fill heightClass="h-5" /></span>
                <span className="num w-8 shrink-0 text-right text-[11px] text-zinc-500">{t.total}</span>
              </li>
            ))}
          </ul>
          <h3 className="mt-4 text-xs font-medium text-zinc-500">ソース別の件数</h3>
          <ul className="mt-1.5 space-y-1">
            {data.sources.slice(0, 6).map((s) => (
              <li key={s.source} className="flex items-center gap-2 text-[11px]">
                <span className="w-24 shrink-0 truncate sm:w-28">{s.source}</span>
                <span className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><span className="block h-full rounded-full bg-accent-500" style={{ width: `${(s.count / biggest) * 100}%` }} /></span>
                <span className="num w-7 shrink-0 text-right text-zinc-500">{s.count}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
