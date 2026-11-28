/** AI チームの「いま」を 1 画面で見せる（アバター・6 段の進み・一文のまとめ）。
 *
 * 何をするチームか・誰がいて・どこまで進んだかを、文字の札を並べずに図で見せる。
 * 段（調査→計画→実装→評価→統合→試験）はタスクと計画の段から求め、進んでいる段だけを
 * 控えめに動かす（`motion-safe`。動きを減らす設定の端末では止まる）。
 * 段やアバターを押すと、その中身（仕事・担当・モデル）がその場で開く。
 * 細かい値（モデル・接続・使用量・履歴）はエキスパートのときだけ添える。
 * 事実はチームの板（ControlDeck 側）が正本で、この部品は表示だけを行う。 */
import { useState } from "react";
import {
  memberKey, ROLE_LABEL,
  type CommanderConsole,
  type CommanderState,
  type Team,
  type TeamMember,
  type TeamRole,
  type TeamTask,
} from "../../api/commander";
import {
  IconBranch, IconCheck, IconChevronRight, IconClipboardList, IconHammer, IconSearch, IconTest,
} from "../../components/icons";

type StageId = "explore" | "plan" | "implement" | "review" | "integrate" | "verify";
type StageState = "done" | "active" | "attention" | "waiting";

const STAGES: ReadonlyArray<{ id: StageId; label: string; Icon: typeof IconSearch }> = [
  { id: "explore", label: "調査", Icon: IconSearch },
  { id: "plan", label: "計画", Icon: IconClipboardList },
  { id: "implement", label: "実装", Icon: IconHammer },
  { id: "review", label: "評価", Icon: IconCheck },
  { id: "integrate", label: "統合", Icon: IconBranch },
  { id: "verify", label: "試験", Icon: IconTest },
];

const STATE_STYLE: Record<StageState, { chip: string; icon: string; line: string }> = {
  done: {
    chip: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300",
    icon: "bg-emerald-500 text-white", line: "bg-emerald-300 dark:bg-emerald-800",
  },
  active: {
    chip: "border-accent-300 bg-accent-50 text-accent-800 dark:border-accent-800 dark:bg-accent-950/40 dark:text-accent-200",
    icon: "bg-accent-600 text-white motion-safe:animate-pulse", line: "bg-accent-400 dark:bg-accent-700",
  },
  attention: {
    chip: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    icon: "bg-red-600 text-white", line: "bg-red-300 dark:bg-red-800",
  },
  waiting: {
    chip: "border-zinc-200 bg-white text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400",
    icon: "bg-zinc-200 text-zinc-500 dark:bg-zinc-700 dark:text-zinc-300", line: "bg-zinc-200 dark:bg-zinc-700",
  },
};

const ROLE_AVATAR: Record<TeamRole, string> = {
  leader: "bg-violet-500",
  developer: "bg-blue-500",
  reviewer: "bg-emerald-500",
  researcher: "bg-amber-500",
  member: "bg-zinc-500",
};

const STATE_DOT: Record<CommanderState | "closed", string> = {
  working: "bg-blue-500 motion-safe:animate-pulse",
  waiting: "bg-emerald-500",
  limited: "bg-amber-500",
  error: "bg-red-500",
  exited: "bg-zinc-400",
  closed: "bg-red-400",
};

const STATE_WORD: Record<CommanderState | "closed", string> = {
  working: "作業中", waiting: "待機中", limited: "上限で停止", error: "エラー", exited: "終了", closed: "コンソールが閉じています",
};

const isHeadless = (m: TeamMember | undefined) => m?.kind === "headless";

