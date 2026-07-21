/** xterm.js ターミナルビュー（遅延ロードチャンク）。
 * モバイル: visualViewport で高さ再計算 + Ctrl/Esc/Tab/矢印の補助キーバー。
 * コピペ: PasteはClipboard APIから直接送信し、CopyはPasteの上スワイプで直接開く。
 * 非 HTTPSでClipboard APIを読めない場合は、二重入力欄を出さずOS keyboard pasteへ案内する。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { wsUrl } from "../../api/client";
import { createUuid } from "../../lib/clientId";
import { useToasts } from "../../stores";
import { IconSettings, IconX } from "../../components/icons";
import { TerminalGeometryController } from "./controllers/TerminalGeometryController";
import { TerminalDiagnostics } from "./controllers/TerminalDiagnostics";
import { TerminalConnectionController } from "./controllers/TerminalConnectionController";
import { TerminalImeController } from "./controllers/TerminalImeController";
import {
  prepareTerminalPaste, TerminalInputController, type InputAck, type InputError, type PasteProgress,
} from "./controllers/TerminalInputController";
import { TerminalResizeBarrier, type TerminalResizeAck } from "./controllers/TerminalResizeBarrier";
import { TerminalWriteQueue } from "./controllers/TerminalWriteQueue";

interface SessionInfo {
  id: string;
  name: string;
  program?: string;
  cwd?: string;
  workload?: "idle" | "running";
  alive?: boolean;
  persistent?: boolean;
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
  onAutomation,
  onExit,
}: {
  sessionId: string;
  sessions: SessionInfo[];
  onSwitch: (id: string) => void;
  onAutomation?: () => void;
  onExit: () => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const helperRef = useRef<HTMLDivElement>(null);
  const historyTrackRef = useRef<HTMLDivElement>(null);
  const historyThumbRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const inputSenderRef = useRef<((data: string) => void) | null>(null);
  const pasteSenderRef = useRef<((text: string) => void) | null>(null);
  const pasteCancelRef = useRef<(() => void) | null>(null);
  const pasteRetryRef = useRef<(() => void) | null>(null);
  const ctrlArmed = useRef(false);
  const pasteGestureRef = useRef({ startY: 0, triggered: false });
  const suppressPasteClickRef = useRef(false);
  const copyRef = useRef<HTMLTextAreaElement>(null);
  const show = useToasts((s) => s.show);
  const [status, setStatus] = useState<"connecting" | "open" | "closed" | "gone">("connecting");
  const [ctrlOn, setCtrlOn] = useState(false);
  const [pasteProgress, setPasteProgress] = useState<PasteProgress | null>(null);
  const [replaying, setReplaying] = useState(true);
  const [copySheet, setCopySheet] = useState(false);
  const [copyText, setCopyText] = useState("");
  const currentSession = sessions.find((session) => session.id === sessionId);

  useEffect(() => {
    const host = hostRef.current;
    const root = rootRef.current;
    const header = headerRef.current;
    const body = bodyRef.current;
    const helper = helperRef.current;
    const historyTrack = historyTrackRef.current;
    const historyThumb = historyThumbRef.current;
    if (!host || !root || !header || !body || !helper || !historyTrack || !historyThumb) return;
    const coarseMobile = window.matchMedia("(max-width: 767px) and (pointer: coarse)").matches;
    if (coarseMobile) {
      // 最初のfitより前にVisual Viewportへ合わせる。接続表示後にroot寸法が変わり、
      // tmux resize/redrawが露出するraceを避ける。
      const viewport = window.visualViewport;
      root.style.width = `${Math.min(viewport?.width ?? window.innerWidth, document.documentElement.clientWidth || window.innerWidth)}px`;
      root.style.height = `${viewport?.height ?? window.innerHeight}px`;
    }
    const dark = document.documentElement.classList.contains("dark");
    const term = new Terminal({
      fontSize: 13,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      cursorBlink: true,
      scrollback: 100_000,
      // FitAddonはscrollback有効時に常に14pxを予約する。モバイルは独自overlay barを
      // 使うため1pxだけ予約し、右端の文字列へ利用可能な幅を戻す。
      ...(coarseMobile ? { overviewRuler: { width: 1 } } : {}),
      theme: dark
        ? { background: "#09090b", foreground: "#e4e4e7" }
        : { background: "#ffffff", foreground: "#18181b", cursor: "#18181b" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    termRef.current = term;
    let historyTrackHeight = historyTrack.clientHeight;
    let historyMarkerFrame = 0;
    const updateHistoryTrack = (remeasure = false) => {
      const buffer = term.buffer.active;
      if (remeasure) historyTrackHeight = historyTrack.clientHeight;
      const trackHeight = historyTrackHeight;
      const historyRows = buffer.baseY;
      const totalRows = historyRows + term.rows;
      historyTrack.setAttribute("aria-valuemax", String(historyRows));
      historyTrack.setAttribute("aria-valuenow", String(buffer.viewportY));
      if (!coarseMobile || historyRows <= 0 || trackHeight <= 0) {
        historyTrack.style.opacity = "0";
        historyTrack.style.pointerEvents = "none";
        return;
      }
      const thumbHeight = Math.min(trackHeight, Math.max(44, trackHeight * term.rows / totalRows));
      const travel = Math.max(0, trackHeight - thumbHeight);
      const top = historyRows > 0 ? travel * buffer.viewportY / historyRows : 0;
      historyTrack.style.opacity = "1";
      historyTrack.style.pointerEvents = "auto";
      historyThumb.style.height = `${thumbHeight}px`;
      historyThumb.style.transform = `translateY(${top}px)`;
    };
    const updateViewportMarker = () => {
      historyMarkerFrame = 0;
      host.dataset.terminalViewportY = String(term.buffer.active.viewportY);
      updateHistoryTrack();
    };
    const scheduleViewportMarker = () => {
      if (!historyMarkerFrame) historyMarkerFrame = window.requestAnimationFrame(updateViewportMarker);
    };
    updateHistoryTrack(true);
    updateViewportMarker();
    const scrollDisposable = term.onScroll(scheduleViewportMarker);
    const historyResizeDisposable = term.onResize(() => updateHistoryTrack(true));
    const historyTrackObserver = new ResizeObserver(() => updateHistoryTrack(true));
    historyTrackObserver.observe(historyTrack);

    const encoder = new TextEncoder();
    const geometryDebug = window.localStorage.getItem("control-deck:terminal-geometry-debug") === "1";
    const writeQueue = new TerminalWriteQueue(term, geometryDebug);
    const presentReplay = (active: boolean) => {
      // visibility:hidden はbrowserのxterm renderer paintも止めるため、再表示の
      // 最初の1 frameに未完成の行が露出する。レイアウトとpaintは継続し、
      // 不透明のreplay overlayの下で安定させる。
      root.dataset.terminalReplaying = active ? "true" : "false";
      host.style.opacity = active ? "0" : "1";
      host.style.pointerEvents = active ? "none" : "auto";
      setReplaying(active);
    };
    const settleCompletedReplay = async () => {
      term.scrollToBottom();
      // terminal.write callbackはparser完了でありDOM renderer完了ではない。
      // rAFだけではxtermのrender callbackより先に安定判定するraceが
      // あるため、公開onRenderを完了境界として待つ。
      await new Promise<void>((resolve) => {
        let completed = false;
        const finish = () => {
          if (completed) return;
          completed = true;
          renderDisposable.dispose();
          resolve();
        };
        const renderDisposable = term.onRender(finish);
        term.refresh(0, Math.max(0, term.rows - 1));
        // WebGL/DOM rendererがrefresh不要と判定してeventを出さない場合も
        // 固定時間は足さず、2 paintを上限に先へ進む。
        window.requestAnimationFrame(() => window.requestAnimationFrame(finish));
      });
      let previous = "";
      let stableFrames = 0;
      for (let frame = 0; frame < 16 && stableFrames < 2; frame += 1) {
        await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
        const buffer = term.buffer.active;
        const normalize = (value: string) => value.replace(/\u00a0/g, " ").trimEnd();
        const bufferRows = Array.from({ length: term.rows }, (_, row) =>
          normalize(buffer.getLine(buffer.viewportY + row)?.translateToString(true) ?? ""));
        const domRows = [...host.querySelectorAll<HTMLElement>(".xterm-rows > div")]
          .map((row) => normalize(row.textContent ?? ""));
        const aligned = domRows.length === bufferRows.length
          && domRows.every((row, index) => row === bufferRows[index]);
        const signature = JSON.stringify(domRows);
        if (aligned && signature === previous) {
          stableFrames += 1;
        } else {
          stableFrames = 0;
          if (!aligned) term.refresh(0, Math.max(0, term.rows - 1));
        }
        previous = signature;
      }
    };
    // セッションは tmux でサーバー側に永続。WS が切れても明示的に閉じるまで自動再接続する
    let disposed = false;
    let retryTimer: number | undefined;
    let retryDelay = 500;
    let geometryController: TerminalGeometryController | null = null;
    let diagnostics: TerminalDiagnostics;
    let resizeBarrier: TerminalResizeBarrier;
    let connectionController: TerminalConnectionController;
    let inputController: TerminalInputController;
    let connectionGeneration = 0;
    let resizeGeneration = 0;
    let everConnected = false;
    let pendingOutputSequence: number | null = null;
    let serverHistoryActive = false;
    let replayFinalizeToken = 0;
    let presentationGeneration = 0;
    let pendingPresentationSync: {
      connectionGeneration: number;
      presentationGeneration: number;
      resolve: (ack: { throughSequence: number; observedOutput: boolean } | null) => void;
      timeout: number;
    } | null = null;
    const outputSequenceWaiters = new Set<{
      connectionGeneration: number;
      sequence: number;
      resolve: (drawn: boolean) => void;
    }>();
    // effectごとに1回だけ生成し、同一mount内の再接続では同じIDを維持する。
    const clientInstanceId = createUuid();
    let lastPtySize: { cols: number; rows: number } | null = null;
    let progressTimer: number | undefined;
    let pendingProgress: PasteProgress | null = null;
    let lastProgressUpdate = 0;
    const reportPasteProgress = (progress: PasteProgress | null) => {
      pendingProgress = progress;
      const now = performance.now();
      const immediate = !progress || progress.state === "completed" || progress.state === "failed"
        || progress.state === "cancelled" || now - lastProgressUpdate >= 100;
      const flush = () => {
        progressTimer = undefined;
        lastProgressUpdate = performance.now();
        setPasteProgress(pendingProgress);
      };
      if (immediate) {
        window.clearTimeout(progressTimer);
        flush();
      } else if (progressTimer === undefined) {
        progressTimer = window.setTimeout(flush, Math.max(0, 100 - (now - lastProgressUpdate)));
      }
    };
    const resolveOutputSequenceWaiters = (activeGeneration: number, sequence: number) => {
      for (const waiter of [...outputSequenceWaiters]) {
        if (waiter.connectionGeneration !== activeGeneration || sequence < waiter.sequence) continue;
        outputSequenceWaiters.delete(waiter);
        waiter.resolve(true);
      }
    };
    const cancelPresentationWaiters = () => {
      if (pendingPresentationSync) {
        window.clearTimeout(pendingPresentationSync.timeout);
        pendingPresentationSync.resolve(null);
        pendingPresentationSync = null;
      }
      for (const waiter of outputSequenceWaiters) waiter.resolve(false);
      outputSequenceWaiters.clear();
    };
    const waitUntilOutputDrawn = (targetGeneration: number, sequence: number): Promise<boolean> => {
      if (targetGeneration !== connectionGeneration) return Promise.resolve(false);
      if (connectionController.getLastSequence() >= sequence) return Promise.resolve(true);
      return new Promise<boolean>((resolve) => {
        outputSequenceWaiters.add({ connectionGeneration: targetGeneration, sequence, resolve });
      });
    };
    const requestPresentationSync = (
      ws: WebSocket,
      targetGeneration: number,
      chunks: readonly Uint8Array[],
    ): Promise<{ throughSequence: number; observedOutput: boolean } | null> => {
      if (chunks.length === 0 || ws.readyState !== WebSocket.OPEN
        || targetGeneration !== connectionGeneration || pendingPresentationSync) return Promise.resolve(null);
      const nextPresentationGeneration = ++presentationGeneration;
      const inputBytes = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
      const afterSequence = connectionController.getLastSequence();
      return new Promise((resolve) => {
        const timeout = window.setTimeout(() => {
          if (pendingPresentationSync?.presentationGeneration !== nextPresentationGeneration) return;
          pendingPresentationSync = null;
          resolve(null);
          // An incomplete presentation must never be exposed. Reconnecting
          // obtains a fresh snapshot and a new deterministic boundary.
          ws.close(4500, "presentation sync timeout");
        }, 2000);
        pendingPresentationSync = {
          connectionGeneration: targetGeneration,
          presentationGeneration: nextPresentationGeneration,
          resolve,
          timeout,
        };
        ws.send(JSON.stringify({
          type: "presentation_input_start",
          presentationGeneration: nextPresentationGeneration,
          inputChunks: chunks.length,
          inputBytes,
          afterSequence,
          connectionGeneration: targetGeneration,
        }));
        for (const chunk of chunks) ws.send(chunk);
        ws.send(JSON.stringify({
          type: "presentation_sync",
          presentationGeneration: nextPresentationGeneration,
          connectionGeneration: targetGeneration,
        }));
        diagnostics?.record("presentation-sync-sent", {
          connectionGeneration: targetGeneration,
          presentationGeneration: nextPresentationGeneration,
          inputChunks: chunks.length,
          inputBytes,
          afterSequence,
        });
      });
    };
    const finalizeReplay = async (
      targetGeneration: number,
      sequence: number,
      event: "history-end-received" | "resume-end-received",
    ) => {
      const token = ++replayFinalizeToken;
      // history_end以前のframeはWebSocket順序どおりenqueue済み。一度だけpaintへ
      // 譲って同一batch末尾を取り込み、queueをdrainする。旧90〜600ms idle待ちは
      // 入力可能化を不必要に遅らせていた。
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
      await writeQueue.drain();
      if (disposed || token !== replayFinalizeToken || targetGeneration !== connectionGeneration) return;
      await settleCompletedReplay();
      if (disposed || token !== replayFinalizeToken || targetGeneration !== connectionGeneration) return;

      // xterm parserはDA/DSR等への端末応答をonDataへ返す。replay中に溜まった
      // 応答を先にoverlay下で送り、その結果のPTY redrawをsequence境界まで
      // 描画する。応答が連鎖した場合もqueueが空になるまでroundを繰り返す。
      for (let round = 0; round < 16; round += 1) {
        const input = connectionController.takePresentationInput(targetGeneration);
        if (input === null) return;
        if (input.length === 0) break;
        const encoded: Uint8Array[] = [];
        for (const value of input) {
          const bytes = encoder.encode(value);
          for (let offset = 0; offset < bytes.byteLength; offset += 16 * 1024) {
            encoded.push(bytes.slice(offset, offset + 16 * 1024));
          }
        }
        for (let offset = 0; offset < encoded.length; ) {
          const batch: Uint8Array[] = [];
          let batchBytes = 0;
          while (offset < encoded.length && batch.length < 64
            && batchBytes + encoded[offset].byteLength <= 64 * 1024) {
            batch.push(encoded[offset]);
            batchBytes += encoded[offset].byteLength;
            offset += 1;
          }
          const ws = wsRef.current;
          if (!ws || ws.readyState !== WebSocket.OPEN) return;
          const ack = await requestPresentationSync(ws, targetGeneration, batch);
          if (!ack || disposed || token !== replayFinalizeToken
            || targetGeneration !== connectionGeneration) return;
          if (!await waitUntilOutputDrawn(targetGeneration, ack.throughSequence)) return;
          await writeQueue.drain();
          await settleCompletedReplay();
          if (disposed || token !== replayFinalizeToken || targetGeneration !== connectionGeneration) return;
        }
      }
      const finalSequence = Math.max(sequence, connectionController.getLastSequence());
      if (!connectionController.markLive(targetGeneration, finalSequence, event)) return;
      // LIVE判定とrenderer公開を同一taskで確定し、中間状態を作らない。
      presentReplay(false);
      inputController.availabilityChanged();
      // mobileで接続完了時に自動focusするとsoftware keyboardがVisual Viewportを
      // resizeし、直後のtmux再描画を可視化する。入力はterminal tapで明示開始する。
      if (!coarseMobile) term.focus();
    };
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
      presentationGeneration = 0;
      const thisConnectionGeneration = connectionGeneration;
      const attachMode = everConnected ? "resume" : "initial";
      resizeBarrier.resetConnection(thisConnectionGeneration);
      connectionController.begin(thisConnectionGeneration, attachMode);
      inputController.connectionChanged();
      setStatus("connecting");
      const query = new URLSearchParams({
        rows: String(term.rows),
        cols: String(term.cols),
        clientInstanceId,
        connectionGeneration: String(thisConnectionGeneration),
        attachMode,
        lastSequence: String(connectionController.getLastSequence()),
        engine: "v1",
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
            if (control.type === "input_ack") {
              inputController.handleAck(control as InputAck);
              return;
            }
            if (control.type === "input_error") {
              inputController.handleError(control as InputError);
              return;
            }
            if (control.type === "resize_ack") {
              diagnostics.record("resize-ack-received", control);
              resizeBarrier.handleAck(control as TerminalResizeAck);
              return;
            }
            if (control.type === "size_probe_result") {
              diagnostics.record("size-probe-result", control);
              return;
            }
            if (control.type === "history_scroll_ack") {
              diagnostics.record("history-scroll-ack", control);
              return;
            }
            if (control.type === "presentation_sync_ack") {
              const pending = pendingPresentationSync;
              if (!pending
                || control.connectionGeneration !== pending.connectionGeneration
                || control.presentationGeneration !== pending.presentationGeneration
                || !Number.isSafeInteger(control.throughSequence)) return;
              window.clearTimeout(pending.timeout);
              pendingPresentationSync = null;
              diagnostics.record("presentation-sync-ack-received", control);
              pending.resolve({
                throughSequence: Number(control.throughSequence),
                observedOutput: control.observedOutput === true,
              });
              return;
            }
            if (control.type === "output") {
              if (control.connectionGeneration === thisConnectionGeneration
                && Number.isSafeInteger(control.sequence)) pendingOutputSequence = control.sequence;
              return;
            }
            if (control.type === "history_reset") {
              if (!connectionController.historyReset(control.connectionGeneration ?? thisConnectionGeneration)) return;
              replayFinalizeToken += 1;
              presentReplay(true);
              writeQueue.enqueueReset();
              return;
            }
            if (control.type === "history_end") {
              void finalizeReplay(
                control.connectionGeneration ?? thisConnectionGeneration,
                Number(control.sequence ?? 0),
                "history-end-received",
              );
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
              void finalizeReplay(
                control.connectionGeneration,
                Number(control.sequence ?? 0),
                "resume-end-received",
              );
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
          if (sequence !== null) {
            connectionController.outputDrawn(thisConnectionGeneration, sequence);
            resolveOutputSequenceWaiters(thisConnectionGeneration, sequence);
          }
          if (token) resizeBarrier.completePtyFrame(token);
        });
      };
      ws.onclose = (ev) => {
        if (ws !== wsRef.current || thisConnectionGeneration !== connectionGeneration) return;
        if (!connectionController.closed(thisConnectionGeneration, ev)) return;
        cancelPresentationWaiters();
        inputController.connectionChanged();
        serverHistoryActive = false;
        replayFinalizeToken += 1;
        resizeBarrier.resetConnection(connectionGeneration);
        if (disposed) return;
        if (ev.code === 4404) {
          // セッション自体が存在しない（終了済み）→ 再接続しない
          setStatus("gone");
          writeQueue.enqueueWrite("\r\n\x1b[90m[セッションが終了しました]\x1b[0m\r\n");
          return;
        }
        // 切断からhistory_reset到着までにもcursor/rendererがDOMを更新し得る。
        // 再接続時の追従描画を見せないため、切断を検知した時点で同期的に隠す。
        presentReplay(true);
        setStatus("closed");
        connectionController.reconnectScheduled(retryDelay);
        retryTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 5000);
      };
      ws.onerror = () => connectionController.error(thisConnectionGeneration);
    };

    const exitServerHistory = () => {
      if (!serverHistoryActive) return;
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: "history_exit",
          connectionGeneration,
        }));
      }
      serverHistoryActive = false;
    };
    const leaveHistoryForInput = () => {
      exitServerHistory();
      // scrollToBottomは100,000行scrollbackの再描画を伴う。通常の文字入力や
      // Backspaceごとに実行するとmobile UIを著しく遅くするため、
      // 実際に履歴中の場合だけ一度戻す。
      if (term.buffer.active.viewportY < term.buffer.active.baseY) term.scrollToBottom();
    };
    const send = (data: string) => {
      leaveHistoryForInput();
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
      requeue: (data) => connectionController.sendOrQueue(data),
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
        inputController.availabilityChanged();
      },
    });
    inputController = new TerminalInputController({
      canSend: () => connectionController.getState().state === "LIVE"
        && !resizeBarrier.isActive() && wsRef.current?.readyState === WebSocket.OPEN,
      connectionGeneration: () => connectionGeneration,
      bufferedAmount: () => wsRef.current?.bufferedAmount ?? Number.POSITIVE_INFINITY,
      sendFrame: (control, bytes) => {
        const ws = wsRef.current;
        if (ws?.readyState !== WebSocket.OPEN) return false;
        try {
          ws.send(JSON.stringify(control));
          ws.send(bytes);
          diagnostics.record("paste-frame-sent", {
            pasteId: control.pasteId, chunkIndex: control.chunkIndex,
            inputSequence: control.inputSequence, byteLength: bytes.byteLength,
            connectionGeneration, bufferedAmount: ws.bufferedAmount,
          });
          return true;
        } catch {
          diagnostics.record("paste-frame-send-failed", { connectionGeneration });
          ws.close(1011, "paste send failed");
          return false;
        }
      },
      onProgress: reportPasteProgress,
      record: (event, details) => diagnostics.record(event, details),
    });
    const enqueuePaste = (text: string) => {
      leaveHistoryForInput();
      const normalized = prepareTerminalPaste(text, term.modes.bracketedPasteMode);
      inputController.enqueuePaste(text, normalized);
    };
    pasteSenderRef.current = enqueuePaste;
    pasteCancelRef.current = () => inputController.cancelCurrent();
    pasteRetryRef.current = () => inputController.retryCurrent();
    const onPaste = (event: ClipboardEvent) => {
      const text = event.clipboardData?.getData("text/plain");
      if (text === undefined) return;
      event.preventDefault();
      event.stopPropagation();
      enqueuePaste(text);
    };
    host.addEventListener("paste", onPaste, true);

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
      onLocalResizeCommitted: (generation) => {
        resizeBarrier.localResizeCommitted(generation);
      },
      resumeConnection,
    });
    type TerminalTestHook = {
      invalidate: (type: "size" | "position" | "renderer" | "connection", reason: string) => void;
      counters: () => ReturnType<TerminalGeometryController["getCounters"]>;
      resetCounters: () => void;
      isGeometryLocked: () => boolean;
      isReplaying: () => boolean;
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
      commitBarrierForTest: (generation: number) => boolean;
      enqueuePtyFrameForTest: (data: string) => boolean;
      writeForTest: (data: string) => Promise<void>;
      sendInputForTest: (data: string) => void;
      enqueuePasteForTest: (text: string) => void;
      pasteState: () => Record<string, unknown>;
      resetBarrierForTest: () => void;
      connectionGeneration: () => number;
      clientInstanceId: () => string;
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
        isReplaying: () => root.dataset.terminalReplaying === "true",
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
        commitBarrierForTest: (generation) => resizeBarrier.localResizeCommitted(generation),
        enqueuePtyFrameForTest: (data) => {
          const token = resizeBarrier.captureFrameAfterAck();
          if (!token) return false;
          writeQueue.enqueueWrite(data, () => resizeBarrier.completePtyFrame(token));
          return true;
        },
        writeForTest: (data) => new Promise<void>((resolve) => term.write(data, resolve)),
        sendInputForTest: (data) => resizeBarrier.sendOrQueue(data),
        enqueuePasteForTest: enqueuePaste,
        pasteState: () => inputController.getState(),
        resetBarrierForTest: () => resizeBarrier.resetConnection(connectionGeneration),
        connectionGeneration: () => connectionGeneration,
        clientInstanceId: () => clientInstanceId,
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
        // replay済みnormal bufferを右端barと同じlocal pathで即時移動する。
        // tmux subprocessとWebSocket往復はgestureのhot pathへ入れない。
        term.scrollLines(Math.max(-100, Math.min(100, lines)));
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
      // 1:1 cell換算は小さな画面で指の移動量に対して重く感じるため、軽い加速を加える。
      touchRemainder += 1.35 * (touchLastY - touch.clientY) / Math.max(touchCellHeight, 1);
      if (!touchScrollFrame) touchScrollFrame = window.requestAnimationFrame(flushTouchScroll);
      touchLastY = touch.clientY;
    };
    const onTouchEnd = (event: TouchEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const wasScrolling = touchScrolling;
      if (touchScrolling && touchScrollFrame) {
        window.cancelAnimationFrame(touchScrollFrame);
        touchScrollFrame = 0;
        flushTouchScroll();
      }
      touchTracking = false;
      touchScrolling = false;
      if (!wasScrolling) {
        leaveHistoryForInput();
        term.focus();
      }
    };
    host.addEventListener("touchstart", onTouchStart, { capture: true, passive: false });
    host.addEventListener("touchmove", onTouchMove, { capture: true, passive: false });
    host.addEventListener("touchend", onTouchEnd, { capture: true, passive: false });
    host.addEventListener("touchcancel", onTouchEnd, { capture: true, passive: false });

    const scrollHistoryToClientY = (clientY: number) => {
      const rect = historyTrack.getBoundingClientRect();
      const maxLine = term.buffer.active.baseY;
      if (rect.height <= 0 || maxLine <= 0) return;
      const ratio = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
      term.scrollToLine(Math.round(maxLine * ratio));
    };
    const onHistoryTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) return;
      event.preventDefault();
      event.stopPropagation();
      historyTrack.dataset.active = "true";
      scrollHistoryToClientY(event.touches[0].clientY);
    };
    const onHistoryTouchMove = (event: TouchEvent) => {
      if (event.touches.length !== 1 || historyTrack.dataset.active !== "true") return;
      event.preventDefault();
      event.stopPropagation();
      scrollHistoryToClientY(event.touches[0].clientY);
    };
    const onHistoryTouchEnd = (event: TouchEvent) => {
      event.preventDefault();
      event.stopPropagation();
      historyTrack.dataset.active = "false";
    };
    historyTrack.addEventListener("touchstart", onHistoryTouchStart, { passive: false });
    historyTrack.addEventListener("touchmove", onHistoryTouchMove, { passive: false });
    historyTrack.addEventListener("touchend", onHistoryTouchEnd, { passive: false });
    historyTrack.addEventListener("touchcancel", onHistoryTouchEnd, { passive: false });

    return () => {
      disposed = true;
      cancelPresentationWaiters();
      inputSenderRef.current = null;
      pasteSenderRef.current = null;
      pasteCancelRef.current = null;
      pasteRetryRef.current = null;
      host.removeEventListener("paste", onPaste, true);
      inputController.dispose();
      window.clearTimeout(retryTimer);
      window.clearTimeout(progressTimer);
      geometryController?.dispose();
      imeController.dispose();
      delete testWindow.__controlDeckTerminalTest;
      window.cancelAnimationFrame(touchScrollFrame);
      window.cancelAnimationFrame(historyMarkerFrame);
      historyTrackObserver.disconnect();
      historyResizeDisposable.dispose();
      host.removeEventListener("touchstart", onTouchStart, true);
      host.removeEventListener("touchmove", onTouchMove, true);
      host.removeEventListener("touchend", onTouchEnd, true);
      host.removeEventListener("touchcancel", onTouchEnd, true);
      historyTrack.removeEventListener("touchstart", onHistoryTouchStart);
      historyTrack.removeEventListener("touchmove", onHistoryTouchMove);
      historyTrack.removeEventListener("touchend", onHistoryTouchEnd);
      historyTrack.removeEventListener("touchcancel", onHistoryTouchEnd);
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
  };

  /** ユーザーgesture内でClipboard APIから直接送る。非secure originではOS pasteへ案内する。 */
  const doPaste = async () => {
    try {
      if (navigator.clipboard?.readText) {
        const text = await navigator.clipboard.readText();
        if (text) {
          pasteSenderRef.current?.(text);
          return;
        }
        show("クリップボードは空です");
        return;
      }
    } catch {
      // 権限拒否または非secure origin。内容をアプリ内入力欄へ二重pasteさせない。
    }
    show("この接続ではクリップボードを直接読めません。キーボードの貼り付けを使用してください", "error");
  };

  const openCopy = () => {
    const selection = termRef.current?.getSelection();
    if (selection && selection.trim()) {
      setCopyText(selection);
    } else {
      const buffer = termRef.current?.buffer.active;
      const lines: string[] = [];
      for (let index = 0; index < (buffer?.length ?? 0); index += 1) {
        const line = buffer!.getLine(index);
        const text = line?.translateToString(true) ?? "";
        if (line?.isWrapped && lines.length > 0) lines[lines.length - 1] += text;
        else lines.push(text);
      }
      setCopyText(lines.join("\n").replace(/\n+$/, ""));
    }
    setCopySheet(true);
  };

  const copyAll = async () => {
    const text = copyRef.current?.value ?? copyText;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        show("コピーしました");
        setCopySheet(false);
        return;
      }
    } catch {
      // 非secure originでは選択済みtextareaを使う。
    }
    const textarea = copyRef.current;
    if (!textarea) return;
    textarea.focus();
    textarea.setSelectionRange(0, textarea.value.length);
    if (document.execCommand("copy")) {
      show("コピーしました");
      setCopySheet(false);
    } else {
      show("自動コピーできません。選択範囲をコピーしてください", "error");
    }
  };

  const startPasteGesture = (event: React.TouchEvent<HTMLButtonElement>) => {
    if (event.touches.length !== 1) return;
    pasteGestureRef.current = { startY: event.touches[0].clientY, triggered: false };
  };

  const movePasteGesture = (event: React.TouchEvent<HTMLButtonElement>) => {
    const gesture = pasteGestureRef.current;
    if (event.touches.length !== 1 || gesture.triggered) return;
    const movedUp = gesture.startY - event.touches[0].clientY;
    if (movedUp >= 28) {
      event.preventDefault();
      gesture.triggered = true;
      suppressPasteClickRef.current = true;
      openCopy();
    }
  };

  const endPasteGesture = () => {
    if (pasteGestureRef.current.triggered) {
      window.setTimeout(() => { suppressPasteClickRef.current = false; }, 0);
    }
    pasteGestureRef.current = { startY: 0, triggered: false };
  };

  // 下部ナビより手前の全画面表示（モバイルで画面全体を使う）
  return createPortal(
    <div ref={rootRef} data-terminal-root data-terminal-replaying={replaying ? "true" : "false"} className="fixed left-0 top-0 z-40 flex h-[100dvh] w-full max-w-full flex-col overflow-hidden bg-white dark:bg-zinc-950">
      {/* ヘッダー */}
      <div ref={headerRef} data-terminal-header className="safe-top flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-1.5 dark:border-zinc-800">
        <div className="min-w-0 flex-1">
          <select
            value={sessionId}
            onChange={(e) => onSwitch(e.target.value)}
            aria-label="セッションを切替"
            className="h-11 max-w-full rounded-lg border border-zinc-300 bg-white px-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
          >
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>{s.program || s.name} · {s.cwd || `#${s.id}`}</option>
            ))}
          </select>
          <div className="mt-0.5 flex min-w-0 items-center gap-2 text-[10px]">
            <span aria-live="polite" className={`inline-flex shrink-0 items-center gap-1 ${status === "open" ? "text-emerald-600 dark:text-emerald-400" : status === "gone" ? "text-red-500" : "text-zinc-400"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${status === "open" ? "bg-emerald-500" : status === "gone" ? "bg-red-500" : "bg-amber-500 motion-safe:animate-pulse"}`} />
              {status === "open" ? "Live" : status === "closed" ? "Reconnecting" : status === "gone" ? "Exited" : "Connecting"}
            </span>
            <span className={`shrink-0 ${currentSession?.workload === "running" ? "text-blue-500" : "text-zinc-400"}`}>{currentSession?.workload === "running" ? `Foreground ${currentSession.program}` : "Shell ready"}</span>
            <code className="min-w-0 truncate text-zinc-400" title={currentSession?.cwd}>{currentSession?.cwd || "N/A"}</code>
          </div>
        </div>
        <button
          onClick={openCopy}
          className="ml-auto hidden rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 md:block"
        >
          コピー
        </button>
        {onAutomation && <button
          onPointerDown={(event) => event.preventDefault()}
          onClick={onAutomation}
          aria-label="Automation settings"
          title="Snippets and schedules"
          className="grid h-11 min-w-11 shrink-0 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <IconSettings />
        </button>}
        <button
          onClick={onExit}
          aria-label="ターミナルを閉じる"
          className="ml-auto flex h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl border border-zinc-300 bg-white px-3 text-sm font-semibold text-zinc-700 shadow-sm transition hover:border-zinc-400 hover:bg-zinc-100 focus:outline-none focus:ring-2 focus:ring-accent-500/40 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700 md:ml-0"
        >
          <IconX className="text-lg" />
          <span className="hidden sm:inline">閉じる</span>
        </button>
      </div>

      {/* ターミナル本体 */}
      {/* FitAddonは直接の親paddingを寸法から引かない。装飾paddingを外側へ分離し、hostは無paddingにする。 */}
      <div ref={bodyRef} data-terminal-body className="relative flex min-h-0 flex-1 overflow-clip bg-white px-1 pt-1 dark:bg-zinc-950">
        {/* clipは端数cellを切りつつ、IME textareaが親を自動scrollするscroll containerを作らない。 */}
        <div ref={hostRef} data-terminal-host aria-hidden={replaying} className={`terminal-xterm-host min-h-0 min-w-0 flex-1 overflow-clip ${replaying ? "opacity-0" : "opacity-100"}`} />
        {replaying && <div data-terminal-replay-overlay role="status" className="absolute inset-0 grid place-items-center bg-white text-xs text-zinc-400 dark:bg-zinc-950"><span className="inline-flex items-center gap-2"><span className="h-2 w-2 animate-pulse rounded-full bg-accent-500" />ターミナルを復元中…</span></div>}
        <div
          ref={historyTrackRef}
          data-terminal-history-track
          data-active="false"
          role="scrollbar"
          aria-label="ターミナル履歴位置"
          aria-orientation="vertical"
          aria-valuemin={0}
          aria-valuemax={0}
          aria-valuenow={0}
          className="terminal-history-track absolute inset-y-1 right-0 z-20 block w-5 opacity-0 md:hidden"
        >
          <div
            ref={historyThumbRef}
            className="terminal-history-thumb ml-auto mr-0.5 w-1 rounded-full bg-zinc-500/70 opacity-70 dark:bg-zinc-300/70"
          />
        </div>
      </div>

      {pasteProgress && pasteProgress.totalBytes >= 32 * 1024
        && pasteProgress.state !== "cancelled" && (
        <div data-terminal-paste-progress className="flex shrink-0 items-center gap-2 border-t border-zinc-200 bg-zinc-50 px-3 py-1 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
          <span className="tabular-nums">
            {pasteProgress.state === "completed" ? "貼り付け完了" : pasteProgress.state === "failed" ? "貼り付け失敗" : "貼り付け送信中"}
            {" "}{Math.ceil(pasteProgress.acknowledgedBytes / 1024)} KB / {Math.ceil(pasteProgress.totalBytes / 1024)} KB
          </span>
          {pasteProgress.state === "failed" ? (
            <button onClick={() => pasteRetryRef.current?.()} className="ml-auto rounded px-2 py-0.5 font-medium text-accent-600">再試行</button>
          ) : pasteProgress.state !== "completed" ? (
            <button onClick={() => pasteCancelRef.current?.()} className="ml-auto rounded px-2 py-0.5 font-medium text-zinc-500">キャンセル</button>
          ) : null}
        </div>
      )}

      {/* モバイル補助キーバー。CopyはPasteの上スワイプで直接開く。 */}
      <div className="relative shrink-0 md:hidden">
        <div
          ref={helperRef}
          data-terminal-helper
          className="terminal-helper-bar flex h-12 flex-nowrap gap-1 overflow-x-auto overflow-y-hidden border-t border-zinc-200 bg-zinc-50 px-2 py-0.5 dark:border-zinc-800 dark:bg-zinc-900"
        >
          <button
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => {
              if (suppressPasteClickRef.current) {
                suppressPasteClickRef.current = false;
                return;
              }
              void doPaste();
            }}
            onTouchStart={startPasteGesture}
            onTouchMove={movePasteGesture}
            onTouchEnd={endPasteGesture}
            onTouchCancel={endPasteGesture}
            onContextMenu={(event) => { event.preventDefault(); openCopy(); }}
            onKeyDown={(event) => { if (event.key === "ArrowUp") { event.preventDefault(); openCopy(); } }}
            aria-haspopup="dialog"
            aria-label="貼付。上へスワイプでコピー"
            title="タップ: 貼付 / 上へスワイプ: コピー"
            className="min-h-11 shrink-0 rounded-lg bg-white px-3 font-mono text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
          >
            貼付
          </button>
        <button
          onPointerDown={(event) => event.preventDefault()}
          onClick={() => sendSeq("\r")}
          aria-label="Enter"
          className="min-h-11 shrink-0 rounded-lg bg-white px-3 font-mono text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
        >
          Enter
        </button>
          {HELPER_KEYS.map((k) => (
          <button
            key={k.label}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => {
              if (k.modifier === "ctrl") {
                ctrlArmed.current = !ctrlArmed.current;
                setCtrlOn(ctrlArmed.current);
              } else if (k.seq) {
                sendSeq(k.seq);
              }
            }}
            className={`min-h-11 shrink-0 rounded-lg px-3 font-mono text-xs font-medium ${
              k.modifier === "ctrl" && ctrlOn
                ? "bg-accent-600 text-white"
                : "bg-white text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
            }`}
          >
            {k.label}
          </button>
          ))}
        </div>
      </div>

      {copySheet && <div className="absolute inset-0 z-40 flex items-end bg-black/40" onClick={() => setCopySheet(false)}><div role="dialog" aria-label="コピー" className="safe-bottom w-full rounded-t-2xl bg-white p-4 dark:bg-zinc-900" onClick={(event) => event.stopPropagation()}><div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-semibold">コピー</h2><button onClick={() => setCopySheet(false)} aria-label="閉じる" className="grid min-h-11 min-w-11 place-items-center rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"><IconX /></button></div><textarea ref={copyRef} readOnly rows={10} value={copyText} className="w-full resize-none rounded-xl border border-zinc-300 bg-zinc-50 p-3 font-mono text-base dark:border-zinc-700 dark:bg-zinc-950" /><p className="mt-1 text-xs text-zinc-400">選択した文字がある場合は選択範囲、それ以外は履歴全体です。</p><button onClick={() => void copyAll()} className="mt-2 min-h-11 w-full rounded-xl bg-accent-600 text-sm font-medium text-white hover:bg-accent-700">コピーする</button></div></div>}

    </div>,
    document.body,
  );
}
