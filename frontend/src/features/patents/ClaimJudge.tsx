/** AI クレームチャートの判定（R3-3）。請求項 N × 1 件の文献を構成要件ごとに ○/△/×/? で。引用が原文に無ければ「?」、人が直して確認済みにする。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { patentsApi, type CaseDetail, type JudgedChart, type JudgeRow, type Verdict } from "../../api/patents";
import { useToasts } from "../../stores";
import { IconButton, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { IconTrash } from "../../components/icons";
import { STATUS_LABEL, inputBase } from "./shared";

const VERDICTS: Verdict[] = ["○", "△", "×", "?"];
const VERDICT_HINT: Record<Verdict, string> = { "○": "開示", "△": "一部・示唆", "×": "開示なし", "?": "判断できない" };
const VERDICT_CLASS: Record<Verdict, string> = {
  "○": "bg-accent-600 text-white",
  "△": "bg-accent-100 text-accent-800 dark:bg-accent-900/50 dark:text-accent-200",
  "×": "bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200",
  "?": "border border-dashed border-zinc-400 text-zinc-600 dark:text-zinc-300",
};
const REVIEW_LABEL: Record<string, string> = { unreviewed: "未確認", checked: "確認済み", rejected: "不採用" };

export interface JudgeCandidate { doc_ref: string; title: string }

function Mark({ v }: { v: Verdict }) {
  return <span title={VERDICT_HINT[v]} className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-sm font-bold ${VERDICT_CLASS[v]}`}>{v}</span>;
}

export function ClaimJudge({ c, claimNo, candidates, editable, localAi, onError }: {
  c: CaseDetail; claimNo: number; candidates: JudgeCandidate[]; editable: boolean; localAi: boolean; onError: (e: unknown) => void;
}) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [docRef, setDocRef] = useState("");
  const charts = useQuery({
    queryKey: ["patent-charts", c.id],
    queryFn: () => patentsApi.charts(c.id),
    refetchInterval: (q) => (q.state.data?.charts.some((x) => x.status === "queued" || x.status === "running") ? 3000 : false),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["patent-charts", c.id] });
  const start = useMutation({
    mutationFn: () => patentsApi.judge(c.id, claimNo, docRef || candidates[0]?.doc_ref || ""),
    onSuccess: () => { show("ローカルのモデルで判定を始めました"); refresh(); },
    onError,
  });
  const list = charts.data?.charts ?? [];
  return (
    <section className="space-y-3">
      <h3 className="text-xs font-semibold text-zinc-500">AI で判定（構成要件ごとに ○ △ × ?）</h3>
      {editable && candidates.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <select className={`${inputBase} w-full min-w-0 sm:w-auto sm:flex-1`} value={docRef || candidates[0].doc_ref} onChange={(e) => setDocRef(e.target.value)}
            aria-label="判定する文献">
            {candidates.map((d) => <option key={d.doc_ref} value={d.doc_ref}>{d.title || d.doc_ref}</option>)}
          </select>
          <button className={primaryButtonClass} disabled={!localAi || start.isPending} onClick={() => start.mutate()}>
            請求項 {claimNo} を判定
          </button>
        </div>
      )}
      {editable && candidates.length === 0 && (
        <p className="text-xs text-zinc-500">判定する文献がありません。「クレームチャートを作る」で候補を出すか、文献の節で先行文献を登録してください。</p>
      )}
      {!localAi && <p className="text-xs text-zinc-500">ローカル専用のモデルが使えないため判定できません（案件の内容はクラウドへ送りません）。</p>}
      <ul className="space-y-3">
        {list.map((chart) => <ChartCard key={chart.id} c={c} chart={chart} editable={editable} onChanged={refresh} onError={onError} />)}
      </ul>
    </section>
  );
}

function ChartCard({ c, chart, editable, onChanged, onError }: {
  c: CaseDetail; chart: JudgedChart; editable: boolean; onChanged: () => void; onError: (e: unknown) => void;
}) {
  const review = useMutation({ mutationFn: (s: string) => patentsApi.reviewChart(c.id, chart.id, s), onSuccess: onChanged, onError });
  const remove = useMutation({ mutationFn: () => patentsApi.removeChart(c.id, chart.id), onSuccess: onChanged, onError });
  const rows = chart.rows;
  const final = (r: JudgeRow) => (r.human_verdict || r.verdict) as Verdict;
  const counts = VERDICTS.map((v) => [v, rows.filter((r) => final(r) === v).length] as const).filter(([, n]) => n > 0);
  const demoted = rows.filter((r) => r.verdict !== r.model_verdict).length;
  return (
    <li className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="break-words text-sm font-semibold">請求項 {chart.claim_no} × {chart.doc_title || chart.doc_ref}</p>
          <p className="mt-0.5 text-xs text-zinc-500">
            {chart.status === "succeeded"
              ? <>モデルの推論 · {REVIEW_LABEL[chart.review_status] ?? chart.review_status}{chart.context_mode === "outline" ? " · 文献は要所のみ渡した" : ""}</>
              : STATUS_LABEL[chart.status] ?? chart.status}
          </p>
          {chart.status === "succeeded" && (
            <p className="mt-1.5 flex flex-wrap gap-2">{counts.map(([v, n]) => <span key={v} className="num flex items-center gap-1 text-xs"><Mark v={v} />{n}</span>)}</p>
          )}
        </div>
        {editable && <IconButton label="判定を削除" onClick={() => remove.mutate()}><IconTrash /></IconButton>}
      </div>
      {chart.stale && chart.stale.length > 0 && (
        <p className="mt-2 rounded-lg bg-zinc-100 px-2 py-1 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
          古い判定です: {chart.stale.join("・")}。判定し直してください。
        </p>
      )}
      {chart.status === "failed" && <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{chart.error}</p>}
      {chart.status === "succeeded" && (
        <>
          {demoted > 0 && <p className="mt-2 text-xs text-zinc-500">引用を原文で確かめられなかった {demoted} 件を「?」にしました。</p>}
          <ul className="mt-2 divide-y divide-zinc-100 dark:divide-zinc-800">
            {rows.map((r) => <RowItem key={r.label} c={c} chart={chart} row={r} editable={editable} onChanged={onChanged} onError={onError} />)}
          </ul>
          {editable && (
            <div className="mt-2 flex gap-2">
              <button className={secondaryButtonClass} onClick={() => review.mutate("checked")}>確認済みにする</button>
              <button className={secondaryButtonClass} onClick={() => review.mutate("rejected")}>不採用</button>
            </div>
          )}
        </>
      )}
    </li>
  );
}

function RowItem({ c, chart, row, editable, onChanged, onError }: {
  c: CaseDetail; chart: JudgedChart; row: JudgeRow; editable: boolean; onChanged: () => void; onError: (e: unknown) => void;
}) {
  const set = useMutation({
    mutationFn: (v: string) => patentsApi.setJudgeRow(c.id, chart.id, row.label, v, row.human_note),
    onSuccess: onChanged, onError,
  });
  const shown = (row.human_verdict || row.verdict) as Verdict;
  return (
    <li className="py-2 text-sm">
      <div className="flex items-start gap-2">
        <Mark v={shown} />
        <div className="min-w-0 flex-1">
          <p><span className="mr-1.5 text-xs font-semibold text-accent-600">{row.label}</span>{row.element}</p>
          {row.human_verdict && <p className="text-xs text-zinc-500">人の判定（モデルは {row.model_verdict}）</p>}
          {row.flag && !row.human_verdict && <p className="text-xs text-zinc-500">{row.flag}</p>}
        </div>
        {editable && (
          <select className={`${inputBase} w-16 shrink-0 px-2 sm:w-20`} aria-label={`構成要件 ${row.label} の判定を直す`} value={row.human_verdict}
            onChange={(e) => set.mutate(e.target.value)}>
            <option value="">AI</option>
            {VERDICTS.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        )}
      </div>
      {(row.quote || row.reason) && (
        <details className="ml-8 mt-1 text-xs text-zinc-600 dark:text-zinc-400">
          <summary className="cursor-pointer text-zinc-500">根拠</summary>
          {row.quote && (
            <blockquote className="mt-1 border-l-2 border-zinc-300 pl-2 dark:border-zinc-700">
              {row.quote}
              <span className="ml-1 text-zinc-500">{row.where}{row.quote_verified ? " · 原文で確認" : " · 原文に見つからない"}</span>
            </blockquote>
          )}
          {row.reason && <p className="mt-1">{row.reason}</p>}
        </details>
      )}
    </li>
  );
}
