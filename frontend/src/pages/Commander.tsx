/** Nexarch（旧 AI Commander）: AI の CLI のコンソールを 1 つの画面で何個でも開く。
 *
 * 表示は 2 つから選ぶ（頭のアイコンの切り替え）:
 * - 一覧: 1 行ずつ。押すと全画面で開く。
 * - タイル: 端末を埋め込んで中身が少し見える。PC は 2〜3 列、モバイルは 1 列。
 * 既定は PC がタイル、モバイルが一覧。選んだ形はこの端末に覚える。
 * 主操作は「＋ コンソール」1 個、アカウントの管理はその他メニュー（UI ルール: 全ボタンを並べない）。 */
import { lazy, Suspense, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { closeConsole, fetchClis, fetchConsoles, type CommanderConsole } from "../api/commander";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { ConfirmDialog, EmptyState, IconButton, Skeleton, DropdownMenu } from "../components/ui";
import { IconDots, IconListRows, IconPlus, IconTilesPreview } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { ConsoleRow, ConsoleTile } from "../features/commander/ConsoleTile";
import { NewConsoleSheet } from "../features/commander/NewConsoleSheet";
import { AccountsSheet } from "../features/commander/AccountsSheet";
import { GlobalResumeMessageSheet } from "../features/commander/ResumeMessageSheet";
import { useToasts } from "../stores";

const XtermView = lazy(() => import("../features/terminal/XtermView"));

type CommanderView = "list" | "tiles";
const VIEW_KEY = "control-deck:commander-view";

function readView(): CommanderView | null {
  try {
    const value = localStorage.getItem(VIEW_KEY);
    return value === "list" || value === "tiles" ? value : null;
  } catch {
    return null;
  }
}

/** 一覧とタイルの切り替え。1 個のアイコンのボタンで、押すと替わる先の形の絵を出す（文字は出さない。読み上げ名は付ける）。 */
function ViewToggle({ value, onChange }: { value: CommanderView; onChange: (value: CommanderView) => void }) {
  const next: CommanderView = value === "list" ? "tiles" : "list";
  const label = next === "list" ? "一覧で表示" : "中身を見ながら表示";
  return (
    <button
      type="button"
      data-commander-view-toggle
      data-view={value}
      aria-label={label}
      title={label}
      onClick={() => onChange(next)}
      className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-xl text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
    >
      {next === "list" ? <IconListRows /> : <IconTilesPreview />}
    </button>
  );
}

export default function CommanderPage() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const isDesktop = useMediaQuery("(min-width: 768px)");
  const [newSheet, setNewSheet] = useState(false);
  const [accountsSheet, setAccountsSheet] = useState(false);
  const [resumeSheet, setResumeSheet] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [closing, setClosing] = useState<CommanderConsole | null>(null);
  // 選んでいなければ PC はタイル、モバイルは一覧。
  const [chosenView, setChosenView] = useState<CommanderView | null>(readView);
  const view: CommanderView = chosenView ?? (isDesktop ? "tiles" : "list");
  const chooseView = (value: CommanderView) => {
    setChosenView(value);
    try {
      localStorage.setItem(VIEW_KEY, value);
    } catch {
      // 保存できない端末（プライベートなど）は、この画面を開いている間だけ覚える
    }
  };

  const { data: consoles, isLoading } = useQuery({
    queryKey: ["commander-consoles"],
    queryFn: fetchConsoles,
    refetchInterval: expanded ? 5_000 : 10_000,
  });
  const { data: clis = [] } = useQuery({
    queryKey: ["commander-clis"],
    queryFn: fetchClis,
  });
  const cliName = (id: string) => clis.find((c) => c.id === id)?.name ?? id;

  const invalidate = () => qc.invalidateQueries({ queryKey: ["commander-consoles"] });

  const kill = async (item: CommanderConsole) => {
    try {
      await closeConsole(item.id);
      invalidate();
      if (expanded === item.id) setExpanded(null);
      show("コンソールを閉じました");
    } catch (e) {
      show(e instanceof Error ? e.message : "閉じられませんでした", "error");
    }
    setClosing(null);
  };

  // 全画面表示（タイルを大きく開く・モバイルで 1 つ選ぶ）
  if (expanded) {
    const items = consoles ?? [];
    return (
      <Suspense fallback={<div className="grid h-full place-items-center text-sm text-zinc-400">ターミナルを読み込み中...</div>}>
        <XtermView
          sessionId={expanded}
          sessions={items}
          onSwitch={setExpanded}
          onExit={() => {
            setExpanded(null);
            invalidate();
          }}
        />
      </Suspense>
    );
  }

  const items = consoles ?? [];
  return (
    <div className="mx-auto max-w-7xl p-4 md:p-6">
      <PageHeader title="Nexarch" actions={<>
        <DropdownMenu
          ariaLabel="その他の操作"
          trigger={<IconDots />}
          items={[
            { label: "アカウントの管理", onSelect: () => setAccountsSheet(true) },
            { label: "自動再開の文言（全体）", onSelect: () => setResumeSheet(true) },
          ]}
        />
        <IconButton variant="primary" label="＋ コンソール" onClick={() => setNewSheet(true)}>
          <IconPlus />
        </IconButton>
      </>} />

      <p className="mb-1 text-[11px] font-semibold tracking-[0.18em] text-accent-600 dark:text-accent-400">AI COMMAND SYSTEM</p>
      <p className="mb-2 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        Claude Code・Codex・OpenCode などのコンソールをアカウントごとに開いて、1 つの画面で並べます。
      </p>
      {items.length > 0 && (
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-xs tabular-nums text-zinc-500 dark:text-zinc-400">{items.length} 個</span>
          <ViewToggle value={view} onChange={chooseView} />
        </div>
      )}

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : items.length === 0 ? (
        <EmptyState actions={
          <button onClick={() => setNewSheet(true)} className="rounded-xl bg-accent-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-700">コンソールを開く</button>
        }>
          コンソールはありません
        </EmptyState>
      ) : view === "tiles" ? (
        <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <ConsoleTile
              key={item.id}
              item={item}
              cliName={cliName(item.commander_cli)}
              onExpand={() => setExpanded(item.id)}
              onClose={() => setClosing(item)}
            />
          ))}
        </ul>
      ) : (
        <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <ConsoleRow
              key={item.id}
              item={item}
              cliName={cliName(item.commander_cli)}
              onOpen={() => setExpanded(item.id)}
              onClose={() => setClosing(item)}
            />
          ))}
        </ul>
      )}

      {closing && (
        <ConfirmDialog
          title={`${cliName(closing.commander_cli)} のコンソールを閉じますか？`}
          message="実行中の CLI は終了します。この操作は取り消せません。"
          confirmLabel="閉じる"
          onConfirm={() => void kill(closing)}
          onClose={() => setClosing(null)}
        />
      )}
      {newSheet && <NewConsoleSheet clis={clis} onClose={() => setNewSheet(false)} />}
      {accountsSheet && <AccountsSheet clis={clis} onClose={() => setAccountsSheet(false)} />}
      {resumeSheet && <GlobalResumeMessageSheet onClose={() => setResumeSheet(false)} />}
    </div>
  );
}
