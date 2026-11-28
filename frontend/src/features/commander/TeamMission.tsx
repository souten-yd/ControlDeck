/** AI チームの「いま」を 1 画面で見せる（リーダーと担い手のアバター・6 段の道・一文のまとめ）。
 *
 * 何をするチームか・誰がいて・どこまで進んだかを、文字の札を並べずに図で見せる。
 * 段（調査→計画→実装→評価→統合→試験）はタスクと計画の段から求め、丸いノードを 1 本の道でつなぐ
 * （終わった所までは緑、いまの段は青で脈打つ、まだは灰色）。進んでいる所だけ控えめに動かす（`motion-safe`）。
 * 担い手の状態は「色・形・動き・文字」で分ける（色だけに頼らない）。判定は `memberStatus()` の 1 か所。
 *   作業中 = 青・破線の輪がゆっくり回る / 待ち = 琥珀・砂時計・輪は動かない / 止まった = 赤・斜線と ×・実線の輪 / 済み = 緑・チェック。
 * 段やアバターを押すと、その中身（仕事・最後の行・CLI とモデル・使用量・報告）がその場で開く。
 * 細かい値（モデルなど）はエキスパートのときだけ添える。事実はチームの板（ControlDeck 側）が正本で、この部品は表示だけを行う。 */
import { useState } from "react";
import {
  memberKey, STATE_LABEL,
  type CommanderConsole,
  type Team,
  type TeamMember,
  type TeamRole,
  type TeamTask,
} from "../../api/commander";
import {
  IconBranch, IconChat, IconCheck, IconChevronRight, IconClipboardList, IconHammer, IconSearch, IconTest, IconX,
} from "../../components/icons";
import { UsageLine } from "./UsageLine";

type StageId = "explore" | "plan" | "implement" | "review" | "integrate" | "verify";
type StageState = "done" | "active" | "attention" | "waiting";

const STAGES: ReadonlyArray<{ id: StageId; label: string; Icon: typeof IconSearch }> = [
  { id: "explore", label: "調査", Icon: IconSearch },
  { id: "plan", label: "計画", Icon: IconClipboardList },
  { id: "implement", label: "実装", Icon: IconHammer },
  { id: "review", label: "評価", Icon: IconChat },  // 「済み」のチェックと区別する（2026-11-28 Claude のデザインの見直し）
  { id: "integrate", label: "統合", Icon: IconBranch },
  { id: "verify", label: "試験", Icon: IconTest },
];

/** ノードの見た目。active は脈打つ輪を別に重ねる。 */
const NODE_STYLE: Record<StageState, string> = {
  done: "bg-emerald-500 text-white",
  active: "bg-accent-600 text-white",
  attention: "bg-red-600 text-white",
  waiting: "border-2 border-zinc-300 bg-white text-zinc-400 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-500",
};
/** その段へ入ってくる線（前の段から）。 */
const LINE_STYLE: Record<StageState, string> = {
  done: "bg-emerald-400 dark:bg-emerald-600",
  active: "bg-gradient-to-r from-emerald-400 to-accent-500 dark:from-emerald-600 dark:to-accent-500",
  attention: "bg-gradient-to-r from-emerald-400 to-red-500 dark:from-emerald-600 dark:to-red-500",
  waiting: "bg-zinc-200 dark:bg-zinc-700",
};
const LABEL_STYLE: Record<StageState, string> = {
  done: "text-emerald-700 dark:text-emerald-300",
  active: "text-accent-700 dark:text-accent-300",
  attention: "text-red-700 dark:text-red-300",
  waiting: "text-zinc-500 dark:text-zinc-400",
};

/** 役割ごとのアバターの色（調=調査・実=実装・評=評価・難=難しい仕事）。 */
const ROLE_AVATAR: Record<TeamRole, string> = {
  leader: "bg-gradient-to-br from-accent-500 to-accent-700",
  researcher: "bg-violet-500",
  developer: "bg-blue-500",
  reviewer: "bg-amber-500",
  member: "bg-zinc-500",
};
const HARD_AVATAR = "bg-fuchsia-600";

const isHeadless = (m: TeamMember | undefined) => m?.kind === "headless";

// ---- 担い手の状態（1 か所で決める） ----

export type MemberStatusKind = "working" | "waiting" | "stopped" | "done";
export interface MemberStatus {
  kind: MemberStatusKind;
  /** 小さな札の文字（作業中・待ち・止まった・済み）。 */
  tag: string;
  /** 詳細に出す説明（なぜそうなのか）。 */
  reason: string;
}

