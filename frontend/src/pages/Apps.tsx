import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAppAction, useApps, useDeleteApp } from "../api/hooks";
import { useAuth, useToasts } from "../stores";
import { formatBytes, formatUptime } from "../lib/format";
import {
  BottomSheet,
  ConfirmDialog,
  DropdownMenu,
  Skeleton,
  StatusBadge,
} from "../components/ui";
import { createPortal } from "react-dom";
import { IconDots, IconPlay, IconPlus, IconRestart, IconStop, IconX } from "../components/icons";
import { AddAppSheet } from "../features/apps/AddAppSheet";
import type { ManagedApp } from "../types";
import { PageHeader } from "../components/PageHeader";

function formatStartedAt(value: string | null): string {
  if (!value) return "開始日時 —";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "開始日時 —";
  const now = new Date();
  const includeYear = date.getFullYear() !== now.getFullYear();
  return `開始 ${new Intl.DateTimeFormat("ja-JP", {
    ...(includeYear ? { year: "numeric" as const } : {}),
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)}`;
}

function canRunSystemdAction(app: ManagedApp, action: "start" | "stop" | "restart" | "kill"): boolean {
  return app.systemd_scope !== "system" || (app.systemd_actions ?? []).includes(action);
}

export default function AppsPage() {
  const { data: allApps, isLoading } = useApps();
  // SearXNG等のインフラアプリはサーバー側が全自動管理するため、操作対象として表示しない
  const apps = allApps?.filter((app) => !app.system_managed);
  const [params, setParams] = useSearchParams();
  const [detail, setDetail] = useState<ManagedApp | null>(null);
  const [editing, setEditing] = useState<ManagedApp | null>(null);
  const [deleting, setDeleting] = useState<ManagedApp | null>(null);
  const [portPick, setPortPick] = useState<ManagedApp | null>(null);
  const [webView, setWebView] = useState<{ id: number; name: string; port: number } | null>(null);
  const can = useAuth((s) => s.can);
  const action = useAppAction();
  const deleteApp = useDeleteApp();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const addOpen = params.get("add") === "1";

  // Web ボタン: サーバーとして待ち受けているアプリを全画面ビューア（iframe）で開く。
  // window.open だと PWA では iOS のアプリ内 Safari（ツールバー付き）になるため。
  const openWeb = (id: number, name: string, port: number) => {
    setWebView({ id, name, port });
  };
  const saveWebPort = (app: ManagedApp, port: number) => {
    api(`/apps/${app.id}`, { method: "PATCH", json: { web_port: port } })
      .then(() => qc.invalidateQueries({ queryKey: ["apps"] }))
      .catch(() => undefined);
  };
  const handleWeb = (app: ManagedApp) => {
    const ports = app.runtime.listening_ports ?? [];
    if (app.web_port) return openWeb(app.id, app.name, app.web_port);
    if (ports.length === 1) {
      openWeb(app.id, app.name, ports[0]);
      saveWebPort(app, ports[0]); // 次回からこのポートを開く
    } else if (ports.length > 1) {
      setPortPick(app); // 初回は選択、以降は保存されたポートを開く
    }
  };
  const hasWeb = (app: ManagedApp) =>
    app.application_type !== "url_shortcut" &&
    app.runtime.status === "RUNNING" &&
    (app.web_port != null || (app.runtime.listening_ports ?? []).length > 0);

  const primaryAction = (app: ManagedApp) => {
    const st = app.runtime.status;
    if (app.application_type === "url_shortcut") return null; // 開くボタンで別処理
    if ((st === "RUNNING" || st === "DEGRADED") && canRunSystemdAction(app, "stop"))
      return { label: "停止", icon: <IconStop />, action: "stop", perm: "apps.stop" };
    if (st === "FAILED" && canRunSystemdAction(app, "restart"))
      return { label: "再起動", icon: <IconRestart />, action: "restart", perm: "apps.start" };
    if ((st === "STOPPED" || st === "UNKNOWN") && canRunSystemdAction(app, "start"))
      return { label: "起動", icon: <IconPlay />, action: "start", perm: "apps.start" };
    return null; // 遷移中
  };

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6">
      <PageHeader title="Apps" actions={can("apps.edit") && (
          <button
            onClick={() => setParams({ add: "1" })}
            aria-label="アプリを追加"
            className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm hover:bg-accent-700"
          >
            <IconPlus /><span className="hidden sm:inline"> アプリを追加</span>
          </button>
        )} />

      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : !apps || apps.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">登録されたアプリはありません</p>
          {can("apps.edit") && (
            <button
              onClick={() => setParams({ add: "1" })}
              className="mt-3 rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700"
            >
              最初のアプリを追加
            </button>
          )}
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {apps.map((app) => {
            const primary = primaryAction(app);
            const showPrimary = primary != null && can(primary.perm);
            const showActions = (app.application_type === "url_shortcut" && Boolean(app.url)) || hasWeb(app) || showPrimary;
            return (
              <li
                key={app.id}
                data-app-card
                className="cursor-pointer rounded-2xl border border-zinc-200 bg-white p-3.5 text-zinc-900 shadow-sm transition-colors hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:border-zinc-600"
                onClick={() => setDetail(app)}
              >
                <div className="flex min-w-0 items-start gap-3">
                  <AppAvatar app={app} />
                  <div className="min-w-0 flex-1 pt-0.5">
                    <p className="truncate text-sm font-semibold text-zinc-950 dark:text-white">{app.name}</p>
                    <div className="mt-1 flex items-center gap-2 sm:hidden"><StatusBadge status={app.runtime.status} /></div>
                  </div>
                  <DropdownMenu
                    ariaLabel={`${app.name} のメニュー`}
                    trigger={<IconDots />}
                    items={[
                      { label: "Logs", onSelect: () => navigate(`/logs?app=${app.id}`) },
                      ...(can("apps.start") && canRunSystemdAction(app, "restart")
                        ? [{ label: "Restart", onSelect: () => action.mutate({ id: app.id, action: "restart" }) }]
                        : []),
                      ...(can("apps.stop") && app.runtime.status === "RUNNING" && canRunSystemdAction(app, "kill")
                        ? [{ label: "Force Stop", danger: true, onSelect: () => action.mutate({ id: app.id, action: "kill" }) }]
                        : []),
                      { label: "Details", onSelect: () => setDetail(app) },
                      ...(can("apps.edit")
                        ? [{ label: "Edit Settings", onSelect: () => setEditing(app) }]
                        : []),
                      ...(can("apps.delete")
                        ? [{ label: "Delete", danger: true, onSelect: () => setDeleting(app) }]
                        : []),
                    ]}
                  />
                </div>

                <div className="mt-2 flex min-w-0 items-start gap-2">
                  <div data-app-runtime className="flex min-h-11 min-w-0 flex-1 items-center px-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
                    <span className="hidden sm:inline-flex"><StatusBadge status={app.runtime.status} /></span>
                    {app.runtime.status === "RUNNING" && (
                      <span className="num whitespace-nowrap text-xs font-semibold text-zinc-800 dark:text-zinc-100 sm:text-sm">
                        稼働 {formatUptime(app.runtime.uptime_seconds)}
                      </span>
                    )}
                    {app.runtime.status === "RUNNING" && (
                      <span className="num whitespace-nowrap text-xs text-zinc-600 dark:text-zinc-300">
                        {formatStartedAt(app.runtime.started_at)}
                      </span>
                    )}
                      {app.runtime.status === "RUNNING" && app.runtime.cpu_percent != null && <span className="num hidden whitespace-nowrap text-[11px] font-medium text-zinc-600 dark:text-zinc-300 sm:inline">CPU {app.runtime.cpu_percent.toFixed(0)}%</span>}
                      {app.runtime.status === "RUNNING" && app.runtime.memory_bytes != null && <span className="num hidden whitespace-nowrap text-[11px] font-medium text-zinc-600 dark:text-zinc-300 sm:inline">RAM {formatBytes(app.runtime.memory_bytes)}</span>}
                      {app.runtime.status === "RUNNING" && app.runtime.gpu_percent != null && <span className="num hidden whitespace-nowrap text-[11px] font-medium text-zinc-600 dark:text-zinc-300 sm:inline">GPU {app.runtime.gpu_percent.toFixed(0)}%</span>}
                      {app.runtime.status === "RUNNING" && app.runtime.vram_bytes != null && <span className="num hidden whitespace-nowrap text-[11px] font-medium text-zinc-600 dark:text-zinc-300 sm:inline">VRAM {formatBytes(app.runtime.vram_bytes)}</span>}
                    </div>
                  </div>

                {showActions && <div data-app-actions className="flex min-h-11 shrink-0 flex-wrap items-center justify-end gap-1.5">
                  {app.application_type === "url_shortcut" && app.url && (
                    <a href={app.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} aria-label={`${app.name} を開く`} className="flex min-h-11 min-w-11 items-center justify-center rounded-xl bg-accent-50 px-2.5 text-sm font-semibold text-accent-700 hover:bg-accent-100 dark:bg-accent-950 dark:text-accent-300 dark:hover:bg-accent-900"><span className="sm:hidden">↗</span><span className="hidden sm:inline">開く ↗</span></a>
                  )}
                  {hasWeb(app) && (
                    <button onClick={(e) => { e.stopPropagation(); handleWeb(app); }} aria-label={`${app.name} を Web で開く`} className="flex min-h-11 min-w-11 items-center justify-center rounded-xl bg-accent-50 px-2.5 text-sm font-semibold text-accent-700 hover:bg-accent-100 dark:bg-accent-950 dark:text-accent-300 dark:hover:bg-accent-900"><span className="sm:hidden">↗</span><span className="hidden sm:inline">Web ↗</span></button>
                  )}
                  {primary && showPrimary && (
                    <button onClick={(e) => { e.stopPropagation(); action.mutate({ id: app.id, action: primary.action }); }} aria-label={`${app.name} を${primary.label}`} className={`flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-xl px-2.5 text-sm font-semibold sm:px-3.5 ${primary.action === "stop" ? "border border-zinc-300 bg-white text-zinc-800 hover:bg-zinc-100 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700" : "bg-accent-50 text-accent-700 hover:bg-accent-100 dark:bg-accent-950 dark:text-accent-300 dark:hover:bg-accent-900"}`}>{primary.icon}<span className="hidden sm:inline">{primary.label}</span></button>
                  )}
                </div>}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* モバイル FAB */}

      {addOpen && <AddAppSheet onClose={() => setParams({})} />}
      {editing && <AddAppSheet editApp={editing} onClose={() => setEditing(null)} />}

      {detail && (
        <AppDetailSheet
          app={apps?.find((a) => a.id === detail.id) ?? detail}
          onClose={() => setDetail(null)}
          onEdit={() => {
            setEditing(detail);
            setDetail(null);
          }}
          onDelete={() => {
            setDeleting(detail);
            setDetail(null);
          }}
        />
      )}

      {/* アプリ Web ビュー（全画面 iframe） */}
      {webView && <WebViewOverlay appId={webView.id} name={webView.name} port={webView.port} onClose={() => setWebView(null)} />}

      {/* Web ポート選択（複数検出時の初回のみ。選択後は保存され次回から直接開く） */}
      {portPick && (
        <BottomSheet title="開くポートを選択" onClose={() => setPortPick(null)}>
          <p className="mb-3 text-xs text-zinc-400">
            複数のポートを検出しました。選択したポートは保存され、次回から Web ボタンで直接開きます（アプリの設定編集で変更できます）。
          </p>
          <ul className="space-y-2">
            {(portPick.runtime.listening_ports ?? []).map((p) => (
              <li key={p}>
                <button
                  onClick={() => {
                    openWeb(portPick.id, portPick.name, p);
                    saveWebPort(portPick, p);
                    setPortPick(null);
                  }}
                  className="flex w-full items-center justify-between rounded-xl border border-zinc-200 px-4 py-3 text-left text-sm hover:border-accent-400 dark:border-zinc-700"
                >
                  <span className="num font-medium">ポート {p}</span>
                  <span className="num truncate pl-3 text-xs text-zinc-400">
                    http://{location.hostname}:{p}/
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </BottomSheet>
      )}

      {deleting && (
        <ConfirmDialog
          title={`「${deleting.name}」を削除しますか？`}
          message="登録と systemd ユニットが削除されます。実行中の場合は停止されます。この操作は取り消せません。"
          confirmLabel="削除する"
          busy={deleteApp.isPending}
          onConfirm={() =>
            deleteApp.mutate(deleting.id, { onSuccess: () => setDeleting(null) })
          }
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

/** アプリの Web UI を全画面 iframe で表示（下部ナビより手前）。
 * PWA で window.open するとアプリ内 Safari のツールバーが出て全画面にならないため。 */
function WebViewOverlay({ appId, name, port, onClose }: { appId: number; name: string; port: number; onClose: () => void }) {
  // 同一オリジンのreverse proxyで表示する。HTTPS（Tailscale Serve等）経由でも
  // 混在コンテンツにならず、認証（セッションcookie）も同一オリジンで効く。
  const url = `/appview/${appId}/`;
  const directUrl = `http://${location.hostname}:${port}/`;
  return createPortal(
    <div className="fixed inset-0 z-40 flex flex-col bg-white dark:bg-zinc-950">
      <div className="safe-top flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-1.5 dark:border-zinc-800">
        <p className="min-w-0 flex-1 truncate text-sm font-medium">
          {name}
          <span className="num ml-2 text-xs text-zinc-400">:{port}</span>
        </p>
        <a
          href={directUrl}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="ブラウザで開く"
          className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          ブラウザで開く ↗
        </a>
        <button
          onClick={onClose}
          aria-label="閉じる"
          className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <IconX />
        </button>
      </div>
      <iframe src={url} title={name} className="min-h-0 w-full flex-1 border-0" allow="fullscreen" />
    </div>,
    document.body,
  );
}

function AppAvatar({ app }: { app: ManagedApp }) {
  return app.icon_path ? (
    <img src={app.icon_path} alt="" className="h-10 w-10 shrink-0 rounded-xl bg-zinc-100 object-cover dark:bg-zinc-800" />
  ) : (
    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-zinc-100 text-base font-semibold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
      {app.name[0]}
    </span>
  );
}

function AppDetailSheet({
  app,
  onClose,
  onEdit,
  onDelete,
}: {
  app: ManagedApp;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const can = useAuth((s) => s.can);
  const action = useAppAction();
  const navigate = useNavigate();
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const isUrl = app.application_type === "url_shortcut";
  const rows: [string, string][] = [
    ["種類", app.application_type],
    ...(isUrl ? ([["URL", app.url ?? "—"]] as [string, string][]) : []),
    ["状態", app.runtime.status],
    ["PID", app.runtime.pid?.toString() ?? "—"],
    ["稼働時間", formatUptime(app.runtime.uptime_seconds)],
    ["CPU", app.runtime.cpu_percent != null ? `${app.runtime.cpu_percent.toFixed(1)}%` : "—"],
    ["RAM", app.runtime.memory_bytes != null ? formatBytes(app.runtime.memory_bytes) : "—"],
    ["GPU", app.runtime.gpu_percent != null ? `${app.runtime.gpu_percent.toFixed(1)}%` : "N/A"],
    ["VRAM", app.runtime.vram_bytes != null ? formatBytes(app.runtime.vram_bytes) : "N/A"],
    ["再起動回数", String(app.runtime.restart_count)],
    ["ユニット", app.systemd_unit_name || "—"],
    ...(app.application_type === "systemd_service" ? ([["scope", app.systemd_scope]] as [string, string][]) : []),
    ["Python", app.python_path ?? "—"],
    ["スクリプト", app.script_path ?? app.executable_path ?? "—"],
    ["作業ディレクトリ", app.working_directory ?? "—"],
    ["引数", app.arguments.join(" ") || "—"],
    ["自動起動", app.auto_start ? "有効" : "無効"],
    ["再起動ポリシー", app.restart_policy],
    ["ヘルスチェック", app.health_check.type === "none" ? "未設定" : app.health_check.type],
    ...(app.runtime.health ? ([["確認結果", `${app.runtime.health.ok ? "正常" : "異常"} · ${app.runtime.health.message}`]] as [string, string][]) : []),
    ...(!isUrl
      ? ([
          ["Web ポート", app.web_port != null ? String(app.web_port) : "—"],
          ["待受ポート", (app.runtime.listening_ports ?? []).join(", ") || "—"],
        ] as [string, string][])
      : []),
  ];
  return (
    <BottomSheet title={app.name} onClose={onClose} wide>
      <div className="mb-4 flex flex-wrap gap-2">
        {isUrl && app.url && (
          <a
            href={app.url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-xl bg-accent-50 px-3.5 py-2 text-sm font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
          >
            開く ↗
          </a>
        )}
        {!isUrl && can("apps.start") && app.runtime.status !== "RUNNING" && canRunSystemdAction(app, "start") && (
          <SheetButton onClick={() => action.mutate({ id: app.id, action: "start" })}>
            起動
          </SheetButton>
        )}
        {!isUrl && can("apps.stop") && app.runtime.status === "RUNNING" && canRunSystemdAction(app, "stop") && (
          <SheetButton onClick={() => action.mutate({ id: app.id, action: "stop" })}>
            停止
          </SheetButton>
        )}
        {!isUrl && can("apps.start") && canRunSystemdAction(app, "restart") && (
          <SheetButton onClick={() => action.mutate({ id: app.id, action: "restart" })}>
            再起動
          </SheetButton>
        )}
        {!isUrl && <SheetButton onClick={() => navigate(`/logs?app=${app.id}`)}>ログ</SheetButton>}
        {!isUrl && app.health_check.type !== "none" && (
          <SheetButton onClick={async () => {
            try {
              const result = await api<{ ok: boolean; message: string }>(`/apps/${app.id}/health-check`, { method: "POST" });
              show(result.ok ? `ヘルスチェック正常: ${result.message}` : `ヘルスチェック異常: ${result.message}`, result.ok ? "success" : "error");
              qc.invalidateQueries({ queryKey: ["apps"] });
            } catch (e) { show(e instanceof Error ? e.message : "ヘルスチェックに失敗しました", "error"); }
          }}>ヘルスチェック</SheetButton>
        )}
        {can("apps.edit") && (
          <SheetButton onClick={onEdit}>設定を編集</SheetButton>
        )}
        {can("apps.delete") && (
          <SheetButton danger onClick={onDelete}>
            削除
          </SheetButton>
        )}
      </div>
      {app.env_warnings.length > 0 && (
        <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
          {app.env_warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}
      <dl className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
        {rows.map(([k, v]) => (
          <div key={k} className="flex gap-4 py-2.5">
            <dt className="w-32 shrink-0 text-zinc-400">{k}</dt>
            <dd className="num min-w-0 break-all">{v}</dd>
          </div>
        ))}
      </dl>
    </BottomSheet>
  );
}

function SheetButton({
  children,
  danger,
  onClick,
}: {
  children: React.ReactNode;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-xl px-3.5 py-2 text-sm font-medium ${
        danger
          ? "bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-950/40 dark:text-red-400"
          : "bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
      }`}
    >
      {children}
    </button>
  );
}
