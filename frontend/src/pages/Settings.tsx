import { lazy, Suspense, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { ACCENTS, useAuth, useTheme, useToasts, type Theme } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { AlertsSettings } from "../features/alerts/AlertsSettings";
import { TotpSettings } from "../features/auth/TotpSettings";
import { PasswordSettings } from "../features/auth/PasswordSettings";
import { PageHeader } from "../components/PageHeader";
import { MobileNavigationSettings } from "../features/settings/MobileNavigationSettings";
import { addonLabel, useInstalledAddons, type AddonActivity, type InstalledAddon } from "../api/addons";
import { AddonStatusChip, addonStateMessage } from "../features/addons/AddonStatus";

const AccessManagement = lazy(() => import("../features/auth/AccessManagement"));

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

  if (user?.totp_required && !user.totp_enabled) {
    return (
      <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
        <PageHeader title="Settings" />
        <section className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4 dark:border-amber-900 dark:bg-amber-950/20 md:p-5">
          <h2 className="text-sm font-semibold text-amber-900 dark:text-amber-200">二要素認証の設定が必要です</h2>
          <p className="mt-1 text-xs leading-relaxed text-amber-800/80 dark:text-amber-300/80">
            管理者がTOTPを必須にしています。設定が完了するまで、ほかの機能とWebSocket接続は利用できません。
          </p>
          <div className="mt-4 border-t border-amber-200 pt-4 dark:border-amber-900">
            <TotpSettings />
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      <PageHeader title="Settings" />

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
        </dl>
        <div className="mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800">
          <TotpSettings />
        </div>
        <div className="mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800">
          <PasswordSettings />
        </div>
      </section>

      {can("users.manage") && (
        <Suspense fallback={<Skeleton className="h-40 rounded-2xl" />}>
          <AccessManagement />
        </Suspense>
      )}

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

      <MobileNavigationSettings />

      {can("settings.manage") && <TerminalV2PhysicalCheckSection />}

      {can("settings.manage") && <AddonsSection />}
      {can("settings.manage") && <ExtensionsSection />}

      {can("system.view") && <AlertsSettings />}

      {can("settings.manage") && <BackupSection />}

      <SessionsSection />

      {can("audit.view") && <AuditSection />}
    </div>
  );
}

function TerminalV2PhysicalCheckSection() {
  const navigate = useNavigate();
  const standalone =
    (window.navigator as Navigator & { standalone?: boolean }).standalone === true
    || window.matchMedia("(display-mode: standalone)").matches;

  return (
    <section aria-labelledby="terminal-v2-physical-check-title" className="rounded-2xl border border-sky-200 bg-sky-50/60 p-4 dark:border-sky-900 dark:bg-sky-950/20 md:p-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 id="terminal-v2-physical-check-title" className="text-sm font-semibold text-sky-900 dark:text-sky-200">
            Terminal V2 Physical Check
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-sky-800/80 dark:text-sky-300/80">
            物理iPhone Safari／ホーム画面PWA専用のLab入口です。既存SessionはV1のまま開き、明示的に作成したV2検証Sessionだけを使用します。
          </p>
        </div>
        <div className="flex shrink-0 gap-1.5 text-[10px] font-semibold">
          <span className={`rounded-full px-2 py-1 ${window.isSecureContext ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"}`}>
            {window.isSecureContext ? "Secure context" : "HTTPS required"}
          </span>
          <span className={`rounded-full px-2 py-1 ${standalone ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : "bg-white text-sky-700 dark:bg-sky-950 dark:text-sky-300"}`}>
            {standalone ? "Standalone PWA" : "Browser"}
          </span>
        </div>
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
        SafariではHTTPS URLを開いて確認後「ホーム画面に追加」し、PWAではこのSettingsから再度Labへ入ってください。Labを開くだけではTerminalの作成・接続・終了を行いません。
      </p>
      <details className="mt-2 rounded-xl border border-sky-200 bg-white/70 px-3 dark:border-sky-900 dark:bg-zinc-950/40">
        <summary className="flex min-h-11 cursor-pointer items-center text-xs font-semibold text-sky-800 dark:text-sky-300">
          Physical check手順
        </summary>
        <ol className="mb-3 list-decimal space-y-1.5 pl-5 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
          <li>SafariとStandalone PWAで別々のV2検証Sessionを作り、表示されたSession IDを記録する。</li>
          <li>日本語変換の確定、Backspace、Enter、全helper key、Paste／上swipe Copyを確認する。</li>
          <li>100KB／300KB／絵文字入りPasteの完了、cancel、retryを確認する。</li>
          <li>本文swipeと右端bar、縦横回転、keyboard開閉10往復で文字ずれ・横overflow・二重textareaがないことを確認する。</li>
          <li>background復帰、回線切断復帰、page reload後も同じSession ID／最新画面／実processを維持する。</li>
          <li>Terminal headerの「Lab」から再計測し、本文を含まないJSONレポートをSafari／Standalone PWAそれぞれでコピーする。</li>
          <li>終了する場合は、この手順で自分が作成して記録したSession IDだけを対象にする。</li>
        </ol>
      </details>
      <button
        type="button"
        onClick={() => navigate("/terminal?terminalLab=v2")}
        className="mt-3 min-h-11 w-full rounded-xl bg-sky-600 px-4 text-sm font-semibold text-white hover:bg-sky-700 sm:w-auto"
      >
        Open Terminal V2 Lab
      </button>
    </section>
  );
}

function BackupSection() {
  const show = useToasts((s) => s.show);
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/v1/system/backup", { headers: { "X-Requested-With": "ControlDeck" }, credentials: "same-origin" });
      if (!res.ok) throw new Error("バックアップに失敗しました");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = res.headers.get("content-disposition")?.match(/filename="?([^"]+)"?/)?.[1] ?? "control-deck-backup.tar.gz";
      a.click();
      URL.revokeObjectURL(url);
      show("バックアップをダウンロードしました");
    } catch (e) {
      show(e instanceof Error ? e.message : "失敗しました", "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="mb-3 text-sm font-semibold text-zinc-500">バックアップ</h2>
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-zinc-500">
          DB・設定・暗号鍵・アプリ定義をまとめてダウンロードします。
          <span className="mt-0.5 block text-xs text-zinc-400">復元は <code className="font-mono">./deck.sh restore &lt;ファイル&gt;</code> で行います。</span>
        </p>
        <button onClick={download} disabled={busy} className="shrink-0 rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          {busy ? "作成中..." : "ダウンロード"}
        </button>
      </div>
    </section>
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


interface AddonState {
  id: string; name: string; summary: string; kind: "npm" | "pip"; route_gated: boolean;
  available: boolean; installed: boolean; managed: boolean;
  enabled: boolean; requested_enabled: boolean; version: string; health: string;
  error: string; executable: string;
  /** 依存する別アドオンのid。未導入なら導入ボタンを止めて順序を案内する。 */
  requires?: string; requires_installed?: boolean;
}

interface PluginState {
  api_version: "1"; id: string; name: string; version: string; description: string; publisher: string;
  capabilities: string[]; navigation: { label: string; url: string; permission: string };
  installed: boolean; enabled: boolean;
}

const EXTENSION_EXAMPLE = JSON.stringify({
  api_version: "2", id: "example-extension", name: "Example Extension", version: "1.0.0",
  description: "Independent local service", publisher: "Your name",
  requires: { addon_contract: ">=2.0 <3.0" },
  runtime: { kind: "external-service", base_url: "http://127.0.0.1:9130", health_path: "/health" },
  host_capabilities: ["theme.read", "route.open"],
  contributions: {
    navigation: [{ id: "workspace", label: { en: "Example", ja: "サンプル" }, route: "/x/example-extension/workspace", icon: "grid" }],
  },
}, null, 2);

function ExtensionsSection() {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [manifestText, setManifestText] = useState(EXTENSION_EXAMPLE);
  const [showInstall, setShowInstall] = useState(false);
  const [removing, setRemoving] = useState<{ id: string; api: "v1" | "v2" } | null>(null);
  const [reviewing, setReviewing] = useState<InstalledAddon | null>(null);
  const [selected, setSelected] = useState<string | null>(searchParams.get("extension"));
  const { data: plugins } = useQuery({ queryKey: ["plugins"], queryFn: () => api<PluginState[]>("/plugins") });
  const { data: extensions } = useInstalledAddons();
  const selectedExtension = extensions?.find((item) => item.id === selected) ?? null;
  const { data: activity } = useQuery({
    queryKey: ["addon-activity", selected],
    queryFn: () => api<AddonActivity[]>(`/addons/${selected}/activity`),
    enabled: selected !== null,
  });

  useEffect(() => {
    const requested = searchParams.get("extension");
    if (requested) setSelected(requested);
  }, [searchParams]);

  const install = async () => {
    try {
      const parsed = JSON.parse(manifestText) as { api_version?: string };
      const endpoint = parsed.api_version === "1" ? "/plugins" : "/addons";
      await api(endpoint, { method: "POST", json: parsed });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["plugins"] }),
        qc.invalidateQueries({ queryKey: ["addons"] }),
      ]);
      setShowInstall(false);
      show("拡張機能のmanifestを登録しました");
    } catch (error) {
      show(error instanceof Error ? error.message : "manifestを登録できませんでした", "error");
    }
  };
  const actV1 = async (id: string, action: "enable" | "disable" | "uninstall") => {
    try {
      await api(`/plugins/${id}/${action}`, { method: "POST" });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["plugins"] }),
        qc.invalidateQueries({ queryKey: ["meta"] }),
      ]);
      show(action === "uninstall" ? "拡張機能を削除しました" : action === "enable" ? "拡張機能を有効化しました" : "拡張機能を無効化しました");
    } catch (error) {
      show(error instanceof Error ? error.message : "拡張機能の操作に失敗しました", "error");
    }
  };
  const actV2 = async (extension: InstalledAddon, action: "enable" | "disable" | "recheck" | "uninstall") => {
    try {
      if (action === "enable") {
        await api(`/addons/${extension.id}/enable`, { method: "POST", json: { granted_capabilities: extension.host_capabilities ?? [] } });
      } else if (action === "uninstall") {
        await api(`/addons/${extension.id}`, { method: "DELETE" });
      } else {
        await api(`/addons/${extension.id}/${action}`, { method: "POST" });
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["addons"] }),
        qc.invalidateQueries({ queryKey: ["addons-effective"] }),
        qc.invalidateQueries({ queryKey: ["addon-activity", extension.id] }),
      ]);
      setReviewing(null);
      if (action === "uninstall") setSelected(null);
      show(action === "enable" ? "拡張機能を有効化しました" : action === "disable" ? "拡張機能を無効化しました" : action === "recheck" ? "状態を再確認しました" : "拡張機能を削除しました");
    } catch (error) {
      show(error instanceof Error ? error.message : "拡張機能の操作に失敗しました", "error");
    }
  };

  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div><h2 className="text-sm font-semibold text-zinc-500">拡張機能</h2><p className="mt-1 text-xs text-zinc-400">別プロセスのサービスを、検証済みmanifestと明示した権限で接続します。従来形式も同じ一覧で管理します。</p></div>
        <button onClick={() => setShowInstall((value) => !value)} className="shrink-0 rounded-xl bg-accent-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-accent-700">manifest登録</button>
      </div>
      {showInstall && <div className="mb-3 space-y-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-950">
        <label className="block text-xs font-medium">control-deck-addon.json（api_version 1/2）</label>
        <textarea aria-label="Extension manifest JSON" value={manifestText} onChange={(event) => setManifestText(event.target.value)} spellCheck={false}
          className="h-56 w-full resize-y rounded-xl border border-zinc-200 bg-white p-3 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900" />
        <div className="flex justify-end gap-2"><button onClick={() => setShowInstall(false)} className="rounded-xl px-3 py-2 text-xs">キャンセル</button><button onClick={() => void install()} className="rounded-xl bg-accent-600 px-3 py-2 text-xs font-medium text-white">検証して登録</button></div>
      </div>}
      <div className="space-y-2.5">
        {(extensions ?? []).map((extension) => <div key={`v2-${extension.id}`} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${extension.enabled ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`} />
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-semibold">{extension.name}<span className="num ml-2 text-[10px] font-normal text-zinc-400">v{extension.version}</span></p><span className="rounded-full bg-zinc-100 px-2 py-0.5 text-[9px] font-semibold text-zinc-500 dark:bg-zinc-800">SERVICE</span><AddonStatusChip state={extension.state} /></div><p className="mt-1 truncate text-[11px] text-zinc-400">{extension.health?.message || addonStateMessage(extension.state)}</p></div>
            <button onClick={() => setSelected(extension.id)} className="rounded-xl px-3 py-2 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800">詳細</button>
            {extension.enabled ? <button onClick={() => void actV2(extension, "disable")} className="rounded-xl bg-zinc-100 px-3.5 py-2 text-xs font-medium dark:bg-zinc-800">無効化</button> : <button onClick={() => setReviewing(extension)} disabled={extension.state === "incompatible"} className="rounded-xl bg-accent-600 px-3.5 py-2 text-xs font-medium text-white disabled:opacity-40">有効化</button>}
            <button onClick={() => setRemoving({ id: extension.id, api: "v2" })} className="rounded-xl px-3 py-2 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40">削除</button>
          </div>
        </div>)}
        {(plugins ?? []).map((plugin) => <div key={plugin.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
          <span className={`h-2.5 w-2.5 rounded-full ${plugin.enabled ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`} />
          <div className="min-w-0 flex-1"><p className="text-sm font-semibold">{plugin.name}<span className="num ml-2 text-[10px] font-normal text-zinc-400">v{plugin.version}</span><span className="ml-2 rounded-full bg-zinc-100 px-2 py-0.5 text-[9px] font-semibold text-zinc-500 dark:bg-zinc-800">基本</span></p><p className="truncate text-[11px] text-zinc-400">{plugin.publisher} · {plugin.navigation.url}</p></div>
          <button onClick={() => void actV1(plugin.id, plugin.enabled ? "disable" : "enable")} className={`rounded-xl px-3.5 py-2 text-xs font-medium ${plugin.enabled ? "bg-zinc-100 dark:bg-zinc-800" : "bg-accent-600 text-white"}`}>{plugin.enabled ? "無効化" : "有効化"}</button>
          <button onClick={() => setRemoving({ id: plugin.id, api: "v1" })} className="rounded-xl px-3 py-2 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40">削除</button>
        </div>)}
        {plugins?.length === 0 && extensions?.length === 0 && <p className="text-xs text-zinc-400">登録済みの拡張機能はありません</p>}
      </div>
      {reviewing && <BottomSheet title="要求する権限を確認" onClose={() => setReviewing(null)}><p className="text-sm text-zinc-500">{reviewing.name} は次のhost機能を要求しています。許可後もfile/projectは都度選択します。</p><ul className="my-4 space-y-2">{(reviewing.host_capabilities ?? []).map((capability) => <li key={capability} className="rounded-xl bg-zinc-50 px-3 py-2 text-sm dark:bg-zinc-950">{capability}</li>)}{reviewing.host_capabilities?.length === 0 && <li className="text-sm text-zinc-400">追加のhost権限は要求していません</li>}</ul><button onClick={() => void actV2(reviewing, "enable")} className="min-h-11 w-full rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">許可して有効化</button></BottomSheet>}
      {selectedExtension && <BottomSheet title={selectedExtension.name} onClose={() => setSelected(null)} sideOnDesktop stable>
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2"><AddonStatusChip state={selectedExtension.state} /><span className="text-xs text-zinc-400">v{selectedExtension.version} · {selectedExtension.publisher}</span></div>
          <p className="text-sm text-zinc-500">{selectedExtension.health?.message || selectedExtension.description || addonStateMessage(selectedExtension.state)}</p>
          {selectedExtension.health?.setup && selectedExtension.health.setup.length > 0 && <div><h3 className="text-xs font-semibold text-zinc-500">セットアップ</h3><div className="mt-2 space-y-2">{selectedExtension.health.setup.map((item) => <div key={item.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><p className="text-sm font-medium">{item.label} · {item.state}</p><p className="text-xs text-zinc-400">{item.detail || item.message}</p></div>)}</div></div>}
          <div><h3 className="text-xs font-semibold text-zinc-500">付与した権限</h3><div className="mt-2 flex flex-wrap gap-1.5">{selectedExtension.granted_capabilities.map((item) => <span key={item} className="rounded-full bg-zinc-100 px-2 py-1 text-[10px] dark:bg-zinc-800">{item}</span>)}{selectedExtension.granted_capabilities.length === 0 && <span className="text-xs text-zinc-400">未付与</span>}</div></div>
          {selectedExtension.warnings.length > 0 && <div className="rounded-xl bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">このhostでは未対応の宣言が {selectedExtension.warnings.length} 件あります。</div>}
          {(selectedExtension.contributions?.settings ?? []).length > 0 && <div><h3 className="text-xs font-semibold text-zinc-500">設定項目</h3><div className="mt-2 space-y-1">{selectedExtension.contributions?.settings?.map((item) => <button key={item.id} onClick={() => item.route && navigate(item.route)} className="min-h-11 w-full rounded-xl border border-zinc-200 px-3 text-left text-sm dark:border-zinc-700">{addonLabel(item.label)}</button>)}</div></div>}
          <div><h3 className="text-xs font-semibold text-zinc-500">最近のアクティビティ</h3><div className="mt-2 space-y-1">{(activity ?? []).slice(0, 10).map((item) => <div key={`${item.at}-${item.method}`} className="flex justify-between gap-2 rounded-xl bg-zinc-50 px-3 py-2 text-xs dark:bg-zinc-950"><span className="truncate">{item.method}</span><span className="text-zinc-400">{item.result}</span></div>)}{activity?.length === 0 && <p className="text-xs text-zinc-400">記録はありません</p>}</div></div>
          <div className="flex flex-wrap gap-2"><button onClick={() => void actV2(selectedExtension, "recheck")} disabled={!selectedExtension.enabled} className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white disabled:opacity-40">再確認</button>{selectedExtension.enabled && <button onClick={() => void actV2(selectedExtension, "disable")} className="min-h-11 rounded-xl bg-zinc-100 px-4 text-sm dark:bg-zinc-800">無効化</button>}</div>
        </div>
      </BottomSheet>}
      {removing && <ConfirmDialog title="拡張機能を削除しますか？" message="Control Deckが管理するmanifestと有効化状態を削除します。外部service本体とdataは削除しません。" confirmLabel="削除する" onConfirm={() => { const target = removing; setRemoving(null); if (target.api === "v1") void actV1(target.id, "uninstall"); else { const extension = extensions?.find((item) => item.id === target.id); if (extension) void actV2(extension, "uninstall"); } }} onClose={() => setRemoving(null)} />}
    </section>
  );
}

/** アドオン管理: 導入（npmユーザー空間・sudo不要）/有効化/無効化/アンインストールをワンタップで。 */
function AddonsSection() {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobKind, setJobKind] = useState<"install" | "update">("install");
  const [uninstalling, setUninstalling] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const { data: addons } = useQuery({
    queryKey: ["features"],
    queryFn: () => api<AddonState[]>("/features"),
    refetchInterval: jobId ? false : 15_000,
  });
  const { data: job } = useQuery({
    queryKey: ["feature-job", jobId],
    queryFn: () => api<{
      status: string; error: string; progress?: { status?: string };
      result?: { version?: string; previous_version?: string };
    }>(`/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.status) ? false : 1200),
  });
  const jobStatus = job?.status;
  useEffect(() => {
    if (!jobId || !job || !jobStatus || ["queued", "running"].includes(jobStatus)) return;
    setJobId(null);
    if (jobStatus !== "succeeded") {
      show(job.error || (jobKind === "update" ? "更新に失敗しました" : "導入に失敗しました"), "error");
      return;
    }
    qc.invalidateQueries({ queryKey: ["features"] });
    if (jobKind === "install") {
      show("導入が完了しました。「有効化」で利用を開始できます");
      return;
    }
    const before = job.result?.previous_version?.replace(/^v/, "") ?? "";
    const after = job.result?.version?.replace(/^v/, "") ?? "";
    show(before && after && before !== after
      ? `v${before} から v${after} へ更新しました（起動中のセッションは再起動後に反映）`
      : `すでに最新版です${after ? `（v${after}）` : ""}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobStatus]);

  /** 有効化/無効化/アンインストール後の反映（ルート再登録）に既存の再読み込み機構を使う。 */
  const reloadPlatform = async () => {
    setReloading(true);
    try {
      await api("/system/platform/reload", { method: "POST" });
      await new Promise((resolve) => window.setTimeout(resolve, 1800));
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        try {
          const response = await fetch("/api/v1/health", { cache: "no-store", credentials: "same-origin" });
          if (response.ok) {
            window.location.reload();
            return;
          }
        } catch { /* 再起動中 */ }
        await new Promise((resolve) => window.setTimeout(resolve, 700));
      }
      throw new Error("復帰確認がタイムアウトしました。手動で再読み込みしてください");
    } catch (error) {
      setReloading(false);
      show(error instanceof Error ? error.message : "再読み込みに失敗しました", "error");
    }
  };

  const install = async (addon: AddonState) => {
    try {
      const r = await api<{ job_id: string }>(`/features/${addon.id}/install-jobs`, { method: "POST", json: {} });
      setJobKind("install");
      setJobId(r.job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "導入開始に失敗しました", "error");
    }
  };
  const update = async (addon: AddonState) => {
    try {
      const r = await api<{ job_id: string }>(`/features/${addon.id}/update-jobs`, { method: "POST", json: {} });
      setJobKind("update");
      setJobId(r.job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "更新開始に失敗しました", "error");
    }
  };
  const act = async (addon: AddonState, action: "enable" | "disable" | "uninstall") => {
    try {
      const r = await api<{ requires_reload?: boolean }>(`/features/${addon.id}/${action}`, { method: "POST", json: {} });
      show(action === "enable" ? "有効化しました。反映のため再読み込みします…" : action === "disable" ? "無効化しました。反映のため再読み込みします…" : "アンインストールしました。再読み込みします…", "info");
      qc.invalidateQueries({ queryKey: ["features"] });
      if (r.requires_reload) await reloadPlatform();
    } catch (e) {
      show(e instanceof Error ? e.message : "操作に失敗しました", "error");
    }
  };

  const btn = "rounded-xl px-3.5 py-2 text-xs font-medium";
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="mb-1 text-sm font-semibold text-zinc-500">オプション機能</h2>
      <p className="mb-3 text-xs text-zinc-400">オプトイン機能の導入と管理。導入はユーザー領域へのインストールでパスワードは不要です。</p>
      <div className="space-y-2.5">
        {(addons ?? []).map((addon) => (
          <div key={addon.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${addon.enabled ? "bg-emerald-500" : addon.installed ? "bg-zinc-300 dark:bg-zinc-600" : "bg-zinc-200 dark:bg-zinc-700"}`}
                title={addon.enabled ? "有効" : addon.installed ? "導入済み（無効）" : "未導入"} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">{addon.name}
                  {addon.version && <span className="num ml-2 text-[10px] font-normal text-zinc-400">v{addon.version.replace(/^v/, "")}</span>}
                </p>
                <p className="text-[11px] text-zinc-400">
                  {addon.enabled ? `利用できます — ${addon.summary}`
                    : addon.installed ? "導入済み（無効）— 有効化すると使えます"
                    : addon.available ? `未導入 — ${addon.summary}（${addon.kind === "pip" ? "pip・1〜2分" : "npm・1〜2分"}）`
                    : addon.kind === "pip" ? "python3が見つかりません" : "npmが見つかりません。Node.jsの導入が必要です"}
                  {addon.error && ` · ${addon.error}`}
                  {addon.requires_installed === false &&
                    ` · 先に ${(addons ?? []).find((a) => a.id === addon.requires)?.name ?? addon.requires} の導入が必要です`}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                {addon.installed && addon.managed && (
                  <button onClick={() => void update(addon)} disabled={jobId !== null || reloading}
                    title="npmの最新版へ更新します"
                    className={`${btn} border border-zinc-200 text-zinc-700 hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800`}>
                    {jobId !== null && jobKind === "update" ? "更新中…" : "更新"}
                  </button>
                )}
                {!addon.installed && (
                  <button onClick={() => void install(addon)}
                    disabled={!addon.available || jobId !== null || addon.requires_installed === false}
                    title={addon.requires_installed === false
                      ? `先に ${(addons ?? []).find((a) => a.id === addon.requires)?.name ?? addon.requires} を導入してください`
                      : undefined}
                    className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}>
                    {jobId !== null ? "導入中…" : "導入"}
                  </button>
                )}
                {addon.installed && !addon.enabled && addon.route_gated && (
                  <button onClick={() => void act(addon, "enable")} disabled={reloading || jobId !== null}
                    className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}>有効化</button>
                )}
                {addon.installed && addon.enabled && addon.route_gated && (
                  <button onClick={() => void act(addon, "disable")} disabled={reloading || jobId !== null}
                    className={`${btn} bg-zinc-100 text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300`}>無効化</button>
                )}
                {addon.installed && (
                  <button onClick={() => setUninstalling(addon.id)} disabled={reloading || jobId !== null}
                    className={`${btn} text-red-600 hover:bg-red-50 disabled:opacity-40 dark:hover:bg-red-950/40`}>削除</button>
                )}
              </div>
            </div>
            {jobId !== null && (jobKind === "update" ? addon.managed : !addon.installed) && (
              <p className="mt-2 animate-pulse text-[11px] text-zinc-400">
                {job?.progress?.status || (jobKind === "update" ? "更新中…" : "導入中…")} — サーバー側で実行中。この画面を閉じても継続します
              </p>
            )}
          </div>
        ))}
        {addons?.length === 0 && <p className="text-xs text-zinc-400">利用可能なオプション機能はありません</p>}
      </div>
      {reloading && (
        <p className="mt-3 animate-pulse rounded-xl bg-accent-50 px-3 py-2 text-xs text-accent-700 dark:bg-accent-600/10 dark:text-accent-300">
          Control Deckを再読み込みして反映しています…
        </p>
      )}
      {uninstalling && (
        <ConfirmDialog
          title="オプション機能をアンインストールしますか？"
          message="Control Deckが導入したランタイム一式（管理領域）だけを削除します。PATH上の外部インストールと個人設定には触れません。"
          confirmLabel="アンインストールする"
          onConfirm={() => {
            const target = (addons ?? []).find((a) => a.id === uninstalling);
            setUninstalling(null);
            if (target) void act(target, "uninstall");
          }}
          onClose={() => setUninstalling(null)}
        />
      )}
    </section>
  );
}
