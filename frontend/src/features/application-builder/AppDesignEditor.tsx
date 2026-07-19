import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ApplicationProject, ComponentDefinition, SemanticComponent } from "../../api/applicationBuilder";
import { applicationBuilderApi } from "../../api/applicationBuilder";
import { useToasts } from "../../stores";
import { findComponent, pagesOf, parentOf, removeComponent, uniqueComponentId, updateComponent, type AppPage } from "./editorModel";

type Viewport = "mobile" | "tablet" | "desktop";

export function AppDesignEditor({ project, catalog }: { project: ApplicationProject; catalog: ComponentDefinition[] }) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [spec, setSpec] = useState<Record<string, unknown>>(() => structuredClone(project.spec));
  const [past, setPast] = useState<Record<string, unknown>[]>([]);
  const [future, setFuture] = useState<Record<string, unknown>[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [viewport, setViewport] = useState<Viewport>("desktop");
  const pages = pagesOf(spec);
  const page = pages[0];
  const root = page?.root ?? null;
  const selected = selectedId ? findComponent(root, selectedId) : null;
  const definitions = useMemo(() => new Map(catalog.map((item) => [item.type, item])), [catalog]);
  const dirty = JSON.stringify(spec) !== JSON.stringify(project.spec);

  useEffect(() => {
    setSpec(structuredClone(project.spec)); setPast([]); setFuture([]); setSelectedId(null);
  }, [project.id, project.updated_at, project.spec]);

  const commit = (next: Record<string, unknown>) => {
    setPast((items) => [...items.slice(-49), spec]); setFuture([]); setSpec(next);
  };
  const withPage = (nextPage: AppPage) => commit({ ...spec, pages: [nextPage, ...pages.slice(1)] });
  const save = useMutation({
    mutationFn: () => applicationBuilderApi.update(project.id, { spec }),
    onSuccess: async () => { show("App designを保存しました"); await qc.invalidateQueries({ queryKey: ["application-project", project.id] }); },
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
  const patchSelected = (patch: Partial<SemanticComponent>) => {
    if (!selected || !root || !page) return;
    withPage({ ...page, root: updateComponent(root, selected.id, (component) => ({ ...component, ...patch })) });
  };
  const removeSelected = () => {
    if (!selected || !root || !page || selected.id === root.id) return;
    withPage({ ...page, root: removeComponent(root, selected.id) }); setSelectedId(null);
  };
  const move = (offset: -1 | 1) => {
    if (!selected || !root || !page) return;
    const parent = parentOf(root, selected.id); if (!parent) return;
    const children = [...(parent.children ?? [])]; const index = children.findIndex((item) => item.id === selected.id); const target = index + offset;
    if (target < 0 || target >= children.length) return;
    [children[index], children[target]] = [children[target], children[index]];
    withPage({ ...page, root: updateComponent(root, parent.id, (item) => ({ ...item, children })) });
  };
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

  return <section aria-label="App Design Editor" className="overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
    <div className="flex min-h-14 flex-wrap items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
      <strong className="mr-auto text-sm">Design</strong>
      <button onClick={undo} disabled={!past.length} className="min-h-10 rounded-lg px-3 text-xs disabled:opacity-30">Undo</button>
      <button onClick={redo} disabled={!future.length} className="min-h-10 rounded-lg px-3 text-xs disabled:opacity-30">Redo</button>
      {(["mobile", "tablet", "desktop"] as Viewport[]).map((item) => <button key={item} onClick={() => setViewport(item)} aria-pressed={viewport === item} className={`min-h-10 rounded-lg px-2 text-[11px] capitalize ${viewport === item ? "bg-zinc-900 text-white dark:bg-white dark:text-zinc-900" : "bg-zinc-100 dark:bg-zinc-800"}`}>{item}</button>)}
      <button onClick={() => save.mutate()} disabled={!dirty || save.isPending} className="min-h-10 rounded-lg bg-accent-600 px-4 text-xs font-semibold text-white disabled:opacity-40">{save.isPending ? "Saving…" : "Save"}</button>
    </div>
    {!page || !root ? <div className="grid min-h-72 place-items-center p-6 text-center"><div><p className="text-sm font-medium">{page ? "Page canvasは未初期化です" : "ページはまだありません"}</p><p className="mt-1 text-xs text-zinc-400">{page ? "既存Pageを維持してresponsive Stackを追加します。" : "最初のPageとresponsive Stackを作成します。"}</p><button onClick={initializePage} className="mt-4 min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">{page ? "Initialize Canvas" : "Add Page"}</button></div></div> :
    <div className="grid min-h-[520px] lg:grid-cols-[220px_minmax(0,1fr)_260px]">
      <aside className="border-b border-zinc-200 p-3 dark:border-zinc-800 lg:border-b-0 lg:border-r"><h3 className="mb-2 text-xs font-semibold text-zinc-500">Components</h3><div className="grid grid-cols-2 gap-1 lg:grid-cols-1">{catalog.map((item) => <button key={item.type} onClick={() => addComponent(item)} className="min-h-11 rounded-lg border border-zinc-200 px-2 text-left text-xs hover:border-accent-400 dark:border-zinc-700"><span className="block truncate font-medium">{item.label}</span><span className="text-[9px] text-zinc-400">{item.category}</span></button>)}</div><h3 className="mb-1 mt-4 text-xs font-semibold text-zinc-500">Component Tree</h3><p className="mb-2 text-[9px] leading-relaxed text-zinc-400">Desktopではcontainerへdrag、touch／keyboardではInspectorのMoveを使用できます。</p>{root && <TreeNode item={root} selectedId={selectedId} onSelect={setSelectedId} onReparent={reparent} definitions={definitions} depth={0} />}</aside>
      <div className="min-w-0 overflow-auto bg-zinc-100 p-3 dark:bg-zinc-950"><div data-testid="app-responsive-preview" className={`mx-auto min-h-96 overflow-hidden rounded-xl border border-zinc-300 bg-white shadow-sm transition-[max-width] dark:border-zinc-700 dark:bg-zinc-900 ${viewport === "mobile" ? "max-w-[320px]" : viewport === "tablet" ? "max-w-[768px]" : "max-w-[1100px]"}`}><div className="border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">{page.title || page.id}</div><div className="p-4">{root && <PreviewNode item={root} definitions={definitions} selectedId={selectedId} onSelect={setSelectedId} />}</div></div></div>
      <aside className="border-t border-zinc-200 p-3 dark:border-zinc-800 lg:border-l lg:border-t-0"><h3 className="mb-3 text-xs font-semibold text-zinc-500">Inspector</h3>{selected ? <Inspector item={selected} definition={definitions.get(selected.type)} onPatch={patchSelected} onMove={move} onRemove={removeSelected} root={selected.id === root?.id} /> : <p className="text-xs text-zinc-400">部品を選択してください。</p>}</aside>
    </div>}
  </section>;
}

function TreeNode({ item, selectedId, onSelect, onReparent, definitions, depth }: { item: SemanticComponent; selectedId: string | null; onSelect: (id: string) => void; onReparent: (source: string, target: string) => void; definitions: Map<string, ComponentDefinition>; depth: number }) {
  const container = definitions.get(item.type)?.container;
  return <div><button draggable={depth > 0} onDragStart={(event) => { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/control-deck-component", item.id); }} onDragOver={(event) => { if (container) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; } }} onDrop={(event) => { if (!container) return; event.preventDefault(); const source = event.dataTransfer.getData("text/control-deck-component"); if (source) onReparent(source, item.id); }} onClick={() => onSelect(item.id)} className={`mb-1 flex min-h-10 w-full min-w-0 items-center rounded-lg px-2 text-left text-xs ${selectedId === item.id ? "bg-accent-50 text-accent-700 dark:bg-accent-600/10" : container ? "hover:bg-accent-50/60 dark:hover:bg-accent-600/10" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`} style={{ paddingLeft: `${8 + depth * 12}px` }}><span className="truncate">{item.id}</span><code className="ml-auto pl-1 text-[8px] text-zinc-400">{item.type}</code></button>{(item.children ?? []).map((child) => <TreeNode key={child.id} item={child} selectedId={selectedId} onSelect={onSelect} onReparent={onReparent} definitions={definitions} depth={depth + 1} />)}</div>;
}

function PreviewNode({ item, definitions, selectedId, onSelect }: { item: SemanticComponent; definitions: Map<string, ComponentDefinition>; selectedId: string | null; onSelect: (id: string) => void }) {
  const selected = item.id === selectedId; const props = item.properties ?? {}; const children = item.children ?? [];
  const shell = `relative rounded-lg ${selected ? "ring-2 ring-accent-500" : ""}`;
  if (definitions.get(item.type)?.container) return <div onClick={(event) => { event.stopPropagation(); onSelect(item.id); }} className={`${shell} ${item.type === "layout.row" ? "flex flex-wrap gap-3" : item.type === "layout.grid" ? "grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" : item.type === "layout.card" ? "border border-zinc-200 p-3 dark:border-zinc-700" : "space-y-3"}`}>{children.length ? children.map((child) => <PreviewNode key={child.id} item={child} definitions={definitions} selectedId={selectedId} onSelect={onSelect} />) : <div className="rounded-lg border border-dashed border-zinc-300 p-4 text-center text-[10px] text-zinc-400 dark:border-zinc-700">Drop components here</div>}</div>;
  const content = item.type === "input.text" ? <label className="block text-xs"><span>{String(props.label ?? "Input")}</span><input disabled className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-3" /></label> : item.type === "action.workflow-run" ? <button className="min-h-11 rounded-lg bg-accent-600 px-4 text-xs font-semibold text-white">{String(props.label ?? "Run")}</button> : item.type === "display.metric" ? <div className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800"><span className="text-xs text-zinc-400">{String(props.label ?? "Metric")}</span><strong className="block text-2xl">{String(props.value ?? 0)}</strong></div> : item.type === "data.table" ? <div className="rounded-lg border border-zinc-200 p-4 text-xs text-zinc-400">Data Table</div> : item.type === "chart.line" ? <div className="grid h-28 place-items-center rounded-lg bg-zinc-50 text-xs text-zinc-400 dark:bg-zinc-800">Line Chart</div> : <p className="text-sm">{String(props.text ?? props.value ?? item.type)}</p>;
  return <div onClick={(event) => { event.stopPropagation(); onSelect(item.id); }} className={`${shell} p-1`}>{content}</div>;
}

function Inspector({ item, definition, onPatch, onMove, onRemove, root }: { item: SemanticComponent; definition?: ComponentDefinition; onPatch: (patch: Partial<SemanticComponent>) => void; onMove: (offset: -1 | 1) => void; onRemove: () => void; root: boolean }) {
  const [json, setJson] = useState(JSON.stringify(item.properties ?? {}, null, 2)); const [error, setError] = useState("");
  useEffect(() => { setJson(JSON.stringify(item.properties ?? {}, null, 2)); setError(""); }, [item.id, item.properties]);
  const applyJson = () => { try { const value = JSON.parse(json); if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(); onPatch({ properties: value }); setError(""); } catch { setError("JSON objectを入力してください"); } };
  return <div className="space-y-3"><div><span className="text-[10px] text-zinc-400">ID</span><code className="block break-all text-xs">{item.id}</code></div><div><span className="text-[10px] text-zinc-400">Type</span><p className="text-xs font-medium">{definition?.label ?? item.type}</p></div><label className="block text-[10px] text-zinc-400">Binding<input value={typeof item.binding === "string" ? item.binding : ""} onChange={(event) => onPatch({ binding: event.target.value || null })} placeholder="workflow-output:answer" className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label><label className="block text-[10px] text-zinc-400">Properties JSON<textarea value={json} onChange={(event) => setJson(event.target.value)} onBlur={applyJson} rows={7} className="mt-1 w-full rounded-lg border border-zinc-300 bg-transparent p-2 font-mono text-[11px] dark:border-zinc-700" /></label>{error && <p role="alert" className="text-[10px] text-red-500">{error}</p>}<div className="grid grid-cols-2 gap-2"><button onClick={() => onMove(-1)} disabled={root} className="min-h-11 rounded-lg border border-zinc-300 text-xs disabled:opacity-30 dark:border-zinc-700">Move up</button><button onClick={() => onMove(1)} disabled={root} className="min-h-11 rounded-lg border border-zinc-300 text-xs disabled:opacity-30 dark:border-zinc-700">Move down</button></div><button onClick={onRemove} disabled={root} className="min-h-11 w-full rounded-lg text-xs text-red-600 disabled:opacity-30">Remove component</button></div>;
}
