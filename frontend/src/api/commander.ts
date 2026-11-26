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
}

/** アカウントの見せ方: 画面の名前 → 接続の見せ方 → 既定／フォルダの名前。 */
export function accountTitle(name: string, label?: string, long = false, title?: string): string {
  if (label) return label;
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
  commander_label?: string;
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
  commander_created_by?: string;
}

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
  cwd: string;
  count: number;
  label: string;
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

export async function fetchConsoles(): Promise<CommanderConsole[]> {
  return (await api<{ consoles: CommanderConsole[] }>("/commander/consoles")).consoles;
}

export async function openConsoles(body: OpenConsolesBody): Promise<ConsolesOpenResult> {
  return api<ConsolesOpenResult>("/commander/consoles", { method: "POST", json: body });
}

export async function closeConsole(id: string): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>(`/commander/consoles/${encodeURIComponent(id)}`, { method: "DELETE" });
}
