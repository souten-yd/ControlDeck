import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { IconPlus, IconTrash } from "../components/icons";

const RemoteViewer = lazy(() => import("../features/remote/RemoteViewer"));

interface Connection {
  id: number;
  name: string;
  protocol: string;
  host: string;
  port: number;
  username: string;
  has_password: boolean;
  is_self: boolean;
}

const PROTO_LABEL: Record<string, string> = { rdp: "RDP", vnc: "VNC", ssh: "SSH" };

export default function RemotePage() {
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [active, setActive] = useState<Connection | null>(null);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<Connection | null>(null);

  const { data: status } = useQuery({ queryKey: ["remote-status"], queryFn: () => api<{ guacd_available: boolean }>("/remote/status") });
  const { data: connections, isLoading } = useQuery({ queryKey: ["remote-connections"], queryFn: () => api<Connection[]>("/remote/connections") });

  const remove = useMutation({
    mutationFn: (id: number) => api(`/remote/connections/${id}`, { method: "DELETE" }),
    onSuccess: () => { show("削除しました"); setDeleting(null); qc.invalidateQueries({ queryKey: ["remote-connections"] }); },
  });

  if (active) {
    return (
      <Suspense fallback={<div className="grid h-full place-items-center text-sm text-zinc-400">接続中...</div>}>
        <RemoteViewer connection={active} onExit={() => setActive(null)} />
      </Suspense>
    );
  }

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">リモートデスクトップ</h1>
        {can("remote_desktop.use") && (
          <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700">
            <IconPlus /> 接続を追加
          </button>
        )}
      </div>

      {status && !status.guacd_available && (
        <p className="mb-4 rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
          guacd が見つかりません。リモート接続には <code className="font-mono">sudo apt install guacd</code> が必要です
          （<code className="font-mono">./deck.sh</code> 実行時に自動導入を試みます）。
        </p>
      )}

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : (
        (() => {
          const selfConn = connections?.find((c) => c.is_self) ?? null;
          const others = connections?.filter((c) => !c.is_self) ?? [];
          const canConnect = !!status?.guacd_available;
          return (
            <div className="space-y-4">
              {/* この PC（最上段固定・削除不可） */}
              {selfConn && (
                <div className="rounded-2xl border-2 border-accent-300 bg-accent-50/40 p-4 dark:border-accent-800 dark:bg-accent-600/10">
                  <div className="flex items-center gap-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent-600 text-white">🖥</span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">
                        {selfConn.name}
                        <span className="ml-2 rounded bg-accent-600/15 px-1.5 py-0.5 text-[10px] font-medium text-accent-700 dark:text-accent-300">この PC</span>
                      </p>
                      <p className="num truncate text-xs text-zinc-500 dark:text-zinc-400">自分のデスクトップ（ヘッドレス）</p>
                    </div>
                    <button
                      onClick={() => setActive(selfConn)}
                      disabled={!canConnect}
                      className="rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
                    >
                      接続
                    </button>
                  </div>
                  <p className="mt-2 text-[11px] text-zinc-400">
                    この接続は deck.sh enable-desktop で管理され、削除・変更はできません。
                  </p>
                </div>
              )}

              {/* その他のリモート接続 */}
              {others.length > 0 && (
                <ul className="space-y-3">
                  {others.map((c) => (
                    <li key={c.id} className="flex items-center gap-3 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500 dark:bg-zinc-800">{PROTO_LABEL[c.protocol]}</span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{c.name}</p>
                        <p className="num truncate text-xs text-zinc-400">{c.username && `${c.username}@`}{c.host}:{c.port}</p>
                      </div>
                      <button
                        onClick={() => setActive(c)}
                        disabled={!canConnect}
                        className="rounded-xl bg-accent-50 px-3.5 py-2 text-sm font-medium text-accent-700 hover:bg-accent-100 disabled:opacity-40 dark:bg-accent-600/15 dark:text-accent-400"
                      >
                        接続
                      </button>
                      {can("remote_desktop.use") && (
                        <button onClick={() => setDeleting(c)} aria-label={`${c.name} を削除`} className="rounded-lg p-2 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40">
                          <IconTrash />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}

              {!selfConn && others.length === 0 && (
                <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
                  <p className="text-sm text-zinc-400">
                    RDP / VNC / SSH の接続を追加してブラウザから操作できます。
                    <br />この PC 自身は <code className="font-mono">./deck.sh enable-desktop</code> で追加されます。
                  </p>
                </div>
              )}
            </div>
          );
        })()
      )}

      {adding && <ConnectionForm onClose={() => setAdding(false)} />}
      {deleting && (
        <ConfirmDialog
          title={`「${deleting.name}」を削除しますか？`}
          message="この接続設定を削除します。"
          confirmLabel="削除する"
          busy={remove.isPending}
          onConfirm={() => remove.mutate(deleting.id)}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

function ConnectionForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [form, setForm] = useState({ name: "", protocol: "rdp", host: "", port: "", username: "", password: "", security: "" });
  const create = useMutation({
    mutationFn: () =>
      api("/remote/connections", {
        method: "POST",
        json: {
          name: form.name, protocol: form.protocol, host: form.host,
          port: form.port ? Number(form.port) : null, username: form.username, password: form.password,
          security: form.protocol === "rdp" ? form.security : "",
        },
      }),
    onSuccess: () => { show("追加しました"); qc.invalidateQueries({ queryKey: ["remote-connections"] }); onClose(); },
    onError: (e) => show(e instanceof Error ? e.message : "追加に失敗しました", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <BottomSheet title="リモート接続を追加" onClose={onClose}>
      <div className="space-y-3">
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="接続名" className={input} />
        <select value={form.protocol} onChange={(e) => setForm({ ...form, protocol: e.target.value })} className={input}>
          <option value="rdp">RDP（Windows リモートデスクトップ）</option>
          <option value="vnc">VNC</option>
          <option value="ssh">SSH</option>
        </select>
        <div className="flex gap-2">
          <input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="ホスト / IP" className={input} />
          <input value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} placeholder="ポート" inputMode="numeric" className={`${input} w-28`} />
        </div>
        <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder="ユーザー名" className={input} />
        <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="パスワード" autoComplete="new-password" className={input} />
        {form.protocol === "rdp" && (
          <label className="block text-xs text-zinc-500">
            RDP セキュリティ
            <select value={form.security} onChange={(e) => setForm({ ...form, security: e.target.value })} className={`${input} mt-1`}>
              <option value="">自動（any／xrdp・多くのサーバー）</option>
              <option value="nla">NLA（Windows 既定）</option>
              <option value="tls">TLS</option>
              <option value="rdp">標準 RDP</option>
            </select>
          </label>
        )}
        <button onClick={() => create.mutate()} disabled={!form.name || !form.host || create.isPending} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          追加
        </button>
      </div>
    </BottomSheet>
  );
}
