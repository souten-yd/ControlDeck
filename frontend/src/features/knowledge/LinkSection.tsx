/** 知識のつながり（K3）: この文献に関わる技術仮説のリンクの一覧と、承認・却下の操作。
 *  リンクは承認済みの知識どうしを利用者が明示して結んだもの（basis=inferred 固定。
 *  作っただけでは未確認。承認しても「原文の事実」にはならない）。
 *  どちらかの知識が今の版で承認済みでなくなったら失効（stale）として見え、
 *  レビューは付けられない。手本: EssenceSection（カード・レビューの操作・stale の印）。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { knowledgeApi, ESSENCE_KINDS, type KnowledgeLink } from "../../api/knowledge";
import { secondaryButtonClass } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

const REVIEW_STATUS_LABEL: Record<string, string> = {
  unreviewed: "未確認", approved: "承認済み", rejected: "却下",
};
const REVIEW_STATUS_CLASS: Record<string, string> = {
  approved: "text-emerald-700 dark:text-emerald-400",
  rejected: "text-zinc-500 dark:text-zinc-400",
  unreviewed: "text-zinc-500 dark:text-zinc-400",
};

export function LinkSection({ docRef, onOpen }: { docRef: string; onOpen: (ref: string) => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const editable = can("workflows.edit");
  // レビューのメモはここで 1 つ持つ（成功したらクリア。K1 の ItemCard と同じ）
  const [note, setNote] = useState("");
  const list = useQuery({
    queryKey: ["knowledge-links", docRef],
    queryFn: () => knowledgeApi.documentLinks(docRef),
  });
  const review = useMutation({
    mutationFn: (args: { linkRef: string; decision: string; note: string }) =>
      knowledgeApi.reviewLink(args.linkRef, args.decision, args.note),
    onSuccess: (_r, args) => {
      setNote("");
      show(args.decision === "approved" ? "リンクを承認しました（技術仮説として。事実ではありません）" : "リンクを却下しました");
      qc.invalidateQueries({ queryKey: ["knowledge-links", docRef] });
    },
    onError: (e) => show(e instanceof Error ? e.message : "レビューできませんでした", "error"),
  });
  const links = list.data?.links ?? [];
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold text-zinc-500">
        知識のつながり <span className="font-normal">（利用者が結んだ技術仮説。inferred・事実ではありません）</span>
      </h3>
      {list.isLoading ? <p className="text-xs text-zinc-500">読み込み中...</p> : links.length === 0 ? (
        <p className="text-xs text-zinc-500">まだありません。知識エッセンスのカードから「別の文献の知識と結ぶ」で作れます。</p>
      ) : (
        <ul className="space-y-2">
          {links.map((l) => (
            <LinkCard key={l.link_ref} link={l} docRef={docRef} editable={editable} note={note}
              onNoteChange={setNote} onOpen={onOpen}
              onReview={(decision, note) => review.mutate({ linkRef: l.link_ref, decision, note })} />
          ))}
        </ul>
      )}
    </section>
  );
}

function LinkCard({ link, docRef, editable, note, onNoteChange, onOpen, onReview }: {
  link: KnowledgeLink; docRef: string; editable: boolean; note: string;
  onNoteChange: (note: string) => void; onOpen: (ref: string) => void;
  onReview: (decision: string, note: string) => void;
}) {
  // この文献が source 側なら「この文献 → 相手」の向きで、target 側なら逆向きで見せる
  const mine = link.source && link.source.doc_ref === docRef ? link.source : link.target;
  const other = mine === link.source ? link.target : link.source;
  const forward = mine === link.source;
  return (
    <li className="rounded-xl bg-zinc-50 p-3 text-sm dark:bg-zinc-800/50">
      <p className="text-[11px] text-zinc-500">
        {forward ? "この文献から" : "この文献へ"}
        <span className="mx-1">→</span>
        技術仮説（未確認を人が確かめるもの）
      </p>
      <div className="mt-1 space-y-1">
        {mine && (
          <p className="break-words leading-relaxed">
            <span className="text-zinc-500">{ESSENCE_KINDS[mine.kind] ?? mine.kind}: </span>
            {mine.summary}
          </p>
        )}
        {other ? (
          <p className="break-words leading-relaxed">
            <span className="text-zinc-500">{ESSENCE_KINDS[other.kind] ?? other.kind}: </span>
            {other.summary}
            <button onClick={() => onOpen(other.doc_ref)}
              className="ml-1 break-all text-xs text-accent-700 hover:underline dark:text-accent-300">
              （{other.doc_title}）
            </button>
          </p>
        ) : (
          <p className="text-xs text-zinc-500">相手の知識が読み取れません（元の文献か版が変わっています）</p>
        )}
      </div>
      {link.note && <p className="mt-1.5 break-words text-xs text-zinc-600 dark:text-zinc-300">理由: {link.note}</p>}
      <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
        <span className={REVIEW_STATUS_CLASS[link.review_status] ?? REVIEW_STATUS_CLASS.unreviewed}>
          {REVIEW_STATUS_LABEL[link.review_status] ?? link.review_status}
        </span>
        {link.stale && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
            失効（どちらかの知識が今の版で承認されていません）
          </span>
        )}
      </p>
      {link.review && (
        <details className="mt-1 text-xs text-zinc-500">
          <summary className="cursor-pointer">前回のレビュー{link.review.stale ? "（失効したもの）" : ""}</summary>
          <p className="num mt-1 break-words">
            {link.review.decision === "approved" ? "承認" : link.review.decision === "rejected" ? "却下" : link.review.decision} ·
            {" "}{link.review.reviewer || "—"} · {new Date(link.review.at * 1000).toLocaleString("ja-JP")}
          </p>
          {link.review.note && <p className="mt-0.5 break-words">{link.review.note}</p>}
        </details>
      )}
      {editable && !link.stale && (
        <div className="mt-2 space-y-1.5">
          <details>
            <summary className="cursor-pointer text-xs text-zinc-500">メモを添える（任意）</summary>
            <input className="mt-1 w-full rounded-xl border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              value={note} onChange={(e) => onNoteChange(e.target.value)} placeholder="この判断の根拠・メモ"
              aria-label="リンクのレビューメモ" />
          </details>
          <div className="flex flex-wrap gap-2">
            <button className={secondaryButtonClass} onClick={() => onReview("approved", note.trim())}>承認</button>
            <button className={secondaryButtonClass} onClick={() => onReview("rejected", note.trim())}>却下</button>
          </div>
        </div>
      )}
      {editable && link.stale && (
        <p className="mt-1 text-xs text-zinc-500">失効しているためレビューできません。元の知識を新しい版で承認してください。</p>
      )}
    </li>
  );
}
