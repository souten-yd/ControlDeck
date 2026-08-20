import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import {
  authorizeAddonBridgeCall,
  openAddonBridge,
  type AddonBridgeSession,
  type EffectiveAddon,
  type EffectiveContribution,
} from "../../api/addons";
import { ApiError } from "../../api/client";
import { api } from "../../api/client";
import { FilePicker } from "../../components/FilePicker";
import { BottomSheet } from "../../components/ui";
import { projectLabApi } from "../../api/projectLab";
import { ACCENTS, useTheme, useToasts } from "../../stores";
import { AddonStatusChip, addonStateMessage } from "./AddonStatus";

interface BridgeRequest {
  id: string;
  method: string;
  params?: Record<string, unknown>;
  session_nonce?: string;
  type?: string;
  shortcut?: string;
}

interface ThemeTokens {
  token_version: "1.0";
  color_scheme: "light" | "dark";
  accent: string;
  bg: string;
  surface: string;
  text: string;
  border: string;
  muted: string;
  radius_sm: number;
  radius_md: number;
  spacing_unit: number;
  density: "comfortable" | "compact";
  locale: "en" | "ja";
  safe_area: { top: number; right: number; bottom: number; left: number };
  motion_reduced: boolean;
}

interface FileBridgeRequest {
  purpose: "pick" | "export";
  mode: "file" | "dir";
  title: string;
  suggestedName?: string;
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
}

interface ProjectBridgeRequest {
  title: string;
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
}

function useThemeTokens(): ThemeTokens {
  const theme = useTheme((state) => state.theme);
  const accent = useTheme((state) => state.accent);
  const oled = useTheme((state) => state.oled);
  const [mediaRevision, setMediaRevision] = useState(0);
  useEffect(() => {
    const dark = matchMedia("(prefers-color-scheme: dark)");
    const motion = matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => setMediaRevision((value) => value + 1);
    dark.addEventListener("change", changed);
    motion.addEventListener("change", changed);
    return () => {
      dark.removeEventListener("change", changed);
      motion.removeEventListener("change", changed);
    };
  }, []);
  return useMemo(() => {
    const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    return {
      token_version: "1.0",
      color_scheme: dark ? "dark" : "light",
      accent: ACCENTS.find((item) => item.id === accent)?.color ?? "#3b82f6",
      bg: dark ? (oled ? "#000000" : "#09090b") : "#fafafa",
      surface: dark ? "#18181b" : "#ffffff",
      text: dark ? "#f4f4f5" : "#18181b",
      border: dark ? "#3f3f46" : "#e4e4e7",
      muted: dark ? "#a1a1aa" : "#71717a",
      radius_sm: 8,
      radius_md: 12,
      spacing_unit: 4,
      density: "comfortable",
      locale: navigator.language.toLowerCase().startsWith("ja") ? "ja" : "en",
      safe_area: { top: 0, right: 0, bottom: 0, left: 0 },
      motion_reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
    };
  }, [accent, mediaRevision, oled, theme]);
}

function bridgeError(error: unknown) {
  if (error instanceof ApiError && error.detail && typeof error.detail === "object") {
    const detail = error.detail as { code?: unknown; message?: unknown };
    return {
      code: typeof detail.code === "string" ? detail.code : `http_${error.status}`,
      message: typeof detail.message === "string" ? detail.message : error.message,
    };
  }
  return { code: "host_error", message: error instanceof Error ? error.message : "Host Bridgeでエラーが発生しました" };
}

function entryPath(routePath: string, contributionPath?: string): string {
  return routePath === "/" ? contributionPath || "/" : routePath;
}

