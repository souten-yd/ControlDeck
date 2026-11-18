/** 入力欄の左下の「＋」。添付・機能の選択・OpenCode の権限を 1 つのメニューにまとめる。
 *
 * 2026-11-18 利用者の依頼「クリップ・機能選択・権限のボタンを並べると煩雑。Claude のように左下に＋を置いて、
 * そこから各機能へ」。あわせて「OpenCode の権限が消えた」を直す: 以前は入力欄の下の行に「いまのモードが
 * OpenCode のときだけ」出していたので、自動判定が文字を打つたびにチャットへ判定し直すと消えた。
 * ここでは OpenCode が使えるなら、判定に関係なくいつでも権限を変えられる。
 *
 * メニューは画面に固定して＋の上に出す（入力カードの枠に切られない）。押せる所は 44px 以上、背の低い画面では
 * 中をスクロールする。外側・Escape・＋が動くスクロール・回転で閉じる。矢印キーで項目を移る。
 *
 * 2026-11-18 利用者「項目が多すぎてはみ出る。スライドも微妙にできない。コンパクトにして高さを減らして」。
 * 全項目を説明つきの行で並べると約 1200px あった。機能はタイル、モードと権限は横並びの切り替えにし、
 * 切り替えには短い説明を添える（指の端末は title を読めない）。上端はステータスバーに重ねない。 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconPaperclip, IconPlus } from "../../components/icons";
import { AUTO_MODE_OPTION, type ModeOption } from "./ModeChip";
import { PERMISSION_MODES, type PermissionMode } from "./OpenCodeActivity";

export interface PlusAction { id: string; icon: ReactNode; label: string; hint: string; disabled?: boolean; onSelect: () => void }

const FOCUS = "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/50 disabled:opacity-40";
const ROW = `flex h-11 w-full items-center gap-2.5 rounded-xl px-2 text-left hover:bg-zinc-50 dark:hover:bg-white/[0.04] ${FOCUS}`;
const ICON = "grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-zinc-100 text-sm dark:bg-zinc-800";
const HEADING = "flex min-w-0 items-baseline gap-2 px-2 pb-1 pt-1.5 text-[10px] font-semibold tracking-wider text-zinc-400 dark:text-zinc-500";
const SEPARATOR = "mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800";
/** 選択中の色。メニューと入力欄の札で揃える */
const TONE = {
  plan: "border-sky-400 bg-sky-100 text-sky-800 dark:border-sky-600 dark:bg-sky-900/60 dark:text-sky-100",
  accent: "border-accent-500/40 bg-accent-50 text-accent-700 dark:border-accent-500/40 dark:bg-accent-600/15 dark:text-accent-300",
  amber: "border-amber-500/40 bg-amber-50 text-amber-700 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300",
};
const UNSELECTED = "border-transparent bg-zinc-50 text-zinc-700 hover:bg-zinc-100 dark:bg-white/[0.03] dark:text-zinc-200 dark:hover:bg-white/[0.07]";
const SEGMENT = `flex h-12 min-w-0 flex-col items-center justify-center rounded-xl border px-1.5 ${FOCUS}`;
const SEGMENT_NOTE = "block max-w-full truncate text-[10px] font-normal leading-tight opacity-75";
const PERMISSION_NOTE: Record<PermissionMode, string> = { auto: "箱の中は確認なし", ask: "実行・編集は毎回確認" };

/** 矢印の向きで、見た目どおり隣の項目を選ぶ（上下は端で反対側へ回る） */
function nextItem(items: HTMLButtonElement[], current: HTMLButtonElement | null, key: string): HTMLButtonElement | null {
  const index = current ? items.indexOf(current) : -1;
  if (!current || index < 0) return items[0] ?? null;
  const center = (el: Element) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; };
  const from = center(current);
  let best: HTMLButtonElement | null = null;
  let bestScore = Infinity;
  for (const item of items) {
    if (item === current) continue;
    const to = center(item);
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const sameRow = Math.abs(dy) < 4;
    const ahead = key === "ArrowRight" ? sameRow && dx > 0 : key === "ArrowLeft" ? sameRow && dx < 0
      : key === "ArrowDown" ? dy >= 4 : dy <= -4;
    if (!ahead) continue;
    const score = sameRow ? Math.abs(dx) : Math.abs(dy) * 1000 + Math.abs(dx);
    if (score < bestScore) { bestScore = score; best = item; }
  }
  if (best) return best;
  if (key === "ArrowDown") return items[0];
  return key === "ArrowUp" ? items[items.length - 1] : null;
}

const AGENT_MODES = [
  ["build", "🛠️", "ビルド", "作る・直す（ファイルを編集し、コマンドを実行する）", "編集・実行する"],
  ["plan", "📋", "プラン", "計画だけ。ファイルの編集や変更を伴うコマンドはしない", "計画だけ・変更なし"],
] as const;

