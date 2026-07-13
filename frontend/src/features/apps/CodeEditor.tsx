/** アプリ用インラインコードエディタ（Monaco 遅延ロード）+ 動作確認（ストリーミング実行）。
 * FrameDeck のような常駐アプリも確認できるよう、実行中の出力をリアルタイム表示し
 * 停止ボタンで明示的に止める。 */
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { wsUrl } from "../../api/client";

const MonacoInline = lazy(() => import("./MonacoInline"));

type RunState = "idle" | "running" | "done";

interface OutLine {
  kind: "stdout" | "stderr" | "notice";
  text: string;
}

export function CodeEditor({
  appType,
  pythonPath,
  workDir,
  code,
  onChange,
}: {
  appType: "python_script" | "shell_script";
  pythonPath: string;
  workDir?: string;
  code: string;
  onChange: (v: string) => void;
}) {
  const [state, setState] = useState<RunState>("idle");
  const [lines, setLines] = useState<OutLine[]>([]);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [stopped, setStopped] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const outRef = useRef<HTMLPreElement>(null);
  const language = appType === "python_script" ? "python" : "shell";

  // 出力の追記に合わせて自動スクロール
  useEffect(() => {
    const el = outRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  // アンマウント時は実行中プロセスも止める（WS 切断でサーバー側が terminate）
  useEffect(() => () => wsRef.current?.close(), []);

  const append = (kind: OutLine["kind"], text: string) => {
    setLines((prev) => {
      const next = [...prev, { kind, text }];
      return next.length > 2000 ? next.slice(-1500) : next; // メモリ上限
    });
  };

  const start = () => {
    setState("running");
    setLines([]);
    setExitCode(null);
    setStopped(false);
    setError(null);
    const ws = new WebSocket(wsUrl("/apps/test-run/stream"));
    wsRef.current = ws;
    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          application_type: appType,
          python_path: pythonPath || null,
          code,
          working_directory: workDir || null,
        }),
      );
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "stdout" || msg.type === "stderr") append(msg.type, msg.data);
        else if (msg.type === "notice") append("notice", `[${msg.message}]\n`);
        else if (msg.type === "start") append("notice", `[実行開始: ${msg.cwd}]\n`);
        else if (msg.type === "exit") {
          setExitCode(msg.code);
          setState("done");
        } else if (msg.type === "error") {
          setError(msg.message);
          setState("done");
        }
      } catch {
        /* 不正なメッセージは無視 */
      }
    };
    ws.onclose = () => {
      setState((s) => (s === "running" ? "done" : s));
    };
    ws.onerror = () => {
      setError("接続に失敗しました");
      setState("done");
    };
  };

  const stop = () => {
    setStopped(true);
    wsRef.current?.send(JSON.stringify({ type: "stop" }));
  };

  const running = state === "running";
  const ok = exitCode === 0 || (stopped && exitCode !== null);

  return (
    <div>
      <div className="overflow-hidden rounded-xl border border-zinc-300 dark:border-zinc-700">
        <Suspense fallback={<div className="grid h-48 place-items-center text-xs text-zinc-400">エディタを読み込み中...</div>}>
          <MonacoInline value={code} language={language} onChange={onChange} />
        </Suspense>
      </div>
      <div className="mt-2 flex items-center gap-2">
        {running ? (
          <button
            type="button"
            onClick={stop}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700"
          >
            ■ 停止
          </button>
        ) : (
          <button
            type="button"
            onClick={start}
            disabled={!code.trim()}
            className="rounded-lg bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300"
          >
            ▶ 動作確認
          </button>
        )}
        <span className="text-xs text-zinc-400">
          {running ? "実行中（出力をリアルタイム表示）" : "一時実行して出力を確認します。常駐アプリは停止ボタンで終了"}
        </span>
      </div>
      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
      {(running || lines.length > 0 || exitCode !== null) && (
        <div className="mt-2 overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
          <div className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium">
            {running ? (
              <span className="flex items-center gap-1.5 text-accent-600 dark:text-accent-400">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" /> 実行中...
              </span>
            ) : exitCode !== null ? (
              <span className={ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
                {stopped ? `停止しました（終了コード ${exitCode}）` : `終了コード ${exitCode}`}
              </span>
            ) : null}
          </div>
          <pre
            ref={outRef}
            className="max-h-56 min-h-[3rem] overflow-auto border-t border-zinc-200 bg-zinc-950 p-2 font-mono text-[11px] leading-relaxed dark:border-zinc-800"
          >
            {lines.map((l, i) => (
              <span
                key={i}
                className={l.kind === "stderr" ? "text-red-400" : l.kind === "notice" ? "text-zinc-500" : "text-zinc-200"}
              >
                {l.text}
              </span>
            ))}
            {lines.length === 0 && !running && <span className="text-zinc-500">（出力なし）</span>}
          </pre>
        </div>
      )}
    </div>
  );
}
