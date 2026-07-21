import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ApplicationClientState, ApplicationEntity, ApplicationPatchOperation, ApplicationProject, ApplicationQuery, ComponentDefinition, ComponentPropertyDefinition, DesignPresetDefinition, DesignTemplateDefinition, DesignTemplateParameterDefinition, SemanticComponent, SemanticComponentCatalog } from "../../api/applicationBuilder";
import { applicationBuilderApi } from "../../api/applicationBuilder";
import { BottomSheet } from "../../components/ui";
import { useToasts } from "../../stores";
import { findComponent, instantiateTemplate, pagesOf, parentOf, removeComponent, uniqueComponentId, updateComponent, type AppPage } from "./editorModel";
import { ProposalDiffPanel } from "./ProposalDiffPanel";
import { DesignProposalGallery } from "./DesignProposalGallery";
import { AppSpecPreview, type AppPreviewState as PreviewState, type AppPreviewViewport as Viewport } from "./AppSpecPreview";
import { auditApplicationPreview, type AccessibilityAuditResult } from "./accessibilityAudit";
import { EntityEditor } from "./EntityEditor";
import { ClientStateEditor } from "./ClientStateEditor";
import { QueryEditor } from "./QueryEditor";


export function AppDesignEditor({ project, catalog, onOpenBuild }: { project: ApplicationProject; catalog: SemanticComponentCatalog; onOpenBuild?: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [spec, setSpec] = useState<Record<string, unknown>>(() => structuredClone(project.spec));
  const [past, setPast] = useState<Record<string, unknown>[]>([]);
  const [future, setFuture] = useState<Record<string, unknown>[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [viewport, setViewport] = useState<Viewport>("desktop");
  const [previewState, setPreviewState] = useState<PreviewState>("default");
  const [patchReviewOpen, setPatchReviewOpen] = useState(false);
  const [aiDesignOpen, setAiDesignOpen] = useState(false);
  const [proposalOperations, setProposalOperations] = useState<ApplicationPatchOperation[]>([]);
  const [templateRequest, setTemplateRequest] = useState<{ template: DesignTemplateDefinition; rootOnly: boolean } | null>(null);
  const [auditPending, setAuditPending] = useState(false);
  const [auditResult, setAuditResult] = useState<AccessibilityAuditResult | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const [workArea, setWorkArea] = useState<"canvas" | "data">("canvas");
  const [mobilePanel, setMobilePanel] = useState<"add" | "layers" | "inspect" | null>(null);
  const previewRef = useRef<HTMLElement>(null);
  const editorRef = useRef<HTMLElement>(null);
  const pages = pagesOf(spec);
  const page = pages[0];
  const root = page?.root ?? null;
  const selected = selectedId ? findComponent(root, selectedId) : null;
  const definitions = useMemo(() => new Map(catalog.components.map((item) => [item.type, item])), [catalog.components]);
  const dirty = JSON.stringify(spec) !== JSON.stringify(project.spec);
  const theme = spec.theme && typeof spec.theme === "object" && !Array.isArray(spec.theme) ? spec.theme as Record<string, unknown> : {};
  const entities = Array.isArray(spec.entities) ? spec.entities as ApplicationEntity[] : [];
  const clientStates = Array.isArray(spec.clientState) ? spec.clientState as ApplicationClientState[] : [];
  const queries = Array.isArray(spec.queries) ? spec.queries as ApplicationQuery[] : [];
  const apiEndpoints = Array.isArray(spec.apiEndpoints)
    ? spec.apiEndpoints.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
  const workflowBindings = Array.isArray(spec.workflows)
    ? spec.workflows.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
  const advisor = spec.xAppAdvisor && typeof spec.xAppAdvisor === "object" && !Array.isArray(spec.xAppAdvisor)
    ? spec.xAppAdvisor as Record<string, unknown> : null;
  const advisorInputs = advisor && Array.isArray(advisor.inputs) ? advisor.inputs as Record<string, unknown>[] : [];
  const advisorOutputs = advisor && Array.isArray(advisor.outputs) ? advisor.outputs as Record<string, unknown>[] : [];

  useEffect(() => {
    setSpec(structuredClone(project.spec)); setPast([]); setFuture([]); setSelectedId(null);
  }, [project.id, project.updated_at, project.spec]);
  useEffect(() => {
    if (!auditPending || previewState !== "default") return;
    const frame = requestAnimationFrame(() => {
      if (previewRef.current) { setAuditResult(auditApplicationPreview(previewRef.current, catalog.accessibilityAudit)); setAuditOpen(true); }
      setAuditPending(false);
    });
    return () => cancelAnimationFrame(frame);
  }, [auditPending, catalog.accessibilityAudit, previewState]);

  const commit = (next: Record<string, unknown>) => {
    setPast((items) => [...items.slice(-49), spec]); setFuture([]); setSpec(next);
  };
  const withPage = (nextPage: AppPage) => commit({ ...spec, pages: [nextPage, ...pages.slice(1)] });
  const save = useMutation({
    mutationFn: () => applicationBuilderApi.update(project.id, { spec }),
    onSuccess: async () => { show("Application Specを保存しました"); await qc.invalidateQueries({ queryKey: ["application-project", project.id] }); },
    onError: (reason) => show(reason instanceof Error ? reason.message : "保存に失敗しました", "error"),
  });
  const initializePage = () => {
    const initialRoot: SemanticComponent = { id: "page-root", type: "layout.stack", properties: { gap: "md", direction: "vertical" }, children: [] };
    const nextPage = page ? { ...page, root: initialRoot } : { id: "home", title: "Home", root: initialRoot };
    commit({ ...spec, pages: [nextPage, ...pages.slice(page ? 1 : 0)] }); setSelectedId(initialRoot.id);
  };
  const addComponent = (definition: ComponentDefinition) => {
    if (!page || !root) return;
    const target = selected && definitions.get(selected.type)?.container ? selected : root;
    const item: SemanticComponent = { id: uniqueComponentId(root, definition.type), type: definition.type, properties: structuredClone(definition.defaults), children: [] };
    const nextRoot = updateComponent(root, target.id, (component) => ({ ...component, children: [...(component.children ?? []), item] }));
    withPage({ ...page, root: nextRoot }); setSelectedId(item.id);
  };
  const addTemplate = (template: DesignTemplateDefinition, rootOnly: boolean, values: Record<string, unknown>) => {
    if (!page || !root) return;
    const target = !rootOnly && selected && definitions.get(selected.type)?.container ? selected : root;
    const item = instantiateTemplate(root, template, values);
    const nextRoot = updateComponent(root, target.id, (component) => ({ ...component, children: [...(component.children ?? []), item] }));
    withPage({ ...page, root: nextRoot }); setSelectedId(item.id);
  };
  const requestTemplate = (template: DesignTemplateDefinition, rootOnly: boolean) => {
    if (template.parameters.length) setTemplateRequest({ template, rootOnly });
    else addTemplate(template, rootOnly, {});
  };
  const applyPreset = (preset: DesignPresetDefinition) => {
    commit({ ...spec, theme: { ...theme, preset: preset.id, tokens: structuredClone(preset.tokens) } });
  };
  const patchSelected = (patch: Partial<SemanticComponent>) => {
    if (!selected || !root || !page) return;
    withPage({ ...page, root: updateComponent(root, selected.id, (component) => ({ ...component, ...patch })) });
  };
  const removeSelected = () => {
    if (!selected || !root || !page || selected.id === root.id) return;
    withPage({ ...page, root: removeComponent(root, selected.id) }); setSelectedId(null);
  };
  const moveComponent = (componentId: string, offset: -1 | 1) => {
    if (!root || !page) return;
    const parent = parentOf(root, componentId); if (!parent) return;
    const children = [...(parent.children ?? [])]; const index = children.findIndex((item) => item.id === componentId); const target = index + offset;
    if (target < 0 || target >= children.length) return;
    [children[index], children[target]] = [children[target], children[index]];
    withPage({ ...page, root: updateComponent(root, parent.id, (item) => ({ ...item, children })) });
  };
  const move = (offset: -1 | 1) => { if (selected) moveComponent(selected.id, offset); };
  const reparent = (sourceId: string, targetId: string) => {
    if (!root || !page || sourceId === root.id || sourceId === targetId) return;
    const source = findComponent(root, sourceId); const target = findComponent(root, targetId);
    if (!source || !target || !definitions.get(target.type)?.container || findComponent(source, targetId)) return;
    const withoutSource = removeComponent(root, sourceId);
    const nextRoot = updateComponent(withoutSource, targetId, (item) => ({ ...item, children: [...(item.children ?? []), source] }));
    withPage({ ...page, root: nextRoot }); setSelectedId(sourceId);
  };
  const undo = () => { const previous = past[past.length - 1]; if (!previous) return; setFuture((items) => [spec, ...items]); setPast((items) => items.slice(0, -1)); setSpec(previous); };
  const redo = () => { const next = future[0]; if (!next) return; setPast((items) => [...items, spec]); setFuture((items) => items.slice(1)); setSpec(next); };
  const updateEntities = (nextEntities: ApplicationEntity[]) => {
    const application = spec.application && typeof spec.application === "object" && !Array.isArray(spec.application) ? spec.application as Record<string, unknown> : {};
    commit({ ...spec, application: { ...application, ...(nextEntities.length ? { database: "sqlite" } : {}) }, entities: nextEntities });
  };
  const updateClientStates = (nextStates: ApplicationClientState[]) => commit({ ...spec, clientState: nextStates });
  const updateQueries = (nextQueries: ApplicationQuery[]) => commit({ ...spec, queries: nextQueries });
  const selectWorkArea = (next: "canvas" | "data") => {
    setWorkArea(next);
    requestAnimationFrame(() => editorRef.current?.scrollIntoView({ block: "start" }));
  };

  return <><section ref={editorRef} aria-label="App Design Editor" className="scroll-mt-20 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
    <div className="relative z-10 flex min-h-16 flex-wrap items-center gap-2 border-b border-zinc-200 bg-white px-3 py-2 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mr-auto flex rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800" role="tablist" aria-label="Create workspace"><button type="button" role="tab" aria-selected={workArea === "canvas"} onClick={() => selectWorkArea("canvas")} className={`min-h-10 rounded-lg px-4 text-xs font-semibold transition ${workArea === "canvas" ? "bg-white shadow-sm dark:bg-zinc-700" : "text-zinc-500"}`}>Canvas</button><button type="button" role="tab" aria-selected={workArea === "data"} onClick={() => selectWorkArea("data")} className={`min-h-10 rounded-lg px-4 text-xs font-semibold transition ${workArea === "data" ? "bg-white shadow-sm dark:bg-zinc-700" : "text-zinc-500"}`}>Data</button></div>
      {workArea === "canvas" && <><button type="button" onClick={undo} disabled={!past.length} aria-label="Undo design change" className="min-h-10 rounded-lg px-2 text-xs disabled:opacity-30">Undo</button><button type="button" onClick={redo} disabled={!future.length} aria-label="Redo design change" className="min-h-10 rounded-lg px-2 text-xs disabled:opacity-30">Redo</button><div className="hidden rounded-xl bg-zinc-100 p-1 md:flex dark:bg-zinc-800">{(["mobile", "tablet", "desktop"] as Viewport[]).map((item) => <button key={item} onClick={() => setViewport(item)} aria-pressed={viewport === item} className={`min-h-9 rounded-lg px-2 text-[10px] capitalize ${viewport === item ? "bg-zinc-900 text-white dark:bg-white dark:text-zinc-900" : "text-zinc-500"}`}>{item}</button>)}</div><details className="relative"><summary className="flex min-h-10 cursor-pointer list-none items-center rounded-lg border border-zinc-300 px-3 text-xs dark:border-zinc-700">Tools</summary><div className="absolute right-0 z-30 mt-1 grid w-52 gap-1 rounded-xl border border-zinc-200 bg-white p-2 shadow-xl dark:border-zinc-700 dark:bg-zinc-900"><button onClick={() => setAiDesignOpen(true)} disabled={dirty} className="min-h-11 rounded-lg px-3 text-left text-xs hover:bg-zinc-100 disabled:opacity-35 dark:hover:bg-zinc-800">AI Design</button><button onClick={() => setPatchReviewOpen(true)} disabled={dirty} className="min-h-11 rounded-lg px-3 text-left text-xs hover:bg-zinc-100 disabled:opacity-35 dark:hover:bg-zinc-800">Review Patch</button><button onClick={() => { setPreviewState("default"); setAuditPending(true); }} disabled={!root || auditPending} className="min-h-11 rounded-lg px-3 text-left text-xs hover:bg-zinc-100 disabled:opacity-35 dark:hover:bg-zinc-800">{auditPending ? "Auditing…" : "Accessibility Audit"}</button><label className="px-3 pb-2 text-[10px] text-zinc-400">Preview state<select aria-label="Preview state" value={previewState} onChange={(event) => setPreviewState(event.target.value as PreviewState)} className="mt-1 min-h-10 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{catalog.previewStates.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label></div></details></>}
      <button onClick={() => save.mutate()} disabled={!dirty || save.isPending} className="min-h-10 rounded-xl bg-accent-600 px-4 text-xs font-semibold text-white shadow-sm disabled:opacity-40">{save.isPending ? "Saving…" : dirty ? "Save changes" : "Saved"}</button>
    </div>
    {advisor && <section aria-label="Workflow App Advisor" className="border-b border-emerald-200 bg-emerald-50/80 px-3 py-3 dark:border-emerald-900 dark:bg-emerald-950/20 md:px-4"><div className="flex flex-col gap-3 sm:flex-row sm:items-center"><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="rounded-full bg-emerald-600 px-2 py-1 text-[10px] font-semibold text-white">自動構成済み</span><strong className="text-xs">Workflow App Advisor</strong></div><p className="mt-2 text-xs leading-relaxed text-emerald-800 dark:text-emerald-200">{String(advisor.message ?? "Workflowの入出力から動作可能なGUIを提案しました。")}</p><p className="mt-1 truncate text-[10px] text-emerald-700/70 dark:text-emerald-300/70">入力: {advisorInputs.map((item) => String(item.label ?? item.name)).join("・") || "なし"} ／ 出力: {advisorOutputs.map((item) => String(item.label ?? item.name)).join("・") || "実行結果"}</p></div><div className="grid shrink-0 gap-2 sm:grid-cols-2"><button type="button" onClick={() => setAiDesignOpen(true)} disabled={dirty} className="min-h-11 rounded-xl border border-violet-300 bg-white px-4 text-xs font-semibold text-violet-700 disabled:opacity-40 dark:border-violet-800 dark:bg-zinc-900 dark:text-violet-300">AIに再検討</button>{onOpenBuild && <button type="button" onClick={onOpenBuild} disabled={dirty} className="min-h-11 rounded-xl bg-emerald-600 px-4 text-xs font-semibold text-white disabled:opacity-40">生成・動作確認へ</button>}</div></div><p className="mt-2 text-[10px] text-emerald-700/70 dark:text-emerald-300/70">そのまま生成できます。気になる箇所はCanvasで選択して手動修正するか、AI案を差分確認して適用できます。</p></section>}
    {workArea === "data" ? <div className="space-y-4 bg-zinc-50/60 p-3 dark:bg-zinc-950/30 md:p-4"><QueryEditor queries={queries} entities={entities} apiEndpoints={apiEndpoints} onChange={updateQueries} /><ClientStateEditor states={clientStates} dirty={dirty} saving={save.isPending} onChange={updateClientStates} onSave={() => save.mutate()} showSave={false} /><EntityEditor entities={entities} dirty={dirty} saving={save.isPending} onChange={updateEntities} onSave={() => save.mutate()} showSave={false} /></div> : <>
    {!page || !root ? <div className="grid min-h-72 place-items-center p-6 text-center"><div><p className="text-sm font-medium">{page ? "Page canvasは未初期化です" : "ページはまだありません"}</p><p className="mt-1 text-xs text-zinc-400">{page ? "既存Pageを維持してresponsive Stackを追加します。" : "最初のPageとresponsive Stackを作成します。"}</p><button onClick={initializePage} className="mt-4 min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">{page ? "Initialize Canvas" : "Add Page"}</button></div></div> :
    <div className="grid min-h-[520px] lg:grid-cols-[220px_minmax(0,1fr)_260px]">
      <aside className="hidden border-r border-zinc-200 p-3 dark:border-zinc-800 lg:block"><h3 className="mb-2 text-xs font-semibold text-zinc-500">Add</h3><label className="block text-[10px] text-zinc-400">Style preset<select aria-label="Design preset" value={String(((spec.theme as Record<string, unknown> | undefined)?.preset) ?? "control-deck-modern")} onChange={(event) => { const preset = catalog.presets.find((item) => item.id === event.target.value); if (preset) applyPreset(preset); }} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-200 bg-transparent px-2 text-xs dark:border-zinc-700">{catalog.presets.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><details className="mt-2"><summary className="min-h-10 cursor-pointer py-2 text-xs font-medium">Ready-made blocks</summary><div className="grid gap-1">{catalog.composites.map((item) => <button key={item.id} onClick={() => requestTemplate(item, false)} className="min-h-11 rounded-lg border border-zinc-200 px-2 text-left text-xs hover:border-accent-400 dark:border-zinc-700">{item.label}</button>)}</div></details><details><summary className="min-h-10 cursor-pointer py-2 text-xs font-medium">Page patterns</summary><div className="grid gap-1">{catalog.patterns.map((item) => <button key={item.id} onClick={() => requestTemplate(item, true)} className="min-h-11 rounded-lg border border-zinc-200 px-2 text-left text-xs hover:border-accent-400 dark:border-zinc-700">{item.label}</button>)}</div></details><details open><summary className="min-h-10 cursor-pointer py-2 text-xs font-medium">Components</summary><div className="grid gap-1">{catalog.components.map((item) => <button key={item.type} onClick={() => addComponent(item)} className="min-h-11 rounded-lg border border-zinc-200 px-2 text-left text-xs hover:border-accent-400 dark:border-zinc-700"><span className="block truncate font-medium">{item.label}</span><span className="text-[9px] text-zinc-400">{item.category}</span></button>)}</div></details><details className="mt-2"><summary className="min-h-10 cursor-pointer py-2 text-xs font-medium">Layers</summary>{root && <TreeNode item={root} selectedId={selectedId} onSelect={setSelectedId} onReparent={reparent} onMove={moveComponent} definitions={definitions} depth={0} />}</details></aside>
      <div className="min-w-0 overflow-auto bg-zinc-100 p-3 dark:bg-zinc-950"><AppSpecPreview spec={spec} catalog={catalog} viewport={viewport} previewState={previewState} selectedId={selectedId} onSelect={setSelectedId} testId="app-responsive-preview" label="Editor" containerRef={previewRef} /></div>
      <aside className="hidden border-l border-zinc-200 p-3 dark:border-zinc-800 lg:block"><h3 className="mb-3 text-xs font-semibold text-zinc-500">Inspector</h3>{selected ? <Inspector item={selected} definition={definitions.get(selected.type)} catalog={catalog} entities={entities} clientStates={clientStates} queries={queries} apiEndpoints={apiEndpoints} workflowBindings={workflowBindings} pageIds={pages.map((item) => item.id)} onPatch={patchSelected} onMove={move} onRemove={removeSelected} root={selected.id === root?.id} /> : <p className="text-xs text-zinc-400">Select a component on the canvas.</p>}</aside>
    </div>}
    {root && <div className="sticky bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-10 grid grid-cols-3 gap-1 border-t border-zinc-200 bg-white/95 p-2 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/95 lg:hidden"><button type="button" onClick={() => setMobilePanel("add")} className="min-h-11 rounded-xl text-xs font-medium">＋ Add</button><button type="button" onClick={() => setMobilePanel("layers")} className="min-h-11 rounded-xl text-xs font-medium">Layers</button><button type="button" onClick={() => setMobilePanel("inspect")} disabled={!selected} className="min-h-11 rounded-xl bg-zinc-900 text-xs font-medium text-white disabled:opacity-30 dark:bg-white dark:text-zinc-900">Inspect</button></div>}
    </>}
    {aiDesignOpen && <DesignProposalGallery project={project} catalog={catalog} selectedComponentId={selectedId} onClose={() => setAiDesignOpen(false)} onReview={(patches) => { setProposalOperations(patches); setAiDesignOpen(false); setPatchReviewOpen(true); }} />}
    {patchReviewOpen && <ProposalDiffPanel project={project} catalog={catalog} initialOperations={proposalOperations} onClose={() => { setPatchReviewOpen(false); setProposalOperations([]); }} />}
    {templateRequest && <TemplateParameterDialog template={templateRequest.template} onClose={() => setTemplateRequest(null)} onInsert={(values) => { addTemplate(templateRequest.template, templateRequest.rootOnly, values); setTemplateRequest(null); }} />}
    {auditOpen && auditResult && <AccessibilityAuditPanel result={auditResult} onClose={() => setAuditOpen(false)} onRunAgain={() => { setAuditOpen(false); setPreviewState("default"); setAuditPending(true); }} />}
  </section>{mobilePanel === "add" && <BottomSheet title="Add to canvas" onClose={() => setMobilePanel(null)} stable><div className="grid grid-cols-2 gap-2 pb-6">{catalog.components.map((item) => <button key={item.type} type="button" onClick={() => { addComponent(item); setMobilePanel(null); }} className="min-h-14 rounded-xl border border-zinc-200 px-3 text-left text-xs dark:border-zinc-700"><strong className="block">{item.label}</strong><span className="text-[9px] text-zinc-400">{item.category}</span></button>)}</div></BottomSheet>}{mobilePanel === "layers" && root && <BottomSheet title="Layers" onClose={() => setMobilePanel(null)} stable><div className="pb-6"><TreeNode item={root} selectedId={selectedId} onSelect={(id) => { setSelectedId(id); setMobilePanel(null); }} onReparent={reparent} onMove={moveComponent} definitions={definitions} depth={0} /></div></BottomSheet>}{mobilePanel === "inspect" && selected && <BottomSheet title="Inspector" onClose={() => setMobilePanel(null)} stable><div className="pb-6"><Inspector item={selected} definition={definitions.get(selected.type)} catalog={catalog} entities={entities} clientStates={clientStates} queries={queries} apiEndpoints={apiEndpoints} workflowBindings={workflowBindings} pageIds={pages.map((item) => item.id)} onPatch={patchSelected} onMove={move} onRemove={removeSelected} root={selected.id === root?.id} /></div></BottomSheet>}</>;
}

function AccessibilityAuditPanel({ result, onClose, onRunAgain }: { result: AccessibilityAuditResult; onClose: () => void; onRunAgain: () => void }) {
  const categories = ["contrast", "focus", "keyboard", "touch"] as const;
  return <BottomSheet title="Accessibility Audit" onClose={onClose} stable><div className="space-y-4 pb-4"><p className="text-xs leading-relaxed text-zinc-500">Default Previewの実DOMを現行catalogの閾値で検査しました。runtimeやEventは実行していません。</p><div className="grid grid-cols-2 gap-2">{categories.map((category) => { const failures = result.issues.filter((item) => item.category === category).length; return <div key={category} className={`rounded-xl p-3 ${failures ? "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300" : "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300"}`}><span className="block text-[10px] capitalize">{category}</span><strong className="mt-1 block text-sm tabular-nums">{failures ? `${failures} issue${failures === 1 ? "" : "s"}` : `${result.checked[category]} passed`}</strong></div>; })}</div>{result.issues.length === 0 ? <p role="status" className="rounded-xl bg-emerald-50 p-3 text-xs font-medium text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">Accessibility audit passed</p> : <section aria-label="Accessibility issues" className="space-y-2">{result.issues.map((issue, index) => <article key={`${issue.code}-${issue.componentId}-${index}`} className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"><div className="flex gap-2"><strong className="min-w-0 flex-1">{issue.message}</strong><code className="text-[9px]">{issue.code}</code></div><code className="mt-1 block break-all text-[9px] opacity-70">{issue.componentId}</code></article>)}</section>}<button type="button" onClick={onRunAgain} className="min-h-11 w-full rounded-xl border border-zinc-300 text-xs font-semibold dark:border-zinc-700">Run audit again</button></div></BottomSheet>;
}

function TemplateParameterDialog({ template, onClose, onInsert }: { template: DesignTemplateDefinition; onClose: () => void; onInsert: (values: Record<string, unknown>) => void }) {
  const [values, setValues] = useState<Record<string, string | boolean>>(() => Object.fromEntries(template.parameters.map((item) => [item.key, item.type === "number" ? String(item.default) : item.default])) as Record<string, string | boolean>);
  const errors = template.parameters.map((item) => parameterError(item, values[item.key])).filter(Boolean);
  const submit = () => {
    if (errors.length) return;
    onInsert(Object.fromEntries(template.parameters.map((item) => [item.key, item.type === "number" ? Number(values[item.key]) : values[item.key]])));
  };
  return <BottomSheet title={`Configure ${template.label}`} onClose={onClose} stable><div className="space-y-3 pb-4"><p className="text-xs leading-relaxed text-zinc-500">{template.description} 値はcatalogで宣言されたComponent propertyだけへ適用され、式やcodeは実行しません。</p><fieldset aria-label="Template parameters" className="space-y-3">{template.parameters.map((parameter) => <TemplateParameterField key={parameter.key} parameter={parameter} value={values[parameter.key]} onChange={(value) => setValues((current) => ({ ...current, [parameter.key]: value }))} />)}</fieldset>{errors.length > 0 && <p role="alert" className="rounded-lg bg-red-50 p-2 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-300">{errors[0]}</p>}<button type="button" onClick={submit} disabled={Boolean(errors.length)} className="min-h-12 w-full rounded-xl bg-accent-600 text-sm font-semibold text-white disabled:opacity-40">Insert template</button></div></BottomSheet>;
}

function TemplateParameterField({ parameter, value, onChange }: { parameter: DesignTemplateParameterDefinition; value: string | boolean; onChange: (value: string | boolean) => void }) {
  const label = `Template parameter ${parameter.label}`;
  if (parameter.type === "boolean") return <label className="flex min-h-11 items-center gap-2 rounded-lg bg-zinc-50 px-3 text-xs dark:bg-zinc-800"><input aria-label={label} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} className="h-5 w-5" />{parameter.label}</label>;
  if (parameter.type === "enum") return <label className="block text-xs text-zinc-500">{parameter.label}<select aria-label={label} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-3 dark:border-zinc-700">{(parameter.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}</select></label>;
  return <label className="block text-xs text-zinc-500">{parameter.label}{parameter.required ? " *" : ""}<input aria-label={label} type={parameter.type === "number" ? "number" : "text"} value={String(value ?? "")} min={parameter.minimum} max={parameter.maximum} maxLength={parameter.maximumLength} onChange={(event) => onChange(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-3 dark:border-zinc-700" /></label>;
}

function parameterError(parameter: DesignTemplateParameterDefinition, value: string | boolean | undefined): string {
  if (parameter.type === "number") { const number = Number(value); if (value === "" || !Number.isFinite(number)) return `${parameter.label}は数値で指定してください`; if (parameter.minimum !== undefined && number < parameter.minimum) return `${parameter.label}は${parameter.minimum}以上にしてください`; if (parameter.maximum !== undefined && number > parameter.maximum) return `${parameter.label}は${parameter.maximum}以下にしてください`; return ""; }
  if (parameter.type === "boolean") return "";
  const text = String(value ?? "");
  if (parameter.required && !text.trim()) return `${parameter.label}は必須です`;
  if (parameter.maximumLength !== undefined && text.length > parameter.maximumLength) return `${parameter.label}は${parameter.maximumLength}文字以内にしてください`;
  if (parameter.type === "enum" && !(parameter.options ?? []).includes(text)) return `${parameter.label}の値が未登録です`;
  return "";
}

function TreeNode({ item, selectedId, onSelect, onReparent, onMove, definitions, depth }: { item: SemanticComponent; selectedId: string | null; onSelect: (id: string) => void; onReparent: (source: string, target: string) => void; onMove: (id: string, offset: -1 | 1) => void; definitions: Map<string, ComponentDefinition>; depth: number }) {
  const container = definitions.get(item.type)?.container;
  return <div><button draggable={depth > 0} aria-keyshortcuts={depth > 0 ? "Alt+ArrowUp Alt+ArrowDown" : undefined} onKeyDown={(event) => { if (!event.altKey || depth === 0 || !["ArrowUp", "ArrowDown"].includes(event.key)) return; event.preventDefault(); onSelect(item.id); onMove(item.id, event.key === "ArrowUp" ? -1 : 1); }} onDragStart={(event) => { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/control-deck-component", item.id); }} onDragOver={(event) => { if (container) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; } }} onDrop={(event) => { if (!container) return; event.preventDefault(); const source = event.dataTransfer.getData("text/control-deck-component"); if (source) onReparent(source, item.id); }} onClick={() => onSelect(item.id)} className={`mb-1 flex min-h-10 w-full min-w-0 items-center rounded-lg px-2 text-left text-xs focus:outline focus:outline-2 focus:outline-offset-2 focus:outline-accent-500 ${selectedId === item.id ? "bg-accent-50 text-accent-700 dark:bg-accent-600/10" : container ? "hover:bg-accent-50/60 dark:hover:bg-accent-600/10" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`} style={{ paddingLeft: `${8 + depth * 12}px` }}><span className="truncate">{item.id}</span><code className="ml-auto pl-1 text-[8px] text-zinc-400">{item.type}</code></button>{(item.children ?? []).map((child) => <TreeNode key={child.id} item={child} selectedId={selectedId} onSelect={onSelect} onReparent={onReparent} onMove={onMove} definitions={definitions} depth={depth + 1} />)}</div>;
}

function Inspector({ item, definition, catalog, entities, clientStates, queries, apiEndpoints, workflowBindings, pageIds, onPatch, onMove, onRemove, root }: { item: SemanticComponent; definition?: ComponentDefinition; catalog: SemanticComponentCatalog; entities: ApplicationEntity[]; clientStates: ApplicationClientState[]; queries: ApplicationQuery[]; apiEndpoints: Record<string, unknown>[]; workflowBindings: Record<string, unknown>[]; pageIds: string[]; onPatch: (patch: Partial<SemanticComponent>) => void; onMove: (offset: -1 | 1) => void; onRemove: () => void; root: boolean }) {
  const [json, setJson] = useState(JSON.stringify(item.properties ?? {}, null, 2)); const [error, setError] = useState("");
  useEffect(() => { setJson(JSON.stringify(item.properties ?? {}, null, 2)); setError(""); }, [item.id, item.properties]);
  const applyJson = () => { try { const value = JSON.parse(json); if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(); onPatch({ properties: value }); setError(""); } catch { setError("JSON objectを入力してください"); } };
  const setProperty = (key: string, value: unknown) => onPatch({ properties: { ...(item.properties ?? {}), [key]: value } });
  const lockKeys = ["structure", "binding", "style", "position", "content"] as const;
  const selectedWorkflow = workflowBindings.find((entry) => String(entry.id ?? "") === String(item.properties?.workflowBinding ?? definition?.defaults.workflowBinding ?? ""));
  const matchingEndpoints = apiEndpoints.filter((entry) => String(entry.mode ?? "sync") === "sync" && entry.workflowId === selectedWorkflow?.workflowId);
  const propertyFields = definition?.propertySchema.filter((field) => field.type !== "json" && !(item.type === "data.table" && typeof item.binding === "string" && item.binding.startsWith("query:") && field.key === "pageSize")) ?? [];
  return <div className="space-y-3"><div className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-950"><p className="text-xs font-semibold">{definition?.label ?? item.type}</p><code className="mt-1 block break-all text-[9px] text-zinc-400">{item.id} · {item.type}</code></div>
    {propertyFields.length > 0 && <fieldset aria-label="Component properties" className="space-y-2"><legend className="mb-1 text-[10px] font-medium text-zinc-400">Content & appearance</legend>{propertyFields.map((field) => item.type === "action.workflow-run" && field.key === "workflowBinding" ? <ReferencePropertySelect key={field.key} field={field} value={(item.properties ?? {})[field.key] ?? definition?.defaults[field.key]} options={workflowBindings.map((entry) => ({ value: String(entry.id ?? ""), label: String(entry.id ?? "") })).filter((entry) => entry.value)} onChange={(value) => setProperty(field.key, value)} /> : item.type === "action.workflow-run" && field.key === "endpointId" ? <ReferencePropertySelect key={field.key} field={field} value={(item.properties ?? {})[field.key] ?? definition?.defaults[field.key]} options={matchingEndpoints.map((entry) => ({ value: String(entry.id ?? ""), label: `${String(entry.id ?? "")} · ${String(entry.path ?? "")}` })).filter((entry) => entry.value)} emptyLabel="Auto-select matching endpoint" onChange={(value) => setProperty(field.key, value)} /> : <SchemaPropertyField key={field.key} field={field} value={(item.properties ?? {})[field.key] ?? definition?.defaults[field.key]} onChange={(value) => setProperty(field.key, value)} />)}</fieldset>}
    <details className="rounded-xl border border-zinc-200 px-3 dark:border-zinc-700"><summary className="min-h-11 cursor-pointer py-3 text-xs font-medium">Data binding{item.binding ? <span className="ml-2 text-[9px] font-normal text-accent-600">Connected</span> : null}</summary><div className="pb-3"><BindingEditor value={item.binding} definitions={catalog.bindingDefinitions} entities={entities} clientStates={clientStates} queries={queries} onChange={(binding) => onPatch({ binding })} /></div></details>
    {Boolean(definition?.eventSchema.length) && <details className="rounded-xl border border-zinc-200 px-3 dark:border-zinc-700"><summary className="min-h-11 cursor-pointer py-3 text-xs font-medium">Interactions{Object.keys(item.events ?? {}).length ? <span className="ml-2 text-[9px] font-normal text-accent-600">{Object.keys(item.events ?? {}).length} active</span> : null}</summary><div className="pb-3"><EventEditor value={item.events} definitions={definition?.eventSchema ?? []} actions={catalog.eventActions} pageIds={pageIds} stateIds={clientStates.map((state) => state.id)} onChange={(events) => onPatch({ events })} /></div></details>}
    <details className="rounded-xl border border-zinc-200 px-3 dark:border-zinc-700"><summary className="min-h-11 cursor-pointer py-3 text-xs font-medium">Advanced</summary><div className="space-y-3 pb-3"><label className="block text-[10px] text-zinc-400">Properties JSON<textarea value={json} onChange={(event) => setJson(event.target.value)} onBlur={applyJson} rows={7} className="mt-1 w-full rounded-lg border border-zinc-300 bg-transparent p-2 font-mono text-[11px] dark:border-zinc-700" /></label>{error && <p role="alert" className="text-[10px] text-red-500">{error}</p>}<fieldset><legend className="mb-1 text-[10px] text-zinc-400">AI redesign locks</legend><div className="grid grid-cols-2 gap-1">{lockKeys.map((key) => <label key={key} className="flex min-h-10 items-center gap-2 rounded-lg bg-zinc-50 px-2 text-[10px] capitalize dark:bg-zinc-800"><input type="checkbox" checked={Boolean(item.locked?.[key])} onChange={(event) => onPatch({ locked: { ...(item.locked ?? {}), [key]: event.target.checked } })} className="h-4 w-4" />{key}</label>)}</div></fieldset></div></details>
    <div className="grid grid-cols-2 gap-2"><button onClick={() => onMove(-1)} disabled={root} className="min-h-11 rounded-lg border border-zinc-300 text-xs disabled:opacity-30 dark:border-zinc-700">Move up</button><button onClick={() => onMove(1)} disabled={root} className="min-h-11 rounded-lg border border-zinc-300 text-xs disabled:opacity-30 dark:border-zinc-700">Move down</button></div><button onClick={onRemove} disabled={root} className="min-h-11 w-full rounded-lg text-xs text-red-600 disabled:opacity-30">Remove component</button></div>;
}

function ReferencePropertySelect({ field, value, options, emptyLabel, onChange }: { field: ComponentPropertyDefinition; value: unknown; options: Array<{ value: string; label: string }>; emptyLabel?: string; onChange: (value: string) => void }) {
  const current = String(value ?? "");
  const known = !current || options.some((option) => option.value === current);
  return <label className="block text-[10px] text-zinc-400">{field.label}{field.required ? " *" : ""}<select aria-label={`Property ${field.label}`} value={current} onChange={(event) => onChange(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{emptyLabel && <option value="">{emptyLabel}</option>}{!known && <option value={current}>{current} · unavailable</option>}{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
}

function BindingEditor({ value, definitions, entities, clientStates, queries, onChange }: { value: SemanticComponent["binding"]; definitions: SemanticComponentCatalog["bindingDefinitions"]; entities: ApplicationEntity[]; clientStates: ApplicationClientState[]; queries: ApplicationQuery[]; onChange: (value: string | null) => void }) {
  const parsed = useMemo(() => {
    if (typeof value === "string") { const separator = value.indexOf(":"); return separator < 0 ? { source: definitions[0]?.id ?? "constant", reference: value } : { source: value.slice(0, separator), reference: value.slice(separator + 1) }; }
    if (value && typeof value === "object") return { source: String(value.source ?? definitions[0]?.id ?? "constant"), reference: String(value.reference ?? value.path ?? "") };
    return { source: definitions[0]?.id ?? "constant", reference: "" };
  }, [definitions, value]);
  const [source, setSource] = useState(parsed.source);
  const [reference, setReference] = useState(parsed.reference);
  useEffect(() => setSource(parsed.source), [parsed.source]);
  useEffect(() => setReference(parsed.reference), [parsed.reference]);
  const commit = (source: string, nextReference: string) => onChange(nextReference.trim() ? `${source}:${nextReference}` : null);
  const selectedDefinition = definitions.find((item) => item.id === source);
  const entityOptions = entities.flatMap((entity) => [entity.id, "id", "createdAt", "updatedAt", ...entity.fields.map((field) => field.id)].map((field, index) => index === 0 ? entity.id : `${entity.id}.${field}`));
  const stateOptions = clientStates.map((state) => state.id);
  const queryOptions = queries.map((query) => query.id);
  return <fieldset aria-label="Binding" className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-700"><legend className="px-1 text-[10px] text-zinc-400">Binding</legend><label className="block text-[10px] text-zinc-400">Source<select aria-label="Binding source" value={source} onChange={(event) => { const nextSource = event.target.value; const nextReference = nextSource === "entity" ? entityOptions[0] ?? "" : nextSource === "state" ? stateOptions[0] ?? "" : nextSource === "query" ? queryOptions[0] ?? "" : reference; setSource(nextSource); setReference(nextReference); commit(nextSource, nextReference); }} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{definitions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>{source === "entity" ? <label className="mt-2 block text-[10px] text-zinc-400">Entity and field<select aria-label="Binding entity reference" value={reference} onChange={(event) => { setReference(event.target.value); commit(source, event.target.value); }} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="">Select Entity</option>{entityOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label> : source === "state" ? <label className="mt-2 block text-[10px] text-zinc-400">State key<select aria-label="Binding state reference" value={reference} onChange={(event) => { setReference(event.target.value); commit(source, event.target.value); }} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="">Select State</option>{stateOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label> : source === "query" ? <label className="mt-2 block text-[10px] text-zinc-400">Query<select aria-label="Binding query reference" value={reference} onChange={(event) => { setReference(event.target.value); commit(source, event.target.value); }} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="">Select Query</option>{queryOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label> : <label className="mt-2 block text-[10px] text-zinc-400">{selectedDefinition?.referenceLabel ?? "Reference"}<input aria-label="Binding reference" value={reference} onChange={(event) => setReference(event.target.value)} onBlur={() => commit(source, reference)} placeholder="answer" className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label>}</fieldset>;
}

function EventEditor({ value, definitions, actions, pageIds, stateIds, onChange }: { value: SemanticComponent["events"]; definitions: NonNullable<ComponentDefinition["eventSchema"]>; actions: SemanticComponentCatalog["eventActions"]; pageIds: string[]; stateIds: string[]; onChange: (value: Record<string, unknown>) => void }) {
  const events = value && typeof value === "object" ? value : {};
  const update = (name: string, config: Record<string, unknown> | null) => {
    const next = { ...events };
    if (config) next[name] = config; else delete next[name];
    onChange(next);
  };
  return <fieldset aria-label="Events" className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-700"><legend className="px-1 text-[10px] text-zinc-400">Events</legend><div className="space-y-2">{definitions.map((definition) => {
    const raw = events[definition.name];
    const config = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : null;
    const action = String(config?.action ?? definition.actions[0] ?? "");
    const actionDefinition = actions.find((item) => item.id === action);
    const defaultPage = pageIds[0] ?? "home";
    const defaultState = stateIds[0] ?? "";
    return <div key={definition.name} className="rounded-lg bg-zinc-50 p-2 dark:bg-zinc-800"><label className="flex min-h-11 items-center gap-2 text-xs"><input aria-label={`Enable ${definition.label}`} type="checkbox" checked={Boolean(config)} onChange={(event) => update(definition.name, event.target.checked ? { action, target: action === "state-set" ? defaultState : action === "navigate" ? defaultPage : "main" } : null)} className="h-4 w-4" />{definition.label}</label>{config && <><label className="mt-1 block text-[10px] text-zinc-400">Action<select aria-label={`${definition.label} action`} value={action} onChange={(event) => update(definition.name, { ...config, action: event.target.value, target: event.target.value === "state-set" ? defaultState : event.target.value === "navigate" ? defaultPage : "main" })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{definition.actions.map((id) => { const item = actions.find((candidate) => candidate.id === id); return <option key={id} value={id}>{item?.label ?? id}</option>; })}</select></label>{action === "navigate" ? <label className="mt-1 block text-[10px] text-zinc-400">{actionDefinition?.targetLabel ?? "Page ID"}<select aria-label={`${definition.label} target`} value={String(config.target ?? "")} onChange={(event) => update(definition.name, { ...config, target: event.target.value })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{pageIds.map((pageId) => <option key={pageId} value={pageId}>{pageId}</option>)}</select></label> : action === "state-set" ? <label className="mt-1 block text-[10px] text-zinc-400">{actionDefinition?.targetLabel ?? "State key"}<select aria-label={`${definition.label} target`} value={String(config.target ?? "")} onChange={(event) => update(definition.name, { ...config, target: event.target.value })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="">Select State</option>{stateIds.map((stateId) => <option key={stateId} value={stateId}>{stateId}</option>)}</select></label> : <EventTargetInput eventLabel={definition.label} label={actionDefinition?.targetLabel ?? "Target"} value={String(config.target ?? "")} onChange={(target) => update(definition.name, { ...config, target })} />}</>}</div>;
  })}</div></fieldset>;
}

function EventTargetInput({ eventLabel, label, value, onChange }: { eventLabel: string; label: string; value: string; onChange: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <label className="mt-1 block text-[10px] text-zinc-400">{label}<input aria-label={`${eventLabel} target`} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={() => onChange(draft)} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label>;
}

function SchemaPropertyField({ field, value, onChange }: { field: ComponentPropertyDefinition; value: unknown; onChange: (value: unknown) => void }) {
  const label = `Property ${field.label}`;
  const inputClass = "mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700";
  if (field.type === "responsive-columns") return <ResponsiveColumnsField field={field} value={value} onChange={onChange} />;
  if (field.type === "table-columns" || field.type === "chart-series") return <StructuredCollectionField field={field} value={value} onChange={onChange} />;
  if (field.type === "boolean") return <label className="flex min-h-11 items-center gap-2 rounded-lg bg-zinc-50 px-2 text-[10px] dark:bg-zinc-800"><input aria-label={label} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4" />{field.label}</label>;
  if (field.type === "enum") return <label className="block text-[10px] text-zinc-400">{field.label}{field.required ? " *" : ""}<select aria-label={label} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className={inputClass}>{(field.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}</select></label>;
  return <BufferedPropertyInput field={field} value={value} label={label} className={inputClass} onChange={onChange} />;
}

function ResponsiveColumnsField({ field, value, onChange }: { field: ComponentPropertyDefinition; value: unknown; onChange: (value: unknown) => void }) {
  const columns = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const breakpoints = field.breakpoints ?? ["mobile", "tablet", "desktop"];
  return <fieldset aria-label="Property Responsive columns" className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-700"><legend className="px-1 text-[10px] text-zinc-400">{field.label}</legend><div className="grid grid-cols-3 gap-1">{breakpoints.map((breakpoint) => <label key={breakpoint} className="min-w-0 text-[9px] capitalize text-zinc-400">{breakpoint}<input aria-label={`Property ${field.label} ${breakpoint}`} type="number" min={field.minimum} max={field.maximum} value={Number(columns[breakpoint] ?? 1)} onChange={(event) => onChange({ ...columns, [breakpoint]: Number(event.target.value) })} className="mt-1 min-h-11 w-full min-w-0 rounded-lg border border-zinc-300 bg-transparent px-1 text-center text-xs dark:border-zinc-700" /></label>)}</div></fieldset>;
}

function StructuredCollectionField({ field, value, onChange }: { field: ComponentPropertyDefinition; value: unknown; onChange: (value: unknown) => void }) {
  const items = Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
  const table = field.type === "table-columns";
  const noun = table ? "column" : "series";
  const update = (index: number, key: string, nextValue: unknown) => onChange(items.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: nextValue } : item));
  const add = () => {
    const used = new Set(items.map((item) => String(item.key ?? "")));
    let index = items.length + 1;
    while (used.has(`${noun}${index}`)) index += 1;
    onChange([...items, table ? { key: `column${index}`, label: `Column ${index}`, type: "string" } : { key: `series${index}`, label: `Series ${index}`, tone: "accent" }]);
  };
  return <fieldset aria-label={`Property ${field.label}`} className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-700"><legend className="px-1 text-[10px] text-zinc-400">{field.label}</legend><div className="space-y-2">{items.map((item, index) => <div key={`${String(item.key)}-${index}`} className="rounded-lg bg-zinc-50 p-2 dark:bg-zinc-800"><BufferedPropertyInput field={{ key: "key", label: `${table ? "Column" : "Series"} ${index + 1} key`, type: "string", required: true }} value={item.key} label={`${table ? "Column" : "Series"} ${index + 1} key`} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" onChange={(next) => update(index, "key", next)} /><BufferedPropertyInput field={{ key: "label", label: `${table ? "Column" : "Series"} ${index + 1} label`, type: "string", required: true }} value={item.label} label={`${table ? "Column" : "Series"} ${index + 1} label`} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" onChange={(next) => update(index, "label", next)} /><label className="mt-1 block text-[10px] text-zinc-400">{table ? "Type" : "Tone"}<select aria-label={`${table ? "Column" : "Series"} ${index + 1} ${table ? "type" : "tone"}`} value={String(item[table ? "type" : "tone"] ?? (table ? "string" : "accent"))} onChange={(event) => update(index, table ? "type" : "tone", event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{(table ? field.columnTypes : field.tones)?.map((option) => <option key={option} value={option}>{option}</option>)}</select></label><button type="button" onClick={() => onChange(items.filter((_item, itemIndex) => itemIndex !== index))} aria-label={`Remove ${noun} ${index + 1}`} className="mt-1 min-h-11 w-full rounded-lg text-xs text-red-600">Remove</button></div>)}</div><button type="button" onClick={add} disabled={items.length >= (field.maximumItems ?? 0)} className="mt-2 min-h-11 w-full rounded-lg border border-zinc-300 text-xs disabled:opacity-30 dark:border-zinc-700">Add {noun}</button></fieldset>;
}

function BufferedPropertyInput({ field, value, label, className, onChange }: { field: ComponentPropertyDefinition; value: unknown; label: string; className: string; onChange: (value: unknown) => void }) {
  const [draft, setDraft] = useState(String(value ?? ""));
  useEffect(() => setDraft(String(value ?? "")), [value]);
  const commit = () => {
    if (field.type === "number") {
      const number = Number(draft);
      if (Number.isFinite(number)) onChange(number);
    } else onChange(draft);
  };
  const common = { "aria-label": label, value: draft, onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(event.target.value), onBlur: commit, className };
  return <label className="block text-[10px] text-zinc-400">{field.label}{field.required ? " *" : ""}{field.type === "multiline" ? <textarea {...common} rows={4} /> : <input {...common} type={field.type === "number" ? "number" : "text"} min={field.minimum} max={field.maximum} />}</label>;
}
