// 開発の作業場（開発用の箱）。AI アシスタントの OpenCode の作業フォルダに選べる。
// 一覧は自分の作業場だけ。作る（ControlDeck 本体 / CodeDEV の git のプロジェクト）・消す（確認つき）。
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";
import { badgeText, targetInfo } from "./workspaceTargets";
import { WorkspaceDiff } from "./WorkspaceDiff";
import { ReviewButton } from "./DiffAsk";
import { WorkspaceMergeButton } from "./WorkspaceMerge";
import { WorkspaceCheckpoints } from "./RestoreDialog";
import { WorkspaceMediaSection } from "./WorkspaceMedia";

export type DevWorkspace = {
  name: string; source: string; source_label: string; branch: string;
  dirty: boolean; unpushed: number; created_at: number; last_used: number; repo_path: string;
  /** 開発対象と機能（古い API では無い）。adopt は取り込み先（pr は GitHub の PR、branch は元へのブランチ） */
  target?: string; target_label?: string; capabilities?: string[]; adopt?: "pr" | "branch";
  /** OpenCode（会話サーバー）がこの作業場で今も動いているか（消すときの確認を強くする） */
  working?: boolean;
  /** 作業場で使うモデルの並び（先頭から使う。古い API では無い）。ヘッダの札に先頭を出す */
  model_chain?: string[];
};

/** 作業場のモデルの並びの先頭を、ヘッダの札の文言にする（例 opencode-go/glm-5.3 → GLM-5.3（作業場））。 */
export function workspaceModelPill(workspace: DevWorkspace | undefined): string {
  const head = workspace?.model_chain?.[0];
  if (!head) return "";
  const name = head.split("/").pop() ?? head;
  if (head === "controldeck/auto") return "ローカル LLM（作業場）";
  return `${name.replace(/^[a-z]{2,4}(?=-)/, (m) => m.toUpperCase())}（作業場）`;
}

export type WorkspaceCheck = {
  passed: boolean; at: number; stale: boolean; head: string;
  items: { status: "ok" | "ng" | "skip"; name: string; message: string }[];
};
export type WorkspaceChanges = {
  base: string; head: string; file_count: number; added: number; removed: number; dirty: boolean;
  /** 未コミットのファイルの数（ファイル数・増減に数に入れたうえでの内訳。箱の git が読めなければ null） */
  uncommitted_files?: number | null;
  files: { path: string; added: number; removed: number; binary: boolean; untracked: boolean }[];
  commits: { sha: string; author: string; at: number; subject: string }[]; commit_count: number;
  check: WorkspaceCheck | null;
  adopted: { kind: "pr" | "branch"; branch: string; pr_url: string; head: string } | null;
  merged?: { at: number; by: string; pr_url: string; tier: string } | null;
  /** 元の main が何件進んでいるか（取れなければ null） */
  behind?: number | null;
  /** 取り込みでぶつかったファイル（OpenCode に直させるボタンを出す） */
  merge_conflict?: { conflicts: string[]; at: number } | null;
  /** data のある所のディスクの空き（GiB）と、下限の 2 倍未満か */
  disk_free_gib?: number; disk_low?: boolean;
  /** PR の題の候補（自動保存の件名ではなく、名前の付いたコミットか会話の題。U18） */
  suggested_title?: string;
};

/** 作業場の進み具合（GET /opencode/workspaces/progress。Project Lab の「作業場」のタブ用） */
export type WorkspaceProgress = {
  state: "working" | "waiting" | "done" | "stopped";
  todo: { done: number; total: number } | null;
  /** 最後に動いた時刻（epoch 秒。文字列だと解釈の時間帯でずれるので backend が秒で返す） */
  last_active_at: number;
  /** 再起動などで止まった（state は stopped）。失敗とは区別する */
  interrupted?: boolean;
  conversation_id: string;
  title: string;
};
/** 作業場の進み具合の文言と色。色は ui-ux ガイドライン（作業中=青・待ち=黄・完了=緑・止まった=赤） */
export const WS_STATES: Record<string, { label: string; short: string; tone: string; pulse?: boolean }> = {
  working: { label: "OpenCode が作業中", short: "作業中", tone: "text-sky-700 dark:text-sky-300", pulse: true },
  waiting: { label: "質問待ち（AI アシスタントで答えてください）", short: "質問待ち", tone: "text-amber-700 dark:text-amber-400" },
  done: { label: "完了", short: "完了", tone: "text-emerald-700 dark:text-emerald-400" },
  stopped: { label: "止まった", short: "止まった", tone: "text-red-700 dark:text-red-400" },
};

