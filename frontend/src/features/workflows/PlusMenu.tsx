/** 入力欄の左下の「＋」。添付・機能の選択・OpenCode の権限を 1 つのメニューにまとめる。
 *
 * 2026-11-18 利用者の依頼「クリップ・機能選択・権限のボタンを並べると煩雑。Claude のように左下に＋を置いて、
 * そこから各機能へ」。あわせて「OpenCode の権限が消えた」を直す: 以前は入力欄の下の行に「いまのモードが
 * OpenCode のときだけ」出していたので、自動判定が文字を打つたびにチャットへ判定し直すと消えた。
 * ここでは OpenCode が使えるなら、判定に関係なくいつでも権限を変えられる。
 *
 * メニューは画面に固定して＋の上に出す（入力カードの枠に切られない）。行は 48px 以上、背の低い画面では
 * 中をスクロールする。外側・Escape・スクロール・回転で閉じる。矢印キーで行を移る。 */
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconPaperclip, IconPlus } from "../../components/icons";
import { AUTO_MODE_OPTION, type ModeOption } from "./ModeChip";
import { PERMISSION_MODES, type PermissionMode } from "./OpenCodeActivity";

export interface PlusAction { id: string; icon: ReactNode; label: string; hint: string; disabled?: boolean; onSelect: () => void }

