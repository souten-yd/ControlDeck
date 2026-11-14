import { lazy, Suspense, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { ReactNode, SVGProps } from "react";
import { api } from "../api/client";
import { ACCENTS, useAuth, useTheme, useToasts, type Theme } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { AlertsSettings } from "../features/alerts/AlertsSettings";
import { TotpSettings } from "../features/auth/TotpSettings";
import { PasskeySettings } from "../features/auth/PasskeySettings";
import { PasswordSettings } from "../features/auth/PasswordSettings";
import { PageHeader } from "../components/PageHeader";
import { MobileNavigationSettings } from "../features/settings/MobileNavigationSettings";
import { FanControlSettings } from "../features/settings/FanControlSettings";
import { RecommendedSkills } from "../features/settings/RecommendedSkills";
import { addonLabel, useInstalledAddons, type AddonActivity, type InstalledAddon } from "../api/addons";
import { AddonStatusChip, addonStateMessage } from "../features/addons/AddonStatus";
import { IconBell, IconChevronLeft, IconChevronRight, IconChip, IconPalette, IconPlugin, IconSearch, IconSettings, IconShield, IconUser, IconUsers } from "../components/icons";

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

  return <SettingsLayout />;
}

type SectionId = "account" | "users" | "appearance" | "notifications" | "features" | "hardware" | "system";
type Can = (permission: string) => boolean;

interface SettingsSectionDef {
  id: SectionId;
  label: string;
  summary: string;
  icon: (p: SVGProps<SVGSVGElement>) => ReactNode;
  /** 検索で拾う語。画面に出る見出し・項目名をそのまま並べる。 */
  keywords: string[];
  allowed: (can: Can) => boolean;
}

/** 設定のカテゴリ。順番は「よく触る順」。ここに無い設定はカテゴリ内の「詳細」に入れる。 */
const SETTINGS_SECTIONS: SettingsSectionDef[] = [
  { id: "account", label: "アカウント", summary: "パスワード・二要素認証・パスキー・ログイン中の端末",
    icon: IconUser, keywords: ["パスワード", "二要素", "TOTP", "パスキー", "顔認証", "指紋", "セッション", "ログアウト", "端末"],
    allowed: () => true },
  { id: "appearance", label: "表示", summary: "テーマ・アクセントカラー・モバイルの下部メニュー",
    icon: IconPalette, keywords: ["テーマ", "ダーク", "ライト", "アクセント", "色", "OLED", "下部メニュー", "ナビゲーション", "モバイル"],
    allowed: () => true },
  { id: "features", label: "機能と拡張", summary: "オプション機能の導入・更新、拡張機能、推奨スキル",
    icon: IconPlugin, keywords: ["オプション機能", "アドオン", "拡張機能", "導入", "アンインストール", "OpenCode", "Maya", "Strata", "llama.cpp", "MediaForge", "SonicForge", "PatentBuddy", "スキル", "manifest"],
    allowed: (can) => can("settings.manage") },
  { id: "hardware", label: "ハードウェア", summary: "GPUのファン制御と温度",
    icon: IconChip, keywords: ["ファン", "温度", "GPU", "R9700", "回転数", "PID", "カーブ"],
    allowed: (can) => can("settings.manage") },
  { id: "notifications", label: "通知", summary: "通知チャンネルとアラートルール",
    icon: IconBell, keywords: ["通知", "アラート", "Discord", "Slack", "メール", "Webhook", "CPU"],
    allowed: (can) => can("system.view") },
  { id: "users", label: "ユーザーと権限", summary: "ユーザーの追加・ロール・権限",
    icon: IconUsers, keywords: ["ユーザー", "ロール", "権限", "管理者", "アカウント追加"],
    allowed: (can) => can("users.manage") },
  { id: "system", label: "システム", summary: "本体の更新・バックアップ・監査ログ",
    icon: IconShield, keywords: ["更新", "アップデート", "リリース", "バックアップ", "復元", "監査ログ", "ログ"],
    allowed: (can) => can("settings.manage") || can("audit.view") },
];

/** Settings that live on other screens, so a search here still reaches them. */
const OTHER_SCREEN_ITEMS: { label: string; screen: string; to: string; keywords: string[] }[] = [
  { label: "コンテキスト長・思考・量子化", screen: "AI モデル（個別設定）", to: "/models", keywords: ["コンテキスト", "CTX", "思考", "reasoning", "量子化", "KV", "VISION", "投機デコード", "Maya", "Strata", "llama"] },
  { label: "アイドル時に自動アンロード・既定のランタイム", screen: "AI モデル（共通設定）", to: "/models", keywords: ["アイドル", "自動アンロード", "自動停止", "ランタイム", "排他", "Deep Research", "アシスタント表示名"] },
  { label: "開発ワークフロー・レビューに使うモデル", screen: "OpenCode 設定", to: "/opencode", keywords: ["Superpowers", "Adversarial", "レビュー", "Auto Advisor", "相談", "OpenCode", "ワークフロー"] },
];

function matches(def: SettingsSectionDef, query: string) {
  const q = query.trim().toLowerCase();
  if (!q) return { hit: true, word: "" };
  if (def.label.toLowerCase().includes(q) || def.summary.toLowerCase().includes(q)) return { hit: true, word: "" };
  const word = def.keywords.find((k) => k.toLowerCase().includes(q)) ?? "";
  return { hit: !!word, word };
}

/** 設定のカテゴリ一覧（PC は左、モバイルは最初の画面）と選んだカテゴリの中身。
 * カテゴリごとに URL（/settings/:section）を持ち、他の画面から直接リンクできる。 */
