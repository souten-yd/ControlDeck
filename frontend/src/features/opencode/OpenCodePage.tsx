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
import { ConfirmDialog, Skeleton } from "../../components/ui";
import { IconFolder, IconPlus, IconTrash } from "../../components/icons";
import { useToasts } from "../../stores";
import { PageHeader } from "../../components/PageHeader";

const XtermView = lazy(() => import("../terminal/XtermView"));

interface FeatureState {
  id: string; installed: boolean; managed: boolean; enabled: boolean;
  version: string; health: string; executable: string;
}
interface Settings { base_url: string; model: string; project_path: string }
interface Status { feature: FeatureState; settings: Settings }
interface TerminalSession { id: string; name: string; created_at: number; attached: boolean; persistent: boolean }
interface CodeProject { name: string; path: string; git: boolean; modified_at: number }

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900";
const LS_SESSIONS = "cd-opencode-sessions"; // このページで開始したセッションID（表示の絞り込み用）

function loadOwnSessions(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_SESSIONS) || "[]");
    return Array.isArray(raw) ? raw.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export default function OpenCodePage() {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const { data } = useQuery({ queryKey: ["opencode-status"], queryFn: () => api<Status>("/opencode/status"), staleTime: 30_000 });
  const { data: projectsData } = useQuery({
    queryKey: ["opencode-projects"],
    queryFn: () => api<{ root: string; projects: CodeProject[] }>("/opencode/projects"),
  });
  const [form, setForm] = useState<Settings>({ base_url: "", model: "", project_path: "" });
  const [prompt, setPrompt] = useState("");
  // プロジェクト選択: 新規（既定）/ CodeDEV既存一覧 / 📁フォルダ（CodeDEV外はコピー取込）
  const [projectMode, setProjectMode] = useState<"new" | "existing" | "folder">("new");
  const [newName, setNewName] = useState("");
  const [existingName, setExistingName] = useState("");
  const [otherPath, setOtherPath] = useState("");
  const [picker, setPicker] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [own, setOwn] = useState<string[]>(loadOwnSessions);
  const [active, setActive] = useState<string | null>(params.get("session"));
  const [killing, setKilling] = useState<string | null>(null);
  const [deletingProject, setDeletingProject] = useState<string | null>(null);

  useEffect(() => { if (data) setForm(data.settings); }, [data]);
  useEffect(() => { localStorage.setItem(LS_SESSIONS, JSON.stringify(own.slice(-20))); }, [own]);

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

  const save = useMutation({
    mutationFn: () => api<Settings>("/opencode/settings", { method: "PUT", json: form }),
    onSuccess: (settings) => { setForm(settings); qc.invalidateQueries({ queryKey: ["opencode-status"] }); show("OpenCode設定を保存しました"); },
    onError: (error) => show(error instanceof Error ? error.message : "設定保存に失敗", "error"),
  });

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
        prompt, base_url: form.base_url, model: form.model,
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
          sessions={sessions.map((s) => ({ id: s.id, name: s.name }))}
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
      <PageHeader title="OpenCode" description={`${data?.feature.version ? `v${data.feature.version.replace(/^v/, "")}` : "確認中"} · CLIそのままの対話TUI（tmux永続・再接続可）`} actions={<button onClick={() => setSettingsOpen((v) => !v)} aria-label="OpenCode Settings" title="LLM endpoint / model settings"
        className={`min-h-11 rounded-xl border px-3 py-2 text-sm ${settingsOpen ? "border-accent-500 text-accent-600" : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"}`}>⚙</button>} />

      {settingsOpen && (
        <section className="grid gap-3 rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800 sm:grid-cols-2">
          <label className="text-xs text-zinc-500">LLM endpoint<input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} className={`${input} mt-1 font-mono`} /></label>
          <label className="text-xs text-zinc-500">モデル<input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} className={`${input} mt-1 font-mono`} /></label>
          <button onClick={() => save.mutate()} disabled={save.isPending} className="rounded-xl border border-accent-500 py-2 text-sm text-accent-600 disabled:opacity-50 sm:col-span-2">設定を保存</button>
        </section>
      )}

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
        {projectMode === "new" && (
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="プロジェクト名（例: my-app）— CodeDEV配下に作成し git init します" className={`${input} font-mono`} autoFocus />
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
                <button onClick={() => setActive(s.id)} className="min-w-0 flex-1 text-left">
                  <p className="truncate font-mono text-sm">opencode · {s.id}</p>
                  <p className="text-xs text-zinc-400">
                    {s.created_at ? new Date(s.created_at * 1000).toLocaleString("ja-JP") : ""}
                    {s.attached && " · 接続中"}
                    {s.persistent && " · 永続 (tmux)"}
                  </p>
                </button>
                <button onClick={() => setActive(s.id)} className="rounded-xl bg-accent-50 px-3.5 py-2 text-sm font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400">接続</button>
                <button onClick={() => setKilling(s.id)} aria-label={`セッション ${s.id} を終了`} className="rounded-lg p-2 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40"><IconTrash /></button>
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
