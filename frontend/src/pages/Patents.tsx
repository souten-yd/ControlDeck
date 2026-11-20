/** Patent（特許調査・検討）の入口。「案件」「分析」「公報」を切り替える。案件は /patents/:id のページで開く
 *  （2026-11-21 利用者「開発フローがフローティングウィンドウになっている。コントロールデックと統合してほしい」）。
 *  PatentBuddy Add-on を本体へ統合したもの。案件の内容は外部へ送らない（AI 支援は Host 管理のローカルモデルだけ）。 */
import { lazy, Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { patentsApi } from "../api/patents";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, EmptyState, IconButton, SegmentedTabs, Skeleton, primaryButtonClass } from "../components/ui";
import { IconChevronRight, IconPlus } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { DeadlineChip, card, errorText, input } from "../features/patents/shared";
import { PublicationsPanel } from "../features/patents/PublicationsPanel";

const AnalyticsDashboard = lazy(() => import("../features/patents/AnalyticsDashboard").then((m) => ({ default: m.AnalyticsDashboard })));

type Tab = "cases" | "analysis" | "docs";
const TABS: ReadonlyArray<readonly [Tab, string]> = [["cases", "案件"], ["analysis", "分析"], ["docs", "公報"]];

export default function PatentsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [params, setParams] = useSearchParams();
  const tab: Tab = (TABS.find(([id]) => id === params.get("tab"))?.[0]) ?? "cases";
  const setTab = (next: Tab) => setParams(next === "cases" ? {} : { tab: next }, { replace: true });
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [title, setTitle] = useState("");
  const { data: cases, isLoading } = useQuery({ queryKey: ["patents"], queryFn: patentsApi.list });
  const create = useMutation({
    mutationFn: () => patentsApi.create(title.trim()),
    onSuccess: (c) => {
      setCreating(false);
      setTitle("");
      qc.invalidateQueries({ queryKey: ["patents"] });
      navigate(`/patents/${c.id}`);
    },
    onError: (e) => show(errorText(e), "error"),
  });

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6">
      <PageHeader
        title="Patent"
        description="特許の調査と検討。案件ごとに段階・期限・請求項・先行文献を管理し、取り込んだ公報を分析します。内容は外部へ送りません。"
        actions={can("workflows.edit") && (
          <IconButton variant="primary" label="案件を作成" onClick={() => setCreating(true)}><IconPlus /></IconButton>
        )}
      />
      <SegmentedTabs tabs={TABS} value={tab} onChange={setTab} className="mb-4 max-w-md" />

      {tab === "cases" && (isLoading ? <Skeleton className="h-24" /> : !cases || cases.length === 0 ? (
        <EmptyState actions={can("workflows.edit") && (
          <button className={primaryButtonClass} onClick={() => setCreating(true)}>案件を作成</button>
        )}>案件がありません。発明の名称から始めて、課題・構成・請求項を書き足していきます。</EmptyState>
      ) : (
        <ul className="grid gap-3 lg:grid-cols-2">
          {cases.map((c) => (
            <li key={c.id} className="min-w-0">
              <Link to={`/patents/${c.id}`} className={`${card} flex min-h-16 w-full items-center gap-3 text-left transition-colors hover:border-zinc-300 dark:hover:border-zinc-700`}>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{c.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-zinc-500">
                    {c.stage_label}{c.next_deadline ? ` · ${c.next_deadline.label}` : ""}
                  </span>
                </span>
                {c.next_deadline && <DeadlineChip days={c.next_deadline.days_left} />}
                <IconChevronRight className="shrink-0 text-zinc-400" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      ))}
      {tab === "analysis" && (
        <Suspense fallback={<Skeleton className="h-56" />}>
          <AnalyticsDashboard onOpenDocs={() => { setTab("docs"); if (can("workflows.edit")) setImporting(true); }} />
        </Suspense>
      )}
      {tab === "docs" && <PublicationsPanel importing={importing} onImport={() => setImporting(true)} onImportClose={() => setImporting(false)} />}

      {creating && (
        <BottomSheet title="案件を作成" onClose={() => setCreating(false)}>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (title.trim()) create.mutate(); }}>
            <input className={input} autoFocus placeholder="発明の名称" value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
            <button className={`${primaryButtonClass} w-full`} disabled={!title.trim() || create.isPending}>作成</button>
          </form>
        </BottomSheet>
      )}
    </div>
  );
}
