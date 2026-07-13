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
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
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
      scrollback: 5000,
      theme: dark
        ? { background: "#09090b", foreground: "#e4e4e7" }
        : { background: "#ffffff", foreground: "#18181b", cursor: "#18181b" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    termRef.current = term;

    const encoder = new TextEncoder();
    const decoder = new TextDecoder();
    // セッションは tmux でサーバー側に永続。WS が切れても明示的に閉じるまで自動再接続する
    let disposed = false;
    let retryTimer: number | undefined;
    let retryDelay = 500;

    const connect = () => {
      if (disposed) return;
      setStatus("connecting");
      const ws = new WebSocket(
        wsUrl(`/terminals/${sessionId}/connect?rows=${term.rows}&cols=${term.cols}`),
      );
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        retryDelay = 500;
        setStatus("open");
        term.focus();
      };
      ws.onmessage = (ev) => {
        term.write(typeof ev.data === "string" ? ev.data : decoder.decode(ev.data));
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
      if (ws?.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
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

    const refit = () => fit.fit();
    const observer = new ResizeObserver(refit);
    observer.observe(host);
    // iOS ソフトウェアキーボード対応
    window.visualViewport?.addEventListener("resize", refit);

    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      document.removeEventListener("visibilitychange", onVisible);
      observer.disconnect();
      window.visualViewport?.removeEventListener("resize", refit);
      wsRef.current?.close();
      term.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const sendSeq = (seq: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
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
        lines.push(buf!.getLine(i)?.translateToString(true) ?? "");
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
    <div className="fixed inset-0 z-40 flex flex-col bg-white dark:bg-zinc-950">
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
          onClick={onExit}
          aria-label="ターミナルを閉じる"
          className="ml-auto rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <IconX />
        </button>
      </div>

      {/* ターミナル本体 */}
      <div ref={hostRef} className="min-h-0 flex-1 px-1 pt-1" />

      {/* モバイル補助キーバー */}
      <div className="safe-bottom flex shrink-0 gap-1 overflow-x-auto border-t border-zinc-200 bg-zinc-50 px-2 py-1.5 dark:border-zinc-800 dark:bg-zinc-900 md:hidden">
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