function SettingsLayout() {
  const can = useAuth((s) => s.can);
  const { section } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const visible = SETTINGS_SECTIONS.filter((def) => def.allowed(can));
  // 旧リンク（/settings?extension=...）は拡張機能のカテゴリへ。
  const requested = searchParams.get("extension") ? "features" : section;
  const current = visible.find((def) => def.id === requested) ?? null;
  const shown = current ?? visible[0];
  const results = visible.map((def) => ({ def, ...matches(def, query) })).filter((r) => r.hit);
  const q = query.trim().toLowerCase();
  const elsewhere = q ? OTHER_SCREEN_ITEMS.filter((item) =>
    item.label.toLowerCase().includes(q) || item.keywords.some((k) => k.toLowerCase().includes(q))) : [];

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6">
      <div className="md:grid md:grid-cols-[15rem_minmax(0,1fr)] md:gap-6">
        <nav aria-label="設定のカテゴリ" className={current ? "hidden md:block" : "block"}>
          <PageHeader title="Settings" />
          <label className="relative mb-3 block">
            <span className="sr-only">設定を検索</span>
            <IconSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="設定を検索（例: パスワード）"
              onKeyDown={(e) => { if (e.key === "Enter" && results[0]) navigate(`/settings/${results[0].def.id}`); }}
              className="min-h-11 w-full rounded-xl border border-zinc-200 bg-white pl-9 pr-3 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
          </label>
          <ul className="space-y-1 md:sticky md:top-4">
            {results.map(({ def, word }) => {
              const Icon = def.icon;
              const active = def.id === shown?.id;
              return <li key={def.id}>
                <Link to={`/settings/${def.id}`} aria-current={active ? "page" : undefined}
                  className={`flex min-h-12 items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors ${active
                    ? "md:bg-accent-50 md:text-accent-700 md:dark:bg-accent-600/15 md:dark:text-accent-300"
                    : "hover:bg-zinc-100 dark:hover:bg-zinc-800/70"}`}>
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-100 text-base text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"><Icon /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{def.label}</span>
                    <span className="block truncate text-xs text-zinc-500 md:hidden">{word ? `一致: ${word}` : def.summary}</span>
                    {word && <span className="hidden text-xs text-zinc-500 md:block">一致: {word}</span>}
                  </span>
                  <IconChevronRight className="shrink-0 text-zinc-400 md:hidden" />
                </Link>
              </li>;
            })}
            {elsewhere.map((item) => (
              <li key={item.label}>
                <Link to={item.to} className="flex min-h-12 items-center gap-3 rounded-xl px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800/70">
                  <span className="min-w-0 flex-1"><span className="block font-medium">{item.label}</span>
                    <span className="block truncate text-xs text-zinc-500">{item.screen}</span></span>
                  <IconChevronRight className="shrink-0 text-zinc-400" />
                </Link>
              </li>
            ))}
            {results.length === 0 && elsewhere.length === 0 && <li className="px-3 py-4 text-sm text-zinc-500">一致する設定はありません</li>}
            <li className="pt-2">
              <Link to="/models" className="flex min-h-12 items-center gap-3 rounded-xl px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800/70">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-100 text-base text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"><IconSettings /></span>
                <span className="min-w-0 flex-1"><span className="block font-medium">AI モデル</span>
                  <span className="block truncate text-xs text-zinc-500">Models 画面で管理します</span></span>
                <IconChevronRight className="shrink-0 text-zinc-400" />
              </Link>
            </li>
            <li>
              <Link to="/opencode" className="flex min-h-12 items-center gap-3 rounded-xl px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800/70">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-100 text-base text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"><IconSettings /></span>
                <span className="min-w-0 flex-1"><span className="block font-medium">OpenCode</span>
                  <span className="block truncate text-xs text-zinc-500">モデル・開発ワークフローは OpenCode 画面の「設定」</span></span>
                <IconChevronRight className="shrink-0 text-zinc-400" />
              </Link>
            </li>
          </ul>
        </nav>
        <main className={current ? "block" : "hidden md:block"}>
          {shown && <>
            <Link to="/settings" className="mb-2 inline-flex min-h-11 items-center gap-1 text-sm text-accent-600 md:hidden dark:text-accent-400">
              <IconChevronLeft /> 設定
            </Link>
            <header className="mb-4 md:pt-1">
              <h2 className="text-xl font-semibold tracking-tight">{shown.label}</h2>
              <p className="mt-1 text-xs text-zinc-500">{shown.summary}</p>
            </header>
            <div className="space-y-6"><SettingsSectionBody id={shown.id} /></div>
          </>}
        </main>
      </div>
    </div>
  );
}

function SettingsSectionBody({ id }: { id: SectionId }) {
  const can = useAuth((s) => s.can);
  switch (id) {
    case "account": return <><AccountSection /><SessionsSection /></>;
    case "appearance": return <><AppearanceSection /><MobileNavigationSettings /></>;
    case "features": return <FeaturesSection />;
    case "hardware": return <FanControlSettings />;
    case "notifications": return <AlertsSettings />;
    case "users": return <Suspense fallback={<Skeleton className="h-40 rounded-2xl" />}><AccessManagement /></Suspense>;
    case "system": return <>
      {can("settings.manage") && <ControlDeckUpdateSection />}
      {can("settings.manage") && <BackupSection />}
      {can("audit.view") && <AuditSection />}
    </>;
  }
}

const card = "rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5";