/** 段ごとの数（計画の項目とタスクを合わせて数える。二重に数えない）。 */
function stageCounts(team: Team, tier: "explore" | "implement" | "review") {
  const items = (team.plan?.items ?? []).filter((i) => i.tier === tier && !i.removed);
  const taskIds = new Set(items.map((i) => i.task_id).filter((id): id is number => id != null));
  const tasks = team.tasks.filter((t) => t.tier === tier && !taskIds.has(t.id));
  const done = items.filter((i) => i.status === "done" || i.status === "integrated").length
    + tasks.filter((t) => t.status === "done").length;
  // 進んでいるか・止まっているかは、計画の項目に紐づくタスクも含めて見る（数からは除いても状態は拾う）。
  const tierTasks = team.tasks.filter((t) => t.tier === tier);
  const active = tierTasks.some((t) => t.status === "doing" || t.run?.status === "running")
    || items.some((i) => i.status === "delegated" && team.leader_run?.status === "running");
  const attention = team.tasks.some((t) => t.tier === tier && (t.status === "blocked" || t.blocked_reason === "no_handoff"));
  return { total: items.length + tasks.length, done, active, attention };
}

function stageOf(team: Team): Array<{ id: StageId; label: string; state: StageState; total: number; done: number; Icon: typeof IconSearch }> {
  const items = (team.plan?.items ?? []).filter((i) => !i.removed);
  const leaderRunning = team.leader_run?.status === "running";
  const byTier = {
    explore: stageCounts(team, "explore"),
    implement: stageCounts(team, "implement"),
    review: stageCounts(team, "review"),
  };
  const result = team.result;
  const checks = result?.checks?.status;
  const integrateDone = Boolean(result?.landed);
  const integrateReady = Boolean(result && result.can_land && !result.landed);
  return STAGES.map(({ id, label, Icon }) => {
    let state: StageState = "waiting";
    let total = 0;
    let done = 0;
    if (id === "explore") {
      const s = byTier.explore;
      ({ total, done } = s);
      state = s.attention ? "attention" : s.active ? "active" : s.total > 0 && s.done >= s.total ? "done" : leaderRunning && items.length === 0 ? "active" : items.length > 0 ? "done" : "waiting";
    } else if (id === "plan") {
      total = items.length;
      done = team.plan ? total : 0;
      state = team.plan ? "done" : leaderRunning ? "active" : "waiting";
    } else if (id === "implement" || id === "review") {
      const s = byTier[id];
      ({ total, done } = s);
      state = s.attention ? "attention" : s.active ? "active" : s.total > 0 && s.done >= s.total ? "done" : "waiting";
    } else if (id === "integrate") {
      total = result ? 1 : 0;
      done = integrateDone ? 1 : 0;
      state = integrateDone ? "done" : result?.outcome === "blocked" ? "attention" : integrateReady ? "active" : "waiting";
    } else {
      total = checks ? 1 : 0;
      done = checks === "passed" ? 1 : 0;
      state = checks === "passed" ? "done" : checks === "failed" || checks === "error" ? "attention" : "waiting";
    }
    return { id, label, state, total, done, Icon };
  });
}

/** 一文のまとめ（いま何が起きているか）。 */
export function missionSummary(team: Team): { text: string; attention: number } {
  const tasks = team.tasks;
  const attention = tasks.filter((t) => t.status === "blocked" || t.blocked_reason === "no_handoff").length;
  const running = tasks.filter((t) => t.status === "doing" || t.run?.status === "running").length;
  const left = tasks.filter((t) => t.status === "todo" || t.status === "rejected").length;
  const stages = stageOf(team);
  const active = stages.find((s) => s.state === "active");
  const done = tasks.filter((t) => t.status === "done").length;
  if (team.leader_run?.status === "running") return { text: team.plan ? "チームリーダーが仕事を割り振っています" : "チームリーダーが調べて計画しています", attention };
  if (attention > 0) return { text: `${attention} 件が要対応です（止まった・引き継ぎ先なし）`, attention };
  if (active) return { text: `${active.label}を${running > 0 ? ` ${running} 人が` : ""}進めています${left > 0 ? `（残り ${left} 件）` : ""}`, attention };
  if (team.result) {
    return {
      text: team.result.landed ? "完了しました（ブランチを取り込み済み）" : team.result.outcome === "done" ? "完了しました（取り込み待ち）" : "止まっています。要対応を開いてください",
      attention,
    };
  }
  if (tasks.length === 0) return { text: "まだ仕事はありません。上の一文から頼むと始まります", attention };
  return { text: `完了 ${done}／${tasks.length} 件`, attention };
}

