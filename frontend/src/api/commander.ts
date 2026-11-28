/** AI Commander（/commander）の API 呼び出し。Terminal のセッション（tmux）を印つきで使う。 */
import { api, ApiError } from "./client";

/** 閉じる・ロックの失敗を画面の日本語にする（409 はロック中の決まりの文言）。 */
export function commanderErrorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 409) return "ロック中です。先にロックを外してください";
  return error instanceof Error ? error.message : fallback;
}

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
/** 新しいアカウントの内部の名前（フォルダ名。英小文字・数字・-）。使われていない account-2, account-3… を返す。
 *  表示はログインしたメールアドレス（login_name）なので、利用者は名前を入れない（2026-11-28）。 */
export function nextAccountName(existing: string[]): string {
  const used = new Set(existing);
  for (let n = 2; n < 1000; n += 1) {
    const name = `account-${n}`;
    if (!used.has(name)) return name;
  }
  return `account-${Date.now() % 100000}`;
}

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
  /** ロック中は ControlDeck の画面から閉じられない（外から消されたら検知して開き直す）。 */
  commander_locked?: boolean;
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

/** 担い手の指定。コンソール（kind・cli・account）とモデル（model・effort）を別々に持つ。空の model・effort は CLI の既定。 */
export interface Endpoint {
  kind: "headless" | "console";
  cli: string;
  account: string;
  model: string;
  effort: string;
}
export const emptyEndpoint = (): Endpoint => ({ kind: "headless", cli: "claude", account: "default", model: "", effort: "" });

/** 役割（実装・レビューなど）。count は同時に呼ぶ最大数（1〜4）。chain は画面用（primary → secondary の順の今の状態）。 */
export interface TeamRoleDef {
  id: string;
  name: string;
  count: number;
  note: string;
  primary: Endpoint | null;
  secondary: Endpoint[];
  last_index?: number;
  chain?: { key: string; label: string; recovers_at: number | null }[];
  /** 今使える最初の endpoint の位置（0 = プライマリ）。全部回復待ちなら null。 */
  active_index?: number | null;
  running?: number;
  on_secondary?: boolean;
  primary_recovers_at?: number | null;
}

/** 役割のモデルの選択肢（サーバーが使えるモデルを前もって抜き出したもの）。
 * 選ぶと CLI・アカウント（接続）・モデル・考えるレベルがまとめて決まる。group は表示のまとまり。 */
export type ModelChoiceGroup = "local" | "opencode" | "openrouter" | "claude" | "codex" | "api" | "other";
export interface ModelChoice {
  id: string;
  label: string;
  group: ModelChoiceGroup | string;
  endpoint: Endpoint;
  efforts?: string[];
}
export interface ModelChoices {
  choices: ModelChoice[];
  note?: string;
}
export async function fetchModelChoices(): Promise<ModelChoices> {
  return api<ModelChoices>("/commander/model-choices");
}
export const MODEL_GROUP_LABEL: Record<string, string> = {
  local: "ローカル", opencode: "OpenCode（無料・Go）", openrouter: "OpenRouter",
  claude: "Claude", codex: "Codex", api: "API", other: "その他",
};
/** まとまりの並び（画面の選択肢の順）。知らないまとまりは後ろへ。 */
export const MODEL_GROUP_ORDER: readonly string[] = ["local", "opencode", "openrouter", "claude", "codex", "api", "other"];

export interface ModelInfo { id: string; label: string; efforts: string[]; default_effort: string }
export interface ModelsInfo {
  cli: string;
  account: string;
  models: ModelInfo[];
  efforts: string[];
  free_input: boolean;
  default_model: string;
  source: string;
  note: string;
}
export async function fetchModels(cli: string, account: string): Promise<ModelsInfo> {
  return api<ModelsInfo>(`/commander/models?cli=${encodeURIComponent(cli)}&account=${encodeURIComponent(account)}`);
}

export interface CommanderSettings {
  resume_message: string;
  default_resume_message: string;
  /** 外から閉じられたロック中のコンソールを自動で開き直す（既定 true）。 */
  auto_restore?: boolean;
  default_leader?: Endpoint | null;
  default_roles?: TeamRoleDef[];
  /** チームの保存先（親フォルダ）。空なら未設定（CodeDEV に自動の作業場所）。 */
  team_root?: string;
}

