/** OpenCode — 対話TUIセッションを主体にした coding agent 画面。
 *
 * セッションはターミナル基盤（tmux）上で動くため、CLIそのままの操作感で
 * 永続・再接続できる。AIチャット等からは POST /opencode/sessions →
 * /opencode?session=<id> で同じ画面に接続できる。
 */
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { FilePicker } from "../../components/FilePicker";
import { BottomSheet, ConfirmDialog, Skeleton } from "../../components/ui";
import { IconFolder, IconPlus, IconSettings, IconTrash } from "../../components/icons";
import { useAuth, useToasts } from "../../stores";
import { PageHeader } from "../../components/PageHeader";
import { BackgroundRuns } from "./BackgroundRuns";
import { WorkflowExtensions, workflowBody, type Workflow } from "./WorkflowExtensions";
import { SettingsGroup, SheetActions, primaryButton } from "../models/formControls";

const XtermView = lazy(() => import("../terminal/XtermView"));

interface FeatureState {
  id: string; installed: boolean; managed: boolean; enabled: boolean;
  version: string; health: string; executable: string;
}
interface Settings { base_url: string; model: string; project_path: string; runtime: string }
interface Status {
  feature: FeatureState; settings: Settings;
  active_runtime: string; runtimes: Record<string, FeatureState>;
}
interface TerminalSession { id: string; name: string; created_at: number; attached: boolean; persistent: boolean; cwd?: string }

/** セッションはCodeDEV配下のプロジェクトで起動するので、作業ディレクトリ名をそのまま名前として使う。 */
function projectLabel(session: TerminalSession): string {
  const directory = (session.cwd || "").replace(/\/+$/, "").split("/").filter(Boolean).pop();
  return directory && directory !== "~" ? directory : session.id.slice(0, 8);
}
interface CodeProject { name: string; path: string; git: boolean; modified_at: number }

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900";
// このページで開始したセッションID（表示の絞り込み用）。
function sessionsKey(runtime: string): string {
  return `cd-opencode-${runtime}-sessions`;
}

function loadOwnSessions(runtime: string): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(sessionsKey(runtime)) || "[]");
    return Array.isArray(raw) ? raw.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

