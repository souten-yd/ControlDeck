/** Patent（特許調査・検討）。案件の段階・期限・請求項の版と構成要件・先行文献（X/Y/A）・クレームチャート・
 *  ローカル専用 AI の支援を 1 つの画面で扱う。PatentBuddy Add-on を本体へ統合したもの（2026-11-21）。
 *  案件の内容は外部へ送らない（AI 支援は Host 管理のローカルモデルだけ）。 */
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { patentsApi, type CaseDetail, type ClaimChart, type PatentMeta, type SearchHit } from "../api/patents";
import { useAuth, useToasts } from "../stores";
import {
  BottomSheet, ConfirmDialog, EmptyState, IconButton, SegmentedTabs, Skeleton, primaryButtonClass, secondaryButtonClass,
} from "../components/ui";
import { IconPlus, IconSearch, IconTrash } from "../components/icons";
import { PageHeader } from "../components/PageHeader";

const MarkdownMessage = lazy(() => import("../features/workflows/MarkdownMessage"));

const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const card = "rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900";
const DATA_FIELDS: [string, string][] = [
  ["field", "技術分野"], ["problem", "課題"], ["solution", "解決手段（構成）"], ["effect", "効果"], ["embodiment", "実施例"],
];
type Tab = "summary" | "deadlines" | "claims" | "refs" | "assist";
const TABS: ReadonlyArray<readonly [Tab, string, string?]> = [
  ["summary", "概要"], ["deadlines", "期限"], ["claims", "請求項"], ["refs", "文献"], ["assist", "AI 支援", "AI"],
];

function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

function DeadlineChip({ days }: { days: number }) {
  const tone = days < 0 ? "bg-amber-600 text-white" : days <= 14 ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
    : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300";
  return <span className={`num shrink-0 rounded-full px-2 py-0.5 text-xs ${tone}`}>{days < 0 ? `${-days} 日超過` : `あと ${days} 日`}</span>;
}

export default function PatentsPage() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [literature, setLiterature] = useState(false);
  const { data: cases, isLoading } = useQuery({ queryKey: ["patents"], queryFn: patentsApi.list });
  const { data: meta } = useQuery({ queryKey: ["patents-meta"], queryFn: patentsApi.meta, staleTime: 30_000 });
  const create = useMutation({
    mutationFn: () => patentsApi.create(title.trim()),
    onSuccess: (c) => {
      setCreating(false);
      setTitle("");
      qc.invalidateQueries({ queryKey: ["patents"] });
      setOpenId(c.id);
    },
    onError: (e) => show(errorText(e), "error"),
  });

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <PageHeader
        title="Patent"
        description="特許の調査・検討。案件の段階と期限、請求項の版と構成要件、先行文献の X/Y/A 分類、クレームチャート、ローカル AI の支援。案件の内容は外部へ送りません。"
        actions={can("workflows.edit") && (
          <IconButton variant="primary" label="案件を作成" onClick={() => setCreating(true)}><IconPlus /></IconButton>
        )}
      />
      {meta && !meta.local_ai_available && (
        <p className="mb-3 rounded-xl bg-zinc-100 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          AI 支援はローカル専用のモデル（Models で選ぶ llama.cpp の GGUF）だけで動きます。いまは使えないため、検索・期限・クレームチャートだけ使えます。
        </p>
      )}
      {isLoading ? <Skeleton className="h-24" /> : !cases || cases.length === 0 ? (
        <EmptyState actions={can("workflows.edit") && (
          <button className={primaryButtonClass} onClick={() => setCreating(true)}>案件を作成</button>
        )}>案件がありません。発明の名称から始めて、課題・構成・請求項を書き足していきます。</EmptyState>
      ) : (
        <ul className="space-y-3">
          {cases.map((c) => (
            <li key={c.id}>
              <button onClick={() => setOpenId(c.id)} className={`${card} flex w-full items-center gap-3 text-left`}>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{c.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-zinc-500">
                    {c.stage_label}{c.next_deadline ? ` · ${c.next_deadline.label}` : ""}
                  </span>
                </span>
                {c.next_deadline && <DeadlineChip days={c.next_deadline.days_left} />}
              </button>
            </li>
          ))}
        </ul>
      )}
      {can("workflows.edit") && (
        <button className={`${secondaryButtonClass} mt-4 w-full`} onClick={() => setLiterature(true)}>公報・論文を取り込む</button>
      )}

      {creating && (
        <BottomSheet title="案件を作成" onClose={() => setCreating(false)}>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (title.trim()) create.mutate(); }}>
            <input className={input} autoFocus placeholder="発明の名称" value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
            <button className={`${primaryButtonClass} w-full`} disabled={!title.trim() || create.isPending}>作成</button>
          </form>
        </BottomSheet>
      )}
      {openId !== null && meta && <CaseSheet id={openId} meta={meta} onClose={() => setOpenId(null)} />}
      {literature && <LiteratureSheet onClose={() => setLiterature(false)} />}
    </div>
  );
}

