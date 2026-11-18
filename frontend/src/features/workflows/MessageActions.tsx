/** 会話のメッセージの操作（コピー・分岐・編集・削除）。
 *
 * 各メッセージの下に小さな「⋯」を置き、タップ（クリック）でメニューを開く。メニューは画面に固定して
 * 出す（会話の枠や入力欄に切られない）。行は 44px 以上で、携帯でも押しやすくする。 */
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type MessageAction = "copy" | "fork" | "edit" | "delete";

const LABEL: Record<MessageAction, { label: string; hint: string }> = {
  copy: { label: "コピー", hint: "この発言の文字を写す" },
  fork: { label: "ここから分岐", hint: "ここまでを新しい会話に写して続ける（元の会話は残る）" },
  edit: { label: "編集して送り直す", hint: "書き直した文で、ここから別の会話として送り直す" },
  delete: { label: "削除", hint: "この発言を消す（自分の発言なら、その回答も）" },
};

type MenuPos = { left: number; top: number; maxHeight: number };

/** メニューを置ける範囲: ボタンを切り取るスクロールの枠（会話の欄）と画面の重なり。下の端は入力欄の上端。
 *  （2026-11-18 利用者の画面: 最後の発言のメニューが入力欄に隠れて押せなかった） */
function boundary(button: HTMLElement): { top: number; bottom: number } {
  const pane = button.closest<HTMLElement>("[data-assistant-messages]")?.getBoundingClientRect();
  const composer = button.closest("[data-assistant-shell]")?.querySelector("[data-assistant-composer]")?.getBoundingClientRect();
  let top = Math.max(8, pane?.top ?? 0);
  let bottom = Math.min(window.innerHeight - 8, pane?.bottom ?? window.innerHeight);
  if (composer && composer.top > top) bottom = Math.min(bottom, composer.top - 8);
  if (bottom - top < 120) { top = 8; bottom = window.innerHeight - 8; }   // 枠が測れないほど狭い
  return { top, bottom };
}

export function MessageActions({ actions, align, disabled, onSelect }: {
  actions: MessageAction[];
  align: "start" | "end";
  disabled?: boolean;
  onSelect: (action: MessageAction) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<MenuPos | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLUListElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  // 開いている間に項目が増える（送った直後は「コピー」だけ → ID が届くと 4 つ）。いつも今の数で置く
  const count = useRef(actions.length);
  count.current = actions.length;

  /** ボタンの位置からメニューの位置を決める。ボタンが会話の欄の中に見えていなければ null。 */
  const place = (): MenuPos | null => {
    const node = button.current;
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    const area = boundary(node);
    if (rect.bottom <= area.top || rect.top >= area.bottom) return null;   // 欄の外（上のバー・入力欄の裏）
    const width = Math.min(272, window.innerWidth - 32);
    const height = count.current * 56 + 8;
    const left = Math.max(16, Math.min(align === "end" ? rect.right - width : rect.left, window.innerWidth - width - 16));
    const below = area.bottom - (rect.bottom + 4);
    const above = rect.top - 4 - area.top;
    // 入る側に開く。どちらにも入らなければ広い側に開き、メニューの中をスクロールする（ボタンに重ねない）
    if (below >= height || below >= above) return { left, top: rect.bottom + 4, maxHeight: Math.max(56, below) };
    const shown = Math.min(height, above);
    return { left, top: rect.top - 4 - shown, maxHeight: Math.max(56, above) };
  };
  const same = (a: MenuPos | null, b: MenuPos | null) =>
    !!a && !!b && Math.round(a.left) === Math.round(b.left) && Math.round(a.top) === Math.round(b.top) && Math.round(a.maxHeight) === Math.round(b.maxHeight);
  const closeMenu = (focusButton: boolean) => {
    setOpen(false);
    if (focusButton) button.current?.focus({ preventScroll: true });
  };

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!ref.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu(true);
      // Tab でメニューの外へ出たら閉じてボタンへ戻す（メニューは body の直下にあり、続きのフォーカスが遠くへ飛ぶ）
      if (event.key === "Tab") { event.preventDefault(); closeMenu(true); }
    };
    // スクロールしたらボタンに付いて動く（書いている途中の回答で会話の欄が動き続けても閉じない）。
    // ボタンが会話の欄の外へ出たら閉じる。1 フレームに 1 回だけ測る
    let frame = 0;
    const follow = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        const next = place();
        if (!next) setOpen(false);
        else setPos((current) => (same(current, next) ? current : next));
      });
    };
    const dismiss = () => setOpen(false);
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", dismiss);
    menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus({ preventScroll: true });
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", dismiss);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  // 開いている間に項目の数が変わったら置き直す
  useEffect(() => {
    if (!open) return;
    const next = place();
    if (!next) setOpen(false);
    else setPos((current) => (same(current, next) ? current : next));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actions.length]);
  const toggle = () => {
    if (open) { setOpen(false); return; }
    const next = place();
    if (!next) return;   // 見えていないボタンからは開かない
    setPos(next);
    setOpen(true);
  };
  const menuId = useId();
  return (
    <div ref={ref} className={`flex ${align === "end" ? "justify-end" : "justify-start"}`}>
      <button ref={button} type="button" onClick={toggle} disabled={disabled}
        aria-label="メッセージの操作" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
        className="grid h-8 min-w-11 place-items-center rounded-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-40 dark:hover:bg-zinc-800 dark:hover:text-zinc-300 [@media(pointer:coarse)]:h-11">
        <span aria-hidden className="text-base leading-none tracking-widest">⋯</span>
      </button>
      {/* 画面のいちばん手前に出す（会話の欄の中に置くと、入力欄より奥に描かれて隠れる） */}
      {open && pos && createPortal(
        <ul ref={menu} id={menuId} role="menu" aria-label="メッセージの操作"
          style={{ left: pos.left, top: pos.top, maxHeight: pos.maxHeight }}
          className="fixed z-[100] w-[min(17rem,calc(100vw-2rem))] overflow-y-auto overscroll-contain rounded-xl border border-zinc-200 bg-white py-1 shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
          {actions.map((action) => (
            <li key={action} role="none">
              <button type="button" role="menuitem" onClick={() => { closeMenu(false); onSelect(action); }}
                className={`flex min-h-12 w-full flex-col items-start justify-center px-3 py-1.5 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800 ${action === "delete" ? "text-red-600 dark:text-red-400" : "text-zinc-800 dark:text-zinc-100"}`}>
                <span className="text-sm font-medium">{LABEL[action].label}</span>
                <span className="text-[11px] leading-snug text-zinc-500">{LABEL[action].hint}</span>
              </button>
            </li>
          ))}
        </ul>,
        document.body,
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
