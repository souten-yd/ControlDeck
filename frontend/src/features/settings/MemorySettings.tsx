/** Settings →「AI の記憶」: AI アシスタントの会話をまたぐ記憶の設定と一覧（docs/design-chat-memory.md）。
 *  守りの規則（守られた記憶は秘密の会話の中でしか使わない・外部の AI には既定で入れない）は画面でも説明する。
 *  本文は暗号化して保存している。編集・削除・守るの切り替え・すべて消すは本人の記憶だけ。 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { ConfirmDialog, Skeleton } from "../../components/ui";

interface Prefs { enabled: boolean; creation: "off" | "explicit" | "confirm" | "auto"; use_external: boolean; use_opencode: boolean }
interface Item {
  id: number; text: string; kind: string; kind_label: string; protected: boolean; forced_protected: boolean;
  source_kind: string; source_id: string; created_at: number; last_used_at: number | null; use_count: number;
  pinned: boolean; pending: boolean; conversation_title: string | null; conversation_exists: boolean;
}

const CREATION: [Prefs["creation"], string, string][] = [
  ["off", "作らない", "記憶は自分で足したものだけになります。"],
  ["explicit", "「覚えて」と言ったときだけ", "「覚えて」「覚えておいて」と書いた回だけ、覚えることを抜き出します。"],
  ["confirm", "自動で候補を出し、承認したものだけ残す", "回の終わりに候補を出します。この画面で承認するまでは使いません。"],
  ["auto", "自動で残す", "回の終わりに、長く役立つ事柄を自動で残します。"],
];
const KINDS: [string, string][] = [["preference", "好み"], ["fact", "事実"], ["work", "仕事の文脈"]];
const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString("ja-JP", { dateStyle: "short", timeStyle: "short" }) : "まだ使っていません");
const btn = "min-h-11 rounded-xl border border-zinc-300 px-3 text-xs font-medium hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-600 dark:hover:bg-zinc-800";
const input = "w-full min-w-0 rounded-xl border border-zinc-300 bg-white px-3 py-2 text-base sm:text-sm dark:border-zinc-700 dark:bg-zinc-900";
const card = "rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5";

export function MemorySettings() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const settings = useQuery({ queryKey: ["memory-settings"], queryFn: () => api<{ prefs: Prefs }>("/memory/settings") });
  const items = useQuery({ queryKey: ["memory-items"], queryFn: () => api<{ items: Item[]; limit: number }>("/memory/items") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["memory-items"] });
  const fail = (e: unknown) => show(e instanceof Error ? e.message : "操作できませんでした", "error");
  const save = useMutation({
    mutationFn: (patch: Partial<Prefs>) => api<{ prefs: Prefs }>("/memory/settings", { method: "PUT", json: patch }),
    onSuccess: (data) => qc.setQueryData(["memory-settings"], data),
    onError: fail,
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api(`/memory/items/${id}`, { method: "PATCH", json: body }),
    onSuccess: refresh, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api(`/memory/items/${id}`, { method: "DELETE" }),
    onSuccess: () => { refresh(); show("記憶を消しました"); }, onError: fail,
  });
  const clear = useMutation({
    mutationFn: () => api<{ deleted: number }>("/memory/items", { method: "DELETE" }),
    onSuccess: (r) => { refresh(); show(`記憶を ${r.deleted} 件すべて消しました`); }, onError: fail,
  });
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [adding, setAdding] = useState({ text: "", kind: "fact", protect: false });
  const [confirmClear, setConfirmClear] = useState(false);
  const add = useMutation({
    mutationFn: () => api("/memory/items", { method: "POST", json: { text: adding.text, kind: adding.kind, protected: adding.protect } }),
    onSuccess: () => { setAdding({ text: "", kind: "fact", protect: false }); refresh(); show("記憶を足しました"); }, onError: fail,
  });

  if (settings.isError || items.isError) return <p className="text-sm text-zinc-500">記憶を読み込めませんでした。</p>;
  if (!settings.data || !items.data) return <Skeleton className="h-64 rounded-2xl" />;
  const prefs = settings.data.prefs;
  const list = items.data.items;
  const pending = list.filter((m) => m.pending);
  const active = list.filter((m) => !m.pending);

  return (
    <div className="space-y-6" data-memory-settings>
      <section className={card} aria-labelledby="memory-prefs">
        <h3 id="memory-prefs" className="text-sm font-semibold">使い方</h3>
        <p className="mb-3 mt-1 text-xs leading-relaxed text-zinc-500">
          会話をまたいで覚えておくことです。覚えた内容は暗号化して、この PC だけに保存します。覚えることを抜き出すのは、
          会話の AI がクラウドでも、必ずこの PC のローカルの AI だけで行います（ローカルの AI が無いときは覚えません）。
        </p>
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <input type="checkbox" className="h-5 w-5" checked={prefs.enabled} onChange={(e) => save.mutate({ enabled: e.target.checked })} />
          <span>記憶を使う</span>
        </label>
        <fieldset className="mt-2" disabled={!prefs.enabled}>
          <legend className="mb-1 text-xs text-zinc-500">作り方</legend>
          <div className="space-y-1">
            {CREATION.map(([id, label, note]) => (
              <label key={id} className="flex min-h-11 items-start gap-3 rounded-xl px-1 py-1 text-sm">
                <input type="radio" name="memory-creation" className="mt-1 h-5 w-5 shrink-0" checked={prefs.creation === id} onChange={() => save.mutate({ creation: id })} />
                <span className="min-w-0"><span className="block font-medium">{label}</span><span className="block text-xs text-zinc-500">{note}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="mt-3 space-y-1 border-t border-zinc-100 pt-3 dark:border-zinc-800">
          <label className="flex min-h-11 items-start gap-3 text-sm">
            <input type="checkbox" className="mt-1 h-5 w-5 shrink-0" checked={prefs.use_external} disabled={!prefs.enabled} onChange={(e) => save.mutate({ use_external: e.target.checked })} />
            <span className="min-w-0"><span className="block font-medium">外部の AI の会話にも記憶を使う</span>
              <span className="block text-xs text-zinc-500">切のままなら、クラウドの AI には記憶を送りません。入れても、守られた記憶は送りません。</span></span>
          </label>
          <label className="flex min-h-11 items-start gap-3 text-sm">
            <input type="checkbox" className="mt-1 h-5 w-5 shrink-0" checked={prefs.use_opencode} disabled={!prefs.enabled} onChange={(e) => save.mutate({ use_opencode: e.target.checked })} />
            <span className="min-w-0"><span className="block font-medium">OpenCode（作業場）にも記憶を使う</span>
              <span className="block text-xs text-zinc-500">切のままなら、作業場には記憶を渡しません。入れても、守られた記憶は渡しません。</span></span>
          </label>
        </div>
        <p data-memory-protect-note className="mt-3 rounded-xl bg-zinc-50 p-3 text-xs leading-relaxed text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300">
          🔒 守られた記憶: 秘密の会話・特許の流れから作った記憶と、あなたが「守る」にした記憶です。<strong>秘密の会話の中でしか使いません</strong>。
          Web 検索や外部の AI を使える会話には、本文も件数も出しません。Web 検索の語を作るときと道具の引数にも、記憶は渡しません。
        </p>
      </section>

      {pending.length > 0 && (
        <section className={card} aria-labelledby="memory-pending" data-memory-pending>
          <h3 id="memory-pending" className="text-sm font-semibold">承認待ちの候補（{pending.length} 件）</h3>
          <ul className="mt-2 space-y-2">
            {pending.map((m) => (
              <li key={m.id} className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950/30">
                <p className="break-words [overflow-wrap:anywhere]">{m.text}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button className={btn} onClick={() => patch.mutate({ id: m.id, body: { approve: true } })}>残す</button>
                  <button className={btn} onClick={() => remove.mutate(m.id)}>捨てる</button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className={card} aria-labelledby="memory-list">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 id="memory-list" className="text-sm font-semibold">覚えていること（{active.length} / {items.data.limit} 件）</h3>
          <button className={`${btn} text-red-600 dark:text-red-400`} disabled={list.length === 0} onClick={() => setConfirmClear(true)}>すべて消す</button>
        </div>
        {active.length === 0 ? <p className="mt-2 text-xs text-zinc-400">まだありません。会話で「覚えておいて」と言うか、下から足せます。</p> : (
          <ul className="mt-2 space-y-2">
            {active.map((m) => (
              <li key={m.id} data-memory-item className="min-w-0 rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-700">
                {editing?.id === m.id ? (
                  <div className="space-y-2">
                    <textarea aria-label="記憶の本文" className={input} rows={3} maxLength={400} value={editing.text} onChange={(e) => setEditing({ id: m.id, text: e.target.value })} />
                    <div className="flex gap-2">
                      <button className={btn} disabled={!editing.text.trim()} onClick={() => { patch.mutate({ id: m.id, body: { text: editing.text } }); setEditing(null); }}>保存</button>
                      <button className={btn} onClick={() => setEditing(null)}>やめる</button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="break-words [overflow-wrap:anywhere]">{m.text}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-500">
                      <span className="rounded bg-zinc-100 px-1.5 dark:bg-zinc-800">{m.kind_label}</span>
                      {m.protected && <span data-memory-protected className="rounded bg-emerald-100 px-1.5 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200">🔒 守られた記憶{m.forced_protected ? "（外せません）" : ""}</span>}
                      {m.pinned && <span>📌 固定</span>}
                      <span>最後に使った時: {when(m.last_used_at)}</span>
                      {m.source_id && (m.conversation_exists
                        ? <Link className="text-accent-600 underline dark:text-accent-400" to={`/assistant?conversation=${encodeURIComponent(m.source_id)}`}>作った会話: {m.conversation_title}</Link>
                        : <span>作った会話は消されています</span>)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button className={btn} onClick={() => setEditing({ id: m.id, text: m.text })}>編集</button>
                      <button className={btn} disabled={m.protected && m.forced_protected} onClick={() => patch.mutate({ id: m.id, body: { protected: !m.protected } })}>
                        {m.protected ? "守りを外す" : "守る"}
                      </button>
                      <button className={btn} onClick={() => patch.mutate({ id: m.id, body: { pinned: !m.pinned } })}>{m.pinned ? "固定を外す" : "固定する"}</button>
                      <button className={`${btn} text-red-600 dark:text-red-400`} onClick={() => remove.mutate(m.id)}>削除</button>
                    </div>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        <form className="mt-4 space-y-2 border-t border-zinc-100 pt-3 dark:border-zinc-800" onSubmit={(e) => { e.preventDefault(); if (adding.text.trim()) add.mutate(); }}>
          <label className="block text-xs text-zinc-500">自分で足す
            <textarea className={`${input} mt-1`} rows={2} maxLength={400} placeholder="例: 利用者は回答を箇条書きで短くまとめる文体を好む" value={adding.text} onChange={(e) => setAdding({ ...adding, text: e.target.value })} />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <select aria-label="種類" className="min-h-11 rounded-xl border border-zinc-300 bg-white px-2 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" value={adding.kind} onChange={(e) => setAdding({ ...adding, kind: e.target.value })}>
              {KINDS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
            <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" className="h-5 w-5" checked={adding.protect} onChange={(e) => setAdding({ ...adding, protect: e.target.checked })} />守る</label>
            <button type="submit" className={btn} disabled={!adding.text.trim() || add.isPending}>足す</button>
          </div>
        </form>
      </section>
      {confirmClear && (
        <ConfirmDialog title="記憶をすべて消す" message={`記憶 ${list.length} 件（承認待ちを含む）をすべて消します。元に戻せません。`} confirmLabel="すべて消す"
          onConfirm={() => { setConfirmClear(false); clear.mutate(); }} onClose={() => setConfirmClear(false)} />
      )}
    </div>
  );
}