export function PlusMenu({
  disabled, onAttach, attachDisabled, mode, modeOptions, detectedLabel, routing, onModeChange, permission, agent, actions = [],
}: {
  disabled?: boolean;
  onAttach: () => void;
  attachDisabled?: boolean;
  /** "auto" か、機能の id */
  mode: string;
  modeOptions: ModeOption[];
  detectedLabel: string;
  routing: boolean;
  onModeChange: (id: string) => void;
  /** OpenCode が使えるときだけ */
  permission?: { mode: PermissionMode; editable: boolean; onChange: (mode: PermissionMode) => void } | null;
  /** OpenCode のエージェント（ビルド／プラン） */
  agent?: { mode: "build" | "plan"; onChange: (mode: "build" | "plan") => void } | null;
  /** 会話の引き継ぎなど、そのときだけ出す操作 */
  actions?: PlusAction[];
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; bottom: number; maxHeight: string } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const place = () => {
    const rect = button.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(336, window.innerWidth - 24);
      setPos({
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        bottom: window.innerHeight - rect.top + 8,
        // ステータスバーの下まで（iPhone のホーム画面アプリは画面の最上部から描かれる）
        maxHeight: `max(44px, calc(${Math.round(rect.top - 20)}px - var(--cd-safe-area-top, env(safe-area-inset-top))))`,
      });
    }
  };
  const close = (focus = true) => {
    setOpen(false);
    if (focus) button.current?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!button.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>("[data-plus-item]:not(:disabled)") ?? [])];
      if (items.length === 0) return;
      event.preventDefault();
      nextItem(items, document.activeElement as HTMLButtonElement, event.key)?.focus();
    };
    // 生成中は会話の欄が自動でスクロールする。これで閉じると、触っている最中にメニューが消える
    // （2026-11-18 利用者「スライドも微妙にできない」）。＋ごと動くスクロールのときだけ閉じる
    const onScroll = (event: Event) => {
      if (button.current && (event.target as Node).contains?.(button.current)) setOpen(false);
    };
    // キーボードの開閉は高さだけが変わる。置き直して開いたままにする。回転は閉じる
    const openedWidth = window.innerWidth;
    const onResize = () => {
      if (window.innerWidth !== openedWidth) setOpen(false);
      else place();
    };
    window.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    menu.current?.querySelector<HTMLButtonElement>("[data-plus-item]:not(:disabled)")?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener("pointerdown", outside);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);
  const toggle = () => {
    place();
    setOpen((current) => !current);
  };
  const modes = [AUTO_MODE_OPTION, ...modeOptions];
  const selectedMode = modes.find((option) => option.id === mode);
  const autoHint = routing ? "計画中…" : `いまは ${detectedLabel}`;
  const modeHint = mode === "auto" ? autoHint : selectedMode?.hint;
  return (
    <>
      <button ref={button} type="button" onClick={toggle} disabled={disabled} data-assistant-plus
        aria-label="添付・機能・権限" aria-haspopup="menu" aria-expanded={open} title="添付・機能・権限"
        className={`grid h-11 w-11 shrink-0 place-items-center rounded-full text-xl transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 disabled:opacity-50 ${
          open ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"}`}>
        <IconPlus className={`transition-transform ${open ? "rotate-45" : ""}`} />
      </button>
      {open && pos && createPortal(
        <div ref={menu} role="menu" aria-label="添付・機能・権限" style={{ left: pos.left, bottom: pos.bottom, maxHeight: pos.maxHeight }}
          className="fixed z-[100] w-[min(21rem,calc(100vw-1.5rem))] touch-pan-y overflow-y-auto overscroll-contain rounded-2xl border border-zinc-200 bg-white p-1.5 shadow-2xl shadow-black/10 dark:border-zinc-700 dark:bg-zinc-900 dark:shadow-black/50">
          <button type="button" role="menuitem" data-plus-item disabled={attachDisabled} title="画像は VLM へ、コード・文書・PDF は会話の資料へ"
            onClick={() => { close(false); onAttach(); }} className={ROW}>
            <span aria-hidden className={ICON}><IconPaperclip /></span>
            <span className="shrink-0 text-sm font-medium text-zinc-900 dark:text-zinc-50">ファイルを添付</span>
            <span className="min-w-0 flex-1 truncate text-right text-[11px] text-zinc-500 dark:text-zinc-400">画像・コード・文書・PDF</span>
          </button>
          {actions.map((action) => (
            <button key={action.id} type="button" role="menuitem" data-plus-item data-plus-action={action.id} disabled={action.disabled} title={action.hint}
              onClick={() => { close(false); action.onSelect(); }} className={`${ROW} h-12`}>
              <span aria-hidden className={ICON}>{action.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium leading-tight text-zinc-900 dark:text-zinc-50">{action.label}</span>
                <span className="block truncate text-[11px] leading-tight text-zinc-500 dark:text-zinc-400">{action.hint}</span>
              </span>
            </button>
          ))}

          <div role="separator" className={SEPARATOR} />
          <div role="group" aria-label="機能">
            <p aria-hidden className={HEADING}>
              <span className="shrink-0">機能</span>
              <span className="min-w-0 flex-1 truncate text-right font-normal tracking-normal">{modeHint}</span>
            </p>
            <div className="grid grid-cols-3 gap-1">
              {modes.map((option) => {
                const selected = option.id === mode;
                return (
                  <button key={option.id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item data-mode-option={option.id}
                    title={option.id === "auto" ? autoHint : option.hint} onClick={() => { onModeChange(option.id); close(); }}
                    className={`flex h-12 min-w-0 flex-col items-center justify-center gap-0.5 rounded-xl border px-1 ${FOCUS} ${selected ? TONE.accent : UNSELECTED}`}>
                    <span aria-hidden className="text-sm leading-none">{option.icon}</span>
                    <span className="block max-w-full truncate text-[11px] font-medium leading-tight">{option.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {agent && (
            <>
              <div role="separator" className={SEPARATOR} />
              <div role="group" aria-label="OpenCode のモード">
                <p aria-hidden className={HEADING}>OpenCode のモード<span className="hidden font-normal tracking-normal sm:inline">Shift+Tab でも切替</span></p>
                <div className="grid grid-cols-2 gap-1">
                  {AGENT_MODES.map(([id, icon, label, hint, note]) => {
                    const selected = agent.mode === id;
                    const tone = !selected ? UNSELECTED : id === "plan" ? TONE.plan : TONE.accent;
                    return (
                      <button key={id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item data-agent-option={id} title={hint}
                        onClick={() => { agent.onChange(id); close(); }} className={`${SEGMENT} ${tone}`}>
                        <span className="flex max-w-full items-center gap-1 text-sm font-medium leading-tight"><span aria-hidden>{icon}</span><span className="truncate">{label}</span></span>
                        <span className={SEGMENT_NOTE}>{note}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </>
          )}
          {permission && (
            <>
              <div role="separator" className={SEPARATOR} />
              <div role="group" aria-label="OpenCode の権限">
                <p aria-hidden className={HEADING}>
                  OpenCode の権限
                  {permission.editable
                    ? <span className="hidden font-normal tracking-normal sm:inline">Shift+Tab でも切替</span>
                    : <span className="font-normal tracking-normal">変更は管理者</span>}
                </p>
                <div className="grid grid-cols-2 gap-1">
                  {PERMISSION_MODES.map((option) => {
                    const selected = option.id === permission.mode;
                    return (
                      <button key={option.id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item
                        data-permission-option={option.id} disabled={!permission.editable && !selected} title={option.hint}
                        onClick={() => { if (permission.editable) permission.onChange(option.id); close(); }}
                        className={`${SEGMENT} ${!selected ? UNSELECTED : option.id === "auto" ? TONE.accent : TONE.amber}`}>
                        <span className="flex max-w-full items-center gap-1.5 text-sm font-medium leading-tight">
                          <span aria-hidden className="font-mono text-xs font-semibold tracking-[-0.2em]">{option.icon}</span>
                          <span className="truncate">{option.label}</span>
                        </span>
                        <span className={SEGMENT_NOTE}>{PERMISSION_NOTE[option.id]}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}

/** ＋の隣に出す小さな札（既定から変えた機能・OpenCode の権限）。押すと＋のメニューと同じ選択を外す/切り替える。 */
export function ComposerPill({ icon, label, title, tone = "accent", onClick, onClear, kind }: {
  icon: ReactNode; label: string; title: string; tone?: "accent" | "amber" | "plan";
  onClick?: () => void; onClear?: () => void;
  /** E2E と画面の計測用（data-composer-pill） */
  kind: string;
}) {
  const color = TONE[tone];
  return (
    <span data-composer-pill={kind} className={`flex h-9 min-w-0 shrink items-center rounded-full border text-xs font-medium ${color}`}>
      <button type="button" onClick={onClick} title={title} aria-label={title}
        className="flex h-11 min-w-0 items-center gap-1.5 pl-3 pr-2 focus:outline-none focus-visible:underline">
        <span aria-hidden className="shrink-0 leading-none">{icon}</span>
        <span data-pill-label className="min-w-0 truncate">{label}</span>
      </button>
      {onClear && (
        <button type="button" onClick={onClear} aria-label={`${label} を外す（自動判定に戻す）`} title="外す"
          className="grid h-11 w-8 shrink-0 place-items-center rounded-r-full opacity-70 hover:opacity-100 focus:outline-none focus-visible:underline">
          ×
        </button>
      )}
    </span>
  );
}
