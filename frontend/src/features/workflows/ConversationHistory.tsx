/** AI アシスタントの会話の履歴。
 *
 * 新しいチャット・検索・日付ごとのまとまり（今日 / 昨日 / 過去7日間 / それ以前）・行ごとの「⋯」
 * （名前を変更・削除）。PC（lg 以上）は会話の左に常に出る列で、畳める。携帯は上の行の
 * 履歴ボタンで左から出るシート（☰ はアプリ全体のドロワーのまま）。中身は同じ部品。 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconCompose, IconSearch, IconX } from "../../components/icons";

export interface ConversationSummary { id: string; title: string; updated_at: string; archived?: boolean }

export const HISTORY_GROUPS = ["今日", "昨日", "過去7日間", "それ以前"] as const;
export type HistoryGroup = (typeof HISTORY_GROUPS)[number];

const DAY = 86_400_000;
const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/** 更新日時から、どのまとまりに入るかを決める（利用者の端末の暦日で数える）。 */
export function historyGroupOf(updatedAt: string, now: Date = new Date()): HistoryGroup {
  // サーバーの時刻はタイムゾーン無しの UTC（SQLite）。付いていなければ UTC として読む
  const iso = updatedAt.endsWith("Z") || /[+-]\d\d:\d\d$/.test(updatedAt) ? updatedAt : `${updatedAt}Z`;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return "それ以前";
  const days = Math.round((startOfDay(now) - startOfDay(new Date(time))) / DAY);
  if (days <= 0) return "今日";
  if (days === 1) return "昨日";
  if (days <= 7) return "過去7日間";
  return "それ以前";
}

export function groupConversations(list: ConversationSummary[], now: Date = new Date()) {
  const groups = new Map<HistoryGroup, ConversationSummary[]>();
  for (const conversation of list) {
    const key = historyGroupOf(conversation.updated_at, now);
    groups.set(key, [...(groups.get(key) ?? []), conversation]);
  }
  return HISTORY_GROUPS.filter((label) => groups.has(label)).map((label) => ({ label, items: groups.get(label)! }));
}

/** 履歴の列を畳む／開くときの印（左に仕切りのある枠）。 */
export function IconHistoryPanel({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden className={className}>
      <rect x="3" y="4" width="18" height="16" rx="3" />
      <path d="M9 4v16" />
    </svg>
  );
}

interface HistoryProps {
  conversations: ConversationSummary[] | undefined;
  currentId: string;
  /** 回答の生成中は会話を切り替えられない。 */
  busy: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<void> | void;
  onDelete: (conversation: ConversationSummary) => void;
  /** 右へスワイプ: アーカイブ（archived=false で戻す） */
  onArchive?: (conversation: ConversationSummary, archived: boolean) => void;
  /** 左へスワイプ: 確認なしで削除（呼び出し側が「元に戻す」を出す） */
  onSwipeDelete?: (conversation: ConversationSummary) => void;
  /** アーカイブした会話（開いたときだけ読む） */
  archivedConversations?: ConversationSummary[];
  onShowArchived?: (show: boolean) => void;
}

