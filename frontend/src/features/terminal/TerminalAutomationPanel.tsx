import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  terminalAutomationApi,
  type AutomationMode,
  type ComposePayload,
  type ComposePreview,
  type ConditionType,
  type TerminalAutomationRun,
  type TerminalAutomationSchedule,
  type TerminalSnippet,
} from "../../api/terminalAutomation";
import { ConfirmDialog, DropdownMenu } from "../../components/ui";
import { IconDots, IconPlay, IconPlus, IconSettings, IconX } from "../../components/icons";
import { useAuth, useToasts } from "../../stores";

interface SessionOption {
  id: string;
  program: string;
  cwd: string;
  workload: "idle" | "running";
  alive: boolean;
}

type Tab = "library" | "run" | "schedules";
const BUILT_INS = new Set(["cwd", "date", "time"]);

export default function TerminalAutomationPanel({
  sessions,
  initialSessionId,
  onClose,
}: {
  sessions: SessionOption[];
  initialSessionId?: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const canManage = useAuth((state) => state.can)("settings.manage");
  const panelRef = useRef<HTMLElement>(null);
  const [tab, setTab] = useState<Tab>(initialSessionId ? "run" : "library");
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<AutomationMode>(initialSessionId ? "terminal" : "detached");
  const [targetSessionId, setTargetSessionId] = useState(initialSessionId ?? "");
  const initialSession = sessions.find((session) => session.id === initialSessionId);
  const [workingDirectory, setWorkingDirectory] = useState(initialSession?.cwd || "~");
  const [conditionType, setConditionType] = useState<ConditionType>(
    initialSession ? (initialSession.workload === "idle" ? "shell_ready" : "program_equals") : "always",
  );
  const [conditionValue, setConditionValue] = useState(initialSession?.workload === "running" ? initialSession.program : "");
  const [timeoutSeconds, setTimeoutSeconds] = useState(3600);
  const [preview, setPreview] = useState<ComposePreview | null>(null);
  const [snippetEditor, setSnippetEditor] = useState<TerminalSnippet | "new" | null>(null);
  const [deleteSnippet, setDeleteSnippet] = useState<TerminalSnippet | null>(null);
  const [deleteSchedule, setDeleteSchedule] = useState<TerminalAutomationSchedule | null>(null);
  const [scheduleForm, setScheduleForm] = useState(false);
  const [editingScheduleId, setEditingScheduleId] = useState<number | null>(null);
  const [scheduleName, setScheduleName] = useState("");
  const [recurrence, setRecurrence] = useState<TerminalAutomationSchedule["recurrence"]>("once");
  const [nextRunLocal, setNextRunLocal] = useState(defaultLocalTime());
  const [runIfMissed, setRunIfMissed] = useState(true);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);

  useEffect(() => {
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = before;
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const snippetsQuery = useQuery({
    queryKey: ["terminal-automation", "snippets"],
    queryFn: terminalAutomationApi.snippets,
  });
  const schedulesQuery = useQuery({
    queryKey: ["terminal-automation", "schedules"],
    queryFn: terminalAutomationApi.schedules,
  });
  const runsQuery = useQuery({
    queryKey: ["terminal-automation", "runs"],
    queryFn: terminalAutomationApi.runs,
    refetchInterval: (query) => query.state.data?.runs.some((run) => ["QUEUED", "RUNNING"].includes(run.status)) ? 1500 : 5000,
  });
  const snippets = snippetsQuery.data?.snippets ?? [];
  const selected = selectedIds.map((id) => snippets.find((snippet) => snippet.id === id)).filter(Boolean) as TerminalSnippet[];
  const variables = useMemo(() => {
    const byName = new Map<string, TerminalSnippet["variables"][number]>();
    for (const snippet of selected) for (const variable of snippet.variables) if (!byName.has(variable.name)) byName.set(variable.name, variable);
    return [...byName.values()];
  }, [selected]);

  useEffect(() => {
    setParameters((current) => {
      const next: Record<string, string> = {};
      for (const variable of variables) next[variable.name] = current[variable.name] ?? variable.default;
      return next;
    });
  }, [variables]);

  const payload: ComposePayload = useMemo(() => ({
    snippet_ids: selectedIds,
    parameters,
    mode,
    target_session_id: mode === "terminal" ? targetSessionId || null : null,
    working_directory: workingDirectory,
    condition_type: mode === "terminal" ? conditionType : "always",
    condition_value: mode === "terminal" ? conditionValue : "",
    timeout_seconds: timeoutSeconds,
  }), [conditionType, conditionValue, mode, parameters, selectedIds, targetSessionId, timeoutSeconds, workingDirectory]);
  const payloadSignature = JSON.stringify(payload);
  useEffect(() => setPreview(null), [payloadSignature]);

  const reviewMutation = useMutation({
    mutationFn: () => terminalAutomationApi.preview(payload),
    onSuccess: setPreview,
    onError: (error) => show(messageOf(error), "error"),
  });
  const runMutation = useMutation({
    mutationFn: () => terminalAutomationApi.startRun(payload),
    onSuccess: (run) => {
      show(run.mode === "terminal" ? "条件確認後にTerminalへ送信します" : "独立サービスで実行を開始しました");
      setPreview(null);
      void qc.invalidateQueries({ queryKey: ["terminal-automation", "runs"] });
    },
    onError: (error) => show(messageOf(error), "error"),
  });

  const useSnippet = (snippet: TerminalSnippet) => {
    setSelectedIds((current) => current.includes(snippet.id) ? current : [...current, snippet.id]);
    setTab("run");
  };
  const chooseTarget = (sessionId: string) => {
    const session = sessions.find((item) => item.id === sessionId);
    setTargetSessionId(sessionId);
    if (session?.cwd) setWorkingDirectory(session.cwd);
    if (session?.workload === "idle") {
      setConditionType("shell_ready");
      setConditionValue("");
    } else if (session) {
      setConditionType("program_equals");
      setConditionValue(session.program);
    }
  };

  const openNewSchedule = () => {
    setEditingScheduleId(null);
    setScheduleName(selected.map((snippet) => snippet.name).join(" + ").slice(0, 128));
    setRecurrence("once");
    setNextRunLocal(defaultLocalTime());
    setRunIfMissed(true);
    setScheduleForm(true);
  };
  const editSchedule = (schedule: TerminalAutomationSchedule) => {
    setSelectedIds(schedule.snippet_ids);
    setParameters(schedule.parameters ?? {});
    setMode(schedule.mode);
    setTargetSessionId(schedule.target_session_id ?? "");
    setWorkingDirectory(schedule.working_directory);
    setConditionType(schedule.condition_type);
    setConditionValue(schedule.condition_value);
    setTimeoutSeconds(schedule.timeout_seconds);
    setEditingScheduleId(schedule.id);
    setScheduleName(schedule.name);
    setRecurrence(schedule.recurrence);
    setNextRunLocal(toLocalInput(schedule.next_run_at));
    setRunIfMissed(schedule.run_if_missed);
    setScheduleForm(true);
    setTab("run");
  };

  return createPortal(
    <div className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[2px]" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-label="Terminal snippets and automation"
        className="safe-bottom absolute inset-x-0 bottom-0 flex h-[92dvh] flex-col overflow-hidden rounded-t-3xl bg-zinc-50 shadow-2xl outline-none dark:bg-zinc-950 md:inset-y-0 md:left-auto md:h-dvh md:w-[min(620px,48vw)] md:rounded-none md:border-l md:border-zinc-200 md:dark:border-zinc-800"
      >
        <header className="safe-top shrink-0 border-b border-zinc-200 bg-white/90 px-4 pb-3 pt-4 backdrop-blur-xl dark:border-zinc-800 dark:bg-zinc-900/90 md:px-6">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-2xl bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-300"><IconSettings /></div>
            <div className="min-w-0 flex-1"><h2 className="text-base font-semibold">Snippets</h2><p className="truncate text-xs text-zinc-500">Compose once, run now or on a durable schedule</p></div>
            <button onClick={onClose} aria-label="閉じる" className="grid min-h-11 min-w-11 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"><IconX /></button>
          </div>
          <nav aria-label="Snippet sections" className="mt-4 grid grid-cols-3 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
            {(["library", "run", "schedules"] as Tab[]).map((item) => (
              <button key={item} onClick={() => setTab(item)} className={`min-h-10 rounded-lg px-2 text-xs font-semibold transition ${tab === item ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white" : "text-zinc-500"}`}>
                {item === "library" ? "Library" : item === "run" ? `Run${selectedIds.length ? ` · ${selectedIds.length}` : ""}` : `Schedules${schedulesQuery.data?.schedules.length ? ` · ${schedulesQuery.data.schedules.length}` : ""}`}
              </button>
            ))}
          </nav>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
          {tab === "library" && <LibraryTab
            snippets={snippets}
            loading={snippetsQuery.isLoading}
            search={search}
            setSearch={setSearch}
            canManage={canManage}
            onUse={useSnippet}
            onEdit={setSnippetEditor}
            onDelete={setDeleteSnippet}
          />}
          {tab === "run" && <RunTab
            snippets={snippets}
            selected={selected}
            selectedIds={selectedIds}
            setSelectedIds={setSelectedIds}
            parameters={parameters}
            setParameters={setParameters}
            mode={mode}
            setMode={setMode}
            sessions={sessions}
            targetSessionId={targetSessionId}
            chooseTarget={chooseTarget}
            workingDirectory={workingDirectory}
            setWorkingDirectory={setWorkingDirectory}
            conditionType={conditionType}
            setConditionType={setConditionType}
            conditionValue={conditionValue}
            setConditionValue={setConditionValue}
            timeoutSeconds={timeoutSeconds}
            setTimeoutSeconds={setTimeoutSeconds}
            preview={preview}
            reviewing={reviewMutation.isPending}
            running={runMutation.isPending}
            onReview={() => reviewMutation.mutate()}
            onRun={() => runMutation.mutate()}
            canManage={canManage}
            onSchedule={openNewSchedule}
            runs={runsQuery.data?.runs ?? []}
            expandedRunId={expandedRunId}
            setExpandedRunId={setExpandedRunId}
          />}
          {tab === "schedules" && <SchedulesTab
            schedules={schedulesQuery.data?.schedules ?? []}
            canManage={canManage}
            onNew={() => { setTab("run"); openNewSchedule(); }}
            onEdit={editSchedule}
            onDelete={setDeleteSchedule}
            onChanged={() => void qc.invalidateQueries({ queryKey: ["terminal-automation"] })}
          />}
        </div>

        {snippetEditor && <SnippetEditor
          snippet={snippetEditor === "new" ? null : snippetEditor}
          onClose={() => setSnippetEditor(null)}
          onSaved={() => {
            setSnippetEditor(null);
            void qc.invalidateQueries({ queryKey: ["terminal-automation", "snippets"] });
          }}
        />}
        {scheduleForm && <ScheduleEditor
          editingId={editingScheduleId}
          name={scheduleName}
          setName={setScheduleName}
          recurrence={recurrence}
          setRecurrence={setRecurrence}
          nextRunLocal={nextRunLocal}
          setNextRunLocal={setNextRunLocal}
          runIfMissed={runIfMissed}
          setRunIfMissed={setRunIfMissed}
          payload={payload}
          selectedCount={selectedIds.length}
          onClose={() => setScheduleForm(false)}
          onSaved={() => {
            setScheduleForm(false);
            setEditingScheduleId(null);
            setTab("schedules");
            void qc.invalidateQueries({ queryKey: ["terminal-automation", "schedules"] });
          }}
        />}
      </aside>

      {deleteSnippet && <ConfirmDialog
        title="Snippetを削除しますか？"
        message={`「${deleteSnippet.name}」を削除します。Scheduleで使用中の場合は削除できません。`}
        confirmLabel="削除する"
        onClose={() => setDeleteSnippet(null)}
        onConfirm={() => void terminalAutomationApi.deleteSnippet(deleteSnippet.id).then(() => {
          setSelectedIds((ids) => ids.filter((id) => id !== deleteSnippet.id));
          setDeleteSnippet(null);
          show("Snippetを削除しました");
          void qc.invalidateQueries({ queryKey: ["terminal-automation", "snippets"] });
        }).catch((error) => show(messageOf(error), "error"))}
      />}
      {deleteSchedule && <ConfirmDialog
        title="Scheduleを削除しますか？"
        message={`「${deleteSchedule.name}」のtimerを停止して削除します。過去のRun履歴は残ります。`}
        confirmLabel="削除する"
        onClose={() => setDeleteSchedule(null)}
        onConfirm={() => void terminalAutomationApi.deleteSchedule(deleteSchedule.id).then(() => {
          setDeleteSchedule(null);
          show("Scheduleを削除しました");
          void qc.invalidateQueries({ queryKey: ["terminal-automation", "schedules"] });
        }).catch((error) => show(messageOf(error), "error"))}
      />}
    </div>,
    document.body,
  );
}

function LibraryTab({ snippets, loading, search, setSearch, canManage, onUse, onEdit, onDelete }: {
  snippets: TerminalSnippet[];
  loading: boolean;
  search: string;
  setSearch: (value: string) => void;
  canManage: boolean;
  onUse: (snippet: TerminalSnippet) => void;
  onEdit: (snippet: TerminalSnippet | "new") => void;
  onDelete: (snippet: TerminalSnippet) => void;
}) {
  const filtered = snippets.filter((snippet) => `${snippet.name} ${snippet.description} ${snippet.tags.join(" ")}`.toLowerCase().includes(search.toLowerCase()));
  return <section aria-label="Snippet library">
    <div className="flex gap-2">
      <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search snippets" aria-label="Search snippets" className="min-h-11 min-w-0 flex-1 rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900" />
      {canManage && <button onClick={() => onEdit("new")} className="inline-flex min-h-11 items-center gap-1.5 rounded-xl bg-accent-600 px-3 text-sm font-semibold text-white hover:bg-accent-700"><IconPlus /> Add</button>}
    </div>
    {loading ? <p className="py-10 text-center text-sm text-zinc-400">Loading…</p> : filtered.length === 0 ? <div className="mt-4 rounded-2xl border border-dashed border-zinc-300 p-8 text-center dark:border-zinc-700"><p className="text-sm text-zinc-500">{snippets.length ? "No matching snippets" : "Save common commands or Codex prompts here."}</p>{canManage && !snippets.length && <button onClick={() => onEdit("new")} className="mt-3 min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">Create first snippet</button>}</div> : <ul className="mt-4 grid gap-3">
      {filtered.map((snippet) => <li key={snippet.id} className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-start gap-3"><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold">{snippet.name}</h3><p className="mt-1 line-clamp-2 text-xs text-zinc-500">{snippet.description || snippet.content}</p></div>{canManage && <DropdownMenu ariaLabel={`${snippet.name} menu`} trigger={<IconDots />} items={[{ label: "Edit", onSelect: () => onEdit(snippet) }, { label: "Delete", danger: true, onSelect: () => onDelete(snippet) }]} />}</div>
        <code className="mt-3 block max-h-16 overflow-hidden whitespace-pre-wrap break-words rounded-xl bg-zinc-50 p-2 text-[11px] text-zinc-600 dark:bg-zinc-950 dark:text-zinc-300">{snippet.content}</code>
        <div className="mt-3 flex items-center gap-2">{snippet.tags.slice(0, 3).map((tag) => <span key={tag} className="rounded-full bg-zinc-100 px-2 py-1 text-[10px] text-zinc-500 dark:bg-zinc-800">{tag}</span>)}<span className="flex-1" /><button onClick={() => onUse(snippet)} className="min-h-11 rounded-xl bg-accent-50 px-4 text-sm font-semibold text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-300">Use</button></div>
      </li>)}
    </ul>}
  </section>;
}

function RunTab(props: {
  snippets: TerminalSnippet[]; selected: TerminalSnippet[]; selectedIds: number[]; setSelectedIds: (ids: number[]) => void;
  parameters: Record<string, string>; setParameters: (value: Record<string, string>) => void;
  mode: AutomationMode; setMode: (value: AutomationMode) => void; sessions: SessionOption[];
  targetSessionId: string; chooseTarget: (value: string) => void; workingDirectory: string; setWorkingDirectory: (value: string) => void;
  conditionType: ConditionType; setConditionType: (value: ConditionType) => void; conditionValue: string; setConditionValue: (value: string) => void;
  timeoutSeconds: number; setTimeoutSeconds: (value: number) => void; preview: ComposePreview | null;
  reviewing: boolean; running: boolean; onReview: () => void; onRun: () => void; canManage: boolean; onSchedule: () => void;
  runs: TerminalAutomationRun[]; expandedRunId: number | null; setExpandedRunId: (id: number | null) => void;
}) {
  const variables = props.selected.flatMap((snippet) => snippet.variables).filter((item, index, all) => all.findIndex((other) => other.name === item.name) === index);
  const move = (index: number, delta: number) => {
    const next = [...props.selectedIds];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    props.setSelectedIds(next);
  };
  return <section aria-label="Compose and run" className="space-y-4">
    <Card title="1 · Compose" subtitle="Select in the exact order to execute">
      {props.snippets.length === 0 ? <p className="text-xs text-zinc-500">Create a snippet in Library first.</p> : <div className="space-y-2">
        {props.selected.map((snippet, index) => <div key={snippet.id} className="flex min-h-11 items-center gap-2 rounded-xl border border-zinc-200 bg-zinc-50 px-3 dark:border-zinc-700 dark:bg-zinc-800/60"><span className="grid h-6 w-6 place-items-center rounded-full bg-zinc-200 text-[10px] font-bold dark:bg-zinc-700">{index + 1}</span><span className="min-w-0 flex-1 truncate text-xs font-medium">{snippet.name}</span><button aria-label={`${snippet.name} up`} onClick={() => move(index, -1)} disabled={index === 0} className="min-h-9 min-w-9 rounded-lg text-zinc-500 disabled:opacity-25">↑</button><button aria-label={`${snippet.name} down`} onClick={() => move(index, 1)} disabled={index === props.selected.length - 1} className="min-h-9 min-w-9 rounded-lg text-zinc-500 disabled:opacity-25">↓</button><button aria-label={`${snippet.name} remove`} onClick={() => props.setSelectedIds(props.selectedIds.filter((id) => id !== snippet.id))} className="grid min-h-9 min-w-9 place-items-center rounded-lg text-zinc-400 hover:text-red-600"><IconX /></button></div>)}
        <select aria-label="Add snippet to composition" value="" onChange={(event) => event.target.value && props.setSelectedIds([...props.selectedIds, Number(event.target.value)])} className="min-h-11 w-full rounded-xl border border-dashed border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"><option value="">+ Add snippet</option>{props.snippets.filter((item) => !props.selectedIds.includes(item.id)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
      </div>}
      {variables.length > 0 && <div className="mt-4 grid gap-3 sm:grid-cols-2">{variables.map((variable) => <label key={variable.name} className="text-xs font-medium text-zinc-600 dark:text-zinc-300">{variable.label || variable.name}{variable.required && " *"}<input value={props.parameters[variable.name] ?? ""} onChange={(event) => props.setParameters({ ...props.parameters, [variable.name]: event.target.value })} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 bg-white px-3 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-950" /></label>)}</div>}
    </Card>
    <Card title="2 · Target" subtitle="Detached is safer; session input requires a live-state condition">
      <div className="grid grid-cols-2 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800"><button onClick={() => props.setMode("detached")} className={`min-h-10 rounded-lg text-xs font-semibold ${props.mode === "detached" ? "bg-white shadow-sm dark:bg-zinc-700" : "text-zinc-500"}`}>Detached run</button><button onClick={() => props.setMode("terminal")} className={`min-h-10 rounded-lg text-xs font-semibold ${props.mode === "terminal" ? "bg-white shadow-sm dark:bg-zinc-700" : "text-zinc-500"}`}>Send to session</button></div>
      {props.mode === "terminal" && <><label className="mt-3 block text-xs font-medium">Terminal<select value={props.targetSessionId} onChange={(event) => props.chooseTarget(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950"><option value="">Choose a session</option>{props.sessions.filter((session) => session.alive).map((session) => <option key={session.id} value={session.id}>{session.program} · {session.cwd} · #{session.id}</option>)}</select></label><div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium">Only when<select value={props.conditionType} onChange={(event) => props.setConditionType(event.target.value as ConditionType)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950"><option value="shell_ready">Shell is ready</option><option value="program_equals">Program matches</option><option value="always">Always (not recommended)</option></select></label>{props.conditionType === "program_equals" && <label className="text-xs font-medium">Expected program<input value={props.conditionValue} onChange={(event) => props.setConditionValue(event.target.value)} placeholder="codex" className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 bg-white px-3 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-950" /></label>}</div><p className="mt-2 rounded-xl bg-amber-50 p-3 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">This mode pastes into an interactive program. If its state changed, Control Deck skips the run instead of typing blindly.</p></>}
      <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_140px]"><label className="text-xs font-medium">Working directory<input value={props.workingDirectory} onChange={(event) => props.setWorkingDirectory(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 bg-white px-3 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-950" /></label><label className="text-xs font-medium">Timeout<input type="number" min={1} max={86400} value={props.timeoutSeconds} onChange={(event) => props.setTimeoutSeconds(Number(event.target.value))} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm tabular-nums dark:border-zinc-700 dark:bg-zinc-950" /></label></div>
    </Card>
    <Card title="3 · Review" subtitle="Nothing runs until the expanded command and condition are reviewed">
      {props.preview ? <><div className={`mb-3 flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium ${props.preview.condition.ready ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"}`}><StatusDot status={props.preview.condition.ready ? "SUCCEEDED" : "SKIPPED"} />{props.preview.condition.reason}</div><pre data-automation-preview className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-950 p-3 font-mono text-xs text-zinc-100">{props.preview.command}</pre><p className="mt-2 text-[10px] text-zinc-400 tabular-nums">{props.preview.command_bytes} bytes · {props.preview.working_directory}</p></> : <p className="text-xs text-zinc-500">Review resolves parameters and checks the target without executing anything.</p>}
      <div className="mt-4 flex flex-wrap justify-end gap-2">{props.canManage && <button onClick={props.onSchedule} disabled={!props.preview?.condition.ready} title={props.preview?.condition.ready ? undefined : "Review the expanded command first"} className="min-h-11 rounded-xl border border-zinc-300 px-4 text-sm font-semibold text-zinc-700 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300">Schedule</button>}<button onClick={props.onReview} disabled={!props.selectedIds.length || props.reviewing} className="min-h-11 rounded-xl border border-accent-300 px-4 text-sm font-semibold text-accent-700 disabled:opacity-40 dark:border-accent-700 dark:text-accent-300">{props.reviewing ? "Checking…" : "Review"}</button><button onClick={props.onRun} disabled={!props.preview?.condition.ready || props.running} className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-accent-600 px-5 text-sm font-semibold text-white disabled:opacity-40"><IconPlay />{props.running ? "Starting…" : "Run now"}</button></div>
    </Card>
    <RecentRuns runs={props.runs} expandedRunId={props.expandedRunId} setExpandedRunId={props.setExpandedRunId} />
  </section>;
}

function SchedulesTab({ schedules, canManage, onNew, onEdit, onDelete, onChanged }: { schedules: TerminalAutomationSchedule[]; canManage: boolean; onNew: () => void; onEdit: (schedule: TerminalAutomationSchedule) => void; onDelete: (schedule: TerminalAutomationSchedule) => void; onChanged: () => void }) {
  const show = useToasts((state) => state.show);
  return <section aria-label="Automation schedules"><div className="flex items-center justify-between"><div><h3 className="text-sm font-semibold">Durable schedules</h3><p className="text-xs text-zinc-500">systemd timers continue after browser or server restarts.</p></div>{canManage && <button onClick={onNew} className="inline-flex min-h-11 items-center gap-1 rounded-xl bg-accent-600 px-3 text-sm font-semibold text-white"><IconPlus /> New</button>}</div>{schedules.length === 0 ? <div className="mt-4 rounded-2xl border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700">No schedules yet.</div> : <ul className="mt-4 space-y-3">{schedules.map((schedule) => <li key={schedule.id} className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"><div className="flex items-start gap-3"><StatusDot status={schedule.enabled ? schedule.status : "PAUSED"} /><div className="min-w-0 flex-1"><h4 className="truncate text-sm font-semibold">{schedule.name}</h4><p className="mt-1 text-xs text-zinc-500">{recurrenceLabel(schedule.recurrence)} · {schedule.mode === "terminal" ? `Session #${schedule.target_session_id}` : "Detached"}</p></div>{canManage && <DropdownMenu ariaLabel={`${schedule.name} menu`} trigger={<IconDots />} items={[{ label: "Edit", onSelect: () => onEdit(schedule) }, { label: schedule.enabled ? "Pause" : "Resume", onSelect: () => void terminalAutomationApi.updateSchedule(schedule.id, { enabled: !schedule.enabled }).then(() => { show(schedule.enabled ? "Scheduleを停止しました" : "Scheduleを再開しました"); onChanged(); }).catch((error) => show(messageOf(error), "error")) }, { label: "Delete", danger: true, onSelect: () => onDelete(schedule) }]} />}</div><div className="mt-3 flex items-center gap-3 border-t border-zinc-100 pt-3 dark:border-zinc-800"><div className="min-w-0 flex-1"><p className="text-[10px] uppercase tracking-wide text-zinc-400">Next run</p><p className="truncate text-xs font-medium tabular-nums">{schedule.enabled ? new Date(schedule.next_run_at).toLocaleString() : "Paused"}</p></div><button onClick={() => void terminalAutomationApi.runScheduleNow(schedule.id).then(() => { show("Scheduleを今すぐ実行しました"); onChanged(); }).catch((error) => show(messageOf(error), "error"))} className="min-h-11 rounded-xl bg-accent-50 px-4 text-xs font-semibold text-accent-700 dark:bg-accent-600/15 dark:text-accent-300">Run now</button></div>{schedule.last_result && <p className="mt-2 text-[10px] text-zinc-400">Last: {schedule.last_result}{schedule.last_run_at ? ` · ${new Date(schedule.last_run_at).toLocaleString()}` : ""}</p>}</li>)}</ul>}</section>;
}

function SnippetEditor({ snippet, onClose, onSaved }: { snippet: TerminalSnippet | null; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((state) => state.show);
  const [name, setName] = useState(snippet?.name ?? "");
  const [description, setDescription] = useState(snippet?.description ?? "");
  const [content, setContent] = useState(snippet?.content ?? "");
  const [tags, setTags] = useState(snippet?.tags.join(", ") ?? "");
  const variables = [...new Set((content.match(/{{\s*[A-Za-z_][A-Za-z0-9_]*\s*}}/g) ?? []).map((match) => match.replace(/[{}\s]/g, "")).filter((name) => !BUILT_INS.has(name)))];
  const mutation = useMutation({
    mutationFn: () => {
      const body = { name, description, content, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean), variables: variables.map((variable) => ({ name: variable, label: variable, default: "", required: false })) };
      return snippet ? terminalAutomationApi.updateSnippet(snippet.id, body) : terminalAutomationApi.createSnippet(body);
    },
    onSuccess: () => { show(snippet ? "Snippetを更新しました" : "Snippetを追加しました"); onSaved(); },
    onError: (error) => show(messageOf(error), "error"),
  });
  return <div className="absolute inset-0 z-20 flex flex-col bg-white dark:bg-zinc-900"><div className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800"><button onClick={onClose} className="min-h-11 rounded-xl px-3 text-sm text-zinc-500">Cancel</button><h3 className="flex-1 text-center text-sm font-semibold">{snippet ? "Edit snippet" : "New snippet"}</h3><button onClick={() => mutation.mutate()} disabled={!name.trim() || !content.trim() || mutation.isPending} className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white disabled:opacity-40">Save</button></div><div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 md:p-6"><label className="block text-xs font-medium">Name<input autoFocus value={name} onChange={(event) => setName(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-600 dark:bg-zinc-950" /></label><label className="block text-xs font-medium">Description<input value={description} onChange={(event) => setDescription(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-600 dark:bg-zinc-950" /></label><label className="block text-xs font-medium">Code or prompt<textarea data-snippet-code value={content} onChange={(event) => setContent(event.target.value)} rows={14} spellCheck={false} className="mt-1 w-full resize-y rounded-xl border border-zinc-400 bg-white p-3 font-mono text-sm leading-relaxed text-zinc-900 shadow-inner outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-600 dark:bg-zinc-950 dark:text-zinc-100" /></label><p className="rounded-xl border border-accent-200 bg-accent-50 p-3 text-xs text-accent-900 dark:border-accent-800 dark:bg-accent-950/50 dark:text-accent-200">Use <code>{"{{task}}"}</code> for a parameter. Built-ins: <code>{"{{cwd}}"}</code>, <code>{"{{date}}"}</code>, <code>{"{{time}}"}</code>.</p>{variables.length > 0 && <div data-detected-parameters className="rounded-xl border border-zinc-300 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-950/70"><p className="text-xs font-semibold text-zinc-700 dark:text-zinc-200">Detected parameters</p><div className="mt-2 flex flex-wrap gap-2">{variables.map((variable) => <code key={variable} className="rounded-lg border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-800 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100">{variable}</code>)}</div></div>}<label className="block text-xs font-medium">Tags<input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="codex, nightly" className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-600 dark:bg-zinc-950" /></label></div></div>;
}

function ScheduleEditor(props: { editingId: number | null; name: string; setName: (value: string) => void; recurrence: TerminalAutomationSchedule["recurrence"]; setRecurrence: (value: TerminalAutomationSchedule["recurrence"]) => void; nextRunLocal: string; setNextRunLocal: (value: string) => void; runIfMissed: boolean; setRunIfMissed: (value: boolean) => void; payload: ComposePayload; selectedCount: number; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((state) => state.show);
  const mutation = useMutation({
    mutationFn: () => {
      const next = new Date(props.nextRunLocal);
      if (Number.isNaN(next.getTime())) throw new Error("実行日時を入力してください");
      const body = { ...props.payload, name: props.name, recurrence: props.recurrence, next_run_at: next.toISOString(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", run_if_missed: props.runIfMissed };
      return props.editingId ? terminalAutomationApi.updateSchedule(props.editingId, body) : terminalAutomationApi.createSchedule(body);
    },
    onSuccess: () => { show(props.editingId ? "Scheduleを更新しました" : "Scheduleを追加しました"); props.onSaved(); },
    onError: (error) => show(messageOf(error), "error"),
  });
  return <div className="absolute inset-0 z-20 flex flex-col bg-white dark:bg-zinc-900"><div className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800"><button onClick={props.onClose} className="min-h-11 rounded-xl px-3 text-sm text-zinc-500">Cancel</button><h3 className="flex-1 text-center text-sm font-semibold">{props.editingId ? "Edit schedule" : "New schedule"}</h3><button onClick={() => mutation.mutate()} disabled={!props.name.trim() || !props.selectedCount || mutation.isPending} className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white disabled:opacity-40">Save</button></div><div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 md:p-6"><label className="block text-xs font-medium">Name<input autoFocus value={props.name} onChange={(event) => props.setName(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950" /></label><label className="block text-xs font-medium">First run<input type="datetime-local" value={props.nextRunLocal} onChange={(event) => props.setNextRunLocal(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 px-3 text-sm tabular-nums dark:border-zinc-700 dark:bg-zinc-950" /></label><label className="block text-xs font-medium">Repeat<select value={props.recurrence} onChange={(event) => props.setRecurrence(event.target.value as TerminalAutomationSchedule["recurrence"])} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-200 px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950"><option value="once">Once</option><option value="daily">Every day</option><option value="weekly">Every week</option><option value="biweekly">Every 2 weeks</option></select></label><label className="flex min-h-12 items-center gap-3 rounded-xl border border-zinc-200 px-3 dark:border-zinc-700"><input type="checkbox" checked={props.runIfMissed} onChange={(event) => props.setRunIfMissed(event.target.checked)} className="h-4 w-4" /><span><span className="block text-xs font-medium">Run after startup if missed</span><span className="block text-[10px] text-zinc-500">Useful when the PC was off at the planned time.</span></span></label><div className="rounded-2xl bg-zinc-50 p-4 text-xs dark:bg-zinc-950"><p className="font-semibold">Execution policy</p><p className="mt-1 text-zinc-500">{props.payload.mode === "detached" ? "Runs as an independent systemd user service." : `Sends only when the selected session condition still matches (#${props.payload.target_session_id}).`}</p><p className="mt-2 text-zinc-400">{props.selectedCount} snippet{props.selectedCount === 1 ? "" : "s"} · timeout {props.payload.timeout_seconds}s</p></div></div></div>;
}

function RecentRuns({ runs, expandedRunId, setExpandedRunId }: { runs: TerminalAutomationRun[]; expandedRunId: number | null; setExpandedRunId: (id: number | null) => void }) {
  const [outputs, setOutputs] = useState<Record<number, string>>({});
  const toggle = async (run: TerminalAutomationRun) => {
    if (expandedRunId === run.id) return setExpandedRunId(null);
    setExpandedRunId(run.id);
    if (!outputs[run.id] && run.mode === "detached" && !["QUEUED", "RUNNING"].includes(run.status)) {
      try {
        const result = await terminalAutomationApi.output(run.id);
        setOutputs((current) => ({ ...current, [run.id]: result.output || "No output" }));
      } catch {
        setOutputs((current) => ({ ...current, [run.id]: "Output is unavailable" }));
      }
    }
  };
  return <Card title="Recent activity" subtitle="Live state from durable run records">{runs.length === 0 ? <p className="text-xs text-zinc-500">No runs yet.</p> : <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">{runs.slice(0, 8).map((run) => <li key={run.id}><button onClick={() => void toggle(run)} className="flex min-h-14 w-full items-center gap-3 text-left"><StatusDot status={run.status} /><span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">Run #{run.id} · {run.mode === "terminal" ? "Session input" : "Detached"}</span><span className="block truncate text-[10px] text-zinc-400">{new Date(run.created_at).toLocaleString()}{run.error ? ` · ${run.error}` : ""}</span></span><span className="text-[10px] font-semibold text-zinc-500">{run.status}</span></button>{expandedRunId === run.id && <pre className="mb-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-zinc-950 p-3 font-mono text-[11px] text-zinc-200">{run.mode === "terminal" ? (run.status === "SKIPPED" ? run.error : "Input was sent to the target session; output remains in that terminal.") : outputs[run.id] ?? (["QUEUED", "RUNNING"].includes(run.status) ? "Running…" : "Loading…")}</pre>}</li>)}</ul>}</Card>;
}

function Card({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"><h3 className="text-sm font-semibold">{title}</h3><p className="mb-4 mt-0.5 text-[11px] text-zinc-500">{subtitle}</p>{children}</div>;
}

function StatusDot({ status }: { status: string }) {
  const color = status === "RUNNING" || status === "QUEUED" || status === "SCHEDULED" ? "bg-blue-500 motion-safe:animate-pulse" : status === "SUCCEEDED" || status === "COMPLETED" ? "bg-emerald-500" : status === "SKIPPED" || status === "PAUSED" ? "bg-amber-500" : "bg-red-500";
  return <span aria-label={status} title={status} className={`h-2.5 w-2.5 shrink-0 rounded-full ${color}`} />;
}

function recurrenceLabel(value: TerminalAutomationSchedule["recurrence"]) {
  return value === "once" ? "Once" : value === "daily" ? "Daily" : value === "weekly" ? "Weekly" : "Every 2 weeks";
}

function defaultLocalTime() {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setSeconds(0, 0);
  return toLocalInput(date.toISOString());
}

function toLocalInput(value: string) {
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : "操作に失敗しました";
}
