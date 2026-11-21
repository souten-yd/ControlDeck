/** 文書を 1 つ開く。本文・図（タップで拡大）・関係する文書。内部の情報（参照番号・分野・機密区分）は「詳細」に隠す。 */
import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { knowledgeApi, AI_USE_LABEL, RELATION_LABEL, type DocumentAsset } from "../../api/knowledge";
import { BottomSheet, IconButton, Skeleton, secondaryButtonClass } from "../../components/ui";
import { IconTrash } from "../../components/icons";
import { useAuth, useToasts } from "../../stores";
import type { Biblio } from "../../api/patents";
import { BiblioBlock } from "./BiblioBlock";
import { EssenceSection } from "./EssenceSection";

const MarkdownMessage = lazy(() => import("../workflows/MarkdownMessage"));
const PREVIEW_CHARS = 4000;

export function DocumentSheet({ docRef, onClose }: { docRef: string; onClose: () => void }) {
  const [ref, setRef] = useState(docRef);
  const [full, setFull] = useState(false);
  const [details, setDetails] = useState(false);
  const [zoom, setZoom] = useState<DocumentAsset | null>(null);
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const { data: doc, isLoading } = useQuery({ queryKey: ["knowledge-doc", ref], queryFn: () => knowledgeApi.document(ref) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["knowledge-doc", ref] });
  const analyze = useMutation({
    mutationFn: (kind: string) => knowledgeApi.analyze(ref, kind),
    onSuccess: () => { show("ローカルの AI が読んでいます。終わったらここに出ます"); setTimeout(refresh, 15_000); },
    onError: (e) => show(e instanceof Error ? e.message : String(e), "error"),
  });
  const review = useMutation({ mutationFn: ({ id, status }: { id: number; status: string }) => knowledgeApi.reviewAnnotation(ref, id, status), onSuccess: refresh });
  const removeNote = useMutation({ mutationFn: (id: number) => knowledgeApi.removeAnnotation(ref, id), onSuccess: refresh });
  const images = (doc?.assets ?? []).filter((a) => a.mime?.startsWith("image/"));
  const original = doc?.assets.find((a) => a.role === "original");
  const figureNotes = (doc?.annotations ?? []).filter((a) => a.kind.startsWith("figure_description"));
  const analyses = (doc?.annotations ?? []).filter((a) => !a.kind.startsWith("figure_description"));
  const open = (next: string) => { setRef(next); setFull(false); setDetails(false); };
  return (
    <BottomSheet title={doc?.title ?? "文書"} onClose={onClose} wide stable>
      {isLoading || !doc ? <Skeleton className="h-40" /> : (
        <div className="space-y-5">
          {(original || doc.url) && (
            <div className="flex flex-wrap gap-2 text-sm">
              {original && <a className="rounded-lg px-2 py-1 text-accent-700 hover:bg-accent-600/10 dark:text-accent-300" href={original.url} target="_blank" rel="noreferrer">原本の PDF を開く</a>}
              {doc.url && <a className="rounded-lg px-2 py-1 text-accent-700 hover:bg-accent-600/10 dark:text-accent-300" href={doc.url} target="_blank" rel="noreferrer">元のページ</a>}
            </div>
          )}
          {Boolean(doc.meta?.biblio) && typeof doc.meta.biblio === "object" && <BiblioBlock key={doc.ref} docRef={doc.ref} biblio={doc.meta.biblio as Biblio} />}
          {images.length > 0 && (
            <section>
              <h3 className="mb-2 text-xs font-semibold text-zinc-500">図・ページ</h3>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                {images.map((a) => (
                  <button key={a.sha256 + a.ord} onClick={() => setZoom(a)} className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800">
                    <img src={a.url} alt={pageLabel(a)} loading="lazy" className="aspect-[3/4] w-full object-contain" />
                    <span className="block truncate px-1 py-0.5 text-[11px] text-zinc-500">{pageLabel(a)}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
          {figureNotes.length > 0 && (
            <section>
              <h3 className="mb-1 text-xs font-semibold text-zinc-500">図表の読み取り <span className="font-normal">（AI の推論・未確認。原本と照らして使う）</span></h3>
              <div className="space-y-2">
                {figureNotes.map((n) => (
                  <details key={n.id} className="rounded-xl bg-zinc-50 p-3 text-sm dark:bg-zinc-800/50">
                    <summary className="cursor-pointer text-xs text-zinc-600 dark:text-zinc-300">{n.kind.replace("figure_description:p", "p.")}</summary>
                    <div className="prose prose-sm mt-2 max-w-none dark:prose-invert"><Suspense fallback={null}><MarkdownMessage text={n.content} /></Suspense></div>
                  </details>
                ))}
              </div>
            </section>
          )}
          <section>
            <h3 className="mb-2 text-xs font-semibold text-zinc-500">AI の分析 <span className="font-normal">（この PC のモデルだけ。推論・未確認）</span></h3>
            {analyses.map((a) => (
              <details key={a.id} className="mb-2 rounded-xl bg-zinc-50 p-3 text-sm dark:bg-zinc-800/50">
                <summary className="flex cursor-pointer items-center gap-2 text-xs">
                  <span className="flex-1 font-semibold">{doc.analysis_kinds?.[a.kind] ?? a.kind}</span>
                  {a.stale && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">原文が変わった</span>}
                  <span className="text-zinc-500">{a.review_status === "checked" ? "確認済み" : a.review_status === "rejected" ? "不採用" : "未確認"}</span>
                </summary>
                <div className="prose prose-sm mt-2 max-w-none dark:prose-invert"><Suspense fallback={null}><MarkdownMessage text={a.content} /></Suspense></div>
                {can("workflows.edit") && (
                  <div className="mt-2 flex items-center gap-2">
                    <button className={secondaryButtonClass} onClick={() => review.mutate({ id: a.id, status: "checked" })}>確認済みにする</button>
                    <button className={secondaryButtonClass} onClick={() => review.mutate({ id: a.id, status: "rejected" })}>不採用</button>
                    <IconButton label="分析を削除" onClick={() => removeNote.mutate(a.id)}><IconTrash /></IconButton>
                  </div>
                )}
              </details>
            ))}
            {can("workflows.edit") && doc.ai_use !== "prohibited" && (
              <div className="flex flex-wrap gap-2">
                {Object.entries(doc.analysis_kinds ?? {}).map(([k, label]) => (
                  <button key={k} className={secondaryButtonClass} disabled={analyze.isPending} onClick={() => analyze.mutate(k)}>{label}を作る</button>
                ))}
              </div>
            )}
          </section>
          <EssenceSection key={ref} docRef={ref} aiUse={doc.ai_use} />
          <section>
            <h3 className="mb-2 text-xs font-semibold text-zinc-500">本文</h3>
            {doc.text ? (
              <>
                <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-xl bg-zinc-50 p-3 text-xs leading-relaxed dark:bg-zinc-800/50">
                  {full ? doc.text : doc.text.slice(0, PREVIEW_CHARS)}
                </pre>
                {!full && doc.text.length > PREVIEW_CHARS && (
                  <button className="mt-1 text-xs text-accent-700 dark:text-accent-300" onClick={() => setFull(true)}>全文を表示（{doc.text.length.toLocaleString()} 文字）</button>
                )}
              </>
            ) : (
              <p className="text-sm text-zinc-500">本文は保存していません（利用条件により番号と題名だけ。本文は手元の正規の 1 部で読んでください）。</p>
            )}
          </section>
          {doc.related.length > 0 && (
            <section>
              <h3 className="mb-2 text-xs font-semibold text-zinc-500">関係する文書</h3>
              <ul className="space-y-1">
                {doc.related.map((r) => (
                  <li key={r.ref}>
                    <button onClick={() => open(r.ref)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800">
                      <span className="min-w-0 flex-1 truncate">{r.title}</span>
                      <span className="shrink-0 text-xs text-zinc-500">{RELATION_LABEL[r.relation] ?? r.relation}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <button className="text-xs text-zinc-500 underline-offset-2 hover:underline" onClick={() => setDetails(!details)}>
            {details ? "詳細を閉じる" : "詳細"}
          </button>
          {details && (
            <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
              <dt>番号</dt><dd>{doc.identifiers.map((i) => i.raw || i.value).join("、") || "—"}</dd>
              <dt>本文の参照</dt><dd className="break-words">{doc.references.slice(0, 30).map((i) => i.raw || i.value).join("、") || "—"}</dd>
              <dt>置き場所</dt><dd>{doc.collection}</dd>
              <dt>扱い</dt><dd>{doc.sensitivity === "public" ? "公開" : "機密（クラウドの AI へ出さない）"} · {AI_USE_LABEL[doc.ai_use] ?? doc.ai_use}</dd>
              <dt>参照</dt><dd className="font-mono">{doc.ref}</dd>
            </dl>
          )}
        </div>
      )}
      {zoom && (
        <div role="dialog" aria-label="拡大" className="fixed inset-0 z-[80] grid place-items-center bg-black/80 p-3" onClick={() => setZoom(null)}>
          <img src={zoom.url} alt={pageLabel(zoom)} className="max-h-full max-w-full rounded-lg bg-white object-contain" />
        </div>
      )}
    </BottomSheet>
  );
}

function pageLabel(a: DocumentAsset) {
  const page = (a.meta?.page as number | undefined) ?? (a.role === "page" ? a.ord : undefined);
  return page ? `p.${page}${a.role === "figure" ? " の図" : ""}` : "図";
}
