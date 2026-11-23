/** Settings →「API キー」: 外部のサービスのキーを 1 か所で（2026-11-21 利用者「キーを設定の中にまとめて。今あるキーは消さない」）。
 *  キーの置き場は変えず、各置き場（クラウドの AI・特許の公開データ）を読み書きする。値は表示しない。
 *  Hugging Face と Workflow のシークレットは出さない（利用者「設定不要」）。 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { ConfirmDialog, Skeleton } from "../../components/ui";

interface KeyField { name: string; label: string; configured: boolean; plain?: boolean }
interface KeyItem {
  id: string; group: string; name: string; note: string; signup: string; configured: boolean; hint: string;
  fields: KeyField[]; used_by: string; manage: string; enabled?: boolean; testable?: boolean;
}
interface KeysState { groups: { id: string; label: string; description: string }[]; keys: KeyItem[] }

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-base sm:text-sm dark:border-zinc-700 dark:bg-zinc-900";
const btn = "min-h-10 rounded-xl px-3.5 text-xs font-medium disabled:opacity-40";

export function KeysSettings() {
  const { data, isError } = useQuery({ queryKey: ["settings-keys"], queryFn: () => api<KeysState>("/settings/keys") });
  if (isError) return <p className="text-sm text-zinc-500">キーの一覧を読み込めませんでした。</p>;
  if (!data) return <Skeleton className="h-64 rounded-2xl" />;
  return (
    <div className="space-y-6">
      <p className="text-xs leading-relaxed text-zinc-500">
        外部のサービスのキーをここでまとめて管理します。キーはこの PC に暗号化して保存し、画面には出しません（設定済みか・末尾の数文字だけ）。
        これまで各画面で入れたキーも、そのままここに出ます。
      </p>
      {data.groups.map((g) => {
        const items = data.keys.filter((k) => k.group === g.id);
        return (
          <section key={g.id} aria-labelledby={`keys-${g.id}`} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
            <h3 id={`keys-${g.id}`} className="text-sm font-semibold">{g.label}</h3>
            <p className="mb-3 text-xs text-zinc-500">{g.description}</p>
            {items.length === 0 ? <p className="text-xs text-zinc-400">まだありません。</p> : (
              <ul className="space-y-2">{items.map((k) => <KeyCard key={k.id} item={k} />)}</ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function KeyCard({ item }: { item: KeyItem }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [values, setValues] = useState<Record<string, string>>({});
  const [open, setOpen] = useState(false);
  const [removing, setRemoving] = useState(false);
  const changed = Object.values(values).some((v) => v.trim());
  const done = (next: KeysState) => { qc.setQueryData(["settings-keys"], next); qc.invalidateQueries({ queryKey: ["patent-sources"] }); qc.invalidateQueries({ queryKey: ["cloud-state"] }); };
  const save = useMutation({
    mutationFn: () => api<KeysState>(`/settings/keys/${encodeURIComponent(item.id)}`, { method: "PUT", json: { values } }),
    onSuccess: (next) => { done(next); setValues({}); setOpen(false); show(`${item.name} のキーを保存しました`); },
    onError: (e) => show(e instanceof Error ? e.message : "保存できませんでした", "error"),
  });
  const remove = useMutation({
    mutationFn: () => api<KeysState>(`/settings/keys/${encodeURIComponent(item.id)}`, { method: "DELETE" }),
    onSuccess: (next) => { done(next); setRemoving(false); show(`${item.name} のキーを削除しました`); },
    onError: (e) => show(e instanceof Error ? e.message : "削除できませんでした", "error"),
  });
  const [probe, setProbe] = useState<{ ok: boolean; message: string } | null>(null);
  const test = useMutation({
    mutationFn: () => api<{ ok: boolean; message: string }>(`/settings/keys/${encodeURIComponent(item.id)}/test`, { method: "POST" }),
    onSuccess: setProbe,
    onError: (e) => setProbe({ ok: false, message: e instanceof Error ? e.message : "確かめられませんでした" }),
  });
  const partial = item.fields.length > 1 && item.fields.some((f) => f.configured) && !item.configured;
  return (
    <li className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 text-sm font-semibold">
            {item.name}
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-normal ${item.configured ? "bg-accent-600/15 text-accent-700 dark:text-accent-300" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"}`}>
              {item.configured ? `設定済み${item.hint ? ` ${item.hint}` : ""}` : partial ? "一部だけ設定済み" : "未設定"}
            </span>
            {item.enabled === false && item.configured && <span className="text-[11px] font-normal text-zinc-500">（使う設定はオフ）</span>}
          </p>
          <p className="text-xs text-zinc-500">{item.note}</p>
        </div>
        <button onClick={() => setOpen(!open)} aria-expanded={open}
          className={`${btn} shrink-0 border border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800`}>
          {item.configured ? "変更" : "設定"}
        </button>
      </div>
      {open && (
        <form className="mt-3 space-y-2 border-t border-zinc-100 pt-3 dark:border-zinc-800" autoComplete="off"
          onSubmit={(e) => { e.preventDefault(); if (changed) save.mutate(); }}>
          {item.fields.map((f) => (
            <label key={f.name} className="block text-xs font-medium text-zinc-500">
              {f.label}{f.configured && <span className="ml-1 font-normal">（設定済み・変えるときだけ入れる）</span>}
              <input className={`${input} mt-1`} type={f.name.endsWith("_url") ? "url" : f.plain ? "text" : "password"} autoComplete="new-password"
                value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
            </label>
          ))}
          <p className="text-[11px] text-zinc-500">使う場所: {item.used_by}{" "}
            {item.manage && <Link to={item.manage} className="text-accent-700 underline dark:text-accent-300">開く</Link>}
            {item.signup && <> ・ <a href={item.signup} target="_blank" rel="noreferrer" className="text-accent-700 underline dark:text-accent-300">キーの取得・登録の案内</a></>}
          </p>
          <div className="flex flex-wrap gap-2">
            <button className={`${btn} bg-accent-600 text-white hover:bg-accent-700`} disabled={!changed || save.isPending}>保存</button>
            {(item.configured || partial) && (
              <button type="button" onClick={() => setRemoving(true)}
                className={`${btn} text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40`}>削除</button>
            )}
            {item.testable && item.configured && (
              <button type="button" onClick={() => { setProbe(null); test.mutate(); }} disabled={test.isPending}
                className={`${btn} border border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800`}>
                {test.isPending ? "確かめています…" : "つながりを確かめる"}
              </button>
            )}
          </div>
          {probe && (
            <p role="status" className={`text-xs ${probe.ok ? "text-accent-700 dark:text-accent-300" : "text-red-600"}`}>{probe.message}</p>
          )}
        </form>
      )}
      {removing && (
        <ConfirmDialog title={`${item.name} のキーを削除しますか？`} message="保存してあるキーを消します。使っている機能は、入れ直すまで使えなくなります。"
          confirmLabel="削除する" busy={remove.isPending} onConfirm={() => remove.mutate()} onClose={() => setRemoving(false)} />
      )}
    </li>
  );
}
