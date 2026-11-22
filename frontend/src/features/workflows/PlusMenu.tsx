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
import { IconChat, IconPaperclip, IconPlay, IconPlus, IconSearch, IconSettings, IconSparkle, IconTerminal } from "../../components/icons";
import { AUTO_MODE_OPTION, type ModeOption } from "./ModeChip";
import { PERMISSION_MODES, type PermissionMode } from "./OpenCodeActivity";
import { CAPABILITIES, TARGETS, targetInfo, type DevTargetState } from "./workspaceTargets";

/** 機能のアイコン。＋のタイルと入力欄の札は線のアイコンでそろえる（2026-11-20 利用者「スタイリッシュに」）。
 *  回答の見出しなど他の場所の絵文字はそのまま */
const MODE_ICONS: Record<string, (p: { className?: string }) => ReactNode> = {
  auto: IconSparkle, chat: IconChat, investigate: IconSearch, gen: IconSettings, run: IconPlay, code: IconTerminal,
};
export function modeIcon(id: string, fallback: ReactNode): ReactNode {
  const Icon = MODE_ICONS[id];
  return Icon ? <Icon /> : fallback;
}

export interface PlusAction { id: string; icon: ReactNode; label: string; hint: string; disabled?: boolean; onSelect: () => void }

const FOCUS = "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/50 disabled:opacity-40";
// 行はどれも「アイコン・名前・その下に短い説明」で揃える（2026-11-20 利用者「添付の説明が右にずれている。他と合わせて」）
const ROW = `flex h-11 w-full items-center gap-2.5 rounded-xl px-1.5 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-white/[0.06] ${FOCUS}`;
const ICON = "grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-zinc-100 text-[17px] text-zinc-600 dark:bg-white/[0.06] dark:text-zinc-300";
const HEADING = "flex min-w-0 items-baseline gap-2 px-1.5 pb-1 pt-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400";
const SEPARATOR = "mx-1 my-1 border-t border-zinc-100 dark:border-white/[0.06]";
/** 選択中の色。メニューと入力欄の札で揃える */
const TONE = {
  plan: "border-sky-400 bg-sky-100 text-sky-800 dark:border-sky-600 dark:bg-sky-900/60 dark:text-sky-100",
  accent: "border-accent-500/40 bg-accent-50 text-accent-700 dark:border-accent-500/40 dark:bg-accent-600/15 dark:text-accent-300",
  amber: "border-amber-500/40 bg-amber-50 text-amber-700 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300",
};
const UNSELECTED = "border-transparent bg-zinc-50 text-zinc-700 hover:bg-zinc-100 dark:bg-white/[0.03] dark:text-zinc-200 dark:hover:bg-white/[0.07]";
const SEGMENT = `flex h-11 min-w-0 items-center justify-center gap-1 rounded-xl border px-2 text-sm font-medium ${FOCUS}`;
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

/** 1 行のトグル（左に名前と今の状態の短い説明、右に 2 択）。説明は 1 行まで */
function ToggleRow({ label, aria, note, children }: { label: string; aria: string; note: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={aria} className="flex min-h-11 items-center gap-2 px-1.5">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium leading-4 text-zinc-900 dark:text-zinc-50">{label}</span>
        <span data-toggle-note className="block truncate text-[10px] leading-3 text-zinc-500 dark:text-zinc-400">{note}</span>
      </span>
      <span className="grid shrink-0 grid-cols-2 gap-1">{children}</span>
    </div>
  );
}

/** 開発対象（作業場の機能の組み合わせ）。作るときは選べ、選んだ作業場は表示だけ。
 *  違いは 1 行の説明で読める。「詳しく」で機能の一覧（作るときだけ個別に入切）。 */
