import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useMeta } from "../api/hooks";
import { useAuth } from "../stores";
import { Logo } from "../components/Logo";
import type { UserInfo } from "../types";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const setUser = useAuth((s) => s.setUser);
  const { data: meta } = useMeta();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await api<UserInfo>("/auth/login", {
        method: "POST",
        json: { username, password, totp_code: totpCode || undefined },
      });
      setUser(user);
      navigate("/", { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "ログインに失敗しました";
      if (msg === "two_factor_required") {
        setNeedTotp(true);
        setError(null);
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="safe-top safe-bottom flex min-h-dvh items-center justify-center px-6">
      <form onSubmit={submit} className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <Logo size={56} />
          <h1 className="text-lg font-semibold">
            {meta?.app_name ?? "Ubuntu Control Deck"}
          </h1>
        </div>
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-zinc-500">
              ユーザー名
            </span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              required
              className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-zinc-500">
              パスワード
            </span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              disabled={needTotp}
              className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          {needTotp && (
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-zinc-500">
                認証コード（6 桁 / リカバリーコード）
              </span>
              <input
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="000000"
                className="num w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-center text-lg tracking-widest outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-700 dark:bg-zinc-900"
              />
              <span className="mt-1 block text-xs text-zinc-400">
                認証アプリのコードを入力してください
              </span>
            </label>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-50"
          >
            {busy ? "確認中..." : needTotp ? "認証してログイン" : "ログイン"}
          </button>
          {needTotp && (
            <button
              type="button"
              onClick={() => { setNeedTotp(false); setTotpCode(""); setError(null); }}
              className="w-full text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
            >
              戻る
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
