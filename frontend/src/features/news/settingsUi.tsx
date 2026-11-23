import type { ReactNode } from "react";

/** 設定 → ニュースの共通の見た目（カード・入力・ボタン）。 */
export const card = "rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5";
export const field = "h-10 min-w-0 rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
export const ghost = "min-h-10 rounded-lg border border-zinc-300 px-3 text-xs font-medium hover:border-zinc-400 disabled:opacity-40 dark:border-zinc-700";
export const primary = "min-h-10 rounded-lg bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40";

export function Card({ id, title, hint, children }: { id: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className={card}>
      <h2 id={id} className="text-sm font-semibold text-zinc-500">{title}</h2>
      {hint && <p className="mt-1 text-xs leading-relaxed text-zinc-400">{hint}</p>}
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