const ROW = "flex min-h-12 w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/50 disabled:opacity-40";
const ICON = "grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-100 text-base dark:bg-zinc-800";
const HEADING = "px-2.5 pb-1 pt-2 text-[10px] font-semibold tracking-wider text-zinc-400 dark:text-zinc-500";

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
  const [pos, setPos] = useState<{ left: number; bottom: number; maxHeight: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
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
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>("[data-plus-item]:not(:disabled)") ?? [])];
      if (items.length === 0) return;
      event.preventDefault();
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
      items[next].focus();
    };
    const onScroll = (event: Event) => {
      if (!menu.current?.contains(event.target as Node)) setOpen(false);
    };
    const dismiss = () => setOpen(false);
    window.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", dismiss);
    menu.current?.querySelector<HTMLButtonElement>("[data-plus-item]:not(:disabled)")?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener("pointerdown", outside);
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
  const modes = [AUTO_MODE_OPTION, ...modeOptions];
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
          className="fixed z-[100] w-[min(21rem,calc(100vw-1.5rem))] overflow-y-auto overscroll-contain rounded-2xl border border-zinc-200 bg-white p-1.5 shadow-2xl shadow-black/10 dark:border-zinc-700 dark:bg-zinc-900 dark:shadow-black/50">
          <button type="button" role="menuitem" data-plus-item disabled={attachDisabled}
            onClick={() => { close(false); onAttach(); }}
            className={`${ROW} hover:bg-zinc-50 dark:hover:bg-white/[0.04]`}>
            <span aria-hidden className={ICON}><IconPaperclip /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-zinc-900 dark:text-zinc-50">ファイルを添付</span>
              <span className="mt-0.5 block truncate text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">画像は VLM へ、コード・文書・PDF は会話の資料へ</span>
            </span>
          </button>
          {actions.map((action) => (
            <button key={action.id} type="button" role="menuitem" data-plus-item data-plus-action={action.id} disabled={action.disabled}
              onClick={() => { close(false); action.onSelect(); }}
              className={`${ROW} hover:bg-zinc-50 dark:hover:bg-white/[0.04]`}>
              <span aria-hidden className={ICON}>{action.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-zinc-900 dark:text-zinc-50">{action.label}</span>
                <span className="mt-0.5 block text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">{action.hint}</span>
              </span>
            </button>
          ))}

          <div role="separator" className="mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800" />
          <div role="group" aria-label="機能">
            <p aria-hidden className={HEADING}>機能</p>
            {modes.map((option, index) => {
              const selected = option.id === mode;
              const hint = option.id === "auto" ? (routing ? "計画中…" : `いまは ${detectedLabel}`) : option.hint;
              return (
                <Fragment key={option.id}>
                  <button type="button" role="menuitemradio" aria-checked={selected} data-plus-item data-mode-option={option.id}
                    onClick={() => { onModeChange(option.id); close(); }}
                    className={`${ROW} ${selected ? "bg-zinc-100 dark:bg-white/[0.07]" : "hover:bg-zinc-50 dark:hover:bg-white/[0.04]"}`}>
                    <span aria-hidden className={ICON}>{option.icon}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-zinc-900 dark:text-zinc-50">{option.label}</span>
                      <span className="mt-0.5 block truncate text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">{hint}</span>
                    </span>
                    <span aria-hidden className="w-4 shrink-0 text-center text-sm text-accent-600 dark:text-accent-400">{selected ? "✓" : ""}</span>
                  </button>
                  {index === 0 && <div role="separator" className="mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800" />}
                </Fragment>
              );
            })}
          </div>

          {agent && (
            <>
              <div role="separator" className="mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800" />
              <div role="group" aria-label="OpenCode のモード">
                <p aria-hidden className={HEADING}>OpenCode のモード（PC は Shift+Tab でも切替）</p>
                {([["build", "🛠️", "ビルド", "作る・直す（ファイルを編集し、コマンドを実行する）"],
                  ["plan", "📋", "プラン", "計画だけ。ファイルの編集や変更を伴うコマンドはしない"]] as const).map(([id, icon, label, hint]) => {
                  const selected = agent.mode === id;
                  return (
                    <button key={id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item data-agent-option={id}
                      onClick={() => { agent.onChange(id); close(); }}
                      className={`${ROW} ${selected ? (id === "plan" ? "bg-sky-50 dark:bg-sky-950/40" : "bg-zinc-100 dark:bg-white/[0.07]") : "hover:bg-zinc-50 dark:hover:bg-white/[0.04]"}`}>
                      <span aria-hidden className={ICON}>{icon}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-zinc-900 dark:text-zinc-50">{label}</span>
                        <span className="mt-0.5 block text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">{hint}</span>
                      </span>
                      <span aria-hidden className="w-4 shrink-0 text-center text-sm text-accent-600 dark:text-accent-400">{selected ? "✓" : ""}</span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
          {permission && (
            <>
              <div role="separator" className="mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800" />
              <div role="group" aria-label="OpenCode の権限">
                <p aria-hidden className={HEADING}>OpenCode の権限{permission.editable ? "（PC は Shift+Tab でも切替）" : "（変更は管理者）"}</p>
                {PERMISSION_MODES.map((option) => {
                  const selected = option.id === permission.mode;
                  return (
                    <button key={option.id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item
                      data-permission-option={option.id} disabled={!permission.editable && !selected}
                      onClick={() => { if (permission.editable) permission.onChange(option.id); close(); }}
                      className={`${ROW} ${selected ? "bg-zinc-100 dark:bg-white/[0.07]" : "hover:bg-zinc-50 dark:hover:bg-white/[0.04]"}`}>
                      <span aria-hidden className={`${ICON} font-mono text-xs font-semibold tracking-[-0.2em] ${option.id === "auto" ? "text-accent-600 dark:text-accent-400" : "text-amber-600 dark:text-amber-400"}`}>{option.icon}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-zinc-900 dark:text-zinc-50">{option.label}</span>
                        <span className="mt-0.5 block text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">{option.hint}</span>
                      </span>
                      <span aria-hidden className="w-4 shrink-0 text-center text-sm text-accent-600 dark:text-accent-400">{selected ? "✓" : ""}</span>
                    </button>
                  );
                })}
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
  const color = tone === "plan"
    ? "border-sky-400 bg-sky-100 text-sky-800 dark:border-sky-600 dark:bg-sky-900/60 dark:text-sky-100"
    : tone === "accent"
    ? "border-accent-500/40 bg-accent-50 text-accent-700 dark:border-accent-500/40 dark:bg-accent-600/15 dark:text-accent-300"
    : "border-amber-500/40 bg-amber-50 text-amber-700 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300";
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
