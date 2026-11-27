/** AI Commander（/commander）の API 呼び出し。Terminal のセッション（tmux）を印つきで使う。 */
import { api } from "./client";

export interface CommanderCli {
  id: string;
  name: string;
  executable: string;
  installed: boolean;
  /** アカウントを分けられる CLI か（gemini は false）。 */
  multi_account: boolean;
  /** "controldeck-opencode" は ControlDeck の OpenCode を OpenCode 画面の API 設定で開く。 */
  connector?: string;
}

export interface CommanderAccount {
  name: string;
  default: boolean;
  /** null は「判定しない」（OpenCode・Gemini は C1 では判定しない）。 */
  logged_in: boolean | null;
  /** 画面の名前（既定のアカウントにも付けられる）。空なら付けていない。 */
  label: string;
  /** 既定の見せ方（OpenCode の接続は「モデル名（提供元）」など）。 */
  title?: string;
  usage?: CommanderUsage | null;
  /** ログインしている名前（Claude はメール、Codex はログインの方法）。 */
  login_name?: string;
  /** Claude: アカウントの設定の statusline（ours=Nexarch が使用率を取る・other=利用者の別のもの・none）。 */
  usage_statusline?: "ours" | "other" | "none";
}

/** アカウントの見せ方: 画面の名前 → ログインの名前（メール）→ 接続の見せ方 → 既定／フォルダの名前。 */
export function accountTitle(name: string, label?: string, long = false, title?: string, login?: string): string {
  if (label) return label;
  if (login) return login;
  if (long && title) return title;
  if (name === "default") return long ? "既定のアカウント（普段の設定）" : "既定";
  return name;
}

/** コンソール = Terminal のセッション + 印（commander_*）。一覧は生きているか・最後に動いた時刻も持つ。 */
export interface CommanderConsole {
  id: string;
  name: string;
  created_at: number;
  attached: boolean;
  persistent: boolean;
  engine: "v1" | "v2-lab";
  program: string;
  cwd: string;
  pid: number;
  activity_at: number;
  alive: boolean;
  workload: "idle" | "running";
  commander_cli: string;
  commander_account: string;
  commander_account_label?: string;
  /** そのアカウントのログインの名前（Claude はメール）。 */
  commander_login_name?: string;
  commander_label?: string;
  /** cwd が CodeDEV の作業場所なら、その表示名（フォルダ名の代わりに出す）。 */
  commander_workdir_name?: string;
  commander_role?: string;
  /** C2: 画面の文字から判定した状態（LLM なし）。 */
  commander_state?: CommanderState;
  /** 画面の飾りを除いた最後の行。 */
  commander_last_line?: string;
  /** 上限・エラーの行（上限で停止・エラーのとき）。 */
  commander_detail?: string;
  /** C3: 自動再開。"off" で止めている。resume_at は次に送る予定（unix 秒）。 */
  commander_autoresume?: string;
  commander_resume_at?: string;
  commander_resume_count?: string;
  /** そのコンソールだけの再開の文言（無ければ全体の設定）。 */
  commander_resume_message?: string;
  /** 5 時間・週の上限の使用率（Claude・Codex）。 */
  commander_usage?: CommanderUsage;
  commander_created_by?: string;
}

export interface UsageWindow { used_percent: number; resets_at: number | null }
/** 5 時間・週の上限の使用率（アカウントごと）。 */
export interface CommanderUsage { five_hour: UsageWindow | null; seven_day: UsageWindow | null; updated_at?: number; source?: string }

export type CommanderState = "working" | "waiting" | "limited" | "error" | "exited";

export const STATE_LABEL: Record<CommanderState, string> = {
  working: "作業中", waiting: "入力待ち", limited: "上限で停止", error: "エラー", exited: "終了",
};

export interface OpenedConsole {
  id: string;
  name: string;
  persistent: boolean;
  engine: string;
  commander_cli: string;
  commander_account: string;
  commander_label: string;
}

export interface ConsolesOpenResult {
  opened: OpenedConsole[];
  not_opened: number;
  reason: string;
}

export interface OpenConsolesBody {
  cli: string;
  account: string;
  /** 既存のフォルダ。new_workspace を渡すときは空でよい。 */
  cwd: string;
  count: number;
  label: string;
  /** 新しい作業場所（自動の名前）を CodeDEV に作って開く。name が空なら自動の名前。 */
  new_workspace?: { name: string };
}

export async function fetchClis(): Promise<CommanderCli[]> {
  return (await api<{ clis: CommanderCli[] }>("/commander/clis")).clis;
}

export async function fetchAccounts(cli: string): Promise<CommanderAccount[]> {
  return (await api<{ accounts: CommanderAccount[] }>(`/commander/accounts/${encodeURIComponent(cli)}`)).accounts;
}

