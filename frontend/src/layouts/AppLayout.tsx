import { useEffect, useRef, useState, type ComponentType, type ReactNode, type SVGProps } from "react";
import { createPortal } from "react-dom";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useMeta } from "../api/hooks";
import { useAuth, useMetrics, useToasts } from "../stores";
import { useMetricsStream } from "../hooks/useMetricsStream";
import { ErrorBoundary } from "../components/ErrorBoundary";
import {
  addonIcon,
  IconDots,
  IconLogout,
  IconMenu,
  IconPlay,
  IconPlugin,
  IconPower,
  IconRestart,
  IconSearch,
} from "../components/icons";
import { BottomSheet, ConfirmDialog, Toasts } from "../components/ui";
import { CommandPalette } from "../components/CommandPalette";
import { ApprovalsCenter } from "../features/approvals/PendingApprovals";
import { Logo } from "../components/Logo";
import { canAccessNavigationItem, NAV_GROUPS, NAVIGATION } from "../navigation";
import { useMobileNavigation } from "../stores/mobileNavigation";
import { addonLabel, invokeAddonCommand, useEffectiveAddons } from "../api/addons";
import { AddonStatusChip, addonStateMessage } from "../features/addons/AddonStatus";
import { SHELL_TOPBAR_CLASS, ShellContext, ShellMenuButton, shellIconButtonClass } from "./shell";

const SIDEBAR_KEY = "cd-sidebar";

/** localStorage は private mode や容量超過で例外を投げる。殻の描画を止めない。 */
function readSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "min";
  } catch {
    return false;
  }
}

type NavIcon = ComponentType<SVGProps<SVGSVGElement>>;

/** ドロワー／サイドバーの 1 行。内部の画面も、別タブで開くプラグインも同じ形にする。 */
interface NavEntry {
  key: string;
  to: string;
  label: string;
  icon: NavIcon;
  external?: boolean;
  title?: string;
  trailing?: ReactNode;
}

interface NavSection {
  id: string;
  label: string;
  entries: NavEntry[];
}

