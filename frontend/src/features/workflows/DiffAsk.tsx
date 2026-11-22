/** 差分の行から「ここを直して」、結果のカードから「レビューして」を、同じ会話へビルドで送る（U7）。
 *  送る口は AssistantChat の send（「このプランで作る」「推奨どおりで進める」と同じ）。
 *  AssistantChat が生成中でないときだけ値を入れる（無いとき・ProjectLab では何も出ない）。 */
import { createContext, useContext, useState } from "react";

export const DiffAskContext = createContext<((text: string) => void) | null>(null);

export type AskTarget = { file: string; line: number; content: string };

/** OpenCode の reviewer（子エージェント）の係を使う指示。短く。 */
export const REVIEW_REQUEST = "今の変更をレビューして（reviewer の係を使って）。問題があれば直して。";

/** 「<ファイル>:<行> の <その行の内容> について: <文>」 */
export function askMessage(t: AskTarget, text: string): string {
  const content = t.content.trim();
  return `${t.file}:${t.line} の ${content.length > 80 ? `${content.slice(0, 79)}…` : content} について: ${text.trim()}`;
}

/** unified diff の各行の（新側の）行番号。削除の行は旧側の番号。@@ の行は新側の開始。差分の外は null。 */
export function unifiedLineNumbers(lines: string[]): ({ line: number; deleted: boolean } | null)[] {
  let old = 0;
  let next = 0;
  return lines.map((text) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) { old = Number(hunk[1]); next = Number(hunk[2]); return { line: next, deleted: false }; }
    if (text.startsWith("\\") || (!old && !next)) return null;
    if (text.startsWith("-")) return { line: old++, deleted: true };
    old++;
    return { line: next++, deleted: false };
  });
}

/** 押せる行にする属性（キーボードでも開ける）。ask が無ければ何も足さない。 */
export function askRowProps(onAsk: (() => void) | null) {
  return onAsk ? {
    role: "button" as const, tabIndex: 0, "data-diff-ask-row": "", title: "この行について直してもらう", onClick: onAsk,
    onKeyDown: (e: { key: string; preventDefault: () => void }) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onAsk(); } },
  } : {};
}

/** 行の下に開く小さな入力。送ると会話へ送って閉じる。 */
export function DiffAskForm({ target, onClose }: { target: AskTarget; onClose: () => void }) {
  const ask = useContext(DiffAskContext);
  const [text, setText] = useState("");
  if (!ask) return null;
  const submit = () => {
    if (!text.trim()) return;
    ask(askMessage(target, text));
    onClose();
  };
  return (
    <form data-diff-ask-form onSubmit={(e) => { e.preventDefault(); submit(); }}
      className="min-w-0 space-y-1.5 border-t border-zinc-200 bg-zinc-50 p-2 font-sans text-xs dark:border-zinc-800 dark:bg-zinc-900/60">
      <p className="truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400" title={`${target.file}:${target.line}`}>{target.file}:{target.line}</p>
      <div className="flex min-w-0 gap-1.5">
        <input autoFocus value={text} onChange={(e) => setText(e.target.value)} aria-label="この行について頼むこと" placeholder="ここをどう直すか"
          className="min-h-11 min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-2 text-base text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" />
        <button type="submit" disabled={!text.trim()} data-diff-ask-send
          className="min-h-11 shrink-0 rounded-lg bg-accent-600 px-3 font-semibold text-white hover:bg-accent-700 disabled:opacity-50">送る</button>
        <button type="button" onClick={onClose} aria-label="閉じる" className="min-h-11 shrink-0 rounded-lg border border-zinc-300 px-2 dark:border-zinc-700">✕</button>
      </div>
    </form>
  );
}

/** 「レビューして」ボタン。生成中（ask が無い）は出さない。 */
export function ReviewButton({ className = "" }: { className?: string }) {
  const ask = useContext(DiffAskContext);
  if (!ask) return null;
  return (
    <button type="button" data-review-ask onClick={() => ask(REVIEW_REQUEST)}
      className={`min-h-11 shrink-0 rounded-lg border border-zinc-300 px-3 text-sm font-semibold text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800 ${className}`}>
      レビューして
    </button>
  );
}
