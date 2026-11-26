/** AI Commander のタイル: コンソール 1 個分。
 *
 * 頭に CLI の名前・アカウント・表示名・生きているか・最終活動を出し、
 * 中身は既存の XtermView を埋め込み表示（embedded）で収める。
 * 頭を押すと全画面で大きく開く。 */
import { lazy, Suspense } from "react";
import type { CommanderConsole } from "../../api/commander";

const XtermView = lazy(() => import("../terminal/XtermView"));

const TILE_TERMINAL_HEIGHT = "h-[300px] xl:h-[340px]";

export function activityText(timestamp: number): string {
  if (!timestamp) return "N/A";
  return new Date(timestamp * 1000).toLocaleString("ja-JP", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function AliveDot({ item }: { item: CommanderConsole }) {
  const running = item.alive && item.workload === "running";
  return (
    <span
      aria-hidden
      title={item.alive ? (running ? "実行中" : "入力待ち") : "終了"}
      className={`h-2.5 w-2.5 shrink-0 rounded-full ${!item.alive ? "bg-red-500" : running ? "bg-blue-500" : "bg-emerald-500"}`}
    />
  );
}

export function ConsoleTile({
  item,
  cliName,
  onExpand,
  onClose,
}: {
  item: CommanderConsole;
  cliName: string;
  onExpand: () => void;
  onClose: () => void;
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
              {item.commander_account === "default" ? "既定" : item.commander_account}
            </span>
            {item.commander_label && (
              <span className="min-w-0 truncate text-xs text-zinc-500 dark:text-zinc-400" title={item.commander_label}>
                {item.commander_label}
              </span>
            )}
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-zinc-500 dark:text-zinc-400" title={item.cwd}>
            {item.cwd || "N/A"} · 最終活動 {activityText(item.activity_at || item.created_at)}
          </span>
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
        <button
          type="button"
          onClick={onClose}
          aria-label={`${cliName}のコンソールを閉じる`}
          title="コンソールを閉じる"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-base text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
        >
          🗑️
        </button>
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
}: {
  item: CommanderConsole;
  cliName: string;
  onOpen: () => void;
  onClose: () => void;
}) {
  return (
    <li
      data-commander-row
      className="flex min-w-0 items-center gap-2 rounded-2xl border border-zinc-200 bg-white p-2.5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <AliveDot item={item} />
      <button type="button" onClick={onOpen} className="min-h-11 min-w-0 flex-1 text-left">
        <span className="flex min-w-0 items-center gap-1.5">
          <strong className="min-w-0 truncate text-sm">{cliName}</strong>
          <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {item.commander_account === "default" ? "既定" : item.commander_account}
          </span>
          {item.commander_label && <span className="min-w-0 truncate text-xs text-zinc-500 dark:text-zinc-400">{item.commander_label}</span>}
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-zinc-500 dark:text-zinc-400" title={item.cwd}>
          {item.cwd || "N/A"} · 最終活動 {activityText(item.activity_at || item.created_at)}
        </span>
      </button>
      <button
        type="button"
        onClick={onOpen}
        data-commander-connect
        className="min-h-11 shrink-0 rounded-full bg-accent-50 px-3 text-xs font-semibold text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
      >
        開く
      </button>
      <button
        type="button"
        onClick={onClose}
        aria-label={`${cliName}のコンソールを閉じる`}
        title="コンソールを閉じる"
        className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-base text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
      >
        🗑️
      </button>
    </li>
  );
}