/** チームの表示（アバターの列・6 段・一文のまとめ・押すと開く中身）。 */
export function TeamMission({
  team, expert, consoles, consoleName, onOpenConsole,
}: {
  team: Team;
  expert: boolean;
  consoles: CommanderConsole[];
  consoleName: (sid: string) => string;
  onOpenConsole: (id: string) => void;
}) {
  const [openStage, setOpenStage] = useState<StageId | null>(null);
  const [openMember, setOpenMember] = useState<string | null>(null);
  const stages = stageOf(team);
  const summary = missionSummary(team);
  const members = team.members.filter((m) => !m.auto);
  const stageTasks = openStage ? tasksOfStage(team, openStage) : [];

  const memberState = (m: TeamMember): CommanderState | "closed" => {
    const key = memberKey(m);
    const mine = team.tasks.filter((t) => t.assignee === key);
    if (isHeadless(m)) {
      if (mine.some((t) => t.run?.status === "running" || t.status === "doing")) return "working";
      if (mine.some((t) => t.limited_at || t.run?.status === "limited")) return "limited";
      return "waiting";
    }
    const item = consoles.find((c) => c.id === m.session_id);
    return item ? item.commander_state ?? (item.alive ? "waiting" : "exited") : "closed";
  };

  return (
    <section data-team-mission aria-label="チームの進み具合" className="mb-4 min-w-0 rounded-3xl border border-zinc-200 bg-gradient-to-br from-white to-zinc-50 p-3 shadow-sm dark:border-zinc-800 dark:from-zinc-900 dark:to-zinc-950">
      <p data-mission-summary className="flex min-w-0 items-start gap-2 text-sm font-semibold">
        <span aria-hidden className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${summary.attention > 0 ? "bg-red-500 motion-safe:animate-pulse" : "bg-accent-500"}`} />
        <span className="min-w-0 break-words">{summary.text}</span>
      </p>

      <div data-mission-stages className="mt-3 grid grid-cols-3 gap-1.5 sm:grid-cols-6">
        {stages.map(({ id, label, state, total, done, Icon }) => {
          const style = STATE_STYLE[state];
          const open = openStage === id;
          return (
            <button
              key={id}
              type="button"
              data-mission-stage={id}
              data-mission-state={state}
              aria-expanded={open}
              onClick={() => setOpenStage(open ? null : id)}
              className={`flex min-h-11 min-w-0 flex-col items-center gap-1 rounded-2xl border px-1 py-2 transition ${style.chip} ${state === "active" ? "shadow-sm ring-2 ring-accent-200 dark:ring-accent-900" : ""}`}
            >
              <span aria-hidden className={`flex h-6 w-6 items-center justify-center rounded-full ${style.icon}`}>
                {state === "done" ? <IconCheck className="h-3.5 w-3.5" /> : <Icon className="h-3.5 w-3.5" />}
              </span>
              <span className="flex min-w-0 items-center gap-1 text-[11px] font-semibold">
                <span className="truncate">{label}</span>
                {total > 0 && <span className="num shrink-0 opacity-70">{done}/{total}</span>}
              </span>
            </button>
          );
        })}
      </div>

      {openStage && (
        <div data-mission-stage-tasks={openStage} className="mt-2 rounded-2xl border border-zinc-200 bg-white p-2 text-xs dark:border-zinc-700 dark:bg-zinc-900">
          {stageTasks.length === 0 ? (
            <p className="px-1 py-1.5 text-zinc-500">この段の仕事はまだありません。</p>
          ) : (
            <ul className="grid gap-1">
              {stageTasks.map((t) => (
                <li key={t.id} className="flex min-w-0 items-start gap-2 rounded-xl px-1 py-1">
                  <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${t.status === "done" ? "bg-emerald-500" : t.status === "blocked" ? "bg-red-500" : t.status === "doing" ? "bg-blue-500 motion-safe:animate-pulse" : "bg-zinc-300"}`} />
                  <span className="min-w-0 flex-1 break-words">
                    <span className="num mr-1 text-zinc-400">#{t.id}</span>{t.title}
                    {t.assignee && <span className="ml-1 text-zinc-500">— {consoleName(t.assignee)}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {members.length > 0 && (
        <div data-mission-members className="mt-3 flex min-w-0 gap-2 overflow-x-auto pb-1">
          {members.map((m) => {
            const key = memberKey(m);
            const title = isHeadless(m) ? `${m.label || m.cli || "ヘッドレス"}` : consoleName(key);
            const state = memberState(m);
            const open = openMember === key;
            const mine = team.tasks.filter((t) => t.assignee === key && t.status !== "done");
            return (
              <div key={key} className="min-w-0 shrink-0">
                <button
                  type="button"
                  data-mission-member={key}
                  aria-expanded={open}
                  onClick={() => setOpenMember(open ? null : key)}
                  className="flex min-h-11 w-full min-w-0 items-center gap-2 rounded-2xl border border-zinc-200 bg-white px-2 py-1.5 text-left hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-900"
                >
                  <span aria-hidden className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ${ROLE_AVATAR[m.role] ?? ROLE_AVATAR.member}`}>
                    {title.slice(0, 1)}
                    <span className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-white dark:border-zinc-900 ${STATE_DOT[state]}`} />
                  </span>
                  <span className="min-w-0">
                    <span className="block max-w-[9rem] truncate text-xs font-semibold">{title}</span>
                    <span className="block text-[10px] text-zinc-500">{ROLE_LABEL[m.role]}{expert && m.model ? `・${m.model}` : ""}</span>
                  </span>
                  {expert && mine.length > 0 && <span className="num shrink-0 rounded-full bg-zinc-100 px-1.5 text-[10px] font-semibold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{mine.length}</span>}
                </button>
                {open && (
                  <div data-mission-member-details={key} className="mt-1 w-56 max-w-full rounded-2xl border border-zinc-200 bg-white p-2 text-[11px] dark:border-zinc-700 dark:bg-zinc-900">
                    <p className="font-semibold">{title}（{ROLE_LABEL[m.role]}）</p>
                    <p data-mission-member-state={state} className="mt-0.5 text-zinc-500">{STATE_WORD[state]}</p>
                    {expert && (
                      <p className="mt-0.5 break-all font-mono text-[10px] text-zinc-500">
                        {m.kind === "headless" ? "画面なし" : "コンソール"}{m.cli ? `・${m.cli}` : ""}{m.account ? `・${m.account}` : ""}{m.model ? `・${m.model}` : ""}{m.effort ? `・${m.effort}` : ""}
                      </p>
                    )}
                    {mine.length > 0 ? (
                      <ul className="mt-1 grid gap-0.5">
                        {mine.slice(0, 4).map((t) => <li key={t.id} className="break-words">・{t.title}</li>)}
                      </ul>
                    ) : <p className="mt-1 text-zinc-500">いま持っている仕事はありません。</p>}
                    {!isHeadless(m) && (
                      <button
                        type="button"
                        onClick={() => onOpenConsole(key)}
                        className="mt-1 flex min-h-8 items-center gap-0.5 font-semibold text-accent-600 underline dark:text-accent-400"
                      >
                        コンソールを開く<IconChevronRight className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** 選んだ段の仕事（計画の項目と、段の付いたタスク）。 */
function tasksOfStage(team: Team, stage: StageId): TeamTask[] {
  if (stage === "integrate" || stage === "verify") return [];
  if (stage === "plan") {
    return (team.plan?.items ?? []).filter((i) => !i.removed).map((i) => team.tasks.find((t) => t.id === i.task_id)).filter((t): t is TeamTask => Boolean(t));
  }
  return team.tasks.filter((t) => t.tier === stage);
}