const STATUS_TAG: Record<MemberStatusKind, string> = { working: "作業中", waiting: "待ち", stopped: "止まった", done: "済み" };
const mk = (kind: MemberStatusKind, reason: string): MemberStatus => ({ kind, tag: STATUS_TAG[kind], reason });

/** 担い手の状態。仕事の status（doing/todo/blocked/done）・ヘッドレスの run の状態・コンソールの commander_state（STATE_LABEL）から作る。
 * 止まった＞作業中＞待ち＞済み の順に強い方を採る（止まりは見逃さない）。 */
export function memberStatus(team: Team, m: TeamMember, consoles: CommanderConsole[]): MemberStatus {
  const key = memberKey(m);
  const mine = team.tasks.filter((t) => t.assignee === key);
  const open = mine.filter((t) => t.status !== "done");
  const runDead = (t: TeamTask) => t.run != null && ["failed", "stopped", "interrupted"].includes(t.run.status);
  if (m.role === "leader" && team.leader_run) {
    const r = team.leader_run.status;
    if (r === "failed" || r === "stopped" || r === "blocked") return mk("stopped", "チームリーダーが止まりました");
  }
  if (isHeadless(m)) {
    if (open.some((t) => t.status === "blocked" || runDead(t))) return mk("stopped", "仕事が止まりました（失敗・中断）");
    if (open.some((t) => t.run?.status === "running" || t.status === "doing" && !t.limited_at && t.run?.status !== "limited")) return mk("working", "仕事を進めています");
    if (open.some((t) => t.limited_at || t.run?.status === "limited")) return mk("waiting", "上限が回復するのを待っています");
    if (open.length === 0 && mine.length > 0) return mk("done", "渡された仕事はすべて終わりました");
    return mk("waiting", "次の仕事を待っています");
  }
  const item = consoles.find((c) => c.id === m.session_id);
  if (!item) return mk("stopped", "コンソールが閉じています");
  const st = item.commander_state ?? (item.alive ? "waiting" : "exited");
  if (st === "error" || st === "exited") return mk("stopped", STATE_LABEL[st]);
  if (open.some((t) => t.status === "blocked")) return mk("stopped", "仕事が止まりました");
  if (st === "working") return mk("working", "仕事を進めています");
  if (st === "limited") return mk("waiting", STATE_LABEL.limited);
  if (open.length === 0 && mine.length > 0) return mk("done", "渡された仕事はすべて終わりました");
  return mk("waiting", STATE_LABEL.waiting);
}

/** リーダー自身の状態（走っている・質問を待つ・止まった・結果が出た）。 */
function leaderStatus(team: Team): MemberStatus {
  const r = team.leader_run?.status;
  if (r === "failed" || r === "stopped" || r === "blocked") return mk("stopped", "チームリーダーが止まりました");
  if (r === "running") return mk("working", "計画し、仕事を割り振っています");
  if ((team.questions ?? []).some((q) => q.answer === null)) return mk("waiting", "あなたの答えを待っています");
  if (team.result) return mk("done", "結果をまとめました");
  return mk("waiting", "次の依頼を待っています");
}

const RING_TONE: Record<MemberStatusKind, string> = {
  working: "border-2 border-dashed border-accent-500 motion-safe:animate-[spin_8s_linear_infinite]",
  waiting: "border-2 border-amber-400 dark:border-amber-500",
  stopped: "border-2 border-red-500",
  done: "border-2 border-emerald-500",
};
const TAG_TONE: Record<MemberStatusKind, string> = {
  working: "bg-accent-50 text-accent-700 dark:bg-accent-950/50 dark:text-accent-300",
  waiting: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  stopped: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  done: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
};

function Hourglass({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden className={className}>
      <path d="M6 3h12M6 21h12M7 3c0 5 5 6 5 9s-5 4-5 9M17 3c0 5-5 6-5 9s5 4 5 9" />
    </svg>
  );
}

