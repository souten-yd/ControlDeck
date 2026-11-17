/** 会話のメッセージの操作（コピー・分岐・編集・削除）。
 *
 * 各メッセージの下に小さな「⋯」を置き、タップ（クリック）でメニューを開く。メニューは画面に固定して
 * 出す（会話の枠や入力欄に切られない）。行は 44px 以上で、携帯でも押しやすくする。 */
import { useEffect, useRef, useState } from "react";

export type MessageAction = "copy" | "fork" | "edit" | "delete";

const LABEL: Record<MessageAction, { label: string; hint: string }> = {
  copy: { label: "コピー", hint: "この発言の文字を写す" },
  fork: { label: "ここから分岐", hint: "ここまでを新しい会話に写して続ける（元の会話は残る）" },
  edit: { label: "編集して送り直す", hint: "書き直した文で、ここから別の会話として送り直す" },
  delete: { label: "削除", hint: "この発言を消す（自分の発言なら、その回答も）" },
};

export function MessageActions({ actions, align, disabled, onSelect }: {
  actions: MessageAction[];
  align: "start" | "end";
  disabled?: boolean;
  onSelect: (action: MessageAction) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    // 画面に固定したメニューなので、スクロールや回転でボタンから離れたら閉じる
    const dismiss = () => setOpen(false);
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    ref.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [open]);
  const toggle = () => {
    const rect = button.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(272, window.innerWidth - 32);
      const height = actions.length * 56 + 8;
      const left = align === "end" ? rect.right - width : rect.left;
      // 下に入らなければ上に開く
      const top = rect.bottom + 4 + height > window.innerHeight ? Math.max(8, rect.top - 4 - height) : rect.bottom + 4;
      setPos({ left: Math.max(16, Math.min(left, window.innerWidth - width - 16)), top });
    }
    setOpen((v) => !v);
  };
  return (
    <div ref={ref} className={`flex ${align === "end" ? "justify-end" : "justify-start"}`}>
      <button ref={button} type="button" onClick={toggle} disabled={disabled}
        aria-label="メッセージの操作" aria-haspopup="menu" aria-expanded={open}
        className="grid h-8 min-w-11 place-items-center rounded-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-40 dark:hover:bg-zinc-800 dark:hover:text-zinc-300 [@media(pointer:coarse)]:h-11">
        <span aria-hidden className="text-base leading-none tracking-widest">⋯</span>
      </button>
      {open && pos && (
        <ul role="menu" aria-label="メッセージの操作" style={{ left: pos.left, top: pos.top }}
          className="fixed z-[100] w-[min(17rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-zinc-200 bg-white py-1 shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
          {actions.map((action) => (
            <li key={action} role="none">
              <button type="button" role="menuitem" onClick={() => { setOpen(false); onSelect(action); }}
                className={`flex min-h-12 w-full flex-col items-start justify-center px-3 py-1.5 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800 ${action === "delete" ? "text-red-600 dark:text-red-400" : "text-zinc-800 dark:text-zinc-100"}`}>
                <span className="text-sm font-medium">{LABEL[action].label}</span>
                <span className="text-[11px] leading-snug text-zinc-500">{LABEL[action].hint}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 自分の発言をその場で書き直す欄。 */
export function MessageEditor({ initial, disabled = false, onSubmit, onCancel }: {
  initial: string; disabled?: boolean; onSubmit: (text: string) => void; onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const node = area.current;
    if (!node) return;
    node.focus();
    node.setSelectionRange(node.value.length, node.value.length);
  }, []);
  return (
    <div className="w-full min-w-0 rounded-2xl border border-accent-400 bg-white p-2 dark:border-accent-500/60 dark:bg-zinc-900">
      <textarea ref={area} value={text} onChange={(event) => setText(event.target.value)} rows={Math.min(8, Math.max(2, text.split("\n").length))}
        aria-label="発言を書き直す"
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && text.trim() && !disabled) onSubmit(text.trim());
        }}
        className="w-full resize-none bg-transparent px-1.5 py-1 text-base outline-none sm:text-sm" />
      <p className="px-1.5 text-[11px] text-zinc-500">{disabled ? "回答の生成が終わってから送り直せます。" : "送り直すと、ここから別の会話になります（元の会話は残ります）。"}</p>
      <div className="mt-1.5 flex justify-end gap-2">
        <button type="button" onClick={onCancel}
          className="min-h-11 rounded-xl px-4 text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800">取消</button>
        <button type="button" onClick={() => text.trim() && onSubmit(text.trim())} disabled={!text.trim() || disabled}
          className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-50">送り直す</button>
      </div>
    </div>
  );
}
