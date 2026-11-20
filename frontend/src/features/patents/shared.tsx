/** Patent 画面で共通に使う小物（入力の見た目・期限の印・エラー文）。 */
import type { ReactNode } from "react";

export const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-base sm:text-sm dark:border-zinc-700 dark:bg-zinc-900";
export const card = "rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900";

export function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

/** 期限の印。過ぎた・14 日以内は琥珀色（赤は使わない: 赤は破壊的操作と重大エラーだけ）。 */
export function DeadlineChip({ days }: { days: number }) {
  const tone = days < 0 ? "bg-amber-600 text-white" : days <= 14 ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
    : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300";
  return <span className={`num shrink-0 rounded-full px-2 py-0.5 text-xs ${tone}`}>{days < 0 ? `${-days} 日超過` : `あと ${days} 日`}</span>;
}

export const STATUS_LABEL: Record<string, string> = {
  queued: "順番待ち", running: "作成中", succeeded: "完了", failed: "失敗", canceled: "取り消し", interrupted: "中断",
};

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block text-xs font-medium text-zinc-500">{label}<div className="mt-1 text-sm font-normal text-zinc-900 dark:text-zinc-100">{children}</div></label>;
}
