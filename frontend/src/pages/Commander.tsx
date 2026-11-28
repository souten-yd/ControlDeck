/** Nexarch（旧 AI Commander）: AI の CLI のコンソールを 1 つの画面で何個でも開く。
 *
 * 表示は 2 つから選ぶ（頭のアイコンの切り替え）:
 * - 一覧: 1 行ずつ。押すと全画面で開く。
 * - タイル: 端末を埋め込んで中身が少し見える。PC は 2〜3 列、モバイルは 1 列。
 * 既定は PC がタイル、モバイルが一覧。選んだ形はこの端末に覚える。
 * 主操作は「＋ コンソール」1 個、アカウントの管理はその他メニュー（UI ルール: 全ボタンを並べない）。 */
import { lazy, Suspense, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { closeAllConsoles, closeConsole, commanderErrorText, fetchClis, fetchConsoles, fetchTeams, type CommanderConsole } from "../api/commander";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { ConfirmDialog, EmptyState, IconButton, SegmentedTabs, Skeleton, DropdownMenu, type MenuItem } from "../components/ui";
import { IconDots, IconListRows, IconPlus, IconTilesPreview } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { ConsoleRow, ConsoleTile } from "../features/commander/ConsoleTile";
import { TeamView } from "../features/commander/TeamView";
import { NewConsoleSheet } from "../features/commander/NewConsoleSheet";
import { AccountsSheet } from "../features/commander/AccountsSheet";
import { AutoRestoreSheet } from "../features/commander/AutoRestoreSheet";
import { GlobalResumeMessageSheet } from "../features/commander/ResumeMessageSheet";
import { ExpertSwitch, HandoffSheet, TeamDefaultsSheet, useExpert } from "../features/commander/TeamRoles";
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
  const [searchParams, setSearchParams] = useSearchParams();
  const section: "consoles" | "team" = searchParams.get("view") === "team" ? "team" : "consoles";
  const chooseSection = (value: "consoles" | "team") => {
    const next = new URLSearchParams(searchParams);
    if (value === "team") next.set("view", "team"); else next.delete("view");
    setSearchParams(next, { replace: true });
  };
  const [newSheet, setNewSheet] = useState(false);
  const [accountsSheet, setAccountsSheet] = useState(false);
  const [resumeSheet, setResumeSheet] = useState(false);
  const [restoreSheet, setRestoreSheet] = useState(false);
  const [bulkClosing, setBulkClosing] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [defaultsSheet, setDefaultsSheet] = useState(false);
  const [handoffFor, setHandoffFor] = useState<{ teamId: string; taskId: number } | null>(null);
  const expert = useExpert();
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
  // 引き継ぎ先が無くて止まったタスクの担当コンソール（そのコンソールのカードに「引き継ぎ先を選ぶ」を出す）
  const { data: teamList } = useQuery({ queryKey: ["commander-teams"], queryFn: fetchTeams, refetchInterval: 15_000 });
  const stuckOf = (sessionId: string) => {
    for (const t of teamList ?? []) {
      const task = t.tasks.find((x) => x.blocked_reason === "no_handoff" && x.assignee === sessionId);
      if (task) return { teamId: t.id, taskId: task.id, title: task.title };
    }
    return null;
  };
  const handoffTeam = teamList?.find((t) => t.id === handoffFor?.teamId);
  const handoffTask = handoffTeam?.tasks.find((t) => t.id === handoffFor?.taskId);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["commander-consoles"] });

  const kill = async (item: CommanderConsole) => {
    if (item.commander_locked) {
      show("ロック中です。先にロックを外してください", "info");
      setClosing(null);
      return;
    }
    try {
      await closeConsole(item.id);
      invalidate();
      if (expanded === item.id) setExpanded(null);
      show("コンソールを閉じました");
    } catch (e) {
      show(commanderErrorText(e, "閉じられませんでした"), "error");
    }
    setClosing(null);
  };

  const closeAll = async () => {
    setBulkBusy(true);
    try {
      const result = await closeAllConsoles();
      invalidate();
      const skipped = result.skipped_locked > 0
        ? `（ロック中の ${result.skipped_locked} 個は閉じていません。ロックを外してからもう一度閉じてください）`
        : "";
      show(`${result.closed} 個のコンソールを閉じました${skipped}`, result.skipped_locked > 0 ? "info" : "success");
      setBulkClosing(false);
    } catch (e) {
      show(commanderErrorText(e, "閉じられませんでした"), "error");
    } finally {
      setBulkBusy(false);
    }
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
  const lockedCount = items.filter((item) => item.commander_locked).length;
  const menuItems: MenuItem[] = [
    { label: "アカウントの管理", onSelect: () => setAccountsSheet(true) },
    { label: "自動再開の文言（全体）", onSelect: () => setResumeSheet(true) },
    { label: "チームの既定（チームリーダー・保存先）", onSelect: () => setDefaultsSheet(true) },
  ];
  // 細かい全体設定はエキスパートのときだけ出す（既定は隠す）。
  if (expert) menuItems.push({ label: "自動で戻す（全体）", onSelect: () => setRestoreSheet(true) });
  // 一括で閉じる。ロック中は飛ばし、その数を確認と結果に出す。
  if (section === "consoles" && items.length > 0) {
    menuItems.push({ label: "すべてのコンソールを閉じる", danger: true, separated: true, onSelect: () => setBulkClosing(true) });
  }
  return (
    <div className="mx-auto max-w-7xl p-4 md:p-6">
      <PageHeader title="Nexarch" actions={<>
        <DropdownMenu
          ariaLabel="その他の操作"
          trigger={<IconDots />}
          items={menuItems}
        />
        {section === "consoles" && (
          <IconButton variant="primary" label="＋ コンソール" onClick={() => setNewSheet(true)}>
            <IconPlus />
          </IconButton>
        )}
      </>} />

      <p className="mb-1 text-[11px] font-semibold tracking-[0.18em] text-accent-600 dark:text-accent-400">AI COMMAND SYSTEM</p>
      <p className="mb-2 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        Claude Code・Codex・OpenCode などのコンソールをアカウントごとに開いて、1 つの画面で並べます。
      </p>
      <div className="mb-3 flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
        <SegmentedTabs
          tabs={[["consoles", "コンソール"], ["team", "チーム"]] as const}
          value={section}
          onChange={chooseSection}
          className="max-w-xs"
        />
        {section === "team" && <ExpertSwitch />}
      </div>
      {section === "team" ? (
        <TeamView consoles={items} cliName={cliName} onOpenConsole={setExpanded} />
      ) : (<>
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
              stuck={stuckOf(item.id)}
              onChooseHandoff={() => { const s = stuckOf(item.id); if (s) setHandoffFor({ teamId: s.teamId, taskId: s.taskId }); }}
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
              stuck={stuckOf(item.id)}
              onChooseHandoff={() => { const s = stuckOf(item.id); if (s) setHandoffFor({ teamId: s.teamId, taskId: s.taskId }); }}
            />
          ))}
        </ul>
      )}
      </>)}

      {closing && (
        <ConfirmDialog
          title={`${cliName(closing.commander_cli)} のコンソールを閉じますか？`}
          message="実行中の CLI は終了します。この操作は取り消せません。"
          confirmLabel="閉じる"
          onConfirm={() => void kill(closing)}
          onClose={() => setClosing(null)}
        />
      )}
      {bulkClosing && (
        <ConfirmDialog
          title="すべてのコンソールを閉じますか？"
          message={items.length - lockedCount === 0
            ? `すべてのコンソール（${lockedCount} 個）がロック中です。閉じるには、先にロックを外してください。`
            : lockedCount > 0
              ? `ロック中の ${lockedCount} 個は閉じずに残します。残りの ${items.length - lockedCount} 個を閉じます。ロック中のものは、ロックを外してから閉じてください。`
              : `${items.length} 個のコンソールを閉じます。実行中の CLI は終了します。この操作は取り消せません。`}
          confirmLabel="閉じる"
          busy={bulkBusy}
          disabled={lockedCount > 0 && items.length - lockedCount === 0}
          onConfirm={() => void closeAll()}
          onClose={() => setBulkClosing(false)}
        />
      )}
      {newSheet && <NewConsoleSheet clis={clis} onClose={() => setNewSheet(false)} />}
      {accountsSheet && <AccountsSheet clis={clis} onClose={() => setAccountsSheet(false)} />}
      {resumeSheet && <GlobalResumeMessageSheet onClose={() => setResumeSheet(false)} />}
      {restoreSheet && <AutoRestoreSheet onClose={() => setRestoreSheet(false)} />}
      {defaultsSheet && <TeamDefaultsSheet expert={expert} onClose={() => setDefaultsSheet(false)} />}
      {handoffFor && handoffTeam && handoffTask && (
        <HandoffSheet
          teamId={handoffTeam.id} task={handoffTask} expert={expert}
          roleName={handoffTeam.roles?.find((r) => r.id === handoffTask.role)?.name}
          onClose={() => setHandoffFor(null)}
          onDone={() => { qc.invalidateQueries({ queryKey: ["commander-teams"] }); invalidate(); }}
        />
      )}
    </div>
  );
}
