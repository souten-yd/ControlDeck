import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { BottomSheet } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

export function PasswordSettings() {
  const navigate = useNavigate();
  const show = useToasts((state) => state.show);
  const [open, setOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);

  const close = () => {
    if (busy) return;
    setOpen(false);
    setCurrentPassword("");
    setNewPassword("");
    setConfirmation("");
  };

  const submit = async () => {
    if (newPassword !== confirmation) {
      show("新しいパスワードが一致しません", "error");
      return;
    }
    setBusy(true);
    try {
      await api("/auth/password", {
        method: "POST",
        json: { current_password: currentPassword, new_password: newPassword },
      });
      useAuth.getState().setUser(null);
      show("パスワードを変更しました。もう一度ログインしてください");
      navigate("/login", { replace: true });
    } catch (error) {
      show(error instanceof Error ? error.message : "パスワードを変更できませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-sm">パスワード</p>
        <p className="mt-0.5 text-xs text-zinc-400">変更すると、この端末を含む全セッションからログアウトします</p>
      </div>
      <button
        type="button"
        aria-label="パスワードを変更"
        onClick={() => setOpen(true)}
        className="shrink-0 rounded-lg bg-accent-50 px-3 py-1.5 text-xs font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
      >
        変更
      </button>

      {open && (
        <BottomSheet title="パスワードを変更" onClose={close}>
          <p className="mb-4 text-sm text-zinc-500 dark:text-zinc-400">
            本人確認のため現在のパスワードが必要です。完了後は全端末で再ログインしてください。
          </p>
          <div className="space-y-3">
            <label className="block text-sm">
              <span className="mb-1 block text-xs text-zinc-500">現在のパスワード</span>
              <input
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 dark:border-zinc-700 dark:bg-zinc-900"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-xs text-zinc-500">新しいパスワード（8文字以上）</span>
              <input
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 dark:border-zinc-700 dark:bg-zinc-900"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-xs text-zinc-500">新しいパスワード（確認）</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 dark:border-zinc-700 dark:bg-zinc-900"
              />
            </label>
          </div>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={busy || !currentPassword || newPassword.length < 8 || newPassword !== confirmation}
            className="mt-5 min-h-11 w-full rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-40"
          >
            {busy ? "変更中..." : "変更して全端末からログアウト"}
          </button>
        </BottomSheet>
      )}
    </div>
  );
}
