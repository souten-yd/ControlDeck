/** GitHub 管理: リポジトリのクローン / 更新 / 保存 / リバート / 削除をボタン操作で行う。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { IconPlus, IconTrash } from "../components/icons";
import { PageHeader } from "../components/PageHeader";

interface RepoStatus {
  ok: boolean;
  error?: string;
  branch?: string;
  commit?: string;
  commit_time?: number | null;
  commit_message?: string;
  dirty?: boolean;
}

interface Repo {
  id: number;
  name: string;
  url: string;
  path: string;
  status: RepoStatus;
}

interface LogEntry {
  sha: string;
  time: number | null;
  message: string;
}

function timeAgo(ts?: number | null): string {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))} 分前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 時間前`;
  return `${Math.floor(s / 86400)} 日前`;
}

export default function GitHubPage() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState<Repo | null>(null);
  const [reverting, setReverting] = useState<Repo | null>(null);
  const [deleting, setDeleting] = useState<Repo | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const { data: repos, isLoading } = useQuery({
    queryKey: ["gitrepos"],
    queryFn: () => api<Repo[]>("/gitrepos"),
    refetchInterval: 30_000,
  });
  const { data: auth } = useQuery({
    queryKey: ["gh-auth"],
    queryFn: () => api<{ available: boolean; logged_in: boolean; account: string }>("/gitrepos/auth-status"),
    staleTime: 60_000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["gitrepos"] });

  const run = async (repo: Repo, action: "update", label: string) => {
    setBusyId(repo.id);
    try {
      const r = await api<{ detail: string }>(`/gitrepos/${repo.id}/${action}`, { method: "POST", json: {} });
      show(`${repo.name}: ${r.detail.split("\n")[0] || `${label}しました`}`);
      invalidate();
    } catch (e) {
      show(e instanceof Error ? e.message : `${label}に失敗しました`, "error");
    } finally {
      setBusyId(null);
    }
  };

  const login = async () => {
    try {
      const s = await api<{ id: string }>("/gitrepos/login-terminal", { method: "POST" });
      show("ターミナルで GitHub ログインを開始します");
      navigate(`/terminal`);
      void s;
    } catch (e) {
      show(e instanceof Error ? e.message : "ログイン開始に失敗しました", "error");
    }
  };

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <PageHeader title="GitHub" actions={<div className="flex items-center gap-2">
          {auth?.available && !auth.logged_in && can("apps.edit") && (
            <button onClick={login} className="rounded-xl bg-zinc-100 px-3 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300">
              GitHub にログイン
            </button>
          )}
          {auth?.logged_in && (
            <span className="text-xs text-zinc-400">{auth.account} でログイン中</span>
          )}
          {can("apps.edit") && (
            <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700">
              <IconPlus /> リポジトリを追加
            </button>
          )}
        </div>} />

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : !repos || repos.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">
            リポジトリ URL を追加すると、クローン・更新・保存・リバートをボタンで操作できます。
          </p>
          {can("apps.edit") && (
            <button onClick={() => setAdding(true)} className="mt-3 rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700">
              最初のリポジトリを追加
            </button>
          )}
        </div>
      ) : (
        <ul className="space-y-3">
          {repos.map((r) => (
            <li key={r.id} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">
                    {r.name}
                    {r.status.dirty && (
                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-950/60 dark:text-amber-400">
                        未保存の変更
                      </span>
                    )}
                  </p>
                  {r.status.ok ? (
                    <p className="num truncate text-xs text-zinc-400">
                      {r.status.branch} · {r.status.commit} · {r.status.commit_message}
                      {r.status.commit_time ? ` · ${timeAgo(r.status.commit_time)}` : ""}
                    </p>
                  ) : (
                    <p className="truncate text-xs text-red-500">{r.status.error}</p>
                  )}
                </div>
                {can("apps.delete") && (
                  <button onClick={() => setDeleting(r)} aria-label={`${r.name} を削除`} className="rounded-lg p-2 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40">
                    <IconTrash />
                  </button>
                )}
              </div>
              {can("apps.edit") && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <RepoButton disabled={busyId === r.id} onClick={() => run(r, "update", "更新")}>
                    {busyId === r.id ? "実行中..." : "⇣ 更新"}
                  </RepoButton>
                  <RepoButton disabled={busyId === r.id} onClick={() => setSaving(r)}>💾 保存</RepoButton>
                  <RepoButton disabled={busyId === r.id} onClick={() => setReverting(r)}>⏪ リバート</RepoButton>
                  <RepoButton onClick={() => navigate(`/files?path=${encodeURIComponent(r.path)}`)}>
                    フォルダを開く
                  </RepoButton>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {adding && <AddRepoSheet onClose={() => setAdding(false)} onDone={invalidate} />}
      {saving && (
        <SaveSheet
          repo={saving}
          onClose={() => setSaving(null)}
          onDone={() => {
            setSaving(null);
            invalidate();
          }}
        />
      )}
      {reverting && (
        <RevertSheet
          repo={reverting}
          onClose={() => setReverting(null)}
          onDone={() => {
            setReverting(null);
            invalidate();
          }}
        />
      )}
      {deleting && (
        <DeleteSheet
          repo={deleting}
          onClose={() => setDeleting(null)}
          onDone={() => {
            setDeleting(null);
            invalidate();
          }}
        />
      )}
    </div>
  );
}

function RepoButton({ children, disabled, onClick }: { children: React.ReactNode; disabled?: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="rounded-xl bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
    >
      {children}
    </button>
  );
}

function AddRepoSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api("/gitrepos", { method: "POST", json: { url: url.trim(), name: name.trim() } }),
    onSuccess: () => {
      show("クローンしました");
      onDone();
      onClose();
    },
    onError: (e) => show(e instanceof Error ? e.message : "クローンに失敗しました", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <BottomSheet title="リポジトリを追加" onClose={onClose}>
      <div className="space-y-3">
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repo.git" className={input} />
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="名前（省略時は URL から）" className={input} />
        <p className="text-xs text-zinc-400">
          追加するとクローンが実行され、管理フォルダ（~/ControlDeckApps）に格納されます。
          非公開リポジトリは先に「GitHub にログイン」を実行してください。
        </p>
        <button
          onClick={() => create.mutate()}
          disabled={!url.trim() || create.isPending}
          className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
        >
          {create.isPending ? "クローン中..." : "クローンして追加"}
        </button>
      </div>
    </BottomSheet>
  );
}

function SaveSheet({ repo, onClose, onDone }: { repo: Repo; onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [message, setMessage] = useState("");
  const save = useMutation({
    mutationFn: () => api<{ detail: string }>(`/gitrepos/${repo.id}/save`, { method: "POST", json: { message } }),
    onSuccess: (r) => {
      show(r.detail.split("\n")[0] || "保存しました");
      onDone();
    },
    onError: (e) => show(e instanceof Error ? e.message : "保存に失敗しました", "error"),
  });
  return (
    <BottomSheet title={`「${repo.name}」を保存`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-xs text-zinc-400">現在の変更をコミットとして記録します（リバートで戻せます）。</p>
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="メモ（省略時は日時）"
          className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        />
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
        >
          {save.isPending ? "保存中..." : "保存する"}
        </button>
      </div>
    </BottomSheet>
  );
}

function RevertSheet({ repo, onClose, onDone }: { repo: Repo; onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [target, setTarget] = useState<LogEntry | null>(null);
  const { data: log, isLoading } = useQuery({
    queryKey: ["gitrepo-log", repo.id],
    queryFn: () => api<LogEntry[]>(`/gitrepos/${repo.id}/log`),
  });
  const revert = useMutation({
    mutationFn: (sha: string) => api(`/gitrepos/${repo.id}/revert`, { method: "POST", json: { sha } }),
    onSuccess: () => {
      show("リバートしました");
      onDone();
    },
    onError: (e) => show(e instanceof Error ? e.message : "リバートに失敗しました", "error"),
  });
  return (
    <BottomSheet title={`「${repo.name}」をリバート`} onClose={onClose}>
      <p className="mb-3 text-xs text-zinc-400">
        戻したい時点を選択してください。選択時点より後の変更は破棄されます。
      </p>
      {isLoading ? (
        <Skeleton className="h-24" />
      ) : (
        <ul className="max-h-80 space-y-1.5 overflow-y-auto">
          {(log ?? []).map((l, i) => (
            <li key={l.sha}>
              <button
                onClick={() => setTarget(l)}
                className="flex w-full items-center gap-3 rounded-xl border border-zinc-200 px-3 py-2.5 text-left hover:border-accent-400 dark:border-zinc-700"
              >
                <span className="num shrink-0 font-mono text-xs text-zinc-400">{l.sha.slice(0, 7)}</span>
                <span className="min-w-0 flex-1 truncate text-sm">{l.message}</span>
                <span className="shrink-0 text-xs text-zinc-400">
                  {i === 0 ? "現在" : timeAgo(l.time)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {target && (
        <ConfirmDialog
          title={`${target.sha.slice(0, 7)} に戻しますか？`}
          message={`「${target.message}」の時点に戻します。それ以降の変更は失われます。`}
          confirmLabel="リバートする"
          busy={revert.isPending}
          onConfirm={() => revert.mutate(target.sha)}
          onClose={() => setTarget(null)}
        />
      )}
    </BottomSheet>
  );
}

function DeleteSheet({ repo, onClose, onDone }: { repo: Repo; onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [deleteFiles, setDeleteFiles] = useState(false);
  const del = useMutation({
    mutationFn: () => api(`/gitrepos/${repo.id}`, { method: "DELETE", json: { delete_files: deleteFiles } }),
    onSuccess: () => {
      show("削除しました");
      onDone();
    },
    onError: (e) => show(e instanceof Error ? e.message : "削除に失敗しました", "error"),
  });
  return (
    <BottomSheet title={`「${repo.name}」を削除`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-zinc-500">登録を解除します。</p>
        <label className="flex items-center gap-2 rounded-xl border border-zinc-200 px-4 py-3 text-sm dark:border-zinc-700">
          <input type="checkbox" checked={deleteFiles} onChange={(e) => setDeleteFiles(e.target.checked)} className="h-5 w-5 accent-current" />
          フォルダごと削除する（取り消せません）
        </label>
        <button
          onClick={() => del.mutate()}
          disabled={del.isPending}
          className="w-full rounded-xl bg-red-600 py-2.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-40"
        >
          {del.isPending ? "削除中..." : "削除する"}
        </button>
      </div>
    </BottomSheet>
  );
}