/** 会話の一覧の印（作業場を使う OpenCode の会話の状態）。中断は止まった（stopped）のうち interrupted のもの */
export function WorkspaceStateMark({ progress }: { progress: WorkspaceProgress }) {
  const mark = progress.state === "stopped" && progress.interrupted
    ? { short: "中断", cls: "border-orange-300 bg-orange-50 text-orange-800 dark:border-orange-700 dark:bg-orange-950/40 dark:text-orange-300" }
    : {
      working: { short: WS_STATES.working.short, cls: "border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300" },
      waiting: { short: WS_STATES.waiting.short, cls: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300" },
      done: { short: WS_STATES.done.short, cls: "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" },
      stopped: { short: WS_STATES.stopped.short, cls: "border-red-300 bg-red-50 text-red-700 dark:border-red-700 dark:bg-red-950/40 dark:text-red-300" },
    }[progress.state];
  return (
    <span data-workspace-mark={progress.state === "stopped" && progress.interrupted ? "interrupted" : progress.state}
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full border px-1.5 text-[10px] font-medium leading-4 ${mark.cls}`}>
      {mark.short}
    </span>
  );
}

export type WorkspaceProgressItem = DevWorkspace & { progress: WorkspaceProgress | null };

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
export function WorkspaceBadge({ className = "", target }: { className?: string; target?: string }) {
  return (
    <span data-testid="workspace-badge" title={`開発の作業場（隔離した箱の中で動きます）${targetInfo(target) ? `・開発対象: ${targetInfo(target)!.label}` : ""}`}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border border-sky-300 bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 ${className}`}>
      <span aria-hidden>🧪</span>{badgeText(target)}
    </span>
  );
}

