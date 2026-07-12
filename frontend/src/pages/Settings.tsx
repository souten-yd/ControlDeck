import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { ACCENTS, useAuth, useTheme, useToasts, type Theme } from "../stores";
import { Skeleton } from "../components/ui";
import { AlertsSettings } from "../features/alerts/AlertsSettings";

interface SessionInfo {
  id: number;
  ip_address: string;
  user_agent: string;
  created_at: string;
  last_seen_at: string;
  current: boolean;
}

interface AuditEntry {
  id: number;
  timestamp: string;
  username: string;
  action: string;
  resource_type: string;
  resource_id: string;
  result: string;
  ip_address: string;
}

export default function SettingsPage() {
  const user = useAuth((s) => s.user);
  const can = useAuth((s) => s.can);
  const theme = useTheme((s) => s.theme);
  const setTheme = useTheme((s) => s.setTheme);
  const accent = useTheme((s) => s.accent);
  const setAccent = useTheme((s) => s.setAccent);
  const oled = useTheme((s) => s.oled);
  const setOled = useTheme((s) => s.setOled);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      <h1 className="text-lg font-semibold">設定</h1>

      <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-500">アカウント</h2>
        <dl className="space-y-2 text-sm">
          <div className="flex gap-4">
            <dt className="w-28 text-zinc-400">ユーザー名</dt>
            <dd>{user?.username}</dd>
          </div>
          <div className="flex gap-4">
            <dt className="w-28 text-zinc-400">ロール</dt>
            <dd>{user?.role}</dd>
          </div>
          <div className="flex gap-4">
            <dt className="w-28 text-zinc-400">二要素認証</dt>
            <dd className="text-zinc-400">
              {user?.totp_enabled ? "有効" : "未設定（Phase 7 で対応予定）"}
            </dd>
          </div>
        </dl>
      </section>

      <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
        <h2 className="mb-3 text-sm font-semibold text-zinc-500">外観</h2>
        <p className="mb-2 text-xs text-zinc-400">モード</p>
        <div className="flex gap-2">
          {(["system", "light", "dark"] as Theme[]).map((t) => (
            <button
              key={t}
              onClick={() => setTheme(t)}
              aria-pressed={theme === t}
              className={`rounded-xl px-4 py-2 text-sm font-medium ${
                theme === t
                  ? "bg-accent-600 text-white"
                  : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300"
              }`}
            >
              {t === "system" ? "システム" : t === "light" ? "ライト" : "ダーク"}
            </button>
          ))}
        </div>

        <p className="mb-2 mt-5 text-xs text-zinc-400">アクセントカラー</p>
        <div className="flex flex-wrap gap-3">
          {ACCENTS.map((a) => (
            <button
              key={a.id}
              onClick={() => setAccent(a.id)}
              aria-label={a.label}
              aria-pressed={accent === a.id}
              title={a.label}
              className={`grid h-11 w-11 place-items-center rounded-full transition-transform hover:scale-105 ${
                accent === a.id
                  ? "ring-2 ring-offset-2 ring-zinc-400 dark:ring-zinc-500 dark:ring-offset-zinc-900"
                  : ""
              }`}
              style={{ backgroundColor: a.color }}
            >
              {accent === a.id && (
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="m5 12 5 5L20 7" />
                </svg>
              )}
            </button>
          ))}
        </div>

        <label className="mt-5 flex items-center justify-between rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-700">
          <span>
            <span className="block text-sm">OLED 完全黒</span>
            <span className="block text-xs text-zinc-400">
              ダークモード時に背景を純黒にします（有機 EL の省電力向け）
            </span>
          </span>
          <input
            type="checkbox"
            checked={oled}
            onChange={(e) => setOled(e.target.checked)}
            className="h-5 w-5 accent-current"
          />
        </label>
      </section>

      {can("system.view") && <AlertsSettings />}

      <SessionsSection />

      {can("audit.view") && <AuditSection />}
    </div>
  );
}

function SessionsSection() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data: sessions } = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api<SessionInfo[]>("/auth/sessions"),
  });

  const revoke = async (id: number) => {
    try {
      await api(`/auth/sessions/${id}`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      show("セッションを失効しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "失効に失敗しました", "error");
    }
  };

  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="mb-3 text-sm font-semibold text-zinc-500">アクティブなセッション</h2>
      {!sessions ? (
        <Skeleton className="h-16" />
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {sessions.map((s) => (
            <li key={s.id} className="flex items-center gap-3 py-2.5 text-sm">
              <div className="min-w-0 flex-1">
                <p className="truncate">
                  {s.ip_address}
                  {s.current && (
                    <span className="ml-2 rounded bg-accent-50 px-1.5 py-0.5 text-[10px] font-medium text-accent-700 dark:bg-accent-600/15 dark:text-accent-400">
                      現在のセッション
                    </span>
                  )}
                </p>
                <p className="truncate text-xs text-zinc-400">{s.user_agent}</p>
              </div>
              {!s.current && (
                <button
                  onClick={() => revoke(s.id)}
                  className="shrink-0 text-xs font-medium text-red-600 hover:underline dark:text-red-400"
                >
                  失効
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AuditSection() {
  const { data: entries } = useQuery({
    queryKey: ["audit"],
    queryFn: () => api<AuditEntry[]>("/audit?limit=50"),
    refetchInterval: 30_000,
  });
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="mb-3 text-sm font-semibold text-zinc-500">監査ログ（最近 50 件）</h2>
      {!entries ? (
        <Skeleton className="h-24" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-zinc-400">
                <th className="py-1.5 pr-3 font-medium">時刻</th>
                <th className="py-1.5 pr-3 font-medium">ユーザー</th>
                <th className="py-1.5 pr-3 font-medium">操作</th>
                <th className="py-1.5 pr-3 font-medium">対象</th>
                <th className="py-1.5 font-medium">結果</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="num whitespace-nowrap py-1.5 pr-3 text-zinc-400">
                    {new Date(e.timestamp + "Z").toLocaleString("ja-JP")}
                  </td>
                  <td className="py-1.5 pr-3">{e.username}</td>
                  <td className="py-1.5 pr-3 font-mono">{e.action}</td>
                  <td className="py-1.5 pr-3 text-zinc-400">
                    {e.resource_type && `${e.resource_type}/${e.resource_id}`}
                  </td>
                  <td
                    className={`py-1.5 ${e.result === "success" ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}
                  >
                    {e.result}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