export interface TeamDefaultsBody { default_leader?: Endpoint | null; default_roles?: TeamRoleDef[]; team_root?: string }
export async function putTeamDefaults(body: TeamDefaultsBody): Promise<CommanderSettings> {
  return api<CommanderSettings>("/commander/settings/team-defaults", { method: "PUT", json: body });
}

export async function fetchCommanderSettings(): Promise<CommanderSettings> {
  return api<CommanderSettings>("/commander/settings");
}

export async function putCommanderSettings(body: { resume_message?: string; auto_restore?: boolean }): Promise<CommanderSettings> {
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

/** ロックの切り替え。ロック中は閉じる（単体・一括）が 409 になる。 */
export async function setConsoleLock(id: string, locked: boolean): Promise<{ ok: boolean; locked: boolean }> {
  return api<{ ok: boolean; locked: boolean }>(`/commander/consoles/${encodeURIComponent(id)}/lock`, {
    method: "PUT",
    json: { locked },
  });
}

/** ロックしていないコンソールをまとめて閉じる。ロック中は飛ばし、その数を返す。 */
export async function closeAllConsoles(): Promise<{ closed: number; skipped_locked: number }> {
  return api<{ closed: number; skipped_locked: number }>("/commander/consoles", { method: "DELETE" });
}

// ---- AI チーム（T1）: タスク板と、担当のコンソールへの送り込み ----

export type TeamRole = "leader" | "developer" | "reviewer" | "researcher" | "member";
export const TEAM_ROLES: ReadonlyArray<readonly [TeamRole, string]> = [
  ["leader", "リーダー"], ["developer", "開発"], ["reviewer", "レビュー"], ["researcher", "調査"], ["member", "メンバー"],
];
export const ROLE_LABEL: Record<TeamRole, string> = Object.fromEntries(TEAM_ROLES) as Record<TeamRole, string>;

export type TaskStatus = "todo" | "doing" | "review" | "done" | "rejected" | "blocked" | "dropped";

/** チームのメンバー。console は Nexarch のコンソール（session_id）、headless は CLI と接続だけ（画面なし。id）。
 * key は担当（assignee）に入れる識別子（サーバーが付ける）。 */
export interface TeamMember {
  /** わかりやすいモデル名（例 GLM-5.3・Codex・ローカル LLM） */
  model_label?: string;
  session_id?: string;
  id?: string;
  key?: string;
  kind?: "console" | "headless";
  cli?: string;
  account?: string;
  label?: string;
  /** codex で、この機械ではサンドボックスが使えない（danger-full-access で動かす）。 */
  no_sandbox?: boolean;
  role: TeamRole;
  model?: string;
  effort?: string;
  /** 役割から自動で作った枠（画面では役割の下にまとめる）。 */
  auto?: boolean;
  role_id?: string;
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
  /** "none" = サンドボックスなしで動かした（この機械では使えない）。 */
  sandbox?: string;
  failed_ops?: number;
  /** 例: 「失敗した操作 2 件」。 */
  failed_note?: string;
  reason?: string;
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
  sandbox?: string;
}

export interface TaskReport { summary: string; changed_files: string[]; tests: string; verify?: string; remaining: string }

/** 確認のコマンド。cwd は作業場所の中の相対パス（省略なら作業場所の直下）。 */
export interface TaskCheck { command: string; cwd?: string }
export type CheckSpec = string | TaskCheck;
/** 確認のコマンドの本文（画面・実行結果の照合に使う）。 */
export const checkCommand = (c: CheckSpec): string => (typeof c === "string" ? c : c.command);
/** 確認のコマンドの表示（作業フォルダがあれば添える）。 */
export const checkText = (c: CheckSpec): string => {
  const command = checkCommand(c);
  return typeof c === "string" || !c.cwd ? command : `${command}（${c.cwd}）`;
};

export interface CheckRow { cmd: string; exit_code: number; seconds: number; tail: string }
export interface ChecksResult {
  status: "running" | "passed" | "failed" | "error";
  ran_at: number;
  results: CheckRow[];
  message?: string;
}

export interface TeamTask {
  /** 担当した（担当している）モデルのわかりやすい名前（最後の実行の担い手） */
  model_label?: string;
  id: number;
  title: string;
  goal: string;
  acceptance: string;
  files: string[];
  /** 確認のコマンド（Nexarch が担当の作業フォルダで実行して合否を決める）。文字列か、作業フォルダ付きの指定。 */
  checks?: CheckSpec[];
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
  /** 役割（TeamRoleDef.id）。 */
  role?: string;
  /** "no_handoff" = 上限で止まり、引き継ぎ先が無い（引き継ぎ先を選ぶと再開）。 */
  blocked_reason?: string;
  /** T3c: チームリーダーが振った仕事。段（explore・implement・review・hard）・計画の項目・worktree のブランチ。 */
  leader_managed?: boolean;
  tier?: LeaderTier;
  item_id?: string;
  branch?: string;
}

export type LeaderTier = "explore" | "implement" | "review" | "hard";
export interface PlanItem {
  id: string; title: string; tier: LeaderTier; depends_on: string[]; files: string[]; checks: string[];
  status: "planned" | "delegated" | "done" | "integrated"; task_id: number | null; removed?: boolean;
}
export interface TimelineRow { at: number; kind: string; text: string }
export interface LeaderQuestion { id: string; question: string; options: string[]; asked_at: number; answer: string | null; answered_at: number | null }
export interface TeamResult {
  summary: string; outcome: "done" | "blocked"; finished_at: number; remaining: string[];
  checks: { status: "passed" | "failed" | "error"; ran_at: number; commands: string[] } | null;
  changes: { files: string[]; count: number; added: number; removed: number } | null;
  usage: { input_tokens: number; output_tokens: number; cost_usd: number };
  can_land: boolean; landed: boolean; team_branch: string; base_branch: string;
}
export interface McpLogRow { at: number; tool: string; ok: boolean; seconds: number }

/** チームに添える資料。path だけを持ち、中身はチームリーダーが必要なときに読む。 */
export interface TeamInput { path: string; source?: string; label?: string }

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
  /** 資料（/data1tb・/data2tb のファイル・フォルダ）。チームリーダーにはパスの一覧だけを渡す。 */
  inputs?: TeamInput[];
  /** 閉じたチーム（ローカル LLM 以外の担い手・外部のモデル・Web の調査を断る）。既定は切。 */
  local_only?: boolean;
  /** ControlDeck の起動のとき、再起動で中断されたチームリーダーを自動で再開する（既定は入）。 */
  auto_resume?: boolean;
  /** 送る前に担当の会話をリセットする（既定 true）。 */
  reset_before_dispatch?: boolean;
  /** 上限で止まったとき: handoff（別の担い手へ引き継ぐ・既定）／wait（回復を待つ）。 */
  on_limit?: OnLimit;
  /** 担い手の候補の順番（メンバーの key）。空なら既定の順。 */
  fallback?: string[];
  max_parallel?: number;
  local_parallel?: number;
  auto_run?: boolean;
  /** codex のサンドボックスがこの機械で使えないとき、サンドボックスなしで動かす（既定 true。false なら止める）。 */
  codex_without_sandbox?: boolean;
  /** チームの作業場所（CodeDEV の team-日時）。 */
  workdir?: string;
  workdir_folder?: string;
  workdir_name?: string;
  parallel?: { running: number; max: number; waiting: number; local_max: number };
  roles?: TeamRoleDef[];
  /** 任命されたチームリーダー。無ければ既定のチームリーダー（leader_effective）。 */
  leader?: Endpoint | null;
  leader_effective?: Endpoint;
  leader_run?: {
    status: "running" | "done" | "failed" | "blocked" | "stopped"; started_at?: number; endpoint?: string; tasks?: number; reason?: string;
    dropped?: string[]; goal?: string; resumes?: number; n?: number;
    finish?: { summary: string; outcome: "done" | "blocked"; at?: number } | null;
    model_label?: string;
  };
  created_by?: string;
  /** T3c */
  plan?: { markdown: string; items: PlanItem[]; updated_at: number };
  timeline?: TimelineRow[];
  questions?: LeaderQuestion[];
  requests?: { at: number; text: string }[];
  result?: TeamResult;
  mcp_log?: McpLogRow[];
  leader_runs?: { status: string; endpoint: string; seconds?: number; reason?: string; ended_at?: number }[];
  usage_total?: { input_tokens: number; output_tokens: number; cost_usd: number };
  git?: { enabled?: boolean; origin_was_git?: boolean; base_branch?: string; team_branch?: string; reason?: string };
  autonomy?: "high" | "ask";
  web_research?: boolean;
  git_init?: boolean;
}

