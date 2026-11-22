/** OpenCode のアクションリスト（todowrite）。リストがあるときだけ、会話の上に小さく浮かべる。
 *  タップで開くと、いま何をしているか・何が終わったか・いつ終わったかが分かる。
 *  2026-11-18 利用者「いつ終わるのかわからん」「有効な時だけ表示するなど工夫して」。
 *  リストはモデルが書いたものなので、更新されないと実際の進み具合とずれる。ずれていそうなら正直に出す
 *  （同日 本番: 最初の 1 回の後、91 回の操作の間更新されず、1 番目が作業中のままに見えた）。 */
import { useEffect, useState } from "react";
import { formatDuration } from "./OpenCodeActivity";

export interface OpenCodeTodo {
  content: string;
  status: string;
  started_at?: number;
  completed_at?: number;
  updated_at?: number;
}

/** リストの最後の更新の後に、これだけ道具を使っていたら「古いまま」と出す */
const STALE_AFTER_TOOLS = 8;
const isOpen = (todo: OpenCodeTodo) => todo.status === "pending" || todo.status === "in_progress";

const clock = (seconds: number) => new Date(seconds * 1000).toLocaleTimeString("ja-JP", { hour12: false, hour: "2-digit", minute: "2-digit" });

/** いまの作業の段（計画・実装・確認）。プランの回は計画、項目の言葉に確認系があれば確認、それ以外は実装。
 *  モデルが書いた ToDo の文から推す目安なので、断定しない（段の名前だけを短く出す）。 */
export function workStage(agent: string | undefined, content: string): "計画" | "実装" | "確認" {
  if (agent === "plan") return "計画";
  if (/(テスト|確認|検証|レビュー|撮影|関門|dev-check|E2E)/i.test(content)) return "確認";
  if (/(計画|設計|調査|調べ|読む|読み込)/.test(content)) return "計画";
  return "実装";
}

const mmss = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

export function TodoFloat({ todos, active, tools = [], agent }: {
  todos?: OpenCodeTodo[]; active: boolean; tools?: Array<{ started_at?: number }>; agent?: string;
}) {
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const ticking = active && Boolean(todos?.some((t) => t.status === "in_progress"));
  useEffect(() => {
    if (!ticking) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [ticking]);
  if (!todos?.length || !active) return null;
  const done = todos.filter((t) => t.status === "completed").length;
  const current = todos.find((t) => t.status === "in_progress");
  const percent = Math.round((done / todos.length) * 100);
  const updatedAt = Math.max(0, ...todos.flatMap((t) => [t.updated_at ?? 0, t.started_at ?? 0, t.completed_at ?? 0]));
  const toolsSince = updatedAt ? tools.filter((t) => (t.started_at ?? 0) > updatedAt).length : 0;
  const stale = todos.some(isOpen) && toolsSince >= STALE_AFTER_TOOLS;
  return (
    <div data-assistant-todos className="pointer-events-none sticky top-0 z-20 mx-auto flex max-w-3xl justify-end">
      <div className={`pointer-events-auto min-w-0 rounded-2xl border border-zinc-200 bg-white/95 shadow-lg backdrop-blur dark:border-zinc-700 dark:bg-zinc-900/95 ${open ? "w-full sm:w-96" : "max-w-full"}`}>
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
          aria-label={`アクションリスト ${done}/${todos.length} 完了`}
          className="flex min-h-11 w-full min-w-0 items-center gap-2 px-3 text-left text-xs">
          <span aria-hidden>📋</span>
          <span className="num shrink-0 font-semibold">{done}/{todos.length}</span>
          <span className="hidden h-1 w-10 shrink-0 sm:block overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
            <span className="block h-full rounded-full bg-accent-500" style={{ width: `${percent}%` }} />
          </span>
          {current && !stale ? (
            <span data-now-line className="flex min-w-0 flex-1 items-center gap-1 whitespace-nowrap text-zinc-500 dark:text-zinc-400">
              <span className="shrink-0">いま:</span>
              <span className="min-w-0 truncate text-zinc-800 dark:text-zinc-100">{current.content}</span>
              <span className="num shrink-0">· {current.started_at ? `${mmss(Math.max(0, now - current.started_at))} · ` : ""}{workStage(agent, current.content)}</span>
            </span>
          ) : (
            <span className="min-w-0 max-w-52 truncate text-zinc-500 dark:text-zinc-400">{stale ? `更新後 ${toolsSince} 操作` : done === todos.length ? "すべて完了" : "次の作業を待っています"}</span>
          )}
        </button>
        {open && stale && (
          <p data-todo-stale className="border-t border-zinc-100 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:border-zinc-800 dark:text-amber-200">
            最後の更新は {clock(updatedAt)} です。その後 {toolsSince} 回の操作をしています。1 つの項目が大きいだけのこともありますが、実際はこの表示より進んでいる可能性があります。
          </p>
        )}
        {open && (
          <ol className="max-h-[50dvh] space-y-1 overflow-y-auto overscroll-contain border-t border-zinc-100 p-2 text-[13px] dark:border-zinc-800">
            {todos.map((todo, index) => {
              const finished = todo.status === "completed";
              const doing = todo.status === "in_progress";
              const cancelled = todo.status === "cancelled";
              return (
                <li key={`${index}-${todo.content}`} data-todo-status={todo.status}
                  className={`flex min-w-0 gap-2 rounded-lg px-2 py-1.5 ${doing ? "bg-accent-50 dark:bg-accent-600/15" : ""}`}>
                  <span aria-hidden className={`w-4 shrink-0 text-center ${finished ? "text-emerald-500" : doing ? "text-accent-600 dark:text-accent-300" : "text-zinc-400"}`}>
                    {finished ? "✓" : doing ? "▶" : cancelled ? "–" : "○"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={`block break-words ${finished || cancelled ? "text-zinc-500 line-through decoration-zinc-400/60" : ""}`}>{todo.content}</span>
                    {(todo.started_at || todo.completed_at) && (
                      <span className="num block text-[11px] text-zinc-500">
                        {todo.completed_at
                          ? `${clock(todo.completed_at)} 完了${todo.started_at && todo.completed_at > todo.started_at ? `（${formatDuration(todo.completed_at - todo.started_at)}）` : ""}`
                          : `${clock(todo.started_at!)} 開始`}
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