/** 履歴の中身（列とシートで共通）。 */
export function ConversationHistory({ conversations, currentId, busy, onOpen, onNew, onRename, onDelete, onArchive,
  onSwipeDelete, archivedConversations, onShowArchived, header }: HistoryProps & { header: ReactNode }) {
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const toggleArchived = () => {
    setShowArchived((v) => !v);
    onShowArchived?.(!showArchived);
  };
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const filtered = useMemo(() => {
    const q = query.trim().toLocaleLowerCase("ja");
    const list = conversations ?? [];
    return q ? list.filter((conversation) => conversation.title.toLocaleLowerCase("ja").includes(q)) : list;
  }, [conversations, query]);
  const groups = useMemo(() => groupConversations(filtered), [filtered]);
  return (
    <div data-assistant-history className="flex h-full min-h-0 w-full min-w-0 flex-col">
      {header}
      <div className="shrink-0 space-y-2 px-3 pb-2">
        <button
          type="button"
          onClick={onNew}
          disabled={busy}
          className="flex min-h-11 w-full items-center gap-2.5 rounded-xl bg-accent-600 px-3.5 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 disabled:opacity-50"
        >
          <IconCompose className="shrink-0 text-lg" />
          新しいチャット
        </button>
        <label className="relative block">
          <span className="sr-only">会話を検索</span>
          <IconSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="会話を検索"
            className="min-h-11 w-full rounded-xl border border-zinc-200 bg-white pl-9 pr-3 text-sm outline-none transition placeholder:text-zinc-400 focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20 dark:border-zinc-700 dark:bg-zinc-950/60 lg:min-h-10"
          />
        </label>
      </div>
      <nav aria-label="会話の履歴" className="safe-bottom min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-2 pb-3">
        {conversations === undefined && <p className="px-3 py-4 text-xs text-zinc-500">読み込み中…</p>}
        {conversations !== undefined && groups.length === 0 && (
          <p className="px-3 py-4 text-xs leading-relaxed text-zinc-500">
            {query.trim() ? "一致する会話はありません" : "まだ会話はありません。下の入力欄から始めてください。"}
          </p>
        )}
        {groups.map((group) => (
          <section key={group.label} aria-label={group.label} data-history-group={group.label}>
            <h3 className="px-3 pb-1 pt-3 text-[11px] font-medium tracking-wide text-zinc-500 dark:text-zinc-400">{group.label}</h3>
            <ul className="space-y-0.5">
              {group.items.map((conversation) => (
                <SwipeRow
                  key={conversation.id}
                  label={conversation.title}
                  disabled={renamingId === conversation.id || (busy && conversation.id === currentId)}
                  right={onArchive ? { label: "アーカイブ", tone: "archive", onCommit: () => onArchive(conversation, true) } : undefined}
                  left={onSwipeDelete ? { label: "削除", tone: "delete", onCommit: () => onSwipeDelete(conversation) } : undefined}
                >
                  <HistoryRow
                    conversation={conversation}
                    active={conversation.id === currentId}
                    busy={busy}
                    renaming={renamingId === conversation.id}
                    onOpen={() => onOpen(conversation.id)}
                    onStartRename={() => setRenamingId(conversation.id)}
                    onRename={async (title) => {
                      setRenamingId(null);
                      if (title && title !== conversation.title) await onRename(conversation.id, title);
                    }}
                    onDelete={() => onDelete(conversation)}
                    onArchive={onArchive ? () => onArchive(conversation, true) : undefined}
                  />
                </SwipeRow>
              ))}
            </ul>
          </section>
        ))}
        {onArchive && (
          <div className="mt-3 border-t border-zinc-200 pt-2 dark:border-zinc-800">
            <button type="button" onClick={toggleArchived} aria-expanded={showArchived}
              className="flex min-h-11 w-full items-center justify-between rounded-xl px-3 text-left text-xs font-medium text-zinc-500 hover:bg-zinc-200/60 dark:hover:bg-zinc-800/70">
              アーカイブした会話
              <span aria-hidden>{showArchived ? "▾" : "▸"}</span>
            </button>
            {showArchived && (
              <ul className="space-y-0.5" aria-label="アーカイブした会話">
                {archivedConversations === undefined && <li className="px-3 py-2 text-xs text-zinc-500">読み込み中…</li>}
                {archivedConversations?.length === 0 && <li className="px-3 py-2 text-xs text-zinc-500">ありません。会話を右へスワイプするとここへ移ります</li>}
                {(archivedConversations ?? []).map((conversation) => (
                  <SwipeRow key={conversation.id} label={conversation.title}
                    right={{ label: "戻す", tone: "archive", onCommit: () => onArchive(conversation, false) }}
                    left={onSwipeDelete ? { label: "削除", tone: "delete", onCommit: () => onSwipeDelete(conversation) } : undefined}>
                    <div className="flex min-w-0 items-center rounded-xl hover:bg-zinc-200/60 dark:hover:bg-zinc-800/70">
                      <button type="button" onClick={() => onOpen(conversation.id)} disabled={busy}
                        className="min-h-11 min-w-0 flex-1 truncate rounded-xl px-3 text-left text-sm text-zinc-500 disabled:opacity-50">
                        {conversation.title}
                      </button>
                      <button type="button" onClick={() => onArchive(conversation, false)}
                        className="min-h-11 shrink-0 rounded-xl px-3 text-xs font-medium text-accent-700 hover:bg-accent-50 dark:text-accent-400 dark:hover:bg-accent-600/15">
                        戻す
                      </button>
                    </div>
                  </SwipeRow>
                ))}
              </ul>
            )}
          </div>
        )}
      </nav>
    </div>
  );
}

