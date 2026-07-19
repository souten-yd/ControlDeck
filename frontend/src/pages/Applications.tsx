import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { applicationBuilderApi } from "../api/applicationBuilder";
import { ApplicationProjectCards } from "../features/application-builder/ApplicationProjectCards";
import { useToasts } from "../stores";

export default function ApplicationsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("New Application");
  const { data = [], isLoading } = useQuery({ queryKey: ["application-projects"], queryFn: () => applicationBuilderApi.list() });
  const create = useMutation({
    mutationFn: () => applicationBuilderApi.create({ name }),
    onSuccess: async (project) => { await qc.invalidateQueries({ queryKey: ["application-projects"] }); navigate(`/applications/${project.id}`); },
    onError: (error) => show(error instanceof Error ? error.message : "作成に失敗しました", "error"),
  });
  return <main className="min-h-0 flex-1 overflow-y-auto p-4 pb-24 md:p-6">
    <div className="mx-auto max-w-6xl">
      <header className="mb-5 flex items-start gap-3"><div className="min-w-0 flex-1"><h1 className="text-xl font-semibold">Application Builder</h1><p className="mt-1 text-xs text-zinc-500">WorkflowとApplication Specを検証するPhase A。生成・ビルドはまだ実行しません。</p></div><button onClick={() => setCreating(true)} className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">新規Project</button></header>
      {creating && <section className="mb-4 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"><label className="text-xs font-medium">Project名<input autoFocus value={name} onChange={(event) => setName(event.target.value)} className="mt-1 block min-h-11 w-full rounded-xl border border-zinc-300 bg-transparent px-3 text-base dark:border-zinc-700" /></label><div className="mt-3 grid grid-cols-2 gap-2"><button onClick={() => setCreating(false)} className="min-h-11 rounded-xl border border-zinc-300 text-sm dark:border-zinc-700">取消</button><button disabled={!name.trim() || create.isPending} onClick={() => create.mutate()} className="min-h-11 rounded-xl bg-accent-600 text-sm font-semibold text-white disabled:opacity-40">作成</button></div></section>}
      {isLoading ? <p className="text-sm text-zinc-400">読込中…</p> : <ApplicationProjectCards projects={data} />}
    </div>
  </main>;
}