/** OpenCode（v2）の画面。v1 は撤去した。 */
export default function OpenCodePage({ runtime = "v2" }: { runtime?: string }) {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const { data } = useQuery({
    queryKey: ["opencode-status", runtime],
    queryFn: () => api<Status>(`/opencode/status?runtime=${encodeURIComponent(runtime)}`),
    staleTime: 30_000,
  });
  const { data: projectsData } = useQuery({
    queryKey: ["opencode-projects"],
    queryFn: () => api<{ root: string; projects: CodeProject[] }>("/opencode/projects"),
  });
  const [prompt, setPrompt] = useState("");
  // プロジェクト選択: 新規（既定）/ CodeDEV既存一覧 / 📁フォルダ（CodeDEV外はコピー取込）
  const [projectMode, setProjectMode] = useState<"new" | "existing" | "folder">("new");
  const [newName, setNewName] = useState("");
  const [existingName, setExistingName] = useState("");
  const [otherPath, setOtherPath] = useState("");
  const [picker, setPicker] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [own, setOwn] = useState<string[]>(() => loadOwnSessions(runtime));
  const [active, setActive] = useState<string | null>(params.get("session"));
  const [killing, setKilling] = useState<string | null>(null);
  const [deletingProject, setDeletingProject] = useState<string | null>(null);

  useEffect(() => { localStorage.setItem(sessionsKey(runtime), JSON.stringify(own.slice(-20))); }, [own, runtime]);

  const { data: terminals } = useQuery({
    queryKey: ["terminals"],
    queryFn: () => api<{ tmux: boolean; sessions: TerminalSession[] }>("/terminals"),
    refetchInterval: active ? false : 10_000,
  });
  // このページで開始したセッションだけを表示（通常のターミナルとは混ぜない）
  const sessions = useMemo(
    () => (terminals?.sessions ?? []).filter((s) => own.includes(s.id)),
    [terminals, own],
  );

  const startDisabled =
    (projectMode === "new" && !newName.trim()) ||
    (projectMode === "existing" && !existingName) ||
    (projectMode === "folder" && !otherPath.trim());
  const start = useMutation({
    mutationFn: () => api<{ id: string }>("/opencode/sessions", {
      method: "POST",
      json: {
        project_name: projectMode === "new" ? newName.trim() : projectMode === "existing" ? existingName : "",
        project_path: projectMode === "folder" ? otherPath : "",
        prompt, base_url: data?.settings.base_url ?? "", model: data?.settings.model ?? "", runtime,
      },
    }),
    onSuccess: ({ id }) => {
      setOwn((prev) => [...prev.filter((v) => v !== id), id]);
      setPrompt("");
      qc.invalidateQueries({ queryKey: ["terminals"] });
      qc.invalidateQueries({ queryKey: ["opencode-projects"] });
      setActive(id);
    },
    onError: (error) => show(error instanceof Error ? error.message : "セッション開始に失敗", "error"),
  });

  const kill = async (id: string) => {
    try {
      await api(`/terminals/${id}`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["terminals"] });
      setOwn((prev) => prev.filter((v) => v !== id));
      if (active === id) setActive(null);
      show("セッションを終了しました");
    } catch (error) {
      show(error instanceof Error ? error.message : "終了に失敗しました", "error");
    }
    setKilling(null);
  };

  // チャット等からの起動: /opencode?session=<id> で直接TUIへ接続する
  useEffect(() => {
    const sid = params.get("session");
    if (sid) {
      setOwn((prev) => (prev.includes(sid) ? prev : [...prev, sid]));
      setActive(sid);
      setParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 接続中は全画面TUI（ターミナルと同じ操作系: ヘルパーキー・コピペ・再接続）
  if (active) {
    return (
      <Suspense fallback={<div className="grid h-full place-items-center text-sm text-zinc-400">OpenCodeに接続中...</div>}>
        <XtermView
          sessionId={active}
          sessions={sessions.map((s) => ({ id: s.id, name: projectLabel(s) }))}
          onSwitch={setActive}
          onExit={() => {
            setActive(null);
            qc.invalidateQueries({ queryKey: ["terminals"] });
          }}
        />
      </Suspense>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 pb-24 sm:p-6">
      <PageHeader title="OpenCode"
        description={`${data?.feature.version ? `v${data.feature.version.match(/\d+(?:\.\d+)+/)?.[0] ?? data.feature.version}` : "確認中"} · CLIそのままの対話TUI（tmux永続・再接続可）`}
        actions={<button onClick={() => setSettingsOpen(true)} disabled={!data} aria-label="OpenCode 設定を開く" title="モデル・開発ワークフローの設定"
        className="flex min-h-11 items-center gap-1.5 rounded-xl border border-zinc-300 px-3 py-2 text-sm text-zinc-600 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300"><IconSettings className="h-4 w-4" />設定</button>} />

      {settingsOpen && data && <OpenCodeSettingsSheet initial={data} onClose={() => setSettingsOpen(false)} />}

      <BackgroundRuns />

      {/* セッション開始 */}
      <section className="space-y-3 rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
        <div className="block text-xs text-zinc-500">
          <p>プロジェクト（CodeDEVで管理: {projectsData?.root ?? "~/CodeDEV"}）</p>
          <div className="mt-1 flex gap-1.5">
            <div className="flex flex-1 gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
              <button type="button" onClick={() => setProjectMode("new")}
                className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${projectMode === "new" ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>
                ➕ 新規作成
              </button>
              <button type="button" onClick={() => setProjectMode("existing")}
                className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${projectMode === "existing" ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>
                📚 既存のプロジェクト
              </button>
            </div>
            <button type="button" onClick={() => { setProjectMode("folder"); setPicker(true); }}
              aria-label="フォルダから選択"
              title="フォルダから選択（CodeDEV外はコピーして取り込みます）"
              className={`grid w-11 shrink-0 place-items-center rounded-xl border text-base ${projectMode === "folder" ? "border-accent-500 bg-accent-50/60 dark:bg-accent-600/10" : "border-zinc-300 dark:border-zinc-700"}`}>
              <IconFolder className="h-4 w-4 text-amber-500" />
            </button>
          </div>
        </div>
        {/* autoFocus は付けない。画面を開いただけで software keyboard が上がり、
            プロジェクト一覧も開始ボタンも隠れてしまう。入力は利用者がこの欄を
            選んだときに始める。 */}
        {projectMode === "new" && (
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="プロジェクト名（例: my-app）— CodeDEV配下に作成し git init します" className={`${input} font-mono`} />
        )}
        {projectMode === "existing" && (
          <div className="flex gap-1.5">
            <select value={existingName} onChange={(e) => setExistingName(e.target.value)} className={`${input} min-w-0 flex-1`}>
              <option value="">CodeDEVのプロジェクトを選択...</option>
              {(projectsData?.projects ?? []).map((p) => (
                <option key={p.name} value={p.name}>{p.name}{p.git ? " · git" : ""}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => existingName && setDeletingProject(existingName)}
              disabled={!existingName}
              aria-label="選択中のプロジェクトを削除"
              title="選択中のプロジェクトをフォルダごと削除"
              className="grid w-11 shrink-0 place-items-center rounded-xl border border-zinc-300 text-zinc-400 hover:border-red-300 hover:text-red-600 disabled:opacity-40 dark:border-zinc-700 dark:hover:border-red-800"
            >
              <IconTrash />
            </button>
          </div>
        )}
        {projectMode === "folder" && (
          <div className="space-y-1">
            <div className="flex gap-2">
              <input value={otherPath} onChange={(e) => setOtherPath(e.target.value)} placeholder="フォルダのパス（📁で選択）" className={`${input} min-w-0 font-mono`} />
              <button onClick={() => setPicker(true)} className="flex shrink-0 items-center gap-1.5 rounded-xl border border-zinc-300 px-3 text-xs dark:border-zinc-700"><IconFolder className="h-4 w-4 text-amber-500" />選択</button>
            </div>
            <p className="text-[10px] text-zinc-400">CodeDEV外のフォルダは ~/CodeDEV へコピーして取り込みます（node_modules等の依存物は除外）</p>
          </div>
        )}
        <label className="block text-xs text-zinc-500">最初の指示（任意 — 起動と同時に送信）
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} className={`${input} mt-1 resize-y`} placeholder="空のままでもOK。TUI内でいつでも指示できます" />
        </label>
        <button onClick={() => start.mutate()} disabled={start.isPending || startDisabled}
          className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          <IconPlus /> {start.isPending ? "起動中..." : "OpenCodeセッションを開始"}
        </button>
      </section>

      {/* セッション一覧 */}
      {terminals === undefined ? (
        <Skeleton className="h-16" />
      ) : sessions.length > 0 && (
        <section>
          <p className="mb-2 text-xs font-medium text-zinc-500">実行中のセッション</p>
          <ul className="divide-y divide-zinc-100 overflow-hidden rounded-2xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
            {sessions.map((s) => (
              <li key={s.id} className="flex items-center gap-3 bg-white px-4 py-3 dark:bg-zinc-900">
                <button onClick={() => setActive(s.id)} title={`session ${s.id}`} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-sm">
                    <span className="text-zinc-400">OpenCode · </span>
                    <span className="font-semibold">{projectLabel(s)}</span>
                  </p>
                  <p className="truncate text-xs text-zinc-400">
                    {s.created_at ? new Date(s.created_at * 1000).toLocaleString("ja-JP") : ""}
                    {s.attached && " · 接続中"}
                    {s.persistent && " · 永続 (tmux)"}
                  </p>
                </button>
                <button onClick={() => setActive(s.id)} className="rounded-xl bg-accent-50 px-3.5 py-2 text-sm font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400">接続</button>
                <button onClick={() => setKilling(s.id)} aria-label={`${projectLabel(s)} のセッションを終了`} className="rounded-lg p-2 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40"><IconTrash /></button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {picker && <FilePicker mode="dir" title="既存プロジェクトを選択" initialPath={otherPath || undefined} onSelect={(path) => { setOtherPath(path); setPicker(false); }} onClose={() => setPicker(false)} />}
      {killing && (
        <ConfirmDialog title="OpenCodeセッションを終了しますか？" message="TUIと実行中の処理は終了します。この操作は取り消せません。" confirmLabel="終了する" onConfirm={() => kill(killing)} onClose={() => setKilling(null)} />
      )}
      {deletingProject && (
        <ConfirmDialog
          title={`プロジェクト「${deletingProject}」を削除しますか？`}
          message={`~/CodeDEV/${deletingProject} をフォルダごと完全に削除します。Git履歴を含む全ファイルが消え、取り消せません。`}
          confirmLabel="フォルダごと削除する"
          onConfirm={async () => {
            try {
              await api(`/opencode/projects/${encodeURIComponent(deletingProject)}`, { method: "DELETE" });
              show(`「${deletingProject}」を削除しました`);
              if (existingName === deletingProject) setExistingName("");
              qc.invalidateQueries({ queryKey: ["opencode-projects"] });
            } catch (error) {
              show(error instanceof Error ? error.message : "削除に失敗しました", "error");
            }
            setDeletingProject(null);
          }}
          onClose={() => setDeletingProject(null)}
        />
      )}
    </div>
  );
}

/** OpenCode の設定を1枚のシートにまとめる。保存は1本で、内部で2つの API を順に呼ぶ。
 * フォームは開いた時点の値で初期化し、再取得で編集中の値を上書きしない。 */
function OpenCodeSettingsSheet({ initial, onClose }: { initial: Status; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const editable = can("settings.manage");
  const [form, setForm] = useState<Settings>(initial.settings);
  const { data: workflowData } = useQuery({ queryKey: ["opencode-workflow"], queryFn: () => api<Workflow>("/opencode/workflow") });
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  useEffect(() => { if (workflowData && !workflow) setWorkflow(workflowData); }, [workflowData, workflow]);
  const models = workflowData?.models ?? [];
  const save = useMutation({
    mutationFn: async () => {
      await api<Settings>("/opencode/settings", { method: "PUT", json: form });
      if (workflow) await api<Workflow>("/opencode/workflow", { method: "PUT", json: workflowBody(workflow) });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["opencode-status"] });
      void qc.invalidateQueries({ queryKey: ["opencode-workflow"] });
      show("OpenCode の設定を保存しました。次に開くセッションから反映されます");
      onClose();
    },
    onError: (error) => show(error instanceof Error ? error.message : "設定を保存できませんでした", "error"),
  });
  const known = form.model === "" || form.model === "auto" || models.some((m) => m.alias === form.model);
  return (
    <BottomSheet title="OpenCode 設定" onClose={onClose} wide>
      <div className="space-y-3">
        {!editable && <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">変更には管理者権限が必要です。</p>}
        <SettingsGroup title="モデル" description="セッションで使うモデル">
          <select aria-label="OpenCode モデル" value={known ? (form.model || "auto") : form.model} disabled={!editable}
            onChange={(e) => setForm({ ...form, model: e.target.value })} className={`${input} min-h-11`}>
            <option value="auto">Models で選んだランタイムに任せる（auto）</option>
            {models.map((m) => <option key={m.alias} value={m.alias}>{m.alias}{m.runtime ? `（${m.runtime}）` : ""}</option>)}
            {!known && <option value={form.model}>{form.model}</option>}
          </select>
          <p className="text-[11px] text-zinc-500">auto にすると、Models の共通設定で選んだランタイムのモデルを使います（必要なら起動します）。</p>
        </SettingsGroup>
        {workflow && <WorkflowExtensions value={{ ...workflow, models, installed: workflowData?.installed, features: workflowData?.features }} onChange={setWorkflow} editable={editable} />}
        <SettingsGroup title="詳細" description="接続先" collapsible defaultOpen={false}>
          <label className="block text-xs text-zinc-500">接続先（LLM endpoint）
            <input value={form.base_url} disabled={!editable} onChange={(e) => setForm({ ...form, base_url: e.target.value })} className={`${input} mt-1 font-mono`} />
          </label>
        </SettingsGroup>
        {editable && (
          <SheetActions>
            <button onClick={() => save.mutate()} disabled={save.isPending || !workflow} className={primaryButton}>{save.isPending ? "保存中…" : "保存"}</button>
          </SheetActions>
        )}
      </div>
    </BottomSheet>
  );
}
