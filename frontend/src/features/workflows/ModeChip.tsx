/** 入力欄の中の「機能」の札。押すと、自動判定と各機能（印・名前・一行の説明）のメニューが開く。
 *
 * メニューは画面に固定して札の上に出す（入力カードの枠に切られない）。行は 48px 以上、
 * 背の低い画面では中をスクロールする。外側・Escape・スクロール・回転で閉じる。 */
import { Fragment, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface ModeOption { id: string; icon: string; label: string; hint: string }

export const AUTO_MODE_OPTION: ModeOption = {
  id: "auto", icon: "✦", label: "自動判定", hint: "入力した内容から、使う機能を自動で選びます",
};

export function ModeChip({ value, options, detectedLabel, routing, disabled, onChange }: {
  /** "auto" か、機能の id。 */
  value: string;
  /** 自動判定を除いた、選べる機能。 */
  options: ModeOption[];
  /** 自動判定がいま選んでいる機能の名前。 */
  detectedLabel: string;
  routing: boolean;
  disabled?: boolean;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; bottom: number; maxHeight: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!button.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
        button.current?.focus({ preventScroll: true });
        return;
      }
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [])];
      if (items.length === 0) return;
      event.preventDefault();
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
      items[next].focus();
    };
    /* メニューの中のスクロールでは閉じない。 */
    const onScroll = (event: Event) => {
      if (!menu.current?.contains(event.target as Node)) setOpen(false);
    };
    const dismiss = () => setOpen(false);
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", dismiss);
    (menu.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
      ?? menu.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"]'))?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [open]);
  const toggle = () => {
    const rect = button.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(336, window.innerWidth - 24);
      setPos({
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        bottom: window.innerHeight - rect.top + 8,
        maxHeight: Math.max(160, rect.top - 24),
      });
    }
    setOpen((current) => !current);
  };
  const all = [AUTO_MODE_OPTION, ...options];
  const current = all.find((option) => option.id === value) ?? AUTO_MODE_OPTION;
  const auto = current.id === "auto";
  const chipLabel = auto ? "自動" : current.label;
  const detail = auto ? (routing ? "計画中…" : detectedLabel) : "";
  return (
    <>
      <button
        ref={button}
        type="button"
        onClick={toggle}
        disabled={disabled}
        data-assistant-mode-chip
        aria-label={`処理モード: ${auto ? `自動判定（${detail}）` : current.label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title="機能を選ぶ"
        className={`flex h-11 min-w-11 max-w-full items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 disabled:opacity-50 ${
          auto
            ? "border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            : "border-accent-500/40 bg-accent-50 text-accent-700 hover:bg-accent-100 dark:border-accent-500/40 dark:bg-accent-600/15 dark:text-accent-300 dark:hover:bg-accent-600/25"}`}
      >
        <span aria-hidden className="shrink-0 text-sm leading-none">{current.icon}</span>
        <span data-mode-label className="shrink-0 whitespace-nowrap">{chipLabel}</span>
        {detail && <span data-mode-detected aria-live="polite" className="min-w-0 truncate font-normal text-zinc-500 dark:text-zinc-400">· {detail}</span>}
        <svg aria-hidden viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-[13px] opacity-60"><path d="m6 9 6 6 6-6" /></svg>
      </button>
      {open && pos && createPortal(
        <div
          ref={menu}
          role="menu"
          aria-label="処理モード"
          style={{ left: pos.left, bottom: pos.bottom, maxHeight: pos.maxHeight }}
          className="fixed z-[100] w-[min(21rem,calc(100vw-1.5rem))] overflow-y-auto overscroll-contain rounded-2xl border border-zinc-200 bg-white p-1.5 shadow-2xl shadow-black/10 dark:border-zinc-700 dark:bg-zinc-900 dark:shadow-black/50"
        >
          {all.map((option, index) => {
            const selected = option.id === current.id;
            return (
              <Fragment key={option.id}>
              {index === 1 && <div role="separator" className="mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800" />}
              <button
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                data-mode-option={option.id}
                onClick={() => { onChange(option.id); setOpen(false); button.current?.focus({ preventScroll: true }); }}
                className={`flex min-h-12 w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/50 ${
                  selected ? "bg-zinc-100 dark:bg-white/[0.07]" : "hover:bg-zinc-50 dark:hover:bg-white/[0.04]"}`}
              >
                <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-100 text-base dark:bg-zinc-800">{option.icon}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-zinc-900 dark:text-zinc-50">{option.label}</span>
                  <span className="mt-0.5 block truncate text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">{option.hint}</span>
                </span>
                <span aria-hidden className="w-4 shrink-0 text-center text-sm text-accent-600 dark:text-accent-400">{selected ? "✓" : ""}</span>
              </button>
              </Fragment>
            );
          })}
        </div>,
        document.body,
      )}
    </>
  );
}
