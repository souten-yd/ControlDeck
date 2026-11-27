/** Nexarch の AI チーム（T1）: メンバー（役割つきのコンソール）とタスク板。
 *
 * 状態の正本は板（ControlDeck 側）。担当へは「担当へ送る」で短い文を送り、終わったら
 * 「報告を取り込む」で画面の「完了報告:」を読む（LLM なし）。主操作は「＋ タスク」の 1 つ、
 * メンバー・チームの操作はその他メニューへ。モバイル（〜767px）は列を縦に並べる。
 *
 * T3b: 目標からチームを作る・役割（自動の振り分け）・チームリーダーの任命・引き継ぎ先を選ぶ。
 * 「エキスパート」が切（既定）のときは、毎回使うもの（目標・板・引き継ぎ先を選ぶ・通知）と、危険・要対応（止まった・上限・
 * 引き継ぎ先なし）だけを出す。細かい札・設定・履歴・使用量は入にしたときだけ。 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  collectTask, createTask, createTeam, deleteTask, deleteTeam, dispatchTask, fetchAccounts, fetchTeams, memberKey, putTaskReport,
  ROLE_LABEL, setTeamMembers, setTeamNotify, STATE_LABEL, stopTask, TEAM_ROLES, updateTask, updateTeam,
  type CommanderConsole, type CommanderState, type RunUsage, type TaskStatus, type Team, type TeamMember, type TeamRole, type TeamTask,
} from "../../api/commander";
import {
  BottomSheet, ConfirmDialog, DropdownMenu, EmptyState, IconButton, primaryButtonClass, secondaryButtonClass, Skeleton,
} from "../../components/ui";
import { IconDots, IconPlus } from "../../components/icons";
import { useToasts } from "../../stores";
import { UsageLine } from "./UsageLine";
import {
  AutoTeamSheet, endpointText, HandoffSheet, LeaderSheet, RolesPanel, RolesSheet, useExpert,
} from "./TeamRoles";
import { LeaderAsk, LeaderTimeline } from "./LeaderTimeline";

const inputClass = "min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const textareaClass = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const fieldLabel = "mb-1 mt-3 block text-xs font-medium text-zinc-600 dark:text-zinc-300";
const SELECTED_KEY = "control-deck:commander-team";

const COLUMNS: ReadonlyArray<{ id: "todo" | "doing" | "review" | "done"; label: string }> = [
  { id: "todo", label: "待ち" }, { id: "doing", label: "作業中" }, { id: "review", label: "レビュー待ち" }, { id: "done", label: "完了" },
];
const STATUS_LABEL: Record<TaskStatus, string> = {
  todo: "待ち", doing: "作業中", review: "レビュー待ち", done: "完了", rejected: "差し戻し", blocked: "自動で直せず",
};
const CHECK_LABEL = { running: "実行中", passed: "合格", failed: "不合格", error: "実行できず" } as const;
const CHECK_CLASS = {
  running: "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300",
  passed: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  error: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
} as const;
const BLOCKED_CLASS = "rounded-full bg-red-50 px-2 py-0.5 font-semibold text-red-700 dark:bg-red-950/40 dark:text-red-300";
const UNVERIFIED_CLASS = "rounded-full bg-orange-50 px-2 py-0.5 font-semibold text-orange-700 dark:bg-orange-950/40 dark:text-orange-300";
const WARN_CLASS = "rounded-full bg-amber-50 px-2 py-0.5 font-semibold text-amber-800 dark:bg-amber-950/40 dark:text-amber-300";
const STATE_TEXT: Record<CommanderState | "closed", string> = { ...STATE_LABEL, closed: "コンソールが閉じています" };
const STATE_COLOR: Record<CommanderState | "closed", string> = {
  working: "text-blue-600 dark:text-blue-400", waiting: "text-emerald-600 dark:text-emerald-400",
  limited: "text-amber-700 dark:text-amber-400", error: "text-red-600 dark:text-red-400",
  exited: "text-zinc-500", closed: "text-red-600 dark:text-red-400",
};
const STATE_DOT: Record<CommanderState, string> = {
  working: "bg-blue-500 animate-pulse", waiting: "bg-emerald-500", limited: "bg-amber-500", error: "bg-red-500", exited: "bg-zinc-400",
};

/** エキスパートが切のときの、タスクの札 1 つ（危険・要対応を優先して 1 つにまとめる）。 */
function simpleBadge(task: TeamTask): { text: string; tone: "red" | "amber" | "blue" } | null {
  if (task.blocked_reason === "no_handoff") return { text: "引き継ぎ先なし", tone: "red" };
  if (task.status === "blocked") return { text: "止まった", tone: "red" };
  if (task.status === "doing" && (task.limited_at || task.run?.status === "limited" || task.wait_until)) return { text: "上限で停止", tone: "amber" };
  if (task.status === "doing" && task.stalled_at) return { text: "止まった", tone: "amber" };
  if (task.status === "doing") return { text: "作業中", tone: "blue" };
  return null;
}
function SimpleBadge({ task }: { task: TeamTask }) {
  const badge = simpleBadge(task);
  return badge ? <span data-team-simple-badge={badge.text} className={TONE_CLASS[badge.tone]}>{badge.text}</span> : null;
}
const TONE_CLASS = {
  red: BLOCKED_CLASS, amber: WARN_CLASS,
  blue: "rounded-full bg-blue-50 px-2 py-0.5 font-semibold text-blue-700 dark:bg-blue-950/40 dark:text-blue-300",
} as const;

const HEADLESS_CLIS: ReadonlyArray<readonly [string, string]> = [["claude", "Claude Code"], ["codex", "Codex"], ["opencode", "OpenCode"]];
const RUN_RESULT_LABEL: Record<string, string> = {
  running: "実行中", sent: "送信", ok: "完了", reported: "報告あり", failed: "失敗", limited: "上限で停止", stopped: "止めた",
  interrupted: "中断", replaced: "入れ替え", write_failed: "書き込みがすべて失敗",
};