type SwipeAction = { label: string; tone: "archive" | "delete"; onCommit: () => void };

/** 行の左右スワイプ（iPhone のメールと同じ）。右へ: アーカイブ、左へ: 削除。
 *  一定以上引いて指を離すとすぐ適用する（確認なし。呼び出し側が「元に戻す」を出す）ので、続けて操作できる。
 *  縦の指の動きはそのまま一覧のスクロールに任せる（最初の動きの向きで決める）。 */
function SwipeRow({ children, label, left, right, disabled = false }: {
  children: ReactNode; label: string; left?: SwipeAction; right?: SwipeAction; disabled?: boolean;
}) {
  const [dx, setDx] = useState(0);
  const [leaving, setLeaving] = useState<0 | 1 | -1>(0);
  const start = useRef<{ x: number; y: number; id: number; axis: "" | "x" | "y"; width: number } | null>(null);
  const moved = useRef(false);
  const threshold = (width: number) => Math.min(120, width * 0.35);
  const onPointerDown = (event: React.PointerEvent<HTMLLIElement>) => {
    if (disabled || leaving || (event.pointerType === "mouse" && event.button !== 0)) return;
    start.current = { x: event.clientX, y: event.clientY, id: event.pointerId, axis: "", width: event.currentTarget.offsetWidth };
    moved.current = false;
  };
  const onPointerMove = (event: React.PointerEvent<HTMLLIElement>) => {
    const s = start.current;
    if (!s || s.id !== event.pointerId) return;
    const mx = event.clientX - s.x;
    const my = event.clientY - s.y;
    if (!s.axis) {
      if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
      s.axis = Math.abs(mx) > Math.abs(my) * 1.4 ? "x" : "y";
      if (s.axis === "x") event.currentTarget.setPointerCapture(event.pointerId);
    }
    if (s.axis !== "x") return;
    moved.current = true;
    const allowed = (mx > 0 && right) || (mx < 0 && left) ? mx : mx / 6;  // 動作の無い側は少しだけ動く
    setDx(allowed);
  };
  const finish = (event: React.PointerEvent<HTMLLIElement>) => {
    const s = start.current;
    start.current = null;
    if (!s || s.axis !== "x") return;
    const width = s.width;
    const action = dx > 0 ? right : left;
    if (action && Math.abs(dx) >= threshold(width)) {
      setLeaving(dx > 0 ? 1 : -1);
      setDx(dx > 0 ? width : -width);
      window.setTimeout(() => {
        action.onCommit();
        setLeaving(0);
        setDx(0);
      }, 180);
    } else {
      setDx(0);
    }
    void event;
  };
  const showing = dx > 0 ? right : dx < 0 ? left : undefined;
  const armed = showing && start.current?.width ? Math.abs(dx) >= threshold(start.current.width) : Boolean(leaving);
  return (
    <li className="relative list-none overflow-hidden rounded-xl" data-swipe-row={label}
      style={{ touchAction: "pan-y" }}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={finish} onPointerCancel={finish}
      onClickCapture={(event) => {
        // スワイプの後の click で会話を開かない
        if (moved.current) { event.stopPropagation(); event.preventDefault(); moved.current = false; }
      }}>
      {showing && (
        <div aria-hidden className={`absolute inset-0 flex items-center px-4 text-sm font-semibold text-white transition-colors ${
          dx > 0 ? "justify-start" : "justify-end"} ${showing.tone === "delete"
          ? (armed ? "bg-red-600" : "bg-red-400")
          : (armed ? "bg-emerald-600" : "bg-emerald-400")}`}>
          {showing.label}
        </div>
      )}
      <div className={`relative bg-zinc-50 dark:bg-zinc-900 ${start.current ? "" : "transition-transform duration-200 ease-out"}`}
        style={{ transform: dx ? `translateX(${dx}px)` : undefined }}>
        {children}
      </div>
    </li>
  );
}

