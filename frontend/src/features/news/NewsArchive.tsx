import { useMemo } from "react";
import { useNewsArchive } from "../../api/news";
import { Skeleton } from "../../components/ui";

const WEEK = ["日", "月", "火", "水", "木", "金", "土"];

/** 日付の一覧（月ごとに畳む・件数つき）。押すとその日の記事へ。 */
export function ArchiveDays({ onPick }: { onPick: (date: string) => void }) {
  const { data, isLoading } = useNewsArchive();
  const months = useMemo(() => {
    const groups = new Map<string, { date: string; count: number }[]>();
    for (const day of data?.days ?? []) {
      const month = day.date.slice(0, 7);
      groups.set(month, [...(groups.get(month) ?? []), day]);
    }
    return [...groups.entries()];
  }, [data]);
  if (isLoading) return <Skeleton className="h-40" />;
  if (months.length === 0) return <p className="rounded-2xl border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">まだアーカイブがありません。</p>;
  return (
    <div className="space-y-2">
      {months.map(([month, days], index) => (
        <details key={month} open={index === 0} className="group rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-sm font-semibold">
            <span>{month.replace("-", "年")}月</span>
            <span className="text-xs font-normal text-zinc-500">{days.length} 日 · {days.reduce((sum, d) => sum + d.count, 0)} 件</span>
          </summary>
          <ul className="grid gap-1 border-t border-zinc-100 p-2 dark:border-zinc-800 sm:grid-cols-2">
            {days.map((day) => {
              const d = new Date(`${day.date}T00:00:00`);
              return (
                <li key={day.date}>
                  <button type="button" onClick={() => onPick(day.date)} className="flex min-h-11 w-full items-center justify-between gap-2 rounded-xl px-3 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800">
                    <span>{d.getMonth() + 1}月{d.getDate()}日（{WEEK[d.getDay()]}）</span>
                    <span className="num text-xs text-zinc-500">{day.count} 件</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </details>
      ))}
    </div>
  );
}