function fmtSeconds(value: number | null | undefined): string {
  if (value == null) return "";
  const s = Math.max(0, Math.round(value));
  return s >= 60 ? `${Math.floor(s / 60)}分${s % 60}秒` : `${s}秒`;
}
function fmtUsage(u: RunUsage | null | undefined): string {
  if (!u) return "";
  const tokens = u.input_tokens + u.output_tokens;
  const parts = [] as string[];
  if (tokens) parts.push(`${tokens.toLocaleString()} トークン`);
  if (u.cost_usd) parts.push(`$${u.cost_usd.toFixed(2)}`);
  return parts.join("・");
}
function fmtClock(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
const isHeadless = (m: TeamMember | undefined) => m?.kind === "headless";

function stateOf(item: CommanderConsole): CommanderState {
  return item.commander_state ?? (item.alive ? "waiting" : "exited");
}

export function TeamView({
  consoles, cliName, onOpenConsole,
}: {
  consoles: CommanderConsole[];
  cliName: (cliId: string) => string;
  onOpenConsole: (id: string) => void;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data: teams, isLoading } = useQuery({ queryKey: ["commander-teams"], queryFn: fetchTeams, refetchInterval: 10_000 });
  const [selected, setSelected] = useState<string>(() => {
    try { return localStorage.getItem(SELECTED_KEY) ?? ""; } catch { return ""; }
  });
  const [membersSheet, setMembersSheet] = useState(false);
  const [briefSheet, setBriefSheet] = useState(false);
  const [fallbackSheet, setFallbackSheet] = useState(false);
  const [parallelSheet, setParallelSheet] = useState(false);
  const [taskSheet, setTaskSheet] = useState(false);
  const [newTeamSheet, setNewTeamSheet] = useState(false);
  const [autoSheet, setAutoSheet] = useState(false);
  const [rolesSheet, setRolesSheet] = useState(false);
  const [leaderSheet, setLeaderSheet] = useState(false);
  const [handoffTask, setHandoffTask] = useState<number | null>(null);
  const expert = useExpert();
  const [openTask, setOpenTask] = useState<number | null>(null);
  const [deletingTeam, setDeletingTeam] = useState(false);

  const list = teams ?? [];
  const team: Team | undefined = list.find((t) => t.id === selected) ?? list[0];
  useEffect(() => {
    try { if (team) localStorage.setItem(SELECTED_KEY, team.id); } catch { /* 保存できない端末は覚えない */ }
  }, [team]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["commander-teams"] });
  const consoleOf = (sid: string) => consoles.find((c) => c.id === sid);
  const headlessName = (m: TeamMember) => `${cliName(m.cli ?? "")}${m.label ? `・${m.label}` : ""}`;
  const consoleName = (sid: string) => {
    const member = team?.members.find((m) => memberKey(m) === sid);
    if (member && isHeadless(member)) return headlessName(member);
    const item = consoleOf(sid);
    if (!item) return "閉じたコンソール";
    return `${cliName(item.commander_cli)}${item.commander_label ? `・${item.commander_label}` : ""}`;
  };
  const roleOf = (t: Team, sid: string) => t.members.find((m) => memberKey(m) === sid)?.role;

  if (isLoading) return <Skeleton className="h-24" />;

  if (!team) {
    return (
      <>
        <LeaderAsk onAsked={(id) => { setSelected(id); refresh(); }} />
        <EmptyState actions={<>
          {expert && <button onClick={() => setAutoSheet(true)} className={secondaryButtonClass}>作業場所を選んでチームを作る</button>}
          {expert && <button onClick={() => setNewTeamSheet(true)} className={secondaryButtonClass}>空のチームを作る</button>}
        </>}>
          チームはありません。上の欄に一文で頼むだけで、チームリーダー（調べ・計画し・担い手へ振り・評価・統合・試験する AI）が進めます。
        </EmptyState>
        {newTeamSheet && <NewTeamSheet onClose={() => setNewTeamSheet(false)} onCreated={(id) => { setSelected(id); refresh(); }} />}
        {autoSheet && <AutoTeamSheet expert={expert} onClose={() => setAutoSheet(false)} onCreated={(id) => { setSelected(id); refresh(); }} />}
      </>
    );
  }

  const removeTeam = async () => {
    try {
      await deleteTeam(team.id);
      refresh();
      show("チームを削除しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "削除できませんでした", "error");
    }
    setDeletingTeam(false);
  };
  const toggleNotify = async () => {
    const next = team.notify === false;
    try {
      await setTeamNotify(team.id, next);
      refresh();
      show(next ? "通知をオンにしました" : "通知をオフにしました");
    } catch (e) {
      show(e instanceof Error ? e.message : "切り替えられませんでした", "error");
    }
  };
  const toggleReset = async () => {
    const next = team.reset_before_dispatch === false;
    try {
      await updateTeam(team.id, { reset_before_dispatch: next });
      refresh();
      show(next ? "送る前に会話をリセットします" : "送る前の会話のリセットをやめました");
    } catch (e) {
      show(e instanceof Error ? e.message : "切り替えられませんでした", "error");
    }
  };
  const toggleOnLimit = async () => {
    const next = team.on_limit === "wait" ? "handoff" : "wait";
    try {
      await updateTeam(team.id, { on_limit: next });
      refresh();
      show(next === "handoff" ? "上限で止まったら別の担い手へ引き継ぎます" : "上限で止まったら回復を待ちます");
    } catch (e) {
      show(e instanceof Error ? e.message : "切り替えられませんでした", "error");
    }
  };
  const toggleSandbox = async () => {
    const next = team.codex_without_sandbox === false;
    try {
      await updateTeam(team.id, { codex_without_sandbox: next });
      refresh();
      show(next ? "Codex のサンドボックスが使えなければ、サンドボックスなしで動かします" : "Codex のサンドボックスが使えなければ止めます");
    } catch (e) {
      show(e instanceof Error ? e.message : "切り替えられませんでした", "error");
    }
  };
  const toggleField = async (fields: Parameters<typeof updateTeam>[1], message: string) => {
    try {
      await updateTeam(team.id, fields);
      refresh();
      show(message);
    } catch (e) {
      show(e instanceof Error ? e.message : "切り替えられませんでした", "error");
    }
  };
  const current = team.tasks.find((t) => t.id === openTask);

  const leaderDriven = Boolean(team.leader_run || team.plan);
  const showBoard = expert || !leaderDriven;  // チームリーダーが動かすチームの板・メンバー・役割はエキスパートだけ（普段は時系列）

  return (
    <div data-team-view>
      <LeaderAsk team={team} onAsked={(id) => { setSelected(id); refresh(); }} />
      <div className="mb-3 flex min-w-0 items-center gap-2">
        {list.length > 1 ? (
          <select
            aria-label="チームを選ぶ"
            value={team.id}
            onChange={(e) => setSelected(e.target.value)}
            className={`${inputClass} min-w-0 flex-1 font-semibold`}
          >
            {list.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        ) : (
          <h2 className="min-w-0 flex-1 truncate text-base font-semibold" data-team-name>{team.name}</h2>
        )}
        <DropdownMenu
          ariaLabel="チームの操作"
          trigger={<IconDots />}
          items={[
            ...(expert ? [{ label: "メンバーを選ぶ", onSelect: () => setMembersSheet(true) }] : []),
            {
              label: team.notify === false ? "通知: オフ（オンにする）" : "通知: オン（オフにする）",
              onSelect: () => void toggleNotify(),
            },
            { label: "チームの前提", onSelect: () => setBriefSheet(true) },
            ...(expert ? [
              {
                label: team.autonomy === "ask" ? "自律: 聞いてから進める（聞かずに進めるにする）" : "自律: 聞かずに最善で進める（聞いてから進めるにする）",
                onSelect: () => void toggleField({ autonomy: team.autonomy === "ask" ? "high" : "ask" }, "自律の度合いを変えました"),
              },
              {
                label: team.web_research === false ? "Web の調査: 禁止（許可にする）" : "Web の調査: 許可（禁止にする）",
                onSelect: () => void toggleField({ web_research: team.web_research === false }, "Web の調査の設定を変えました"),
              },
              {
                label: team.git_init === false ? "git が無い作業場所: 作らない（git init するにする）" : "git が無い作業場所: git init する（作らないにする）",
                onSelect: () => void toggleField({ git_init: team.git_init === false }, "git の設定を変えました"),
              },
              { label: "チームリーダーを任命", onSelect: () => setLeaderSheet(true) },
              { label: "役割を編集（自動の振り分け）", onSelect: () => setRolesSheet(true) },
              {
                label: team.on_limit === "wait" ? "上限で止まったら: 回復を待つ（引き継ぐにする）" : "上限で止まったら: 別の担い手へ引き継ぐ（回復を待つにする）",
                onSelect: () => void toggleOnLimit(),
              },
              {
                label: team.codex_without_sandbox === false
                  ? "Codex のサンドボックスが使えないとき: 止める（サンドボックスなしで動かすにする）"
                  : "Codex のサンドボックスが使えないとき: サンドボックスなしで動かす（止めるにする）",
                onSelect: () => void toggleSandbox(),
              },
              { label: "候補の順番", onSelect: () => setFallbackSheet(true) },
              { label: "同時並列の設定", onSelect: () => setParallelSheet(true) },
              {
                label: team.reset_before_dispatch === false ? "送る前に会話をリセット: オフ（オンにする）" : "送る前に会話をリセット: オン（オフにする）",
                onSelect: () => void toggleReset(),
              },
            ] : []),
            { label: "チームを作る", onSelect: () => setNewTeamSheet(true) },
            { label: "このチームを削除", danger: true, separated: true, onSelect: () => setDeletingTeam(true) },
          ]}
        />
        {expert && <IconButton variant="primary" label="＋ タスク" onClick={() => setTaskSheet(true)}><IconPlus /></IconButton>}
      </div>
      <LeaderTimeline team={team} expert={expert} onChanged={refresh} onHandoff={setHandoffTask} />
      {showBoard && <>
      {expert && (
        <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2">
          <button type="button" data-team-auto-open onClick={() => setAutoSheet(true)} className={secondaryButtonClass}>作業場所を選んでチームを作る</button>
          <span data-team-leader className="min-w-0 break-words text-xs text-zinc-600 dark:text-zinc-300">
            <span className="font-semibold">チームリーダー</span> {endpointText(team.leader ?? team.leader_effective)}{team.leader ? "" : "（既定）"}
          </span>
        </div>
      )}
      <RolesPanel team={team} expert={expert} onEdit={() => setRolesSheet(true)} />

      <section aria-label="メンバー" className="mb-4">
        {team.members.filter((m) => !m.auto).length === 0 ? (
          <p className="rounded-2xl border border-dashed border-zinc-300 p-3 text-xs text-zinc-500 dark:border-zinc-700">
            手動のメンバーはいません（役割から自動で呼び出します）。
            <button type="button" onClick={() => setMembersSheet(true)} className="ml-1 font-semibold text-accent-600 underline dark:text-accent-400">メンバーを選ぶ</button>
          </p>
        ) : (
          <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {team.members.filter((m) => !m.auto).map((m) => {
              const key = memberKey(m);
              if (isHeadless(m)) {
                const mine = team.tasks.filter((t) => t.assignee === key);
                const mstate = mine.some((t) => t.run?.status === "running") ? "working" : mine.some((t) => t.status === "doing" && t.run?.status === "limited") ? "limited" : "waiting";
                return (
                  <li key={key} data-team-member data-team-member-kind="headless" className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900">
                    <div className="flex min-h-11 w-full min-w-0 items-center gap-2">
                      <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-full ${STATE_DOT[mstate]}`} />
                      <span className="min-w-0 flex-1">
                        <span className="flex min-w-0 items-center gap-1.5">
                          <strong className="min-w-0 truncate text-sm">{headlessName(m)}</strong>
                          {expert && <span className="shrink-0 rounded-full bg-accent-50 px-2 py-0.5 text-[10px] font-semibold text-accent-700 dark:bg-accent-900/30 dark:text-accent-300">{ROLE_LABEL[m.role]}</span>}
                          {expert && <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-semibold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">ヘッドレス</span>}
                          {expert && m.no_sandbox && (
                            <span data-team-no-sandbox className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">サンドボックスなし</span>
                          )}
                        </span>
                        <span data-team-member-state={mstate} className={`mt-0.5 block text-[11px] font-semibold ${STATE_COLOR[mstate]}`}>
                          {mstate === "working" ? (expert ? "実行中" : "作業中") : mstate === "limited" ? "上限で停止" : expert ? "待機中（画面なし）" : "待機中"}
                        </span>
                        {expert && m.no_sandbox && <span className="mt-0.5 block text-[11px] text-amber-700 dark:text-amber-400">サンドボックスなし（この機械では使えない）</span>}
                      </span>
                    </div>
                  </li>
                );
              }
              const item = consoleOf(m.session_id ?? "");
              const state = item ? stateOf(item) : null;
              return (
                <li key={key} data-team-member className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900">
                  <button
                    type="button"
                    disabled={!item}
                    onClick={() => onOpenConsole(key)}
                    aria-label={`${consoleName(key)}を開く`}
                    className="flex min-h-11 w-full min-w-0 items-center gap-2 text-left disabled:opacity-60"
                  >
                    <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-full ${state ? STATE_DOT[state] : "bg-zinc-400"}`} />
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <strong className="min-w-0 truncate text-sm">{consoleName(key)}</strong>
                        {expert && <span className="shrink-0 rounded-full bg-accent-50 px-2 py-0.5 text-[10px] font-semibold text-accent-700 dark:bg-accent-900/30 dark:text-accent-300">{ROLE_LABEL[m.role]}</span>}
                      </span>
                      <span data-team-member-state={state ?? "closed"} className={`mt-0.5 block text-[11px] font-semibold ${STATE_COLOR[state ?? "closed"]}`}>
                        {expert || !state ? STATE_TEXT[state ?? "closed"] : state === "working" ? "作業中" : state === "limited" || state === "error" ? "止まった" : state === "exited" ? "終了" : "待機中"}
                      </span>
                      {expert && <UsageLine usage={item?.commander_usage} className="mt-0.5" />}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <div className="mb-3 grid min-w-0 gap-1 text-xs text-zinc-600 dark:text-zinc-300">
        {expert && <p data-team-parallel>
          <span className="font-semibold">同時に動いている数</span>{" "}
          <span className="num">{team.parallel?.running ?? 0}／{team.parallel?.max ?? team.max_parallel ?? 3}</span>
          <span className="ml-2">待ち <span className="num">{team.parallel?.waiting ?? 0}</span></span>
          {team.auto_run === false && <span className="ml-2 text-amber-700 dark:text-amber-400">自動の割り当て: オフ</span>}
          <span className="ml-2 text-zinc-500">{team.on_limit === "wait" ? "上限: 回復を待つ" : "上限: 別の担い手へ引き継ぐ"}</span>
        </p>}
        {team.workdir && (
          <p data-team-workdir className="flex min-w-0 flex-wrap items-center gap-x-2">
            <span className="font-semibold">作業場所</span>
            <span className="min-w-0 truncate" title={team.workdir}>{team.workdir_name || team.workdir_folder}</span>
            {team.workdir_folder && (
              <Link to={`/project-lab?project=${encodeURIComponent(team.workdir_folder)}`} className="font-semibold text-accent-600 underline dark:text-accent-400">Project Lab で開く</Link>
            )}
          </p>
        )}
      </div>

      <div className="grid min-w-0 gap-3 md:grid-cols-4">
        {COLUMNS.map((col) => {
          const tasks = team.tasks.filter((t) => (col.id === "todo" ? t.status === "todo" || t.status === "rejected" || t.status === "blocked" : t.status === col.id));
          return (
            <section key={col.id} data-team-column={col.id} aria-label={col.label} className="min-w-0 rounded-2xl bg-zinc-50 p-2 dark:bg-zinc-900/50">
              <h3 className="mb-2 flex items-center justify-between px-1 text-xs font-semibold text-zinc-600 dark:text-zinc-300">
                {col.label}<span className="num text-zinc-400">{tasks.length}</span>
              </h3>
              <ul className="grid gap-2">
                {tasks.map((task) => (
                  <li key={task.id} className="min-w-0">
                    <button
                      type="button"
                      data-team-task={task.id}
                      onClick={() => setOpenTask(task.id)}
                      className="block min-h-11 w-full min-w-0 rounded-xl border border-zinc-200 bg-white p-2.5 text-left shadow-sm hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900"
                    >
                      <span className="flex min-w-0 items-start gap-1.5">
                        <span className="num shrink-0 text-xs text-zinc-400">#{task.id}</span>
                        <strong className="min-w-0 break-words text-sm">{task.title}</strong>
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-1 text-[11px]">
                        {!expert && <SimpleBadge task={task} />}
                        {expert && task.blocked_reason === "no_handoff" && <span data-team-no-handoff className={BLOCKED_CLASS}>引き継ぎ先なし</span>}
                        {expert && task.role && <span data-team-task-role className="rounded-full bg-accent-50 px-2 py-0.5 font-semibold text-accent-700 dark:bg-accent-900/30 dark:text-accent-300">{team.roles?.find((r) => r.id === task.role)?.name ?? "役割"}</span>}
                        {expert && task.status === "blocked" && task.blocked_reason !== "no_handoff" && <span data-team-blocked className={BLOCKED_CLASS}>自動で直せず</span>}
                        {expert && task.checks_result && (
                          <span data-team-checks={task.checks_result.status} className={`rounded-full px-2 py-0.5 font-semibold ${CHECK_CLASS[task.checks_result.status]}`}>
                            確認: {CHECK_LABEL[task.checks_result.status]}
                          </span>
                        )}
                        {expert && task.status === "rejected" && <span className="rounded-full bg-red-50 px-2 py-0.5 font-semibold text-red-700 dark:bg-red-950/40 dark:text-red-300">差し戻し</span>}
                        {expert && task.unverified && <span data-team-unverified className={UNVERIFIED_CLASS}>未検証</span>}
                        {expert && task.status === "doing" && task.stalled_at && <span data-team-stalled className={WARN_CLASS}>止まった（報告なし）</span>}
                        {expert && task.status === "doing" && task.limited_at && <span data-team-limited className={WARN_CLASS}>上限で停止</span>}
                        {expert && task.run?.status === "running" && (
                          <span data-team-running className="rounded-full bg-blue-50 px-2 py-0.5 font-semibold text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
                            実行中（ヘッドレス）{task.run.elapsed != null ? `・${fmtSeconds(task.run.elapsed)}` : ""}
                          </span>
                        )}
                        {expert && task.status === "doing" && task.wait_until && (
                          <span data-team-waiting className={WARN_CLASS}>回復を待つ（〜{fmtClock(task.wait_until)}）</span>
                        )}
                        {expert && task.report_ready && <span data-team-report-ready className="rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">報告あり</span>}
                        <span className="min-w-0 truncate text-zinc-500 dark:text-zinc-400">
                          {task.assignee ? `${expert && roleOf(team, task.assignee) ? ROLE_LABEL[roleOf(team, task.assignee)!] + "・" : ""}${consoleName(task.assignee)}` : "担当なし"}
                        </span>
                        {task.assignee_state === "closed" && <span className="font-semibold text-red-600 dark:text-red-400">コンソールが閉じています</span>}
                      </span>
                      {expert && task.run?.status === "running" && (task.run.last_line || task.run.usage) && (
                        <span data-team-run-line className="mt-1 block min-w-0 truncate text-[11px] text-zinc-500 dark:text-zinc-400">
                          {task.run.last_line}{fmtUsage(task.run.usage) ? `・${fmtUsage(task.run.usage)}` : ""}
                        </span>
                      )}
                      {expert && task.warnings.length > 0 && (
                        <span data-team-warning className="mt-1 block break-words text-[11px] text-amber-700 dark:text-amber-400">{task.warnings.join(" / ")}</span>
                      )}
                    </button>
                    {task.blocked_reason === "no_handoff" && (
                      <button type="button" data-team-handoff-open={task.id} onClick={() => setHandoffTask(task.id)} className={`mt-1 w-full ${primaryButtonClass}`}>引き継ぎ先を選ぶ</button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      </>}

      {membersSheet && <MembersSheet team={team} consoles={consoles} cliName={cliName} onClose={() => setMembersSheet(false)} onSaved={refresh} />}
      {briefSheet && <BriefSheet team={team} onClose={() => setBriefSheet(false)} onSaved={refresh} />}
      {fallbackSheet && <FallbackSheet team={team} name={consoleName} onClose={() => setFallbackSheet(false)} onSaved={refresh} />}
      {parallelSheet && <ParallelSheet team={team} onClose={() => setParallelSheet(false)} onSaved={refresh} />}
      {taskSheet && <TaskCreateSheet team={team} expert={expert} consoleName={consoleName} onClose={() => setTaskSheet(false)} onSaved={refresh} />}
      {newTeamSheet && <NewTeamSheet onClose={() => setNewTeamSheet(false)} onCreated={(id) => { setSelected(id); refresh(); }} />}
      {autoSheet && <AutoTeamSheet expert={expert} onClose={() => setAutoSheet(false)} onCreated={(id) => { setSelected(id); refresh(); }} />}
      {rolesSheet && <RolesSheet team={team} expert={expert} onClose={() => setRolesSheet(false)} onSaved={refresh} />}
      {leaderSheet && <LeaderSheet team={team} expert={expert} onClose={() => setLeaderSheet(false)} onSaved={refresh} />}
      {handoffTask != null && team.tasks.find((t) => t.id === handoffTask) && (
        <HandoffSheet
          teamId={team.id} task={team.tasks.find((t) => t.id === handoffTask)!} expert={expert}
          roleName={team.roles?.find((r) => r.id === team.tasks.find((t) => t.id === handoffTask)?.role)?.name}
          onClose={() => setHandoffTask(null)} onDone={refresh}
        />
      )}
      {current && (
        <TaskSheet
          team={team} task={current} consoleName={consoleName} roleOf={(sid) => roleOf(team, sid)} expert={expert}
          onHandoff={() => { setOpenTask(null); setHandoffTask(current.id); }}
          onOpenConsole={onOpenConsole} onClose={() => setOpenTask(null)} onChanged={refresh}
        />
      )}
      {deletingTeam && (
        <ConfirmDialog
          title={`チーム「${team.name}」を削除しますか？`}
          message="タスクの記録も消えます。コンソールは閉じません。"
          confirmLabel="削除"
          onConfirm={() => void removeTeam()}
          onClose={() => setDeletingTeam(false)}
        />
      )}
    </div>
  );
}

function BriefSheet({ team, onClose, onSaved }: { team: Team; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((s) => s.show);
  const [brief, setBrief] = useState(team.brief ?? "");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await updateTeam(team.id, { brief });
      onSaved();
      show("チームの前提を保存しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="チームの前提" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        プロジェクトの説明・決まり・使ってよいコマンドなど。タスクを送るたびに、文面の先頭へ付けます（8000 文字まで）。
      </p>
      <label className={fieldLabel} htmlFor="team-brief">前提</label>
      <textarea id="team-brief" rows={10} value={brief} maxLength={8000} onChange={(e) => setBrief(e.target.value)} className={textareaClass} />
      <p className="num mt-1 text-right text-[11px] text-zinc-400">{brief.length} / 8000</p>
      <button type="button" disabled={busy} onClick={() => void save()} className={`mt-3 w-full ${primaryButtonClass}`}>保存</button>
    </BottomSheet>
  );
}

function NewTeamSheet({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const show = useToasts((s) => s.show);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const team = await createTeam(name.trim());
      onCreated(team.id);
      show("チームを作りました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "作れませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="チームを作る" onClose={onClose}>
      <label className={fieldLabel} htmlFor="team-name">チームの名前（40 文字まで）</label>
      <input id="team-name" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} className={inputClass} />
      <button type="button" disabled={!name.trim() || busy} onClick={() => void submit()} className={`mt-4 w-full ${primaryButtonClass}`}>作る</button>
    </BottomSheet>
  );
}

function MembersSheet({
  team, consoles, cliName, onClose, onSaved,
}: {
  team: Team; consoles: CommanderConsole[]; cliName: (id: string) => string; onClose: () => void; onSaved: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [roles, setRoles] = useState<Record<string, TeamRole | "">>(
    () => Object.fromEntries(team.members.filter((m) => !isHeadless(m)).map((m) => [m.session_id ?? "", m.role])),
  );
  const [headless, setHeadless] = useState<TeamMember[]>(() => team.members.filter(isHeadless));
  const [addCli, setAddCli] = useState("claude");
  const [addAccount, setAddAccount] = useState("default");
  const [addRole, setAddRole] = useState<TeamRole>("developer");
  const [addLabel, setAddLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const accountsQuery = useQuery({ queryKey: ["commander-accounts", addCli], queryFn: () => fetchAccounts(addCli) });
  const leaders = Object.values(roles).filter((r) => r === "leader").length + headless.filter((m) => m.role === "leader").length;
  const add = () => {
    setHeadless([...headless, { kind: "headless", cli: addCli, account: addAccount, role: addRole, label: addLabel.trim() }]);
    setAddLabel("");
  };
  const save = async () => {
    setBusy(true);
    try {
      const members: TeamMember[] = [
        ...Object.entries(roles).filter(([, r]) => r).map(([session_id, role]) => ({ session_id, role: role as TeamRole })),
        ...headless.map((m) => ({ kind: "headless" as const, id: m.id, cli: m.cli, account: m.account, role: m.role, label: m.label ?? "" })),
      ];
      await setTeamMembers(team.id, members);
      onSaved();
      show("メンバーを保存しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="メンバーを選ぶ" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        Nexarch のコンソールに役割を付けます（リーダーは 1 人まで）。役割を「なし」にするとメンバーから外れます。
      </p>
      {consoles.length === 0 && <p className="mt-3 text-sm text-zinc-500">コンソールがありません。先に「コンソール」で開くか、下でヘッドレスを足してください。</p>}
      <ul className="mt-3 grid gap-2">
        {consoles.map((item) => (
          <li key={item.id} className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm" title={item.cwd}>
              {cliName(item.commander_cli)}{item.commander_label ? `・${item.commander_label}` : ""}
            </span>
            <select
              aria-label={`${cliName(item.commander_cli)}${item.commander_label ? `・${item.commander_label}` : ""}の役割`}
              value={roles[item.id] ?? ""}
              onChange={(e) => setRoles({ ...roles, [item.id]: e.target.value as TeamRole | "" })}
              className="min-h-11 w-32 shrink-0 rounded-xl border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            >
              <option value="">なし</option>
              {TEAM_ROLES.map(([id, label]) => (
                <option key={id} value={id} disabled={id === "leader" && leaders >= 1 && roles[item.id] !== "leader"}>{label}</option>
              ))}
            </select>
          </li>
        ))}
      </ul>

      <section data-team-headless-section className="mt-4 border-t border-zinc-200 pt-3 dark:border-zinc-800">
        <h4 className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">ヘッドレス（画面なし）</h4>
        <p className="mt-1 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
          CLI と接続だけで、タスクごとに新しい会話で 1 回実行します。結果は自動で板に入ります。
        </p>
        <ul className="mt-2 grid gap-2">
          {headless.map((m, i) => (
            <li key={m.id ?? `new-${i}`} data-team-headless-row className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm">
                {cliName(m.cli ?? "")}（{m.account}）{m.label ? `・${m.label}` : ""}・{ROLE_LABEL[m.role]}
              </span>
              <button
                type="button" onClick={() => setHeadless(headless.filter((_, j) => j !== i))}
                aria-label={`${cliName(m.cli ?? "")}${m.label ? `・${m.label}` : ""}を外す`} className={`${secondaryButtonClass} shrink-0`}
              >外す</button>
            </li>
          ))}
        </ul>
        <div className="mt-2 grid gap-2 rounded-xl border border-dashed border-zinc-300 p-2.5 dark:border-zinc-700">
          <label className="text-xs font-medium text-zinc-600 dark:text-zinc-300" htmlFor="hl-cli">CLI</label>
          <select id="hl-cli" value={addCli} onChange={(e) => { setAddCli(e.target.value); setAddAccount("default"); }} className={inputClass}>
            {HEADLESS_CLIS.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          <label className="text-xs font-medium text-zinc-600 dark:text-zinc-300" htmlFor="hl-account">アカウント・接続</label>
          <select id="hl-account" value={addAccount} onChange={(e) => setAddAccount(e.target.value)} className={inputClass}>
            {(accountsQuery.data ?? [{ name: "default", label: "", title: "", default: true, logged_in: null }]).map((a) => (
              <option key={a.name} value={a.name}>{a.label || a.title || (a.default ? "既定" : a.name)}</option>
            ))}
          </select>
          <label className="text-xs font-medium text-zinc-600 dark:text-zinc-300" htmlFor="hl-role">役割</label>
          <select id="hl-role" value={addRole} onChange={(e) => setAddRole(e.target.value as TeamRole)} className={inputClass}>
            {TEAM_ROLES.map(([id, label]) => <option key={id} value={id} disabled={id === "leader" && leaders >= 1}>{label}</option>)}
          </select>
          <label className="text-xs font-medium text-zinc-600 dark:text-zinc-300" htmlFor="hl-label">名前（任意）</label>
          <input id="hl-label" value={addLabel} maxLength={40} onChange={(e) => setAddLabel(e.target.value)} className={inputClass} />
          <button type="button" onClick={add} className={secondaryButtonClass}>ヘッドレスのメンバーを足す</button>
        </div>
      </section>
      <button type="button" disabled={busy} onClick={() => void save()} className={`mt-4 w-full ${primaryButtonClass}`}>保存</button>
    </BottomSheet>
  );
}

function FallbackSheet({
  team, name, onClose, onSaved,
}: { team: Team; name: (key: string) => string; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((s) => s.show);
  const all = team.members.map(memberKey);
  const saved = team.fallback ?? [];
  const [order, setOrder] = useState<string[]>(() => [...saved.filter((k) => all.includes(k)), ...all.filter((k) => !saved.includes(k))]);
  const [busy, setBusy] = useState(false);
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    setOrder(next);
  };
  const save = async (value: string[]) => {
    setBusy(true);
    try {
      await updateTeam(team.id, { fallback: value });
      onSaved();
      show(value.length ? "候補の順番を保存しました" : "候補の順番を既定に戻しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="候補の順番" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        上限で止まったとき、上から順に空いている担い手へ引き継ぎます。既定は「別の CLI → 同じ CLI の別アカウント → ローカル」です。
      </p>
      <ol className="mt-3 grid gap-2">
        {order.map((key, i) => (
          <li key={key} data-team-fallback-row className="flex min-w-0 items-center gap-2">
            <span className="num w-5 shrink-0 text-xs text-zinc-400">{i + 1}</span>
            <span className="min-w-0 flex-1 truncate text-sm">{name(key)}</span>
            <button type="button" aria-label={`${name(key)}を上へ`} disabled={i === 0} onClick={() => move(i, -1)} className={`${secondaryButtonClass} shrink-0`}>↑</button>
            <button type="button" aria-label={`${name(key)}を下へ`} disabled={i === order.length - 1} onClick={() => move(i, 1)} className={`${secondaryButtonClass} shrink-0`}>↓</button>
          </li>
        ))}
      </ol>
      {order.length === 0 && <p className="mt-3 text-sm text-zinc-500">メンバーがいません。</p>}
      <button type="button" disabled={busy || order.length === 0} onClick={() => void save(order)} className={`mt-4 w-full ${primaryButtonClass}`}>保存</button>
      <button type="button" disabled={busy} onClick={() => void save([])} className={`mt-2 w-full ${secondaryButtonClass}`}>既定の順に戻す</button>
    </BottomSheet>
  );
}

function ParallelSheet({ team, onClose, onSaved }: { team: Team; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((s) => s.show);
  const [max, setMax] = useState(team.max_parallel ?? 3);
  const [local, setLocal] = useState(team.local_parallel ?? 1);
  const [auto, setAuto] = useState(team.auto_run !== false);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await updateTeam(team.id, { max_parallel: max, local_parallel: local, auto_run: auto });
      onSaved();
      show("同時並列の設定を保存しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="同時並列の設定" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        担い手ごとに同時に 1 つ。担当ファイルが重なるタスクは、片方が終わるまで待ちます。
      </p>
      <label className={fieldLabel} htmlFor="par-max">同時に走らせるタスクの数（1〜8）</label>
      <input id="par-max" type="number" min={1} max={8} value={max} onChange={(e) => setMax(Number(e.target.value))} className={inputClass} />
      <label className={fieldLabel} htmlFor="par-local">ローカルの担い手の同時の数（1〜8）</label>
      <input id="par-local" type="number" min={1} max={8} value={local} onChange={(e) => setLocal(Number(e.target.value))} className={inputClass} />
      <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">ローカル LLM は 1 枚の GPU を共有します。モデルの同時処理の枠に合わせて上げてください。</p>
      <label className="mt-3 flex min-h-11 items-center gap-2 text-sm">
        <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
        待ちのタスクを空いた担い手へ自動で割り当てる
      </label>
      <button
        type="button" disabled={busy || max < 1 || max > 8 || local < 1 || local > 8}
        onClick={() => void save()} className={`mt-4 w-full ${primaryButtonClass}`}
      >保存</button>
    </BottomSheet>
  );
}

function AssigneeSelect({
  team, value, consoleName, onChange, id,
}: {
  team: Team; value: string; consoleName: (sid: string) => string; onChange: (sid: string) => void; id: string;
}) {
  const roleText = (sid: string) => { const r = team.members.find((m) => memberKey(m) === sid)?.role; return r ? `${ROLE_LABEL[r]}・` : ""; };
  const inList = !value || team.members.some((m) => memberKey(m) === value);
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={inputClass}>
      <option value="">担当なし</option>
      {!inList && <option value={value}>{consoleName(value)}（メンバー外）</option>}
      {team.members.map((m) => <option key={memberKey(m)} value={memberKey(m)}>{roleText(memberKey(m))}{consoleName(memberKey(m))}{isHeadless(m) ? "（ヘッドレス）" : ""}</option>)}
    </select>
  );
}

function TaskCreateSheet({
  team, expert, consoleName, onClose, onSaved,
}: { team: Team; expert: boolean; consoleName: (sid: string) => string; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((s) => s.show);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [files, setFiles] = useState("");
  const [checks, setChecks] = useState("");
  const [assignee, setAssignee] = useState("");
  const [roleId, setRoleId] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const task = await createTask(team.id, {
        title: title.trim(), goal, acceptance, assignee, ...(roleId ? { role: roleId } : {}),
        files: files.split("\n").map((f) => f.trim()).filter(Boolean),
        checks: checks.split("\n").map((f) => f.trim()).filter(Boolean),
      });
      onSaved();
      show(task.warnings.length ? `タスクを作りました（${task.warnings.join(" / ")}）` : "タスクを作りました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "作れませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="タスクを作る" onClose={onClose}>
      <label className={fieldLabel} htmlFor="task-title">タイトル（必須）</label>
      <input id="task-title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} className={inputClass} />
      <label className={fieldLabel} htmlFor="task-goal">目的</label>
      <textarea id="task-goal" rows={3} value={goal} maxLength={4000} onChange={(e) => setGoal(e.target.value)} className={textareaClass} />
      <label className={fieldLabel} htmlFor="task-acceptance">受け入れ条件</label>
      <textarea id="task-acceptance" rows={3} value={acceptance} maxLength={4000} onChange={(e) => setAcceptance(e.target.value)} className={textareaClass} />
      <label className={fieldLabel} htmlFor="task-files">担当ファイル（1 行に 1 つ）</label>
      <textarea id="task-files" rows={2} value={files} onChange={(e) => setFiles(e.target.value)} className={`${textareaClass} font-mono`} />
      {expert && (<>
        <label className={fieldLabel} htmlFor="task-checks">確認のコマンド（1 行に 1 つ）</label>
        <textarea id="task-checks" rows={2} value={checks} onChange={(e) => setChecks(e.target.value)} className={`${textareaClass} font-mono`} />
        <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">担当の作業フォルダで Nexarch が実行します（10 個まで）。パイプや && は使えません。</p>
        {(team.roles ?? []).length > 0 && (<>
          <label className={fieldLabel} htmlFor="task-role">役割（自動の振り分け）</label>
          <select id="task-role" value={roleId} onChange={(e) => setRoleId(e.target.value)} className={inputClass}>
            <option value="">なし（担当か候補の順で決める）</option>
            {(team.roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </>)}
      </>)}
      <label className={fieldLabel} htmlFor="task-assignee">担当</label>
      <AssigneeSelect id="task-assignee" team={team} value={assignee} consoleName={consoleName} onChange={setAssignee} />
      <button type="button" disabled={!title.trim() || busy} onClick={() => void submit()} className={`mt-4 w-full ${primaryButtonClass}`}>作る</button>
    </BottomSheet>
  );
}

function TaskSheet({
  team, task, consoleName, roleOf, expert, onHandoff, onOpenConsole, onClose, onChanged,
}: {
  team: Team; task: TeamTask; consoleName: (sid: string) => string; roleOf: (sid: string) => TeamRole | undefined;
  expert: boolean; onHandoff: () => void;
  onOpenConsole: (id: string) => void; onClose: () => void; onChanged: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [manual, setManual] = useState(false);
  const [confirmPass, setConfirmPass] = useState(false);
  const [editChecks, setEditChecks] = useState(false);
  const [checksDraft, setChecksDraft] = useState((task.checks ?? []).join("\n"));
  const [draft, setDraft] = useState({ summary: "", changed: "", tests: "", verify: "", remaining: "" });

  const run = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await action();
      onChanged();
      show(done);
    } catch (e) {
      show(e instanceof Error ? e.message : "操作できませんでした", "error");
    }
    setBusy(false);
  };
  const pass = () => run(() => updateTask(team.id, task.id, { status: "done" }), "合格にしました");
  const closed = task.assignee_state === "closed";
  const running = task.run?.status === "running";
  const canDispatch = Boolean(task.assignee) && !closed && task.status !== "done" && !running;
  const headlessAssignee = isHeadless(team.members.find((m) => memberKey(m) === task.assignee));
  const canCollect = Boolean(task.assignee) && !closed && !headlessAssignee && (task.status === "doing" || task.status === "review");
  const role = task.assignee ? roleOf(task.assignee) : undefined;
  const assigneeState = task.assignee_state;

  // 主操作は 1 つ: 状態に応じて「合格」「報告を取り込む」「担当へ送る」のどれか
  const primary = task.status === "review" ? "pass" : task.status === "doing" ? (headlessAssignee ? "none" : "collect") : "dispatch";

  return (
    <BottomSheet title={`#${task.id} ${task.title}`} onClose={onClose}>
      <p data-team-task-status className="text-xs font-semibold text-zinc-500">
        {STATUS_LABEL[task.status]}
        {expert && task.report_ready && <span className="ml-2 text-emerald-600 dark:text-emerald-400">報告あり</span>}
        {expert && task.unverified && <span data-team-unverified className="ml-2 text-orange-700 dark:text-orange-300">未検証</span>}
        {task.status === "doing" && task.stalled_at && <span data-team-stalled className="ml-2 text-amber-700 dark:text-amber-400">止まった（報告なし）</span>}
        {task.status === "doing" && task.limited_at && <span data-team-limited className="ml-2 text-amber-700 dark:text-amber-400">上限で停止</span>}
        {expert && running && <span data-team-running className="ml-2 text-blue-600 dark:text-blue-400">実行中（ヘッドレス）</span>}
        {task.blocked_reason === "no_handoff" && <span data-team-no-handoff className="ml-2 text-red-600 dark:text-red-400">上限で止まり、引き継ぎ先がありません</span>}
        {task.status === "blocked" && task.blocked_reason !== "no_handoff" && <span data-team-blocked className="ml-2 text-red-600 dark:text-red-400">確認が通らず、自動で直せませんでした</span>}
        {expert && task.checks_result && <span data-team-checks={task.checks_result.status} className="ml-2">確認: {CHECK_LABEL[task.checks_result.status]}</span>}
        {expert && Boolean(task.attempts) && <span className="ml-2 font-normal">自動で直させた回数 {task.attempts}/2</span>}
      </p>
      <dl className="mt-2 grid gap-2 text-sm">
        <div><dt className="text-xs text-zinc-500">目的</dt><dd className="whitespace-pre-wrap break-words">{task.goal || "（なし）"}</dd></div>
        <div><dt className="text-xs text-zinc-500">受け入れ条件</dt><dd className="whitespace-pre-wrap break-words">{task.acceptance || "（なし）"}</dd></div>
        <div>
          <dt className="text-xs text-zinc-500">担当ファイル</dt>
          <dd className="break-all font-mono text-xs">{task.files.length ? task.files.join(", ") : "指定なし"}</dd>
        </div>
      </dl>
      {expert && task.warnings.length > 0 && <p className="mt-2 break-words text-xs text-amber-700 dark:text-amber-400">{task.warnings.join(" / ")}</p>}

      {expert && <section data-team-checks-panel className="mt-3 rounded-xl border border-zinc-200 p-2.5 text-sm dark:border-zinc-700">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-semibold text-zinc-500">確認のコマンド</p>
          <button type="button" onClick={() => setEditChecks((v) => !v)} aria-expanded={editChecks} className="text-xs font-semibold text-accent-600 underline dark:text-accent-400">
            {editChecks ? "編集をやめる" : "編集"}
          </button>
        </div>
        {editChecks ? (
          <>
            <label className="sr-only" htmlFor="detail-checks">確認のコマンド（1 行に 1 つ）</label>
            <textarea id="detail-checks" rows={3} value={checksDraft} onChange={(e) => setChecksDraft(e.target.value)} className={`mt-1 ${textareaClass} font-mono`} />
            <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">担当の作業フォルダで実行します。パイプや && は使えません（10 個まで）。</p>
            <button
              type="button" disabled={busy} className={`mt-2 w-full ${secondaryButtonClass}`}
              onClick={() => void run(() => updateTask(team.id, task.id, { checks: checksDraft.split("\n").map((c) => c.trim()).filter(Boolean) }), "確認のコマンドを保存しました")}
            >確認のコマンドを保存</button>
          </>
        ) : (task.checks ?? []).length === 0 ? (
          <p className="mt-1 text-xs text-zinc-500">なし（確認は担当の自己申告だけ）</p>
        ) : (
          <ul className="mt-1 grid gap-1">
            {(task.checks ?? []).map((cmd, i) => {
              const row = task.checks_result?.results.find((r) => r.cmd === cmd) ?? task.checks_result?.results[i];
              return (
                <li key={`${i}-${cmd}`} data-team-check-row className="min-w-0">
                  <p className="flex min-w-0 items-start gap-1.5 text-xs">
                    <span aria-hidden className={row ? (row.exit_code === 0 ? "text-emerald-600" : "text-red-600") : "text-zinc-400"}>
                      {row ? (row.exit_code === 0 ? "✓" : "✗") : "・"}
                    </span>
                    <code className="min-w-0 break-all font-mono">{cmd}</code>
                    {row && <span className="num shrink-0 text-zinc-500">終了 {row.exit_code}</span>}
                  </p>
                  {row && row.tail && (
                    <details className="ml-4 mt-0.5">
                      <summary className="cursor-pointer text-[11px] text-zinc-500">出力の最後</summary>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-100 p-2 font-mono text-[11px] dark:bg-zinc-800">{row.tail}</pre>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {task.checks_result?.message && <p className="mt-1 break-words text-xs text-amber-700 dark:text-amber-400">{task.checks_result.message}</p>}
        {task.checks_result?.status === "running" && <p className="mt-1 text-xs text-blue-600 dark:text-blue-400">実行中です…</p>}
      </section>}

      <label className={fieldLabel} htmlFor="detail-assignee">担当</label>
      <AssigneeSelect
        id="detail-assignee" team={team} value={task.assignee} consoleName={consoleName}
        onChange={(sid) => void run(() => updateTask(team.id, task.id, { assignee: sid }), "担当を変えました")}
      />
      {task.assignee && (
        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px]">
          <span className="text-zinc-500">{role ? ROLE_LABEL[role] : "メンバー外"}</span>
          {assigneeState && <span className={`font-semibold ${STATE_COLOR[assigneeState]}`}>{STATE_TEXT[assigneeState]}</span>}
          {!closed && !headlessAssignee && (
            <button type="button" onClick={() => { onClose(); onOpenConsole(task.assignee); }} className="font-semibold text-accent-600 underline dark:text-accent-400">コンソールを開く</button>
          )}
        </p>
      )}

      {task.report && (
        <div data-team-report className="mt-3 rounded-xl border border-zinc-200 p-2.5 text-sm dark:border-zinc-700">
          <p className="text-xs font-semibold text-zinc-500">報告</p>
          <p className="break-words">{task.report.summary}</p>
          <p className="mt-1 break-all font-mono text-xs text-zinc-500">変更: {task.report.changed_files.join(", ") || "なし"}</p>
          <p className="mt-1 break-words text-xs">試験: {task.report.tests || "（なし）"}</p>
          <p className="break-words text-xs">確認: {task.report.verify || "（なし）"}</p>
          <p className="break-words text-xs">残り: {task.report.remaining || "なし"}</p>
        </div>
      )}

      {expert && (task.run || (task.runs ?? []).length > 0) && (
        <section data-team-run-panel className="mt-3 rounded-xl border border-zinc-200 p-2.5 text-sm dark:border-zinc-700">
          <p className="text-xs font-semibold text-zinc-500">実行</p>
          {task.run && (
            <div data-team-run-status={task.run.status} className="mt-1 text-xs">
              <p className="font-semibold">
                {task.run.status === "running" ? "実行中（ヘッドレス）" : RUN_RESULT_LABEL[task.run.status === "finished" ? "ok" : task.run.status] ?? task.run.status}
                {" "}
                <span className="font-normal text-zinc-500">
                  {fmtSeconds(task.run.elapsed ?? (task.run.ended_at ? task.run.ended_at - task.run.started_at : undefined))}
                </span>
              </p>
              {task.run.sandbox === "none" && <p data-team-run-sandbox className="mt-0.5 text-amber-700 dark:text-amber-400">サンドボックスなし（この機械では使えない）</p>}
              {task.run.failed_note && <p data-team-run-failed className="mt-0.5 text-red-600 dark:text-red-400">{task.run.failed_note}</p>}
              {task.run.last_line && <p data-team-run-line className="mt-0.5 break-words text-zinc-600 dark:text-zinc-300">最後の行: {task.run.last_line}</p>}
              {task.run.usage && fmtUsage(task.run.usage) && <p data-team-run-usage className="mt-0.5 text-zinc-500">使用量: {fmtUsage(task.run.usage)}</p>}
              {task.run.status === "limited" && task.wait_until && <p className="mt-0.5 text-amber-700 dark:text-amber-400">回復を待っています（〜{fmtClock(task.wait_until)}）</p>}
              {task.run.tail && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-[11px] text-zinc-500">出力の最後</summary>
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-100 p-2 font-mono text-[11px] dark:bg-zinc-800">{task.run.tail}</pre>
                </details>
              )}
            </div>
          )}
          {(task.runs ?? []).length > 0 && (
            <ol data-team-runs className="mt-2 grid gap-1 text-xs">
              {(task.runs ?? []).map((r) => (
                <li key={r.n} className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                  <span className="num text-zinc-400">{r.n}</span>
                  <span className="min-w-0 break-words font-semibold">{r.label || r.cli}</span>
                  <span className="text-zinc-500">{r.kind === "headless" ? "ヘッドレス" : "画面"}・{r.cli}{r.account && r.account !== "default" ? `（${r.account}）` : ""}</span>
                  <span>{RUN_RESULT_LABEL[r.result] ?? r.result}</span>
                  {r.sandbox === "none" && <span className="text-amber-700 dark:text-amber-400">サンドボックスなし（この機械では使えない）</span>}
                  {r.seconds != null && <span className="text-zinc-500">{fmtSeconds(r.seconds)}</span>}
                  {fmtUsage(r.usage) && <span className="text-zinc-500">{fmtUsage(r.usage)}</span>}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      <div className="sheet-actions sticky bottom-0 -mx-5 mt-4 flex flex-wrap gap-2 border-t border-zinc-200 bg-white px-5 py-3 dark:border-zinc-800 dark:bg-zinc-900">
        {task.status === "review" && (
          <button type="button" disabled={busy} onClick={() => (task.unverified ? setConfirmPass(true) : void pass())}
            className={primary === "pass" ? primaryButtonClass : secondaryButtonClass}>合格</button>
        )}
        {canCollect && (
          <button type="button" disabled={busy} onClick={() => void run(() => collectTask(team.id, task.id), "報告を取り込みました")}
            className={primary === "collect" ? primaryButtonClass : secondaryButtonClass}>報告を取り込む</button>
        )}
        {canDispatch && (
          <button type="button" disabled={busy} onClick={() => void run(() => dispatchTask(team.id, task.id), headlessAssignee ? "実行を始めました" : "担当へ送りました")}
            className={primary === "dispatch" ? primaryButtonClass : secondaryButtonClass}>{headlessAssignee ? "実行する" : "担当へ送る"}</button>
        )}
        {task.blocked_reason === "no_handoff" && (
          <button type="button" data-team-handoff-open={task.id} onClick={onHandoff} className={primaryButtonClass}>引き継ぎ先を選ぶ</button>
        )}
        {running && (
          <button type="button" disabled={busy} onClick={() => void run(() => stopTask(team.id, task.id), "止めています")}
            className={secondaryButtonClass}>止める</button>
        )}
        {(task.status === "review" || task.status === "doing") && (
          <button type="button" disabled={busy} onClick={() => void run(() => updateTask(team.id, task.id, { status: "rejected" }), "差し戻しました")}
            className={secondaryButtonClass}>差し戻し</button>
        )}
        {expert && <button type="button" onClick={() => setManual((v) => !v)} aria-expanded={manual} className={secondaryButtonClass}>報告を手で入力</button>}
        <button type="button" onClick={() => setConfirmDelete(true)} className={`${secondaryButtonClass} text-red-600 dark:text-red-400`}>削除</button>
      </div>

      {manual && (
        <div className="mt-3">
          <label className={fieldLabel} htmlFor="rep-summary">完了報告（1 行）</label>
          <input id="rep-summary" value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} className={inputClass} />
          <label className={fieldLabel} htmlFor="rep-changed">変更ファイル（1 行に 1 つ）</label>
          <textarea id="rep-changed" rows={2} value={draft.changed} onChange={(e) => setDraft({ ...draft, changed: e.target.value })} className={`${textareaClass} font-mono`} />
          <label className={fieldLabel} htmlFor="rep-tests">試験</label>
          <input id="rep-tests" value={draft.tests} onChange={(e) => setDraft({ ...draft, tests: e.target.value })} className={inputClass} />
          <label className={fieldLabel} htmlFor="rep-verify">確認（実行したコマンドと結果）</label>
          <input id="rep-verify" value={draft.verify} onChange={(e) => setDraft({ ...draft, verify: e.target.value })} className={inputClass} />
          <label className={fieldLabel} htmlFor="rep-remaining">残り</label>
          <input id="rep-remaining" value={draft.remaining} onChange={(e) => setDraft({ ...draft, remaining: e.target.value })} className={inputClass} />
          <button
            type="button" disabled={busy || !draft.summary.trim()}
            onClick={() => void run(() => putTaskReport(team.id, task.id, {
              summary: draft.summary.trim(), tests: draft.tests.trim(), verify: draft.verify.trim(), remaining: draft.remaining.trim(),
              changed_files: draft.changed.split("\n").map((f) => f.trim()).filter(Boolean),
            }), "報告を保存しました")}
            className={`mt-3 w-full ${secondaryButtonClass}`}
          >報告を保存</button>
        </div>
      )}

      {confirmPass && (
        <ConfirmDialog
          title="確認の結果がありません。合格にしますか？"
          message="報告に確認の結果（実行したコマンドと結果）が無い、または試験が空です。"
          confirmLabel="合格にする"
          danger={false}
          onConfirm={() => { setConfirmPass(false); void pass(); }}
          onClose={() => setConfirmPass(false)}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title={`タスク #${task.id} を削除しますか？`}
          message="この操作は取り消せません。"
          confirmLabel="削除"
          onConfirm={() => { setConfirmDelete(false); void run(async () => { await deleteTask(team.id, task.id); onClose(); }, "タスクを削除しました"); }}
          onClose={() => setConfirmDelete(false)}
        />
      )}
    </BottomSheet>
  );
}
