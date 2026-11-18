/** 送信待ちの要望（生成中に送ったもの）と、音声入力の波形。
 *
 * 2026-11-18 利用者「実行中に要望を送信したい。サーバー側で保存しておき、適切なタイミングで LLM に送る。
 * 複数の送信・削除・編集・取り消しができるように」「Claude のように波形が出て、音声が入っているのを確かめたい」。
 * 送信待ちはサーバーが持ち、回答が正常に終わったら古い順に送る（同じ送り方の続きは 1 つにまとめる）。 */
import { useState } from "react";
import { WAVE_BARS, useVoiceMeter, type VoiceMeterStore } from "./useAssistantAsr";

export interface QueuedItem {
  id: string;
  content: string;
  status: "queued" | "failed";
  mode: string;
  error: string;
}

export function QueuedRequests({ items, held, removed, onEdit, onDelete, onRestore, onSendNow, onClear, canSteer = false, onSteer }: {
  items: QueuedItem[];
  /** 生成中でないのに残っている（止めた・失敗した後） */
  held: boolean;
  /** 直前に外した要望（「元に戻す」を出す） */
  removed: { ids: string[]; label: string } | null;
  onEdit: (id: string, content: string) => Promise<boolean>;
  onDelete: (id: string) => void;
  onRestore: () => void;
  onSendNow: () => void;
  onClear: () => void;
  /** OpenCode が作業中なら、回答を待たずに伝えられる（今の道具が終わったところで届く） */
  canSteer?: boolean;
  onSteer?: (id: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  if (items.length === 0 && !removed) return null;
  return (
    <section data-assistant-queue aria-label="送信待ちの要望"
      className="mb-2 w-full min-w-0 rounded-2xl border border-zinc-200 bg-zinc-50/90 p-2 text-sm shadow-sm dark:border-zinc-700 dark:bg-zinc-900/90">
      {items.length > 0 && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-1 pb-1.5">
          <span className="min-w-0 flex-1 text-xs text-zinc-600 dark:text-zinc-300">
            <span className="font-semibold">送信待ち {items.length} 件</span>
            <span className="text-zinc-500"> · {held ? "止めた・失敗したので送っていません" : "回答が終わったら送ります"}</span>
          </span>
          {held && (
            <button type="button" onClick={onSendNow}
              className="min-h-9 rounded-lg bg-accent-600 px-3 text-xs font-semibold text-white hover:bg-accent-700 [@media(pointer:coarse)]:min-h-11">今すぐ送る</button>
          )}
          <button type="button" onClick={onClear}
            className="min-h-9 rounded-lg px-2 text-xs text-zinc-500 hover:bg-zinc-200/70 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 [@media(pointer:coarse)]:min-h-11">すべて取り消す</button>
        </div>
      )}
      <ol className="max-h-56 space-y-1.5 overflow-y-auto overscroll-contain">
        {items.map((item, index) => (
          <li key={item.id} data-queued-item={item.id}
            className={`min-w-0 rounded-xl border bg-white px-2.5 py-1.5 dark:bg-zinc-950 ${item.status === "failed" ? "border-red-300 dark:border-red-800" : "border-zinc-200 dark:border-zinc-800"}`}>
            {editing === item.id ? (
              <div className="space-y-1.5">
                <textarea value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="送信待ちの要望を書き直す"
                  rows={Math.min(6, Math.max(2, draft.split("\n").length))} autoFocus
                  onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }}
                  className="w-full resize-none rounded-lg border border-zinc-300 bg-transparent px-2 py-1 text-base outline-none focus:border-accent-500 dark:border-zinc-700 sm:text-sm" />
                <div className="flex justify-end gap-1.5">
                  <button type="button" onClick={() => setEditing(null)}
                    className="min-h-9 rounded-lg px-3 text-xs text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800 [@media(pointer:coarse)]:min-h-11">取消</button>
                  <button type="button" disabled={!draft.trim()}
                    onClick={async () => { if (await onEdit(item.id, draft.trim())) setEditing(null); }}
                    className="min-h-9 rounded-lg bg-accent-600 px-3 text-xs font-semibold text-white hover:bg-accent-700 disabled:opacity-50 [@media(pointer:coarse)]:min-h-11">保存</button>
                </div>
              </div>
            ) : (
              <div className="flex min-w-0 items-start gap-1.5">
                <span aria-hidden className="mt-0.5 shrink-0 text-[11px] tabular-nums text-zinc-400">{index + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-3 whitespace-pre-wrap break-words text-[13px] leading-snug text-zinc-800 dark:text-zinc-100">{item.content}</p>
                  {item.status === "failed" && <p className="mt-0.5 text-[11px] text-red-600 dark:text-red-400">送れませんでした: {item.error}</p>}
                </div>
                {canSteer && item.status === "queued" && (
                  <button type="button" onClick={() => onSteer?.(item.id)} aria-label={`送信待ち ${index + 1} を今すぐ伝える`} title="今の作業が一区切りしたところで OpenCode に届きます"
                    className="grid h-8 shrink-0 place-items-center rounded-lg px-1.5 text-xs font-semibold text-accent-700 hover:bg-accent-50 dark:text-accent-300 dark:hover:bg-accent-950/40 [@media(pointer:coarse)]:h-11">今すぐ伝える</button>
                )}
                <button type="button" onClick={() => { setDraft(item.content); setEditing(item.id); }} aria-label={`送信待ち ${index + 1} を編集`}
                  className="grid h-8 min-w-9 shrink-0 place-items-center rounded-lg text-xs text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 [@media(pointer:coarse)]:h-11">編集</button>
                <button type="button" onClick={() => onDelete(item.id)} aria-label={`送信待ち ${index + 1} を削除`}
                  className="grid h-8 min-w-9 shrink-0 place-items-center rounded-lg text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40 [@media(pointer:coarse)]:h-11">削除</button>
              </div>
            )}
          </li>
        ))}
      </ol>
      {removed && (
        <p role="status" className="flex min-w-0 items-center gap-2 px-1 pt-1.5 text-xs text-zinc-500">
          <span className="min-w-0 flex-1 truncate">{removed.label}</span>
          <button type="button" onClick={onRestore}
            className="min-h-9 shrink-0 rounded-lg px-2 font-semibold text-accent-700 hover:bg-accent-50 dark:text-accent-300 dark:hover:bg-accent-950/40 [@media(pointer:coarse)]:min-h-11">元に戻す</button>
        </p>
      )}
    </section>
  );
}

function clock(ms: number): string {
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** 聞いている間の波形（直近の音の大きさの棒）と経過時間 */
export function VoiceWave({ meter, pending, finishing }: {
  meter: VoiceMeterStore; pending: number; finishing: boolean;
}) {
  const { levels, elapsedMs } = useVoiceMeter(meter);
  return (
    <div data-assistant-voice className="flex min-w-0 flex-1 items-center gap-2 px-1" aria-live="polite">
      <div role="img" aria-label="マイクの音の大きさ" className="flex h-8 min-w-0 flex-1 items-center justify-end gap-[2px] overflow-hidden">
        {levels.slice(-WAVE_BARS).map((level, i) => (
          <span key={i} className={`w-[3px] shrink-0 rounded-full ${finishing ? "bg-zinc-300 dark:bg-zinc-600" : "bg-accent-500 dark:bg-accent-400"}`}
            style={{ height: `${Math.max(3, Math.round(level * 28))}px` }} />
        ))}
      </div>
      <span className="shrink-0 text-xs tabular-nums text-zinc-500">{clock(elapsedMs)}</span>
      {pending > 0 && <span className="shrink-0 animate-pulse text-[11px] text-zinc-500">文字に…</span>}
    </div>
  );
}
