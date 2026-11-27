/** Nexarch の AI チーム（T1）: メンバー（役割つきのコンソール）とタスク板。
 *
 * 状態の正本は板（ControlDeck 側）。担当へは「担当へ送る」で短い文を送り、終わったら
 * 「報告を取り込む」で画面の「完了報告:」を読む（LLM なし）。主操作は「＋ タスク」の 1 つ、
 * メンバー・チームの操作はその他メニューへ。モバイル（〜767px）は列を縦に並べる。 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  collectTask, createTask, createTeam, deleteTask, deleteTeam, dispatchTask, fetchTeams, putTaskReport,
  ROLE_LABEL, setTeamMembers, STATE_LABEL, TEAM_ROLES, updateTask,
  type CommanderConsole, type CommanderState, type TaskStatus, type Team, type TeamRole, type TeamTask,
} from "../../api/commander";
import {
  BottomSheet, ConfirmDialog, DropdownMenu, EmptyState, IconButton, primaryButtonClass, secondaryButtonClass, Skeleton,
} from "../../components/ui";
import { IconDots, IconPlus } from "../../components/icons";
import { useToasts } from "../../stores";
import { UsageLine } from "./UsageLine";

const inputClass = "min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const textareaClass = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const fieldLabel = "mb-1 mt-3 block text-xs font-medium text-zinc-600 dark:text-zinc-300";
const SELECTED_KEY = "control-deck:commander-team";

const COLUMNS: ReadonlyArray<{ id: "todo" | "doing" | "review" | "done"; label: string }> = [
  { id: "todo", label: "待ち" }, { id: "doing", label: "作業中" }, { id: "review", label: "レビュー待ち" }, { id: "done", label: "完了" },
];
const STATUS_LABEL: Record<TaskStatus, string> = {
  todo: "待ち", doing: "作業中", review: "レビュー待ち", done: "完了", rejected: "差し戻し",
};
const STATE_TEXT: Record<CommanderState | "closed", string> = { ...STATE_LABEL, closed: "コンソールが閉じています" };
const STATE_COLOR: Record<CommanderState | "closed", string> = {
  working: "text-blue-600 dark:text-blue-400", waiting: "text-emerald-600 dark:text-emerald-400",
  limited: "text-amber-700 dark:text-amber-400", error: "text-red-600 dark:text-red-400",
  exited: "text-zinc-500", closed: "text-red-600 dark:text-red-400",
};
const STATE_DOT: Record<CommanderState, string> = {
  working: "bg-blue-500 animate-pulse", waiting: "bg-emerald-500", limited: "bg-amber-500", error: "bg-red-500", exited: "bg-zinc-400",
};

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
  const [taskSheet, setTaskSheet] = useState(false);
  const [newTeamSheet, setNewTeamSheet] = useState(false);
  const [openTask, setOpenTask] = useState<number | null>(null);
  const [deletingTeam, setDeletingTeam] = useState(false);

  const list = teams ?? [];
  const team: Team | undefined = list.find((t) => t.id === selected) ?? list[0];
  useEffect(() => {
    try { if (team) localStorage.setItem(SELECTED_KEY, team.id); } catch { /* 保存できない端末は覚えない */ }
  }, [team]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["commander-teams"] });
  const consoleOf = (sid: string) => consoles.find((c) => c.id === sid);
  const consoleName = (sid: string) => {
    const item = consoleOf(sid);
    if (!item) return "閉じたコンソール";
    return `${cliName(item.commander_cli)}${item.commander_label ? `・${item.commander_label}` : ""}`;
  };
  const roleOf = (t: Team, sid: string) => t.members.find((m) => m.session_id === sid)?.role;

  if (isLoading) return <Skeleton className="h-24" />;

  if (!team) {
    return (
      <>
        <EmptyState actions={<button onClick={() => setNewTeamSheet(true)} className={primaryButtonClass}>チームを作る</button>}>
          チームはありません。コンソールに役割を付けて、タスクを渡せます。
        </EmptyState>
        {newTeamSheet && <NewTeamSheet onClose={() => setNewTeamSheet(false)} onCreated={(id) => { setSelected(id); refresh(); }} />}
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
  const current = team.tasks.find((t) => t.id === openTask);

  return (
    <div data-team-view>
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
            { label: "メンバーを選ぶ", onSelect: () => setMembersSheet(true) },
            { label: "チームを作る", onSelect: () => setNewTeamSheet(true) },
            { label: "このチームを削除", danger: true, separated: true, onSelect: () => setDeletingTeam(true) },
          ]}
        />
        <IconButton variant="primary" label="＋ タスク" onClick={() => setTaskSheet(true)}><IconPlus /></IconButton>
      </div>

      <section aria-label="メンバー" className="mb-4">
        {team.members.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-zinc-300 p-3 text-xs text-zinc-500 dark:border-zinc-700">
            メンバーがいません。
            <button type="button" onClick={() => setMembersSheet(true)} className="ml-1 font-semibold text-accent-600 underline dark:text-accent-400">メンバーを選ぶ</button>
          </p>
        ) : (
          <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {team.members.map((m) => {
              const item = consoleOf(m.session_id);
              const state = item ? stateOf(item) : null;
              return (
                <li key={m.session_id} data-team-member className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900">
                  <button
                    type="button"
                    disabled={!item}
                    onClick={() => onOpenConsole(m.session_id)}
                    aria-label={`${consoleName(m.session_id)}を開く`}
                    className="flex min-h-11 w-full min-w-0 items-center gap-2 text-left disabled:opacity-60"
                  >
                    <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-full ${state ? STATE_DOT[state] : "bg-zinc-400"}`} />
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <strong className="min-w-0 truncate text-sm">{consoleName(m.session_id)}</strong>
                        <span className="shrink-0 rounded-full bg-accent-50 px-2 py-0.5 text-[10px] font-semibold text-accent-700 dark:bg-accent-900/30 dark:text-accent-300">{ROLE_LABEL[m.role]}</span>
                      </span>
                      <span data-team-member-state={state ?? "closed"} className={`mt-0.5 block text-[11px] font-semibold ${STATE_COLOR[state ?? "closed"]}`}>
                        {STATE_TEXT[state ?? "closed"]}
                      </span>
                      <UsageLine usage={item?.commander_usage} className="mt-0.5" />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <div className="grid min-w-0 gap-3 md:grid-cols-4">
        {COLUMNS.map((col) => {
          const tasks = team.tasks.filter((t) => (col.id === "todo" ? t.status === "todo" || t.status === "rejected" : t.status === col.id));
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
                        {task.status === "rejected" && <span className="rounded-full bg-red-50 px-2 py-0.5 font-semibold text-red-700 dark:bg-red-950/40 dark:text-red-300">差し戻し</span>}
                        {task.report_ready && <span data-team-report-ready className="rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">報告あり</span>}
                        <span className="min-w-0 truncate text-zinc-500 dark:text-zinc-400">
                          {task.assignee ? `${roleOf(team, task.assignee) ? ROLE_LABEL[roleOf(team, task.assignee)!] + "・" : ""}${consoleName(task.assignee)}` : "担当なし"}
                        </span>
                        {task.assignee_state === "closed" && <span className="font-semibold text-red-600 dark:text-red-400">コンソールが閉じています</span>}
                      </span>
                      {task.warnings.length > 0 && (
                        <span data-team-warning className="mt-1 block break-words text-[11px] text-amber-700 dark:text-amber-400">{task.warnings.join(" / ")}</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      {membersSheet && <MembersSheet team={team} consoles={consoles} cliName={cliName} onClose={() => setMembersSheet(false)} onSaved={refresh} />}
      {taskSheet && <TaskCreateSheet team={team} consoleName={consoleName} onClose={() => setTaskSheet(false)} onSaved={refresh} />}
      {newTeamSheet && <NewTeamSheet onClose={() => setNewTeamSheet(false)} onCreated={(id) => { setSelected(id); refresh(); }} />}
      {current && (
        <TaskSheet
          team={team} task={current} consoleName={consoleName} roleOf={(sid) => roleOf(team, sid)}
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
    () => Object.fromEntries(team.members.map((m) => [m.session_id, m.role])),
  );
  const [busy, setBusy] = useState(false);
  const leaders = Object.values(roles).filter((r) => r === "leader").length;
  const save = async () => {
    setBusy(true);
    try {
      const members = Object.entries(roles).filter(([, r]) => r).map(([session_id, role]) => ({ session_id, role: role as TeamRole }));
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
      {consoles.length === 0 && <p className="mt-3 text-sm text-zinc-500">コンソールがありません。先に「コンソール」で開いてください。</p>}
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
      <button type="button" disabled={busy} onClick={() => void save()} className={`mt-4 w-full ${primaryButtonClass}`}>保存</button>
    </BottomSheet>
  );
}

function AssigneeSelect({
  team, value, consoleName, onChange, id,
}: {
  team: Team; value: string; consoleName: (sid: string) => string; onChange: (sid: string) => void; id: string;
}) {
  const roleText = (sid: string) => { const r = team.members.find((m) => m.session_id === sid)?.role; return r ? `${ROLE_LABEL[r]}・` : ""; };
  const inList = !value || team.members.some((m) => m.session_id === value);
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={inputClass}>
      <option value="">担当なし</option>
      {!inList && <option value={value}>{consoleName(value)}（メンバー外）</option>}
      {team.members.map((m) => <option key={m.session_id} value={m.session_id}>{roleText(m.session_id)}{consoleName(m.session_id)}</option>)}
    </select>
  );
}

function TaskCreateSheet({
  team, consoleName, onClose, onSaved,
}: { team: Team; consoleName: (sid: string) => string; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((s) => s.show);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [files, setFiles] = useState("");
  const [assignee, setAssignee] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const task = await createTask(team.id, {
        title: title.trim(), goal, acceptance, assignee,
        files: files.split("\n").map((f) => f.trim()).filter(Boolean),
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
      <label className={fieldLabel} htmlFor="task-assignee">担当</label>
      <AssigneeSelect id="task-assignee" team={team} value={assignee} consoleName={consoleName} onChange={setAssignee} />
      <button type="button" disabled={!title.trim() || busy} onClick={() => void submit()} className={`mt-4 w-full ${primaryButtonClass}`}>作る</button>
    </BottomSheet>
  );
}

function TaskSheet({
  team, task, consoleName, roleOf, onOpenConsole, onClose, onChanged,
}: {
  team: Team; task: TeamTask; consoleName: (sid: string) => string; roleOf: (sid: string) => TeamRole | undefined;
  onOpenConsole: (id: string) => void; onClose: () => void; onChanged: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [manual, setManual] = useState(false);
  const [draft, setDraft] = useState({ summary: "", changed: "", tests: "", remaining: "" });

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
  const closed = task.assignee_state === "closed";
  const canDispatch = Boolean(task.assignee) && !closed && task.status !== "done";
  const canCollect = Boolean(task.assignee) && !closed && (task.status === "doing" || task.status === "review");
  const role = task.assignee ? roleOf(task.assignee) : undefined;
  const assigneeState = task.assignee_state;

  // 主操作は 1 つ: 状態に応じて「合格」「報告を取り込む」「担当へ送る」のどれか
  const primary = task.status === "review" ? "pass" : task.status === "doing" ? "collect" : "dispatch";

  return (
    <BottomSheet title={`#${task.id} ${task.title}`} onClose={onClose}>
      <p data-team-task-status className="text-xs font-semibold text-zinc-500">
        {STATUS_LABEL[task.status]}
        {task.report_ready && <span className="ml-2 text-emerald-600 dark:text-emerald-400">報告あり</span>}
      </p>
      <dl className="mt-2 grid gap-2 text-sm">
        <div><dt className="text-xs text-zinc-500">目的</dt><dd className="whitespace-pre-wrap break-words">{task.goal || "（なし）"}</dd></div>
        <div><dt className="text-xs text-zinc-500">受け入れ条件</dt><dd className="whitespace-pre-wrap break-words">{task.acceptance || "（なし）"}</dd></div>
        <div>
          <dt className="text-xs text-zinc-500">担当ファイル</dt>
          <dd className="break-all font-mono text-xs">{task.files.length ? task.files.join(", ") : "指定なし"}</dd>
        </div>
      </dl>
      {task.warnings.length > 0 && <p className="mt-2 break-words text-xs text-amber-700 dark:text-amber-400">{task.warnings.join(" / ")}</p>}

      <label className={fieldLabel} htmlFor="detail-assignee">担当</label>
      <AssigneeSelect
        id="detail-assignee" team={team} value={task.assignee} consoleName={consoleName}
        onChange={(sid) => void run(() => updateTask(team.id, task.id, { assignee: sid }), "担当を変えました")}
      />
      {task.assignee && (
        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px]">
          <span className="text-zinc-500">{role ? ROLE_LABEL[role] : "メンバー外"}</span>
          {assigneeState && <span className={`font-semibold ${STATE_COLOR[assigneeState]}`}>{STATE_TEXT[assigneeState]}</span>}
          {!closed && (
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
          <p className="break-words text-xs">残り: {task.report.remaining || "なし"}</p>
        </div>
      )}

      <div className="sheet-actions sticky bottom-0 -mx-5 mt-4 flex flex-wrap gap-2 border-t border-zinc-200 bg-white px-5 py-3 dark:border-zinc-800 dark:bg-zinc-900">
        {task.status === "review" && (
          <button type="button" disabled={busy} onClick={() => void run(() => updateTask(team.id, task.id, { status: "done" }), "合格にしました")}
            className={primary === "pass" ? primaryButtonClass : secondaryButtonClass}>合格</button>
        )}
        {canCollect && (
          <button type="button" disabled={busy} onClick={() => void run(() => collectTask(team.id, task.id), "報告を取り込みました")}
            className={primary === "collect" ? primaryButtonClass : secondaryButtonClass}>報告を取り込む</button>
        )}
        {canDispatch && (
          <button type="button" disabled={busy} onClick={() => void run(() => dispatchTask(team.id, task.id), "担当へ送りました")}
            className={primary === "dispatch" ? primaryButtonClass : secondaryButtonClass}>担当へ送る</button>
        )}
        {(task.status === "review" || task.status === "doing") && (
          <button type="button" disabled={busy} onClick={() => void run(() => updateTask(team.id, task.id, { status: "rejected" }), "差し戻しました")}
            className={secondaryButtonClass}>差し戻し</button>
        )}
        <button type="button" onClick={() => setManual((v) => !v)} aria-expanded={manual} className={secondaryButtonClass}>報告を手で入力</button>
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
          <label className={fieldLabel} htmlFor="rep-remaining">残り</label>
          <input id="rep-remaining" value={draft.remaining} onChange={(e) => setDraft({ ...draft, remaining: e.target.value })} className={inputClass} />
          <button
            type="button" disabled={busy || !draft.summary.trim()}
            onClick={() => void run(() => putTaskReport(team.id, task.id, {
              summary: draft.summary.trim(), tests: draft.tests.trim(), remaining: draft.remaining.trim(),
              changed_files: draft.changed.split("\n").map((f) => f.trim()).filter(Boolean),
            }), "報告を保存しました")}
            className={`mt-3 w-full ${secondaryButtonClass}`}
          >報告を保存</button>
        </div>
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