export function ago(seconds: number): string {
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
  const [diff, setDiff] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const [working, setWorking] = useState(false);
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState("");
  const [title, setTitle] = useState("");
  const wasBusy = useRef(busy);
  const changesKey = ["workspace-changes", name];
  const { data: info, error } = useQuery({
    queryKey: changesKey, staleTime: 15_000,
    // 応答が欠けていても画面ごと落とさない（2026-11-22: 形の違う応答で files.length が落ちた）。
    // 数も 0 で保証する（visual review: 欠けた応答で「undefined ファイル」と画面に出た）
    queryFn: async () => {
      const raw = await api<Partial<WorkspaceChanges>>(`${base}/changes`);
      const files = Array.isArray(raw?.files) ? raw.files : [];
      return {
        ...raw, files,
        commits: Array.isArray(raw?.commits) ? raw.commits : [],
        file_count: typeof raw?.file_count === "number" ? raw.file_count : files.length,
        added: typeof raw?.added === "number" ? raw.added : 0,
        removed: typeof raw?.removed === "number" ? raw.removed : 0,
      } as WorkspaceChanges;
    },
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
    // 作業場の一覧（未保存の表示の元）も取り直す。ターンの終わりの自動保存で dirty が変わるため
    if (wasBusy.current && !busy) {
      void qc.invalidateQueries({ queryKey: changesKey });
      void qc.invalidateQueries({ queryKey: ["opencode-workspaces"] });
      setOpen(true);
    }
    wasBusy.current = busy;
  }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (wasRunning.current && !running) void qc.invalidateQueries({ queryKey: changesKey });
    wasRunning.current = running;
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps

  const check = info?.check ?? null;
  // 取り込み先は開発対象で決まる（pr は GitHub の PR、branch は元へのブランチ）。古い API は元から決める
  const isBody = (workspace.adopt ?? (workspace.source === "controldeck" ? "pr" : "branch")) === "pr";
  const adopted = info?.adopted && info.adopted.head === info.head && !info.dirty ? info.adopted : null;
  const canAdoptHere = !isBody || can("settings.manage");
  const ready = Boolean(check?.passed && !check.stale) && !adopted && !running && canAdoptHere;
  const target = isBody ? "GitHub に PR を作ります（マージはしません）" : `元のリポジトリに ws/${name} のブランチとして取り込みます`;

  // 最後の関門が「画面の見た目」だけで落ちたか（画像を読むモデルの目の誤検出を、管理者が人の目で確かめて外せる）
  const onlyVisualFailed = Boolean(check && !check.passed && !check.stale && check.items.some((i) => i.status === "ng")
    && check.items.filter((i) => i.status === "ng").every((i) => i.name.includes("見た目")));
  const runCheck = async (skipVisual = false) => {
    try {
      await api(`${base}/check`, { method: "POST", ...(skipVisual ? { json: { skip_visual: true } } : {}) });
      await qc.invalidateQueries({ queryKey: ["workspace-check", name] });
    } catch (e) {
      show(e instanceof Error ? e.message : "関門を流せませんでした", "error");
    }
  };
  const askResolve = async () => {
    // 取り込みでぶつかったファイルを、この作業場で動いた同じ会話の OpenCode に直させる
    if (asking || working) return;
    setAsking(true);
    try {
      await api(`${base}/resolve-conflict`, { method: "POST" });
      show("ぶつかりを OpenCode に直させる文を会話へ送りました");
    } catch (e) {
      show(e instanceof Error ? e.message : "OpenCode に直させる文を送れませんでした", "error");
    } finally {
      setAsking(false);
    }
  };
  const adopt = async () => {
    if (working) return;
    setWorking(true);
    try {
      await api(`${base}/adopt`, { method: "POST", json: { confirm: true, commit_message: info?.dirty ? message.trim() : "", ...(isBody && title.trim() ? { title: title.trim() } : {}) } });
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
          {/* 未コミットも数に入れたうえで、その内訳を出す（2026-11-22 までは 0 ファイル・未コミットと出ていた） */}
          {info && (info.uncommitted_files ?? 0) > 0 ? (
            <span className="text-amber-600 dark:text-amber-400">（うち未コミット {info.uncommitted_files}）</span>
          ) : info?.dirty ? (
            <span className="text-amber-600 dark:text-amber-400">・未コミット</span>
          ) : null}
          {info?.disk_low && <span className="text-amber-600 dark:text-amber-400">・空き {info.disk_free_gib} GiB</span>}
          {info?.merge_conflict && <span className="text-red-600 dark:text-red-400">・ぶつかり</span>}
        </span>
        <span className={`shrink-0 ${checkTone}`} data-testid="workspace-check-label">{checkLabel}</span>
        <span aria-hidden>{open ? "▲" : "▼"}</span>
      </button>
      {open && info && (
        <div className="space-y-2 border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {info.disk_low && (
            <p data-testid="workspace-disk-low" className="text-amber-700 dark:text-amber-400">
              ⚠️ ディスクの空きが少ない（残り {info.disk_free_gib} GiB）。下限を下回ると自動保存が止まります。必要なものを消してください。
            </p>
          )}
          {info.merge_conflict && (
            <div data-testid="workspace-conflict" className="rounded-lg border border-red-200 bg-red-50 p-2 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
              <p>main を取り込むときに {(info.merge_conflict.conflicts ?? []).length} ファイルがぶつかりました。</p>
              <ul className="mt-1 max-h-24 overflow-y-auto font-mono text-[11px]">
                {(info.merge_conflict.conflicts ?? []).slice(0, 10).map((f) => <li key={f} className="truncate">{f}</li>)}
              </ul>
              <button type="button" onClick={() => void askResolve()} disabled={asking} className={`mt-2 ${BTN}`}>
                {asking ? "送っています…" : "OpenCode に直させる"}
              </button>
            </div>
          )}
          {typeof info.behind === "number" && info.behind > 0 && !info.merge_conflict && (
            <p data-testid="workspace-behind" className="text-zinc-500 dark:text-zinc-400">
              元の main が {info.behind} 件進んでいます（取り込むときに箱の中へ取り込みます）。
            </p>
          )}
          <button type="button" onClick={() => setFiles((v) => !v)} aria-expanded={files}
            className="min-h-11 w-full text-left text-zinc-700 underline-offset-2 hover:underline dark:text-zinc-200">
            変わったファイル {info.file_count} 件{files ? "を閉じる" : "を開く"}
          </button>
          {files && (
            <ul data-testid="workspace-files" className="max-h-40 space-y-0.5 overflow-y-auto overflow-x-hidden font-mono text-[11px]">
              {info.files.map((f) => (
                <li key={f.path} className="flex min-w-0 gap-2">
                  <span className="w-0 min-w-0 flex-1 truncate" title={f.path}>{f.path}</span>
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
          <button type="button" onClick={() => setDiff((v) => !v)} aria-expanded={diff} data-testid="workspace-diff-toggle"
            className="min-h-11 w-full text-left text-zinc-700 underline-offset-2 hover:underline dark:text-zinc-200">
            変更{diff ? "を閉じる" : "（行ごとの差分）を見る"}
          </button>
          {diff && <WorkspaceDiff name={name} />}
          <WorkspaceMediaSection name={name} ago={ago} />
          <WorkspaceCheckpoints name={name} busy={busy} onRestored={() => void qc.invalidateQueries({ queryKey: changesKey })} />
          {check && !check.passed && !check.stale && (
            <ul data-testid="workspace-check-failures" className="list-disc pl-4 text-red-700 dark:text-red-400">
              {check.items.filter((i) => i.status === "ng").map((i) => <li key={i.name}>{i.name}: {i.message}</li>)}
            </ul>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void runCheck()} disabled={running || busy} className={BTN}>
              {running ? "関門を流しています…" : "関門を流す"}
            </button>
            {onlyVisualFailed && can("settings.manage") && !running && (
              <button type="button" data-testid="workspace-check-skip-visual" onClick={() => void runCheck(true)} disabled={busy}
                title="画像を読むモデルの指摘を、撮影を見て人が確かめた場合に。この項目だけ外して関門を流し直し、外したことは記録に残ります"
                className={BTN}>
                見た目は確かめた（外して流す）
              </button>
            )}
            {info.file_count > 0 && <ReviewButton className="text-sm" />}
            {!adopted && (
              <button type="button" onClick={() => { setTitle(info?.suggested_title ?? ""); setAdopting(true); }} disabled={!ready}
                title={!canAdoptHere ? "本体へ取り込むには設定の権限（settings.manage）が要ります" : !ready ? "関門が合格で、その後に変更が無いときに押せます" : ""}
                className="min-h-11 shrink-0 rounded-lg bg-sky-600 px-3 text-sm font-medium text-white hover:bg-sky-700 disabled:bg-sky-600/15 disabled:text-sky-700 dark:disabled:text-sky-400">
                取り込む
              </button>
            )}
          </div>
          {adopted && (
            <div data-testid="workspace-adopted" className="flex min-w-0 flex-wrap items-center gap-2 text-emerald-700 dark:text-emerald-400">
              <p className="min-w-0 flex-1 break-all">
                {adopted.kind === "pr"
                  ? <>PR を作りました。<a href={adopted.pr_url} target="_blank" rel="noreferrer" className="underline">{adopted.pr_url}</a>{info?.merged ? "（マージ済み）" : " で確認してマージします。"}</>
                  : <>元のリポジトリに ws/{name} のブランチとして取り込みました。</>}
              </p>
              {adopted.kind === "pr" && can("settings.manage") && (
                <WorkspaceMergeButton name={name} className={BTN} merged={Boolean(info?.merged)} />
              )}
            </div>
          )}
        </div>
      )}
      {adopting && (
        <ConfirmDialog title="取り込む" danger={false} message={target} confirmLabel="取り込む" busy={working}
          disabled={Boolean(info?.dirty) && !message.trim()} onConfirm={() => void adopt()} onClose={() => setAdopting(false)}>
          {isBody && (
            <label className="mt-3 block text-xs text-zinc-600 dark:text-zinc-400">
              PR の題
              <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} data-testid="adopt-title"
                className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
            </label>
          )}
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
  value, workspaces, projects, onCreated, onDeleted, busy = false, source, onSource, target, capabilities,
}: {
  /** 作る元（controldeck / codedev:<名前>）。＋メニューの「開発対象」と合わせるため親が持つ */
  source: string;
  onSource: (source: string) => void;
  /** 作るときに選んだ開発対象と機能の上書き（＋メニューで選ぶ） */
  target: string;
  capabilities: string[] | null;
  busy?: boolean;
  value: string;
  workspaces: DevWorkspace[];
  projects: { name: string; git: boolean }[];
  onCreated: (name: string) => void;
  onDeleted: (name: string) => void;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [working, setWorking] = useState(false);
  const [deleting, setDeleting] = useState<DevWorkspace | null>(null);
  const current = selectedWorkspace(value, workspaces);

  const create = async () => {
    if (working) return;
    setWorking(true);
    try {
      const made = await api<DevWorkspace>("/opencode/workspaces", { method: "POST", json: { source, target, ...(capabilities ? { capabilities } : {}) } });
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
        <WorkspaceBadge target={target} />
        <select aria-label="作業場の元" value={source} onChange={(e) => onSource(e.target.value)}
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
          開発対象: {targetInfo(target)?.label ?? target}（＋メニューの「開発対象」で変えられます）。
          元の履歴から隔離した作業ツリーを作ります。元のフォルダの未コミットの変更は入りません。
        </p>
      </div>
    );
  }
  if (!current) return null;
  const unsaved = current.dirty || current.unpushed > 0;
  return (
    <>
    <div data-testid="workspace-info" className="mb-2 flex min-w-0 flex-wrap items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
      <WorkspaceBadge target={current.target} />
      <span className="min-w-0 flex-1 truncate">
        {current.source_label}・{current.branch || "ブランチ不明"}・{ago(current.last_used)}
        {unsaved && <span className="ml-1 text-amber-600 dark:text-amber-400">・未保存の変更あり</span>}
        {current.working && <span className="ml-1 text-sky-600 dark:text-sky-400">・OpenCode が作業中</span>}
      </span>
      <button type="button" onClick={() => setDeleting(current)} aria-label={`作業場 ${current.name} を消す`}
        className="grid min-h-8 shrink-0 place-items-center rounded-lg border border-zinc-300 px-2 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">
        消す
      </button>
      {deleting && (
        <ConfirmDialog
          title="作業場を消す"
          message={deleting.working
            ? `作業場 ${deleting.name} で OpenCode が今も作業中です。止めてから消してください（作業の途中で消すと、続きからできません）。消すと戻せません。`
            : deleting.dirty || deleting.unpushed > 0
              ? `作業場 ${deleting.name} には保存されていない変更があります（${[deleting.dirty ? "未コミット" : "", deleting.unpushed > 0 ? `未 push のコミット ${deleting.unpushed} 件` : ""].filter(Boolean).join("・")}）。消すと戻せません。`
              : `作業場 ${deleting.name} を消します。元のリポジトリには影響しません。`}
          confirmLabel={deleting.working || deleting.dirty || deleting.unpushed > 0 ? "変更ごと消す" : "消す"}
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

