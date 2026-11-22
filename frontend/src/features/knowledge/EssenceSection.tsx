/** 知識エッセンス（K1b）: 文献からローカル専用の AI が抜き出した技術知識を、種類ごとのカードで見る。
 *  各カードは要約・引用（折りたたみ。開くと原文からの引用）・「AI の推論（未確認）」の印・レビューの状態を出し、
 *  権限のある利用者が承認・却下・差し戻し（メモは任意）で確かめる。レビューは追記だけなので、
 *  前回のレビュー（誰がいつどうした）は折りたたみで見られる（API が返すのは最新の 1 件だけ）。
 *  原文の版が変わった行は stale の印を出し、前の承認は効かないことを明示する。
 *  K3: 承認済みの知識は「別の文献の知識と結ぶ」（技術仮説のリンク）の元になれる。
 *  手本: DocumentSheet の「AI の分析」（節の形・権限の出し分け）と ClaimJudge（行ごとの確認・Job の見せ方）。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { knowledgeApi, ESSENCE_DECISION_LABEL, type EssenceItem, type EssenceRow } from "../../api/knowledge";
import { primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";
import { JobProgress } from "./JobProgress";
import { LinkMaker } from "./LinkMaker";

/** 行（1 回の抽出）の状態。succeeded は内容があるので一覧側で扱う。 */
const ROW_STATUS_LABEL: Record<string, string> = {
  queued: "順番待ち", running: "抽出中", failed: "失敗", canceled: "取り消し", interrupted: "中断",
};
const REVIEW_STATUS_LABEL: Record<string, string> = {
  unreviewed: "未確認", approved: "承認済み", rejected: "却下", returned: "差し戻し",
};
const REVIEW_STATUS_CLASS: Record<string, string> = {
  approved: "text-emerald-700 dark:text-emerald-400",
  rejected: "text-zinc-500 dark:text-zinc-400",
  returned: "text-amber-700 dark:text-amber-400",
  unreviewed: "text-zinc-500 dark:text-zinc-400",
};

export function EssenceSection({ docRef, aiUse }: { docRef: string; aiUse: string }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const editable = can("workflows.edit");
  const [jobId, setJobId] = useState<string | null>(null);
  const [startError, setStartError] = useState("");
  const list = useQuery({
    queryKey: ["knowledge-essences", docRef],
    queryFn: () => knowledgeApi.essences(docRef),
    // ほかの人が始めた抽出・ブラウザを閉じていた抽出も、終わったらここに反映する
    refetchInterval: (q) =>
      (q.state.data?.essences?.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false),
  });
  const rows = list.data?.essences ?? [];
  const kinds = list.data?.kinds ?? {};
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["knowledge-essences", docRef] });
    // 承認の変更で、リンクの失効（K3）と知識マップにも反映させたいので一緒に読み直す
    qc.invalidateQueries({ queryKey: ["knowledge-links", docRef] });
    qc.invalidateQueries({ queryKey: ["knowledge-map", docRef] });
  };
  const start = useMutation({
    mutationFn: () => knowledgeApi.extractEssences(docRef),
    onSuccess: ({ job_id }) => {
      setStartError("");
      setJobId(job_id);
      show("ローカルの AI が知識を抜き出しています。終わったらここに出ます");
      refresh();
    },
    // 503（ローカルのモデルが無い）・422（利用条件で AI に使えない文献）は、返ってきた理由をそのまま出す
    onError: (e) => setStartError(e instanceof Error ? e.message : String(e)),
  });
  const busy = jobId !== null || rows.some((r) => r.status === "queued" || r.status === "running");
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold text-zinc-500">
        知識エッセンス <span className="font-normal">（この PC のモデルが抜き出した技術知識。AI の推論・未確認）</span>
      </h3>
      <div className="space-y-2">
        {!list.isLoading && (
          aiUse === "prohibited" ? (
            <p className="text-xs text-zinc-500">この文献は利用条件により AI に入力できないため、知識の抽出はできません。</p>
          ) : editable && rows.length === 0 ? (
            <button className={primaryButtonClass} disabled={start.isPending || busy} onClick={() => start.mutate()}>
              {busy ? "抽出中..." : "知識を抽出（ローカルの AI）"}
            </button>
          ) : editable && (
            <button className={secondaryButtonClass} disabled={start.isPending || busy} onClick={() => start.mutate()}>
              {busy ? "抽出中..." : "もう一度抽出する"}
            </button>
          )
        )}
        {!list.isLoading && rows.length === 0 && !editable && aiUse !== "prohibited" && (
          <p className="text-xs text-zinc-500">まだ抽出していません。</p>
        )}
        {startError && <p className="text-xs text-red-600 dark:text-red-400">{startError}</p>}
        {jobId && (
          <JobProgress
            jobId={jobId}
            onFinished={(job) => {
              setJobId(null);
              refresh();
              if (job.status === "succeeded") {
                // 同じ文献・同じ版・同じ内容の再実行は backend が新しく書かない（前回の行を返す）
                const r = job.result as { items?: number; unchanged?: boolean } | undefined;
                show(r?.unchanged
                  ? "前回と同じ内容だったため、既存の結果とレビューをそのまま使います"
                  : `知識を ${r?.items ?? "?"} 件抜き出しました`);
              } else if (job.status === "canceled") {
                show("抽出を取り消しました");
              } else {
                show(job.error || "知識の抽出に失敗しました", "error");
              }
            }}
          />
        )}
        {rows.map((row) => (
          <RowBlock key={row.id} row={row} docRef={docRef} kinds={kinds} editable={editable} onChanged={refresh} />
        ))}
      </div>
    </section>
  );
}