function AccountSection() {
  const user = useAuth((s) => s.user);
  return (
    <section className={card}>
      <dl className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
        <div className="flex gap-3"><dt className="text-zinc-400">ユーザー名</dt><dd>{user?.username}</dd></div>
        <div className="flex gap-3"><dt className="text-zinc-400">ロール</dt><dd>{user?.role}</dd></div>
      </dl>
      <div className="mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800"><TotpSettings /></div>
      <div className="mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800"><PasswordSettings /></div>
      <div className="mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800"><PasskeySettings /></div>
    </section>
  );
}

function AppearanceSection() {
  const theme = useTheme((s) => s.theme);
  const setTheme = useTheme((s) => s.setTheme);
  const accent = useTheme((s) => s.accent);
  const setAccent = useTheme((s) => s.setAccent);
  const oled = useTheme((s) => s.oled);
  const setOled = useTheme((s) => s.setOled);
  return (
    <section className={card}>
      <h3 className="mb-2 text-sm font-semibold">テーマ</h3>
      <div className="flex gap-2">
        {(["system", "light", "dark"] as Theme[]).map((t) => (
          <button key={t} onClick={() => setTheme(t)} aria-pressed={theme === t}
            className={`min-h-11 rounded-xl px-4 text-sm font-medium ${theme === t
              ? "bg-accent-600 text-white"
              : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300"}`}>
            {t === "system" ? "システム" : t === "light" ? "ライト" : "ダーク"}
          </button>
        ))}
      </div>
      <h3 className="mb-2 mt-5 text-sm font-semibold">アクセントカラー</h3>
      <div className="flex flex-wrap gap-3">
        {ACCENTS.map((a) => (
          <button key={a.id} onClick={() => setAccent(a.id)} aria-label={a.label} aria-pressed={accent === a.id} title={a.label}
            className={`grid h-11 w-11 place-items-center rounded-full transition-transform hover:scale-105 ${accent === a.id
              ? "ring-2 ring-offset-2 ring-zinc-400 dark:ring-zinc-500 dark:ring-offset-zinc-900" : ""}`}
            style={{ backgroundColor: a.color }}>
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
          <span className="block text-xs text-zinc-400">ダークモード時に背景を純黒にします（有機 EL の省電力向け）</span>
        </span>
        <input type="checkbox" checked={oled} onChange={(e) => setOled(e.target.checked)} className="h-5 w-5 accent-current" />
      </label>
    </section>
  );
}

/** オプション機能・拡張機能・推奨スキルは同じカテゴリの中で切り替える。 */
function FeaturesSection() {
  // The tab lives in the URL (?tab=…) so it can be linked to and survives 戻る.
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab");
  const tab: "features" | "extensions" | "skills" = requested === "extensions" || requested === "skills" ? requested
    : searchParams.get("extension") ? "extensions" : "features";
  const setTab = (id: "features" | "extensions" | "skills") => {
    const next = new URLSearchParams(searchParams);
    next.delete("extension");
    if (id === "features") next.delete("tab"); else next.set("tab", id);
    setSearchParams(next);
  };
  const tabs = [["features", "オプション機能"], ["extensions", "拡張機能"], ["skills", "推奨スキル"]] as const;
  return <>
    <div role="tablist" aria-label="機能と拡張" className="grid grid-cols-3 gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
      {tabs.map(([id, label]) => (
        <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
          className={`min-h-10 rounded-lg px-2 text-sm font-medium ${tab === id
            ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"}`}>
          {label}
        </button>
      ))}
    </div>
    {tab === "features" && <AddonsSection />}
    {tab === "extensions" && <ExtensionsSection />}
    {tab === "skills" && <RecommendedSkills />}
  </>;
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
      <h2 className="mb-3 text-sm font-semibold">バックアップ</h2>
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

/** ブラウザ名とOSだけの短い表記。全文は title で見られる。 */
function describeAgent(ua: string) {
  const browser = /Edg\/(\d+)/.exec(ua) ? `Edge ${/Edg\/(\d+)/.exec(ua)![1]}`
    : /HeadlessChrome\/(\d+)/.exec(ua) ? `自動テスト（Chrome ${/HeadlessChrome\/(\d+)/.exec(ua)![1]}）`
    : /Chrome\/(\d+)/.exec(ua) ? `Chrome ${/Chrome\/(\d+)/.exec(ua)![1]}`
    : /Firefox\/(\d+)/.exec(ua) ? `Firefox ${/Firefox\/(\d+)/.exec(ua)![1]}`
    : /Version\/(\d+).*Safari/.exec(ua) ? `Safari ${/Version\/(\d+).*Safari/.exec(ua)![1]}`
    : /^([\w.-]+)\/[\d.]+/.exec(ua)?.[1] ?? "不明なブラウザ";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows"
    : /Mac OS X/.test(ua) ? "macOS" : /Linux|X11/.test(ua) ? "Linux" : "";
  return os ? `${browser} · ${os}` : browser;
}

function SessionsSection() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [expanded, setExpanded] = useState(false);
  const [confirmAll, setConfirmAll] = useState(false);
  const { data: sessions } = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api<SessionInfo[]>("/auth/sessions"),
  });
  // 現在の端末を先頭に、残りは最後に使った順。多いときは先頭の数件だけ見せる。
  const ordered = [...(sessions ?? [])].sort((a, b) =>
    Number(b.current) - Number(a.current) || (b.last_seen_at || "").localeCompare(a.last_seen_at || ""));
  const others = ordered.filter((s) => !s.current);
  const visible = expanded ? ordered : ordered.slice(0, 5);

  const revoke = async (id: number) => {
    try {
      await api(`/auth/sessions/${id}`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      show("セッションを失効しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "失効に失敗しました", "error");
    }
  };
  const revokeOthers = async () => {
    setConfirmAll(false);
    const results = await Promise.allSettled(others.map((s) => api(`/auth/sessions/${s.id}`, { method: "DELETE" })));
    qc.invalidateQueries({ queryKey: ["sessions"] });
    const failed = results.filter((r) => r.status === "rejected").length;
    show(failed ? `${failed}件を失効できませんでした` : "この端末以外のセッションをすべて失効しました", failed ? "error" : "info");
  };

  return (
    <section className={card}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">ログイン中の端末{sessions && `（${sessions.length}）`}</h3>
        {others.length > 0 && (
          <button onClick={() => setConfirmAll(true)} className="min-h-11 text-xs font-medium text-red-600 hover:underline dark:text-red-400">
            この端末以外をすべて失効
          </button>
        )}
      </div>
      {!sessions ? (
        <Skeleton className="h-16" />
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {visible.map((s) => (
            <li key={s.id} className="flex items-center gap-3 py-2.5 text-sm">
              <div className="min-w-0 flex-1">
                <p className="truncate">
                  {s.ip_address}
                  {s.current && (
                    <span className="ml-2 rounded bg-accent-50 px-1.5 py-0.5 text-[10px] font-medium text-accent-700 dark:bg-accent-600/15 dark:text-accent-400">
                      この端末
                    </span>
                  )}
                </p>
                <p className="truncate text-xs text-zinc-400" title={s.user_agent}>
                  {describeAgent(s.user_agent)}{s.last_seen_at && ` · 最終 ${new Date(s.last_seen_at + "Z").toLocaleString("ja-JP")}`}
                </p>
              </div>
              {!s.current && (
                <button onClick={() => revoke(s.id)} className="min-h-11 shrink-0 px-2 text-xs font-medium text-red-600 hover:underline dark:text-red-400">
                  失効
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {ordered.length > 5 && (
        <button onClick={() => setExpanded(!expanded)} className="mt-1 min-h-11 text-xs font-medium text-accent-600 dark:text-accent-400">
          {expanded ? "折りたたむ" : `すべて表示（他 ${ordered.length - 5} 件）`}
        </button>
      )}
      {confirmAll && (
        <ConfirmDialog title="この端末以外のセッションを失効しますか？"
          message={`${others.length}件のセッションをログアウトさせます。この端末はログインしたままです。`}
          confirmLabel="すべて失効" onConfirm={() => void revokeOthers()} onClose={() => setConfirmAll(false)} />
      )}
    </section>
  );
}

function auditResult(result: string): string {
  return result === "success" ? "成功" : result === "failure" || result === "failed" || result === "error" ? "失敗" : result;
}

function AuditSection() {
  const [limit, setLimit] = useState(50);
  const [showAutomated, setShowAutomated] = useState(false);
  const { data: entries } = useQuery({
    queryKey: ["audit", limit],
    queryFn: () => api<AuditEntry[]>(`/audit?limit=${limit}`),
    refetchInterval: 30_000,
  });
  // アドオンが数秒ごとに行う自動処理（トークン確認など）は人の操作を埋もれさせるので既定で隠す。
  const automated = (e: AuditEntry) => e.username.startsWith("addon:");
  const rows = (entries ?? []).filter((e) => showAutomated || !automated(e));
  const hidden = (entries ?? []).length - rows.length;
  return (
    <section className={card}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">監査ログ</h3>
        <label className="flex min-h-11 items-center gap-2 text-xs text-zinc-500">
          <input type="checkbox" checked={showAutomated} onChange={(e) => setShowAutomated(e.target.checked)} className="h-4 w-4" />
          アドオンの自動処理も表示{!showAutomated && hidden > 0 && `（${hidden}件）`}
        </label>
      </div>
      {!entries ? (
        <Skeleton className="h-24" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-zinc-500">直近 {limit} 件に人の操作はありません</p>
      ) : (
        <>
        {/* Phones: one entry per two lines instead of a table that folds every column. */}
        <ul className="divide-y divide-zinc-100 text-xs dark:divide-zinc-800 sm:hidden">
          {rows.map((e) => (
            <li key={e.id} className="py-2">
              <p className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono">{e.action}</span>
                <span className={e.result === "success" ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>{auditResult(e.result)}</span>
              </p>
              <p className="mt-0.5 truncate text-zinc-500">
                <span className="num">{new Date(e.timestamp + "Z").toLocaleString("ja-JP")}</span> · {e.username}
                {e.resource_type && ` · ${e.resource_type}/${e.resource_id}`}
              </p>
            </li>
          ))}
        </ul>
        <div className="hidden overflow-x-auto sm:block">
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
              {rows.map((e) => (
                <tr key={e.id}>
                  <td className="num whitespace-nowrap py-1.5 pr-3 text-zinc-400">{new Date(e.timestamp + "Z").toLocaleString("ja-JP")}</td>
                  <td className="py-1.5 pr-3">{e.username}</td>
                  <td className="py-1.5 pr-3 font-mono">{e.action}</td>
                  <td className="py-1.5 pr-3 text-zinc-400">{e.resource_type && `${e.resource_type}/${e.resource_id}`}</td>
                  <td className={`py-1.5 ${e.result === "success" ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>{auditResult(e.result)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}
      {limit < 500 && (entries?.length ?? 0) >= limit && (
        <button onClick={() => setLimit(Math.min(500, limit * 4))} className="mt-2 min-h-11 text-xs font-medium text-accent-600 dark:text-accent-400">
          さらに古い記録を読み込む
        </button>
      )}
    </section>
  );
}



interface ControlDeckUpdateStatus {
  channel: "main";
  current_sha: string;
  remote_sha: string;
  update_available: boolean;
  can_update: boolean;
  error: string;
  job?: { phase?: string; step?: string; error?: string; old_sha?: string; new_sha?: string };
}

/** Only explicit administrator interaction can schedule a platform update. */
function ControlDeckUpdateSection() {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["control-deck-update"],
    queryFn: () => api<ControlDeckUpdateStatus>("/system/control-deck-update"),
    refetchInterval: (q) => ["scheduled", "running"].includes(q.state.data?.job?.phase ?? "") ? 10000 : 300000,
    retry: false,
  });
  const apply = async () => {
    if (!data?.remote_sha || starting) return;
    setStarting(true);
    setConfirming(false);
    try {
      await api("/system/control-deck-update", { method: "POST", json: { expected_sha: data.remote_sha } });
      show("ControlDeck更新を開始しました。完了時にWebサービスを再起動します。");
      void qc.invalidateQueries({ queryKey: ["control-deck-update"] });
    } catch (error) {
      show(error instanceof Error ? error.message : "ControlDeck更新を開始できませんでした", "error");
    } finally {
      setStarting(false);
    }
  };

  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="text-sm font-semibold">ControlDeck 本体の更新</h2>
      <p className="mt-1 text-xs leading-relaxed text-zinc-400">
        公式GitHubの正式Releaseへ更新します。未コミット変更がある場合は中止します。
        更新は手動のみで、依存導入・画面ビルド後にWebサービスを再起動します。
      </p>
      <div className="mt-3 space-y-1 text-xs text-zinc-500">
        <p>現在: <span className="font-mono">{data?.current_sha?.slice(0, 12) || "確認中"}</span></p>
        <p>正式Release: <span className="font-mono">{data?.remote_sha?.slice(0, 12) || "-"}</span></p>
        {data?.job?.phase && <p>更新状態: {data.job.phase}{data.job.step ? ` / ${data.job.step}` : ""}</p>}
        {data?.job?.error && <p className="text-red-600">{data.job.error}</p>}
        {data?.error && <p className="text-amber-600">{data.error}</p>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={() => void refetch()} disabled={isFetching}
          className="min-h-11 rounded-xl border border-zinc-300 px-4 text-xs font-medium disabled:opacity-40 dark:border-zinc-700">
          {isFetching ? "確認中…" : "更新を確認"}
        </button>
        {data?.update_available && (
          <button type="button" onClick={() => setConfirming(true)}
            disabled={!data.can_update || starting || ["scheduled", "running"].includes(data.job?.phase ?? "")}
            className="min-h-11 rounded-xl bg-accent-600 px-4 text-xs font-semibold text-white disabled:opacity-40">
            {starting ? "開始中…" : "本体を更新"}
          </button>
        )}
      </div>
      {/* State as text, never as a disabled button that reads like "already latest". */}
      {!data?.update_available && !isFetching && (
        <p className="mt-2 text-xs text-zinc-500">
          {data?.remote_sha && data.current_sha ? "最新です" : `確認できません${data?.error ? `: ${data.error}` : ""}`}
        </p>
      )}
      {confirming && <ConfirmDialog
        title="ControlDeck本体を更新しますか？"
        message={`公式Release（${data?.remote_sha?.slice(0, 12) || "不明"}）へ更新し、Webサービスを再起動します。接続中の操作は中断される可能性があります。`}
        confirmLabel="更新して再起動"
        busy={starting}
        onConfirm={() => void apply()}
        onClose={() => setConfirming(false)}
      />}
    </section>
  );
}

interface AddonState {
  id: string; name: string; summary: string;
  kind: "npm" | "pip" | "release-bundle" | "gpu-runtime"; route_gated: boolean;
  available: boolean; installed: boolean; managed: boolean;
  enabled: boolean; requested_enabled: boolean; version: string; health: string;
  error: string; executable: string; preview?: boolean;
  /** 依存する別アドオンのid。未導入なら導入ボタンを止めて順序を案内する。 */
  requires?: string; requires_installed?: boolean;
  /** 配布元リポジトリ。GPUランタイムだけ持つ。 */
  repo?: string;
  /** GPUランタイムの構成（バックエンド / ROCmトラック / ホスト環境との整合）。 */
  runtime?: GpuRuntimeDetail;
}

interface GpuRuntimeDetail {
  runtime: "llama.cpp" | "strata" | "maya-s";
  warning?: string;
  /** llama.cpp */
  active_backend?: string;
  installed_backends?: string[];
  selectable_backends?: string[];
  backend_labels?: Record<string, string>;
  rocm_series_major?: number;
  host_rocm_version?: string;
  /** Strata Terminal (managed user-local installation). */
  cli_path?: string;
  models_dir?: string;
  built?: boolean;
  settings?: Record<string, unknown>;
  configured?: boolean;
  running?: boolean;
}

interface ReleaseStatus {
  feature_id: string;
  installed_tag: string;
  latest_tag: string;
  update_available: boolean;
  published_at: string;
  error: string;
}

/** GPUランタイムの更新有無。GitHubを見るので一覧とは別クエリ・低頻度にする。 */
function useReleaseStatus(addons: AddonState[] | undefined) {
  const runtimes = (addons ?? []).filter((item) => item.kind === "gpu-runtime" && item.installed);
  return useQuery({
    queryKey: ["feature-release-status", runtimes.map((item) => item.id).join(",")],
    queryFn: async () => {
      const entries = await Promise.all(runtimes.map(async (item) => {
        try {
          return [item.id, await api<ReleaseStatus>(`/features/${item.id}/release-status`)] as const;
        } catch {
          return [item.id, null] as const;
        }
      }));
      return Object.fromEntries(entries) as Record<string, ReleaseStatus | null>;
    },
    enabled: runtimes.length > 0,
    // GitHub の未認証レート上限に配慮する。導入・更新の直後は手動で無効化する。
    refetchInterval: 15 * 60_000,
    staleTime: 5 * 60_000,
  });
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
        <div><h2 className="text-sm font-semibold">拡張機能</h2><p className="mt-1 text-xs text-zinc-400">別プロセスのサービスを、検証済みmanifestと明示した権限で接続します。従来形式も同じ一覧で管理します。</p></div>
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
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p title={extension.name} className="min-w-0 truncate text-sm font-semibold">{extension.name}<span className="num ml-2 text-[10px] font-normal text-zinc-400">v{extension.version}</span></p><span className="rounded-full bg-zinc-100 px-2 py-0.5 text-[9px] font-semibold text-zinc-500 dark:bg-zinc-800">SERVICE</span><AddonStatusChip state={extension.state} /></div><p className="mt-1 truncate text-[11px] text-zinc-400">{extension.health?.message || addonStateMessage(extension.state)}</p></div>
            <div className="flex w-full flex-wrap justify-end gap-2 sm:w-auto">
              <button onClick={() => setSelected(extension.id)} className="rounded-xl px-3 py-2 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800">詳細</button>
              {extension.enabled ? <button onClick={() => void actV2(extension, "disable")} className="rounded-xl bg-zinc-100 px-3.5 py-2 text-xs font-medium dark:bg-zinc-800">無効化</button> : <button onClick={() => setReviewing(extension)} disabled={extension.state === "incompatible"} className="rounded-xl bg-accent-600 px-3.5 py-2 text-xs font-medium text-white disabled:opacity-40">有効化</button>}
              <button onClick={() => setRemoving({ id: extension.id, api: "v2" })} className="rounded-xl px-3 py-2 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40">削除</button>
            </div>
          </div>
        </div>)}
        {(plugins ?? []).map((plugin) => <div key={plugin.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
          <span className={`h-2.5 w-2.5 rounded-full ${plugin.enabled ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`} />
          <div className="min-w-0 flex-1"><p title={plugin.name} className="truncate text-sm font-semibold">{plugin.name}<span className="num ml-2 text-[10px] font-normal text-zinc-400">v{plugin.version}</span><span className="ml-2 rounded-full bg-zinc-100 px-2 py-0.5 text-[9px] font-semibold text-zinc-500 dark:bg-zinc-800">基本</span></p><p className="truncate text-[11px] text-zinc-400">{plugin.publisher} · {plugin.navigation.url}</p></div>
          <div className="flex w-full justify-end gap-2 sm:w-auto">
            <button onClick={() => void actV1(plugin.id, plugin.enabled ? "disable" : "enable")} className={`rounded-xl px-3.5 py-2 text-xs font-medium ${plugin.enabled ? "bg-zinc-100 dark:bg-zinc-800" : "bg-accent-600 text-white"}`}>{plugin.enabled ? "無効化" : "有効化"}</button>
            <button onClick={() => setRemoving({ id: plugin.id, api: "v1" })} className="rounded-xl px-3 py-2 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40">削除</button>
          </div>
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

/** GPUランタイムの構成表示。何が入っていて、ホスト環境と噛み合っているかを1か所で見せる。 */
function GpuRuntimeDetails({ addon, release }: {
  addon: AddonState;
  release: ReleaseStatus | null;
}) {
  const detail = addon.runtime;
  if (!detail) return null;
  const chip = "rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300";
  return (
    <div className="mt-1.5 space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {detail.runtime === "llama.cpp" ? (
          <>
            {(detail.installed_backends ?? []).map((backend) => (
              <span key={backend} className={backend === detail.active_backend
                ? "rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300"
                : chip}>
                {detail.backend_labels?.[backend] ?? backend}{backend === detail.active_backend ? " · 使用中" : ""}
              </span>
            ))}
            {(detail.installed_backends ?? []).length === 0
              && (detail.selectable_backends ?? []).map((backend) => (
                <span key={backend} className={chip}>{detail.backend_labels?.[backend] ?? backend}（未導入）</span>
              ))}
          </>
        ) : (
          <>
            <span className={chip}>Terminal</span>
            {detail.cli_path && <span className={chip}>{detail.cli_path}</span>}
          </>
        )}
        {release?.update_available && (
          <span className="num rounded bg-accent-100 px-1.5 py-0.5 text-[10px] font-medium text-accent-700 dark:bg-accent-600/20 dark:text-accent-300">
            更新あり: {release.latest_tag}
          </span>
        )}
      </div>
      {detail.warning && (
        <p className="rounded-lg border border-amber-200 bg-amber-50/60 p-2 text-[10px] leading-relaxed text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {detail.warning}
        </p>
      )}
      {release?.error && <p className="text-[10px] text-zinc-400">更新確認: {release.error}</p>}
    </div>
  );
}


/** Maya-S アドオンが持つのはエンジン（コード導入・HIPビルド）だけ。
 * モデルのダウンロード・既存GGUFの読み込み・個別設定・ロードは Models 画面で行う。 */
function MayaSPanel({ addon }: { addon: AddonState }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useQuery({
    queryKey: ["maya-s-job", jobId],
    queryFn: () => api<{status: string; error?: string; progress?: {status?: string}}>(`/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (q) => q.state.data && !["queued", "running"].includes(q.state.data.status) ? false : 1500,
  });
  useEffect(() => {
    if (!jobId || !job || ["queued", "running"].includes(job.status)) return;
    setJobId(null);
    setBusy(false);
    void qc.invalidateQueries({ queryKey: ["features"] });
    show(job.status === "succeeded" ? "Maya-SのHIPビルドが完了しました" : job.error || "Maya-SのHIPビルドに失敗しました",
      job.status === "succeeded" ? "info" : "error");
  }, [jobId, job?.status]);

  const build = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const started = await api<{job_id: string}>("/features/maya-s/model/build-jobs", { method: "POST", json: {} });
      setJobId(started.job_id);
    } catch (error) {
      show(error instanceof Error ? error.message : "HIPビルドを開始できませんでした", "error");
      setBusy(false);
    }
  };

  const runtime = addon.runtime;
  return <div className="mt-3 space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
    <p className="text-xs font-semibold">Maya-S エンジン</p>
    <p className="text-[11px] text-zinc-500">
      {runtime?.running ? "モデル稼働中 · 127.0.0.1:8099"
        : runtime?.configured ? "HIPモデル準備済み・停止中"
        : runtime?.built ? "HIPビルド済み・モデル未準備" : "HIP未ビルド・モデル未準備"}
    </p>
    <p className="text-[11px] text-zinc-500">
      モデルのダウンロード・既存モデルの読み込み・個別設定・ロードは{" "}
      <Link to="/models" className="text-accent-600 underline dark:text-accent-400">Models</Link> で行います。
    </p>
    <button type="button" disabled={busy || runtime?.running} onClick={() => void build()}
      className="min-h-11 rounded-lg border px-3 text-xs disabled:opacity-40">
      {runtime?.built ? "HIPを再ビルド" : "HIPをビルド"}（モデル取得なし）
    </button>
    {jobId && <p className="text-xs text-zinc-500" role="status">
      ジョブ {job?.status ?? "queued"} · {job?.progress?.status || "処理中"}
    </p>}
  </div>;
}

/** アドオン管理: 導入（npmユーザー空間・sudo不要）/有効化/無効化/アンインストールをワンタップで。 */
/** "llama-gpu-b11028" → "b11028", "opencode v2.3.0" → "v2.3.0",
 * "strata 0.2.13.1 (build …)" → "v0.2.13.1". The full string stays in the tooltip. */
function shortVersion(raw: string): string {
  const semver = raw.match(/\bv?(\d+(?:\.\d+){1,3})\b/);
  if (semver) return `v${semver[1]}`;
  const build = raw.match(/\b(b\d{3,})\b/);
  if (build) return build[1];
  return raw.length > 16 ? `${raw.slice(0, 16)}…` : raw;
}

function AddonsSection() {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobKind, setJobKind] = useState<"install" | "update">("install");
  // Which card the running job belongs to: only that card shows progress.
  const [jobAddon, setJobAddon] = useState<string | null>(null);
  const [uninstalling, setUninstalling] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const { data: addons } = useQuery({
    queryKey: ["features"],
    queryFn: () => api<AddonState[]>("/features"),
    refetchInterval: jobId ? false : 15_000,
  });
  const { data: releases } = useReleaseStatus(addons);
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
    qc.invalidateQueries({ queryKey: ["feature-release-status"] });
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
      setJobAddon(addon.id);
      setJobId(r.job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "導入開始に失敗しました", "error");
    }
  };
  const update = async (addon: AddonState) => {
    try {
      const r = await api<{ job_id: string }>(`/features/${addon.id}/update-jobs`, { method: "POST", json: {} });
      setJobKind("update");
      setJobAddon(addon.id);
      setJobId(r.job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "更新開始に失敗しました", "error");
    }
  };
  const act = async (addon: AddonState, action: "enable" | "disable" | "uninstall") => {
    try {
      const r = await api<{ requires_reload?: boolean }>(`/features/${addon.id}/${action}`, { method: "POST", json: {} });
      const label = action === "enable" ? "有効化しました" : action === "disable" ? "無効化しました" : "アンインストールしました";
      show(r.requires_reload ? `${label}。反映のため再読み込みします…` : label, "info");
      qc.invalidateQueries({ queryKey: ["features"] });
      if (r.requires_reload) await reloadPlatform();
    } catch (e) {
      show(e instanceof Error ? e.message : "操作に失敗しました", "error");
    }
  };

  const btn = "min-h-10 rounded-xl px-3.5 text-xs font-medium";
  const busy = jobId !== null || reloading;
  const requiresName = (addon: AddonState) => (addons ?? []).find((a) => a.id === addon.requires)?.name ?? addon.requires;
  const mine = (addon: AddonState) => jobId !== null && jobAddon === addon.id;
  const statusLine = (addon: AddonState) =>
    addon.installed && addon.error ? `修復が必要: ${addon.error}`
      : addon.enabled ? addon.summary
      : addon.kind === "release-bundle" && addon.installed && addon.requested_enabled ? "導入済み — サービスの復旧が必要です"
      : addon.installed ? "導入済み（無効）— 有効化すると使えます"
      : addon.available ? addon.summary
      : addon.kind === "pip" ? "python3が見つかりません" : "npmが見つかりません。Node.jsの導入が必要です";
  /** その時に必要な操作だけを1つ前に出す。残りは「詳細」に入れる。 */
  const primary = (addon: AddonState) => {
    if (!addon.installed) {
      return <button onClick={() => void install(addon)} disabled={!addon.available || busy || addon.requires_installed === false}
        title={addon.requires_installed === false ? `先に ${requiresName(addon)} を導入してください` : undefined}
        className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}>
        {mine(addon) && jobKind === "install" ? "導入中…" : addon.kind === "gpu-runtime" ? "セットアップ" : "導入"}
      </button>;
    }
    if (addon.error && !addon.route_gated) {
      // Installed but broken, with no enable switch: reinstalling is the way out.
      return <button onClick={() => void install(addon)} disabled={busy}
        className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}>
        {mine(addon) && jobKind === "install" ? "再導入中…" : "再導入"}
      </button>;
    }
    if (!addon.enabled && addon.route_gated) {
      return <button onClick={() => void act(addon, "enable")} disabled={busy}
        className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}>
        {addon.kind === "release-bundle" && addon.requested_enabled ? "起動して復旧" : "有効化"}
      </button>;
    }
    if (addon.managed && releases?.[addon.id]?.update_available) {
      return <button onClick={() => void update(addon)} disabled={busy}
        className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}>
        {mine(addon) && jobKind === "update" ? "更新中…" : "更新する"}
      </button>;
    }
    return null;
  };
  const groups: [string, AddonState[]][] = [
    ["利用中", (addons ?? []).filter((a) => a.enabled)],
    ["導入済み（無効）", (addons ?? []).filter((a) => a.installed && !a.enabled)],
    ["導入できる機能", (addons ?? []).filter((a) => !a.installed)],
  ];
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="sr-only">オプション機能</h2>
      <p className="mb-3 text-xs text-zinc-500">導入はユーザー領域へのインストールで、パスワードは不要です。</p>
      <div className="space-y-5">
        {groups.filter(([, items]) => items.length > 0).map(([title, items]) => (
          <div key={title}>
            <h3 className="mb-2 text-xs font-semibold text-zinc-500">{title}（{items.length}）</h3>
            <div className="space-y-2">
              {items.map((addon) => {
                const hasUpdate = releases?.[addon.id]?.update_available;
                return (
                  <div id={`feature-${addon.id}`} key={addon.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
                    <div className="flex items-center gap-3">
                      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${addon.installed && addon.error ? "bg-red-500" : addon.enabled ? "bg-emerald-500" : addon.installed ? "bg-zinc-300 dark:bg-zinc-600" : "bg-zinc-200 dark:bg-zinc-700"}`}
                        title={addon.enabled ? "有効" : addon.installed ? "導入済み（無効）" : "未導入"} />
                      <div className="min-w-0 flex-1">
                        <p className="flex flex-wrap items-center gap-x-2 text-sm font-semibold">
                          {addon.name}
                          {addon.version && <span className="num text-[11px] font-normal text-zinc-500" title={addon.version}>{shortVersion(addon.version)}</span>}
                          {hasUpdate && <span className="rounded bg-accent-50 px-1.5 py-0.5 text-[10px] font-medium text-accent-700 dark:bg-accent-600/15 dark:text-accent-300">更新あり</span>}
                          {addon.preview && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">PREVIEW</span>}
                        </p>
                        <p className="line-clamp-2 text-xs text-zinc-500">
                          <span className={addon.installed && addon.error ? "text-red-600 dark:text-red-400" : undefined}>{statusLine(addon)}</span>
                          {addon.error && !addon.installed && <span className="text-red-600 dark:text-red-400"> · {addon.error}</span>}
                          {addon.requires_installed === false && ` · 先に ${requiresName(addon)} の導入が必要です`}
                        </p>
                      </div>
                      <div className="shrink-0">{primary(addon)}</div>
                    </div>
                    {addon.installed && (
                      <details className="group mt-2">
                        <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1 text-xs text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300">
                          <IconChevronRight className="transition-transform group-open:rotate-90" /> 詳細と操作
                        </summary>
                        <div className="mt-2 space-y-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
                          {addon.preview && <p className="text-[11px] text-amber-700 dark:text-amber-300">PREVIEW — release署名またはcatalog digest固定前</p>}
                          {addon.kind === "gpu-runtime" && <GpuRuntimeDetails addon={addon} release={releases?.[addon.id] ?? null} />}
                          {addon.id === "maya-s" && <MayaSPanel addon={addon} />}
                          <div className="flex flex-wrap gap-1.5">
                            {addon.managed && !hasUpdate && (
                              <button onClick={() => void update(addon)} disabled={busy}
                                title={addon.kind === "gpu-runtime" ? `${addon.repo ?? "配布元"} の最新リリースへ更新します` : "npmの最新版へ更新します"}
                                className={`${btn} border border-zinc-200 text-zinc-700 hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800`}>
                                {mine(addon) && jobKind === "update" ? "更新中…" : "更新"}
                              </button>
                            )}
                            {addon.enabled && addon.route_gated && (
                              <button onClick={() => void act(addon, "disable")} disabled={busy}
                                className={`${btn} bg-zinc-100 text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300`}>無効化</button>
                            )}
                            {/* llama.cpp は登録済みモデル全部の土台なので、アドオン画面からは消させない。 */}
                            {addon.id !== "llama-cpp" && (
                              <button onClick={() => setUninstalling(addon.id)} disabled={busy}
                                className={`${btn} text-red-600 hover:bg-red-50 disabled:opacity-40 dark:hover:bg-red-950/40`}>アンインストール</button>
                            )}
                          </div>
                        </div>
                      </details>
                    )}
                    {mine(addon) && (
                      <p className="mt-2 animate-pulse text-[11px] text-zinc-400">
                        {job?.progress?.status || (jobKind === "update" ? "更新中…" : "導入中…")} — サーバー側で実行中。この画面を閉じても継続します
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
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
          title={`${(addons ?? []).find((a) => a.id === uninstalling)?.name ?? "オプション機能"} をアンインストールしますか？`}
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
