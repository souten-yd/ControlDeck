/** AI アシスタントの OpenCode: 道具の実行（1 行ずつ）・許可の確認・権限モードの札。
 *
 * OpenCode v2 のサーバー（opencode serve）から流れてくる道具の実行と許可の問い合わせを、
 * Claude Code のように 1 行ずつ出す。携帯で押しやすいよう、押せる所は高さ 44px（h-11）以上にする。 */
import { useState } from "react";

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
  { id: "auto", label: "Auto", icon: "⏵⏵", hint: "箱の中は確認なしで進める（禁止リストの操作だけ止める）" },
  // U+FE0E: 携帯で絵文字にならないよう文字の形で出す
  { id: "ask", label: "確認", icon: "⏸\uFE0E", hint: "コマンドの実行・編集・外部への取得は毎回確認" },
];

/** OpenCode の道具名 → Claude Code 風の表示名。 */
const TOOL_LABEL: Record<string, string> = {
  shell: "Bash", edit: "Edit", write: "Write", read: "Read", grep: "Grep", glob: "Glob", webfetch: "Fetch",
  websearch: "Search", subagent: "Task", execute: "MCP", skill: "Skill", question: "Ask",
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

/** 許可の問い合わせの見出し（Claude Code 風の問いかけ）。 */
const ACTION_QUESTION: Record<string, string> = {
  shell: "Bash を実行しますか？", edit: "Edit でファイルを編集しますか？", write: "Write でファイルを書き込みますか？",
  webfetch: "Fetch で Web から取得しますか？", execute: "MCP で ControlDeck の道具を使いますか？",
  external_directory: "プロジェクト外のフォルダを使いますか？",
};

function ToolRow({ tool }: { tool: OpenCodeTool }) {
  const [open, setOpen] = useState(false);
  const summary = toolSummary(tool);
  const running = tool.status === "running";
  const failed = tool.status === "failed";
  const output = (tool.output ?? "").trim();
  const preview = output ? output.split("\n")[0] : running ? "実行中…" : "";
  const more = output ? output.split("\n").length - 1 : 0;
  return (
    <div className="min-w-0">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="group flex min-h-11 w-full min-w-0 flex-col justify-center gap-0.5 px-3 py-1.5 text-left font-mono text-xs leading-5 hover:bg-zinc-100/70 focus:outline-none focus-visible:bg-zinc-100 dark:hover:bg-white/[0.04] dark:focus-visible:bg-white/[0.06]">
        <span className="flex w-full min-w-0 items-start gap-2">
          <span className="flex h-5 w-2 shrink-0 items-center justify-center">
            <span role="img" aria-label={running ? "実行中" : failed ? "失敗" : "完了"}
              className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-accent-500 shadow-[0_0_0_3px] shadow-accent-500/25" : failed ? "bg-red-500" : "bg-emerald-500"}`} />
          </span>
          <span className={`min-w-0 flex-1 ${open ? "whitespace-pre-wrap break-all" : "flex"}`}>
            <span className="shrink-0 font-bold text-zinc-900 dark:text-zinc-50">{TOOL_LABEL[tool.name] ?? tool.name}</span>
            {summary && (open ? (
              <span className="text-zinc-600 dark:text-zinc-300">({summary})</span>
            ) : (
              <>
                <span className="shrink-0 text-zinc-400 dark:text-zinc-500">(</span>
                <span className="min-w-0 truncate text-zinc-600 dark:text-zinc-300">{summary}</span>
                <span className="shrink-0 text-zinc-400 dark:text-zinc-500">)</span>
              </>
            ))}
          </span>
        </span>
        {!open && preview && (
          <span className="flex w-full min-w-0 gap-2 pl-4 text-[11px] text-zinc-400 dark:text-zinc-500">
            <span aria-hidden className="shrink-0">⎿</span>
            <span className={`min-w-0 truncate ${failed ? "text-red-500/90 dark:text-red-400/90" : ""}`}>{preview}</span>
            {more > 0 && <span className="shrink-0 text-zinc-400/80 dark:text-zinc-600">+{more} 行</span>}
          </span>
        )}
      </button>
      {open && (
        <div className="flex min-w-0 gap-2 pb-2.5 pl-7 pr-3 font-mono text-[11px] leading-snug">
          <span aria-hidden className="shrink-0 text-zinc-400 dark:text-zinc-500">⎿</span>
          <pre className={`max-h-60 min-w-0 flex-1 overflow-auto whitespace-pre-wrap break-all border-l border-zinc-200 pl-2.5 dark:border-zinc-700/80 ${failed ? "text-red-600 dark:text-red-400" : "text-zinc-600 dark:text-zinc-400"}`}>
            {output || (running ? "実行中…" : "（出力なし）")}
          </pre>
        </div>
      )}
    </div>
  );
}

function PermissionPrompt({ item, onAnswer }: {
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
    const tone = item.status === "reject" ? "text-red-500" : item.status === "expired" ? "text-zinc-400" : "text-emerald-500";
    return (
      <p className="flex min-w-0 items-center gap-2 px-3 font-mono text-[11px] leading-5 text-zinc-500 dark:text-zinc-500">
        <span aria-hidden className={`w-2 shrink-0 text-center ${tone}`}>{item.status === "reject" ? "✕" : item.status === "expired" ? "–" : "✓"}</span>
        <span className="shrink-0">{label}:</span>
        <code className="min-w-0 truncate font-mono">{(item.resources ?? []).join(" / ")}</code>
        <span className="shrink-0">— {done}</span>
      </p>
    );
  }
  const question = ACTION_QUESTION[item.action ?? ""] ?? `${label} を許可しますか？`;
  const save = item.save?.length ? item.save.join("・") : "";
  const options: { decision: "once" | "always" | "reject"; label: string; title?: string }[] = [
    { decision: "once", label: "はい（今回だけ）" },
    { decision: "always", label: save ? `はい、以後「${save}」は聞かない` : "はい、以後は聞かない",
      title: save ? `このプロジェクトでは「${save}」を聞かずに許可します` : undefined },
    { decision: "reject", label: "いいえ（拒否）" },
  ];
  return (
    <div role="alertdialog" aria-label="OpenCode が許可を求めています"
      className="min-w-0 overflow-hidden rounded-lg border border-l-[3px] border-zinc-200 border-l-amber-500 bg-white font-mono shadow-sm dark:border-zinc-800 dark:border-l-amber-400 dark:bg-zinc-900/70">
      <div className="px-3 pb-2 pt-2.5">
        <p className="text-[10px] font-semibold tracking-wider text-amber-600 dark:text-amber-400">許可の確認</p>
        <p className="mt-0.5 font-sans text-sm font-semibold text-zinc-900 dark:text-zinc-50">{question}</p>
        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md bg-zinc-100 px-2.5 py-2 text-xs leading-snug text-zinc-800 dark:bg-black/40 dark:text-zinc-100">
          {(item.resources ?? []).join("\n")}
        </pre>
      </div>
      <div className="flex flex-col border-t border-zinc-100 p-1 dark:border-zinc-800">
        {options.map((option, index) => (
          <button key={option.decision} type="button" disabled={sending} onClick={() => void answer(option.decision)} title={option.title}
            className={`flex min-h-11 w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] leading-snug focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/50 disabled:opacity-50 ${
              index === 0
                ? "bg-accent-50 font-semibold text-accent-700 hover:bg-accent-100 dark:bg-accent-500/15 dark:text-accent-300 dark:hover:bg-accent-500/25"
                : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-white/[0.06]"
            }`}>
            <span aria-hidden className="w-3 shrink-0 text-accent-600 dark:text-accent-400">{index === 0 ? "❯" : ""}</span>
            <span className={`shrink-0 ${index === 0 ? "" : "text-zinc-400 dark:text-zinc-500"}`}>{index + 1}.</span>
            <span className="min-w-0 break-words font-sans">{option.label}</span>
          </button>
        ))}
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
    <div className="min-w-0 space-y-1.5">
      {!!tools?.length && (
        <div className="min-w-0 overflow-hidden rounded-lg border border-zinc-200/80 bg-zinc-50/70 py-0.5 dark:border-zinc-800 dark:bg-zinc-900/50">
          {tools.map((tool) => <ToolRow key={tool.id} tool={tool} />)}
        </div>
      )}
      {answered.map((item) => <PermissionPrompt key={item.id} item={item} onAnswer={async () => {}} />)}
      {pending.map((item) => (
        <PermissionPrompt key={item.id} item={item} onAnswer={(decision) => onAnswer(item.id, decision)} />
      ))}
    </div>
  );
}

/** 入力欄の下の権限モードの札。タップで選ぶ（携帯）、Shift+Tab で切り替え（PC は入力欄側で扱う）。 */