export interface TaskFields {
  title?: string;
  goal?: string;
  acceptance?: string;
  files?: string[];
  checks?: CheckSpec[];
  assignee?: string;
  status?: TaskStatus;
  role?: string;
}

const teamUrl = (id: string) => `/commander/teams/${encodeURIComponent(id)}`;
const taskUrl = (id: string, task: number) => `${teamUrl(id)}/tasks/${task}`;

export async function fetchTeams(): Promise<Team[]> {
  return (await api<{ teams: Team[] }>("/commander/teams")).teams;
}
export async function createTeam(name: string): Promise<Team> {
  return api<Team>("/commander/teams", { method: "POST", json: { name } });
}
/** 目標からチームを作る（チームリーダーが計画する。結果は一覧の leader_run で見る）。workdir が空なら新しい自動の作業場所。 */
export async function createAutoTeam(goal: string, workdir = "", name = "", inputPaths: string[] = []): Promise<Team> {
  return api<Team>("/commander/teams/auto", { method: "POST", json: { goal, workdir, name, ...(inputPaths.length ? { input_paths: inputPaths } : {}) } });
}
/** 既存のチームのチームリーダーに一文を頼む（計画を作って板へ足す。結果は一覧の leader_run で見る）。 */
export async function askLeader(id: string, goal: string, inputPaths: string[] = []): Promise<Team> {
  return api<Team>(`${teamUrl(id)}/leader/run`, { method: "POST", json: { goal, ...(inputPaths.length ? { input_paths: inputPaths } : {}) } });
}
export async function answerLeaderQuestion(id: string, questionId: string, answer: string): Promise<void> {
  await api(`${teamUrl(id)}/questions/${encodeURIComponent(questionId)}/answer`, { method: "POST", json: { answer } });
}
export async function resumeLeader(id: string): Promise<Team> {
  return api<Team>(`${teamUrl(id)}/leader/resume`, { method: "POST" });
}
export async function stopLeader(id: string): Promise<void> {
  await api(`${teamUrl(id)}/leader/stop`, { method: "POST" });
}
/** 「取り込む」: チームのブランチを元のブランチへ（元から git の作業場所だけ）。 */
export async function landTeam(id: string): Promise<Team> {
  return api<Team>(`${teamUrl(id)}/leader/land`, { method: "POST" });
}
export async function setTeamRoles(id: string, roles: TeamRoleDef[]): Promise<Team> {
  return api<Team>(`${teamUrl(id)}/roles`, { method: "PUT", json: { roles } });
}
export async function setTeamLeader(id: string, leader: Endpoint | null): Promise<Team> {
  return api<Team>(`${teamUrl(id)}/leader`, { method: "PUT", json: { leader } });
}
/** 引き継ぎ先が無くて止まったタスクへ、選んだ担い手で引き継いで再開する。 */
export async function chooseHandoff(id: string, task: number, endpoint: Endpoint, addToSecondary: boolean): Promise<TeamTask> {
  return api<TeamTask>(`${teamUrl(id)}/tasks/${task}/handoff`, { method: "POST", json: { endpoint, add_to_secondary: addToSecondary } });
}
export async function updateTeam(
  id: string,
  fields: {
    name?: string; brief?: string; reset_before_dispatch?: boolean; on_limit?: OnLimit; fallback?: string[];
    max_parallel?: number; local_parallel?: number; auto_run?: boolean; codex_without_sandbox?: boolean;
    autonomy?: "high" | "ask"; web_research?: boolean; git_init?: boolean;
    local_only?: boolean; auto_resume?: boolean;
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
