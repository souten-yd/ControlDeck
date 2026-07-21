import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useMeta } from "../api/hooks";
import { useAuth, useMetrics, useToasts } from "../stores";
import { useMetricsStream } from "../hooks/useMetricsStream";
import {
  IconBook,
  IconBranch,
  IconChart,
  IconChevronLeft,
  IconChip,
  IconFile,
  IconGrid,
  IconPlus,
  IconPower,
  IconSettings,
  IconTerminal,
  IconLogout,
} from "../components/icons";
import { BottomSheet, ConfirmDialog, Toasts } from "../components/ui";
import { CommandPalette } from "../components/CommandPalette";
import { Logo } from "../components/Logo";
import { PRODUCT_NAMES } from "../constants/productNames";
import { canAccessNavigationItem, IconAssistant, IconCode, IconFlow, IconRemote, NAVIGATION } from "../navigation";
import { useMobileNavigation } from "../stores/mobileNavigation";

export default function AppLayout() {
  const user = useAuth((s) => s.user);
  const can = useAuth((s) => s.can);
  const connected = useMetrics((s) => s.connected);
  const { data: meta } = useMeta();
  const enabledFeatures = new Set(meta?.enabled_features ?? []);
  const visibleNav = NAVIGATION.filter((item) => (!item.feature || enabledFeatures.has(item.feature)) && canAccessNavigationItem(item, can));
  const mobilePaths = useMobileNavigation((state) => state.paths);
  const mobileNav = mobilePaths.map((path) => visibleNav.find((item) => item.to === path)).filter((item) => item !== undefined);
  const [collapsed, setCollapsed] = useState(
    localStorage.getItem("cd-sidebar") === "min",
  );
  const [actionOpen, setActionOpen] = useState(false);
  const [powerAction, setPowerAction] = useState<"reboot" | "shutdown" | null>(null);
  const [platformReloading, setPlatformReloading] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const show = useToasts((s) => s.show);
  // ワークフローエディタ（/workflows/:id）等は全画面表示（ヘッダー・下部ナビを隠す）
  const immersive = location.pathname === "/assistant" || /^\/workflows\/[^/]+$/.test(location.pathname);

  useMetricsStream(can("system.view"));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const toggleSidebar = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem("cd-sidebar", next ? "min" : "full");
  };

  const doPower = async ({ delayMinutes, mode, totpCode }: {
    delayMinutes: number; mode: "graceful" | "immediate"; totpCode: string;
  }) => {
    if (!powerAction) return;
    try {
      if (delayMinutes > 0) {
        await api("/system/power/schedule", { method: "POST", json: { action: powerAction, delay_minutes: delayMinutes, totp_code: totpCode } });
        show(`${delayMinutes}分後の${powerAction === "reboot" ? "再起動" : "シャットダウン"}を予約しました`, "info");
      } else {
        await api(`/system/${powerAction}`, { method: "POST", json: { mode, totp_code: totpCode } });
        show(powerAction === "reboot" ? "再起動を実行しました" : "シャットダウンを実行しました", "info");
      }
    } catch (e) {
      show(e instanceof Error ? e.message : "電源操作に失敗しました", "error");
    }
    setPowerAction(null);
  };

  const reloadPlatform = async () => {
    setActionOpen(false);
    setPlatformReloading(true);
    try {
      await api("/system/platform/reload", { method: "POST" });
      show("Control Deckを再読み込みしています…", "info");
      await new Promise((resolve) => window.setTimeout(resolve, 1800));
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        try {
          const response = await fetch("/api/v1/health", { cache: "no-store", credentials: "same-origin" });
          if (response.ok) {
            window.location.reload();
            return;
          }
        } catch { /* service再起動中 */ }
        await new Promise((resolve) => window.setTimeout(resolve, 700));
      }
      throw new Error("サービスの復帰確認がタイムアウトしました。ブラウザを手動更新してください");
    } catch (error) {
      setPlatformReloading(false);
      show(error instanceof Error ? error.message : "再読み込みに失敗しました", "error");
    }
  };

  const logout = async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      useAuth.getState().setUser(null);
      navigate("/login");
    }
  };

  return (
    <div className="flex h-full">
      {/* デスクトップサイドバー */}
      <aside
        className={`hidden shrink-0 flex-col border-r border-zinc-200 dark:border-zinc-800 md:flex ${
          collapsed ? "w-16" : "w-56"
        } transition-[width] duration-150`}
      >
        <div className="flex h-14 items-center gap-2 px-4">
          <Logo size={28} className="shrink-0" />
          {!collapsed && (
            <span className="truncate text-sm font-semibold">
              {meta?.app_name ?? "Control Deck"}
            </span>
          )}
        </div>
        <nav className="flex-1 space-y-1 px-2 py-2">
          {visibleNav.map(({ to, label, icon: Ico }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              title={label}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium ${
                  isActive
                    ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-400"
                    : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
                }`
              }
            >
              <Ico className="shrink-0 text-lg" />
              {!collapsed && label}
            </NavLink>
          ))}
          {(meta?.plugin_navigation ?? []).filter((plugin) => can(plugin.permission)).map((plugin) => (
            <a
              key={`plugin-${plugin.id}`}
              href={plugin.url}
              target="_blank"
              rel="noopener noreferrer"
              title={plugin.label}
              className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
            >
              <IconGrid className="shrink-0 text-lg" />
              {!collapsed && plugin.label}
            </a>
          ))}
        </nav>
        <div className="px-2 pb-3">
          <button
            onClick={toggleSidebar}
            aria-label={collapsed ? "サイドバーを展開" : "サイドバーを縮小"}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900"
          >
            <IconChevronLeft
              className={`text-lg transition-transform ${collapsed ? "rotate-180" : ""}`}
            />
            {!collapsed && "縮小"}
          </button>
        </div>
      </aside>

      {/* メイン */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="safe-top flex min-h-12 shrink-0 items-center justify-between gap-3 border-b border-zinc-200 px-4 dark:border-zinc-800 md:min-h-14">
          <div className="flex items-center gap-2 md:hidden">
            <Logo size={24} />
          </div>
          <div className="hidden md:block">
            <button
              onClick={() => setPaletteOpen(true)}
              className="flex w-64 items-center gap-2 rounded-xl border border-zinc-200 px-3 py-1.5 text-sm text-zinc-400 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700"
            >
              Search or run a command
              <kbd className="ml-auto rounded bg-zinc-100 px-1.5 text-[10px] text-zinc-500 dark:bg-zinc-800">
                Ctrl K
              </kbd>
            </button>
          </div>
          <div className="flex items-center gap-3">
            {!connected && can("system.view") && (
              <span className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
                再接続中
              </span>
            )}
            <button
              onClick={() => setActionOpen(true)}
              aria-label="Quick Actions"
              className="hidden rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 md:block"
            >
              <IconPower />
            </button>
            <span className="text-xs text-zinc-400">{user?.display_name}</span>
          </div>
        </header>

        {/* overflow-x-hidden: リロード直後等に幅超過要素があってもページ全体を横に広げない */}
        <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          <Outlet />
        </main>

        {/* モバイル下部ナビ（フロー内配置で iOS の fixed 浮き上がりを回避）。
            全画面ページ（エディタ等）では非表示にして没入表示にする。 */}
        <nav
          aria-label="Main navigation"
          className={`safe-bottom z-30 shrink-0 border-t border-zinc-200 bg-white/95 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95 ${immersive ? "hidden" : "md:hidden"}`}
        >
          <div className="flex justify-around">
            {mobileNav.map((n) => (
              <MobileNavLink key={n.to} {...n} />
            ))}
            <button
              onClick={() => setActionOpen(true)}
              aria-label="More"
              className="flex min-h-[52px] min-w-0 max-w-24 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 text-zinc-600 dark:text-zinc-400"
            >
              <span className="grid h-8 w-8 place-items-center rounded-full bg-accent-600 text-white">
                <IconPlus />
              </span>
              <span className="max-w-full truncate px-0.5 text-[10px]">More</span>
            </button>
          </div>
        </nav>
      </div>

      {/* グローバル操作シート */}
      {actionOpen && (
        <BottomSheet title="Quick Actions" onClose={() => setActionOpen(false)}>
          <div className="grid grid-cols-2 gap-1">
            {can("apps.edit") && (
              <ActionItem
                icon={<IconPlus />}
                label="Add App"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/apps?add=1");
                }}
              />
            )}
            {(can("workflows.run") || can("workflows.edit")) && (
              <ActionItem
                icon={<IconFlow />}
                label="Workflows"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/workflows");
                }}
              />
            )}
            {can("application_builder.view") && (
              <ActionItem
                icon={<IconGrid />}
                label={PRODUCT_NAMES.appStudio}
                onClick={() => {
                  setActionOpen(false);
                  navigate("/applications");
                }}
              />
            )}
            {can("project_lab.view") && (
              <ActionItem
                icon={<IconCode />}
                label="Project Lab"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/project-lab");
                }}
              />
            )}
            {can("remote_desktop.use") && (
              <ActionItem
                icon={<IconRemote />}
                label="Remote Desktop"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/remote");
                }}
              />
            )}
            {can("terminal.use") && (
              <ActionItem
                icon={<IconTerminal />}
                label="Terminal"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/terminal");
                }}
              />
            )}
            <ActionItem
              icon={<IconAssistant />}
              label="AI Assistant"
              onClick={() => {
                setActionOpen(false);
                navigate("/assistant");
              }}
            />
            {enabledFeatures.has("opencode") && can("workflows.run") && (
              <ActionItem
                icon={<IconCode />}
                label="OpenCode"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/opencode");
                }}
              />
            )}
            {(meta?.plugin_navigation ?? []).filter((plugin) => can(plugin.permission)).map((plugin) => (
              <ActionItem
                key={`plugin-${plugin.id}`}
                icon={<IconGrid />}
                label={plugin.label}
                onClick={() => {
                  setActionOpen(false);
                  window.open(plugin.url, "_blank", "noopener,noreferrer");
                }}
              />
            ))}
            {can("files.view") && (
              <ActionItem
                icon={<IconFile />}
                label="File Manager"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/files");
                }}
              />
            )}
            {can("apps.view") && (
              <ActionItem
                icon={<IconBranch />}
                label="GitHub"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/github");
                }}
              />
            )}
            {can("workflows.run") && (
              <ActionItem
                icon={<IconBook />}
                label="Knowledge (RAG)"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/knowledge");
                }}
              />
            )}
            {can("workflows.run") && (
              <ActionItem
                icon={<IconChip />}
                label="Models & LLM"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/models");
                }}
              />
            )}
            <ActionItem
              icon={<IconChart />}
              label="System Monitor"
              onClick={() => {
                setActionOpen(false);
                navigate("/system");
              }}
            />
            <ActionItem
              icon={<IconSettings />}
              label="Settings"
              onClick={() => {
                setActionOpen(false);
                navigate("/settings");
              }}
            />
          </div>
          <div className="my-3 border-t border-zinc-200 dark:border-zinc-800" />
          <ActionItem
            icon={<IconLogout />}
            label={`Sign Out (${user?.display_name ?? ""})`}
            onClick={() => {
              setActionOpen(false);
              void logout();
            }}
          />
          {can("power.manage") && (
            <>
              <p className="mb-1 mt-3 px-1 text-xs text-zinc-400">Power</p>
              <div className="space-y-1">
                <ActionItem
                  icon={<IconRestartPower />}
                  label={platformReloading ? "Reloading ControlDeck…" : "Reload ControlDeck"}
                  onClick={reloadPlatform}
                />
                <ActionItem
                  icon={<IconRestartPower />}
                  label="Restart PC"
                  danger
                  onClick={() => {
                    setActionOpen(false);
                    setPowerAction("reboot");
                  }}
                />
                <ActionItem
                  icon={<IconPower />}
                  label="Shut Down PC"
                  danger
                  onClick={() => {
                    setActionOpen(false);
                    setPowerAction("shutdown");
                  }}
                />
              </div>
            </>
          )}
        </BottomSheet>
      )}

      {/* 電源確認ダイアログ */}
      {powerAction && (
        <PowerConfirm
          action={powerAction}
          onConfirm={doPower}
          onClose={() => setPowerAction(null)}
        />
      )}

      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          onPower={(a) => setPowerAction(a)}
        />
      )}

      <Toasts />
    </div>
  );
}

function IconRestartPower(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" width="1em" height="1em" aria-hidden {...props}>
      <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      <path d="M21 3v6h-6" />
    </svg>
  );
}

function MobileNavLink({
  to,
  label,
  icon: Ico,
}: {
  to: string;
  label: string;
  icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
}) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `flex min-h-[52px] min-w-0 max-w-24 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 ${
          isActive
            ? "text-accent-600 dark:text-accent-400"
            : "text-zinc-500 dark:text-zinc-400"
        }`
      }
    >
      <Ico className="text-xl" />
      <span className="max-w-full truncate px-0.5 text-[10px]">{label}</span>
    </NavLink>
  );
}

function ActionItem({
  icon,
  label,
  hint,
  danger,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  hint?: string;
  danger?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm font-medium disabled:opacity-40 ${
        danger
          ? "text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
          : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
      }`}
    >
      <span className="text-lg">{icon}</span>
      {label}
      {hint && <span className="ml-auto text-xs font-normal text-zinc-400">{hint}</span>}
    </button>
  );
}

function PowerConfirm({ action, onConfirm, onClose }: {
  action: "reboot" | "shutdown";
  onConfirm: (value: { delayMinutes: number; mode: "graceful" | "immediate"; totpCode: string }) => void;
  onClose: () => void;
}) {
  const [safety, setSafety] = useState<{
    running_apps: number; running_workflows: number; connected_terminals: number;
    connected_remote_desktops: number; totp_required: boolean; totp_enabled: boolean;
  } | null>(null);
  const [safetyError, setSafetyError] = useState(false);
  const [delay, setDelay] = useState(0);
  const [mode, setMode] = useState<"graceful" | "immediate">("graceful");
  const [totpCode, setTotpCode] = useState("");
  const [scheduled, setScheduled] = useState<{ action: string; at: string; status: string } | null>(null);
  const show = useToasts((s) => s.show);
  useEffect(() => {
    api<typeof safety>("/system/power/safety")
      .then((value) => setSafety(value))
      .catch(() => setSafetyError(true));
    api<{ action: string; at: string; status: string } | null>("/system/power/schedule").then(setScheduled).catch(() => undefined);
  }, []);
  const label = action === "reboot" ? "再起動" : "シャットダウン";
  const disabled = safety === null || (safety.totp_required && (!safety.totp_enabled || totpCode.trim().length === 0));
  const counts = safety ? [
    ["実行中アプリ", safety.running_apps], ["実行中Workflow", safety.running_workflows],
    ["接続中Terminal", safety.connected_terminals], ["接続中RD", safety.connected_remote_desktops],
  ] as const : [];
  return (
    <ConfirmDialog
      title={delay ? `PC の${label}を予約しますか？` : `PC を${label}しますか？`}
      message={delay ? "予約はWebサービスを再起動してもsystemd timerに保持されます。" : mode === "graceful" ? "systemdがサービスを正常停止してから実行します。接続中のセッションは切断されます。" : "サービスの正常停止を待たず即時実行します。データ消失の危険があります。"}
      confirmLabel={delay ? `${delay}分後に予約` : mode === "graceful" ? `正常停止して${label}` : `即時${label}`}
      onConfirm={() => onConfirm({ delayMinutes: delay, mode, totpCode: totpCode.trim() })}
      onClose={onClose}
      disabled={disabled}
    >
      <label className="mt-3 block text-xs text-zinc-500">実行タイミング
        <select value={delay} onChange={(e) => { setDelay(Number(e.target.value)); setMode("graceful"); }}
          className="mt-1 w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
          <option value={0}>今すぐ</option><option value={15}>15分後</option><option value={30}>30分後</option>
          <option value={60}>1時間後</option><option value={180}>3時間後</option><option value={480}>8時間後</option>
        </select>
      </label>
      {delay === 0 && <fieldset className="mt-3">
        <legend className="text-xs text-zinc-500">実行方式</legend>
        <div className="mt-1 grid grid-cols-2 gap-2">
          <label className={`rounded-xl border p-3 text-xs ${mode === "graceful" ? "border-accent-500 bg-accent-50 dark:bg-accent-950/20" : "border-zinc-200 dark:border-zinc-700"}`}>
            <input type="radio" name="power-mode" value="graceful" checked={mode === "graceful"} onChange={() => setMode("graceful")} className="mr-2" />正常停止
            <span className="mt-1 block pl-5 text-[10px] text-zinc-400">推奨・systemd管理</span>
          </label>
          <label className={`rounded-xl border p-3 text-xs ${mode === "immediate" ? "border-red-500 bg-red-50 dark:bg-red-950/20" : "border-zinc-200 dark:border-zinc-700"}`}>
            <input type="radio" name="power-mode" value="immediate" checked={mode === "immediate"} onChange={() => setMode("immediate")} className="mr-2" />即時実行
            <span className="mt-1 block pl-5 text-[10px] text-red-500">正常停止を待たない</span>
          </label>
        </div>
      </fieldset>}
      {safety && <div className="mt-3 grid grid-cols-2 gap-2" aria-label="現在の稼働状況">
        {counts.map(([name, count]) => <div key={name} className={`rounded-lg px-3 py-2 ${count ? "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400" : "bg-zinc-50 text-zinc-500 dark:bg-zinc-800/70"}`}>
          <span className="block text-[10px]">{name}</span><span className="num text-sm font-semibold">{count} 件</span>
        </div>)}
      </div>}
      {safetyError && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600 dark:bg-red-950/40 dark:text-red-400">稼働状況を確認できないため、電源操作を実行できません。</p>}
      {safety?.totp_required && <label className="mt-3 block text-xs text-zinc-500">TOTP再認証
        <input value={totpCode} onChange={(event) => setTotpCode(event.target.value)} inputMode="numeric" autoComplete="one-time-code" placeholder="6桁コードまたはリカバリーコード"
          className="mt-1 w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-base dark:border-zinc-700 dark:bg-zinc-900" />
        {!safety.totp_enabled && <span className="mt-1 block text-red-600">設定画面でTOTPを有効化するまで電源操作できません。</span>}
      </label>}
      {scheduled && ["scheduled", "executing"].includes(scheduled.status) && (
        <div className="mt-3 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
          現在の予約: {scheduled.action === "reboot" ? "再起動" : "シャットダウン"} · {new Date(scheduled.at).toLocaleString("ja-JP")}
          <button className="ml-2 underline" onClick={async () => {
            try { await api("/system/power/schedule", { method: "DELETE" }); setScheduled(null); show("電源予約を取消しました"); }
            catch (e) { show(e instanceof Error ? e.message : "取消に失敗しました", "error"); }
          }}>取消</button>
        </div>
      )}
    </ConfirmDialog>
  );
}
