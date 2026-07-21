/** Terminal V2 Lab. V1と同じUI契約に対する、独立した接続／描画実装。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { wsUrl } from "../../api/client";
import { IconSettings, IconX } from "../../components/icons";
import { createUuid } from "../../lib/clientId";
import { useToasts } from "../../stores";
import {
  prepareTerminalPaste, TerminalInputController, type InputAck, type InputError, type PasteProgress,
} from "./controllers/TerminalInputController";
import { TerminalWriteSchedulerV2 } from "./v2/TerminalWriteSchedulerV2";

interface SessionInfo {
  id: string;
  name: string;
  program?: string;
  cwd?: string;
  workload?: "idle" | "running";
}

type ConnectionState = "CONNECTING" | "REPLAYING" | "LIVE" | "RECONNECTING" | "CLOSED";

const HELPER_KEYS: { label: string; seq?: string; modifier?: "ctrl" }[] = [
  { label: "Esc", seq: "\x1b" }, { label: "Tab", seq: "\t" }, { label: "Ctrl", modifier: "ctrl" },
  { label: "↑", seq: "\x1b[A" }, { label: "↓", seq: "\x1b[B" },
  { label: "←", seq: "\x1b[D" }, { label: "→", seq: "\x1b[C" },
  { label: "^C", seq: "\x03" }, { label: "^D", seq: "\x04" },
  { label: "^Z", seq: "\x1a" }, { label: "^L", seq: "\x0c" },
];

export default function XtermViewV2({
  sessionId, sessions, onSwitch, onAutomation, onExit,
}: {
  sessionId: string;
  sessions: SessionInfo[];
  onSwitch: (id: string) => void;
  onAutomation?: () => void;
  onExit: () => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const helperRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const thumbRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const sendRef = useRef<(data: string) => void>(() => undefined);
  const pasteRef = useRef<(text: string) => void>(() => undefined);
  const cancelPasteRef = useRef<() => void>(() => undefined);
  const retryPasteRef = useRef<() => void>(() => undefined);
  const ctrlArmed = useRef(false);
  const pasteGesture = useRef({ startY: 0, copied: false });
  const suppressPasteClick = useRef(false);
  const copyAreaRef = useRef<HTMLTextAreaElement>(null);
  const show = useToasts((state) => state.show);
  const [state, setState] = useState<ConnectionState>("CONNECTING");
  const [ctrlOn, setCtrlOn] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [copyText, setCopyText] = useState("");
  const [pasteProgress, setPasteProgress] = useState<PasteProgress | null>(null);
  const current = sessions.find((session) => session.id === sessionId);

  useEffect(() => {
    const root = rootRef.current;
    const host = hostRef.current;
    const helper = helperRef.current;
    const track = trackRef.current;
    const thumb = thumbRef.current;
    if (!root || !host || !helper || !track || !thumb) return;
    const coarseMobile = window.matchMedia("(max-width: 767px) and (pointer: coarse)").matches;
    const dark = document.documentElement.classList.contains("dark");
    const terminal = new Terminal({
      fontSize: 13,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      cursorBlink: true,
      scrollback: 100_000,
      ...(coarseMobile ? { overviewRuler: { width: 1 } } : {}),
      theme: dark
        ? { background: "#09090b", foreground: "#e4e4e7" }
        : { background: "#ffffff", foreground: "#18181b", cursor: "#18181b" },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(host);
    termRef.current = terminal;
    const writes = new TerminalWriteSchedulerV2(terminal);
    const encoder = new TextEncoder();
    let disposed = false;
    let ws: WebSocket | null = null;
    let retryTimer = 0;
    let retryDelay = 300;
    let connectionGeneration = 0;
    let resizeGeneration = 0;
    let everConnected = false;
    let live = false;
    let replayStartedAt = 0;
    let replayBytes = 0;
    let lastSequence = 0;
    let pendingOutputSequence: number | null = null;
    let lastSize: { cols: number; rows: number } | null = null;
    let geometryFrame = 0;
    let geometryPending = false;
    let composing = false;
    let scrollFrame = 0;
    let historyHeight = track.clientHeight;
    const clientInstanceId = createUuid();

    const bodyStyle = document.body.getAttribute("style");
    const htmlStyle = document.documentElement.getAttribute("style");
    if (coarseMobile) {
      document.body.style.overflow = "hidden";
      document.body.style.overscrollBehavior = "none";
      document.documentElement.style.overflow = "hidden";
      document.documentElement.style.overscrollBehavior = "none";
    }

    const applyViewport = () => {
      if (!coarseMobile) return;
      const viewport = window.visualViewport;
      const layoutWidth = document.documentElement.clientWidth || window.innerWidth;
      root.style.width = `${Math.min(viewport?.width ?? innerWidth, layoutWidth)}px`;
      root.style.height = `${viewport?.height ?? innerHeight}px`;
      const left = viewport?.offsetLeft ?? 0;
      const top = viewport?.offsetTop ?? 0;
      root.style.transform = left || top ? `translate3d(${left}px, ${top}px, 0)` : "none";
    };

    const sendResize = () => {
      geometryFrame = 0;
      if (disposed || composing) { geometryPending = true; return; }
      geometryPending = false;
      const dimensions = fit.proposeDimensions();
      if (!dimensions || dimensions.cols < 10 || dimensions.rows < 3) return;
      if (terminal.cols !== dimensions.cols || terminal.rows !== dimensions.rows) {
        terminal.resize(dimensions.cols, dimensions.rows);
      }
      if (lastSize?.cols === dimensions.cols && lastSize.rows === dimensions.rows) return;
      lastSize = dimensions;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: "resize", cols: dimensions.cols, rows: dimensions.rows,
          resizeGeneration: ++resizeGeneration, connectionGeneration,
        }));
      }
    };
    const scheduleGeometry = () => {
      applyViewport();
      if (!geometryFrame) geometryFrame = requestAnimationFrame(sendResize);
    };
    applyViewport();
    fit.fit();

    const updateHistory = (remeasure = false) => {
      if (remeasure) historyHeight = track.clientHeight;
      const buffer = terminal.buffer.active;
      const maximum = buffer.baseY;
      track.setAttribute("aria-valuemax", String(maximum));
      track.setAttribute("aria-valuenow", String(buffer.viewportY));
      if (!coarseMobile || maximum <= 0 || historyHeight <= 0) {
        track.style.opacity = "0";
        track.style.pointerEvents = "none";
        return;
      }
      const thumbHeight = Math.min(historyHeight, Math.max(44, historyHeight * terminal.rows / (maximum + terminal.rows)));
      const travel = Math.max(0, historyHeight - thumbHeight);
      thumb.style.height = `${thumbHeight}px`;
      thumb.style.transform = `translateY(${travel * buffer.viewportY / maximum}px)`;
      track.style.opacity = "1";
      track.style.pointerEvents = "auto";
    };
    const onScroll = terminal.onScroll(() => {
      if (!scrollFrame) scrollFrame = requestAnimationFrame(() => { scrollFrame = 0; updateHistory(); });
    });
    const onTermResize = terminal.onResize(() => updateHistory(true));
    const trackObserver = new ResizeObserver(() => updateHistory(true));
    trackObserver.observe(track);

    const finalizeReplay = async (sequence: number) => {
      await writes.drain();
      if (disposed) return;
      terminal.scrollToBottom();
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      if (disposed) return;
      lastSequence = Math.max(lastSequence, sequence);
      live = true;
      setState("LIVE");
      host.style.opacity = "1";
      host.style.pointerEvents = "auto";
      inputController.availabilityChanged();
      if (!coarseMobile) terminal.focus();
      root.dataset.terminalV2ReplayMs = String(Math.round(performance.now() - replayStartedAt));
      root.dataset.terminalV2ReplayBytes = String(replayBytes);
    };

    const inputController = new TerminalInputController({
      canSend: () => live && ws?.readyState === WebSocket.OPEN,
      connectionGeneration: () => connectionGeneration,
      bufferedAmount: () => ws?.bufferedAmount ?? Number.POSITIVE_INFINITY,
      sendFrame: (control, bytes) => {
        if (ws?.readyState !== WebSocket.OPEN) return false;
        ws.send(JSON.stringify(control));
        ws.send(bytes);
        return true;
      },
      onProgress: setPasteProgress,
    });

    const connect = () => {
      if (disposed || ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return;
      connectionGeneration += 1;
      const generation = connectionGeneration;
      const attachMode = everConnected ? "resume" : "initial";
      live = false;
      replayStartedAt = performance.now();
      replayBytes = 0;
      setState(everConnected ? "RECONNECTING" : "CONNECTING");
      host.style.opacity = "0";
      host.style.pointerEvents = "none";
      const query = new URLSearchParams({
        rows: String(terminal.rows), cols: String(terminal.cols), clientInstanceId,
        connectionGeneration: String(generation), attachMode, lastSequence: String(lastSequence),
        engine: "v2",
      });
      ws = new WebSocket(wsUrl(`/terminals/${sessionId}/connect?${query}`));
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        if (generation !== connectionGeneration) return;
        everConnected = true;
        retryDelay = 300;
        lastSize = null;
        scheduleGeometry();
      };
      ws.onmessage = (event) => {
        if (generation !== connectionGeneration) return;
        if (typeof event.data === "string") {
          try {
            const control = JSON.parse(event.data) as Record<string, unknown>;
            if (control.type === "input_ack") { inputController.handleAck(control as unknown as InputAck); return; }
            if (control.type === "input_error") { inputController.handleError(control as unknown as InputError); return; }
            if (control.type === "output") {
              pendingOutputSequence = Number(control.sequence ?? 0);
              return;
            }
            if (control.type === "history_reset") {
              live = false;
              replayBytes = 0;
              setState("REPLAYING");
              writes.reset();
              return;
            }
            if (control.type === "history_end" || control.type === "resume_end") {
              void finalizeReplay(Number(control.sequence ?? 0));
              return;
            }
            if (["resume_ready", "resume_reset_required", "resize_ack", "size_probe_result"].includes(String(control.type))) return;
          } catch {
            // Protocol外の文字列outputは通常描画する。
          }
          writes.write(event.data);
          return;
        }
        const bytes = new Uint8Array(event.data as ArrayBuffer);
        replayBytes += live ? 0 : bytes.byteLength;
        const sequence = pendingOutputSequence;
        pendingOutputSequence = null;
        writes.write(bytes, () => {
          if (sequence !== null) lastSequence = Math.max(lastSequence, sequence);
        });
      };
      ws.onclose = (event) => {
        if (generation !== connectionGeneration) return;
        inputController.connectionChanged();
        if (disposed) return;
        if (event.code === 4404) { setState("CLOSED"); return; }
        setState("RECONNECTING");
        retryTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(5_000, retryDelay * 2);
      };
    };

    const leaveHistory = () => {
      if (terminal.buffer.active.viewportY < terminal.buffer.active.baseY) terminal.scrollToBottom();
    };
    const send = (data: string) => {
      leaveHistory();
      if (live && ws?.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
    };
    sendRef.current = send;
    const onData = terminal.onData((data) => {
      // replay中のDA/DSR自動応答は過去snapshotに対するものなので送らない。
      if (!live) return;
      if (ctrlArmed.current && data.length === 1 && /[a-z]/i.test(data)) {
        ctrlArmed.current = false;
        setCtrlOn(false);
        send(String.fromCharCode(data.toLowerCase().charCodeAt(0) - 96));
      } else send(data);
    });
    pasteRef.current = (text) => {
      leaveHistory();
      inputController.enqueuePaste(text, prepareTerminalPaste(text, terminal.modes.bracketedPasteMode));
    };
    cancelPasteRef.current = () => inputController.cancelCurrent();
    retryPasteRef.current = () => inputController.retryCurrent();
    const onPaste = (event: ClipboardEvent) => {
      const text = event.clipboardData?.getData("text/plain");
      if (text === undefined) return;
      event.preventDefault();
      event.stopPropagation();
      pasteRef.current(text);
    };
    host.addEventListener("paste", onPaste, true);

    const textarea = host.querySelector<HTMLTextAreaElement>(".xterm-helper-textarea");
    const onCompositionStart = () => { composing = true; };
    const onCompositionEnd = () => {
      composing = false;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (geometryPending) scheduleGeometry();
      }));
    };
    textarea?.addEventListener("compositionstart", onCompositionStart);
    textarea?.addEventListener("compositionend", onCompositionEnd);
    const resizeObserver = new ResizeObserver(scheduleGeometry);
    resizeObserver.observe(host);
    visualViewport?.addEventListener("resize", scheduleGeometry);
    visualViewport?.addEventListener("scroll", applyViewport);
    window.addEventListener("resize", scheduleGeometry);

    let touchTracking = false;
    let touchScrolling = false;
    let startX = 0;
    let startY = 0;
    let lastY = 0;
    let remainder = 0;
    let cellHeight = 13;
    let touchFrame = 0;
    const flushTouch = () => {
      touchFrame = 0;
      const lines = Math.trunc(remainder);
      if (lines) { terminal.scrollLines(Math.max(-100, Math.min(100, lines))); remainder -= lines; }
    };
    const touchStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) return;
      event.preventDefault(); event.stopPropagation();
      const touch = event.touches[0];
      touchTracking = true; touchScrolling = false;
      startX = touch.clientX; startY = touch.clientY; lastY = touch.clientY; remainder = 0;
      const screen = host.querySelector<HTMLElement>(".xterm-screen");
      cellHeight = screen && terminal.rows ? screen.getBoundingClientRect().height / terminal.rows : 13;
    };
    const touchMove = (event: TouchEvent) => {
      if (!touchTracking || event.touches.length !== 1) return;
      const touch = event.touches[0];
      if (!touchScrolling) {
        const dx = Math.abs(touch.clientX - startX);
        const dy = Math.abs(touch.clientY - startY);
        if (Math.max(dx, dy) < 8) return;
        if (dx >= dy) { touchTracking = false; return; }
        touchScrolling = true;
      }
      event.preventDefault(); event.stopPropagation();
      remainder += 1.35 * (lastY - touch.clientY) / Math.max(1, cellHeight);
      if (!touchFrame) touchFrame = requestAnimationFrame(flushTouch);
      lastY = touch.clientY;
    };
    const touchEnd = (event: TouchEvent) => {
      event.preventDefault(); event.stopPropagation();
      const scrolled = touchScrolling;
      if (touchFrame) { cancelAnimationFrame(touchFrame); touchFrame = 0; flushTouch(); }
      touchTracking = false; touchScrolling = false;
      if (!scrolled) { leaveHistory(); terminal.focus(); }
    };
    host.addEventListener("touchstart", touchStart, { capture: true, passive: false });
    host.addEventListener("touchmove", touchMove, { capture: true, passive: false });
    host.addEventListener("touchend", touchEnd, { capture: true, passive: false });
    host.addEventListener("touchcancel", touchEnd, { capture: true, passive: false });

    const scrollToClientY = (clientY: number) => {
      const rect = track.getBoundingClientRect();
      if (!rect.height || !terminal.buffer.active.baseY) return;
      terminal.scrollToLine(Math.round(terminal.buffer.active.baseY * Math.max(0, Math.min(1, (clientY - rect.top) / rect.height))));
    };
    const trackStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) return;
      event.preventDefault(); event.stopPropagation(); track.dataset.active = "true";
      scrollToClientY(event.touches[0].clientY);
    };
    const trackMove = (event: TouchEvent) => {
      if (event.touches.length !== 1 || track.dataset.active !== "true") return;
      event.preventDefault(); event.stopPropagation(); scrollToClientY(event.touches[0].clientY);
    };
    const trackEnd = (event: TouchEvent) => { event.preventDefault(); event.stopPropagation(); track.dataset.active = "false"; };
    track.addEventListener("touchstart", trackStart, { passive: false });
    track.addEventListener("touchmove", trackMove, { passive: false });
    track.addEventListener("touchend", trackEnd, { passive: false });
    track.addEventListener("touchcancel", trackEnd, { passive: false });

    const testWindow = window as typeof window & { __controlDeckTerminalV2Test?: Record<string, unknown> };
    testWindow.__controlDeckTerminalV2Test = {
      rows: () => terminal.rows,
      cols: () => terminal.cols,
      viewportY: () => terminal.buffer.active.viewportY,
      baseY: () => terminal.buffer.active.baseY,
      send: (data: string) => send(data),
      paste: (data: string) => pasteRef.current(data),
      pasteState: () => inputController.getState(),
      scrollLines: (lines: number) => terminal.scrollLines(lines),
      openCopy: () => openCopyFromTerminal(terminal, setCopyText, setCopyOpen),
      close: () => ws?.close(4001, "v2 test reconnect"),
    };
    updateHistory(true);
    connect();

    return () => {
      disposed = true;
      live = false;
      sendRef.current = () => undefined;
      pasteRef.current = () => undefined;
      window.clearTimeout(retryTimer);
      cancelAnimationFrame(geometryFrame);
      cancelAnimationFrame(scrollFrame);
      cancelAnimationFrame(touchFrame);
      resizeObserver.disconnect();
      trackObserver.disconnect();
      visualViewport?.removeEventListener("resize", scheduleGeometry);
      visualViewport?.removeEventListener("scroll", applyViewport);
      window.removeEventListener("resize", scheduleGeometry);
      textarea?.removeEventListener("compositionstart", onCompositionStart);
      textarea?.removeEventListener("compositionend", onCompositionEnd);
      host.removeEventListener("paste", onPaste, true);
      host.removeEventListener("touchstart", touchStart, true);
      host.removeEventListener("touchmove", touchMove, true);
      host.removeEventListener("touchend", touchEnd, true);
      host.removeEventListener("touchcancel", touchEnd, true);
      track.removeEventListener("touchstart", trackStart);
      track.removeEventListener("touchmove", trackMove);
      track.removeEventListener("touchend", trackEnd);
      track.removeEventListener("touchcancel", trackEnd);
      onData.dispose(); onScroll.dispose(); onTermResize.dispose();
      inputController.dispose(); writes.dispose();
      if (ws) { ws.onopen = null; ws.onmessage = null; ws.onclose = null; ws.close(); }
      terminal.dispose();
      delete testWindow.__controlDeckTerminalV2Test;
      if (coarseMobile) {
        if (bodyStyle === null) document.body.removeAttribute("style"); else document.body.setAttribute("style", bodyStyle);
        if (htmlStyle === null) document.documentElement.removeAttribute("style"); else document.documentElement.setAttribute("style", htmlStyle);
      }
    };
  }, [sessionId]);

  const sendSeq = (sequence: string) => sendRef.current(sequence);
  const doPaste = async () => {
    try {
      const text = await navigator.clipboard?.readText?.();
      if (text) pasteRef.current(text);
      else show("クリップボードは空です");
    } catch {
      show("この接続ではクリップボードを直接読めません。キーボードの貼り付けを使用してください", "error");
    }
  };
  const openCopy = () => openCopyFromTerminal(termRef.current, setCopyText, setCopyOpen);
  const copyAll = async () => {
    const text = copyAreaRef.current?.value ?? copyText;
    try { await navigator.clipboard.writeText(text); show("コピーしました"); setCopyOpen(false); return; } catch { /* fallback */ }
    copyAreaRef.current?.focus(); copyAreaRef.current?.select();
    if (document.execCommand("copy")) { show("コピーしました"); setCopyOpen(false); }
    else show("自動コピーできません。選択範囲をコピーしてください", "error");
  };

  return createPortal(<div ref={rootRef} data-terminal-root data-terminal-engine="v2" className="fixed left-0 top-0 z-40 flex h-[100dvh] w-full max-w-full flex-col overflow-hidden bg-white dark:bg-zinc-950">
    <div data-terminal-header className="safe-top flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-1.5 dark:border-zinc-800">
      <div className="min-w-0 flex-1">
        <select value={sessionId} onChange={(event) => onSwitch(event.target.value)} aria-label="セッションを切替" className="h-8 max-w-full rounded-lg border border-zinc-300 bg-white px-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900">
          {sessions.map((session) => <option key={session.id} value={session.id}>{session.program || session.name} · {session.cwd || `#${session.id}`}</option>)}
        </select>
        <div className="mt-0.5 flex min-w-0 items-center gap-2 text-[10px]">
          <span aria-live="polite" className={`inline-flex shrink-0 items-center gap-1 ${state === "LIVE" ? "text-emerald-600 dark:text-emerald-400" : state === "CLOSED" ? "text-red-500" : "text-zinc-400"}`}><span className={`h-1.5 w-1.5 rounded-full ${state === "LIVE" ? "bg-emerald-500" : state === "CLOSED" ? "bg-red-500" : "animate-pulse bg-amber-500"}`} />{state === "LIVE" ? "Live" : state === "CLOSED" ? "Exited" : state === "RECONNECTING" ? "Reconnecting" : "Connecting"}</span>
          <span className={current?.workload === "running" ? "shrink-0 text-blue-500" : "shrink-0 text-zinc-400"}>{current?.workload === "running" ? `Foreground ${current.program}` : "Shell ready"}</span>
          <code className="min-w-0 truncate text-zinc-400">{current?.cwd || "N/A"}</code>
        </div>
      </div>
      <button onClick={openCopy} className="ml-auto hidden min-h-11 rounded-lg px-2.5 text-xs font-medium text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 md:block">コピー</button>
      {onAutomation && <button onPointerDown={(event) => event.preventDefault()} onClick={onAutomation} aria-label="Automation settings" title="Snippets and schedules" className="grid h-11 min-w-11 shrink-0 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"><IconSettings /></button>}
      <button onClick={onExit} aria-label="ターミナルを閉じる" className="ml-auto flex h-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-zinc-300 bg-white px-3 text-sm font-semibold text-zinc-700 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 md:ml-0"><IconX /><span className="hidden sm:inline">閉じる</span></button>
    </div>
    <div data-terminal-body className="relative flex min-h-0 flex-1 overflow-clip bg-white px-1 pt-1 dark:bg-zinc-950">
      <div ref={hostRef} data-terminal-host aria-hidden={state !== "LIVE"} className="terminal-xterm-host min-h-0 min-w-0 flex-1 overflow-clip opacity-0" />
      {state !== "LIVE" && <div role="status" data-terminal-replay-overlay className="absolute inset-0 grid place-items-center bg-white text-xs text-zinc-400 dark:bg-zinc-950"><span className="inline-flex items-center gap-2"><span className="h-2 w-2 animate-pulse rounded-full bg-accent-500" />ターミナルを復元中…</span></div>}
      <div ref={trackRef} data-terminal-history-track data-active="false" role="scrollbar" aria-label="ターミナル履歴位置" aria-orientation="vertical" aria-valuemin={0} aria-valuemax={0} aria-valuenow={0} className="terminal-history-track absolute inset-y-1 right-0 z-20 block w-5 opacity-0 md:hidden"><div ref={thumbRef} className="terminal-history-thumb ml-auto mr-0.5 w-1 rounded-full bg-zinc-500/70 opacity-70 dark:bg-zinc-300/70" /></div>
    </div>
    {pasteProgress && pasteProgress.totalBytes >= 32 * 1024 && pasteProgress.state !== "cancelled" && <div data-terminal-paste-progress className="flex shrink-0 items-center gap-2 border-t border-zinc-200 bg-zinc-50 px-3 py-1 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"><span className="tabular-nums">{pasteProgress.state === "completed" ? "貼り付け完了" : pasteProgress.state === "failed" ? "貼り付け失敗" : "貼り付け送信中"} {Math.ceil(pasteProgress.acknowledgedBytes / 1024)} KB / {Math.ceil(pasteProgress.totalBytes / 1024)} KB</span>{pasteProgress.state === "failed" ? <button onClick={() => retryPasteRef.current()} className="ml-auto min-h-11 px-2 font-medium text-accent-600">再試行</button> : pasteProgress.state !== "completed" ? <button onClick={() => cancelPasteRef.current()} className="ml-auto min-h-11 px-2 text-zinc-500">キャンセル</button> : null}</div>}
    <div className="relative shrink-0 md:hidden"><div ref={helperRef} data-terminal-helper className="terminal-helper-bar flex h-10 flex-nowrap gap-1 overflow-x-auto overflow-y-hidden border-t border-zinc-200 bg-zinc-50 px-2 py-1.5 dark:border-zinc-800 dark:bg-zinc-900">
      <button onPointerDown={(event) => event.preventDefault()} onClick={() => { if (suppressPasteClick.current) { suppressPasteClick.current = false; return; } void doPaste(); }} onTouchStart={(event) => { if (event.touches.length === 1) pasteGesture.current = { startY: event.touches[0].clientY, copied: false }; }} onTouchMove={(event) => { if (event.touches.length !== 1 || pasteGesture.current.copied || pasteGesture.current.startY - event.touches[0].clientY < 28) return; event.preventDefault(); pasteGesture.current.copied = true; suppressPasteClick.current = true; openCopy(); }} onTouchEnd={() => { if (pasteGesture.current.copied) setTimeout(() => { suppressPasteClick.current = false; }, 0); pasteGesture.current = { startY: 0, copied: false }; }} onTouchCancel={() => { pasteGesture.current = { startY: 0, copied: false }; suppressPasteClick.current = false; }} onContextMenu={(event) => { event.preventDefault(); openCopy(); }} onKeyDown={(event) => { if (event.key === "ArrowUp") { event.preventDefault(); openCopy(); } }} aria-haspopup="dialog" aria-label="貼付。上へスワイプでコピー" title="タップ: 貼付 / 上へスワイプ: コピー" className="shrink-0 rounded-lg bg-white px-3 py-1.5 font-mono text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">貼付</button>
      <button onPointerDown={(event) => event.preventDefault()} onClick={() => sendSeq("\r")} aria-label="Enter" className="shrink-0 rounded-lg bg-white px-3 py-1.5 font-mono text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">Enter</button>
      {HELPER_KEYS.map((key) => <button key={key.label} aria-label={key.label} onPointerDown={(event) => event.preventDefault()} onClick={() => { if (key.modifier) { ctrlArmed.current = !ctrlArmed.current; setCtrlOn(ctrlArmed.current); } else if (key.seq) sendSeq(key.seq); }} className={`shrink-0 rounded-lg px-3 py-1.5 font-mono text-xs font-medium ${key.modifier && ctrlOn ? "bg-accent-600 text-white" : "bg-white text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"}`}>{key.label}</button>)}
    </div></div>
    {copyOpen && <div className="absolute inset-0 z-40 flex items-end bg-black/40" onClick={() => setCopyOpen(false)}><div role="dialog" aria-label="コピー" className="safe-bottom w-full rounded-t-2xl bg-white p-4 dark:bg-zinc-900" onClick={(event) => event.stopPropagation()}><div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-semibold">コピー</h2><button onClick={() => setCopyOpen(false)} aria-label="閉じる" className="grid min-h-11 min-w-11 place-items-center rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"><IconX /></button></div><textarea ref={copyAreaRef} readOnly rows={10} value={copyText} className="w-full resize-none rounded-xl border border-zinc-300 bg-zinc-50 p-3 font-mono text-base dark:border-zinc-700 dark:bg-zinc-950" /><p className="mt-1 text-xs text-zinc-400">選択した文字がある場合は選択範囲、それ以外は履歴全体です。</p><button onClick={() => void copyAll()} className="mt-2 min-h-11 w-full rounded-xl bg-accent-600 text-sm font-medium text-white hover:bg-accent-700">コピーする</button></div></div>}
  </div>, document.body);
}

function openCopyFromTerminal(
  terminal: Terminal | null,
  setCopyText: (value: string) => void,
  setCopyOpen: (value: boolean) => void,
) {
  const selected = terminal?.getSelection();
  if (selected?.trim()) setCopyText(selected);
  else {
    const buffer = terminal?.buffer.active;
    const lines: string[] = [];
    for (let index = 0; index < (buffer?.length ?? 0); index += 1) {
      const line = buffer!.getLine(index);
      const text = line?.translateToString(true) ?? "";
      if (line?.isWrapped && lines.length) lines[lines.length - 1] += text;
      else lines.push(text);
    }
    setCopyText(lines.join("\n").replace(/\n+$/, ""));
  }
  setCopyOpen(true);
}
