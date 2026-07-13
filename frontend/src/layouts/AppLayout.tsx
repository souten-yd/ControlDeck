import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useMeta } from "../api/hooks";
import { useAuth, useMetrics, useToasts } from "../stores";
import { useMetricsStream } from "../hooks/useMetricsStream";
import {
  IconBranch,
  IconChart,
  IconChevronLeft,
  IconFile,
  IconGrid,
  IconHome,
  IconLogs,
  IconPlus,
  IconPower,
  IconSettings,
  IconTerminal,
  IconUpload,
} from "../components/icons";
import { BottomSheet, ConfirmDialog, Toasts } from "../components/ui";
import { CommandPalette } from "../components/CommandPalette";
import { Logo } from "../components/Logo";

const NAV = [
  { to: "/", label: "ホーム", icon: IconHome },
  { to: "/apps", label: "アプリ", icon: IconGrid },
  { to: "/workflows", label: "ワークフロー", icon: IconFlow },
  { to: "/files", label: "ファイル", icon: IconFile },
  { to: "/terminal", label: "ターミナル", icon: IconTerminal },
  { to: "/remote", label: "リモート", icon: IconRemote },
  { to: "/github", label: "GitHub", icon: IconBranch },
  { to: "/logs", label: "ログ", icon: IconLogs },
  { to: "/system", label: "システム", icon: IconChart },
  { to: "/settings", label: "設定", icon: IconSettings },
];

// モバイル下部ナビ: ホーム / アプリ / ワークフロー / ターミナル / リモート + 右端に操作
const MOBILE_NAV = [NAV[0], NAV[1], NAV[2], NAV[4], NAV[5]];

function IconFlow(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden {...props}>
      <rect x="2" y="4" width="7" height="6" rx="1.5" />
      <rect x="15" y="14" width="7" height="6" rx="1.5" />
      <path d="M9 7h4a2 2 0 0 1 2 2v5" />
    </svg>
  );
}

function IconRemote(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden {...props}>
      <rect x="2" y="4" width="20" height="13" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}

