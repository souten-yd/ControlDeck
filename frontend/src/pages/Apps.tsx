import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAppAction, useApps, useDeleteApp } from "../api/hooks";
import { useAuth } from "../stores";
import { formatBytes, formatUptime } from "../lib/format";
import {
  BottomSheet,
  ConfirmDialog,
  DropdownMenu,
  Skeleton,
  StatusBadge,
} from "../components/ui";
import { IconDots, IconPlay, IconPlus, IconRestart, IconStop } from "../components/icons";
import { AddAppSheet } from "../features/apps/AddAppSheet";
import type { ManagedApp } from "../types";

export default function AppsPage() {
  const { data: apps, isLoading } = useApps();
  const [params, setParams] = useSearchParams();
  const [detail, setDetail] = useState<ManagedApp | null>(null);
  const [deleting, setDeleting] = useState<ManagedApp | null>(null);
  const can = useAuth((s) => s.can);
  const action = useAppAction();
  const deleteApp = useDeleteApp();
  const navigate = useNavigate();
  const addOpen = params.get("add") === "1";

  const primaryAction = (app: ManagedApp) => {
    const st = app.runtime.status;
    if (st === "RUNNING" || st === "DEGRADED")
      return { label: "停止", icon: <IconStop />, action: "stop", perm: "apps.stop" };
    if (st === "FAILED")
      return { label: "再起動", icon: <IconRestart />, action: "restart", perm: "apps.start" };
    if (st === "STOPPED" || st === "UNKNOWN")
      return { label: "起動", icon: <IconPlay />, action: "start", perm: "apps.start" };
    return null; // 遷移中
  };

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">アプリ</h1>
        {can("apps.edit") && (
          <button
            onClick={() => setParams({ add: "1" })}
            className="hidden items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700 md:flex"
          >
            <IconPlus /> アプリを追加
          </button>
        )}
      </div>

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
            return (
              <li
                key={app.id}
                className="flex cursor-pointer items-center gap-3 rounded-2xl border border-zinc-200 bg-white p-4 transition-colors hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
                onClick={() => setDetail(app)}
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-zinc-100 text-base font-semibold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  {app.name[0]}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{app.name}</p>
                  <div className="mt-0.5 flex items-center gap-3">
                    <StatusBadge status={app.runtime.status} />
                    {app.runtime.status === "RUNNING" && (
                      <span className="num text-xs text-zinc-400">
                        {formatUptime(app.runtime.uptime_seconds)}
                        <span className="hidden sm:inline">
                          {app.runtime.cpu_percent != null &&
                            ` · CPU ${app.runtime.cpu_percent.toFixed(0)}%`}
                          {app.runtime.memory_bytes != null &&
                            ` · ${formatBytes(app.runtime.memory_bytes)}`}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
                {primary && can(primary.perm) && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      action.mutate({ id: app.id, action: primary.action });
                    }}
                    aria-label={`${app.name} を${primary.label}`}
                    className={`flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-xl px-3 text-sm font-medium sm:min-w-0 sm:px-3.5 ${
                      primary.action === "stop"
                        ? "bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
                        : "bg-accent-50 text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
                    }`}
                  >
                    {primary.icon}
                    <span className="hidden sm:inline">{primary.label}</span>
                  </button>
                )}
                <DropdownMenu
                  ariaLabel={`${app.name} のメニュー`}
                  trigger={<IconDots />}
                  items={[
                    { label: "ログ", onSelect: () => navigate(`/logs?app=${app.id}`) },
                    ...(can("apps.start")
                      ? [{ label: "再起動", onSelect: () => action.mutate({ id: app.id, action: "restart" }) }]
                      : []),
                    ...(can("apps.stop") && app.runtime.status === "RUNNING"
                      ? [{ label: "強制終了", danger: true, onSelect: () => action.mutate({ id: app.id, action: "kill" }) }]
                      : []),
                    { label: "詳細・設定", onSelect: () => setDetail(app) },
                    ...(can("apps.delete")
                      ? [{ label: "削除", danger: true, onSelect: () => setDeleting(app) }]
                      : []),
                  ]}
                />
              </li>
            );
          })}
        </ul>
      )}

      {/* モバイル FAB */}
      {can("apps.edit") && (
        <button
          onClick={() => setParams({ add: "1" })}
          aria-label="アプリを追加"
          className="fixed bottom-24 right-4 z-20 grid h-13 w-13 place-items-center rounded-2xl bg-accent-600 p-3.5 text-xl text-white shadow-lg hover:bg-accent-700 md:hidden"
        >
          <IconPlus />
        </button>
      )}

      {addOpen && <AddAppSheet onClose={() => setParams({})} />}

      {detail && (
        <AppDetailSheet
          app={apps?.find((a) => a.id === detail.id) ?? detail}
          onClose={() => setDetail(null)}
          onDelete={() => {
            setDeleting(detail);
            setDetail(null);
          }}
        />
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

function AppDetailSheet({
  app,
  onClose,
  onDelete,
}: {
  app: ManagedApp;
  onClose: () => void;
  onDelete: () => void;
}) {
  const can = useAuth((s) => s.can);
  const action = useAppAction();
  const navigate = useNavigate();
  const rows: [string, string][] = [
    ["種類", app.application_type],
    ["状態", app.runtime.status],
    ["PID", app.runtime.pid?.toString() ?? "—"],
    ["稼働時間", formatUptime(app.runtime.uptime_seconds)],
    ["CPU", app.runtime.cpu_percent != null ? `${app.runtime.cpu_percent.toFixed(1)}%` : "—"],
    ["RAM", app.runtime.memory_bytes != null ? formatBytes(app.runtime.memory_bytes) : "—"],
    ["再起動回数", String(app.runtime.restart_count)],
    ["ユニット", app.systemd_unit_name || "—"],
    ["Python", app.python_path ?? "—"],
    ["スクリプト", app.script_path ?? app.executable_path ?? "—"],
    ["作業ディレクトリ", app.working_directory ?? "—"],
    ["引数", app.arguments.join(" ") || "—"],
    ["自動起動", app.auto_start ? "有効" : "無効"],
    ["再起動ポリシー", app.restart_policy],
  ];
  return (
    <BottomSheet title={app.name} onClose={onClose} wide>
      <div className="mb-4 flex flex-wrap gap-2">
        {can("apps.start") && app.runtime.status !== "RUNNING" && (
          <SheetButton onClick={() => action.mutate({ id: app.id, action: "start" })}>
            起動
          </SheetButton>
        )}
        {can("apps.stop") && app.runtime.status === "RUNNING" && (
          <SheetButton onClick={() => action.mutate({ id: app.id, action: "stop" })}>
            停止
          </SheetButton>
        )}
        {can("apps.start") && (
          <SheetButton onClick={() => action.mutate({ id: app.id, action: "restart" })}>
            再起動
          </SheetButton>
        )}
        <SheetButton onClick={() => navigate(`/logs?app=${app.id}`)}>ログ</SheetButton>
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
