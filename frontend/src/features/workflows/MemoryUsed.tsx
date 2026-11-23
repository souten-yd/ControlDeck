/** 答えの下の「記憶を n 件使いました」。開くと使った記憶の一覧（本文は /memory/used が会話の権限を見て返す。
 *  秘密でない会話では守られた記憶は数にも出ない: 件数はサーバーが数えた normal だけ）。 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

interface UsedItem { id: number; text: string; kind_label: string; protected: boolean }

export function MemoryUsed({ conversationId, count, ids, created }: {
  conversationId: string; count: number; ids: number[]; created?: { created: number; pending: number };
}) {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["memory-used", conversationId, ids.join(",")],
    queryFn: () => api<{ items: UsedItem[] }>(`/memory/used?conv_id=${encodeURIComponent(conversationId)}&ids=${ids.join(",")}`),
    enabled: open && !!conversationId && ids.length > 0,
  });
  const made = created && (created.created > 0 || created.pending > 0);
  if (count <= 0 && !made) return null;
  return (
    <div data-memory-used className="min-w-0 px-1 text-[11px] text-zinc-500 dark:text-zinc-400">
      {count > 0 && (
        <>
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
            className="inline-flex min-h-8 items-center gap-1 rounded-lg px-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <span aria-hidden>🧠</span>記憶を {count} 件使いました<span aria-hidden>{open ? "▲" : "▼"}</span>
          </button>
          {open && (
            <ul data-memory-used-list className="mt-1 space-y-1 rounded-xl border border-zinc-200 bg-white p-2 dark:border-zinc-700 dark:bg-zinc-900">
              {(data?.items ?? []).map((m) => (
                <li key={m.id} className="min-w-0 break-words [overflow-wrap:anywhere]">
                  <span className="mr-1 rounded bg-zinc-100 px-1 dark:bg-zinc-800">{m.kind_label}</span>
                  {m.protected && <span className="mr-1" title="守られた記憶（秘密の会話の中だけで使います）">🔒</span>}
                  {m.text}
                </li>
              ))}
              {data && data.items.length === 0 && <li>この記憶は、すでに消されています。</li>}
              {!data && <li>読み込み中…</li>}
            </ul>
          )}
        </>
      )}
      {made && (
        <p role="status" data-memory-created className="min-h-6">
          <span aria-hidden>📝</span>{created!.created > 0 && ` 記憶に残しました（${created!.created} 件）`}
          {created!.pending > 0 && ` 記憶の候補が ${created!.pending} 件あります（設定の「AI の記憶」で承認できます）`}
        </p>
      )}
    </div>
  );
}
