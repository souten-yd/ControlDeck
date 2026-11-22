/** 知識マップ（K3）: 1 つの文献を中心に、1〜2 hop の文献（書誌の関係）と承認済みの知識を
 *  SVG の放射図で見る。読み取りだけ（LLM なし・backend/app/knowledge/map.py と対になる）。
 *  ノードを押すとその文献を開く。上限（ノード 61・エッジ 60）に達したら打ち切りの印を出す。
 *  依存は足さず SVG で手書き（指示書・docs/HANDOFF-knowledge-platform.md の K3 の行）。
 *  320px では円を縮小して読める範囲だけの字を載せる（題名は 8 字に切り詰め、全文は title 属性）。
 *  手本: EssenceSection（節の形・TanStack Query）と BiblioBlock（DocumentSheet の中の節）。 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { knowledgeApi, type MapNode } from "../../api/knowledge";
import { secondaryButtonClass } from "../../components/ui";

const VIEW = 440;                       // SVG の一辺（viewBox。幅 100% で縮小される）
const CENTER = VIEW / 2;
// 段ごとの半径と知識の円の半径は、どの段の要素も viewBox の端に届かないように選ぶ
// （2 段目の知識: 180 + 26 + 5 = 211 < 220。1 段目の知識と 2 段目のラベルも重ならない:
//   112 + 26 + 5 = 143 < 180 - 33 = 147）。
const DOC_RADIUS: Record<number, number> = { 0: 0, 1: 112, 2: 180 };
const ESSENCE_RADIUS = 26;

/** ノードの位置: root は中央、文献は深さごとの円周、知識は親の文献の周囲。 */
function layout(nodes: MapNode[]) {
  const pos = new Map<string, { x: number; y: number }>();
  const byDepth = new Map<number, MapNode[]>();
  for (const n of nodes) {
    if (n.kind !== "document") continue;
    const depth = n.depth ?? 0;
    byDepth.set(depth, [...(byDepth.get(depth) ?? []), n]);
  }
  for (const [depth, list] of byDepth) {
    list.forEach((n, i) => {
      if (depth === 0) {
        pos.set(n.id, { x: CENTER, y: CENTER });
        return;
      }
      const angle = (i / Math.max(1, list.length)) * Math.PI * 2 - Math.PI / 2;
      const r = DOC_RADIUS[Math.min(depth, 2)] ?? DOC_RADIUS[2];
      pos.set(n.id, { x: CENTER + r * Math.cos(angle), y: CENTER + r * Math.sin(angle) });
    });
  }
  for (const n of nodes) {
    if (n.kind !== "essence") continue;
    const parent = pos.get(`d:${n.doc_ref}`);
    if (!parent) continue;
    const siblings = nodes.filter((o) => o.kind === "essence" && o.doc_ref === n.doc_ref);
    const i = siblings.findIndex((o) => o.id === n.id);
    const angle = (i / Math.max(1, siblings.length)) * Math.PI * 2 - Math.PI / 2;
    pos.set(n.id, { x: parent.x + ESSENCE_RADIUS * Math.cos(angle), y: parent.y + ESSENCE_RADIUS * Math.sin(angle) });
  }
  return pos;
}

function edgeClass(relation: string) {
  if (relation === "evidence") return "stroke-zinc-300 dark:stroke-zinc-600";
  if (relation === "version") return "stroke-zinc-400 dark:stroke-zinc-500 [stroke-dasharray:4 3]";
  if (relation === "cited_by") return "stroke-zinc-400 dark:stroke-zinc-500";
  return "stroke-accent-600 dark:stroke-accent-400";  // cites（この文献が参照する）
}

function clip(text: string, width: number) {
  return text.length > width ? `${text.slice(0, width)}…` : text;
}