/** 丸いアバター。輪（形・動き）・右下の印・止まったときの斜線で状態を示す。 */
function Avatar({ initial, tone, status, size = "md" }: { initial: string; tone: string; status: MemberStatusKind; size?: "md" | "lg" }) {
  const dim = size === "lg" ? "h-14 w-14" : "h-11 w-11";
  return (
    <span aria-hidden data-avatar-ring={status} className={`relative flex ${dim} shrink-0 items-center justify-center`}>
      <span className={`absolute inset-0 rounded-full ${RING_TONE[status]}`} />
      <span className={`relative flex h-[78%] w-[78%] items-center justify-center overflow-hidden rounded-full text-sm font-bold text-white ${tone} ${status === "stopped" ? "opacity-70 saturate-50" : ""}`}>
        {initial}
        {status === "stopped" && (
          <svg viewBox="0 0 24 24" className="absolute inset-0 h-full w-full text-red-100" aria-hidden><path d="M4 20 20 4" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" /></svg>
        )}
      </span>
      {status !== "working" && (
        <span className={`absolute -bottom-0.5 -right-0.5 flex h-[18px] w-[18px] items-center justify-center rounded-full border-2 border-white text-white dark:border-zinc-900 ${status === "done" ? "bg-emerald-500" : status === "stopped" ? "bg-red-600" : "bg-amber-500"}`}>
          {status === "done" ? <IconCheck className="h-2.5 w-2.5" /> : status === "stopped" ? <IconX className="h-2.5 w-2.5" /> : <Hourglass className="h-2.5 w-2.5" />}
        </span>
      )}
    </span>
  );
}

function StatusTag({ status, className = "" }: { status: MemberStatus; className?: string }) {
  return <span data-status-tag={status.kind} className={`rounded-full px-1.5 py-px text-[10px] font-semibold ${TAG_TONE[status.kind]} ${className}`}>{status.tag}</span>;
}

/** 難しい仕事の担い手か（役割の名前が「難」を含む）。 */
function roleTone(team: Team, m: TeamMember): string {
  const name = team.roles?.find((r) => r.id === m.role_id)?.name ?? "";
  return name.includes("難") ? HARD_AVATAR : ROLE_AVATAR[m.role] ?? ROLE_AVATAR.member;
}

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

