// 作業場の「動かす」欄。箱（podman）の中でアプリを動かし、待ち受けたら preview を出す。
// 箱の外へ出るのは pod の公開ポートだけなので、箱の中では {host}（0.0.0.0）で待たせる。
// iframe は Project Lab の preview と同じ sandbox（allow-same-origin なし）。ControlDeck の API・Cookie には触れない。
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { IconPlay, IconStop } from "../../components/icons";
import { useToasts } from "../../stores";

type LabState = {
  running: boolean; listening: boolean; command: string; started_at: string; log_tail: string; preview_url: string | null;
};

const PLACEHOLDER = "npm run dev -- --host {host} --port {port} --base {base}";

export function WorkspaceLab({ name }: { name: string }) {
  const base = `/opencode/workspaces/${encodeURIComponent(name)}/lab`;
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const key = ["workspace-lab", name];
  const { data } = useQuery({
    queryKey: key,
    queryFn: () => api<LabState>(base),
    // 動いている間は数秒おきに読み直す（待ち受けの開始・止まったことを見つける）
    refetchInterval: (q) => (q.state.data?.running ? 3000 : false),
  });
  const [command, setCommand] = useState("");
  const [touched, setTouched] = useState(false);
  // 前回の command を初期値にする（利用者が打ち始めたら上書きしない）
  useEffect(() => {
    if (!touched && data?.command) setCommand(data.command);
  }, [data?.command, touched]);

  const fail = (e: unknown) => show(e instanceof ApiError || e instanceof Error ? e.message : "操作できませんでした");
  const run = useMutation({
    mutationFn: () => api<LabState>(`${base}/run`, { method: "POST", json: { command: command.trim() } }),
    onSuccess: (state) => qc.setQueryData(key, state),
    onError: fail,
  });
  const stop = useMutation({
    mutationFn: () => api<LabState>(`${base}/stop`, { method: "POST" }),
    onSuccess: (state) => qc.setQueryData(key, state),
    onError: fail,
  });

  const running = Boolean(data?.running);
  const listening = Boolean(data?.listening);
  const status = running ? (listening ? "動いています（待ち受け中）" : "起動を待っています…") : "止まっています";
  const tone = running ? (listening ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400") : "text-zinc-500 dark:text-zinc-400";
  const previewUrl = running && listening ? data?.preview_url : null;

  return (
    <div data-testid="workspace-lab" className="mt-3 border-t border-zinc-200 pt-3 dark:border-zinc-800">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="text-xs font-semibold">動かして確かめる</span>
        <span data-testid="workspace-lab-state" className={`min-w-0 text-[11px] font-medium ${tone}`}>{status}</span>
      </div>
      <label className="mt-1.5 block">
        <span className="sr-only">動かすコマンド</span>
        <input
          type="text" value={command} maxLength={500} placeholder={PLACEHOLDER} spellCheck={false}
          onChange={(e) => { setTouched(true); setCommand(e.target.value); }}
          className="min-h-11 w-full min-w-0 rounded-xl border border-zinc-300 bg-white px-3 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
        />
      </label>
      <p className="mt-1 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
        {"{port}"} は箱の公開ポート、{"{host}"} は 0.0.0.0（箱の中では 0.0.0.0 で待たないと届きません）、
        {"{base}"} は preview の URL の接頭辞に置き換わります。
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button" onClick={() => run.mutate()} disabled={run.isPending || !command.trim()}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white disabled:opacity-40"
        >
          <IconPlay className="h-4 w-4" /> {running ? "動かし直す" : "動かす"}
        </button>
        <button
          type="button" onClick={() => stop.mutate()} disabled={stop.isPending || !running}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-zinc-300 px-4 text-sm font-medium disabled:opacity-40 dark:border-zinc-700"
        >
          <IconStop className="h-4 w-4" /> 止める
        </button>
        {previewUrl && (
          <a href={previewUrl} target="_blank" rel="noopener noreferrer"
            className="inline-flex min-h-11 items-center rounded-xl px-3 text-sm font-medium text-accent-700 hover:underline dark:text-accent-300">
            別タブで開く
          </a>
        )}
      </div>
      {previewUrl && (
        <iframe
          title="作業場の preview" src={previewUrl} sandbox="allow-scripts allow-forms"
          className="mt-2 h-64 w-full rounded-xl border border-zinc-200 bg-white dark:border-zinc-700"
        />
      )}
      {data?.log_tail ? (
        <details className="mt-2">
          <summary className="min-h-11 cursor-pointer py-3 text-xs font-medium text-zinc-600 dark:text-zinc-300">ログの末尾</summary>
          <pre data-testid="workspace-lab-log" className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-100">
            {data.log_tail}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
