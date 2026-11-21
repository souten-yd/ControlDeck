// 開発の作業場（開発用の箱）。AI アシスタントの OpenCode の作業フォルダに選べる。
// 一覧は自分の作業場だけ。作る（ControlDeck 本体 / CodeDEV の git のプロジェクト）・消す（確認つき）。
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

export type DevWorkspace = {
  name: string; source: string; source_label: string; branch: string;
  dirty: boolean; unpushed: number; created_at: number; last_used: number; repo_path: string;
};

export type WorkspaceCheck = {
  passed: boolean; at: number; stale: boolean; head: string;
  items: { status: "ok" | "ng" | "skip"; name: string; message: string }[];
};
export type WorkspaceChanges = {
  base: string; head: string; file_count: number; added: number; removed: number; dirty: boolean;
  files: { path: string; added: number; removed: number; binary: boolean; untracked: boolean }[];
  commits: { sha: string; author: string; at: number; subject: string }[]; commit_count: number;
  check: WorkspaceCheck | null;
  adopted: { kind: "pr" | "branch"; branch: string; pr_url: string; head: string } | null;
};

export const WS_PREFIX = "ws:";
export const WS_NEW = "__ws_new__";

export function workspaceValue(name: string): string {
  return `${WS_PREFIX}${name}`;
}

/** 選択値（ws:<名前>）から作業場を引く。 */
export function selectedWorkspace(value: string, list: DevWorkspace[] | undefined): DevWorkspace | undefined {
  return value.startsWith(WS_PREFIX) ? list?.find((w) => workspaceValue(w.name) === value) : undefined;
}

