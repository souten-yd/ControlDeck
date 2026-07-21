import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { applicationBuilderApi, type ApplicationProject, type Diagnostic } from "../api/applicationBuilder";
import { PageHeader } from "../components/PageHeader";
import { AppDesignEditor } from "../features/application-builder/AppDesignEditor";
import { PlatformAdvisorPanel } from "../features/application-builder/PlatformAdvisorPanel";
import { SourceGenerationPanel } from "../features/application-builder/SourceGenerationPanel";

const severityStyle = { error: "border-red-300 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300", warning: "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300", suggestion: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300" };

export default function ApplicationEditorPage() {
  const projectId = Number(useParams().id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const project = useQuery({ queryKey: ["application-project", projectId], queryFn: () => applicationBuilderApi.get(projectId), enabled: Number.isFinite(projectId) });
  const capabilities = useQuery({ queryKey: ["application-capabilities"], queryFn: applicationBuilderApi.capabilities });
  const schema = useQuery({ queryKey: ["application-builder-schema"], queryFn: applicationBuilderApi.schema });
  const validation = useQuery({ queryKey: ["application-validation", projectId, project.data?.updated_at], queryFn: () => applicationBuilderApi.validate(project.data!), enabled: Boolean(project.data) });
  const [workspace, setWorkspace] = useState<"create" | "target" | "export" | "review">("create");
  if (project.isLoading) return <div className="p-6 text-sm text-zinc-400">読込中…</div>;
  if (!project.data) return <div className="p-6 text-sm text-red-500">Projectを読み込めません。</div>;
  const app = (project.data.spec.application ?? {}) as Record<string, unknown>;
  const diagnostics = validation.data?.diagnostics ?? [];
  const tabs = [
    { id: "create" as const, label: "Create", hint: "Design & data" },
    { id: "target" as const, label: "Target", hint: "Linux & Windows" },
    { id: "export" as const, label: "Export", hint: "Source package" },
    { id: "review" as const, label: "Review", hint: diagnostics.length ? `${diagnostics.length} diagnostic${diagnostics.length === 1 ? "" : "s"}` : "Ready" },
  ];
  return <main className="min-h-0 flex-1 overflow-y-auto bg-zinc-50/70 p-3 pb-24 dark:bg-zinc-950/40 md:p-6"><div className="mx-auto max-w-[1500px]">
    <PageHeader leading={<button onClick={() => navigate("/applications")} aria-label="Back to App Studio" className="grid min-h-11 min-w-11 place-items-center rounded-xl text-zinc-500 transition hover:bg-white hover:shadow-sm dark:hover:bg-zinc-900">←</button>} title={<span className="flex min-w-0 flex-wrap items-center gap-2"><span className="truncate">{project.data.name}</span><span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-semibold leading-5 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">Draft</span><span className="rounded-full bg-zinc-900 px-2 py-1 text-[10px] font-medium leading-5 text-white dark:bg-white dark:text-zinc-900">E7</span></span>} description="Design the app, choose where it runs, then export deterministic source." actions={project.data.workflow_id ? <button onClick={() => navigate(`/workflows/${project.data.workflow_id}`)} className="min-h-11 rounded-xl border border-zinc-300 bg-white px-3 text-xs font-medium shadow-sm transition hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900">Open Workflow</button> : undefined} />
    <nav aria-label="Application workspace" className="sticky top-0 z-20 mb-4 grid grid-cols-4 gap-1 rounded-2xl border border-zinc-200 bg-white/90 p-1.5 shadow-sm backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/90">{tabs.map((tab) => <button key={tab.id} type="button" onClick={() => setWorkspace(tab.id)} aria-current={workspace === tab.id ? "page" : undefined} className={`min-h-12 min-w-0 rounded-xl px-2 text-left transition ${workspace === tab.id ? "bg-zinc-900 text-white shadow-sm dark:bg-white dark:text-zinc-900" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}><strong className="block truncate text-xs">{tab.label}</strong><span className={`hidden truncate text-[9px] sm:block ${workspace === tab.id ? "opacity-70" : "text-zinc-400"}`}>{tab.hint}</span></button>)}</nav>
    <div hidden={workspace !== "create"}>{schema.data ? <AppDesignEditor project={project.data} catalog={schema.data.semanticComponents} onOpenBuild={() => setWorkspace("export")} /> : <div className="h-52 animate-pulse rounded-2xl bg-zinc-100 dark:bg-zinc-900" />}</div>
    <div hidden={workspace !== "target"}><PlatformAdvisorPanel project={project.data} capabilities={capabilities.data} /></div>
    <div hidden={workspace !== "export"}><SourceGenerationPanel project={project.data} capabilities={capabilities.data} /></div>
    <div hidden={workspace !== "review"} className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.72fr)]">
      <div className="space-y-4">
        <Panel title="Application"><Info label="Name" value={String(app.displayName || app.name || project.data.name)} /><Info label="Type" value={project.data.application_type} /><Info label="Workflow" value={project.data.workflow_id ? `#${project.data.workflow_id}` : "Not connected"} /><Info label="Spec" value={`v${project.data.schema_version}`} /></Panel>
        <LlmRuntimeSettings project={project.data} onSaved={() => qc.invalidateQueries({ queryKey: ["application-project", projectId] })} />
        <Panel title="Application IR"><div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><Count label="Pages" value={validation.data?.applicationIr.pages.length ?? 0} /><Count label="Entities" value={validation.data?.applicationIr.entities.length ?? 0} /><Count label="API" value={validation.data?.applicationIr.api_endpoints.length ?? 0} /><Count label="Targets" value={validation.data?.applicationIr.targets.length ?? 0} /></div></Panel>
        {validation.data?.workflowIr && <Panel title="Workflow IR"><div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><Count label="Inputs" value={validation.data.workflowIr.inputs.length} /><Count label="Outputs" value={validation.data.workflowIr.outputs.length} /><Count label="Nodes" value={validation.data.workflowIr.nodes.length} /><Count label="Edges" value={validation.data.workflowIr.edges.length} /></div><p className="mt-3 text-[11px] text-zinc-500">Capability: {validation.data.workflowIr.capabilities.join(", ") || "なし"}</p></Panel>}
      </div>
      <div className="space-y-4">
        <Panel title="Generation boundary"><div className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs leading-relaxed text-blue-700 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300">Linux／Windows向けC# ConsoleまたはASP.NET Core sourceを決定的に生成します。Preview／ZIP生成はexecutor、network、subprocess、file write、Secret解決を行いません。BuildはExportで明示した場合だけ、networkを拒否した一時systemd user unit内で復元・ビルド・自己テストします。<br />{capabilities.data?.build.note ?? capabilities.data?.host.note}</div></Panel>
        <Panel title={`Diagnostics (${diagnostics.length})`}>{validation.isLoading ? <p className="text-xs text-zinc-400">検証中…</p> : diagnostics.length === 0 ? <p className="text-xs text-emerald-600">blocking errorはありません。</p> : <div className="space-y-2">{diagnostics.map((item, index) => <DiagnosticCard key={`${item.code}-${index}`} item={item} />)}</div>}</Panel>
      </div>
    </div>
  </div></main>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) { return <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"><h2 className="mb-3 text-sm font-semibold">{title}</h2>{children}</section>; }
function Info({ label, value }: { label: string; value: string }) { return <div className="flex gap-3 border-b border-zinc-100 py-2 text-xs last:border-0 dark:border-zinc-800"><span className="w-24 shrink-0 text-zinc-400">{label}</span><strong className="min-w-0 break-words font-medium">{value}</strong></div>; }
function Count({ label, value }: { label: string; value: number }) { return <div className="rounded-xl bg-zinc-50 p-3 text-center dark:bg-zinc-950"><strong className="block text-xl tabular-nums">{value}</strong><span className="text-[10px] text-zinc-400">{label}</span></div>; }
function DiagnosticCard({ item }: { item: Diagnostic }) { return <article className={`rounded-xl border p-3 text-xs ${severityStyle[item.severity]}`}><div className="flex gap-2"><strong className="min-w-0 flex-1">{item.message}</strong><code className="text-[9px] opacity-70">{item.code}</code></div>{item.path && <p className="mt-1 break-all font-mono text-[9px] opacity-70">{item.path}</p>}{item.suggestedFix && <p className="mt-2 text-[10px]">推奨: {item.suggestedFix}</p>}</article>; }

function LlmRuntimeSettings({ project, onSaved }: { project: ApplicationProject; onSaved: () => Promise<unknown> }) {
  const current = (project.spec.llmRuntime ?? {}) as Record<string, unknown>;
  const mode = String(current.mode ?? "none");
  const provider = String(current.provider ?? "ollama");
  const save = useMutation({
    mutationFn: (patch: Record<string, unknown>) => applicationBuilderApi.update(project.id, {
      spec: { ...project.spec, llmRuntime: { ...current, ...patch, bundleRuntime: false, baseUrlEnvironment: "LLM_BASE_URL", modelEnvironment: "LLM_MODEL" } },
    }),
    onSuccess: onSaved,
  });
  return <Panel title="LLM Runtime"><p className="mb-3 text-xs leading-relaxed text-zinc-500">生成アプリへmodelやruntimeを同梱せず、LM Studio／Ollamaへ接続する構成を選べます。</p><label className="block text-xs text-zinc-500">Integration<select aria-label="LLM runtime integration" value={mode} onChange={(event) => save.mutate({ mode: event.target.value, provider: event.target.value === "external" ? provider : null })} disabled={save.isPending} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-transparent px-3 dark:border-zinc-700"><option value="none">None</option><option value="external">External provider · not bundled</option><option value="embedded" disabled>Embedded runtime · planned</option><option value="remote" disabled>Remote ControlDeck · planned</option></select></label>{mode === "external" && <label className="mt-3 block text-xs text-zinc-500">Provider<select aria-label="External LLM provider" value={provider} onChange={(event) => save.mutate({ mode: "external", provider: event.target.value })} disabled={save.isPending} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-transparent px-3 dark:border-zinc-700"><option value="ollama">Ollama</option><option value="lmstudio">LM Studio</option><option value="openai-compatible">OpenAI compatible</option></select></label>}<p className="mt-3 text-[10px] text-zinc-400">接続先とmodelは生成時にLLM_BASE_URL／LLM_MODELから受け取り、Secretやruntime binaryは成果物へ保存しません。</p></Panel>;
}
