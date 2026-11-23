import { useMemo } from "react";
import { useNewsArchive } from "../../api/news";

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
  if (isLoading) return <div className="nw-skel" />;
  if (months.length === 0) return <p className="nw-empty">まだアーカイブがありません。</p>;
  return (
    <div>
      {months.map(([month, days], index) => (
        <details key={month} open={index === 0} className="nw-archive-month">
          <summary><span>{month.replace("-", "年")}月</span><span className="nw-muted" style={{ fontSize: ".75rem", fontWeight: 400 }}>{days.length} 日 · {days.reduce((sum, d) => sum + d.count, 0)} 件</span></summary>
          <div className="nw-archive-days">
            {days.map((day) => {
              const d = new Date(`${day.date}T00:00:00`);
              return (
                <button key={day.date} type="button" onClick={() => onPick(day.date)}>
                  <span>{d.getMonth() + 1}月{d.getDate()}日（{WEEK[d.getDay()]}）</span>
                  <span className="nw-muted" style={{ fontSize: ".75rem" }}>{day.count} 件</span>
                </button>
              );
            })}
          </div>
        </details>
      ))}
    </div>
  );
}