export function KnowledgeMap({ docRef, onOpen }: { docRef: string; onOpen: (ref: string) => void }) {
  const [hops, setHops] = useState<1 | 2>(1);
  const list = useQuery({
    queryKey: ["knowledge-map", docRef, hops],
    queryFn: () => knowledgeApi.knowledgeMap(docRef, hops),
  });
  const nodes = list.data?.nodes ?? [];
  const edges = list.data?.edges ?? [];
  const kinds = list.data?.kinds ?? {};
  const pos = layout(nodes);
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold text-zinc-500">
        知識マップ <span className="font-normal">（関係する文献と承認済みの知識。読み取りだけ・AI は使わない）</span>
      </h3>
      <div className="mb-2 flex gap-2">
        {[1, 2].map((h) => (
          <button key={h} className={hops === h
            ? "min-h-9 rounded-xl border border-accent-600 px-3 text-xs font-medium text-accent-700 dark:text-accent-300"
            : `${secondaryButtonClass} min-h-9 px-3 text-xs`}
            onClick={() => setHops(h as 1 | 2)}>
            {h} hop{h > 1 ? " まで" : " だけ"}
          </button>
        ))}
      </div>
      {list.isLoading ? (
        <p className="text-xs text-zinc-500">読み込み中...</p>
      ) : list.isError ? (
        <p className="text-xs text-red-600 dark:text-red-400">{list.error instanceof Error ? list.error.message : "読めませんでした"}</p>
      ) : nodes.length <= 1 ? (
        <p className="text-xs text-zinc-500">
          関係する文献も承認済みの知識もまだありません。「関係する文書」の有無と、知識エッセンスの承認を確認してください。
        </p>
      ) : (
        <>
          <svg viewBox={`0 0 ${VIEW} ${VIEW}`} className="mx-auto block w-full max-w-[420px] touch-manipulation"
            role="group" aria-label="知識マップの図">
            {edges.map((e) => {
              const a = pos.get(e.source);
              const b = pos.get(e.target);
              if (!a || !b) return null;
              return <line key={`${e.source}-${e.target}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                strokeWidth={e.relation === "evidence" ? 1 : 1.5} className={edgeClass(e.relation)} />;
            })}
            {nodes.map((n) => {
              const at = pos.get(n.id);
              if (!at) return null;
              if (n.kind === "essence") {
                const label = `${kinds[n.essence_kind ?? ""] ?? n.essence_kind ?? "知識"}: ${n.summary ?? ""}`;
                return (
                  <circle key={n.id} cx={at.x} cy={at.y} r={5} role="button" tabIndex={0} aria-label={label}
                    className="cursor-pointer fill-accent-500 stroke-white hover:opacity-80 dark:stroke-zinc-900"
                    onClick={() => n.doc_ref && onOpen(n.doc_ref)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") n.doc_ref && onOpen(n.doc_ref); }}>
                    <title>{label}</title>
                  </circle>
                );
              }
              const isRoot = n.relation === "root";
              return (
                <g key={n.id} role="button" tabIndex={0} aria-label={`文献 ${n.title ?? ""}`}
                  className="cursor-pointer" onClick={() => n.ref && onOpen(n.ref)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") n.ref && onOpen(n.ref); }}>
                  <title>{`${n.title ?? ""}${n.via ? `（${n.via}）` : ""}`}</title>
                  <rect x={at.x - 33} y={at.y - 11} width={66} height={22} rx={8}
                    className={isRoot ? "fill-accent-600/15 stroke-accent-600 dark:stroke-accent-400"
                      : "fill-white stroke-zinc-400 dark:fill-zinc-800 dark:stroke-zinc-500"} />
                  <text x={at.x} y={at.y + 3.5} textAnchor="middle" style={{ fontSize: 9 }}
                    className="fill-zinc-700 dark:fill-zinc-200">{clip(n.title ?? "", 7)}</text>
                </g>
              );
            })}
          </svg>
          <p className="num mt-1 text-center text-[11px] text-zinc-500">
            {nodes.length}/{list.data?.limits.nodes} ノード
            {list.data?.truncated && (
              <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                上限に達したため一部だけ表示
              </span>
            )}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">
            四角=文献・円=承認済みの知識。押すとその文献を開きます。線の太い実線=この文献が参照、
            細い実線=この文献を参照、破線=別の版、薄い線=知識の出典。
          </p>
        </>
      )}
    </section>
  );
}
