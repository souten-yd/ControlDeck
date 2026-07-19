import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { applicationBuilderApi, type Diagnostic } from "../api/applicationBuilder";
import { PageHeader } from "../components/PageHeader";
import { AppDesignEditor } from "../features/application-builder/AppDesignEditor";

const severityStyle = { error: "border-red-300 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300", warning: "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300", suggestion: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300" };

export default function ApplicationEditorPage() {
  const projectId = Number(useParams().id);
  const navigate = useNavigate();
  const project = useQuery({ queryKey: ["application-project", projectId], queryFn: () => applicationBuilderApi.get(projectId), enabled: Number.isFinite(projectId) });
  const capabilities = useQuery({ queryKey: ["application-capabilities"], queryFn: applicationBuilderApi.capabilities });
  const schema = useQuery({ queryKey: ["application-builder-schema"], queryFn: applicationBuilderApi.schema });
  const validation = useQuery({ queryKey: ["application-validation", projectId, project.data?.updated_at], queryFn: () => applicationBuilderApi.validate(project.data!), enabled: Boolean(project.data) });
  const selectedFramework = useMemo(() => capabilities.data?.frameworks.find((item) => item.id === project.data?.ui_framework), [capabilities.data, project.data]);
  if (project.isLoading) return <div className="p-6 text-sm text-zinc-400">読込中…</div>;
  if (!project.data) return <div className="p-6 text-sm text-red-500">Projectを読み込めません。</div>;
  const app = (project.data.spec.application ?? {}) as Record<string, unknown>;
  const diagnostics = validation.data?.diagnostics ?? [];
  return <main className="min-h-0 flex-1 overflow-y-auto p-3 pb-24 md:p-6"><div className="mx-auto max-w-6xl">
    <PageHeader leading={<button onClick={() => navigate("/applications")} aria-label="Back to App Studio" className="grid min-h-11 min-w-11 place-items-center rounded-xl text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900">←</button>} title={<span className="flex min-w-0 flex-wrap items-center gap-2"><span className="truncate">{project.data.name}</span><span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-semibold leading-5 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">Draft</span><span className="rounded-full bg-zinc-100 px-2 py-1 text-[10px] font-medium leading-5 dark:bg-zinc-800">F1.2</span></span>} description="Semantic componentsでresponsive UIを設計します。生成やbuildはまだ実行しません。" actions={project.data.workflow_id ? <button onClick={() => navigate(`/workflows/${project.data.workflow_id}`)} className="min-h-11 rounded-xl border border-zinc-300 px-3 text-xs font-medium dark:border-zinc-700">Open Workflow</button> : undefined} />
    {schema.data ? <AppDesignEditor project={project.data} catalog={schema.data.semanticComponents.components} /> : <div className="mb-4 h-52 animate-pulse rounded-2xl bg-zinc-100 dark:bg-zinc-900" />}
    <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(300px,0.7fr)]">
      <div className="space-y-4">
        <Panel title="概要"><Info label="Application" value={String(app.displayName || app.name || project.data.name)} /><Info label="形式" value={project.data.application_type} /><Info label="Workflow" value={project.data.workflow_id ? `#${project.data.workflow_id}` : "未接続"} /><Info label="Spec" value={`v${project.data.schema_version}`} /></Panel>
        <Panel title="Application IR"><div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><Count label="Pages" value={validation.data?.applicationIr.pages.length ?? 0} /><Count label="Entities" value={validation.data?.applicationIr.entities.length ?? 0} /><Count label="API" value={validation.data?.applicationIr.api_endpoints.length ?? 0} /><Count label="Targets" value={validation.data?.applicationIr.targets.length ?? 0} /></div></Panel>
        {validation.data?.workflowIr && <Panel title="Workflow IR"><div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><Count label="Inputs" value={validation.data.workflowIr.inputs.length} /><Count label="Outputs" value={validation.data.workflowIr.outputs.length} /><Count label="Nodes" value={validation.data.workflowIr.nodes.length} /><Count label="Edges" value={validation.data.workflowIr.edges.length} /></div><p className="mt-3 text-[11px] text-zinc-500">Capability: {validation.data.workflowIr.capabilities.join(", ") || "なし"}</p></Panel>}
      </div>
      <div className="space-y-4">
        <Panel title="Target capability"><Info label="Framework" value={selectedFramework?.label ?? project.data.ui_framework} /><Info label="Platforms" value={selectedFramework?.platforms.join(" / ") ?? "確認中"} /><Info label="実装状態" value={selectedFramework?.status ?? "確認中"} /><div className="mt-3 rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300">Source生成: 未実装<br />Build: 未実装<br />{capabilities.data?.host.note}</div></Panel>
        <Panel title={`Diagnostics (${diagnostics.length})`}>{validation.isLoading ? <p className="text-xs text-zinc-400">検証中…</p> : diagnostics.length === 0 ? <p className="text-xs text-emerald-600">blocking errorはありません。</p> : <div className="space-y-2">{diagnostics.map((item, index) => <DiagnosticCard key={`${item.code}-${index}`} item={item} />)}</div>}</Panel>
      </div>
    </div>
  </div></main>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) { return <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"><h2 className="mb-3 text-sm font-semibold">{title}</h2>{children}</section>; }
function Info({ label, value }: { label: string; value: string }) { return <div className="flex gap-3 border-b border-zinc-100 py-2 text-xs last:border-0 dark:border-zinc-800"><span className="w-24 shrink-0 text-zinc-400">{label}</span><strong className="min-w-0 break-words font-medium">{value}</strong></div>; }
function Count({ label, value }: { label: string; value: number }) { return <div className="rounded-xl bg-zinc-50 p-3 text-center dark:bg-zinc-950"><strong className="block text-xl tabular-nums">{value}</strong><span className="text-[10px] text-zinc-400">{label}</span></div>; }
function DiagnosticCard({ item }: { item: Diagnostic }) { return <article className={`rounded-xl border p-3 text-xs ${severityStyle[item.severity]}`}><div className="flex gap-2"><strong className="min-w-0 flex-1">{item.message}</strong><code className="text-[9px] opacity-70">{item.code}</code></div>{item.path && <p className="mt-1 break-all font-mono text-[9px] opacity-70">{item.path}</p>}{item.suggestedFix && <p className="mt-2 text-[10px]">推奨: {item.suggestedFix}</p>}</article>; }