function RowBlock({ row, docRef, kinds, editable, onChanged }: {
  row: EssenceRow; docRef: string; kinds: Record<string, string>; editable: boolean; onChanged: () => void;
}) {
  // 種類（課題・原因・…・応用）ごとにまとめる。kinds の並び（API の返り順）に従う
  const groups = Object.entries(kinds)
    .map(([kind, label]) => ({
      kind, label,
      entries: row.items.map((item, index) => ({ item, index })).filter(({ item }) => item.kind === kind),
    }))
    .filter((g) => g.entries.length > 0);
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <p className="num text-xs text-zinc-500">
        {row.status === "succeeded"
          ? `AI の推論（未確認） · ${row.items.length} 件`
          : ROW_STATUS_LABEL[row.status] ?? row.status}
      </p>
      {row.stale && (
        <p className="mt-1.5 inline-flex rounded-lg bg-amber-100 px-2 py-1 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
          原文が変わりました（前の承認は効きません）
        </p>
      )}
      {row.status === "failed" && row.error && <p className="mt-1 break-words text-xs text-red-600 dark:text-red-400">{row.error}</p>}
      {row.dropped > 0 && (
        <p className="num mt-1 text-[11px] text-zinc-400">検めを通らなかった提案 {row.dropped} 件（引用の不一致・形違い・上限超過）</p>
      )}
      <div className="mt-2 space-y-3">
        {groups.map((g) => (
          <div key={g.kind}>
            <p className="num text-xs font-semibold text-zinc-500">
              {g.label} <span className="font-normal">({g.entries.length})</span>
            </p>
            <ul className="mt-1.5 space-y-2">
              {g.entries.map(({ item, index }) => (
                <ItemCard key={index} row={row} item={item} index={index} docRef={docRef}
                  editable={editable} onChanged={onChanged} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function ItemCard({ row, item, index, docRef, editable, onChanged }: {
  row: EssenceRow; item: EssenceItem; index: number; docRef: string; editable: boolean; onChanged: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [note, setNote] = useState("");
  const [linking, setLinking] = useState(false);
  const review = useMutation({
    mutationFn: (decision: string) => knowledgeApi.reviewEssence(`${row.shard}:${row.id}`, index, decision, note.trim()),
    onSuccess: () => { setNote(""); onChanged(); },
    onError: (e) => show(e instanceof Error ? e.message : "レビューできませんでした", "error"),
  });
  return (
    <li className="rounded-xl bg-zinc-50 p-3 text-sm dark:bg-zinc-800/50">
      <p className="break-words font-medium leading-relaxed">{item.summary}</p>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
        <span className="text-zinc-500">AI の推論（未確認）</span>
        <span className={`font-medium ${REVIEW_STATUS_CLASS[item.review_status] ?? REVIEW_STATUS_CLASS.unreviewed}`}>
          {REVIEW_STATUS_LABEL[item.review_status] ?? item.review_status}
        </span>
      </p>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-xs text-accent-700 dark:text-accent-300">引用</summary>
        <blockquote className="mt-1 whitespace-pre-wrap break-words border-l-2 border-zinc-300 pl-2 text-xs leading-relaxed text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
          {item.quote}
        </blockquote>
      </details>
      {item.review && (
        <details className="mt-1 text-xs text-zinc-500">
          <summary className="cursor-pointer">前回のレビュー{item.review.stale ? "（前の版でのもの）" : ""}</summary>
          <p className="num mt-1 break-words">
            {ESSENCE_DECISION_LABEL[item.review.decision] ?? item.review.decision} · {item.review.reviewer || "—"} ·{" "}
            {new Date(item.review.at * 1000).toLocaleString("ja-JP")}
          </p>
          {item.review.note && <p className="mt-0.5 break-words">{item.review.note}</p>}
        </details>
      )}
      {editable && !row.stale && (
        <div className="mt-2 space-y-1.5">
          <details>
            <summary className="cursor-pointer text-xs text-zinc-500">メモを添える（任意）</summary>
            <input
              className="mt-1 w-full rounded-xl border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="この判断の根拠・メモ"
              aria-label={`知識 ${index + 1} のレビューメモ`}
            />
          </details>
          <div className="flex flex-wrap gap-2">
            <button className={secondaryButtonClass} disabled={review.isPending} onClick={() => review.mutate("approved")}>承認</button>
            <button className={secondaryButtonClass} disabled={review.isPending} onClick={() => review.mutate("rejected")}>却下</button>
            <button className={secondaryButtonClass} disabled={review.isPending} onClick={() => review.mutate("returned")}>差し戻し</button>
          </div>
        </div>
      )}
      {editable && row.stale && (
        <p className="mt-1 text-xs text-zinc-500">原文が変わっているためレビューできません。新しい版でもう一度抽出してください。</p>
      )}
      {editable && !row.stale && item.review_status === "approved" && (
        <div className="mt-2">
          {linking ? (
            <LinkMaker sourceEssenceRef={`${row.shard}:${row.id}`} sourceItem={index} excludeDocRef={docRef}
              essenceLabel={item.summary} onDone={() => { setLinking(false); onChanged(); }} />
          ) : (
            <button className={secondaryButtonClass} onClick={() => setLinking(true)}>別の文献の知識と結ぶ</button>
          )}
        </div>
      )}
    </li>
  );
}
