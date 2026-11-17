/** AI アシスタントの OpenCode: 道具の実行（カード）・許可の確認・権限モードの札。
 *
 * OpenCode v2 のサーバー（opencode serve）から流れてくる道具の実行と許可の問い合わせを、
 * Claude Code のように 1 行ずつ出す。携帯で押しやすいよう、押せる所は高さ 44px（h-11）以上にする。 */
import { useEffect, useRef, useState } from "react";

export interface OpenCodeTool {
  id: string;
  name: string;
  status: "running" | "completed" | "failed";
  input?: Record<string, unknown>;
  output?: string;
}

export interface OpenCodePermission {
  id: string;
  action?: string;
  resources?: string[];
  save?: string[];
  tool?: string;
  /** pending / once / always / reject / expired */
  status: string;
}

export type PermissionMode = "auto" | "ask";

export const PERMISSION_MODES: { id: PermissionMode; label: string; icon: string; hint: string }[] = [
  { id: "auto", label: "Auto", icon: "⚡", hint: "編集・読むだけ・テスト/ビルドは自動。危ない操作と判断できないものだけ確認" },
  { id: "ask", label: "確認", icon: "✋", hint: "コマンドの実行・編集・外部への取得は毎回確認" },
];

const TOOL_ICON: Record<string, string> = {
  shell: "⌨️", edit: "✏️", write: "📝", read: "📄", glob: "🔍", grep: "🔎", webfetch: "🌐",
  websearch: "🌐", subagent: "🤖", execute: "🧩", skill: "📚", question: "❓",
};

/** 道具の入力を 1 行にまとめる（コマンド・ファイル・検索語）。 */
export function toolSummary(tool: OpenCodeTool): string {
  const input = tool.input ?? {};
  for (const key of ["command", "filePath", "path", "pattern", "url", "query", "description", "prompt"]) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim().split("\n")[0];
  }
  const raw = JSON.stringify(input);
  return raw === "{}" ? "" : raw;
}

const ACTION_LABEL: Record<string, string> = {
  shell: "コマンドを実行", edit: "ファイルを編集", write: "ファイルを書き込み", webfetch: "Web から取得",
  execute: "ControlDeck の道具を使用", external_directory: "プロジェクト外のフォルダを使用",
};

function ToolCard({ tool }: { tool: OpenCodeTool }) {
  const [open, setOpen] = useState(false);
  const summary = toolSummary(tool);
  const status = tool.status === "running"
    ? <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-accent-500 border-t-transparent" aria-label="実行中" />
    : tool.status === "failed"
      ? <span className="shrink-0 text-red-500" aria-label="失敗">✕</span>
      : <span className="shrink-0 text-emerald-500" aria-label="完了">✓</span>;
  return (
    <div className="min-w-0 rounded-lg border border-zinc-200 bg-white/70 dark:border-zinc-700 dark:bg-zinc-900/60">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex min-h-11 w-full min-w-0 items-center gap-2 px-2.5 py-1.5 text-left text-xs">
        <span aria-hidden className="shrink-0">{TOOL_ICON[tool.name] ?? "🔧"}</span>
        <span className="shrink-0 font-medium text-zinc-600 dark:text-zinc-300">{tool.name}</span>
        <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400">{summary}</code>
        {status}
      </button>
      {open && (
        <div className="border-t border-zinc-100 px-2.5 py-2 dark:border-zinc-800">
          {summary && <pre className="mb-1.5 whitespace-pre-wrap break-all font-mono text-[11px] text-zinc-600 dark:text-zinc-300">{summary}</pre>}
          <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-900 p-2 font-mono text-[11px] leading-snug text-zinc-100">
            {tool.output || (tool.status === "running" ? "実行中…" : "（出力なし）")}
          </pre>
        </div>
      )}
    </div>
  );
}

