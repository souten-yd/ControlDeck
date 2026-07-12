/** xterm.js ターミナルビュー（遅延ロードチャンク）。
 * モバイル: visualViewport で高さ再計算 + Ctrl/Esc/Tab/矢印の補助キーバー。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { wsUrl } from "../../api/client";
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
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");
  const [ctrlOn, setCtrlOn] = useState(false);

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

    const ws = new WebSocket(
      wsUrl(`/terminals/${sessionId}/connect?rows=${term.rows}&cols=${term.cols}`),
    );
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    const encoder = new TextEncoder();
    const decoder = new TextDecoder();

    ws.onopen = () => {
      setStatus("open");
      term.focus();
    };
    ws.onmessage = (ev) => {
      term.write(typeof ev.data === "string" ? ev.data : decoder.decode(ev.data));
    };
    ws.onclose = () => {
      setStatus("closed");
      term.write("\r\n\x1b[90m[切断されました]\x1b[0m\r\n");
    };

    const send = (data: string) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
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
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: "resize", rows, cols }));
    });

    const refit = () => fit.fit();
    const observer = new ResizeObserver(refit);
    observer.observe(host);
    // iOS ソフトウェアキーボード対応
    window.visualViewport?.addEventListener("resize", refit);

    return () => {
      observer.disconnect();
      window.visualViewport?.removeEventListener("resize", refit);
      ws.close();
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
            status === "open" ? "text-emerald-600 dark:text-emerald-400" : status === "closed" ? "text-red-500" : "text-zinc-400"
          }`}
        >
          {status === "open" ? "接続中" : status === "closed" ? "切断" : "接続中..."}
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
    </div>,
    document.body,
  );
}