export default function AppLayout() {
  const user = useAuth((s) => s.user);
  const can = useAuth((s) => s.can);
  const streamStatus = useMetrics((s) => s.status);
  const { data: meta } = useMeta();
  const { data: effectiveAddons } = useEffectiveAddons(true);
  const addonById = new Map((effectiveAddons?.addons ?? []).map((addon) => [addon.id, addon]));
  const addonNavigation = effectiveAddons?.contributions.navigation ?? [];
  const addonQuickActions = effectiveAddons?.contributions.quick_actions ?? [];

  const enabledFeatures = new Set(meta?.enabled_features ?? []);
  const enrollmentPending = Boolean(user?.totp_required && !user.totp_enabled);
  const visibleNav = NAVIGATION.filter((item) =>
    (!enrollmentPending || item.to === "/settings")
    && (!item.feature || enabledFeatures.has(item.feature))
    && canAccessNavigationItem(item, can));
  /* 「よく使う」は以前の下部ナビの選択をそのまま引き継ぐ（保存先も同じ）。 */
  const pinnedPaths = useMobileNavigation((state) => state.paths);
  const [collapsed, setCollapsed] = useState(readSidebarCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);
  const [actionOpen, setActionOpen] = useState(false);
  const [powerAction, setPowerAction] = useState<"reboot" | "shutdown" | null>(null);
  const [platformReloading, setPlatformReloading] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const show = useToasts((s) => s.show);
  const appName = meta?.app_name ?? "Control Deck";

  /* メニューの中身。よく使うものを先頭に固定し、残りをまとまりごとに並べる。
     同じ行き先を 2 回は出さない（いまどこにいるかの印が 1 つに決まる）。 */
  const sections = ((): NavSection[] => {
    const toEntry = (item: (typeof visibleNav)[number]): NavEntry => ({
      key: item.to, to: item.to, label: item.label, icon: item.icon,
    });
    const pinned = pinnedPaths
      .map((path) => visibleNav.find((item) => item.to === path))
      .filter((item) => item !== undefined);
    const pinnedSet = new Set(pinned.map((item) => item.to));
    const result: NavSection[] = [];
    if (pinned.length) result.push({ id: "pinned", label: "よく使う", entries: pinned.map(toEntry) });
    const extensions: NavEntry[] = [
      ...addonNavigation.map((contribution): NavEntry => {
        const addon = addonById.get(contribution.addon_id);
        const label = addonLabel(contribution.label);
        return {
          key: `addon-${contribution.addon_id}-${contribution.id}`,
          to: contribution.route || `/x/${contribution.addon_id}/${contribution.id}`,
          label,
          icon: addonIcon(contribution.icon, contribution.addon_id),
          title: `${label}${addon && addon.state !== "healthy" ? ` — ${addonStateMessage(addon.state)}` : ""}`,
          trailing: addon ? <AddonStatusChip state={addon.state} compact /> : undefined,
        };
      }),
      ...(meta?.plugin_navigation ?? []).filter((plugin) => can(plugin.permission)).map((plugin): NavEntry => ({
        key: `plugin-${plugin.id}`, to: plugin.url, label: plugin.label, icon: IconPlugin, external: true,
      })),
    ];
    for (const group of NAV_GROUPS) {
      const entries = visibleNav.filter((item) => item.group === group.id && !pinnedSet.has(item.to)).map(toEntry);
      if (entries.length) result.push({ id: group.id, label: group.label, entries });
      /* 拡張機能は AI の次、システムの前。 */
      if (group.id === "ai" && extensions.length && !enrollmentPending) {
        result.push({ id: "extensions", label: "拡張機能", entries: extensions });
      }
    }
    return result;
  })();

  /* 上の行に出す「いまどこか」。拡張機能の画面なら拡張機能の名前を出す。 */
  const currentTitle = (() => {
    const contribution = addonNavigation.find((item) =>
      location.pathname.startsWith(item.route || `/x/${item.addon_id}/${item.id}`));
    /* 拡張機能は製品の名前で呼ぶ。貢献のラベル（「音声」など）はその中の
       画面の名前で、上の行が答えるべき「どのアプリか」ではない。 */
    if (contribution) {
      return addonById.get(contribution.addon_id)?.name ?? addonLabel(contribution.label);
    }
    const item = [...visibleNav].sort((a, b) => b.to.length - a.to.length)
      .find((entry) => entry.to === "/" ? location.pathname === "/" : location.pathname.startsWith(entry.to));
    return item?.label ?? appName;
  })();
  /** 宣言された quick action を実行し、拡張機能が返した route へ遷移する。 */
  const runQuickAction = async (addonId: string, contributionId: string) => {
    try {
      const { route } = await invokeAddonCommand(addonId, contributionId);
      if (route) navigate(route);
      else show("操作を実行しました");
    } catch (error) {
      show(error instanceof ApiError ? error.message : "拡張機能の操作を実行できませんでした", "error");
    }
  };
  /* 自前の上の行を持つ画面。殻の上の行は出さず、その画面が同じ ☰ を置く。 */
  const ownsTopBar = location.pathname === "/assistant";

  useMetricsStream(can("system.view") && !enrollmentPending);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    const openFromAddon = () => setPaletteOpen(true);
    window.addEventListener("control-deck:command-palette", openFromAddon);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("control-deck:command-palette", openFromAddon);
    };
  }, []);

  /* 殻の中では文書そのものをスクロールさせない。iPhone で指を動かしたとき、
     画面全体（入力欄ごと）がゴムのように引っ張られるのを止める。 */
  useEffect(() => {
    document.documentElement.classList.add("cd-app-shell");
    return () => document.documentElement.classList.remove("cd-app-shell");
  }, []);

  /* 画面を移ったらドロワーは閉じる。PC 幅へ広がったときも閉じる。 */
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname, location.search]);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 768px)");
    const onChange = () => query.matches && setDrawerOpen(false);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const toggleSidebar = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(SIDEBAR_KEY, next ? "min" : "full");
    } catch { /* 保存できなくても、この場の開閉は効く */ }
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


  const openPalette = () => {
    setDrawerOpen(false);
    setPaletteOpen(true);
  };
  const openActions = () => {
    setDrawerOpen(false);
    setActionOpen(true);
  };
  const navPanel = (compact: boolean, onToggle: () => void, toggleLabel: string, expanded: boolean) => (
    <NavPanel
      compact={compact}
      appName={appName}
      sections={sections}
      userName={user?.display_name ?? ""}
      toggleLabel={toggleLabel}
      toggleExpanded={expanded}
      onToggle={onToggle}
      onSearch={openPalette}
      onAccount={openActions}
    />
  );
  const extras = <ApprovalsCenter />;

  return (
    <ShellContext.Provider value={{ openNav: () => setDrawerOpen(true), navOpen: drawerOpen, extras, headerSlot: ownsTopBar ? null : headerSlot }}>
    <div className="flex h-full overflow-hidden">
      {/* PC: 折りたためるサイドバー（☰ で開閉。状態は端末に覚える） */}
      <aside
        data-shell-sidebar
        data-collapsed={collapsed}
        className={`hidden shrink-0 border-r border-zinc-200/80 bg-zinc-100/60 transition-[width] duration-200 ease-out motion-reduce:transition-none dark:border-zinc-800/80 dark:bg-zinc-900/60 md:block ${
          collapsed ? "w-[60px]" : "w-64"
        }`}
      >
        {navPanel(collapsed, toggleSidebar, collapsed ? "サイドバーを開く" : "サイドバーを閉じる", !collapsed)}
      </aside>

      {/* メイン */}
      <div className="flex min-w-0 flex-1 flex-col">
        {!ownsTopBar && (
          <header data-shell-topbar className={SHELL_TOPBAR_CLASS}>
            <div className="flex h-14 items-center gap-1 px-3 md:px-5">
              {/* 携帯は左上の ☰ からすべての機能へ行く。題名は「いまどこか」。 */}
              <ShellMenuButton />
              {/* PageHeader を使う画面は、題名（h1）と主操作をここへ描く。使わない画面
                  （エディタ・拡張機能など）は、殻が「いまどこか」だけを出す。 */}
              <div ref={setHeaderSlot} data-shell-header-slot className="peer flex min-w-0 flex-1 items-center gap-2 pl-1 empty:hidden" />
              <h1 data-shell-title className="hidden min-w-0 flex-1 truncate pl-1 text-lg font-semibold leading-7 tracking-tight peer-empty:block">{currentTitle}</h1>
              <div className="flex shrink-0 items-center gap-2">
                {/* L2 の承認待ち。どの画面でも見え、新しい依頼はポップアップで開く。 */}
                {extras}
                {/* 初回接続を「再接続中」と出さない。まだ一度も繋がっていないのに
                    再接続と言われると、壊れているように見える。 */}
                {streamStatus !== "live" && can("system.view") && (
                  streamStatus === "reconnecting" ? (
                    <span className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
                      再接続中
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5 text-xs text-zinc-400 dark:text-zinc-500">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400" />
                      接続中
                    </span>
                  )
                )}
              </div>
            </div>
          </header>
        )}

        {/* overflow-x-hidden: リロード直後等に幅超過要素があってもページ全体を横に広げない */}
        <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain">
          {/* 1 画面の例外で全体を白くしない。path が変われば境界も解除する。 */}
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>

      {/* 携帯: 左から出るドロワー */}
      <NavDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        {navPanel(false, () => setDrawerOpen(false), "メニューを閉じる", true)}
      </NavDrawer>

      {/* アカウントと操作（画面の移動はドロワー、ここは「実行するもの」だけ） */}
      {actionOpen && (
        <BottomSheet title="アカウントと操作" onClose={() => setActionOpen(false)}>
          <div className="space-y-1">
            {addonQuickActions.map((contribution) => <ActionItem
              key={`addon-quick-${contribution.addon_id}-${contribution.id}`}
              icon={<IconPlay />}
              label={addonLabel(contribution.label)}
              hint="拡張機能"
              onClick={() => {
                setActionOpen(false);
                void runQuickAction(contribution.addon_id, contribution.id);
              }}
            />)}
            <ActionItem
              icon={<IconLogout />}
              label={`Sign Out (${user?.display_name ?? ""})`}
              onClick={() => {
                setActionOpen(false);
                void logout();
              }}
            />
          </div>
          {can("power.manage") && (
            <>
              <div className="my-3 border-t border-zinc-200 dark:border-zinc-800" />
              <p className="mb-1 px-1 text-xs text-zinc-400">Power</p>
              <div className="space-y-1">
                <ActionItem
                  icon={<IconRestart />}
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
          effectiveAddons={effectiveAddons}
        />
      )}

      <Toasts />
    </div>
    </ShellContext.Provider>
  );
}

/** メニューの中身。PC のサイドバーと携帯のドロワーで同じものを使う。
 *  compact は PC で折りたたんだ状態（アイコンだけの細い列）。 */
function NavPanel({ compact, appName, sections, userName, toggleLabel, toggleExpanded, onToggle, onSearch, onAccount }: {
  compact: boolean;
  appName: string;
  sections: NavSection[];
  userName: string;
  toggleLabel: string;
  toggleExpanded: boolean;
  onToggle: () => void;
  onSearch: () => void;
  onAccount: () => void;
}) {
  const rowBase = `flex min-h-11 items-center rounded-xl text-sm transition-colors md:min-h-9 ${
    compact ? "w-11 justify-center" : "w-full gap-3 px-3"
  }`;
  const rowIdle = "text-zinc-600 hover:bg-zinc-200/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-100";
  const rowActive = "bg-white font-medium text-zinc-900 shadow-sm ring-1 ring-zinc-200/80 dark:bg-zinc-800 dark:text-white dark:ring-zinc-700/60";
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={`safe-top shrink-0 ${compact ? "px-2" : "px-3"}`}>
        <div className="flex h-14 items-center gap-1.5">
          <button
            type="button"
            onClick={onToggle}
            aria-label={toggleLabel}
            aria-expanded={toggleExpanded}
            title={toggleLabel}
            data-nav-toggle
            className={`${shellIconButtonClass} ${compact ? "" : "-ml-1.5"}`}
          >
            <IconMenu />
          </button>
          {!compact && (
            <>
              <Logo size={24} className="shrink-0" />
              <span className="min-w-0 flex-1 truncate text-[15px] font-semibold tracking-tight">{appName}</span>
            </>
          )}
        </div>
      </div>

      <div className={`shrink-0 pb-1 ${compact ? "px-2" : "px-3"}`}>
        <button
          type="button"
          onClick={onSearch}
          aria-label="検索・コマンド"
          title="検索・コマンド（Ctrl K）"
          className={compact
            ? `${rowBase} ${rowIdle}`
            : "flex min-h-11 w-full items-center gap-3 rounded-xl border border-zinc-200 bg-white px-3 text-sm text-zinc-500 transition-colors hover:border-zinc-300 hover:text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-400 dark:hover:border-zinc-700 dark:hover:text-zinc-200 md:min-h-10"}
        >
          <IconSearch className="shrink-0 text-lg" />
          {!compact && (
            <>
              <span className="min-w-0 flex-1 truncate text-left">検索・コマンド</span>
              <kbd className="hidden shrink-0 rounded-md bg-zinc-100 px-1.5 py-0.5 font-sans text-[10px] font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400 md:block">
                Ctrl K
              </kbd>
            </>
          )}
        </button>
      </div>

      <nav
        aria-label="メインナビゲーション"
        className={`min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain pb-3 ${compact ? "px-2" : "px-3"}`}
      >
        {sections.map((section, index) => (
          <div key={section.id} role="group" aria-label={section.label} data-nav-section={section.id}>
            {compact
              ? index > 0 && <div aria-hidden className="mx-2 my-2 border-t border-zinc-200 dark:border-zinc-800" />
              : <p className="px-3 pb-1 pt-4 text-[11px] font-medium tracking-wide text-zinc-500 dark:text-zinc-500">{section.label}</p>}
            {compact && index === 0 && <div className="h-1" />}
            <ul className="space-y-0.5">
              {section.entries.map((entry) => {
                const Ico = entry.icon;
                const inner = (
                  <>
                    <Ico className="shrink-0 text-[18px]" />
                    {!compact && <span className="min-w-0 flex-1 truncate">{entry.label}</span>}
                    {!compact && entry.trailing}
                  </>
                );
                return (
                  <li key={entry.key}>
                    {entry.external ? (
                      <a
                        href={entry.to}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={entry.title ?? entry.label}
                        aria-label={compact ? entry.label : undefined}
                        className={`${rowBase} ${rowIdle}`}
                      >
                        {inner}
                      </a>
                    ) : (
                      <NavLink
                        to={entry.to}
                        end={entry.to === "/"}
                        title={entry.title ?? entry.label}
                        aria-label={compact ? entry.label : undefined}
                        className={({ isActive }) => `${rowBase} ${isActive ? rowActive : rowIdle}`}
                      >
                        {inner}
                      </NavLink>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* 利用者。サインアウトと電源はここから。 */}
      <div className={`safe-bottom shrink-0 border-t border-zinc-200/80 dark:border-zinc-800/80 ${compact ? "px-2" : "px-3"}`}>
        <button
          type="button"
          onClick={onAccount}
          aria-label="アカウントと操作"
          aria-haspopup="dialog"
          title={userName || "アカウントと操作"}
          className={`my-2 flex min-h-11 items-center rounded-xl text-left text-sm transition-colors hover:bg-zinc-200/60 dark:hover:bg-zinc-800/80 ${
            compact ? "w-11 justify-center" : "w-full gap-3 px-2"
          }`}
        >
          <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-600 text-xs font-semibold uppercase text-white">
            {(userName.trim()[0] ?? "?")}
          </span>
          {!compact && (
            <>
              <span className="min-w-0 flex-1 truncate font-medium text-zinc-800 dark:text-zinc-100">{userName}</span>
              <IconDots className="shrink-0 text-lg text-zinc-400" />
            </>
          )}
        </button>
      </div>
    </div>
  );
}

const DRAWER_FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** 携帯のドロワー。左から滑り込み、背景を暗くする。
 *  閉じ方は、外側を押す・左へ払う・Escape・☰。開いている間は Tab をこの中に留める。 */
function NavDrawer({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  const [shown, setShown] = useState(false);
  const [dragX, setDragX] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const touchRef = useRef<{ x: number; y: number; horizontal: boolean | null } | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    setShown(false);
    setDragX(0);
    const timer = window.setTimeout(() => setMounted(false), 220);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!mounted || !open) return;
    /* 閉じた位置を一度描かせてから開いた位置へ移す（滑り込みの起点）。 */
    const frame = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(frame);
  }, [mounted, open]);

  useEffect(() => {
    if (!mounted || !open) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    panel?.querySelector<HTMLElement>("[data-nav-toggle]")?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const items = [...panel.querySelectorAll<HTMLElement>(DRAWER_FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (!active || !panel.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      /* 開いたボタンへ戻す。画面を移って消えていれば何もしない。 */
      if (previous && document.contains(previous)) previous.focus({ preventScroll: true });
    };
  }, [mounted, open]);

  if (!mounted) return null;
  return createPortal(
    <div data-nav-drawer data-state={shown ? "open" : "closed"} className="fixed inset-0 z-50 md:hidden" role="presentation">
      <div
        aria-hidden
        data-nav-backdrop
        onClick={onClose}
        className={`absolute inset-0 bg-black/45 backdrop-blur-[2px] transition-opacity duration-200 motion-reduce:transition-none ${shown ? "opacity-100" : "opacity-0"}`}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="メニュー"
        onTouchStart={(event) => {
          const touch = event.touches[0];
          touchRef.current = { x: touch.clientX, y: touch.clientY, horizontal: null };
        }}
        onTouchMove={(event) => {
          const start = touchRef.current;
          if (!start) return;
          const dx = event.touches[0].clientX - start.x;
          const dy = event.touches[0].clientY - start.y;
          /* 縦に動かし始めたら一覧のスクロール。横に動かし始めたときだけ引き出しを動かす。 */
          if (start.horizontal === null && Math.abs(dx) + Math.abs(dy) > 8) start.horizontal = Math.abs(dx) > Math.abs(dy);
          if (start.horizontal) setDragX(Math.min(0, dx));
        }}
        onTouchEnd={() => {
          const close = dragX < -64;
          touchRef.current = null;
          setDragX(0);
          if (close) onClose();
        }}
        onTouchCancel={() => {
          touchRef.current = null;
          setDragX(0);
        }}
        style={shown && dragX ? { transform: `translateX(${dragX}px)`, transition: "none" } : undefined}
        className={`absolute inset-y-0 left-0 w-[min(19rem,calc(100vw-3rem))] touch-pan-y border-r border-zinc-200/80 bg-zinc-50 shadow-2xl transition-transform duration-200 ease-out motion-reduce:transition-none dark:border-zinc-800 dark:bg-zinc-900 ${
          shown ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** PC の再起動。アプリの再読み込み（IconRestart）と同じ形にしない。
    輪の中の電源記号で「機械が落ちて上がる」ことを示す。 */
function IconRestartPower(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" width="1em" height="1em" aria-hidden {...props}>
      <path d="M20 13a8 8 0 1 1-3.2-6.4" />
      <path d="M20 3.5v4h-4" />
      <path d="M12 9.5v4" />
      <path d="M9.6 11a3.2 3.2 0 1 0 4.8 0" />
    </svg>
  );
}
/** 長い注記は折り返しの原因になる。意味を保った短い印にする。 */
function hintMark(hint: string): string {
  if (hint === "拡張機能") return "EXT";
  return hint.length > 3 ? "!" : hint;
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
      <span className="shrink-0 text-lg">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {hint && (
        <span
          title={hint}
          aria-label={hint}
          className="shrink-0 rounded-md border border-zinc-300 px-1 text-[10px] font-semibold uppercase leading-4 tracking-wide text-zinc-400 dark:border-zinc-700"
        >
          {hintMark(hint)}
        </span>
      )}
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