export default function AppLayout() {
  const user = useAuth((s) => s.user);
  const can = useAuth((s) => s.can);
  const connected = useMetrics((s) => s.connected);
  const { data: meta } = useMeta();
  const [collapsed, setCollapsed] = useState(
    localStorage.getItem("cd-sidebar") === "min",
  );
  const [actionOpen, setActionOpen] = useState(false);
  const [powerAction, setPowerAction] = useState<"reboot" | "shutdown" | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const show = useToasts((s) => s.show);
  // ワークフローエディタ（/workflows/:id）等は全画面表示（ヘッダー・下部ナビを隠す）
  const immersive = /^\/workflows\/[^/]+$/.test(location.pathname);

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

  const doPower = async () => {
    if (!powerAction) return;
    try {
      await api(`/system/${powerAction}`, { method: "POST" });
      show(powerAction === "reboot" ? "再起動を実行しました" : "シャットダウンを実行しました", "info");
    } catch (e) {
      show(e instanceof Error ? e.message : "電源操作に失敗しました", "error");
    }
    setPowerAction(null);
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
          {NAV.map(({ to, label, icon: Ico }) => (
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
        <header className={`safe-top h-12 shrink-0 items-center justify-between gap-3 border-b border-zinc-200 px-4 dark:border-zinc-800 md:h-14 ${immersive ? "hidden md:flex" : "flex"}`}>
          <div className="flex items-center gap-2 md:hidden">
            <Logo size={24} />
          </div>
          <div className="hidden md:block">
            <button
              onClick={() => setPaletteOpen(true)}
              className="flex w-64 items-center gap-2 rounded-xl border border-zinc-200 px-3 py-1.5 text-sm text-zinc-400 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700"
            >
              検索・コマンド
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
            {can("power.manage") && (
              <button
                onClick={() => setActionOpen(true)}
                aria-label="グローバル操作"
                className="hidden rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 md:block"
              >
                <IconPower />
              </button>
            )}
            <button
              onClick={logout}
              className="text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
            >
              {user?.display_name} ログアウト
            </button>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>

        {/* モバイル下部ナビ（フロー内配置で iOS の fixed 浮き上がりを回避）。
            全画面ページ（エディタ等）では非表示にして没入表示にする。 */}
        <nav
          aria-label="メインナビゲーション"
          className={`safe-bottom z-30 shrink-0 border-t border-zinc-200 bg-white/95 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95 ${immersive ? "hidden" : "md:hidden"}`}
        >
          <div className="grid grid-cols-6">
            {MOBILE_NAV.map((n) => (
              <MobileNavLink key={n.to} {...n} />
            ))}
            <button
              onClick={() => setActionOpen(true)}
              aria-label="操作メニュー"
              className="flex flex-col items-center gap-0.5 py-2 text-zinc-600 dark:text-zinc-400"
            >
              <span className="grid h-8 w-8 place-items-center rounded-full bg-accent-600 text-white">
                <IconPlus />
              </span>
              <span className="text-[10px]">操作</span>
            </button>
          </div>
        </nav>
      </div>

      {/* グローバル操作シート */}
      {actionOpen && (
        <BottomSheet title="操作" onClose={() => setActionOpen(false)}>
          <div className="space-y-1">
            {can("apps.edit") && (
              <ActionItem
                icon={<IconPlus />}
                label="アプリを追加"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/apps?add=1");
                }}
              />
            )}
            {can("workflows.run") && (
              <ActionItem
                icon={<IconFlow />}
                label="ワークフロー"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/workflows");
                }}
              />
            )}
            {can("terminal.use") && (
              <ActionItem
                icon={<IconTerminal />}
                label="ターミナル"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/terminal");
                }}
              />
            )}
            {can("files.view") && (
              <ActionItem
                icon={<IconFile />}
                label="ファイルマネージャー"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/files");
                }}
              />
            )}
            {can("apps.view") && (
              <ActionItem
                icon={<IconBranch />}
                label="GitHub 管理"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/github");
                }}
              />
            )}
            {can("files.edit") && (
              <ActionItem
                icon={<IconUpload />}
                label="ファイルアップロード"
                onClick={() => {
                  setActionOpen(false);
                  navigate("/files?upload=1");
                }}
              />
            )}
            <ActionItem
              icon={<IconChart />}
              label="システム監視"
              onClick={() => {
                setActionOpen(false);
                navigate("/system");
              }}
            />
            <ActionItem
              icon={<IconSettings />}
              label="設定"
              onClick={() => {
                setActionOpen(false);
                navigate("/settings");
              }}
            />
          </div>
          {can("power.manage") && (
            <>
              <div className="my-3 border-t border-zinc-200 dark:border-zinc-800" />
              <p className="mb-1 px-1 text-xs text-zinc-400">電源</p>
              <div className="space-y-1">
                <ActionItem
                  icon={<IconRestartPower />}
                  label="PC を再起動"
                  danger
                  onClick={() => {
                    setActionOpen(false);
                    setPowerAction("reboot");
                  }}
                />
                <ActionItem
                  icon={<IconPower />}
                  label="PC をシャットダウン"
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
        `flex min-h-[52px] flex-col items-center justify-center gap-0.5 py-1.5 ${
          isActive
            ? "text-accent-600 dark:text-accent-400"
            : "text-zinc-500 dark:text-zinc-400"
        }`
      }
    >
      <Ico className="text-xl" />
      <span className="text-[10px]">{label}</span>
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

function PowerConfirm({
  action,
  onConfirm,
  onClose,
}: {
  action: "reboot" | "shutdown";
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [runningApps, setRunningApps] = useState<number | null>(null);
  useEffect(() => {
    api<{ runtime: { status: string } }[]>("/apps")
      .then((apps) =>
        setRunningApps(apps.filter((a) => a.runtime.status === "RUNNING").length),
      )
      .catch(() => setRunningApps(null));
  }, []);
  const label = action === "reboot" ? "再起動" : "シャットダウン";
  return (
    <ConfirmDialog
      title={`PC を${label}しますか？`}
      message={`この操作は取り消せません。接続中のセッションはすべて切断されます。`}
      confirmLabel={`${label}する`}
      onConfirm={onConfirm}
      onClose={onClose}
    >
      {runningApps != null && runningApps > 0 && (
        <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
          実行中のアプリが {runningApps} 件あります
        </p>
      )}
    </ConfirmDialog>
  );
}
