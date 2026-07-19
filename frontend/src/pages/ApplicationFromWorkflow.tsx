import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { applicationBuilderApi } from "../api/applicationBuilder";
import { ApplicationProjectCards } from "../features/application-builder/ApplicationProjectCards";
import { useToasts } from "../stores";
import { PageHeader } from "../components/PageHeader";

export default function ApplicationFromWorkflowPage() {
  const workflowId = Number(useParams().id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const { data = [], isLoading } = useQuery({ queryKey: ["application-projects", workflowId], queryFn: () => applicationBuilderApi.list(workflowId), enabled: Number.isFinite(workflowId) });
  const create = useMutation({
    mutationFn: (source: "draft" | "published") => applicationBuilderApi.createFromWorkflow(workflowId, { source }),
    onSuccess: async (project) => { await qc.invalidateQueries({ queryKey: ["application-projects"] }); navigate(`/applications/${project.id}`); },
    onError: (error) => show(error instanceof Error ? error.message : "アプリ化に失敗しました", "error"),
  });
  return <main className="min-h-0 flex-1 overflow-y-auto p-4 pb-24 md:p-6"><div className="mx-auto max-w-5xl">
    <PageHeader title="Create in App Studio" description="処理をWorkflow IRへ変換し、独立したApplication Specとして設計します。" leading={<button onClick={() => navigate(`/workflows/${workflowId}`)} aria-label="Back to Workflow" className="grid min-h-11 min-w-11 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900">←</button>} />
    <section className="my-5 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"><h2 className="text-sm font-semibold">新しいProject</h2><p className="mt-1 text-xs text-zinc-500">Draftは現在の編集内容、公開版はimmutableな本番契約を基準にします。</p><div className="mt-3 grid gap-2 sm:grid-cols-2"><button disabled={create.isPending} onClick={() => create.mutate("draft")} className="min-h-11 rounded-xl bg-accent-600 text-sm font-semibold text-white disabled:opacity-50">現在のDraftから作成</button><button disabled={create.isPending} onClick={() => create.mutate("published")} className="min-h-11 rounded-xl border border-zinc-300 text-sm font-medium dark:border-zinc-700">公開版から作成</button></div></section>
    <h2 className="mb-3 text-sm font-semibold">Connected Projects</h2>{isLoading ? <p className="text-sm text-zinc-400">読込中…</p> : <ApplicationProjectCards projects={data} />}
  </div></main>;
}
