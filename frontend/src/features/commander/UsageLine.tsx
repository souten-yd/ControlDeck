/** 5 時間・週の上限の使用率と、回復までの時間（Claude は statusline、Codex は記録から。取れない CLI は出さない）。 */
import type { CommanderUsage, UsageWindow } from "../../api/commander";

export function untilText(resetsAt: number | null | undefined, now = Date.now() / 1000): string {
  if (!resetsAt) return "";
  const left = Math.max(0, resetsAt - now);
  const days = Math.floor(left / 86400);
  const hours = Math.floor((left % 86400) / 3600);
  const minutes = Math.floor((left % 3600) / 60);
  if (days > 0) return `あと ${days}日${hours}時間`;
  if (hours > 0) return `あと ${hours}時間${minutes}分`;
  return `あと ${minutes}分`;
}

function Window({ label, value }: { label: string; value: UsageWindow }) {
  const pct = Math.round(value.used_percent);
  const tone = pct >= 90 ? "text-red-600 dark:text-red-400" : pct >= 80 ? "text-amber-700 dark:text-amber-400" : "text-zinc-600 dark:text-zinc-300";
  const until = untilText(value.resets_at);
  return (
    <span className="inline-flex min-w-0 items-center gap-1 whitespace-nowrap" title={value.resets_at ? `回復: ${new Date(value.resets_at * 1000).toLocaleString("ja-JP")}` : undefined}>
      <span className="text-zinc-400">{label}</span>
      <span className={`num font-semibold ${tone}`}>{pct}%</span>
      <span className="relative h-1.5 w-10 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700" aria-hidden>
        <span className={`absolute inset-y-0 left-0 rounded-full ${pct >= 90 ? "bg-red-500" : pct >= 80 ? "bg-amber-500" : "bg-accent-500"}`} style={{ width: `${Math.min(100, pct)}%` }} />
      </span>
      {until && <span className="text-zinc-400">{until}</span>}
    </span>
  );
}

export function UsageLine({ usage, className = "" }: { usage?: CommanderUsage | null; className?: string }) {
  if (!usage || (!usage.five_hour && !usage.seven_day)) return null;
  return (
    <span data-commander-usage className={`flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] ${className}`}>
      {usage.five_hour && <Window label="5時間" value={usage.five_hour} />}
      {usage.seven_day && <Window label="週" value={usage.seven_day} />}
    </span>
  );
}
