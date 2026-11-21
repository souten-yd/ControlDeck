// 開発の作業場（開発用の箱）。AI アシスタントの OpenCode の作業フォルダに選べる。
// 一覧は自分の作業場だけ。作る（ControlDeck 本体 / CodeDEV の git のプロジェクト）・消す（確認つき）。
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";
import { useToasts } from "../../stores";

export type DevWorkspace = {
  name: string; source: string; source_label: string; branch: string;
  dirty: boolean; unpushed: number; created_at: number; last_used: number; repo_path: string;
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

export function WorkspaceBar({
  value, workspaces, projects, onCreated, onDeleted,
}: {
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
  );
}
