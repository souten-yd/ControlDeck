/** OpenCode の回答ごとの変更。ファイルの一覧・差分・取り消し。
 *
 * 2026-11-18 利用者の依頼（OpenCode の機能のうち、AI アシスタントで使えない便利なものを出す）。
 * 何を変えたかを回答のすぐ下で確かめ、最新の回答なら取り消せる。 */
import { useState } from "react";
import { api } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";

export interface CodeChange { file: string; additions: number; deletions: number; status: "added" | "deleted" | "modified" }
interface ChangeDetail extends CodeChange { patch: string; truncated?: boolean }

const STATUS_LABEL = { added: "追加", deleted: "削除", modified: "変更" };

function lineTone(line: string): string {
  if (line.startsWith("@@")) return "text-sky-700 dark:text-sky-300";
  if (line.startsWith("+")) return "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200";
  if (line.startsWith("-")) return "bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-200";
  return "text-zinc-600 dark:text-zinc-300";
}

function patchLines(patch: string): string[] {
  const lines = patch.replace(/\n$/, "").split("\n");
  const start = lines.findIndex((line) => line.startsWith("@@"));
  return start >= 0 ? lines.slice(start) : lines;
}

export function OpenCodeChanges({ conversationId, messageId, changes, reverted, canRevert, onReverted }: {
  conversationId: string; messageId: string; changes: CodeChange[]; reverted?: boolean; canRevert: boolean; onReverted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState<ChangeDetail[] | null>(null);
  const [error, setError] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [reverting, setReverting] = useState(false);
  const base = `/chat/conversations/${encodeURIComponent(conversationId)}/opencode`;
  const added = changes.reduce((sum, item) => sum + item.additions, 0);
  const removed = changes.reduce((sum, item) => sum + item.deletions, 0);
  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || details) return;
    try {
      setError("");
      const result = await api<{ files: ChangeDetail[] }>(`${base}/changes?message_id=${encodeURIComponent(messageId)}`);
      setDetails(result.files);
    } catch (e) {
      setError(e instanceof Error ? e.message : "差分を読み込めませんでした");
    }
  };
  const revert = async () => {
    setReverting(true);
    try {
      setError("");
      await api(`${base}/revert`, { method: "POST", json: { message_id: messageId } });
      setConfirming(false);
      onReverted();
    } catch (e) {
      setConfirming(false);
      setOpen(true);
      setError(e instanceof Error ? e.message : "取り消せませんでした");
    } finally {
      setReverting(false);
    }
  };
  return (
    <section data-code-changes className="min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white text-xs dark:border-zinc-800 dark:bg-zinc-900">
      <button type="button" onClick={() => void toggle()} aria-expanded={open}
        className="flex min-h-11 w-full min-w-0 items-center gap-2 px-3 text-left hover:bg-zinc-50 focus:outline-none focus-visible:bg-zinc-100 dark:hover:bg-white/[0.04] dark:focus-visible:bg-white/[0.06]">
        <span aria-hidden className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
        <span className="min-w-0 flex-1 truncate font-medium text-zinc-800 dark:text-zinc-100">
          {reverted ? "取り消した変更" : "変更"} {changes.length} ファイル
        </span>
        <span className="shrink-0 font-mono tabular-nums text-emerald-700 dark:text-emerald-300">+{added}</span>
        <span className="shrink-0 font-mono tabular-nums text-red-700 dark:text-red-300">−{removed}</span>
      </button>
      {open && (
        <div className="border-t border-zinc-100 dark:border-zinc-800">
          {error && <p role="alert" className="px-3 py-2 text-red-600 dark:text-red-400">{error}</p>}
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {changes.map((item) => {
              const detail = details?.find((d) => d.file === item.file);
              const expanded = shown === item.file;
              return (
                <li key={item.file} className="min-w-0">
                  <button type="button" onClick={() => setShown(expanded ? null : item.file)} aria-expanded={expanded} disabled={!detail}
                    className="flex min-h-11 w-full min-w-0 items-center gap-2 px-3 text-left font-mono text-[11px] hover:bg-zinc-50 disabled:cursor-default dark:hover:bg-white/[0.04]">
                    <span className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-100" title={item.file}>{item.file}</span>
                    <span className="shrink-0 font-sans text-zinc-500 dark:text-zinc-400">{STATUS_LABEL[item.status]}</span>
                    <span className="shrink-0 tabular-nums text-emerald-700 dark:text-emerald-300">+{item.additions}</span>
                    <span className="shrink-0 tabular-nums text-red-700 dark:text-red-300">−{item.deletions}</span>
                  </button>
                  {expanded && detail && (
                    <div data-code-patch className="max-h-80 overflow-auto overscroll-contain border-t border-zinc-100 py-1 font-mono text-[11px] leading-5 dark:border-zinc-800">
                      {patchLines(detail.patch).map((line, index) => (
                        <div key={index} className={`min-w-max whitespace-pre px-3 ${lineTone(line)}`}>{line || " "}</div>
                      ))}
                      {detail.truncated && <p className="px-3 py-1 font-sans text-zinc-500">長いため途中までです</p>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {canRevert && !reverted && (
            <div className="border-t border-zinc-100 p-2 dark:border-zinc-800">
              <button type="button" onClick={() => setConfirming(true)} data-code-revert
                className="min-h-11 w-full rounded-lg border border-red-300 px-3 text-xs font-semibold text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/40">
                この回の変更を取り消す
              </button>
            </div>
          )}
        </div>
      )}
      {confirming && (
        <ConfirmDialog title="この回の変更を取り消す" confirmLabel="取り消す" busy={reverting}
          message={`${changes.length} ファイルをこの依頼の前の状態に戻します。依頼の後に手で変えた内容も戻ることがあります。OpenCode もこの回のやり取りを忘れます。`}
          onConfirm={() => void revert()} onClose={() => setConfirming(false)} />
      )}
    </section>
  );
}
