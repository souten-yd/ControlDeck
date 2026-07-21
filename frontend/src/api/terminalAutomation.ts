import { api } from "./client";

export interface SnippetVariable {
  name: string;
  label: string;
  default: string;
  required: boolean;
}

export interface TerminalSnippet {
  id: number;
  name: string;
  description: string;
  content: string;
  variables: SnippetVariable[];
  tags: string[];
  created_at: string;
  updated_at: string;
}

export type AutomationMode = "detached" | "terminal";
export type ConditionType = "always" | "shell_ready" | "program_equals";

export interface ComposePayload {
  snippet_ids: number[];
  parameters: Record<string, string>;
  mode: AutomationMode;
  target_session_id?: string | null;
  working_directory: string;
  condition_type: ConditionType;
  condition_value: string;
  timeout_seconds: number;
}

export interface ComposePreview {
  command: string;
  command_bytes: number;
  working_directory: string;
  snippets: { id: number; name: string }[];
  condition: {
    ready: boolean;
    reason: string;
    session?: Record<string, unknown> | null;
  };
}

export interface TerminalAutomationRun {
  id: number;
  schedule_id?: number | null;
  snippet_ids: number[];
  mode: AutomationMode;
  target_session_id?: string | null;
  working_directory: string;
  condition_type: ConditionType;
  condition_value: string;
  timeout_seconds: number;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED" | "TIMED_OUT";
  unit_name?: string | null;
  exit_code?: number | null;
  error: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface TerminalAutomationSchedule extends ComposePayload {
  id: number;
  name: string;
  recurrence: "once" | "daily" | "weekly" | "biweekly";
  next_run_at: string;
  timezone: string;
  run_if_missed: boolean;
  enabled: boolean;
  status: string;
  last_run_at?: string | null;
  last_result: string;
  created_at: string;
  updated_at: string;
}

export const terminalAutomationApi = {
  snippets: () => api<{ snippets: TerminalSnippet[] }>("/terminal-automation/snippets"),
  createSnippet: (body: Omit<TerminalSnippet, "id" | "created_at" | "updated_at">) =>
    api<TerminalSnippet>("/terminal-automation/snippets", { method: "POST", json: body }),
  updateSnippet: (id: number, body: Partial<Omit<TerminalSnippet, "id" | "created_at" | "updated_at">>) =>
    api<TerminalSnippet>(`/terminal-automation/snippets/${id}`, { method: "PATCH", json: body }),
  deleteSnippet: (id: number) => api(`/terminal-automation/snippets/${id}`, { method: "DELETE" }),
  preview: (body: ComposePayload) => api<ComposePreview>("/terminal-automation/preview", {
    method: "POST", json: body,
  }),
  startRun: (body: ComposePayload) => api<TerminalAutomationRun>("/terminal-automation/runs", {
    method: "POST", json: body,
  }),
  runs: () => api<{ runs: TerminalAutomationRun[] }>("/terminal-automation/runs?limit=30"),
  output: (id: number) => api<{ output: string; available: boolean }>(`/terminal-automation/runs/${id}/output`),
  schedules: () => api<{ schedules: TerminalAutomationSchedule[] }>("/terminal-automation/schedules"),
  createSchedule: (body: ComposePayload & {
    name: string;
    recurrence: TerminalAutomationSchedule["recurrence"];
    next_run_at: string;
    timezone: string;
    run_if_missed: boolean;
  }) => api<TerminalAutomationSchedule>("/terminal-automation/schedules", {
    method: "POST", json: body,
  }),
  updateSchedule: (id: number, body: Partial<TerminalAutomationSchedule>) =>
    api<TerminalAutomationSchedule>(`/terminal-automation/schedules/${id}`, {
      method: "PATCH", json: body,
    }),
  deleteSchedule: (id: number) => api(`/terminal-automation/schedules/${id}`, { method: "DELETE" }),
  runScheduleNow: (id: number) => api<TerminalAutomationRun>(
    `/terminal-automation/schedules/${id}/run-now`, { method: "POST" },
  ),
};