export function EmbeddedAddonView({
  addon,
  contribution,
  routePath,
}: {
  addon: EffectiveAddon;
  contribution: EffectiveContribution;
  routePath: string;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const show = useToasts((state) => state.show);
  const themeTokens = useThemeTokens();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const portRef = useRef<MessagePort | null>(null);
  const sessionRef = useRef<AddonBridgeSession | null>(null);
  const notificationTimes = useRef<number[]>([]);
  const notificationDedupe = useRef(new Map<string, number>());
  const fileGrants = useRef(new Map<string, { path: string; kind: string }>());
  const jobSubscriptions = useRef(new Map<string, number>());
  const initialPath = useRef(entryPath(routePath, contribution.path));
  const [connectionKey, setConnectionKey] = useState(0);
  const [connection, setConnection] = useState<"connecting" | "ready" | "timeout" | "error">("connecting");
  const [error, setError] = useState("");
  const [title, setTitle] = useState(addon.name);
  const [busy, setBusy] = useState(false);
  const [fileRequest, setFileRequest] = useState<FileBridgeRequest | null>(null);
  const [projectRequest, setProjectRequest] = useState<ProjectBridgeRequest | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const projects = useQuery({
    queryKey: ["project-lab", "projects", "addon-picker"],
    queryFn: projectLabApi.list,
    enabled: projectRequest !== null,
  });
  const job = useQuery({
    queryKey: ["addon-job", jobId],
    queryFn: () => api<Record<string, unknown>>(`/jobs/${encodeURIComponent(jobId as string)}`),
    enabled: jobId !== null,
    refetchInterval: jobId ? 1_000 : false,
  });
  const viewIdentity = `${addon.id}:${contribution.id}`;
  const viewIdentityRef = useRef(viewIdentity);
  if (viewIdentityRef.current !== viewIdentity) {
    viewIdentityRef.current = viewIdentity;
    initialPath.current = entryPath(routePath, contribution.path);
  }
  const framePath = initialPath.current.startsWith("/") ? initialPath.current : "/";
  const frameSrc = `/addon-frame/${encodeURIComponent(addon.id)}${framePath}`;

  const sendEvent = useCallback((event: string, data: unknown) => {
    portRef.current?.postMessage({ type: "event", event, data });
  }, []);

  useEffect(() => {
    setConnection("connecting");
    setError("");
    setTitle(addon.name);
    setBusy(false);
  }, [addon.name, viewIdentity]);

  useEffect(() => {
    if (!busy) return;
    const beforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    const beforeNavigation = (event: MouseEvent) => {
      const link = (event.target as Element | null)?.closest("a[href]");
      if (!link || window.confirm("拡張機能に未保存の変更があります。移動しますか？")) return;
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", beforeNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", beforeNavigation, true);
    };
  }, [busy]);

  const execute = useCallback(async (request: BridgeRequest, session: AddonBridgeSession) => {
    const params = request.params ?? {};
    const approval = await authorizeAddonBridgeCall(addon.id, session, request.method, params);
    if (request.method === "host.theme.get") return themeTokens;
    if (request.method === "host.context.get") return {
      addon_id: addon.id,
      view_id: contribution.id,
      route: location.pathname,
      locale: themeTokens.locale,
      visible: document.visibilityState === "visible",
    };
    if (request.method === "host.route.open") {
      navigate(String(params.route), { replace: params.replace === true });
      return { opened: true };
    }
    if (request.method === "host.route.sync") {
      const path = String(params.path);
      const target = `/x/${addon.id}/${contribution.id}${path === "/" ? "" : path}`;
      navigate(target, { replace: params.replace === true });
      return { route: target };
    }
    if (request.method === "host.title.set") {
      setTitle(String(params.title));
      return { title: String(params.title) };
    }
    if (request.method === "host.notification.show") {
      const now = Date.now();
      notificationTimes.current = notificationTimes.current.filter((value) => now - value < 60_000);
      const dedupeKey = typeof params.dedupe_key === "string" ? params.dedupe_key : "";
      if (dedupeKey && now - (notificationDedupe.current.get(dedupeKey) ?? 0) < 60_000) return { shown: false, deduplicated: true };
      if (notificationTimes.current.length >= 6) return { shown: false, rate_limited: true };
      notificationTimes.current.push(now);
      if (dedupeKey) notificationDedupe.current.set(dedupeKey, now);
      show(`${String(params.title)}: ${String(params.message)}`, params.level === "error" ? "error" : params.level === "success" ? "success" : "info");
      return { shown: true };
    }
    if (request.method === "host.permission.has") return { has_permission: approval.has_permission === true };
    if (request.method === "host.busy.set") {
      setBusy(params.busy === true);
      return { busy: params.busy === true };
    }
    if (request.method === "host.file.pick" || request.method === "host.file.export") {
      return await new Promise((resolve, reject) => setFileRequest({
        purpose: request.method === "host.file.pick" ? "pick" : "export",
        mode: request.method === "host.file.pick" && params.mode === "dir" ? "dir" : request.method === "host.file.export" ? "dir" : "file",
        title: typeof params.title === "string" ? params.title : request.method === "host.file.export" ? "書き出し先を選択" : "拡張機能へ渡す項目を選択",
        suggestedName: typeof params.suggested_name === "string" ? params.suggested_name : undefined,
        resolve,
        reject,
      }));
    }
    if (request.method === "host.project.pick") {
      return await new Promise((resolve, reject) => setProjectRequest({
        title: typeof params.title === "string" ? params.title : "プロジェクトを選択",
        resolve,
        reject,
      }));
    }
    if (request.method === "host.job.open") {
      setJobId(String(params.job_id));
      return { opened: true, job_id: String(params.job_id) };
    }
    if (request.method === "host.job.subscribe") {
      const subscribedId = String(params.job_id);
      if (!jobSubscriptions.current.has(subscribedId)) {
        const poll = async () => {
          try {
            const value = await api<Record<string, unknown>>(`/jobs/${encodeURIComponent(subscribedId)}`);
            sendEvent("job.changed", { job_id: subscribedId, job: value });
            if (["succeeded", "failed", "canceled", "interrupted"].includes(String(value.status).toLowerCase())) {
              const timer = jobSubscriptions.current.get(subscribedId);
              if (timer) window.clearInterval(timer);
              jobSubscriptions.current.delete(subscribedId);
            }
          } catch (reason) {
            sendEvent("job.error", { job_id: subscribedId, error: bridgeError(reason) });
          }
        };
        await poll();
        jobSubscriptions.current.set(subscribedId, window.setInterval(() => void poll(), 1_000));
      }
      return { subscribed: true, job_id: subscribedId };
    }
    throw new Error("このHost Bridge methodは画面側でまだ利用できません");
  }, [addon.id, contribution.id, location.pathname, navigate, sendEvent, show, themeTokens]);
  const executeRef = useRef(execute);
  const themeTokensRef = useRef(themeTokens);
  executeRef.current = execute;
  themeTokensRef.current = themeTokens;

  useEffect(() => {
    let disposed = false;
    let refreshTimer = 0;
    const timeout = window.setTimeout(() => {
      if (!disposed && !sessionRef.current) setConnection("timeout");
    }, 8_000);
    const connect = async (event: MessageEvent) => {
      const frameWindow = iframeRef.current?.contentWindow;
      const data = event.data as { type?: unknown; bridge_version?: unknown } | null;
      if (event.source !== frameWindow || event.origin !== "null" || !data || data.type !== "control-deck-addon.connect" || data.bridge_version !== "1.0") return;
      try {
        const session = await openAddonBridge(addon.id, contribution.id);
        if (disposed || event.source !== iframeRef.current?.contentWindow) return;
        const channel = new MessageChannel();
        portRef.current?.close();
        portRef.current = channel.port1;
        sessionRef.current = session;
        channel.port1.onmessage = (message: MessageEvent<BridgeRequest>) => {
          const request = message.data;
          if (!request) return;
          if (request.type === "shortcut" && request.shortcut === "command_palette") {
            if (request.session_nonce === sessionRef.current?.session_nonce) window.dispatchEvent(new Event("control-deck:command-palette"));
            return;
          }
          if (typeof request.id !== "string" || typeof request.method !== "string") return;
          if (request.session_nonce !== sessionRef.current?.session_nonce) {
            channel.port1.postMessage({ type: "response", id: request.id, ok: false, error: { code: "invalid_session", message: "Bridge sessionが一致しません" } });
            return;
          }
          const activeSession = sessionRef.current;
          if (!activeSession) return;
          void executeRef.current(request, activeSession).then((result) => {
            channel.port1.postMessage({ type: "response", id: request.id, ok: true, result });
          }).catch((reason) => {
            channel.port1.postMessage({ type: "response", id: request.id, ok: false, error: bridgeError(reason) });
          });
        };
        channel.port1.start();
        frameWindow?.postMessage({
          type: "control-deck-host.connected",
          bridge_version: "1.0",
          session_nonce: session.session_nonce,
          theme: themeTokensRef.current,
        }, "*", [channel.port2]);
        setConnection("ready");
        setError("");
        channel.port1.postMessage({ type: "event", event: "route.changed", data: { path: routePath || "/" } });
        channel.port1.postMessage({ type: "event", event: "visibility.changed", data: { visible: document.visibilityState === "visible" } });
        window.clearTimeout(timeout);
        refreshTimer = window.setInterval(() => {
          void openAddonBridge(addon.id, contribution.id).then((fresh) => {
            if (disposed) return;
            sessionRef.current = fresh;
            sendEvent("session.updated", { session_nonce: fresh.session_nonce, expires_in: fresh.expires_in });
          });
        }, 8 * 60_000);
      } catch (reason) {
        if (!disposed) {
          setConnection("error");
          setError(bridgeError(reason).message);
        }
      }
    };
    window.addEventListener("message", connect);
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      window.clearInterval(refreshTimer);
      window.removeEventListener("message", connect);
      portRef.current?.close();
      portRef.current = null;
      sessionRef.current = null;
    };
  }, [addon.id, connectionKey, contribution.id, sendEvent]);

  useEffect(() => sendEvent("theme.changed", themeTokens), [sendEvent, themeTokens]);
  useEffect(() => {
    if (addon.state === "disable_pending") sendEvent("disable.pending", { grace_ms: 2_000 });
  }, [addon.state, sendEvent]);
  useEffect(() => () => {
    for (const timer of jobSubscriptions.current.values()) window.clearInterval(timer);
    jobSubscriptions.current.clear();
  }, []);
  useEffect(() => {
    const changed = () => sendEvent("visibility.changed", { visible: document.visibilityState === "visible" });
    changed();
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, [sendEvent]);
  useEffect(() => sendEvent("route.changed", { path: routePath || "/" }), [routePath, sendEvent]);

  const retry = () => {
    initialPath.current = entryPath(routePath, contribution.path);
    setConnection("connecting");
    setError("");
    setConnectionKey((value) => value + 1);
  };

  const finishFileRequest = (path: string) => {
    if (!fileRequest) return;
    const grantId = crypto.randomUUID();
    fileGrants.current.set(grantId, { path, kind: fileRequest.purpose });
    const parts = path.split("/").filter(Boolean);
    const name = parts[parts.length - 1] ?? "selected";
    fileRequest.resolve({
      grant_id: grantId,
      name: fileRequest.suggestedName ?? name,
      kind: fileRequest.mode,
    });
    setFileRequest(null);
  };

  return <div className="flex h-full min-h-0 flex-col" style={{ backgroundColor: themeTokens.bg }}>
    <header className="flex min-h-12 shrink-0 items-center gap-3 border-b px-4" style={{ borderColor: themeTokens.border, color: themeTokens.text }}>
      <div className="min-w-0 flex-1"><h1 className="truncate text-sm font-semibold">{title}</h1><p className="truncate text-[10px]" style={{ color: themeTokens.muted }}>{addon.name} · {routePath || "/"}</p></div>
      {busy && <span className="rounded-full bg-amber-100 px-2 py-1 text-[10px] font-semibold text-amber-800">未保存</span>}
      {addon.state === "healthy" ? null : <button aria-label={`状態詳細: ${addonStateMessage(addon.state)}`} onClick={() => navigate(`/settings?extension=${encodeURIComponent(addon.id)}`)}><AddonStatusChip state={addon.state} /></button>}
      <button onClick={() => navigate(`/settings?extension=${encodeURIComponent(addon.id)}`)} className="min-h-10 rounded-xl px-3 text-xs font-medium">詳細</button>
    </header>
    <div className="relative min-h-0 flex-1">
      {connection !== "ready" && <div className="absolute inset-0 z-10 grid place-items-center p-6" style={{ backgroundColor: themeTokens.bg, color: themeTokens.text }}>
        {connection === "connecting" ? <div className="w-full max-w-sm animate-pulse space-y-3" aria-label="拡張機能を接続中"><div className="h-8 w-2/3 rounded-xl" style={{ backgroundColor: themeTokens.surface }} /><div className="h-32 rounded-2xl" style={{ backgroundColor: themeTokens.surface }} /><p className="text-center text-xs" style={{ color: themeTokens.muted }}>拡張機能へ安全に接続しています…</p></div> : <div className="max-w-sm rounded-2xl border p-5 text-center" style={{ borderColor: themeTokens.border, backgroundColor: themeTokens.surface }}><h2 className="font-semibold">拡張画面を表示できません</h2><p className="mt-2 text-sm" style={{ color: themeTokens.muted }}>{connection === "timeout" ? "8秒以内に応答がありませんでした。serviceと設定を確認してください。" : error || addonStateMessage(addon.state)}</p><div className="mt-4 flex justify-center gap-2"><button onClick={retry} className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">再試行</button><button onClick={() => navigate(`/settings?extension=${encodeURIComponent(addon.id)}`)} className="min-h-11 rounded-xl px-4 text-sm">設定を開く</button></div></div>}
      </div>}
      <iframe
        key={`${viewIdentity}:${connectionKey}`}
        ref={iframeRef}
        title={`${addon.name} — ${contribution.id}`}
        src={frameSrc}
        sandbox="allow-scripts allow-forms allow-popups allow-downloads"
        className={`h-full w-full border-0 ${connection === "ready" ? "visible" : "invisible"}`}
      />
    </div>
    {fileRequest && <FilePicker mode={fileRequest.mode} title={fileRequest.title} onSelect={finishFileRequest} onClose={() => { fileRequest.reject(new Error("picker_canceled")); setFileRequest(null); }} />}
    {projectRequest && <BottomSheet title={projectRequest.title} onClose={() => { projectRequest.reject(new Error("picker_canceled")); setProjectRequest(null); }}>
      <div className="space-y-2">{projects.isLoading && <p className="text-sm text-zinc-400">プロジェクトを読み込んでいます…</p>}{projects.isError && <p className="text-sm text-red-500">プロジェクトを取得できませんでした</p>}{projects.data?.map((project) => <button key={project.id} onClick={() => { projectRequest.resolve({ project_id: project.id, name: project.name }); setProjectRequest(null); }} className="min-h-12 w-full rounded-xl border border-zinc-200 px-3 text-left dark:border-zinc-700"><span className="block text-sm font-medium">{project.name}</span><span className="block truncate text-xs text-zinc-400">{project.description || project.id}</span></button>)}</div>
    </BottomSheet>}
    {jobId && <BottomSheet title="ジョブ詳細" onClose={() => setJobId(null)}><div className="space-y-3">{job.isLoading && <p className="text-sm text-zinc-400">ジョブを確認しています…</p>}{job.isError && <p className="text-sm text-red-500">ジョブを表示できません</p>}{job.data && <><p className="font-mono text-xs text-zinc-400">{jobId}</p><p className="text-sm font-semibold">{String(job.data.status ?? "unknown")}</p>{job.data.progress && <pre className="overflow-x-auto rounded-xl bg-zinc-50 p-3 text-xs dark:bg-zinc-950">{JSON.stringify(job.data.progress, null, 2)}</pre>}</>}</div></BottomSheet>}
  </div>;
}

export function AddonCompanion({ addon }: { addon: EffectiveAddon }) {
  const navigate = useNavigate();
  return <div className="mx-auto max-w-lg space-y-4 p-4"><section className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900"><div className="flex flex-wrap items-center gap-2"><h1 className="font-semibold">{addon.name}</h1><AddonStatusChip state={addon.state} /></div><p className="mt-2 text-sm text-zinc-500">{addon.health?.message || addonStateMessage(addon.state)}</p><p className="mt-4 rounded-xl bg-zinc-50 p-3 text-xs text-zinc-500 dark:bg-zinc-950">この拡張機能の作業画面はデスクトップ向けです。モバイルでは状態とセットアップを確認できます。</p>{addon.health?.setup && addon.health.setup.length > 0 && <ul className="mt-4 space-y-2">{addon.health.setup.map((item) => <li key={item.id} className="rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-700">{item.label} · {item.state}</li>)}</ul>}<button onClick={() => navigate(`/settings?extension=${encodeURIComponent(addon.id)}`)} className="mt-4 min-h-11 w-full rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">権限・詳細を開く</button></section></div>;
}
