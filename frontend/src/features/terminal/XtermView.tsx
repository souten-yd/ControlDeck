/** xterm.js ターミナルビュー（遅延ロードチャンク）。
 * モバイル: visualViewport で高さ再計算 + Ctrl/Esc/Tab/矢印の補助キーバー。
 * コピペ: iOS では xterm 上の長押し選択が効かないため、貼付/コピーのシートで対応。
 * 非 HTTPS（Tailscale IP 直アクセス等）では Clipboard API が無いので手動フォールバック。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { wsUrl } from "../../api/client";
import { useToasts } from "../../stores";
import { IconX } from "../../components/icons";
import { TerminalGeometryController } from "./controllers/TerminalGeometryController";
import { TerminalDiagnostics } from "./controllers/TerminalDiagnostics";
import { TerminalConnectionController } from "./controllers/TerminalConnectionController";
import { TerminalImeController } from "./controllers/TerminalImeController";
import { TerminalResizeBarrier, type TerminalResizeAck } from "./controllers/TerminalResizeBarrier";
import { TerminalWriteQueue } from "./controllers/TerminalWriteQueue";

interface SessionInfo {
  id: string;
  name: string;
}

const HELPER_KEYS: { label: string; seq?: string; modifier?: "ctrl" }[] = [
  { label: "Esc", seq: "\x1b" },
  { label: "Tab", seq: "\t" },
  { label: "Ctrl", modifier: "ctrl" },
  { label: "↑", seq: "\x1b[A" },
  { label: "↓", seq: "\x1b[B" },
  { label: "←", seq: "\x1b[D" },
  { label: "→", seq: "\x1b[C" },
  { label: "^C", seq: "\x03" },
  { label: "^D", seq: "\x04" },
  { label: "^Z", seq: "\x1a" },
  { label: "^L", seq: "\x0c" },
];

export default function XtermView({
  sessionId,
  sessions,
  onSwitch,
  onExit,
}: {
  sessionId: string;
  sessions: SessionInfo[];
  onSwitch: (id: string) => void;
  onExit: () => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const helperRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const inputSenderRef = useRef<((data: string) => void) | null>(null);
  const ctrlArmed = useRef(false);
  const pasteRef = useRef<HTMLTextAreaElement>(null);
  const copyRef = useRef<HTMLTextAreaElement>(null);
  const show = useToasts((s) => s.show);
  const [status, setStatus] = useState<"connecting" | "open" | "closed" | "gone">("connecting");
  const [ctrlOn, setCtrlOn] = useState(false);
  const [sheet, setSheet] = useState<"paste" | "copy" | null>(null);
  const [copyText, setCopyText] = useState("");

  useEffect(() => {
    const host = hostRef.current;
    const root = rootRef.current;
    const header = headerRef.current;
    const body = bodyRef.current;
    const helper = helperRef.current;
    if (!host || !root || !header || !body || !helper) return;
    const coarseMobile = window.matchMedia("(max-width: 767px) and (pointer: coarse)").matches;
    const dark = document.documentElement.classList.contains("dark");
    const term = new Terminal({
      fontSize: 13,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      cursorBlink: true,
      scrollback: 100_000,
      theme: dark
        ? { background: "#09090b", foreground: "#e4e4e7" }
        : { background: "#ffffff", foreground: "#18181b", cursor: "#18181b" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    termRef.current = term;
    const updateViewportMarker = () => {
      host.dataset.terminalViewportY = String(term.buffer.active.viewportY);
    };
    updateViewportMarker();
    const scrollDisposable = term.onScroll(updateViewportMarker);

    const encoder = new TextEncoder();
    const geometryDebug = window.localStorage.getItem("control-deck:terminal-geometry-debug") === "1";
    const writeQueue = new TerminalWriteQueue(term, geometryDebug);
    // セッションは tmux でサーバー側に永続。WS が切れても明示的に閉じるまで自動再接続する
    let disposed = false;
    let retryTimer: number | undefined;
    let retryDelay = 500;
    let geometryController: TerminalGeometryController | null = null;
    let diagnostics: TerminalDiagnostics;
    let resizeBarrier: TerminalResizeBarrier;
    let connectionController: TerminalConnectionController;
    let connectionGeneration = 0;
    let resizeGeneration = 0;
    let everConnected = false;
    let pendingOutputSequence: number | null = null;
    const clientInstanceId = crypto.randomUUID().replaceAll("-", "");
    let lastPtySize: { cols: number; rows: number } | null = null;
    const sendNow = (data: string): void => {
      const ws = wsRef.current;
      if (ws?.readyState !== WebSocket.OPEN) return;
      ws.send(encoder.encode(data));
      diagnostics?.record("input-sent", {
        connectionGeneration,
        length: data.length,
      });
    };
    const notifyBackendTerminalSize = (cols: number, rows: number, createBarrier: boolean): number | null => {
      if (cols < 10 || rows < 3) return null;
      if (lastPtySize?.cols === cols && lastPtySize.rows === rows) return null;
      const ws = wsRef.current;
      if (ws?.readyState !== WebSocket.OPEN) return null;
      const nextResizeGeneration = ++resizeGeneration;
      if (createBarrier
        && !resizeBarrier.startResize(nextResizeGeneration, connectionGeneration, cols, rows)) return null;
      try {
        ws.send(JSON.stringify({
          type: "resize",
          rows,
          cols,
          resizeGeneration: nextResizeGeneration,
          connectionGeneration,
          debug: geometryDebug,
        }));
      } catch (error) {
        if (createBarrier) resizeBarrier.abort("resize-send-failed");
        diagnostics?.record("resize-send-failed", { nextResizeGeneration, connectionGeneration });
        console.error("[terminal-resize] send failed", error);
        return null;
      }
      lastPtySize = { cols, rows };
      diagnostics?.record("resize-sent", {
        resizeGeneration: nextResizeGeneration,
        connectionGeneration,
        cols,
        rows,
        createBarrier,
      });
      return nextResizeGeneration;
    };

    const connect = () => {
      if (disposed) return;
      const current = wsRef.current;
      if (current?.readyState === WebSocket.OPEN || current?.readyState === WebSocket.CONNECTING) return;
      window.clearTimeout(retryTimer);
      connectionGeneration += 1;
      const thisConnectionGeneration = connectionGeneration;
      const attachMode = everConnected ? "resume" : "initial";
      resizeBarrier.resetConnection(thisConnectionGeneration);
      connectionController.begin(thisConnectionGeneration, attachMode);
      setStatus("connecting");
      const query = new URLSearchParams({
        rows: String(term.rows),
        cols: String(term.cols),
        clientInstanceId,
        connectionGeneration: String(thisConnectionGeneration),
        attachMode,
        lastSequence: String(connectionController.getLastSequence()),
      });
      const ws = new WebSocket(
        wsUrl(`/terminals/${sessionId}/connect?${query.toString()}`),
      );
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        if (ws !== wsRef.current || thisConnectionGeneration !== connectionGeneration) return;
        if (!connectionController.opened(thisConnectionGeneration, attachMode)) return;
        everConnected = true;
        retryDelay = 500;
        lastPtySize = null;
        setStatus("open");
        geometryController?.onConnectionOpen();
      };
      ws.onmessage = (ev) => {
        if (ws !== wsRef.current || thisConnectionGeneration !== connectionGeneration) return;
        if (typeof ev.data === "string") {
          try {
            const control = JSON.parse(ev.data);
            if (control.type === "resize_ack") {
              diagnostics.record("resize-ack-received", control);
              resizeBarrier.handleAck(control as TerminalResizeAck);
              return;
            }
            if (control.type === "size_probe_result") {
              diagnostics.record("size-probe-result", control);
              return;
            }
            if (control.type === "output") {
              if (control.connectionGeneration === thisConnectionGeneration
                && Number.isSafeInteger(control.sequence)) pendingOutputSequence = control.sequence;
              return;
            }
            if (control.type === "history_reset") {
              if (!connectionController.historyReset(control.connectionGeneration ?? thisConnectionGeneration)) return;
              writeQueue.enqueueReset();
              return;
            }
            if (control.type === "history_end") {
              writeQueue.enqueueTask(() => {
                connectionController.markLive(
                  control.connectionGeneration ?? thisConnectionGeneration,
                  Number(control.sequence ?? 0),
                  "history-end-received",
                );
                term.focus();
              }, "history-end");
              return;
            }
            if (control.type === "resume_ready") {
              connectionController.resumeReady(control.connectionGeneration);
              return;
            }
            if (control.type === "resume_reset_required") {
              connectionController.resumeResetRequired(control.connectionGeneration);
              return;
            }
            if (control.type === "resume_end") {
              writeQueue.enqueueTask(() => {
                connectionController.markLive(
                  control.connectionGeneration,
                  Number(control.sequence ?? 0),
                  "resume-end-received",
                );
                term.focus();
              }, "resume-end");
              return;
            }
          } catch {
            // 旧backend等の通常文字列はそのまま表示する。
          }
          const token = resizeBarrier.captureFrameAfterAck();
          diagnostics.recordPty("pty-frame-received", ev.data, { connectionGeneration, token });
          writeQueue.enqueueWrite(ev.data, () => {
            diagnostics.record("pty-write-complete", { connectionGeneration, token });
            if (token) resizeBarrier.completePtyFrame(token);
          });
          return;
        }
        const data = new Uint8Array(ev.data);
        const sequence = pendingOutputSequence;
        pendingOutputSequence = null;
        connectionController.historyFrame(data.byteLength);
        const token = resizeBarrier.captureFrameAfterAck();
        diagnostics.recordPty("pty-frame-received", data, { connectionGeneration, token });
        writeQueue.enqueueWrite(data, () => {
          diagnostics.record("pty-write-complete", { connectionGeneration, token });
          if (sequence !== null) connectionController.outputDrawn(thisConnectionGeneration, sequence);
          if (token) resizeBarrier.completePtyFrame(token);
        });
      };
      ws.onclose = (ev) => {
        if (ws !== wsRef.current || thisConnectionGeneration !== connectionGeneration) return;
        if (!connectionController.closed(thisConnectionGeneration, ev)) return;
        resizeBarrier.resetConnection(connectionGeneration);
        if (disposed) return;
        if (ev.code === 4404) {
          // セッション自体が存在しない（終了済み）→ 再接続しない
          setStatus("gone");
          writeQueue.enqueueWrite("\r\n\x1b[90m[セッションが終了しました]\x1b[0m\r\n");
          return;
        }
        setStatus("closed");
        connectionController.reconnectScheduled(retryDelay);
        retryTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 5000);
      };
      ws.onerror = () => connectionController.error(thisConnectionGeneration);
    };

    const send = (data: string) => {
      connectionController.sendOrQueue(data);
    };
    inputSenderRef.current = send;
    const dataDisposable = term.onData((data) => {
      if (ctrlArmed.current && data.length === 1 && /[a-z]/i.test(data)) {
        ctrlArmed.current = false;
        setCtrlOn(false);
        send(String.fromCharCode(data.toLowerCase().charCodeAt(0) - 96));
        return;
      }
      send(data);
    });
    const resumeConnection = () => {
      const ws = wsRef.current;
      if (ws && ws.readyState !== WebSocket.OPEN && ws.readyState !== WebSocket.CONNECTING) {
        window.clearTimeout(retryTimer);
        retryDelay = 500;
        connect();
      }
    };

    const bodyStyle = document.body.getAttribute("style");
    const htmlStyle = document.documentElement.getAttribute("style");
    if (coarseMobile) {
      // body位置の固定はbrowserのkeyboard自動panと二重になり欠落を生む。
      // layout位置はbrowserに任せ、背景pageのscrollだけを止める。
      document.body.style.overflow = "hidden";
      document.body.style.overscrollBehavior = "none";
      document.documentElement.style.overflow = "hidden";
      document.documentElement.style.overscrollBehavior = "none";
    }
    const imeController = new TerminalImeController({
      host,
      terminal: term,
      debug: geometryDebug,
      collectGeometryDebug: () => geometryController?.getDebugState() ?? {},
      // composition終了後もtextareaのstyle/focusへ触れず、保留geometryだけを確定する。
      onCompositionSettled: () => geometryController?.flushAfterComposition(),
    });
    diagnostics = new TerminalDiagnostics({
      terminal: term,
      host,
      root,
      helper,
      enabled: geometryDebug,
      isComposing: imeController.isComposing,
    });
    connectionController = new TerminalConnectionController({
      debug: geometryDebug,
      sendNow: (data) => resizeBarrier.sendOrQueue(data),
      snapshot: () => ({
        documentVisibility: document.visibilityState,
        visualViewportWidth: window.visualViewport?.width,
        visualViewportHeight: window.visualViewport?.height,
        rows: term.rows,
        cols: term.cols,
      }),
    });
    resizeBarrier = new TerminalResizeBarrier({
      sendNow,
      debug: geometryDebug,
      onAckAccepted: (acceptedResizeGeneration, cols, rows) => {
        geometryController?.commitAcknowledgedResize(acceptedResizeGeneration, cols, rows);
      },
      onSettled: (settledResizeGeneration, reason) => {
        diagnostics.record("resize-transaction-settled", {
          resizeGeneration: settledResizeGeneration,
          connectionGeneration,
          reason,
        });
        if (reason === "timeout-before-ack" || reason === "ack-failure") lastPtySize = null;
        const ws = wsRef.current;
        if (geometryDebug && ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "size_probe",
            resizeGeneration: settledResizeGeneration,
            connectionGeneration,
          }));
        }
        geometryController?.onResizeTransactionSettled(settledResizeGeneration, reason);
      },
    });
    geometryController = new TerminalGeometryController({
      root,
      header,
      body,
      host,
      helper,
      terminal: term,
      fitAddon: fit,
      writeQueue,
      coarseMobile,
      debug: geometryDebug,
      isGeometryLocked: imeController.isGeometryLocked,
      isResizeTransactionActive: resizeBarrier.isActive,
      sendPtyResize: notifyBackendTerminalSize,
      resumeConnection,
    });
    type TerminalTestHook = {
      invalidate: (type: "size" | "position" | "renderer" | "connection", reason: string) => void;
      counters: () => ReturnType<TerminalGeometryController["getCounters"]>;
      resetCounters: () => void;
      isGeometryLocked: () => boolean;
      textareaCount: () => number;
      rows: () => number;
      cols: () => number;
      viewportY: () => number;
      baseY: () => number;
      resizeBarrierState: () => Record<string, unknown>;
      resizeBarrierLog: () => readonly Record<string, unknown>[];
      terminalLog: () => readonly Record<string, unknown>[];
      captureRenderState: () => Record<string, unknown>;
      startBarrierForTest: (generation: number, cols: number, rows: number) => boolean;
      ackBarrierForTest: (ack: TerminalResizeAck) => boolean;
      enqueuePtyFrameForTest: (data: string) => boolean;
      sendInputForTest: (data: string) => void;
      resetBarrierForTest: () => void;
      connectionGeneration: () => number;
      connectionState: () => Record<string, unknown>;
      connectionLog: () => readonly Record<string, unknown>[];
      historyReplayCounters: () => ReturnType<TerminalConnectionController["getCounters"]>;
      closeWebSocketForTest: () => void;
      setLastSequenceForTest: (sequence: number) => void;
      controllerListenerCount: number;
    };
    const testWindow = window as typeof window & { __controlDeckTerminalTest?: TerminalTestHook };
    if (geometryDebug) {
      testWindow.__controlDeckTerminalTest = {
        invalidate: (type, reason) => geometryController?.invalidate(type, reason),
        counters: () => geometryController!.getCounters(),
        resetCounters: () => geometryController?.resetCounters(),
        isGeometryLocked: imeController.isGeometryLocked,
        textareaCount: () => imeController.getTextareaCount(),
        rows: () => term.rows,
        cols: () => term.cols,
        viewportY: () => term.buffer.active.viewportY,
        baseY: () => term.buffer.active.baseY,
        resizeBarrierState: () => resizeBarrier.getState(),
        resizeBarrierLog: () => resizeBarrier.getDebugLog(),
        terminalLog: () => diagnostics.getLog(),
        captureRenderState: () => diagnostics.captureRenderState(),
        startBarrierForTest: (generation, cols, rows) =>
          resizeBarrier.startResize(generation, connectionGeneration, cols, rows),
        ackBarrierForTest: (ack) => resizeBarrier.handleAck(ack),
        enqueuePtyFrameForTest: (data) => {
          const token = resizeBarrier.captureFrameAfterAck();
          if (!token) return false;
          writeQueue.enqueueWrite(data, () => resizeBarrier.completePtyFrame(token));
          return true;
        },
        sendInputForTest: (data) => resizeBarrier.sendOrQueue(data),
        resetBarrierForTest: () => resizeBarrier.resetConnection(connectionGeneration),
        connectionGeneration: () => connectionGeneration,
        connectionState: () => connectionController.getState(),
        connectionLog: () => connectionController.getLog(),
        historyReplayCounters: () => connectionController.getCounters(),
        closeWebSocketForTest: () => wsRef.current?.close(4001, "playwright reconnect"),
        setLastSequenceForTest: (sequence) => connectionController.setLastSequenceForTest(sequence),
        // geometry 5 + IME textarea 7 + host focusin 1。observer/touch/xterm内部は別集計。
        controllerListenerCount: 13,
      };
    }
    connect();

    // xterm.js 6の独自scrollbarはtouch dragをbuffer scrollへ変換しないため明示的に補う。
    let touchTracking = false;
    let touchScrolling = false;
    let touchStartX = 0;
    let touchStartY = 0;
    let touchLastY = 0;
    let touchRemainder = 0;
    let touchCellHeight = term.options.fontSize ?? 13;
    let touchScrollFrame = 0;
    const flushTouchScroll = () => {
      touchScrollFrame = 0;
      const lines = Math.trunc(touchRemainder);
      if (lines !== 0) {
        term.scrollLines(lines);
        touchRemainder -= lines;
      }
    };
    const onTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) {
        touchTracking = false;
        return;
      }
      // xterm 6自身の未完なtouch scroll stateと二重処理しない。tap focusはtouchendで復元する。
      event.preventDefault();
      event.stopPropagation();
      const touch = event.touches[0];
      touchTracking = true;
      touchScrolling = false;
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
      touchLastY = touch.clientY;
      touchRemainder = 0;
      const screen = host.querySelector<HTMLElement>(".xterm-screen");
      touchCellHeight = screen && term.rows > 0
        ? screen.getBoundingClientRect().height / term.rows
        : term.options.fontSize ?? 13;
      window.cancelAnimationFrame(touchScrollFrame);
      touchScrollFrame = 0;
    };
    const onTouchMove = (event: TouchEvent) => {
      if (!touchTracking || event.touches.length !== 1) return;
      event.stopPropagation();
      const touch = event.touches[0];
      if (!touchScrolling) {
        const distanceX = Math.abs(touch.clientX - touchStartX);
        const distanceY = Math.abs(touch.clientY - touchStartY);
        if (Math.max(distanceX, distanceY) < 8) return;
        if (distanceX >= distanceY) {
          touchTracking = false;
          return;
        }
        touchScrolling = true;
      }

      event.preventDefault();
      touchRemainder += (touchLastY - touch.clientY) / Math.max(touchCellHeight, 1);
      if (!touchScrollFrame) touchScrollFrame = window.requestAnimationFrame(flushTouchScroll);
      touchLastY = touch.clientY;
    };
    const onTouchEnd = (event: TouchEvent) => {
      event.stopPropagation();
      const wasScrolling = touchScrolling;
      if (touchScrolling && !touchScrollFrame) flushTouchScroll();
      touchTracking = false;
      touchScrolling = false;
      if (!wasScrolling) term.focus();
    };
    host.addEventListener("touchstart", onTouchStart, { capture: true, passive: false });
    host.addEventListener("touchmove", onTouchMove, { capture: true, passive: false });
    host.addEventListener("touchend", onTouchEnd, { capture: true, passive: true });
    host.addEventListener("touchcancel", onTouchEnd, { capture: true, passive: true });

    return () => {
      disposed = true;
      inputSenderRef.current = null;
      window.clearTimeout(retryTimer);
      geometryController?.dispose();
      imeController.dispose();
      delete testWindow.__controlDeckTerminalTest;
      window.cancelAnimationFrame(touchScrollFrame);
      host.removeEventListener("touchstart", onTouchStart, true);
      host.removeEventListener("touchmove", onTouchMove, true);
      host.removeEventListener("touchend", onTouchEnd, true);
      host.removeEventListener("touchcancel", onTouchEnd, true);
      if (coarseMobile) {
        if (bodyStyle === null) document.body.removeAttribute("style");
        else document.body.setAttribute("style", bodyStyle);
        if (htmlStyle === null) document.documentElement.removeAttribute("style");
        else document.documentElement.setAttribute("style", htmlStyle);
      }
      const ws = wsRef.current;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      }
      wsRef.current = null;
      resizeBarrier.dispose();
      dataDisposable.dispose();
      scrollDisposable.dispose();
      writeQueue.dispose();
      term.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const sendSeq = (seq: string) => {
    inputSenderRef.current?.(seq);
    termRef.current?.focus();
  };

  /** クリップボードから貼り付け。API 不可・拒否時は手動貼付シートへ。 */
  const doPaste = async () => {
    try {
      if (navigator.clipboard?.readText) {
        const text = await navigator.clipboard.readText();
        if (text) {
          termRef.current?.paste(text);
          termRef.current?.focus();
          return;
        }
      }
    } catch {
      // 権限拒否 or 非対応 → フォールバック
    }
    setSheet("paste");
  };

  /** 選択範囲があればそれを、なければスクロールバック全文をコピーシートに表示。 */
  const openCopy = () => {
    const sel = termRef.current?.getSelection();
    if (sel && sel.trim()) {
      setCopyText(sel);
    } else {
      const buf = termRef.current?.buffer.active;
      const lines: string[] = [];
      for (let i = 0; i < (buf?.length ?? 0); i++) {
        const line = buf!.getLine(i);
        const text = line?.translateToString(true) ?? "";
        if (line?.isWrapped && lines.length > 0) {
          // 画面幅によるsoft wrapを実改行としてコピーしない。
          lines[lines.length - 1] += text;
        } else {
          lines.push(text);
        }
      }
      setCopyText(lines.join("\n").replace(/\n+$/, ""));
    }
    setSheet("copy");
  };

  /** Clipboard API → execCommand の順で試す（HTTP でも動くように）。 */
  const copyAll = async () => {
    const text = copyRef.current?.value ?? copyText;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        show("コピーしました");
        setSheet(null);
        return;
      }
    } catch {
      // 非セキュアコンテキスト等 → execCommand へ
    }
    const ta = copyRef.current;
    if (ta) {
      ta.focus();
      ta.setSelectionRange(0, ta.value.length);
      if (document.execCommand("copy")) {
        show("コピーしました");
        setSheet(null);
      } else {
        show("自動コピーできません。長押しで選択してコピーしてください", "error");
      }
    }
  };

  const submitPaste = () => {
    const text = pasteRef.current?.value ?? "";
    setSheet(null);
    if (text) termRef.current?.paste(text);
    termRef.current?.focus();
  };

  // 下部ナビより手前の全画面表示（モバイルで画面全体を使う）
  return createPortal(
    <div ref={rootRef} data-terminal-root className="fixed left-0 top-0 z-40 flex h-[100dvh] w-full flex-col overflow-hidden bg-white dark:bg-zinc-950">
      {/* ヘッダー */}
      <div ref={headerRef} data-terminal-header className="safe-top flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-1.5 dark:border-zinc-800">
        <select
          value={sessionId}
          onChange={(e) => onSwitch(e.target.value)}
          aria-label="セッションを切替"
          className="min-w-0 rounded-lg border border-zinc-300 bg-white px-2 py-1 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
        >
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <span
          className={`text-xs ${
            status === "open" ? "text-emerald-600 dark:text-emerald-400" : status === "gone" ? "text-red-500" : "text-zinc-400"
          }`}
        >
          {status === "open" ? "接続中" : status === "closed" ? "再接続中..." : status === "gone" ? "終了済み" : "接続中..."}
        </span>
        <button
          onClick={openCopy}
          className="ml-auto hidden rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 md:block"
        >
          コピー
        </button>
        <button
          onClick={onExit}
          aria-label="ターミナルを閉じる"
          className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 md:ml-0"
        >
          <IconX />
        </button>
      </div>

      {/* ターミナル本体 */}
      {/* FitAddonは直接の親paddingを寸法から引かない。装飾paddingを外側へ分離し、hostは無paddingにする。 */}
      <div ref={bodyRef} data-terminal-body className="flex min-h-0 flex-1 overflow-clip bg-white px-1 pt-1 dark:bg-zinc-950">
        {/* clipは端数cellを切りつつ、IME textareaが親を自動scrollするscroll containerを作らない。 */}
        <div ref={hostRef} data-terminal-host className="terminal-xterm-host min-h-0 min-w-0 flex-1 overflow-clip" />
      </div>

      {/* モバイル補助キーバー */}
      <div
        ref={helperRef}
        data-terminal-helper
        className="terminal-helper-bar flex h-10 shrink-0 flex-nowrap gap-1 overflow-x-auto overflow-y-hidden border-t border-zinc-200 bg-zinc-50 px-2 py-1.5 dark:border-zinc-800 dark:bg-zinc-900 md:hidden"
      >
        <button
          onClick={doPaste}
          className="shrink-0 rounded-lg bg-white px-3 py-1.5 font-mono text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
        >
          貼付
        </button>
        <button
          onClick={openCopy}
          className="shrink-0 rounded-lg bg-white px-3 py-1.5 font-mono text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
        >
          コピー
        </button>
        {HELPER_KEYS.map((k) => (
          <button
            key={k.label}
            onClick={() => {
              if (k.modifier === "ctrl") {
                ctrlArmed.current = !ctrlArmed.current;
                setCtrlOn(ctrlArmed.current);
                termRef.current?.focus();
              } else if (k.seq) {
                sendSeq(k.seq);
              }
            }}
            className={`shrink-0 rounded-lg px-3 py-1.5 font-mono text-xs font-medium ${
              k.modifier === "ctrl" && ctrlOn
                ? "bg-accent-600 text-white"
                : "bg-white text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
            }`}
          >
            {k.label}
          </button>
        ))}
      </div>

      {/* 貼付/コピーシート */}
      {sheet && (
        <div className="absolute inset-0 z-10 flex items-end bg-black/40" onClick={() => setSheet(null)}>
          <div
            className="safe-bottom w-full rounded-t-2xl bg-white p-4 dark:bg-zinc-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold">
                {sheet === "paste" ? "貼り付け" : "コピー"}
              </h2>
              <button
                onClick={() => setSheet(null)}
                aria-label="閉じる"
                className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                <IconX />
              </button>
            </div>
            {sheet === "paste" ? (
              <>
                <textarea
                  ref={pasteRef}
                  autoFocus
                  rows={4}
                  placeholder="ここに長押しでペーストして「送信」"
                  className="w-full resize-none rounded-xl border border-zinc-300 bg-white p-3 font-mono text-base dark:border-zinc-700 dark:bg-zinc-950"
                />
                <button
                  onClick={submitPaste}
                  className="mt-2 w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700"
                >
                  ターミナルへ送信
                </button>
              </>
            ) : (
              <>
                <textarea
                  ref={copyRef}
                  readOnly
                  rows={10}
                  value={copyText}
                  className="w-full resize-none rounded-xl border border-zinc-300 bg-zinc-50 p-3 font-mono text-base dark:border-zinc-700 dark:bg-zinc-950"
                />
                <p className="mt-1 text-xs text-zinc-400">長押しで範囲選択してコピーもできます</p>
                <button
                  onClick={copyAll}
                  className="mt-2 w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700"
                >
                  全文コピー
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
}