function PermissionCard({ item, onAnswer }: {
  item: OpenCodePermission; onAnswer: (decision: "once" | "always" | "reject") => Promise<void>;
}) {
  const [sending, setSending] = useState(false);
  const answer = async (decision: "once" | "always" | "reject") => {
    setSending(true);
    try {
      await onAnswer(decision);
    } finally {
      setSending(false);
    }
  };
  const label = ACTION_LABEL[item.action ?? ""] ?? item.action ?? "操作";
  if (item.status !== "pending") {
    const done = { once: "今回だけ許可しました", always: "常に許可しました", reject: "拒否しました", expired: "時間切れ（答える前に終わりました）" }[item.status] ?? item.status;
    return (
      <p className="flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-500">
        <span aria-hidden>{item.status === "reject" ? "🚫" : item.status === "expired" ? "⌛" : "✅"}</span>
        <span className="shrink-0">{label}:</span>
        <code className="min-w-0 truncate font-mono">{(item.resources ?? []).join(" / ")}</code>
        <span className="shrink-0">— {done}</span>
      </p>
    );
  }
  return (
    <div role="alertdialog" aria-label="OpenCode が許可を求めています"
      className="rounded-xl border-2 border-amber-400 bg-amber-50 p-3 dark:border-amber-500/70 dark:bg-amber-500/10">
      <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">OpenCode が許可を求めています: {label}</p>
      <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded bg-white/80 p-2 font-mono text-[12px] text-zinc-800 dark:bg-zinc-900 dark:text-zinc-100">
        {(item.resources ?? []).join("\n")}
      </pre>
      <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-3">
        <button type="button" disabled={sending} onClick={() => void answer("once")}
          className="min-h-11 rounded-lg bg-accent-600 px-3 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-50">
          今回だけ許可
        </button>
        <button type="button" disabled={sending} onClick={() => void answer("always")}
          className="min-h-11 rounded-lg border border-accent-600 px-3 text-sm font-semibold text-accent-700 hover:bg-accent-50 disabled:opacity-50 dark:text-accent-300 dark:hover:bg-accent-500/10"
          title={item.save?.length ? `この会話では「${item.save.join("・")}」を聞かずに許可します` : undefined}>
          常に許可{item.save?.length ? <span className="ml-1 font-mono text-[11px] font-normal">（{item.save.join("・")}）</span> : null}
        </button>
        <button type="button" disabled={sending} onClick={() => void answer("reject")}
          className="min-h-11 rounded-lg border border-zinc-300 px-3 text-sm font-semibold text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-800">
          拒否
        </button>
      </div>
    </div>
  );
}

export function OpenCodeActivity({ tools, permissions, onAnswer }: {
  tools?: OpenCodeTool[];
  permissions?: OpenCodePermission[];
  onAnswer: (requestId: string, decision: "once" | "always" | "reject") => Promise<void>;
}) {
  if (!tools?.length && !permissions?.length) return null;
  const pending = (permissions ?? []).filter((p) => p.status === "pending");
  const answered = (permissions ?? []).filter((p) => p.status !== "pending");
  return (
    <div className="space-y-1.5">
      {(tools ?? []).map((tool) => <ToolCard key={tool.id} tool={tool} />)}
      {answered.map((item) => <PermissionCard key={item.id} item={item} onAnswer={async () => {}} />)}
      {pending.map((item) => (
        <PermissionCard key={item.id} item={item} onAnswer={(decision) => onAnswer(item.id, decision)} />
      ))}
    </div>
  );
}

/** 入力欄の下の権限モードの札。タップで選ぶ（携帯）、Shift+Tab で切り替え（PC は入力欄側で扱う）。 */
export function PermissionModeChip({ mode, editable, onChange }: {
  mode: PermissionMode; editable: boolean; onChange: (mode: PermissionMode) => void;
}) {
  const [open, setOpen] = useState(false);
  // 入力欄の枠（overflow-hidden）に切られないよう、メニューは画面に固定して札の上に出す
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  const toggle = () => {
    if (!editable) return;
    const rect = button.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(288, window.innerWidth - 32);
      setPos({ left: Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)), bottom: window.innerHeight - rect.top + 8 });
    }
    setOpen((v) => !v);
  };
  const current = PERMISSION_MODES.find((m) => m.id === mode) ?? PERMISSION_MODES[0];
  return (
    <div ref={ref} className="relative shrink-0">
      <button ref={button} type="button" onClick={toggle} aria-haspopup="listbox" aria-expanded={open}
        title={editable ? `権限: ${current.label}（タップで切替・PC は Shift+Tab）` : `権限: ${current.label}（変更は管理者）`}
        className="flex h-6 items-center gap-1 rounded-full border border-zinc-300 px-2 text-[10px] font-semibold text-zinc-600 dark:border-zinc-600 dark:text-zinc-300">
        <span aria-hidden>{current.icon}</span>{current.label}
      </button>
      {open && pos && (
        <ul role="listbox" aria-label="権限モード" style={{ left: pos.left, bottom: pos.bottom }}
          className="fixed z-[100] w-[min(18rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
          {PERMISSION_MODES.map((option) => (
            <li key={option.id}>
              <button type="button" role="option" aria-selected={option.id === mode}
                onClick={() => { onChange(option.id); setOpen(false); }}
                className={`flex min-h-12 w-full items-start gap-2 px-3 py-2 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800 ${option.id === mode ? "bg-accent-50 dark:bg-accent-500/10" : ""}`}>
                <span aria-hidden className="text-base">{option.icon}</span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-zinc-800 dark:text-zinc-100">{option.label}{option.id === mode ? " ✓" : ""}</span>
                  <span className="block text-[11px] leading-snug text-zinc-500">{option.hint}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
