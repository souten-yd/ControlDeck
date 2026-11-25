import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/** 設定 → ニュースの共通の見た目（カード・入力・ボタン）。 */
export const card = "rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5";
export const field = "h-10 min-w-0 rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
export const ghost = "min-h-10 rounded-lg border border-zinc-300 px-3 text-xs font-medium hover:border-zinc-400 disabled:opacity-40 dark:border-zinc-700";
export const primary = "min-h-10 rounded-lg bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40";

/** 説明は 1 文だけ常に出し、残りは「？」を押したときだけ出す。 */
function Hint({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const cut = text.indexOf("。");
  const head = cut >= 0 && cut < text.length - 1 ? text.slice(0, cut + 1) : text;
  const rest = head === text ? "" : text.slice(head.length);
  return (
    <>
      <p className="mt-1 flex items-start gap-1.5 text-xs leading-relaxed text-zinc-400">
        <span className="min-w-0 flex-1">{head}</span>
        {rest && <button type="button" aria-expanded={open} aria-label="説明の続きを見る" onClick={() => setOpen(!open)}
          className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-zinc-300 text-[11px] text-zinc-500 dark:border-zinc-700">？</button>}
      </p>
      {rest && open && <p className="mt-1 text-xs leading-relaxed text-zinc-400" data-testid="card-hint-rest">{rest}</p>}
    </>
  );
}

export function Card({ id, title, hint, children }: { id: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className={card}>
      <h2 id={id} className="text-sm font-semibold text-zinc-500">{title}</h2>
      {hint && <Hint text={hint} />}
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

/** 「詳しい設定」の畳む節。既定は閉じ、開閉は端末に覚える（覚えられない環境では毎回閉じる）。 */
export function Fold({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  const key = `controldeck.fold.${id}`;
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(key) === "1"; } catch { return false; } });
  return (
    <details open={open} data-testid={`fold-${id}`} onToggle={(e) => {
      const next = (e.currentTarget as HTMLDetailsElement).open;
      setOpen(next);
      try { localStorage.setItem(key, next ? "1" : "0"); } catch { /* 覚えられない環境 */ }
    }} className="rounded-xl border border-zinc-200 dark:border-zinc-700">
      <summary className="flex min-h-11 cursor-pointer items-center px-3 text-xs font-semibold text-zinc-600 dark:text-zinc-300">{title}</summary>
      <div className="space-y-3 border-t border-zinc-200 p-3 dark:border-zinc-700">{children}</div>
    </details>
  );
}

/** 未保存の変更を持つカードが、タブの切り替え（NewsSettings）へ知らせるための口。 */
export const DirtyContext = createContext<(id: string, dirty: boolean) => void>(() => {});

/** カードの中に置く。dirty の間だけ「未保存」を知らせ、外れる（タブを離れる）ときに下ろす。 */
export function DirtyReporter({ id, dirty }: { id: string; dirty: boolean }) {
  const report = useContext(DirtyContext);
  useEffect(() => { report(id, dirty); }, [id, dirty, report]);
  useEffect(() => () => report(id, false), [id, report]);
  return null;
}