export async function createAccount(cli: string, name: string): Promise<CommanderAccount> {
  return api<CommanderAccount>(`/commander/accounts/${encodeURIComponent(cli)}`, {
    method: "POST",
    json: { name },
  });
}

export async function deleteAccount(cli: string, name: string): Promise<void> {
  await api<void>(`/commander/accounts/${encodeURIComponent(cli)}/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export async function setAccountLabel(cli: string, name: string, label: string): Promise<{ name: string; label: string }> {
  return api(`/commander/accounts/${encodeURIComponent(cli)}/${encodeURIComponent(name)}/label`, {
    method: "PUT",
    json: { label },
  });
}

export async function setConsoleAutoResume(id: string, enabled: boolean): Promise<{ ok: boolean }> {
  return api(`/commander/consoles/${encodeURIComponent(id)}/autoresume`, { method: "PUT", json: { enabled } });
}

export interface CommanderSettings { resume_message: string; default_resume_message: string }

export async function fetchCommanderSettings(): Promise<CommanderSettings> {
  return api<CommanderSettings>("/commander/settings");
}

export async function putCommanderSettings(body: { resume_message: string }): Promise<CommanderSettings> {
  return api<CommanderSettings>("/commander/settings", { method: "PUT", json: body });
}

export async function setConsoleResumeMessage(id: string, message: string): Promise<{ ok: boolean }> {
  return api(`/commander/consoles/${encodeURIComponent(id)}/resume-message`, { method: "PUT", json: { message } });
}

export async function setUsageStatusline(name: string, enabled: boolean): Promise<{ usage_statusline: string }> {
  return api(`/commander/accounts/claude/${encodeURIComponent(name)}/usage-statusline`, { method: "PUT", json: { enabled } });
}

export async function fetchConsoles(): Promise<CommanderConsole[]> {
  return (await api<{ consoles: CommanderConsole[] }>("/commander/consoles")).consoles;
}

export async function openConsoles(body: OpenConsolesBody): Promise<ConsolesOpenResult> {
  return api<ConsolesOpenResult>("/commander/consoles", { method: "POST", json: body });
}

export async function closeConsole(id: string): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>(`/commander/consoles/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// ---- AI チーム（T1）: タスク板と、担当のコンソールへの送り込み ----

export type TeamRole = "leader" | "developer" | "reviewer" | "researcher" | "member";
export const TEAM_ROLES: ReadonlyArray<readonly [TeamRole, string]> = [
  ["leader", "リーダー"], ["developer", "開発"], ["reviewer", "レビュー"], ["researcher", "調査"], ["member", "メンバー"],
];
export const ROLE_LABEL: Record<TeamRole, string> = Object.fromEntries(TEAM_ROLES) as Record<TeamRole, string>;

export type TaskStatus = "todo" | "doing" | "review" | "done" | "rejected" | "blocked";

/** チームのメンバー。console は Nexarch のコンソール（session_id）、headless は CLI と接続だけ（画面なし。id）。
 * key は担当（assignee）に入れる識別子（サーバーが付ける）。 */
export interface TeamMember {
  session_id?: string;
  id?: string;
  key?: string;
  kind?: "console" | "headless";
  cli?: string;
  account?: string;
  label?: string;
  role: TeamRole;
}
export const memberKey = (m: TeamMember): string => m.key ?? m.id ?? m.session_id ?? "";

export type OnLimit = "wait" | "handoff";

export interface RunUsage { input_tokens: number; output_tokens: number; cost_usd: number }
/** ヘッドレスの実行（今の実行）。 */
export interface TaskRun {
  run_id: string;
  status: "running" | "finished" | "limited" | "failed" | "stopped" | "interrupted";
  member: string;
  cli: string;
  started_at: number;
  ended_at?: number;
  elapsed?: number;
  last_line: string;
  progress: string[];
  usage: RunUsage | null;
  tail: string;
  exit_code: number | null;
  reset_at?: number | null;
}
/** 実際にやった担い手の記録（何回目・誰・結果・時間・使用量）。 */
export interface TaskRunRecord {
  n: number;
  kind: "headless" | "console";
  member: string;
  cli: string;
  account: string;
  label: string;
  started_at: number;
  ended_at: number | null;
  seconds: number | null;
  result: string;
  usage: RunUsage | null;
}

export interface TaskReport { summary: string; changed_files: string[]; tests: string; verify?: string; remaining: string }

export interface CheckRow { cmd: string; exit_code: number; seconds: number; tail: string }
export interface ChecksResult {
  status: "running" | "passed" | "failed" | "error";
  ran_at: number;
  results: CheckRow[];
  message?: string;
}

export interface TeamTask {
  id: number;
  title: string;
  goal: string;
  acceptance: string;
  files: string[];
  /** 確認のコマンド（Nexarch が担当の作業フォルダで実行して合否を決める）。 */
  checks?: string[];
  /** 確認の結果。checks が無いタスクには無い。 */
  checks_result?: ChecksResult;
  /** 確認が落ちて自動で直させた回数（2 回まで）。 */
  attempts?: number;
  assignee: string;
  status: TaskStatus;
  report: TaskReport | null;
  created_at: number;
  updated_at: number;
  history: { at: number; event: string; by: string }[];
  /** 担当ファイルが他の未完了のタスクと重なる警告。 */
  warnings: string[];
  /** 担当のコンソールの状態。"closed" は閉じている、"" は担当なし。 */
  assignee_state?: CommanderState | "closed" | "";
  /** 作業中のタスクで、担当の画面に「完了報告:」がある。 */
  report_ready?: boolean;
  /** 報告があるが確認の結果が無い（または試験が空・なし）。 */
  unverified?: boolean;
  /** 見張りが「報告なしで止まった」と見つけた時刻。 */
  stalled_at?: number;
  /** 見張りが「上限で止まった」と見つけた時刻。 */
  limited_at?: number;
  /** ヘッドレスの今の実行。 */
  run?: TaskRun;
  runs?: TaskRunRecord[];
  /** 回復を待つ時刻（epoch 秒）。 */
  wait_until?: number;
}

export interface Team {
  id: string;
  name: string;
  created_at: number;
  members: TeamMember[];
  tasks: TeamTask[];
  /** 見張りの通知（既定オン）。 */
  notify?: boolean;
  /** チームの前提。送り込みの文面の先頭に付く。 */
  brief?: string;
  /** 送る前に担当の会話をリセットする（既定 true）。 */
  reset_before_dispatch?: boolean;
  /** 上限で止まったとき: handoff（別の担い手へ引き継ぐ・既定）／wait（回復を待つ）。 */
  on_limit?: OnLimit;
  /** 担い手の候補の順番（メンバーの key）。空なら既定の順。 */
  fallback?: string[];
  max_parallel?: number;
  local_parallel?: number;
  auto_run?: boolean;
  /** チームの作業場所（CodeDEV の team-日時）。 */
  workdir?: string;
  workdir_folder?: string;
  workdir_name?: string;
  parallel?: { running: number; max: number; waiting: number; local_max: number };
}

export interface TaskFields {
  title?: string;
  goal?: string;
  acceptance?: string;
  files?: string[];
  checks?: string[];
  assignee?: string;
  status?: TaskStatus;
}

const teamUrl = (id: string) => `/commander/teams/${encodeURIComponent(id)}`;
const taskUrl = (id: string, task: number) => `${teamUrl(id)}/tasks/${task}`;

export async function fetchTeams(): Promise<Team[]> {
  return (await api<{ teams: Team[] }>("/commander/teams")).teams;
}
export async function createTeam(name: string): Promise<Team> {
  return api<Team>("/commander/teams", { method: "POST", json: { name } });
}
export async function updateTeam(
  id: string,
  fields: {
    name?: string; brief?: string; reset_before_dispatch?: boolean; on_limit?: OnLimit; fallback?: string[];
    max_parallel?: number; local_parallel?: number; auto_run?: boolean;
  },
): Promise<Team> {
  return api<Team>(teamUrl(id), { method: "PUT", json: fields });
}
export async function setTeamNotify(id: string, notify: boolean): Promise<Team> {
  return api<Team>(`${teamUrl(id)}/notify`, { method: "PUT", json: { notify } });
}
export async function deleteTeam(id: string): Promise<void> {
  await api(teamUrl(id), { method: "DELETE" });
}
export async function setTeamMembers(id: string, members: TeamMember[]): Promise<Team> {
  return api<Team>(`${teamUrl(id)}/members`, { method: "PUT", json: { members } });
}
export async function createTask(id: string, fields: TaskFields): Promise<TeamTask> {
  return api<TeamTask>(`${teamUrl(id)}/tasks`, { method: "POST", json: fields });
}
export async function updateTask(id: string, task: number, fields: TaskFields): Promise<TeamTask> {
  return api<TeamTask>(taskUrl(id, task), { method: "PUT", json: fields });
}
export async function deleteTask(id: string, task: number): Promise<void> {
  await api(taskUrl(id, task), { method: "DELETE" });
}
export async function dispatchTask(id: string, task: number): Promise<TeamTask> {
  return api<TeamTask>(`${taskUrl(id, task)}/dispatch`, { method: "POST" });
}
export async function stopTask(id: string, task: number): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>(`${taskUrl(id, task)}/stop`, { method: "POST" });
}
export async function collectTask(id: string, task: number): Promise<TeamTask> {
  return api<TeamTask>(`${taskUrl(id, task)}/collect`, { method: "POST" });
}
export async function putTaskReport(id: string, task: number, report: TaskReport): Promise<TeamTask> {
  return api<TeamTask>(`${taskUrl(id, task)}/report`, { method: "PUT", json: report });
}