function HistoryRow({ conversation, active, busy, renaming, onOpen, onStartRename, onRename, onDelete, onArchive }: {
  onArchive?: () => void;
  conversation: ConversationSummary;
  active: boolean;
  busy: boolean;
  renaming: boolean;
  onOpen: () => void;
  onStartRename: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  if (renaming) {
    return (
      <div>
        <TitleEditor initial={conversation.title} label={`「${conversation.title}」の新しい名前`} onDone={onRename}
          className="min-h-11 w-full rounded-xl border border-accent-500 bg-white px-3 text-sm outline-none ring-2 ring-accent-500/20 dark:bg-zinc-950" />
      </div>
    );
  }
  return (
    <div
      data-history-row={conversation.id}
      className={`group flex min-w-0 items-center rounded-xl transition-colors ${active
        ? "bg-white shadow-sm ring-1 ring-zinc-200/80 dark:bg-zinc-800 dark:ring-zinc-700/60"
        : "hover:bg-zinc-200/60 dark:hover:bg-zinc-800/70"}`}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={busy && !active}
        aria-current={active ? "true" : undefined}
        title={conversation.title}
        className={`min-h-11 min-w-0 flex-1 truncate rounded-xl pl-3 pr-1 text-left text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 disabled:opacity-50 ${
          active ? "font-medium text-zinc-900 dark:text-white" : "text-zinc-700 dark:text-zinc-300"}`}
      >
        {conversation.title}
      </button>
      <RowMenu
        label={`「${conversation.title}」の操作`}
        items={[
          { id: "rename", label: "名前を変更", onSelect: onStartRename },
          ...(onArchive ? [{ id: "archive", label: "アーカイブ", disabled: busy && active, onSelect: onArchive }] : []),
          { id: "delete", label: "削除", danger: true, disabled: busy && active, onSelect: onDelete },
        ]}
      />
    </div>
  );
}

/** 題名をその場で書き換える欄。Enter・フォーカスを外すで確定、Escape で取り消し。 */
export function TitleEditor({ initial, label, className, onDone }: {
  initial: string; label: string; className: string; onDone: (title: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const settled = useRef(false);
  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
    ref.current?.select();
  }, []);
  const finish = (title: string) => {
    if (settled.current) return;
    settled.current = true;
    onDone(title);
  };
  return (
    <input
      ref={ref}
      value={value}
      maxLength={200}
      aria-label={label}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => finish(value.trim())}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Enter") {
          event.preventDefault();
          finish(value.trim());
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          finish("");
        }
      }}
      className={className}
    />
  );
}

interface MenuItem { id: string; label: string; danger?: boolean; disabled?: boolean; onSelect: () => void }