function CaseSheet({ id, meta, onClose }: { id: number; meta: PatentMeta; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const editable = can("workflows.edit");
  const [tab, setTab] = useState<Tab>("summary");
  const [deleting, setDeleting] = useState(false);
  const { data: c } = useQuery({
    queryKey: ["patent", id],
    queryFn: () => patentsApi.get(id),
    // AI 支援の実行中だけ見に行く
    refetchInterval: (q) => (q.state.data?.assists.some((a) => a.status === "queued" || a.status === "running") ? 3000 : false),
  });
  const set = (next: CaseDetail) => {
    qc.setQueryData(["patent", id], next);
    qc.invalidateQueries({ queryKey: ["patents"] });
  };
  const fail = (e: unknown) => show(errorText(e), "error");
  const remove = useMutation({
    mutationFn: () => patentsApi.remove(id),
    onSuccess: () => { show("削除しました"); qc.invalidateQueries({ queryKey: ["patents"] }); onClose(); },
    onError: fail,
  });
  return (
    <BottomSheet title={c?.title ?? "案件"} onClose={onClose} wide stable headerActions={editable && (
      <IconButton label="案件を削除" onClick={() => setDeleting(true)}><IconTrash /></IconButton>
    )}>
      <SegmentedTabs tabs={TABS} value={tab} onChange={setTab} className="mb-4" />
      {!c ? <Skeleton className="h-40" /> : (
        <>
          {tab === "summary" && <SummaryTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
          {tab === "deadlines" && <DeadlinesTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
          {tab === "claims" && <ClaimsTab c={c} editable={editable} onSaved={set} onError={fail} />}
          {tab === "refs" && <RefsTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
          {tab === "assist" && <AssistTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
        </>
      )}
      {deleting && (
        <ConfirmDialog title="案件を削除しますか？" message="期限・請求項の版・先行文献・AI 支援の記録も削除されます。取り込んだ文献は残ります。取り消せません。"
          confirmLabel="削除する" busy={remove.isPending} onConfirm={() => remove.mutate()} onClose={() => setDeleting(false)} />
      )}
    </BottomSheet>
  );
}

interface TabProps { c: CaseDetail; meta: PatentMeta; editable: boolean; onSaved: (c: CaseDetail) => void; onError: (e: unknown) => void }

function SummaryTab({ c, meta, editable, onSaved, onError }: TabProps) {
  const show = useToasts((s) => s.show);
  const [title, setTitle] = useState(c.title);
  const [stage, setStage] = useState(c.stage);
  const [data, setData] = useState<Record<string, string | boolean>>(c.data);
  useEffect(() => { setTitle(c.title); setStage(c.stage); setData(c.data); }, [c.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => patentsApi.patch(c.id, { title, stage, data }),
    onSuccess: (next) => { onSaved(next); show("保存しました"); },
    onError,
  });
  const stageInfo = meta.stages.find((s) => s.id === stage);
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Field label="発明の名称"><input className={input} value={title} disabled={!editable} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="段階">
        <select className={input} value={stage} disabled={!editable} onChange={(e) => setStage(e.target.value)}>
          {meta.stages.map((s, i) => <option key={s.id} value={s.id}>{i + 1}. {s.label}</option>)}
        </select>
        {stageInfo && <p className="mt-1 text-xs text-zinc-500">{stageInfo.goal}</p>}
      </Field>
      {DATA_FIELDS.map(([key, label]) => (
        <Field key={key} label={label}>
          <textarea className={`${input} min-h-20`} value={String(data[key] ?? "")} disabled={!editable}
            onChange={(e) => setData({ ...data, [key]: e.target.value })} />
        </Field>
      ))}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={Boolean(data.foreign_resident)} disabled={!editable}
          onChange={(e) => setData({ ...data, foreign_resident: e.target.checked })} />
        在外者（拒絶理由への応答期限を 3 か月で数える）
      </label>
      {editable && <button className={`${primaryButtonClass} w-full`} disabled={save.isPending}>保存</button>}
    </form>
  );
}

function DeadlinesTab({ c, meta, editable, onSaved, onError }: TabProps) {
  const [kind, setKind] = useState("filing");
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  const add = useMutation({
    mutationFn: () => patentsApi.addEvent(c.id, { kind, date, note }),
    onSuccess: (next) => { onSaved(next); setDate(""); setNote(""); },
    onError,
  });
  const removeEvent = useMutation({ mutationFn: (eventId: number) => patentsApi.removeEvent(c.id, eventId), onSuccess: onSaved, onError });
  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 text-xs font-semibold text-zinc-500">期限の目安</h3>
        {c.deadlines.length === 0 ? <p className="text-sm text-zinc-500">出願日などの出来事を記録すると、期限の目安を出します。</p> : (
          <ul className="space-y-2">
            {c.deadlines.map((d) => (
              <li key={d.key} className="flex items-start gap-2 text-sm">
                <span className="min-w-0 flex-1"><span className="block">{d.label}</span>
                  <span className="num block text-xs text-zinc-500">{d.due} · {d.basis}</span></span>
                <DeadlineChip days={d.days_left} />
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-zinc-500">目安です。正式な期限は特許庁の通知・弁理士に確認してください。</p>
      </section>
      <section>
        <h3 className="mb-2 text-xs font-semibold text-zinc-500">出来事</h3>
        <ul className="space-y-1">
          {c.events.map((e) => (
            <li key={e.id} className="flex items-center gap-2 text-sm">
              <span className="num shrink-0 text-xs text-zinc-500">{e.date}</span>
              <span className="min-w-0 flex-1 truncate">{meta.event_kinds[e.kind] ?? e.kind}{e.note ? ` — ${e.note}` : ""}</span>
              {editable && <IconButton label="削除" onClick={() => removeEvent.mutate(e.id)}><IconTrash /></IconButton>}
            </li>
          ))}
        </ul>
        {editable && (
          <form className="mt-3 grid gap-2 sm:grid-cols-[1fr_9rem]" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <select className={input} value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(meta.event_kinds).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
            <input className={input} type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
            <input className={`${input} sm:col-span-2`} placeholder="メモ（任意）" value={note} onChange={(e) => setNote(e.target.value)} />
            <button className={`${secondaryButtonClass} sm:col-span-2`} disabled={!date || add.isPending}>出来事を記録</button>
          </form>
        )}
      </section>
    </div>
  );
}

function ClaimsTab({ c, editable, onSaved, onError }: Omit<TabProps, "meta">) {
  const show = useToasts((s) => s.show);
  const latest = c.claims[0]?.text ?? String(c.data.claims_draft ?? "");
  const [text, setText] = useState(latest);
  const [chart, setChart] = useState<ClaimChart | null>(null);
  const [claimNo, setClaimNo] = useState(1);
  useEffect(() => setText(latest), [c.claims[0]?.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => patentsApi.saveClaims(c.id, text),
    onSuccess: (next) => { onSaved(next); show(`第 ${next.claims[0]?.version ?? 1} 版として保存しました`); },
    onError,
  });
  const runChart = useMutation({ mutationFn: () => patentsApi.claimChart(c.id, claimNo), onSuccess: setChart, onError });
  return (
    <div className="space-y-4">
      <section>
        <textarea className={`${input} min-h-48 font-mono text-xs`} value={text} disabled={!editable} onChange={(e) => setText(e.target.value)}
          placeholder={"【請求項１】\n…を備え、…ことを特徴とする…。"} />
        <div className="mt-2 flex items-center gap-2">
          <span className="num flex-1 text-xs text-zinc-500">{c.claims.length ? `第 ${c.claims[0].version} 版（全 ${c.claims.length} 版）` : "未保存"}</span>
          {editable && <button className={secondaryButtonClass} disabled={!text.trim() || save.isPending} onClick={() => save.mutate()}>新しい版で保存</button>}
        </div>
      </section>
      {c.claim_elements.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold text-zinc-500">構成要件（最新版）</h3>
          <div className="space-y-3">
            {c.claim_elements.map((claim) => (
              <div key={claim.no} className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/50">
                <p className="mb-1 text-xs font-semibold">請求項 {claim.no}</p>
                <ul className="space-y-1 text-sm">
                  {claim.elements.map((el, i) => (
                    <li key={i} className="flex gap-2"><span className="w-8 shrink-0 text-xs font-semibold text-accent-600">{el.label}</span><span className="min-w-0">{el.text}</span></li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <select className={`${input} w-28`} value={claimNo} onChange={(e) => setClaimNo(Number(e.target.value))}>
              {c.claim_elements.map((cl) => <option key={cl.no} value={cl.no}>請求項 {cl.no}</option>)}
            </select>
            <button className={`${secondaryButtonClass} flex-1`} disabled={runChart.isPending} onClick={() => runChart.mutate()}>
              {runChart.isPending ? "探しています…" : "クレームチャートを作る"}
            </button>
          </div>
        </section>
      )}
      {chart && (
        <section className="space-y-2">
          <p className="text-xs text-zinc-500">構成要件ごとにナレッジ（一般・特許・規格）を探した候補です。開示しているかは原文で確かめてください。</p>
          {chart.rows.map((row) => (
            <div key={row.element.label} className="rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-800">
              <p className="font-semibold"><span className="mr-2 text-accent-600">{row.element.label}</span>{row.element.text}</p>
              {row.evidence.length === 0 ? <p className="mt-1 text-xs text-zinc-500">候補なし</p> : (
                <ul className="mt-2 space-y-2">
                  {row.evidence.map((ev) => (
                    <li key={ev.doc_ref + ev.snippet.slice(0, 20)} className="text-xs">
                      <span className="font-medium">{ev.title || ev.doc_ref}</span><span className="text-zinc-500"> · {ev.collection}</span>
                      <p className="mt-0.5 line-clamp-3 text-zinc-600 dark:text-zinc-400">{ev.snippet}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

function RefsTab({ c, meta, editable, onSaved, onError }: TabProps) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [category, setCategory] = useState("A");
  const [manual, setManual] = useState("");
  const search = useMutation({ mutationFn: () => patentsApi.search(query), onSuccess: (r) => setHits(r.results), onError });
  const add = useMutation({
    mutationFn: (body: { ref?: string; doc_ref?: string; relevance?: string }) => patentsApi.addRef(c.id, { ...body, category }),
    onSuccess: (next) => { onSaved(next); setManual(""); },
    onError,
  });
  const removeRef = useMutation({ mutationFn: (refId: number) => patentsApi.removeRef(c.id, refId), onSuccess: onSaved, onError });
  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 text-xs font-semibold text-zinc-500">登録した先行文献</h3>
        {c.refs.length === 0 ? <p className="text-sm text-zinc-500">まだありません。</p> : (
          <ul className="space-y-2">
            {c.refs.map((r) => (
              <li key={r.id} className="flex items-start gap-2 text-sm">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent-600/15 text-xs font-bold text-accent-700 dark:text-accent-300"
                  title={meta.ref_categories[r.category]}>{r.category}</span>
                <span className="min-w-0 flex-1"><span className="block truncate">{r.ref || r.doc_ref}</span>
                  {r.relevance && <span className="block text-xs text-zinc-500">{r.relevance}</span>}</span>
                {editable && <IconButton label="削除" onClick={() => removeRef.mutate(r.id)}><IconTrash /></IconButton>}
              </li>
            ))}
          </ul>
        )}
      </section>
      {editable && (
        <label className="block text-xs text-zinc-500">追加するときの分類
          <select className={`${input} mt-1`} value={category} onChange={(e) => setCategory(e.target.value)}>
            {Object.entries(meta.ref_categories).map(([k, label]) => <option key={k} value={k}>{k}: {label}</option>)}
          </select>
        </label>
      )}
      <section>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim()) search.mutate(); }}>
          <input className={input} placeholder="ナレッジを検索（一般・特許・規格）" value={query} onChange={(e) => setQuery(e.target.value)} />
          <IconButton label="検索" variant="primary" type="submit" disabled={!query.trim() || search.isPending}><IconSearch /></IconButton>
        </form>
        {hits && (hits.length === 0 ? <p className="mt-2 text-sm text-zinc-500">見つかりませんでした。</p> : (
          <ul className="mt-3 space-y-2">
            {hits.map((h) => (
              <li key={h.chunk_ref} className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <div className="flex items-start gap-2">
                  <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{h.title || h.doc_ref}</span>
                    <span className="text-zinc-500">{h.collection}{h.sensitivity === "public" ? " · 公開" : ""}</span></span>
                  {editable && (
                    <button className="shrink-0 rounded-lg px-2 py-1 text-accent-700 hover:bg-accent-600/10 dark:text-accent-300"
                      onClick={() => add.mutate({ doc_ref: h.doc_ref, ref: h.title, relevance: h.snippet.slice(0, 200) })}>{category} で追加</button>
                  )}
                </div>
                <p className="mt-1 line-clamp-3 text-zinc-600 dark:text-zinc-400">{h.snippet}</p>
              </li>
            ))}
          </ul>
        ))}
      </section>
      {editable && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (manual.trim()) add.mutate({ ref: manual.trim() }); }}>
          <input className={input} placeholder="文献番号を直接入れる（例: 特開2026-116114）" value={manual} onChange={(e) => setManual(e.target.value)} />
          <button className={secondaryButtonClass} disabled={!manual.trim() || add.isPending}>追加</button>
        </form>
      )}
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  queued: "順番待ち", running: "作成中", succeeded: "完了", failed: "失敗", canceled: "取り消し", interrupted: "中断",
};

function AssistTab({ c, meta, editable, onSaved, onError }: TabProps) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const suggested = c.stage_info?.ai ?? [];
  const [task, setTask] = useState(suggested[0] ?? "interview");
  const [extra, setExtra] = useState("");
  const run = useMutation({
    mutationFn: () => patentsApi.assist(c.id, task, extra),
    onSuccess: () => { show("ローカルのモデルで作成を始めました"); qc.invalidateQueries({ queryKey: ["patent", c.id] }); },
    onError,
  });
  const review = useMutation({
    mutationFn: ({ assistId, status }: { assistId: number; status: string }) => patentsApi.review(c.id, assistId, status),
    onSuccess: onSaved, onError,
  });
  const removeAssist = useMutation({ mutationFn: (assistId: number) => patentsApi.removeAssist(c.id, assistId), onSuccess: onSaved, onError });
  const tasks = Object.entries(meta.tasks).sort(([a], [b]) => Number(!suggested.includes(a)) - Number(!suggested.includes(b)));
  return (
    <div className="space-y-4">
      {!meta.local_ai_available && (
        <p className="rounded-xl bg-zinc-100 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          ローカル専用のモデルが使えないため実行できません。案件の内容はクラウドへ送りません。
        </p>
      )}
      {editable && (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
          <select className={input} value={task} onChange={(e) => setTask(e.target.value)}>
            {tasks.map(([k, label]) => <option key={k} value={k}>{suggested.includes(k) ? `★ ${label}` : label}</option>)}
          </select>
          <textarea className={`${input} min-h-16`} placeholder="追加の指示（拒絶理由通知の本文など、任意）" value={extra} onChange={(e) => setExtra(e.target.value)} />
          <button className={`${primaryButtonClass} w-full`} disabled={!meta.local_ai_available || run.isPending}>ローカル AI で作成</button>
          <p className="text-xs text-zinc-500">★ は今の段階で使う支援。結果はモデルの推論で、原文と照合するまで未確認として扱います。</p>
        </form>
      )}
      <ul className="space-y-3">
        {c.assists.map((a) => (
          <li key={a.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">{a.label}</span>
              <span className="shrink-0 text-xs text-zinc-500">{STATUS_LABEL[a.status] ?? a.status}</span>
              {editable && <IconButton label="記録を削除" onClick={() => removeAssist.mutate(a.id)}><IconTrash /></IconButton>}
            </div>
            {a.status === "succeeded" && (
              <>
                <p className="mt-1 text-xs text-zinc-500">
                  モデルの推論 · {a.review_status === "checked" ? "確認済み" : a.review_status === "rejected" ? "不採用" : "未確認"}
                </p>
                <div className="prose prose-sm mt-2 max-w-none dark:prose-invert">
                  <Suspense fallback={<Skeleton className="h-16" />}><MarkdownMessage text={a.answer} /></Suspense>
                </div>
                {a.evidence.length > 0 && (
                  <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-xs text-zinc-500">
                    {a.evidence.map((e, i) => <li key={i}>{e.title || e.doc_ref}（{e.collection}）</li>)}
                  </ol>
                )}
                {editable && (
                  <div className="mt-2 flex gap-2">
                    <button className={secondaryButtonClass} onClick={() => review.mutate({ assistId: a.id, status: "checked" })}>確認済みにする</button>
                    <button className={secondaryButtonClass} onClick={() => review.mutate({ assistId: a.id, status: "rejected" })}>不採用</button>
                  </div>
                )}
              </>
            )}
            {a.status === "failed" && <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{a.error}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function LiteratureSheet({ onClose }: { onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [isPublic, setPublic] = useState(false);
  const add = useMutation({
    mutationFn: () => patentsApi.addLiterature({ title, text, url, public: isPublic }),
    onSuccess: () => { show("取り込みを始めました（Jobs で進み具合を見られます）"); onClose(); },
    onError: (e) => show(errorText(e), "error"),
  });
  return (
    <BottomSheet title="公報・論文を取り込む" onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className={input} placeholder="題名・公報番号" value={title} onChange={(e) => setTitle(e.target.value)} />
        <input className={input} placeholder="URL（本文を貼る場合は空のまま）" value={url} onChange={(e) => setUrl(e.target.value)} />
        <textarea className={`${input} min-h-32`} placeholder="本文" value={text} onChange={(e) => setText(e.target.value)} />
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={isPublic} onChange={(e) => setPublic(e.target.checked)} />
          <span>公開済みの公報・論文（クラウドのモデルの横断検索にも出してよい）。社内資料・未公開の内容なら外したまま</span>
        </label>
        <button className={`${primaryButtonClass} w-full`} disabled={(!text.trim() && !url.trim()) || add.isPending}>取り込む</button>
      </form>
    </BottomSheet>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block text-xs font-medium text-zinc-500">{label}<div className="mt-1 text-sm font-normal text-zinc-900 dark:text-zinc-100">{children}</div></label>;
}