function DevTargetSection({ state, detail, onDetail }: { state: DevTargetState; detail: boolean; onDetail: (v: boolean) => void }) {
  const editable = state.mode === "create";
  const info = targetInfo(state.target);
  const caps = new Set(state.capabilities);
  const targetReason = (id: string): string => {
    if (id === "controldeck" && !state.isBody) return "元が ControlDeck 本体のときだけ選べます";
    if (id === "controldeck" && !state.canManage) return "ControlDeck 本体の作業場には設定の権限（settings.manage）が要ります";
    return "";
  };
  const capReason = (id: string): string => {
    if (id === "gpu") return "準備中";
    if (id === "mirror" && !state.isBody) return "元が ControlDeck 本体のときだけ";
    return "";
  };
  return (
    <>
      <div role="separator" className={SEPARATOR} />
      <div role="group" aria-label="開発対象" data-dev-target>
        <p aria-hidden className={HEADING}>
          <span className="shrink-0">開発対象</span>
          <span className="min-w-0 flex-1 truncate text-right text-[10px] font-normal text-zinc-400 dark:text-zinc-500">
            {editable ? "作るときに選ぶ" : "この作業場（変えられません）"}
          </span>
        </p>
        <div className="grid grid-cols-3 gap-1">
          {TARGETS.map((t) => {
            const selected = t.id === state.target;
            const reason = editable ? targetReason(t.id) : "";
            return (
              <button key={t.id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item data-target-option={t.id}
                disabled={!editable || Boolean(reason)} title={reason || t.label}
                onClick={() => state.onTarget(t.id)}
                className={`flex h-11 min-w-0 flex-col items-center justify-center rounded-xl border px-1 ${FOCUS} ${selected ? TONE.accent : UNSELECTED} ${!editable && selected ? "disabled:opacity-100" : ""}`}>
                <span className="block max-w-full truncate text-[12px] font-medium leading-tight">{t.short}</span>
              </button>
            );
          })}
        </div>
        <p data-target-note className="line-clamp-2 px-1.5 pt-1 text-[11px] leading-4 text-zinc-500 dark:text-zinc-400" title={info?.note}>
          {info?.note ?? "この作業場の開発対象は不明です（古い作業場）"}
        </p>
        <button type="button" data-plus-item data-target-detail aria-expanded={detail} onClick={() => onDetail(!detail)}
          className={`flex h-11 w-full items-center gap-1 rounded-lg px-1.5 text-[11px] text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-white/[0.06] ${FOCUS}`}>
          <span aria-hidden>{detail ? "▾" : "▸"}</span>詳しく（機能の一覧）
        </button>
        {detail && (
          <ul data-target-capabilities className="space-y-0.5 pb-1">
            {CAPABILITIES.map((c) => {
              const on = caps.has(c.id);
              const reason = capReason(c.id);
              const locked = !editable || Boolean(reason);
              return (
                <li key={c.id}>
                  <button type="button" role="switch" aria-checked={on} data-plus-item data-capability={c.id} disabled={locked}
                    title={reason || c.note} onClick={() => state.onCapability(c.id, !on)}
                    className={`flex h-11 w-full items-center gap-2 rounded-lg px-1.5 text-left hover:bg-zinc-100 disabled:hover:bg-transparent dark:hover:bg-white/[0.06] ${FOCUS} ${locked && !reason ? "disabled:opacity-100" : ""}`}>
                    <span aria-hidden className={`grid h-5 w-9 shrink-0 items-center rounded-full px-0.5 transition-colors ${on ? "bg-accent-600" : "bg-zinc-300 dark:bg-zinc-600"}`}>
                      <span className={`h-4 w-4 rounded-full bg-white shadow transition-transform ${on ? "translate-x-4" : ""}`} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium leading-4 text-zinc-900 dark:text-zinc-50">{c.label}</span>
                      <span className="block truncate text-[10px] leading-3 text-zinc-500 dark:text-zinc-400">{reason || c.note}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </>
  );
}

export function PlusMenu({
  disabled, onAttach, attachDisabled, mode, modeOptions, detectedLabel, routing, onModeChange, permission, agent, actions = [], devTarget = null,
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
  /** OpenCode の「開発の作業場」を使うときだけ（作るときは選べる・選んだ作業場は表示だけ） */
  devTarget?: DevTargetState | null;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(false);
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
          className="fixed z-[100] w-[min(21rem,calc(100vw-1.5rem))] touch-pan-y overflow-y-auto overscroll-contain rounded-2xl border border-zinc-200/80 bg-white/95 p-1 shadow-xl shadow-black/10 backdrop-blur-xl dark:border-white/10 dark:bg-zinc-900/95 dark:shadow-black/60">
          {[{ id: "attach", icon: <IconPaperclip />, label: "ファイルを添付", hint: "画像・コード・文書・PDF", disabled: attachDisabled,
            title: "画像は VLM へ、コード・文書・PDF は会話の資料へ", onSelect: onAttach }, ...actions].map((action) => (
            <button key={action.id} type="button" role="menuitem" data-plus-item data-plus-action={action.id} disabled={action.disabled}
              title={"title" in action ? action.title : action.hint}
              onClick={() => { close(false); action.onSelect(); }} className={ROW}>
              <span aria-hidden className={ICON}>{action.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium leading-5 text-zinc-900 dark:text-zinc-50">{action.label}</span>
                <span className="block truncate text-[11px] leading-4 text-zinc-500 dark:text-zinc-400">{action.hint}</span>
              </span>
            </button>
          ))}

          <div role="separator" className={SEPARATOR} />
          <div role="group" aria-label="機能">
            <p aria-hidden className={HEADING}>
              <span className="shrink-0">機能</span>
              <span className="min-w-0 flex-1 truncate text-right text-[10px] font-normal text-zinc-400 dark:text-zinc-500">{modeHint}</span>
            </p>
            <div className="grid grid-cols-3 gap-1">
              {modes.map((option) => {
                const selected = option.id === mode;
                return (
                  <button key={option.id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item data-mode-option={option.id}
                    title={option.id === "auto" ? autoHint : option.hint} onClick={() => { onModeChange(option.id); close(); }}
                    className={`flex h-11 min-w-0 flex-col items-center justify-center rounded-xl border px-1 ${FOCUS} ${selected ? TONE.accent : UNSELECTED}`}>
                    <span aria-hidden className="text-base leading-none">{modeIcon(option.id, option.icon)}</span>
                    <span className="block max-w-full truncate text-[11px] font-medium leading-tight">{option.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {devTarget && <DevTargetSection state={devTarget} detail={detail} onDetail={setDetail} />}
          {(agent || permission) && <div role="separator" className={SEPARATOR} />}
          {agent && (
            <ToggleRow label="プラン／ビルド" aria="OpenCode のモード" note={AGENT_MODES.find((m) => m[0] === agent.mode)?.[4] ?? ""}>
              {AGENT_MODES.map(([id, icon, label, hint]) => {
                const selected = agent.mode === id;
                return (
                  <button key={id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item data-agent-option={id} title={hint}
                    onClick={() => { agent.onChange(id); close(); }}
                    className={`${SEGMENT} ${!selected ? UNSELECTED : id === "plan" ? TONE.plan : TONE.accent}`}>
                    <span aria-hidden>{icon}</span><span className="truncate">{label}</span>
                  </button>
                );
              })}
            </ToggleRow>
          )}
          {permission && (
            <ToggleRow label="権限" aria="OpenCode の権限"
              note={permission.editable ? PERMISSION_NOTE[permission.mode] : `${PERMISSION_NOTE[permission.mode]}・変更は管理者`}>
              {PERMISSION_MODES.map((option) => {
                const selected = option.id === permission.mode;
                return (
                  <button key={option.id} type="button" role="menuitemradio" aria-checked={selected} data-plus-item
                    data-permission-option={option.id} disabled={!permission.editable && !selected} title={option.hint}
                    onClick={() => { if (permission.editable) permission.onChange(option.id); close(); }}
                    className={`${SEGMENT} ${!selected ? UNSELECTED : option.id === "auto" ? TONE.accent : TONE.amber}`}>
                    <span className="truncate">{option.label}</span>
                  </button>
                );
              })}
            </ToggleRow>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}

/** ＋の隣に出す小さな札（既定から変えた機能・OpenCode の権限）。押すと＋のメニューと同じ選択を外す/切り替える。 */
/** 入力欄の札。アイコンだけを出し（2026-11-19 利用者「文字はなし、×も不要、タップしたら消える」）、
 *  名前は読み上げとツールチップに残す。タップの動作は呼ぶ側が決める（外す・切り替える）。 */
export function ComposerPill({ icon, label, title, tone = "accent", onClick, kind }: {
  icon: ReactNode; label: string; title: string; tone?: "accent" | "amber" | "plan";
  onClick?: () => void;
  /** E2E と画面の計測用（data-composer-pill） */
  kind: string;
}) {
  const color = TONE[tone];
  return (
    <span data-composer-pill={kind} className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-sm ${color}`}>
      <button type="button" onClick={onClick} title={title} aria-label={title}
        className="grid h-11 w-11 place-items-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40">
        <span aria-hidden className="leading-none">{icon}</span>
        <span data-pill-label className="sr-only">{label}</span>
      </button>
    </span>
  );
}