/** チームの表示（リーダー・6 段の道・担い手のアバター・押すと開く中身）。 */
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
  const leader = leaderStatus(team);
  const members = team.members.filter((m) => !m.auto);
  const stageTasks = openStage ? tasksOfStage(team, openStage) : [];
  const opened = members.find((m) => memberKey(m) === openMember);

  const nameOf = (m: TeamMember) => (isHeadless(m) ? `${m.label || m.cli || "ヘッドレス"}` : consoleName(memberKey(m)));

  return (
    <section data-team-mission aria-label="チームの進み具合" className="mb-4 min-w-0 overflow-hidden rounded-3xl border border-zinc-200 bg-gradient-to-br from-white to-zinc-50 p-3 shadow-sm dark:border-zinc-800 dark:from-zinc-900 dark:to-zinc-950">
      <div data-mission-leader={leader.kind} className="flex min-w-0 items-center gap-3">
        <Avatar initial="★" tone={ROLE_AVATAR.leader} status={leader.kind} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
            チームリーダー<StatusTag status={leader} />
          </p>
          <p data-mission-summary data-mission-attention={summary.attention} className="mt-0.5 min-w-0 break-words text-sm font-semibold">{summary.text}</p>
        </div>
      </div>

      <div data-mission-stages className="mt-4 flex min-w-0">
        {stages.map(({ id, label, state, total, done, Icon }, i) => {
          const open = openStage === id;
          return (
            <button
              key={id}
              type="button"
              data-mission-stage={id}
              data-mission-state={state}
              aria-expanded={open}
              aria-label={`${label}${total > 0 ? ` ${done}/${total}` : ""}`}
              onClick={() => setOpenStage(open ? null : id)}
              className="relative flex min-h-14 min-w-0 flex-1 flex-col items-center gap-1 rounded-xl px-0 pb-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-500"
            >
              {i > 0 && <span aria-hidden data-mission-line={state} className={`absolute right-1/2 top-[15px] h-1 w-full rounded-full ${LINE_STYLE[state]}`} />}
              <span aria-hidden className={`relative z-10 flex h-8 w-8 items-center justify-center rounded-full ${NODE_STYLE[state]} ${open ? "ring-2 ring-accent-400 ring-offset-2 ring-offset-white dark:ring-offset-zinc-900" : ""}`}>
                {state === "active" && <span className="absolute inset-0 rounded-full bg-accent-400/50 motion-safe:animate-ping" />}
                {state === "done" ? <IconCheck className="relative h-4 w-4" /> : state === "attention" ? <IconX className="relative h-4 w-4" /> : <Icon className="relative h-4 w-4" />}
              </span>
              <span className={`whitespace-nowrap text-[11px] font-semibold leading-none ${LABEL_STYLE[state]}`}>{label}</span>
              <span className="num min-h-[10px] text-[10px] leading-none text-zinc-400 dark:text-zinc-500">{total > 0 ? `${done}/${total}` : ""}</span>
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
        <div data-mission-members className="mt-3 grid min-w-0 grid-cols-4 gap-1 pb-1 sm:flex sm:flex-wrap">
          {members.map((m) => {
            const key = memberKey(m);
            const title = nameOf(m);
            const st = memberStatus(team, m, consoles);
            const open = openMember === key;
            return (
              <button
                key={key}
                type="button"
                data-mission-member={key}
                data-mission-status={st.kind}
                aria-expanded={open}
                onClick={() => setOpenMember(open ? null : key)}
                className={`flex min-h-11 w-[5.5rem] shrink-0 flex-col items-center gap-1 rounded-2xl px-1 py-1.5 text-center hover:bg-zinc-100 dark:hover:bg-zinc-800 ${open ? "bg-zinc-100 dark:bg-zinc-800" : ""}`}
              >
                <Avatar initial={title.slice(0, 1)} tone={roleTone(team, m)} status={st.kind} />
                <span className="block w-full truncate text-xs font-semibold">{title}</span>
                <StatusTag status={st} />
                {expert && m.model && <span className="block w-full truncate font-mono text-[10px] text-zinc-500">{m.model}</span>}
              </button>
            );
          })}
        </div>
      )}

      {opened && <MemberDetails team={team} m={opened} title={nameOf(opened)} consoles={consoles} onOpenConsole={onOpenConsole} />}
    </section>
  );
}

/** アバターを押すと開く詳細（今の仕事・最後の行・CLI とモデル・使用量・報告）。 */
function MemberDetails({ team, m, title, consoles, onOpenConsole }: {
  team: Team; m: TeamMember; title: string; consoles: CommanderConsole[]; onOpenConsole: (id: string) => void;
}) {
  const key = memberKey(m);
  const st = memberStatus(team, m, consoles);
  const mine = team.tasks.filter((t) => t.assignee === key && t.status !== "done");
  const running = mine.find((t) => t.run?.status === "running") ?? mine.find((t) => t.run);
  const lastDone = [...team.tasks].reverse().find((t) => t.assignee === key && t.status === "done" && t.report);
  const item = consoles.find((c) => c.id === m.session_id);
  const usage = running?.run?.usage;
  return (
    <div data-mission-member-details={key} className="mt-2 min-w-0 rounded-2xl border border-zinc-200 bg-white p-3 text-xs dark:border-zinc-700 dark:bg-zinc-900">
      <p className="flex flex-wrap items-center gap-1.5 text-sm font-semibold">
        <span className="min-w-0 break-words">{title}</span>
        <StatusTag status={st} />
      </p>
      <p data-mission-member-state={st.kind} className="mt-0.5 text-zinc-500 dark:text-zinc-400">{st.tag}・{st.reason}</p>
      <p data-mission-member-cli className="mt-1 break-all font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
        {m.kind === "headless" ? "画面なし" : "コンソール"}{m.cli ? `・${m.cli}` : ""}{m.account ? `・${m.account}` : ""}{m.model ? `・${m.model}` : ""}{m.effort ? `・${m.effort}` : ""}
      </p>
      {mine.length > 0 ? (
        <ul className="mt-2 grid gap-0.5">
          {mine.slice(0, 4).map((t) => <li key={t.id} className="break-words">・{t.title}</li>)}
        </ul>
      ) : <p className="mt-2 text-zinc-500">いま持っている仕事はありません。</p>}
      {running?.run?.last_line && (
        <p data-mission-member-last className="mt-2 break-all rounded-lg bg-zinc-50 px-2 py-1 font-mono text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{running.run.last_line}</p>
      )}
      {usage && (
        <p data-mission-member-usage className="num mt-1 text-[11px] text-zinc-500">
          {usage.input_tokens.toLocaleString()} 入力・{usage.output_tokens.toLocaleString()} 出力{usage.cost_usd > 0 ? `・$${usage.cost_usd.toFixed(2)}` : ""}
        </p>
      )}
      {item && <UsageLine usage={item.commander_usage} className="mt-1" />}
      {lastDone?.report && (
        <p data-mission-member-report className="mt-2 break-words text-zinc-600 dark:text-zinc-300"><span className="font-semibold">報告</span>　{lastDone.report.summary}</p>
      )}
      {!isHeadless(m) && (
        <button type="button" onClick={() => onOpenConsole(key)} className="mt-2 flex min-h-8 items-center gap-0.5 font-semibold text-accent-600 underline dark:text-accent-400">
          コンソールを開く<IconChevronRight className="h-3 w-3" />
        </button>
      )}
    </div>
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
