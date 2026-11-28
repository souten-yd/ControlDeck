/** AI Commander のタイル: コンソール 1 個分。
 *
 * 頭に CLI の名前・アカウント・表示名・生きているか・最終活動を出し、
 * 中身は既存の XtermView を埋め込み表示（embedded）で収める。
 * 頭を押すと全画面で大きく開く。 */
import { lazy, Suspense, useState, type MouseEvent } from "react";
import { ConsoleResumeMessageSheet } from "./ResumeMessageSheet";
import { UsageLine } from "./UsageLine";
import { useQueryClient } from "@tanstack/react-query";
import {
  accountTitle, commanderErrorText, setConsoleAutoResume, setConsoleLock, STATE_LABEL,
  type CommanderConsole, type CommanderState,
} from "../../api/commander";
import { useToasts } from "../../stores";
import { IconLock, IconLockOpen } from "../../components/icons";

const XtermView = lazy(() => import("../terminal/XtermView"));

const TILE_TERMINAL_HEIGHT = "h-[220px] md:h-[300px] xl:h-[340px]";

export function activityText(timestamp: number): string {
  if (!timestamp) return "N/A";
  return new Date(timestamp * 1000).toLocaleString("ja-JP", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

const STATE_DOT: Record<CommanderState, string> = {
  working: "bg-blue-500 animate-pulse",
  waiting: "bg-emerald-500",
  limited: "bg-amber-500",
  error: "bg-red-500",
  exited: "bg-zinc-400",
};

function stateOf(item: CommanderConsole): CommanderState {
  return item.commander_state ?? (item.alive ? "waiting" : "exited");
}

function AliveDot({ item }: { item: CommanderConsole }) {
  const state = stateOf(item);
  return <span aria-hidden title={STATE_LABEL[state]} className={`h-2.5 w-2.5 shrink-0 rounded-full ${STATE_DOT[state]}`} />;
}

/** 自動再開（上限で止まったら回復の時刻に「続けてください」）のトグル。全コンソールに出す（既定は入）。
 * 上限で止まっているときは次の予定の時刻も出す。押しても行の「開く」は動かさない。 */
function AutoResume({ item, cliName }: { item: CommanderConsole; cliName: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const show = useToasts((s) => s.show);
  const on = item.commander_autoresume !== "off";
  const limited = stateOf(item) === "limited";
  const at = Number(item.commander_resume_at || 0);
  const when = at ? new Date(at * 1000).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" }) : "";
  const toggle = async () => {
    try {
      await setConsoleAutoResume(item.id, !on);
      qc.invalidateQueries({ queryKey: ["commander-consoles"] });
      show(on ? "自動再開を切りました" : "自動再開を入れました");
    } catch (e) {
      show(e instanceof Error ? e.message : "切り替えられませんでした", "error");
    }
  };
  return (
    <span data-commander-autoresume={on ? "on" : "off"} className="mt-1 flex min-w-0 items-center gap-2 text-[11px] text-zinc-500 dark:text-zinc-400">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="自動再開"
        title="上限で止まったら、回復の時刻に「続けてください」を送ります"
        onClick={(e) => { e.stopPropagation(); void toggle(); }}
        className="flex min-h-9 shrink-0 items-center gap-1.5"
      >
        <span className={`relative h-5 w-9 rounded-full transition ${on ? "bg-accent-600" : "bg-zinc-300 dark:bg-zinc-700"}`}>
          <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
        </span>
        <span className="font-medium">自動再開</span>
      </button>
      {limited && on && (
        <span className="min-w-0 truncate">{when ? `予定 ${when}` : "回復の時刻を待っています"}</span>
      )}
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setEditing(true); }}
        aria-label="再開の文言"
        title={item.commander_resume_message ? `このコンソールの文言: ${item.commander_resume_message}` : "再開の文言（全体の設定を使用中）"}
        className={`ml-auto grid h-9 w-9 shrink-0 place-items-center rounded-full text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 ${item.commander_resume_message ? "" : "opacity-60"}`}
      >
        ✏️
      </button>
      {editing && <ConsoleResumeMessageSheet item={item} cliName={cliName} onClose={() => setEditing(false)} />}
    </span>
  );
}

/** ロックの切り替え。既定はロックなし。押すたびに切り替え、ロック中は閉じるを止める。
 * 状態は aria-pressed と鍵の絵（閉・開）で示す。 */
function LockButton({ item, cliName }: { item: CommanderConsole; cliName: string }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [busy, setBusy] = useState(false);
  const locked = item.commander_locked === true;
  const toggle = async (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      const result = await setConsoleLock(item.id, !locked);
      qc.invalidateQueries({ queryKey: ["commander-consoles"] });
      show(result.locked
        ? "ロックしました。ロック中のコンソールは閉じられません"
        : "ロックを外しました");
    } catch (error) {
      show(commanderErrorText(error, "ロックを切り替えられませんでした"), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      data-commander-lock={locked ? "on" : "off"}
      aria-pressed={locked}
      aria-label={locked ? `${cliName}のロック中（押すとロックを外す）` : `${cliName}のロックなし（押すとロックする）`}
      title={locked ? "ロック中。閉じるには先にロックを外してください" : "ロックすると、勝手に閉じられないようにします"}
      disabled={busy}
      onClick={(event) => void toggle(event)}
      className={`grid h-9 w-9 shrink-0 place-items-center rounded-full text-base disabled:opacity-40 ${
        locked
          ? "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
          : "text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
      }`}
    >
      {locked ? <IconLock className="h-4 w-4" /> : <IconLockOpen className="h-4 w-4" />}
    </button>
  );
}

/** 閉じるのボタン。ロック中は押せない（理由を読み上げ名と説明に出す）。 */
function CloseButton({ item, cliName, onClose }: { item: CommanderConsole; cliName: string; onClose: () => void }) {
  const locked = item.commander_locked === true;
  return (
    <button
      type="button"
      onClick={onClose}
      disabled={locked}
      aria-label={locked ? `${cliName}のコンソールはロック中で閉じられません` : `${cliName}のコンソールを閉じる`}
      title={locked ? "ロック中は閉じられません。先にロックを外してください" : "コンソールを閉じる"}
      className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-base text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent dark:text-red-400 dark:hover:bg-red-950/40"
    >
      🗑️
    </button>
  );
}

/** 状態の文字と最後の行（上限・エラーならその行）。 */
function StateLine({ item }: { item: CommanderConsole }) {
  const state = stateOf(item);
  const text = (state === "limited" || state === "error") && item.commander_detail ? item.commander_detail : item.commander_last_line;
  return (
    <span data-commander-state={state} className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px]">
      <span className={`shrink-0 font-semibold ${
        state === "limited" ? "text-amber-700 dark:text-amber-400"
          : state === "error" ? "text-red-600 dark:text-red-400"
            : state === "working" ? "text-blue-600 dark:text-blue-400"
              : "text-zinc-500 dark:text-zinc-400"
      }`}>{STATE_LABEL[state]}</span>
      {text && <span className="min-w-0 truncate font-mono text-zinc-500 dark:text-zinc-400" title={text}>{text}</span>}
    </span>
  );
}

/** 引き継ぎ先が無くて止まったタスクの担当のコンソールに出す（押すと引き継ぎ先を選べる）。 */
export interface StuckTask { teamId: string; taskId: number; title: string }

function HandoffButton({ stuck, onChoose }: { stuck: StuckTask; onChoose: () => void }) {
  return (
    <button
      type="button" data-console-handoff onClick={(e) => { e.stopPropagation(); onChoose(); }}
      className="mt-1 min-h-11 w-full min-w-0 rounded-xl bg-red-50 px-3 text-left text-xs font-semibold text-red-700 dark:bg-red-950/40 dark:text-red-300"
    >
      <span className="block truncate">上限で止まり、引き継ぎ先がありません（#{stuck.taskId} {stuck.title}）</span>
      <span className="block underline">引き継ぎ先を選ぶ</span>
    </button>
  );
}

export function ConsoleTile({
  item,
  cliName,
  onExpand,
  onClose,
  stuck,
  onChooseHandoff,
}: {
  item: CommanderConsole;
  cliName: string;
  onExpand: () => void;
  onClose: () => void;
  stuck?: StuckTask | null;
  onChooseHandoff?: () => void;
}) {
  return (
    <li
      data-commander-tile
      className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex min-w-0 shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <AliveDot item={item} />
        <button type="button" onClick={onExpand} className="min-w-0 flex-1 text-left" aria-label={`${cliName}のコンソールを大きく開く`}>
          <span className="flex min-w-0 items-center gap-1.5">
            <strong className="min-w-0 truncate text-sm">{cliName}</strong>
            <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
              {accountTitle(item.commander_account, item.commander_account_label, false, undefined, item.commander_login_name)}
            </span>
            {item.commander_label && (
              <span className="min-w-0 truncate text-xs text-zinc-500 dark:text-zinc-400" title={item.commander_label}>
                {item.commander_label}
              </span>
            )}
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-zinc-500 dark:text-zinc-400" title={item.cwd}>
            {item.commander_workdir_name || item.cwd || "N/A"} · 最終活動 {activityText(item.activity_at || item.created_at)}
          </span>
          <StateLine item={item} />
          <UsageLine usage={item.commander_usage} className="mt-0.5" />
        </button>
        <button
          type="button"
          onClick={onExpand}
          aria-label="大きく開く"
          title="大きく開く"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          ⤢
        </button>
        <LockButton item={item} cliName={cliName} />
        <CloseButton item={item} cliName={cliName} onClose={onClose} />
      </div>
      <div className="shrink-0 border-b border-zinc-200 px-3 pb-1 dark:border-zinc-800">
        <AutoResume item={item} cliName={cliName} />
        {stuck && onChooseHandoff && <HandoffButton stuck={stuck} onChoose={onChooseHandoff} />}
      </div>
      <div className={`relative ${TILE_TERMINAL_HEIGHT}`}>
        <Suspense fallback={<div className="grid h-full place-items-center text-xs text-zinc-400">ターミナルを読み込み中...</div>}>
          <XtermView
            embedded
            sessionId={item.id}
            sessions={[item]}
            onSwitch={() => onExpand()}
            onExit={onExpand}
          />
        </Suspense>
      </div>
    </li>
  );
}

/** モバイル（〜767px）の 1 行。押すと全画面で開く（1 つずつ切り替え）。 */
export function ConsoleRow({
  item,
  cliName,
  onOpen,
  onClose,
  stuck,
  onChooseHandoff,
}: {
  item: CommanderConsole;
  cliName: string;
  onOpen: () => void;
  onClose: () => void;
  stuck?: StuckTask | null;
  onChooseHandoff?: () => void;
}) {
  return (
    <li
      data-commander-row
      className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-2.5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex min-w-0 items-center gap-2">
      <AliveDot item={item} />
      <button type="button" onClick={onOpen} className="min-h-11 min-w-0 flex-1 text-left">
        <span className="flex min-w-0 items-center gap-1.5">
          <strong className="min-w-0 truncate text-sm">{cliName}</strong>
          <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {accountTitle(item.commander_account, item.commander_account_label, false, undefined, item.commander_login_name)}
          </span>
          {item.commander_label && <span className="min-w-0 truncate text-xs text-zinc-500 dark:text-zinc-400">{item.commander_label}</span>}
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-zinc-500 dark:text-zinc-400" title={item.cwd}>
          {item.commander_workdir_name || item.cwd || "N/A"} · 最終活動 {activityText(item.activity_at || item.created_at)}
        </span>
        <StateLine item={item} />
          <UsageLine usage={item.commander_usage} className="mt-0.5" />
      </button>
      <button
        type="button"
        onClick={onOpen}
        data-commander-connect
        className="min-h-11 shrink-0 rounded-full bg-accent-50 px-3 text-xs font-semibold text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
      >
        開く
      </button>
      <LockButton item={item} cliName={cliName} />
      <CloseButton item={item} cliName={cliName} onClose={onClose} />
      </div>
      <div className="pl-4">
        <AutoResume item={item} cliName={cliName} />
        {stuck && onChooseHandoff && <HandoffButton stuck={stuck} onChoose={onChooseHandoff} />}
      </div>
    </li>
  );
}
