import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { BottomSheet, ConfirmDialog, Skeleton } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

interface ManagedUser {
  id: number;
  username: string;
  display_name: string;
  role_id: number;
  role_name: string;
  is_active: boolean;
  totp_enabled: boolean;
  created_at: string;
  last_login_at: string | null;
}

interface ManagedRole {
  id: number;
  name: string;
  permissions: string[];
  preset: boolean;
  user_count: number;
}

export function AccessManagement() {
  const [newUser, setNewUser] = useState(false);
  const [editingUser, setEditingUser] = useState<ManagedUser | null>(null);
  const [newRole, setNewRole] = useState(false);
  const [editingRole, setEditingRole] = useState<ManagedRole | null>(null);
  const { data: users } = useQuery({ queryKey: ["managed-users"], queryFn: () => api<ManagedUser[]>("/users") });
  const { data: roles } = useQuery({ queryKey: ["managed-roles"], queryFn: () => api<ManagedRole[]>("/roles") });
  const { data: permissions } = useQuery({ queryKey: ["managed-permissions"], queryFn: () => api<string[]>("/roles/permissions") });

  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-500">ユーザーとロール</h2>
          <p className="mt-1 text-xs text-zinc-400">アカウント、Customロール、最小権限を管理します</p>
        </div>
        <button
          type="button"
          onClick={() => setNewUser(true)}
          className="shrink-0 rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700"
        >
          ユーザー追加
        </button>
      </div>

      <div className="mt-4">
        <h3 className="mb-1 text-xs font-semibold text-zinc-500">ユーザー</h3>
        {!users ? <Skeleton className="h-16" /> : (
          <ul className="max-h-72 divide-y divide-zinc-100 overflow-y-auto dark:divide-zinc-800">
            {users.map((user) => (
              <li key={user.id} className="flex min-h-12 items-center gap-3 py-2 text-sm">
                <span className={`h-2 w-2 shrink-0 rounded-full ${user.is_active ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-700"}`} />
                <div className="min-w-0 flex-1">
                  <p className="truncate">{user.display_name || user.username}</p>
                  <p className="truncate text-xs text-zinc-400">{user.username} · {user.role_name}{user.totp_enabled ? " · TOTP" : ""}</p>
                </div>
                <button type="button" onClick={() => setEditingUser(user)} className="shrink-0 text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">
                  編集
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-5 border-t border-zinc-100 pt-4 dark:border-zinc-800">
        <div className="mb-1 flex items-center justify-between gap-3">
          <h3 className="text-xs font-semibold text-zinc-500">ロール</h3>
          <button type="button" onClick={() => setNewRole(true)} className="text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">
            Customロールを追加
          </button>
        </div>
        {!roles ? <Skeleton className="h-16" /> : (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {roles.map((role) => (
              <li key={role.id} className="flex min-h-11 items-center gap-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="truncate">{role.name}</p>
                  <p className="text-xs text-zinc-400">{role.permissions.length}権限 · {role.user_count}ユーザー{role.preset ? " · preset" : ""}</p>
                </div>
                {!role.preset && (
                  <button type="button" onClick={() => setEditingRole(role)} className="shrink-0 text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">
                    編集
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {newUser && roles && <UserForm roles={roles} onClose={() => setNewUser(false)} />}
      {editingUser && roles && <UserForm user={editingUser} roles={roles} onClose={() => setEditingUser(null)} />}
      {newRole && permissions && <RoleForm permissions={permissions} onClose={() => setNewRole(false)} />}
      {editingRole && permissions && <RoleForm role={editingRole} permissions={permissions} onClose={() => setEditingRole(null)} />}
    </section>
  );
}

export default AccessManagement;

function UserForm({ user, roles, onClose }: { user?: ManagedUser; roles: ManagedRole[]; onClose: () => void }) {
  const currentUserId = useAuth((state) => state.user?.id);
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [username, setUsername] = useState(user?.username ?? "");
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [password, setPassword] = useState("");
  const [roleId, setRoleId] = useState(user?.role_id ?? roles.find((role) => role.name === "viewer")?.id ?? roles[0]?.id ?? 0);
  const [active, setActive] = useState(user?.is_active ?? true);
  const self = user?.id === currentUserId;
  const changed = !user || displayName.trim() !== user.display_name || roleId !== user.role_id || active !== user.is_active || password.length > 0;
  const mutation = useMutation({
    mutationFn: () => user
      ? api<ManagedUser>(`/users/${user.id}`, {
          method: "PATCH",
          json: {
            display_name: displayName,
            role_id: roleId,
            is_active: active,
            ...(password ? { new_password: password } : {}),
          },
        })
      : api<ManagedUser>("/users", {
          method: "POST",
          json: { username, display_name: displayName, password, role_id: roleId },
        }),
    onSuccess: () => {
      show(user ? "ユーザーを更新しました" : "ユーザーを追加しました");
      void qc.invalidateQueries({ queryKey: ["managed-users"] });
      void qc.invalidateQueries({ queryKey: ["managed-roles"] });
      onClose();
    },
    onError: (error) => show(error instanceof Error ? error.message : "保存できませんでした", "error"),
  });
  const input = "mt-1 w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <BottomSheet title={user ? "ユーザーを編集" : "ユーザーを追加"} onClose={onClose}>
      <div className="space-y-3">
        <label className="block text-xs text-zinc-500">ユーザー名
          <input aria-label="管理ユーザー名" value={username} disabled={Boolean(user)} onChange={(event) => setUsername(event.target.value)} autoCapitalize="none" className={input} />
        </label>
        <label className="block text-xs text-zinc-500">表示名
          <input aria-label="管理表示名" value={displayName} onChange={(event) => setDisplayName(event.target.value)} className={input} />
        </label>
        <label className="block text-xs text-zinc-500">{user ? "新しいパスワード（変更時のみ）" : "初期パスワード（8文字以上）"}
          <input aria-label="管理パスワード" type="password" value={password} disabled={self} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" className={input} />
        </label>
        <label className="block text-xs text-zinc-500">ロール
          <select aria-label="管理ロール" value={roleId} disabled={self} onChange={(event) => setRoleId(Number(event.target.value))} className={input}>
            {roles.map((role) => <option key={role.id} value={role.id}>{role.name}（{role.permissions.length}権限）</option>)}
          </select>
        </label>
        {user && (
          <label className="flex min-h-11 items-center justify-between rounded-xl border border-zinc-200 px-3.5 dark:border-zinc-700">
            <span className="text-sm">アカウントを有効化</span>
            <input type="checkbox" checked={active} disabled={self} onChange={(event) => setActive(event.target.checked)} className="h-5 w-5 accent-current" />
          </label>
        )}
        {self && <p className="text-xs text-zinc-400">自分自身のロール・状態・パスワードは、この管理画面から変更できません。</p>}
        <button
          type="button"
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending || !changed || !roleId || (!user && (username.length < 3 || password.length < 8)) || (Boolean(user) && password.length > 0 && password.length < 8)}
          className="min-h-11 w-full rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-40"
        >
          {mutation.isPending ? "保存中..." : "保存"}
        </button>
      </div>
    </BottomSheet>
  );
}

function RoleForm({ role, permissions, onClose }: { role?: ManagedRole; permissions: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [name, setName] = useState(role?.name ?? "");
  const [selected, setSelected] = useState<string[]>(role?.permissions ?? []);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const changed = !role || [...selected].sort().join("\n") !== [...role.permissions].sort().join("\n");
  const save = useMutation({
    mutationFn: () => role
      ? api<ManagedRole>(`/roles/${role.id}`, { method: "PATCH", json: { permissions: selected } })
      : api<ManagedRole>("/roles", { method: "POST", json: { name, permissions: selected } }),
    onSuccess: () => {
      show(role ? "ロールを更新しました" : "Customロールを追加しました");
      void qc.invalidateQueries({ queryKey: ["managed-roles"] });
      onClose();
    },
    onError: (error) => show(error instanceof Error ? error.message : "保存できませんでした", "error"),
  });
  const remove = useMutation({
    mutationFn: () => api(`/roles/${role!.id}`, { method: "DELETE" }),
    onSuccess: () => {
      show("ロールを削除しました");
      void qc.invalidateQueries({ queryKey: ["managed-roles"] });
      onClose();
    },
    onError: (error) => { setConfirmDelete(false); show(error instanceof Error ? error.message : "削除できませんでした", "error"); },
  });
  return (
    <BottomSheet title={role ? "Customロールを編集" : "Customロールを追加"} onClose={onClose} wide>
      <label className="block text-xs text-zinc-500">ロール名
        <input aria-label="Customロール名" value={name} disabled={Boolean(role)} onChange={(event) => setName(event.target.value)} autoCapitalize="none" className="mt-1 w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
      </label>
      <fieldset className="mt-4">
        <legend className="mb-2 text-xs font-semibold text-zinc-500">権限</legend>
        <div className="grid max-h-[45dvh] grid-cols-1 gap-1 overflow-y-auto sm:grid-cols-2">
          {permissions.map((permission) => (
            <label key={permission} className="flex min-h-10 items-center gap-2 rounded-lg px-2 text-xs hover:bg-zinc-50 dark:hover:bg-zinc-800">
              <input
                type="checkbox"
                checked={selected.includes(permission)}
                onChange={(event) => setSelected(event.target.checked ? [...selected, permission] : selected.filter((item) => item !== permission))}
                className="h-4 w-4 accent-current"
              />
              <span className="font-mono">{permission}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="mt-4 flex gap-2">
        {role && (
          <button type="button" onClick={() => setConfirmDelete(true)} className="min-h-11 rounded-xl px-4 text-sm font-medium text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/30">
            削除
          </button>
        )}
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending || name.length < 3 || !changed} className="min-h-11 flex-1 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-40">
          {save.isPending ? "保存中..." : "保存"}
        </button>
      </div>
      {confirmDelete && role && (
        <ConfirmDialog
          title="Customロールを削除しますか？"
          message={`「${role.name}」を削除します。利用中のロールは削除できません。`}
          confirmLabel="削除する"
          busy={remove.isPending}
          onConfirm={() => remove.mutate()}
          onClose={() => setConfirmDelete(false)}
        />
      )}
    </BottomSheet>
  );
}
