/** 規格（JIS・IEC・ISO など）。登録する前に、本文を保存できるか・AI に使えるかを利用条件から判定して見せる。
 *  判定の理由（規約の条項）は「理由」に隠し、必要なときだけ開く。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  knowledgeApi, AI_USE_LABEL, STATUS_LABEL, STORAGE_LABEL, type StandardHit, type StandardInput,
} from "../../api/knowledge";
import { BottomSheet, EmptyState, IconButton, Skeleton, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { FilePicker } from "../../components/FilePicker";
import { IconPlus, IconSearch } from "../../components/icons";
import { useAuth, useToasts } from "../../stores";
import { DocumentSheet } from "./DocumentSheet";

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const INSTRUMENT_LABEL: Record<string, string> = {
  purchase: "購入した（JSA・ISO・IEC などのストア）",
  free_view: "無料の閲覧サービスで見た（JISC など）",
  open_publication: "自由に配布されている（ITU-T・RFC など）",
  organisation_contract: "会社・組織の契約で使っている",
  written_permission: "発行元から書面で許可を得た",
  public_law: "法令・告示・通達（技術基準の解釈など）",
  user_authored: "自分で書いたメモ（本文の転記なし）",
};

export function StandardsPanel() {
  const can = useAuth((s) => s.can);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<StandardHit[] | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["standards"], queryFn: knowledgeApi.standards });
  const search = useMutation({ mutationFn: () => knowledgeApi.searchStandards(query.trim()), onSuccess: (r) => setHits(r.results) });
  const rows = data?.standards ?? [];
  return (
    <div className="space-y-4">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim()) search.mutate(); }}>
        <input className={input} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="規格番号・条項の言葉で探す" />
        <IconButton label="探す" variant="primary" type="submit" disabled={!query.trim()}><IconSearch /></IconButton>
        {can("workflows.edit") && <IconButton label="規格を登録" onClick={() => setAdding(true)}><IconPlus /></IconButton>}
      </form>
      {hits && (hits.length === 0 ? <p className="text-sm text-zinc-500">見つかりませんでした（旧版は出しません）。</p> : (
        <ul className="space-y-2">
          {hits.map((h) => (
            <li key={h.ref} className="rounded-2xl border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
              <button className="w-full text-left" onClick={() => setOpen(h.ref)}>
                <span className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold">{h.designation} {h.title}</span>
                  {h.exact && <span className="shrink-0 rounded-full bg-accent-600/15 px-2 py-0.5 text-[11px] text-accent-700 dark:text-accent-300">番号が一致</span>}
                </span>
                {h.clauses.length === 0 && !h.fulltext && <span className="mt-1 block text-xs text-zinc-500">本文は保存していません（番号と題名だけ）</span>}
                {h.clauses.map((c, i) => (
                  <span key={i} className="mt-1 block text-xs text-zinc-600 dark:text-zinc-400">
                    {c.clause && <span className="mr-1 font-semibold text-accent-600">{c.clause} {c.heading}</span>}{c.snippet}
                  </span>
                ))}
              </button>
            </li>
          ))}
        </ul>
      ))}
      {isLoading ? <Skeleton className="h-20" /> : rows.length === 0 ? (
        <EmptyState actions={can("workflows.edit") && <button className={primaryButtonClass} onClick={() => setAdding(true)}>規格を登録</button>}>
          規格がありません。番号と入手のしかたを入れると、本文を保存できるか・AI に使えるかを判定して登録します。
        </EmptyState>
      ) : (
        <ul className="space-y-2">
          {rows.map((s) => (
            <li key={s.ref}>
              <button onClick={() => setOpen(s.ref)} className="flex w-full items-center gap-3 rounded-2xl border border-zinc-200 bg-white p-3 text-left shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{s.designation}{s.edition && !s.designation.includes(s.edition) ? `:${s.edition}` : ""}</span>
                  <span className="block truncate text-xs text-zinc-500">{s.title}</span>
                </span>
                <span className="shrink-0 text-right text-[11px] text-zinc-500">
                  <span className={`block ${s.status === "superseded" ? "text-amber-700 dark:text-amber-300" : ""}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
                  <span className="block">{AI_USE_LABEL[s.ai_use] ?? s.ai_use}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding && data && <AddStandardSheet instruments={Object.keys(data.instruments)} onClose={() => setAdding(false)} />}
      {open && <DocumentSheet docRef={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

const EMPTY: StandardInput = {
  designation: "", title: "", edition: "", instrument: "purchase", text: "", pdf_path: "", permission_ref: "",
  reviewer: "", reviewed_on: "", granted_storage: null, granted_ai_use: null,
};

function AddStandardSheet({ instruments, onClose }: { instruments: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [form, setForm] = useState<StandardInput>(EMPTY);
  const [picking, setPicking] = useState(false);
  const [why, setWhy] = useState(false);
  const contract = form.instrument === "organisation_contract" || form.instrument === "written_permission";
  const set = (patch: Partial<StandardInput>) => setForm((f) => ({ ...f, ...patch }));
  const decision = useMutation({ mutationFn: () => knowledgeApi.decide(form) });
  useEffect(() => {
    if (form.designation.trim().length < 3) return;
    const t = setTimeout(() => decision.mutate(), 400);
    return () => clearTimeout(t);
  }, [form.designation, form.instrument, form.permission_ref, form.reviewer, form.reviewed_on, form.granted_storage, form.granted_ai_use]); // eslint-disable-line react-hooks/exhaustive-deps
  const canStore = decision.data?.storage === "shared_server";
  const add = useMutation({
    mutationFn: () => knowledgeApi.addStandard(form),
    onSuccess: () => { show("登録を始めました"); setTimeout(() => qc.invalidateQueries({ queryKey: ["standards"] }), 1500); onClose(); },
    onError: (e) => show(e instanceof Error ? e.message : String(e), "error"),
  });
  return (
    <BottomSheet title="規格を登録" onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className={input} placeholder="規格番号（例: JIS C 61000-4-2:2012）" value={form.designation} onChange={(e) => set({ designation: e.target.value })} required />
        <input className={input} placeholder="題名" value={form.title} onChange={(e) => set({ title: e.target.value })} />
        <label className="block text-xs text-zinc-500">どうやって手に入れましたか
          <select className={`${input} mt-1`} value={form.instrument} onChange={(e) => set({ instrument: e.target.value })}>
            {instruments.map((k) => <option key={k} value={k}>{INSTRUMENT_LABEL[k] ?? k}</option>)}
          </select>
        </label>
        {contract && (
          <div className="grid gap-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/50 sm:grid-cols-2">
            <input className={input} placeholder="契約・許可の番号" value={form.permission_ref} onChange={(e) => set({ permission_ref: e.target.value })} />
            <input className={input} placeholder="確認した人" value={form.reviewer} onChange={(e) => set({ reviewer: e.target.value })} />
            <input className={input} type="date" value={form.reviewed_on} onChange={(e) => set({ reviewed_on: e.target.value })} />
            <select className={input} value={form.granted_ai_use ?? ""} onChange={(e) => set({ granted_ai_use: e.target.value || null, granted_storage: e.target.value ? "shared_server" : null })}>
              <option value="">契約で認められた AI の使い方</option>
              <option value="local_only">ローカルの AI だけ</option>
              <option value="allowed">クラウドの AI も可</option>
              <option value="prohibited">AI には使わない</option>
            </select>
          </div>
        )}
        {decision.data && (
          <div className="rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <p>{STORAGE_LABEL[decision.data.storage] ?? decision.data.storage} · {AI_USE_LABEL[decision.data.ai_use] ?? decision.data.ai_use}</p>
            <button type="button" className="mt-1 text-xs text-zinc-500 hover:underline" onClick={() => setWhy(!why)}>{why ? "理由を閉じる" : "理由"}</button>
            {why && <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-zinc-600 dark:text-zinc-400">{decision.data.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
          </div>
        )}
        {canStore && (
          <>
            <textarea className={`${input} min-h-28`} placeholder="本文（貼り付け）" value={form.text} onChange={(e) => set({ text: e.target.value })} />
            <button type="button" className={`${secondaryButtonClass} w-full`} onClick={() => setPicking(true)}>
              {form.pdf_path ? `PDF: ${form.pdf_path.split("/").pop()}` : "PDF を選ぶ"}
            </button>
          </>
        )}
        <button className={`${primaryButtonClass} w-full`} disabled={!form.designation.trim() || add.isPending}>登録</button>
      </form>
      {picking && <FilePicker mode="file" title="規格の PDF" onSelect={(p) => { set({ pdf_path: p }); setPicking(false); }} onClose={() => setPicking(false)} />}
    </BottomSheet>
  );
}