/** 行の「⋯」。メニューは画面に固定して出す（一覧の枠に切られない）。行は 44px 以上。 */
function RowMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!ref.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
      button.current?.focus({ preventScroll: true });
    };
    const dismiss = () => setOpen(false);
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [open]);
  const toggle = () => {
    const rect = button.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(208, window.innerWidth - 32);
      const height = items.length * 48 + 8;
      const top = rect.bottom + 4 + height > window.innerHeight ? Math.max(8, rect.top - 4 - height) : rect.bottom + 4;
      setPos({ left: Math.max(16, Math.min(rect.right - width, window.innerWidth - width - 16)), top });
    }
    setOpen((value) => !value);
  };
  return (
    <div ref={ref} className="shrink-0">
      <button
        ref={button}
        type="button"
        onClick={toggle}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`grid h-11 w-11 place-items-center rounded-xl text-zinc-500 transition hover:bg-zinc-200/70 hover:text-zinc-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 dark:text-zinc-400 dark:hover:bg-zinc-700 dark:hover:text-zinc-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-visible:opacity-100 [@media(hover:hover)]:group-hover:opacity-100 ${open ? "!opacity-100" : ""}`}
      >
        <span aria-hidden className="text-base leading-none tracking-widest">⋯</span>
      </button>
      {open && pos && createPortal(
        <ul ref={menu} role="menu" aria-label={label} style={{ left: pos.left, top: pos.top }}
          className="fixed z-[100] w-[min(13rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-zinc-200 bg-white py-1 shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
          {items.map((item) => (
            <li key={item.id} role="none">
              <button type="button" role="menuitem" disabled={item.disabled}
                onClick={() => { setOpen(false); item.onSelect(); }}
                className={`flex min-h-12 w-full items-center px-3.5 text-left text-sm font-medium hover:bg-zinc-50 focus:outline-none focus-visible:bg-zinc-100 disabled:opacity-40 dark:hover:bg-zinc-800 dark:focus-visible:bg-zinc-800 ${
                  item.danger ? "text-red-600 dark:text-red-400" : "text-zinc-800 dark:text-zinc-100"}`}>
                {item.label}
              </button>
            </li>
          ))}
        </ul>,
        document.body,
      )}
    </div>
  );
}

const SHEET_FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** 携帯の履歴シート。左から滑り込む。外側・Escape・× で閉じ、開いている間は Tab を中に留める。 */
export function HistorySheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  const [shown, setShown] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    setShown(false);
    const timer = window.setTimeout(() => setMounted(false), 220);
    return () => window.clearTimeout(timer);
  }, [open]);
  useEffect(() => {
    if (!mounted || !open) return;
    const frame = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(frame);
  }, [mounted, open]);
  useEffect(() => {
    if (!mounted || !open) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("[data-history-close]")?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      /* 行の「⋯」のメニューと削除の確認は body に出す。開いている間はそちらに任せる
         （Escape は確認だけを閉じ、Tab は確認の中を回る）。 */
      if (document.querySelector('[role="menu"], [role="alertdialog"]')) return;
      if (event.key === "Escape") {
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(SHEET_FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (!active || !panel.current.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (previous && document.contains(previous)) previous.focus({ preventScroll: true });
    };
  }, [mounted, open]);
  if (!mounted) return null;
  return createPortal(
    <div data-history-sheet data-state={shown ? "open" : "closed"} className="fixed inset-0 z-50 lg:hidden" role="presentation">
      <div
        aria-hidden
        onClick={onClose}
        className={`absolute inset-0 bg-black/45 backdrop-blur-[2px] transition-opacity duration-200 motion-reduce:transition-none ${shown ? "opacity-100" : "opacity-0"}`}
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="会話の履歴"
        className={`absolute inset-y-0 left-0 w-[min(20rem,calc(100vw-2.5rem))] border-r border-zinc-200/80 bg-zinc-50 shadow-2xl transition-transform duration-200 ease-out motion-reduce:transition-none dark:border-zinc-800 dark:bg-zinc-900 ${
          shown ? "translate-x-0" : "-translate-x-full"}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** 履歴の先頭の行（題と、畳む／閉じるボタン）。上の行と同じ高さ。 */
export function HistoryHeader({ action }: { action: ReactNode }) {
  return (
    <div className="safe-top shrink-0 px-3">
      <div className="flex h-14 items-center gap-1">
        <h2 className="min-w-0 flex-1 truncate pl-1 text-[15px] font-semibold tracking-tight">チャット</h2>
        {action}
      </div>
    </div>
  );
}

export function HistoryCloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button type="button" onClick={onClose} data-history-close aria-label="履歴を閉じる"
      className="-mr-1.5 grid h-11 w-11 shrink-0 place-items-center rounded-xl text-xl text-zinc-600 transition-colors hover:bg-zinc-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800">
      <IconX />
    </button>
  );
}
