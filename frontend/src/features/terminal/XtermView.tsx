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
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const historyReadyRef = useRef(false);
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
    if (!host) return;
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
    let scrollRefreshFrame = 0;
    term.onScroll(() => {
      updateViewportMarker();
      window.cancelAnimationFrame(scrollRefreshFrame);
      scrollRefreshFrame = window.requestAnimationFrame(() => term.refresh(0, term.rows - 1));
    });

    const encoder = new TextEncoder();
    // xterm.writeは非同期queue。resetを直接呼ぶと先行するtmux初期描画を追い越すため、
    // data/resetをWebSocketの受信順どおり完了させる。
    let writeTail = Promise.resolve();
    const enqueueWrite = (data: string | Uint8Array) => {
      writeTail = writeTail.then(() => new Promise<void>((resolve) => term.write(data, resolve)));
    };
    const enqueueReset = () => {
      writeTail = writeTail.then(() => {
        term.reset();
      });
    };
    // セッションは tmux でサーバー側に永続。WS が切れても明示的に閉じるまで自動再接続する
    let disposed = false;
    let retryTimer: number | undefined;
    let retryDelay = 500;

    const connect = () => {
      if (disposed) return;
      historyReadyRef.current = false;
      setStatus("connecting");
      const ws = new WebSocket(
        wsUrl(`/terminals/${sessionId}/connect?rows=${term.rows}&cols=${term.cols}`),
      );
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        retryDelay = 500;
        setStatus("open");
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          try {
            const control = JSON.parse(ev.data);
            if (control.type === "history_reset") {
              enqueueReset();
              return;
            }
            if (control.type === "history_end") {
              writeTail = writeTail.then(() => {
                historyReadyRef.current = true;
                term.focus();
              });
              return;
            }
          } catch {
            // 旧backend等の通常文字列はそのまま表示する。
          }
          enqueueWrite(ev.data);
          return;
        }
        enqueueWrite(new Uint8Array(ev.data));
      };
      ws.onclose = (ev) => {
        if (disposed) return;
        if (ev.code === 4404) {
          // セッション自体が存在しない（終了済み）→ 再接続しない
          setStatus("gone");
          term.write("\r\n\x1b[90m[セッションが終了しました]\x1b[0m\r\n");
          return;
        }
        setStatus("closed");
        retryTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 5000);
      };
    };
    connect();

    const send = (data: string) => {
      const ws = wsRef.current;
      if (historyReadyRef.current && ws?.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
    };
    term.onData((data) => {
      if (ctrlArmed.current && data.length === 1 && /[a-z]/i.test(data)) {
        ctrlArmed.current = false;
        setCtrlOn(false);
        send(String.fromCharCode(data.toLowerCase().charCodeAt(0) - 96));
        return;
      }
      send(data);
    });
    term.onResize(({ rows, cols }) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: "resize", rows, cols }));
    });

    // タブ復帰時（iOS はバックグラウンドで WS が切れる）は待たずに即再接続
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const ws = wsRef.current;
      if (ws && ws.readyState !== WebSocket.OPEN && ws.readyState !== WebSocket.CONNECTING) {
        window.clearTimeout(retryTimer);
        retryDelay = 500;
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    const root = rootRef.current;
    const coarseMobile = window.matchMedia("(max-width: 767px) and (pointer: coarse)").matches;
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
    let fitFrame = 0;
    const syncViewport = () => {
      if (coarseMobile && root && window.visualViewport) {
        const viewport = window.visualViewport;
        root.style.left = `${viewport.offsetLeft}px`;
        root.style.top = `${viewport.offsetTop}px`;
        root.style.width = `${viewport.width}px`;
        root.style.height = `${viewport.height}px`;
      }
    };
    const scheduleFit = () => {
      window.cancelAnimationFrame(fitFrame);
      fitFrame = window.requestAnimationFrame(() => {
        const dimensions = fit.proposeDimensions();
        if (dimensions && (dimensions.cols !== term.cols || dimensions.rows !== term.rows)) {
          term.resize(dimensions.cols, dimensions.rows);
        }
      });
    };
    const syncViewportAndFit = () => {
      syncViewport();
      scheduleFit();
    };
    syncViewportAndFit();
    const observer = new ResizeObserver(scheduleFit);
    observer.observe(host);
    // iOS/Android: keyboardで縮小・移動するvisual viewportへroot自体を追従。
    window.visualViewport?.addEventListener("resize", syncViewportAndFit);
    // keyboardの自動panは寸法を変えないため、座標だけ同期して入力中のreflowを避ける。
    window.visualViewport?.addEventListener("scroll", syncViewport);

    // xterm.js 6の独自scrollbarはtouch dragをbuffer scrollへ変換しないため明示的に補う。
    let touchTracking = false;
    let touchScrolling = false;
    let touchStartX = 0;
    let touchStartY = 0;
    let touchLastY = 0;
    let touchRemainder = 0;
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
      const screen = host.querySelector<HTMLElement>(".xterm-screen");
      const cellHeight = screen && term.rows > 0
        ? screen.getBoundingClientRect().height / term.rows
        : term.options.fontSize ?? 13;
      touchRemainder += (touchLastY - touch.clientY) / Math.max(cellHeight, 1);
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
      historyReadyRef.current = false;
      window.clearTimeout(retryTimer);
      document.removeEventListener("visibilitychange", onVisible);
      observer.disconnect();
      window.cancelAnimationFrame(fitFrame);
      window.cancelAnimationFrame(touchScrollFrame);
      window.cancelAnimationFrame(scrollRefreshFrame);
      window.visualViewport?.removeEventListener("resize", syncViewportAndFit);
      window.visualViewport?.removeEventListener("scroll", syncViewport);
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
      wsRef.current?.close();
      term.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const sendSeq = (seq: string) => {
    if (historyReadyRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(new TextEncoder().encode(seq));
    }
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
      <div className="safe-top flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-1.5 dark:border-zinc-800">
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
      <div ref={hostRef} className="terminal-xterm-host min-h-0 flex-1 overflow-hidden px-1 pt-1" />

      {/* モバイル補助キーバー */}
      <div
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
