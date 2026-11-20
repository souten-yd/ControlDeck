/** 公報の書誌（出願人・IPC・日付・請求項の数）。OCR や規則で読み取ったものは「確認が必要」と出し、手で直せる。
 *  直した書誌は分析（/patents の「分析」）の集計に使われる。 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { patentsApi, type Biblio } from "../../api/patents";
import { useAuth, useToasts } from "../../stores";
import { primaryButtonClass, secondaryButtonClass } from "../../components/ui";

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-base sm:text-sm dark:border-zinc-700 dark:bg-zinc-900";
const GUESSED = new Set(["ocr_extract", "rule_extract"]);

const list = (v: string) => v.split(/[、,\n]/).map((s) => s.trim()).filter(Boolean);

export function BiblioBlock({ docRef, biblio }: { docRef: string; biblio: Biblio }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(() => toForm(biblio));
  const save = useMutation({
    mutationFn: () => patentsApi.patchBiblio(docRef, {
      applicants: list(form.applicants), ipc: list(form.ipc),
      filing_date: form.filing_date, publication_date: form.publication_date,
      claims_count: form.claims_count === "" ? null : Number(form.claims_count),
    }),
    onSuccess: () => {
      show("書誌を直しました");
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["knowledge-doc", docRef] });
      qc.invalidateQueries({ queryKey: ["patents-analytics"] });
    },
    onError: (e) => show(e instanceof Error ? e.message : String(e), "error"),
  });
  const guessed = GUESSED.has(biblio.source ?? "");
  const rows: [string, string][] = [
    ["出願人", (biblio.applicants ?? []).join("、")],
    ["IPC", (biblio.ipc ?? []).join("、")],
    ["出願日", biblio.filing_date ?? ""],
    ["公開日", biblio.publication_date ?? ""],
    ["請求項の数", biblio.claims_count != null ? `${biblio.claims_count}` : ""],
  ];
  const set = (key: keyof ReturnType<typeof toForm>) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-xs font-semibold text-zinc-500">書誌</h3>
        {guessed && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">読み取り（確認が必要）</span>}
        <span className="flex-1" />
        {can("workflows.edit") && !editing && (
          <button type="button" className="-my-2 min-h-11 rounded-lg px-3 text-xs text-accent-700 hover:bg-accent-600/10 dark:text-accent-300"
            onClick={() => { setForm(toForm(biblio)); setEditing(true); }}>直す</button>
        )}
      </div>
      {editing ? (
        <form className="grid gap-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/50 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <label className="block text-xs text-zinc-500 sm:col-span-2">出願人（「、」で区切る）
            <input className={`${input} mt-1`} value={form.applicants} onChange={set("applicants")} /></label>
          <label className="block text-xs text-zinc-500 sm:col-span-2">IPC（「、」で区切る。例: H02M 7/48）
            <input className={`${input} mt-1`} value={form.ipc} onChange={set("ipc")} /></label>
          <label className="block text-xs text-zinc-500">出願日
            <input className={`${input} mt-1`} type="date" value={form.filing_date} onChange={set("filing_date")} /></label>
          <label className="block text-xs text-zinc-500">公開日
            <input className={`${input} mt-1`} type="date" value={form.publication_date} onChange={set("publication_date")} /></label>
          <label className="block text-xs text-zinc-500">請求項の数
            <input className={`${input} mt-1`} type="number" min={0} inputMode="numeric" value={form.claims_count} onChange={set("claims_count")} /></label>
          <div className="flex items-end gap-2 sm:col-span-2">
            <button className={`${primaryButtonClass} flex-1 sm:flex-none`} disabled={save.isPending}>保存</button>
            <button type="button" className={`${secondaryButtonClass} flex-1 sm:flex-none`} onClick={() => setEditing(false)}>やめる</button>
          </div>
        </form>
      ) : (
        <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="contents"><dt className="text-xs leading-5 text-zinc-500">{k}</dt><dd className="num min-w-0 break-words">{v || <span className="text-zinc-400">—</span>}</dd></div>
          ))}
        </dl>
      )}
    </section>
  );
}

function toForm(b: Biblio) {
  return {
    applicants: (b.applicants ?? []).join("、"), ipc: (b.ipc ?? []).join("、"),
    filing_date: b.filing_date ?? "", publication_date: b.publication_date ?? "",
    claims_count: b.claims_count != null ? String(b.claims_count) : "",
  };
}