/** 作業場の小さな印（作業場で動いていることが分かる）。 */
export function WorkspaceBadge({ className = "" }: { className?: string }) {
  return (
    <span data-testid="workspace-badge" title="開発の作業場（隔離した箱の中で動きます）"
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border border-sky-300 bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 ${className}`}>
      <span aria-hidden>🧪</span>作業場
    </span>
  );
}

function ago(seconds: number): string {
  if (!seconds) return "未使用";
  const diff = Math.max(0, Date.now() / 1000 - seconds);
  if (diff < 90) return "たった今";
  if (diff < 3600) return `${Math.round(diff / 60)} 分前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 時間前`;
  return `${Math.round(diff / 86400)} 日前`;
}

const BTN = "min-h-11 shrink-0 rounded-lg border border-zinc-300 px-3 text-sm text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800";

/** 作業場の結果のカード: 変更の要約・関門・取り込む。OpenCode の回が終わるたびに読み直す。 */
export function WorkspaceResultCard({ workspace, busy }: { workspace: DevWorkspace; busy: boolean }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const name = workspace.name;
  const base = `/opencode/workspaces/${encodeURIComponent(name)}`;
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const wasBusy = useRef(busy);
  const changesKey = ["workspace-changes", name];
  const { data: info, error } = useQuery({
    queryKey: changesKey, queryFn: () => api<WorkspaceChanges>(`${base}/changes`), staleTime: 15_000,
  });
  const { data: checkState } = useQuery({
    queryKey: ["workspace-check", name],
    queryFn: () => api<{ running: boolean }>(`${base}/check`),
    refetchInterval: (q) => (q.state.data?.running ? 3000 : false),
  });
  const running = Boolean(checkState?.running);
  const wasRunning = useRef(running);
  useEffect(() => {
    // OpenCode の回が終わったら結果を読み直して開く
    if (wasBusy.current && !busy) { void qc.invalidateQueries({ queryKey: changesKey }); setOpen(true); }
    wasBusy.current = busy;
  }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (wasRunning.current && !running) void qc.invalidateQueries({ queryKey: changesKey });
    wasRunning.current = running;
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps

  const check = info?.check ?? null;
  const isBody = workspace.source === "controldeck";
  const adopted = info?.adopted && info.adopted.head === info.head && !info.dirty ? info.adopted : null;
  const canAdoptHere = !isBody || can("settings.manage");
  const ready = Boolean(check?.passed && !check.stale) && !adopted && !running && canAdoptHere;
  const target = isBody ? "GitHub に PR を作ります（マージはしません）" : `元のリポジトリに ws/${name} のブランチとして取り込みます`;

  const runCheck = async () => {
    try {
      await api(`${base}/check`, { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["workspace-check", name] });
    } catch (e) {
      show(e instanceof Error ? e.message : "関門を流せませんでした", "error");
    }
  };
  const adopt = async () => {
    if (working) return;
    setWorking(true);
    try {
      await api(`${base}/adopt`, { method: "POST", json: { confirm: true, commit_message: info?.dirty ? message.trim() : "" } });
      await qc.invalidateQueries({ queryKey: changesKey });
      setAdopting(false);
      show(isBody ? "PR を作りました" : `ブランチ ws/${name} として取り込みました`);
    } catch (e) {
      await qc.invalidateQueries({ queryKey: changesKey });
      setAdopting(false);
      show(e instanceof Error ? e.message : "取り込めませんでした", "error");
    } finally {
      setWorking(false);
    }
  };

  const checkLabel = running ? "関門を流しています…" : !check ? "関門は未実施" : check.stale ? "関門の後に変更あり" : check.passed ? "関門 合格" : "関門 不合格";
  const checkTone = running || !check || check.stale ? "text-zinc-500 dark:text-zinc-400"
    : check.passed ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400";
  return (
    <div data-testid="workspace-result" className="mb-2 min-w-0 rounded-lg border border-sky-200 bg-white text-xs dark:border-sky-900 dark:bg-zinc-900">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex min-h-11 w-full min-w-0 items-center gap-2 px-3 text-left">
        <span className="font-medium text-zinc-700 dark:text-zinc-200">作業場の結果</span>
        <span className="min-w-0 flex-1 truncate text-zinc-500 dark:text-zinc-400">
          {error ? "読めませんでした" : info ? `${info.file_count} ファイル +${info.added} −${info.removed}` : "読み込み中…"}
          {info?.dirty && <span className="text-amber-600 dark:text-amber-400">・未コミット</span>}
        </span>
        <span className={`shrink-0 ${checkTone}`} data-testid="workspace-check-label">{checkLabel}</span>
        <span aria-hidden>{open ? "▲" : "▼"}</span>
      </button>
      {open && info && (
        <div className="space-y-2 border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          <button type="button" onClick={() => setFiles((v) => !v)} aria-expanded={files}
            className="min-h-11 w-full text-left text-zinc-700 underline-offset-2 hover:underline dark:text-zinc-200">
            変わったファイル {info.file_count} 件{files ? "を閉じる" : "を開く"}
          </button>
          {files && (
            <ul data-testid="workspace-files" className="max-h-40 space-y-0.5 overflow-y-auto font-mono text-[11px]">
              {info.files.map((f) => (
                <li key={f.path} className="flex min-w-0 gap-2">
                  <span className="min-w-0 flex-1 truncate">{f.path}</span>
                  <span className="shrink-0 text-zinc-500">{f.untracked ? "未追跡" : f.binary ? "バイナリ" : `+${f.added} −${f.removed}`}</span>
                </li>
              ))}
            </ul>
          )}
          {info.dirty && (
            <p className="text-amber-700 dark:text-amber-400">コミットされていない変更があります。OpenCode にコミットさせるか、取り込むときにここでコミットします。</p>
          )}
          {info.commits.length > 0 && (
            <p className="text-zinc-500 dark:text-zinc-400">コミット {info.commit_count} 件（最新: {info.commits[0].subject}）</p>
          )}
          {check && !check.passed && !check.stale && (
            <ul data-testid="workspace-check-failures" className="list-disc pl-4 text-red-700 dark:text-red-400">
              {check.items.filter((i) => i.status === "ng").map((i) => <li key={i.name}>{i.name}: {i.message}</li>)}
            </ul>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void runCheck()} disabled={running || busy} className={BTN}>
              {running ? "関門を流しています…" : "関門を流す"}
            </button>
            {!adopted && (
              <button type="button" onClick={() => setAdopting(true)} disabled={!ready}
                title={!canAdoptHere ? "本体へ取り込むには設定の権限（settings.manage）が要ります" : !ready ? "関門が合格で、その後に変更が無いときに押せます" : ""}
                className="min-h-11 shrink-0 rounded-lg bg-sky-600 px-3 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-40">
                取り込む
              </button>
            )}
          </div>
          {adopted && (
            <p data-testid="workspace-adopted" className="break-all text-emerald-700 dark:text-emerald-400">
              {adopted.kind === "pr"
                ? <>PR を作りました。確認とマージは <a href={adopted.pr_url} target="_blank" rel="noreferrer" className="underline">{adopted.pr_url}</a> で行ってください。</>
                : <>元のリポジトリに ws/{name} のブランチとして取り込みました。</>}
            </p>
          )}
        </div>
      )}
      {adopting && (
        <ConfirmDialog title="取り込む" danger={false} message={target} confirmLabel="取り込む" busy={working}
          disabled={Boolean(info?.dirty) && !message.trim()} onConfirm={() => void adopt()} onClose={() => setAdopting(false)}>
          {info?.dirty && (
            <label className="mt-3 block text-xs text-zinc-600 dark:text-zinc-400">
              コミットメッセージ（未コミットの変更をここでコミットします）
              <input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={200}
                className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
            </label>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}

export function WorkspaceBar({
  value, workspaces, projects, onCreated, onDeleted, busy = false,
}: {
  busy?: boolean;
  value: string;
  workspaces: DevWorkspace[];
  projects: { name: string; git: boolean }[];
  onCreated: (name: string) => void;
  onDeleted: (name: string) => void;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [source, setSource] = useState("controldeck");
  const [working, setWorking] = useState(false);
  const [deleting, setDeleting] = useState<DevWorkspace | null>(null);
  const current = selectedWorkspace(value, workspaces);

  const create = async () => {
    if (working) return;
    setWorking(true);
    try {
      const made = await api<DevWorkspace>("/opencode/workspaces", { method: "POST", json: { source } });
      await qc.invalidateQueries({ queryKey: ["opencode-workspaces"] });
      show(`作業場 ${made.name} を作りました`);
      onCreated(made.name);
    } catch (e) {
      show(e instanceof Error ? e.message : "作業場を作れませんでした", "error");
    } finally {
      setWorking(false);
    }
  };

  const remove = async () => {
    if (!deleting || working) return;
    const target = deleting;
    const unsaved = target.dirty || target.unpushed > 0;
    setWorking(true);
    try {
      await api(`/opencode/workspaces/${encodeURIComponent(target.name)}${unsaved ? "?confirm=true" : ""}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["opencode-workspaces"] });
      show(`作業場 ${target.name} を消しました`);
      setDeleting(null);
      onDeleted(target.name);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // 一覧が古く、保存されていない変更が増えていた。確認し直す
        await qc.invalidateQueries({ queryKey: ["opencode-workspaces"] });
        setDeleting(null);
        show("保存されていない変更があります。もう一度、内容を確かめて消してください", "error");
      } else {
        show(e instanceof Error ? e.message : "作業場を消せませんでした", "error");
      }
    } finally {
      setWorking(false);
    }
  };

  if (value === WS_NEW) {
    return (
      <div data-testid="workspace-create" className="mb-2 flex min-w-0 flex-wrap items-center gap-1.5 rounded-lg border border-sky-200 bg-sky-50/60 p-2 dark:border-sky-900 dark:bg-sky-950/20">
        <WorkspaceBadge />
        <select aria-label="作業場の元" value={source} onChange={(e) => setSource(e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900">
          <option value="controldeck">ControlDeck 本体（origin/main）</option>
          {projects.filter((p) => p.git).map((p) => (
            <option key={p.name} value={`codedev:${p.name}`}>CodeDEV: {p.name}</option>
          ))}
        </select>
        <button type="button" onClick={() => void create()} disabled={working}
          className="min-h-9 shrink-0 rounded-lg bg-sky-600 px-3 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50">
          {working ? "作っています…" : "作る"}
        </button>
        <p className="w-full text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
          元の履歴から隔離した作業ツリーを作ります。元のフォルダの未コミットの変更は入りません。
        </p>
      </div>
    );
  }
  if (!current) return null;
  const unsaved = current.dirty || current.unpushed > 0;
  return (
    <>
    <div data-testid="workspace-info" className="mb-2 flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
      <WorkspaceBadge />
      <span className="min-w-0 flex-1 truncate">
        {current.source_label}・{current.branch || "ブランチ不明"}・{ago(current.last_used)}
        {unsaved && <span className="ml-1 text-amber-600 dark:text-amber-400">・未保存の変更あり</span>}
      </span>
      <button type="button" onClick={() => setDeleting(current)} aria-label={`作業場 ${current.name} を消す`}
        className="grid min-h-8 shrink-0 place-items-center rounded-lg border border-zinc-300 px-2 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">
        消す
      </button>
      {deleting && (
        <ConfirmDialog
          title="作業場を消す"
          message={deleting.dirty || deleting.unpushed > 0
            ? `作業場 ${deleting.name} には保存されていない変更があります（${[deleting.dirty ? "未コミット" : "", deleting.unpushed > 0 ? `未 push のコミット ${deleting.unpushed} 件` : ""].filter(Boolean).join("・")}）。消すと戻せません。`
            : `作業場 ${deleting.name} を消します。元のリポジトリには影響しません。`}
          confirmLabel={deleting.dirty || deleting.unpushed > 0 ? "変更ごと消す" : "消す"}
          busy={working}
          onConfirm={() => void remove()}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
    <WorkspaceResultCard workspace={current} busy={busy} />
    </>
  );
}

