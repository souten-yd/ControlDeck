import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import { api, wsUrl } from "../api/client";
import { useApps } from "../api/hooks";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, DropdownMenu } from "../components/ui";
import { IconDots, IconPause, IconPlay, IconSearch } from "../components/icons";
import { PageHeader } from "../components/PageHeader";

const MAX_LINES = 20000;
const ROW_HEIGHT = 20;

export default function LogsPage() {
  const { data: apps } = useApps();
  const [params, setParams] = useSearchParams();
  const appId = params.get("app") ? Number(params.get("app")) : null;
  const stream = params.get("stream") === "stderr" ? "stderr" : "stdout";
  const app = apps?.find((a) => a.id === appId) ?? null;
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);

  const [lines, setLines] = useState<string[]>([]);
  const [paused, setPaused] = useState(false);
  const [query, setQuery] = useState("");
  const [wrap, setWrap] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const pendingRef = useRef<string[]>([]);
  const partialRef = useRef("");

  // WebSocket 接続
  useEffect(() => {
    if (appId == null) return;
    setLines([]);
    partialRef.current = "";
    let closed = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout>;
    let retry = 0;

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(wsUrl(`/apps/${appId}/logs/stream?stream=${stream}`));
      ws.onopen = () => (retry = 0);
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as
          | { type: "initial"; lines: string[] }
          | { type: "append"; data: string };
        if (msg.type === "initial") {
          setLines(msg.lines);
          return;
        }
        const text = partialRef.current + msg.data;
        const parts = text.split("\n");
        partialRef.current = parts.pop() ?? "";
        if (parts.length === 0) return;
        if (pausedRef.current) {
          pendingRef.current.push(...parts);
        } else {
          setLines((old) => [...old, ...parts].slice(-MAX_LINES));
        }
      };
      ws.onclose = () => {
        if (!closed) retryTimer = setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++));
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, [appId, stream]);

  const togglePause = () => {
    if (paused && pendingRef.current.length > 0) {
      const pending = pendingRef.current;
      pendingRef.current = [];
      setLines((old) => [...old, ...pending].slice(-MAX_LINES));
    }
    setPaused(!paused);
  };

  const filtered = useMemo(() => {
    if (!query.trim()) return lines;
    try {
      const re = new RegExp(query, "i");
      return lines.filter((l) => re.test(l));
    } catch {
      const q = query.toLowerCase();
      return lines.filter((l) => l.toLowerCase().includes(q));
    }
  }, [lines, query]);

  const download = () => {
    if (appId != null)
      window.open(`/api/v1/apps/${appId}/logs/download?stream=${stream}`, "_blank");
  };

  const copyAll = async () => {
    await navigator.clipboard.writeText(filtered.join("\n"));
    show("コピーしました", "info");
  };

  const deleteLogs = async () => {
    if (appId == null) return;
    try {
      await api(`/apps/${appId}/logs?stream=all`, { method: "DELETE" });
      setLines([]);
      show("ログを削除しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "削除に失敗しました", "error");
    }
    setConfirmDelete(false);
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Logs" className="mb-0 shrink-0 border-b border-zinc-200 px-4 py-4 dark:border-zinc-800" />
      {/* ツールバー（常時表示は最小限） */}
      <div className="flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <select
          value={appId ?? ""}
          onChange={(e) =>
            setParams(e.target.value ? { app: e.target.value, stream } : {})
          }
          aria-label="アプリを選択"
          className="max-w-[40vw] rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        >
          <option value="">アプリを選択</option>
          {apps?.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <div className="relative min-w-0 flex-1">
          <IconSearch className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-zinc-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="検索（正規表現可）"
            aria-label="ログ検索"
            className="w-full rounded-lg border border-zinc-300 bg-white py-1.5 pl-8 pr-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          />
        </div>
        <button
          onClick={togglePause}
          aria-label={paused ? "追従を再開" : "一時停止"}
          className={`rounded-lg p-2 ${paused ? "bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}
        >
          {paused ? <IconPlay /> : <IconPause />}
        </button>
        <DropdownMenu
          ariaLabel="Log menu"
          trigger={<IconDots />}
          items={[
            {
              label: stream === "stdout" ? "Switch to stderr" : "Switch to stdout",
              onSelect: () =>
                appId != null &&
                setParams({ app: String(appId), stream: stream === "stdout" ? "stderr" : "stdout" }),
            },
            { label: wrap ? "Disable Line Wrap" : "Wrap Lines", onSelect: () => setWrap(!wrap) },
            { label: "Download", onSelect: download },
            { label: "Copy All", onSelect: copyAll },
            ...(can("logs.delete")
              ? [{ label: "Delete Logs", danger: true, onSelect: () => setConfirmDelete(true) }]
              : []),
          ]}
        />
      </div>

      {/* 本体 */}
      {appId == null ? (
        <div className="grid flex-1 place-items-center p-8 text-center text-sm text-zinc-400">
          アプリを選択するとログが表示されます
        </div>
      ) : (
        <LogViewer lines={filtered} follow={!paused} wrap={wrap} />
      )}

      <div className="shrink-0 border-t border-zinc-200 px-3 py-1 text-[11px] text-zinc-400 dark:border-zinc-800">
        {app ? `${app.name} · ${stream} · ${filtered.length.toLocaleString()} 行` : ""}
        {paused && " · 一時停止中"}
      </div>

      {confirmDelete && (
        <BottomSheet title="ログを削除しますか？" onClose={() => setConfirmDelete(false)}>
          <p className="text-sm text-zinc-500">
            stdout / stderr の両方が削除されます。この操作は取り消せません。
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <button
              onClick={() => setConfirmDelete(false)}
              className="rounded-xl px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              キャンセル
            </button>
            <button
              onClick={deleteLogs}
              className="rounded-xl bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
            >
              削除する
            </button>
          </div>
        </BottomSheet>
      )}
    </div>
  );
}

/** 仮想スクロールのログビューア。数万行でも軽快に動作する。 */
function LogViewer({
  lines,
  follow,
  wrap,
}: {
  lines: string[];
  follow: boolean;
  wrap: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState({ start: 0, end: 100 });
  const stickToBottom = useRef(true);

  const recompute = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const start = Math.max(0, Math.floor(el.scrollTop / ROW_HEIGHT) - 20);
    const visible = Math.ceil(el.clientHeight / ROW_HEIGHT) + 40;
    setRange({ start, end: Math.min(lines.length, start + visible) });
    stickToBottom.current =
      el.scrollTop + el.clientHeight >= el.scrollHeight - ROW_HEIGHT * 3;
  }, [lines.length]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    if (follow && stickToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
    recompute();
  }, [lines, follow, recompute]);

  // 折り返しモードでは仮想化せず末尾 2000 行のみ表示（高さが可変になるため）
  if (wrap) {
    const shown = lines.slice(-2000);
    return (
      <div ref={containerRef} className="min-h-0 flex-1 overflow-auto bg-zinc-950 p-2">
        <pre className="whitespace-pre-wrap break-all font-mono text-[12px] leading-5 text-zinc-200">
          {shown.join("\n")}
        </pre>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={recompute}
      className="min-h-0 flex-1 overflow-auto bg-zinc-950"
      role="log"
      aria-label="アプリログ"
    >
      <div style={{ height: lines.length * ROW_HEIGHT, position: "relative" }}>
        {lines.slice(range.start, range.end).map((line, i) => (
          <div
            key={range.start + i}
            style={{
              position: "absolute",
              top: (range.start + i) * ROW_HEIGHT,
              height: ROW_HEIGHT,
              left: 0,
              right: 0,
            }}
            className={`overflow-hidden whitespace-pre px-2 font-mono text-[12px] leading-5 ${lineColor(line)}`}
          >
            {line || " "}
          </div>
        ))}
      </div>
    </div>
  );
}

function lineColor(line: string): string {
  if (/\b(ERROR|CRITICAL|FATAL|Traceback)\b/.test(line)) return "text-red-400";
  if (/\b(WARN|WARNING)\b/.test(line)) return "text-amber-400";
  return "text-zinc-200";
}
