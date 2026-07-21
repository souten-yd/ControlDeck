import { lazy, Suspense, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useToasts } from "../stores";
import { ConfirmDialog, Skeleton } from "../components/ui";
import { IconPlus, IconSettings, IconTerminal } from "../components/icons";
import { PageHeader } from "../components/PageHeader";

const XtermView = lazy(() => import("../features/terminal/XtermView"));
const XtermViewV2 = lazy(() => import("../features/terminal/XtermViewV2"));
const TerminalAutomationPanel = lazy(() => import("../features/terminal/TerminalAutomationPanel"));

interface TerminalSession {
  id: string;
  name: string;
  created_at: number;
  attached: boolean;
  persistent: boolean;
  program: string;
  cwd: string;
  pid: number;
  activity_at: number;
  alive: boolean;
  workload: "idle" | "running";
  engine: "v1" | "v2-lab";
}

export default function TerminalPage() {
  const [searchParams] = useSearchParams();
  const terminalLabV2 = searchParams.get("terminalLab") === "v2";
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [active, setActive] = useState<string | null>(null);
  const [killing, setKilling] = useState<string | null>(null);
  const [automationOpen, setAutomationOpen] = useState(false);
  const [automationSession, setAutomationSession] = useState<string | null>(null);
  const [v2LabSessions, setV2LabSessions] = useState<string[]>(() => {
    try {
      const stored = JSON.parse(sessionStorage.getItem("control-deck:terminal-v2-lab-sessions") || "[]");
      return Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : [];
    } catch {
      return [];
    }
  });

  const rememberV2Session = (id: string) => {
    setV2LabSessions((current) => {
      const next = current.includes(id) ? current : [...current, id];
      try { sessionStorage.setItem("control-deck:terminal-v2-lab-sessions", JSON.stringify(next)); } catch { /* tab内stateを維持 */ }
      return next;
    });
  };
  const forgetV2Session = (id: string) => {
    setV2LabSessions((current) => {
      const next = current.filter((candidate) => candidate !== id);
      try { sessionStorage.setItem("control-deck:terminal-v2-lab-sessions", JSON.stringify(next)); } catch { /* tab内stateを維持 */ }
      return next;
    });
  };

  const { data, isLoading } = useQuery({
    queryKey: ["terminals"],
    queryFn: () => api<{ tmux: boolean; sessions: TerminalSession[] }>("/terminals"),
    refetchInterval: active ? 3_000 : 10_000,
  });
  const visibleSessions = (data?.sessions ?? []).filter(
    (session) => session.engine !== "v2-lab" || v2LabSessions.includes(session.id),
  );

  const create = async () => {
    try {
      const s = await api<{ id: string }>(terminalLabV2 ? "/terminals?engine=v2-lab" : "/terminals", { method: "POST" });
      if (terminalLabV2) rememberV2Session(s.id);
      // 新Sessionをselect optionsへ反映してから全画面viewをmountする。
      // 先にactiveだけを変えると、遅いbrowserでは旧optionsのvalueを保持したまま
      // engine判定／session switchが一時的に食い違う。
      await qc.invalidateQueries({ queryKey: ["terminals"] });
      setActive(s.id);
    } catch (e) {
      show(e instanceof Error ? e.message : "セッション作成に失敗しました", "error");
    }
  };

  const kill = async (id: string) => {
    try {
      await api(`/terminals/${id}`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["terminals"] });
      if (active === id) setActive(null);
      forgetV2Session(id);
      show("セッションを終了しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "終了に失敗しました", "error");
    }
    setKilling(null);
  };

  // 接続中は全画面ターミナル
  if (active) {
    const TerminalView = terminalLabV2 && v2LabSessions.includes(active) ? XtermViewV2 : XtermView;
    return (
      <>
        <Suspense fallback={<div className="grid h-full place-items-center text-sm text-zinc-400">ターミナルを読み込み中...</div>}>
          <TerminalView
            sessionId={active}
            sessions={visibleSessions}
            onSwitch={setActive}
            onAutomation={() => {
              setAutomationSession(active);
              setAutomationOpen(true);
            }}
            onExit={() => {
              setAutomationOpen(false);
              setActive(null);
              qc.invalidateQueries({ queryKey: ["terminals"] });
            }}
          />
          {automationOpen && <TerminalAutomationPanel
            sessions={data?.sessions ?? []}
            initialSessionId={automationSession}
            onClose={() => setAutomationOpen(false)}
          />}
        </Suspense>
      </>
    );
  }

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      {terminalLabV2 && <div role="status" className="mb-4 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-xs text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300">
        Terminal V2 Labです。このtabで新規作成した検証sessionだけをV2で開き、既存sessionは常に安定版で開きます。
      </div>}
      <PageHeader title="Terminal" actions={<div className="flex items-center gap-2"><button
          onClick={() => {
            setAutomationSession(null);
            setAutomationOpen(true);
          }}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-zinc-300 bg-white px-3 text-sm font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
        >
          <IconSettings /> Snippets
        </button><button
          onClick={create}
          className="flex min-h-11 items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 text-sm font-medium text-white hover:bg-accent-700"
        >
          <IconPlus /> {terminalLabV2 ? "V2検証セッション" : "新規セッション"}
        </button></div>} />

      {data && !data.tmux && (
        <p className="mb-4 rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
          tmux が未インストールのため、セッションはバックエンド再起動で失われます。
          永続化するには <code className="font-mono">sudo apt install tmux</code> を実行してください。
        </p>
      )}

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : !data || visibleSessions.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">アクティブなセッションはありません</p>
          <button
            onClick={create}
            className="mt-3 rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700"
          >
            セッションを開始
          </button>
        </div>
      ) : (
        <ul className="grid gap-3">
          {visibleSessions.map((s) => (
            <li key={s.id} className="rounded-2xl border border-zinc-200 bg-white p-3 shadow-sm transition hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700">
              <div className="flex items-start gap-3">
                <div className="relative grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  <IconTerminal />
                  <span aria-hidden="true" className={`absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-white dark:border-zinc-900 ${s.alive ? s.workload === "running" ? `bg-blue-500 ${isRecentlyActive(s.activity_at) ? "motion-safe:animate-pulse" : ""}` : "bg-emerald-500" : "bg-red-500"}`} />
                </div>
                <button onClick={() => setActive(s.id)} className="min-h-11 min-w-0 flex-1 text-left">
                  <span className="flex min-w-0 items-center gap-2"><strong className="truncate text-sm">{s.program || "Shell"}</strong><code className="shrink-0 text-[10px] text-zinc-400">#{s.id}</code></span>
                  <code className="mt-1 block truncate text-[11px] text-zinc-500" title={s.cwd}>{s.cwd || "N/A"}</code>
                </button>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => { setAutomationSession(s.id); setAutomationOpen(true); }}
                    aria-label={`${s.program || s.name}のオートメーション設定`}
                    title="オートメーション設定"
                    className="grid h-11 w-11 place-items-center rounded-xl text-lg hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 dark:hover:bg-zinc-800"
                  >
                    <span aria-hidden="true">🔧</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setKilling(s.id)}
                    aria-label={`${s.program || s.name}のセッションを削除`}
                    title="セッションを削除"
                    className="grid h-11 w-11 place-items-center rounded-xl text-lg text-red-600 hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 dark:text-red-400 dark:hover:bg-red-950/40"
                  >
                    <span aria-hidden="true">🗑️</span>
                  </button>
                </div>
              </div>
              <div data-terminal-session-info className="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
                <div data-terminal-status-row className="flex flex-wrap items-center gap-2">
                  <WorkloadBadge session={s} />
                  {s.attached && <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">Web client connected</span>}
                </div>
                <div data-terminal-meta-row className="mt-2 flex min-w-0 items-end gap-3">
                  <div data-terminal-dates className="min-w-0 flex-1 text-[10px] leading-4 text-zinc-500 dark:text-zinc-400">
                    <p className="truncate" title={`最終活動 ${formatActivity(s.activity_at || s.created_at)}`}>最終活動 {formatActivity(s.activity_at || s.created_at)}</p>
                    <p className="truncate" title={`作成 ${formatActivity(s.created_at)} · ${s.persistent ? "tmux persistent" : "in-memory"}`}>作成 {formatActivity(s.created_at)} · {s.persistent ? "tmux persistent" : "in-memory"}</p>
                  </div>
                  <button data-terminal-connect onClick={() => setActive(s.id)} className="min-h-11 shrink-0 rounded-xl bg-accent-50 px-4 text-sm font-semibold text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400">Connect</button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {killing && (
        <ConfirmDialog
          title="セッションを終了しますか？"
          message="実行中のプロセスはすべて終了します。この操作は取り消せません。"
          confirmLabel="終了する"
          onConfirm={() => kill(killing)}
          onClose={() => setKilling(null)}
        />
      )}
      {automationOpen && <Suspense fallback={null}><TerminalAutomationPanel
        sessions={visibleSessions}
        initialSessionId={automationSession}
        onClose={() => setAutomationOpen(false)}
      /></Suspense>}
    </div>
  );
}

function WorkloadBadge({ session }: { session: TerminalSession }) {
  const running = session.alive && session.workload === "running";
  return <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[10px] font-semibold ${!session.alive ? "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300" : running ? "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"}`}>
    <span className={`h-1.5 w-1.5 rounded-full ${!session.alive ? "bg-red-500" : running ? `bg-blue-500 ${isRecentlyActive(session.activity_at) ? "motion-safe:animate-pulse" : ""}` : "bg-emerald-500"}`} />
    {!session.alive ? "Exited" : running ? "Foreground active" : "Shell ready"}
  </span>;
}

function formatActivity(timestamp: number) {
  if (!timestamp) return "N/A";
  return new Date(timestamp * 1000).toLocaleString("ja-JP", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function isRecentlyActive(timestamp: number) {
  return timestamp > 0 && Date.now() / 1000 - timestamp < 10;
}
