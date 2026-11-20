/** 特許の案件ページ（/patents/:id）。浮いたシートではなく ControlDeck のページとして開く
 *  （2026-11-21 利用者「開発フローがフローティングウィンドウになっている。コントロールデックと統合してほしい」）。
 *  PC（1024px〜）は左に区画の一覧・中央に中身・右（1280px〜）に「次の期限」「次にやること」。それより狭い幅はタブで 1 枚。 */
import { useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { patentsApi, type CaseDetail, type PatentMeta } from "../api/patents";
import { useAuth, useToasts } from "../stores";
import { ConfirmDialog, DropdownMenu, EmptyState, SegmentedTabs, Skeleton, type MenuItem } from "../components/ui";
import { IconChevronLeft, IconDots } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { AssistTab, ClaimsTab, DeadlinesTab, HearingTab, RefsTab, SummaryTab } from "../features/patents/CaseSections";
import { SimilarSection } from "../features/patents/CaseSimilar";
import { WatchSection } from "../features/patents/CaseWatch";
import { DeadlineChip, card, errorText } from "../features/patents/shared";

type Section = "summary" | "hearing" | "claims" | "refs" | "watch" | "deadlines" | "assist" | "similar";
const SECTIONS: ReadonlyArray<readonly [Section, string, string?]> = [
  ["summary", "概要"], ["hearing", "ヒアリング", "話す"], ["claims", "請求項", "請求"], ["refs", "文献"], ["watch", "ウォッチ", "監視"],
  ["deadlines", "期限"], ["assist", "AI 支援", "AI"], ["similar", "分析"],
];
const SECTION_HINT: Record<Section, string> = {
  summary: "発明の名称・段階・課題と解決手段",
  hearing: "発明を話して記録し、AI で整理する",
  claims: "請求項の版と構成要件、クレームチャート",
  refs: "先行文献の登録（X/Y/A）と検索",
  watch: "公開データを定期に検索し、新しい公報を候補に出す",
  deadlines: "出来事の記録と期限の目安",
  assist: "ローカルの AI による下書きと確認",
  similar: "請求項に近い公報（先行文献の候補）",
};

export default function PatentCasePage() {
  const { id: raw } = useParams();
  const id = Number(raw);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const editable = can("workflows.edit");
  const [params, setParams] = useSearchParams();
  const section: Section = (SECTIONS.find(([s]) => s === params.get("s"))?.[0]) ?? "summary";
  const setSection = (next: Section) => setParams(next === "summary" ? {} : { s: next }, { replace: true });
  const [deleting, setDeleting] = useState(false);
  const { data: meta } = useQuery({ queryKey: ["patents-meta"], queryFn: patentsApi.meta, staleTime: 30_000 });
  const { data: c, isError } = useQuery({
    queryKey: ["patent", id],
    queryFn: () => patentsApi.get(id),
    enabled: Number.isFinite(id),
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
    onSuccess: () => { show("削除しました"); qc.invalidateQueries({ queryKey: ["patents"] }); navigate("/patents", { replace: true }); },
    onError: fail,
  });
  const stageIndex = meta && c ? meta.stages.findIndex((s) => s.id === c.stage) : -1;
  const nextStage = meta && stageIndex >= 0 ? meta.stages[stageIndex + 1] : undefined;
  const advance = useMutation({
    mutationFn: () => patentsApi.patch(id, { stage: nextStage!.id }),
    onSuccess: (next) => { set(next); show(`「${next.stage_label}」へ進めました`); },
    onError: fail,
  });
  const menu: MenuItem[] = [
    ...(nextStage ? [{ label: `次の段階へ（${nextStage.label}）`, onSelect: () => advance.mutate() }] : []),
    { label: "案件を削除", danger: true, separated: Boolean(nextStage), onSelect: () => setDeleting(true) },
  ];

  const back = (
    <Link to="/patents" aria-label="Patent の一覧へ" title="Patent の一覧へ"
      className="cd-icon-button relative -ml-1 grid h-10 w-10 shrink-0 place-items-center rounded-full text-lg text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100">
      <IconChevronLeft />
    </Link>
  );

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <PageHeader title={c?.title ?? "案件"} leading={back} actions={editable && c && (
        <DropdownMenu ariaLabel="案件のその他の操作" trigger={<IconDots />} items={menu} />
      )} />
      {isError ? (
        <EmptyState actions={<Link to="/patents" className="text-sm text-accent-700 dark:text-accent-300">一覧へ戻る</Link>}>
          案件を開けませんでした。削除されたか、見る権限がありません。
        </EmptyState>
      ) : !c || !meta ? <Skeleton className="h-64" /> : (
        <>
          <StageSteps meta={meta} c={c} />
          <SegmentedTabs tabs={SECTIONS} value={section} onChange={setSection} className="mb-4 lg:hidden" />
          <div className="items-start lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-6 xl:grid-cols-[11rem_minmax(0,1fr)_16rem]">
            <SectionNav value={section} onChange={setSection} />
            <section aria-labelledby="patent-section-title" className={`${card} min-w-0 md:p-5`}>
              <header className="mb-4">
                <h2 id="patent-section-title" className="text-base font-semibold">{SECTIONS.find(([s]) => s === section)?.[1]}</h2>
                <p className="mt-0.5 text-xs text-zinc-500">{SECTION_HINT[section]}</p>
              </header>
              {section === "summary" && <SummaryTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
              {section === "hearing" && <HearingTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} onOpenAssist={() => setSection("assist")} />}
              {section === "claims" && <ClaimsTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
              {section === "refs" && <RefsTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
              {section === "watch" && <WatchSection c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
              {section === "deadlines" && <DeadlinesTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
              {section === "assist" && <AssistTab c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
              {section === "similar" && <SimilarSection c={c} meta={meta} editable={editable} onSaved={set} onError={fail} />}
            </section>
            <SidePanel c={c} onOpen={setSection} />
          </div>
        </>
      )}
      {deleting && (
        <ConfirmDialog title="案件を削除しますか？" message="期限・請求項の版・先行文献・AI 支援の記録も削除されます。取り込んだ文献は残ります。取り消せません。"
          confirmLabel="削除する" busy={remove.isPending} onConfirm={() => remove.mutate()} onClose={() => setDeleting(false)} />
      )}
    </div>
  );
}

/** 段階の表示。PC は 11 段の点、狭い幅は「3/11 先行技術調査」と細い帯。変えるのは「概要」か ⋯ の「次の段階へ」。 */
function StageSteps({ meta, c }: { meta: PatentMeta; c: CaseDetail }) {
  const index = Math.max(0, meta.stages.findIndex((s) => s.id === c.stage));
  const total = meta.stages.length;
  return (
    <div className="mb-4">
      <p className="flex items-baseline gap-2 text-sm">
        <span className="num shrink-0 text-xs font-medium text-zinc-500">{index + 1}/{total}</span>
        <span className="min-w-0 truncate font-medium">{c.stage_label}</span>
      </p>
      <ol aria-label="段階" className="mt-2 flex gap-1">
        {meta.stages.map((s, i) => (
          <li key={s.id} aria-current={i === index ? "step" : undefined} title={`${i + 1}. ${s.label}`}
            className={`h-1.5 min-w-0 flex-1 rounded-full ${i < index ? "bg-accent-600/40 dark:bg-accent-500/40" : i === index ? "bg-accent-600 dark:bg-accent-500" : "bg-zinc-200 dark:bg-zinc-800"}`}>
            <span className="sr-only">{i + 1}. {s.label}{i === index ? "（いま）" : i < index ? "（済み）" : ""}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** PC の左の区画一覧。上下の矢印キーで移れる。 */
function SectionNav({ value, onChange }: { value: Section; onChange: (s: Section) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent, i: number) => {
    const step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (i + step + SECTIONS.length) % SECTIONS.length;
    onChange(SECTIONS[next][0]);
    refs.current[next]?.focus();
  };
  return (
    <nav aria-label="案件の区画" className="sticky top-4 hidden lg:block">
      <ul className="space-y-1">
        {SECTIONS.map(([id, label], i) => {
          const active = id === value;
          return (
            <li key={id}>
              <button ref={(el) => { refs.current[i] = el; }} type="button" aria-current={active ? "page" : undefined}
                tabIndex={active ? 0 : -1} onClick={() => onChange(id)} onKeyDown={(e) => onKey(e, i)}
                className={`flex min-h-11 w-full items-center rounded-xl px-3 text-left text-sm transition-colors ${active
                  ? "bg-accent-50 font-medium text-accent-700 dark:bg-accent-600/15 dark:text-accent-300"
                  : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800/70"}`}>
                {label}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** 右の欄（1280px〜）: 次の期限と、今の段階でやること。 */
function SidePanel({ c, onOpen }: { c: CaseDetail; onOpen: (s: Section) => void }) {
  const next = [...c.deadlines].sort((a, b) => a.days_left - b.days_left).slice(0, 3);
  return (
    <aside aria-label="案件の要点" className="sticky top-4 hidden space-y-4 xl:block">
      <section className={card}>
        <h3 className="text-xs font-semibold text-zinc-500">次の期限</h3>
        {next.length === 0 ? <p className="mt-2 text-sm text-zinc-500">まだありません。出願日などを「期限」に記録すると出ます。</p> : (
          <ul className="mt-2 space-y-2">
            {next.map((d) => (
              <li key={d.key} className="text-sm">
                <span className="flex items-start gap-2"><span className="min-w-0 flex-1">{d.label}</span><DeadlineChip days={d.days_left} /></span>
                <span className="num block text-xs text-zinc-500">{d.due}</span>
              </li>
            ))}
          </ul>
        )}
        <button type="button" className="mt-1 min-h-11 text-xs text-accent-700 hover:underline dark:text-accent-300" onClick={() => onOpen("deadlines")}>期限を開く</button>
      </section>
      {c.stage_info && (
        <section className={card}>
          <h3 className="text-xs font-semibold text-zinc-500">次にやること</h3>
          <p className="mt-2 text-sm leading-relaxed">{c.stage_info.goal}</p>
          {c.stage_info.ai.length > 0 && (
            <button type="button" className="mt-1 min-h-11 text-xs text-accent-700 hover:underline dark:text-accent-300" onClick={() => onOpen("assist")}>AI 支援を使う</button>
          )}
        </section>
      )}
    </aside>
  );
}
