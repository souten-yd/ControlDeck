/** React Flow ベースのワークフローエディター（遅延ロードチャンク）。
 * モダンなグラフィック: アイコン付きノード、カテゴリ色、グラデーションエッジ、ドットグリッド。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { BottomSheet, DropdownMenu } from "../../components/ui";
import { IconDots, IconPlay, IconPlus, IconX } from "../../components/icons";
import {
  CATEGORY_ORDER,
  NODE_TYPES,
  deleteSnippet,
  loadSnippets,
  newNodeId,
  saveSnippet,
  type FieldDef,
  type Snippet,
} from "./nodeTypes";
import type { ManagedApp } from "../../types";

interface DefNode {
  id: string;
  type: string;
  name?: string;
  config?: Record<string, unknown>;
  position?: { x: number; y: number };
  rotation?: number; // 0/90/180/270
  mirror?: boolean;
}
interface DefEdge { id?: string; source: string; target: string; branch?: string | null }
interface WorkflowDetail {
  id: number;
  name: string;
  enabled: boolean;
  definition: { nodes: DefNode[]; edges: DefEdge[] };
}
type FlowNodeData = { def: DefNode; running?: string };

// ---- カスタムノード（アイコン + カテゴリ色 + 状態） ----
function FlowNode({ data, selected }: NodeProps) {
  const d = data as FlowNodeData;
  const def = d.def;
  const meta = NODE_TYPES[def.type];
  const color = meta?.color ?? "#888";
  const statusRing =
    d.running === "RUNNING"
      ? "ring-2 ring-accent-400 animate-pulse"
      : d.running === "SUCCEEDED"
        ? "ring-2 ring-emerald-400"
        : d.running === "FAILED"
          ? "ring-2 ring-red-400"
          : "";
  const transform = `${def.rotation ? `rotate(${def.rotation}deg)` : ""} ${def.mirror ? "scaleX(-1)" : ""}`.trim();
  return (
    <div
      style={transform ? { transform } : undefined}
      className={`group relative min-w-40 overflow-hidden rounded-xl border bg-white shadow-sm transition-shadow hover:shadow-md dark:bg-zinc-900 ${
        selected ? "border-transparent ring-2 ring-accent-500" : "border-zinc-200 dark:border-zinc-700"
      } ${statusRing}`}
    >
      {def.type !== "trigger" && (
        <Handle type="target" position={Position.Left} className="!h-3 !w-3 !border-2 !border-white !bg-zinc-400 dark:!border-zinc-900" />
      )}
      {/* カラーバー */}
      <div className="h-1 w-full" style={{ backgroundColor: color }} />
      {/* 内容はミラー/回転を打ち消して可読性を維持 */}
      <div
        className="flex items-center gap-2.5 px-3 py-2.5"
        style={transform ? { transform: `${def.mirror ? "scaleX(-1)" : ""} ${def.rotation ? `rotate(${-def.rotation}deg)` : ""}`.trim() } : undefined}
      >
        <span
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-sm"
          style={{ backgroundColor: `${color}1a`, color }}
        >
          {meta?.icon ?? "●"}
        </span>
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold">{def.name || meta?.label || def.type}</p>
          <p className="truncate text-[10px] text-zinc-400">{meta?.label}</p>
        </div>
      </div>
      {meta?.branches ? (
        <>
          <Handle id="true" type="source" position={Position.Right} style={{ top: "45%" }} className="!h-3 !w-3 !border-2 !border-white !bg-emerald-500 dark:!border-zinc-900" />
          <Handle id="false" type="source" position={Position.Right} style={{ top: "75%" }} className="!h-3 !w-3 !border-2 !border-white !bg-red-400 dark:!border-zinc-900" />
          <span className="pointer-events-none absolute right-1 top-[38%] text-[8px] font-medium text-emerald-500">真</span>
          <span className="pointer-events-none absolute right-1 top-[68%] text-[8px] font-medium text-red-400">偽</span>
        </>
      ) : meta?.loop ? (
        <>
          <Handle id="body" type="source" position={Position.Right} style={{ top: "45%" }} className="!h-3 !w-3 !border-2 !border-white !bg-amber-500 dark:!border-zinc-900" />
          <Handle id="done" type="source" position={Position.Bottom} className="!h-3 !w-3 !border-2 !border-white !bg-zinc-400 dark:!border-zinc-900" />
          <span className="pointer-events-none absolute right-1 top-[38%] text-[8px] font-medium text-amber-500">反復</span>
        </>
      ) : (
        <Handle type="source" position={Position.Right} className="!h-3 !w-3 !border-2 !border-white !bg-zinc-400 dark:!border-zinc-900" />
      )}
    </div>
  );
}

const nodeTypes = { cdNode: FlowNode };

function toFlow(def: WorkflowDetail["definition"]): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: (def.nodes ?? []).map((n, i) => ({
      id: n.id,
      type: "cdNode",
      position: n.position ?? { x: 80 + i * 220, y: 160 },
      data: { def: n },
    })),
    edges: (def.edges ?? []).map((e, i) => ({
      id: e.id ?? `e${i}`,
      source: e.source,
      target: e.target,
      sourceHandle: e.branch ?? undefined,
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: { strokeWidth: 2 },
    })),
  };
}

export default function WorkflowEditor({ workflowId }: { workflowId: number }) {
  const navigate = useNavigate();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [name, setName] = useState("");
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [executionsOpen, setExecutionsOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [ctxMenu, setCtxMenu] = useState<{ nodeId: string; x: number; y: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const readOnly = !can("workflows.edit");

  const { data: wf } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => api<WorkflowDetail>(`/workflows/${workflowId}`),
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!wf) return;
    const flow = toFlow(wf.definition);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    setName(wf.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wf]);

  const markDirty = useCallback(() => setDirty(true), []);

  const onConnect = useCallback(
    (conn: Connection) => {
      setEdges((eds) =>
        addEdge({ ...conn, animated: true, markerEnd: { type: MarkerType.ArrowClosed }, style: { strokeWidth: 2 } }, eds),
      );
      markDirty();
    },
    [setEdges, markDirty],
  );

  const buildDefinition = useCallback(() => {
    return {
      nodes: nodes.map((n) => ({
        ...(n.data as FlowNodeData).def,
        id: n.id,
        position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      })),
      edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, branch: e.sourceHandle ?? null })),
    };
  }, [nodes, edges]);

  const save = async () => {
    setSaving(true);
    try {
      await api(`/workflows/${workflowId}`, { method: "PATCH", json: { name, definition: buildDefinition() } });
      setDirty(false);
      show("保存しました");
      qc.invalidateQueries({ queryKey: ["workflows"] });
    } catch (e) {
      show(e instanceof Error ? e.message : "保存に失敗しました", "error");
    } finally {
      setSaving(false);
    }
  };

  const run = async () => {
    if (dirty) await save();
    try {
      await api(`/workflows/${workflowId}/run`, { method: "POST" });
      show("実行を開始しました");
      setExecutionsOpen(true);
    } catch (e) {
      show(e instanceof Error ? e.message : "実行に失敗しました", "error");
    }
  };

  const addNode = (type: string, at?: { x: number; y: number }) => {
    const id = newNodeId();
    const meta = NODE_TYPES[type];
    const def: DefNode = { id, type, name: meta.label, config: {} };
    setNodes((ns) => [
      ...ns,
      { id, type: "cdNode", position: at ?? { x: 140 + ns.length * 30, y: 100 + ns.length * 40 }, data: { def } },
    ]);
    setPaletteOpen(false);
    setSelected(id);
    markDirty();
  };

  const insertSnippet = (snippet: Snippet) => {
    const idMap = new Map<string, string>();
    const offset = { x: 60, y: 60 };
    const newNodes = snippet.nodes.map((n) => {
      const nid = newNodeId();
      idMap.set(n.id, nid);
      return {
        id: nid,
        type: "cdNode" as const,
        position: { x: (n.position?.x ?? 100) + offset.x, y: (n.position?.y ?? 100) + offset.y },
        data: { def: { ...n, id: nid } },
      };
    });
    setNodes((ns) => [...ns, ...newNodes]);
    setEdges((es) => [
      ...es,
      ...snippet.edges
        .filter((e) => idMap.has(e.source) && idMap.has(e.target))
        .map((e, i) => ({
          id: `se${Date.now()}${i}`,
          source: idMap.get(e.source)!,
          target: idMap.get(e.target)!,
          sourceHandle: e.branch ?? undefined,
          animated: true,
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { strokeWidth: 2 },
        })),
    ]);
    setPaletteOpen(false);
    markDirty();
    show(`スニペット「${snippet.name}」を挿入しました`);
  };

  const removeNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
    setSelected(null);
    markDirty();
  };

  const updateNodeDef = (id: string, patch: Partial<DefNode>) => {
    setNodes((ns) =>
      ns.map((n) => (n.id === id ? { ...n, data: { def: { ...(n.data as FlowNodeData).def, ...patch } } } : n)),
    );
    markDirty();
  };

  const duplicateNode = (id: string) => {
    const src = nodes.find((n) => n.id === id);
    if (!src) return;
    const srcDef = (src.data as FlowNodeData).def;
    if (srcDef.type === "trigger") return show("トリガーは複製できません", "error");
    const nid = newNodeId();
    setNodes((ns) => [
      ...ns,
      {
        id: nid,
        type: "cdNode",
        position: { x: src.position.x + 40, y: src.position.y + 40 },
        data: { def: { ...srcDef, id: nid, name: `${srcDef.name || NODE_TYPES[srcDef.type]?.label} のコピー` } },
      },
    ]);
    setSelected(nid);
    markDirty();
  };

  const rotateNode = (id: string) => {
    const def = (nodes.find((n) => n.id === id)?.data as FlowNodeData | undefined)?.def;
    updateNodeDef(id, { rotation: (((def?.rotation ?? 0) + 90) % 360) });
  };

  const mirrorNode = (id: string) => {
    const def = (nodes.find((n) => n.id === id)?.data as FlowNodeData | undefined)?.def;
    updateNodeDef(id, { mirror: !def?.mirror });
  };

  const selectedNodes = useMemo(() => nodes.filter((n) => n.selected), [nodes]);

  // 選択ノードをスニペットとして保存
  const saveAsSnippet = () => {
    const targets = selectedNodes.length > 0 ? selectedNodes : nodes.filter((n) => (n.data as FlowNodeData).def.type !== "trigger");
    if (targets.length === 0) return show("保存するノードがありません", "error");
    const label = prompt("スニペット名", "マイスニペット");
    if (!label) return;
    const ids = new Set(targets.map((n) => n.id));
    saveSnippet({
      id: `snip-${Date.now()}`,
      name: label,
      nodes: targets.map((n) => ({ ...(n.data as FlowNodeData).def, position: n.position })),
      edges: edges
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({ source: e.source, target: e.target, branch: e.sourceHandle ?? null })),
      createdAt: Date.now(),
    });
    show(`スニペット「${label}」を保存しました`);
  };

  // ワークフロー JSON の出力・読み込み
  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ name, definition: buildDefinition() }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name || "workflow"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importJson = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(String(reader.result));
        const def = data.definition ?? data;
        const flow = toFlow(def);
        setNodes(flow.nodes);
        setEdges(flow.edges);
        if (data.name) setName(data.name);
        markDirty();
        show("読み込みました");
      } catch {
        show("JSON の読み込みに失敗しました", "error");
      }
    };
    reader.readAsText(file);
  };

  const selectedDef = useMemo(() => {
    const node = nodes.find((n) => n.id === selected);
    return node ? (node.data as FlowNodeData).def : null;
  }, [nodes, selected]);

  return (
    <div className="flex h-full flex-col">
      {/* ツールバー */}
      <div className="flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <button onClick={() => navigate("/workflows")} aria-label="一覧へ戻る" className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
          <IconX />
        </button>
        <input
          value={name}
          onChange={(e) => { setName(e.target.value); markDirty(); }}
          disabled={readOnly}
          aria-label="ワークフロー名"
          className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 py-1.5 text-sm font-medium hover:border-zinc-200 focus:border-accent-500 focus:outline-none dark:hover:border-zinc-700"
        />
        <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={(e) => { if (e.target.files?.[0]) importJson(e.target.files[0]); e.target.value = ""; }} />
        <DropdownMenu
          ariaLabel="その他メニュー"
          trigger={<IconDots />}
          items={[
            { label: "実行履歴", onSelect: () => setExecutionsOpen(true) },
            { label: "JSON を出力", onSelect: exportJson },
            ...(readOnly ? [] : [
              { label: "JSON を読み込み", onSelect: () => fileRef.current?.click() },
              { label: "選択をスニペット保存", onSelect: saveAsSnippet },
            ]),
          ]}
        />
        {!readOnly && (
          <button onClick={save} disabled={saving || !dirty} className="rounded-xl bg-zinc-100 px-3.5 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300">
            {saving ? "保存中..." : dirty ? "保存" : "保存済み"}
          </button>
        )}
        {can("workflows.run") && (
          <button onClick={run} className="flex items-center gap-1 rounded-xl bg-accent-600 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-accent-700">
            <IconPlay /> 実行
          </button>
        )}
      </div>

      {/* キャンバス */}
      <div className="relative min-h-0 flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={(c) => { onNodesChange(c); if (c.some((ch) => ch.type === "position" || ch.type === "remove")) markDirty(); }}
          onEdgesChange={(c) => { onEdgesChange(c); if (c.some((ch) => ch.type === "remove")) markDirty(); }}
          onConnect={onConnect}
          onNodeClick={(_e, n) => setSelected(n.id)}
          onPaneClick={() => { setSelected(null); setCtxMenu(null); }}
          onNodeContextMenu={(e, n) => {
            e.preventDefault();
            if (readOnly) return;
            setSelected(n.id);
            setCtxMenu({ nodeId: n.id, x: e.clientX, y: e.clientY });
          }}
          onMoveStart={() => setCtxMenu(null)}
          nodeTypes={nodeTypes}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          fitView
          minZoom={0.2}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{ animated: true, markerEnd: { type: MarkerType.ArrowClosed }, style: { strokeWidth: 2 } }}
          className="!bg-zinc-50 dark:!bg-zinc-950"
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} className="!text-zinc-300 dark:!text-zinc-700" />
          <Controls showInteractive={false} className="!bottom-6 !rounded-lg !shadow-md" />
          <MiniMap
            pannable
            zoomable
            nodeColor={(n) => NODE_TYPES[(n.data as FlowNodeData)?.def?.type]?.color ?? "#888"}
            className="!hidden !rounded-lg md:!block"
            maskColor="rgb(0 0 0 / 0.08)"
          />
        </ReactFlow>

        {!readOnly && (
          <button onClick={() => setPaletteOpen(true)} aria-label="ノードを追加" className="absolute bottom-6 right-4 z-10 grid place-items-center rounded-2xl bg-accent-600 p-3.5 text-xl text-white shadow-lg hover:bg-accent-700">
            <IconPlus />
          </button>
        )}

        {/* 右側チャットボタン */}
        <button
          onClick={() => setChatOpen((v) => !v)}
          aria-label="チャット"
          className={`absolute right-4 top-4 z-10 flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-medium shadow-md ${
            chatOpen ? "bg-accent-600 text-white" : "bg-white text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
          }`}
        >
          💬 チャット
        </button>

        {/* 右クリックコンテキストメニュー */}
        {ctxMenu && (
          <NodeContextMenu
            x={ctxMenu.x}
            y={ctxMenu.y}
            isTrigger={(nodes.find((n) => n.id === ctxMenu.nodeId)?.data as FlowNodeData | undefined)?.def.type === "trigger"}
            onClose={() => setCtxMenu(null)}
            onAction={(act) => {
              const id = ctxMenu.nodeId;
              setCtxMenu(null);
              if (act === "delete") removeNode(id);
              else if (act === "duplicate") duplicateNode(id);
              else if (act === "rotate") rotateNode(id);
              else if (act === "mirror") mirrorNode(id);
              else if (act === "config") setSelected(id);
            }}
          />
        )}

        {chatOpen && <ChatWindow workflowId={workflowId} onClose={() => setChatOpen(false)} dirty={dirty} onSave={save} />}
      </div>

      {paletteOpen && <NodePalette onAdd={addNode} onSnippet={insertSnippet} onClose={() => setPaletteOpen(false)} />}

      {selectedDef && (
        <NodeConfigSheet
          def={selectedDef}
          readOnly={readOnly}
          onChange={(patch) => updateNodeDef(selectedDef.id, patch)}
          onDelete={selectedDef.type !== "trigger" ? () => removeNode(selectedDef.id) : undefined}
          onClose={() => setSelected(null)}
        />
      )}

      {executionsOpen && <ExecutionsSheet workflowId={workflowId} onClose={() => setExecutionsOpen(false)} />}
    </div>
  );
}

// ---- ノードパレット（カテゴリ別 + スニペット） ----
function NodePalette({
  onAdd,
  onSnippet,
  onClose,
}: {
  onAdd: (type: string) => void;
  onSnippet: (s: Snippet) => void;
  onClose: () => void;
}) {
  const [snippets, setSnippets] = useState<Snippet[]>(loadSnippets());
  const byCategory = useMemo(() => {
    const map: Record<string, [string, (typeof NODE_TYPES)[string]][]> = {};
    for (const [type, meta] of Object.entries(NODE_TYPES)) {
      if (type === "trigger") continue;
      (map[meta.category] ??= []).push([type, meta]);
    }
    return map;
  }, []);

  return (
    <BottomSheet title="ノードを追加" onClose={onClose} wide>
      {snippets.length > 0 && (
        <div className="mb-4">
          <p className="mb-1 px-1 text-xs font-medium text-accent-600 dark:text-accent-400">マイスニペット</p>
          <div className="grid grid-cols-2 gap-2">
            {snippets.map((s) => (
              <div key={s.id} className="flex items-center gap-1 rounded-xl border border-accent-200 bg-accent-50/40 dark:border-accent-800 dark:bg-accent-600/10">
                <button onClick={() => onSnippet(s)} className="min-w-0 flex-1 truncate px-3 py-2.5 text-left text-sm">
                  ⧉ {s.name}
                </button>
                <button
                  onClick={() => { deleteSnippet(s.id); setSnippets(loadSnippets()); }}
                  aria-label="削除"
                  className="px-2 text-zinc-400 hover:text-red-500"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      {CATEGORY_ORDER.filter((c) => byCategory[c]).map((category) => (
        <div key={category} className="mb-3">
          <p className="mb-1 px-1 text-xs font-medium text-zinc-400">{category}</p>
          <div className="grid grid-cols-2 gap-2">
            {byCategory[category].map(([type, meta]) => (
              <button
                key={type}
                onClick={() => onAdd(type)}
                title={meta.desc}
                className="flex items-center gap-2.5 rounded-xl border border-zinc-200 px-3 py-2.5 text-left hover:border-accent-400 hover:bg-accent-50/40 dark:border-zinc-700 dark:hover:bg-accent-600/10"
              >
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-sm" style={{ backgroundColor: `${meta.color}1a`, color: meta.color }}>
                  {meta.icon}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{meta.label}</span>
                  {meta.desc && <span className="block truncate text-[10px] text-zinc-400">{meta.desc}</span>}
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </BottomSheet>
  );
}

// ---- ノード設定フォーム ----
function NodeConfigSheet({
  def,
  readOnly,
  onChange,
  onDelete,
  onClose,
}: {
  def: DefNode;
  readOnly: boolean;
  onChange: (patch: Partial<DefNode>) => void;
  onDelete?: () => void;
  onClose: () => void;
}) {
  const meta = NODE_TYPES[def.type];
  const { data: apps } = useQuery({
    queryKey: ["apps"],
    queryFn: () => api<ManagedApp[]>("/apps"),
    enabled: meta?.fields.some((f) => f.type === "app") ?? false,
  });
  const config = def.config ?? {};
  const setConfig = (key: string, value: unknown) => onChange({ config: { ...config, [key]: value } });
  const visibleFields = (meta?.fields ?? []).filter((f) => !f.showIf || String(config[f.showIf.key] ?? "") === f.showIf.value);

  return (
    <BottomSheet title={meta?.label ?? def.type} onClose={onClose} wide>
      {meta?.desc && <p className="mb-3 rounded-lg bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-800/60">{meta.desc}</p>}
      <div className="space-y-4">
        <Field label="表示名">
          <input value={def.name ?? ""} onChange={(e) => onChange({ name: e.target.value })} disabled={readOnly} className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        </Field>
        {visibleFields.map((f) => (
          <Field key={f.key} label={f.label} hint={f.hint}>
            <ConfigInput field={f} value={config[f.key]} disabled={readOnly} apps={apps} onChange={(v) => setConfig(f.key, v)} />
          </Field>
        ))}
        <p className="text-xs text-zinc-400">
          ノード ID: <code className="font-mono">{def.id}</code>（他ノードから{" "}
          <code className="font-mono">{"{{"}{def.id}.フィールド{"}}"}</code> で参照）
        </p>
        {onDelete && !readOnly && (
          <button onClick={onDelete} className="w-full rounded-xl bg-red-50 py-2.5 text-sm font-medium text-red-600 hover:bg-red-100 dark:bg-red-950/40 dark:text-red-400">
            このノードを削除
          </button>
        )}
      </div>
    </BottomSheet>
  );
}

function ConfigInput({
  field, value, disabled, apps, onChange,
}: {
  field: FieldDef;
  value: unknown;
  disabled: boolean;
  apps?: ManagedApp[];
  onChange: (v: unknown) => void;
}) {
  const cls = "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  if (field.type === "select") {
    return (
      <select value={String(value ?? field.options?.[0]?.value ?? "")} onChange={(e) => onChange(e.target.value)} disabled={disabled} className={cls}>
        {field.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  }
  if (field.type === "app") {
    return (
      <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} disabled={disabled} className={cls}>
        <option value="">選択してください</option>
        {apps?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>
    );
  }
  if (field.type === "textarea" || field.type === "code") {
    return (
      <textarea
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={field.type === "code" ? 6 : 3}
        placeholder={field.placeholder}
        spellCheck={false}
        className={`${cls} font-mono text-xs`}
      />
    );
  }
  return (
    <input
      type={field.type === "number" ? "number" : "text"}
      value={String(value ?? "")}
      onChange={(e) => onChange(field.type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
      disabled={disabled}
      placeholder={field.placeholder}
      className={cls}
    />
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>
      {children}
      {hint && <p className="mt-1 text-xs text-zinc-400">{hint}</p>}
    </div>
  );
}

// ---- 実行履歴 ----
interface ExecutionSummary {
  id: number;
  status: string;
  trigger_type: string;
  started_at: string;
  finished_at: string | null;
  error: string;
}

function ExecutionsSheet({ workflowId, onClose }: { workflowId: number; onClose: () => void }) {
  const [detailId, setDetailId] = useState<number | null>(null);
  const { data: executions } = useQuery({
    queryKey: ["executions", workflowId],
    queryFn: () => api<ExecutionSummary[]>(`/workflow-executions?workflow_id=${workflowId}`),
    refetchInterval: 2000,
  });
  const { data: detail } = useQuery({
    queryKey: ["execution", detailId],
    queryFn: () => api<ExecutionSummary & { context: Record<string, { status: string; output?: unknown; error?: string }> }>(`/workflow-executions/${detailId}`),
    enabled: detailId !== null,
    refetchInterval: (q) => (q.state.data && ["QUEUED", "RUNNING"].includes(q.state.data.status) ? 1500 : false),
  });
  const statusCls: Record<string, string> = {
    SUCCEEDED: "text-emerald-600 dark:text-emerald-400",
    FAILED: "text-red-600 dark:text-red-400",
    RUNNING: "text-accent-600 dark:text-accent-400",
    TIMED_OUT: "text-amber-600 dark:text-amber-400",
  };
  return (
    <BottomSheet title={detailId ? `実行 #${detailId}` : "実行履歴"} onClose={detailId ? () => setDetailId(null) : onClose} wide>
      {detailId === null ? (
        !executions || executions.length === 0 ? (
          <p className="py-6 text-center text-sm text-zinc-400">実行履歴はありません</p>
        ) : (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {executions.map((ex) => (
              <li key={ex.id}>
                <button onClick={() => setDetailId(ex.id)} className="flex w-full items-center gap-3 py-2.5 text-left text-sm">
                  <span className={`w-16 shrink-0 text-xs font-medium ${statusCls[ex.status] ?? "text-zinc-400"}`}>{ex.status}</span>
                  <span className="num min-w-0 flex-1 truncate text-xs text-zinc-400">
                    {new Date(ex.started_at + (ex.started_at.endsWith("Z") ? "" : "Z")).toLocaleString("ja-JP")} · {ex.trigger_type === "manual" ? "手動" : "スケジュール"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : detail ? (
        <div className="space-y-3">
          <p className={`text-sm font-medium ${statusCls[detail.status] ?? ""}`}>{detail.status}</p>
          {detail.error && <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600 dark:bg-red-950/40 dark:text-red-400">{detail.error}</p>}
          {Object.entries(detail.context).map(([nodeId, r]) => (
            <div key={nodeId} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="mb-1 flex items-center justify-between text-xs font-medium">
                <code className="font-mono">{nodeId}</code>
                <span className={statusCls[r.status] ?? "text-zinc-400"}>{r.status}</span>
              </p>
              {r.error && <p className="text-xs text-red-500">{r.error}</p>}
              {r.output !== undefined && (
                <pre className="mt-1 max-h-32 overflow-auto rounded bg-zinc-50 p-2 font-mono text-[11px] dark:bg-zinc-950">{JSON.stringify(r.output, null, 1)}</pre>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="py-6 text-center text-sm text-zinc-400">読み込み中...</p>
      )}
    </BottomSheet>
  );
}

// ---- ノード右クリックメニュー ----
function NodeContextMenu({
  x, y, isTrigger, onClose, onAction,
}: {
  x: number;
  y: number;
  isTrigger: boolean;
  onClose: () => void;
  onAction: (action: "delete" | "duplicate" | "rotate" | "mirror" | "config") => void;
}) {
  useEffect(() => {
    const close = () => onClose();
    document.addEventListener("click", close);
    document.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("scroll", close, true);
    };
  }, [onClose]);
  const items: { label: string; action: "delete" | "duplicate" | "rotate" | "mirror" | "config"; danger?: boolean; icon: string }[] = [
    { label: "設定を編集", action: "config", icon: "⚙" },
    ...(isTrigger ? [] : [{ label: "複製", action: "duplicate" as const, icon: "⧉" }]),
    { label: "回転", action: "rotate", icon: "↻" },
    { label: "左右ミラー", action: "mirror", icon: "⇋" },
    ...(isTrigger ? [] : [{ label: "削除", action: "delete" as const, danger: true, icon: "🗑" }]),
  ];
  return createPortal(
    <div
      style={{ left: x, top: y }}
      onClick={(e) => e.stopPropagation()}
      className="fixed z-[60] w-40 overflow-hidden rounded-xl border border-zinc-200 bg-white py-1 shadow-xl dark:border-zinc-700 dark:bg-zinc-800"
    >
      {items.map((it) => (
        <button
          key={it.action}
          onClick={() => onAction(it.action)}
          className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-700 ${
            it.danger ? "text-red-600 dark:text-red-400" : ""
          }`}
        >
          <span className="w-4 text-center">{it.icon}</span>
          {it.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}

// ---- フローティングチャットウィンドウ（チャットフロー） ----
interface ChatMsg { role: "user" | "assistant"; text: string }

function ChatWindow({
  workflowId, onClose, dirty, onSave,
}: {
  workflowId: number;
  onClose: () => void;
  dirty: boolean;
  onSave: () => Promise<void>;
}) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const show = useToasts((s) => s.show);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const send = async () => {
    const msg = input.trim();
    if (!msg || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setBusy(true);
    try {
      if (dirty) await onSave();
      const { execution_id } = await api<{ execution_id: number }>(`/workflows/${workflowId}/run`, {
        method: "POST",
        json: { input: { message: msg } },
      });
      // 実行完了までポーリングし、signal.display ノードの出力を返答として表示
      let reply = "";
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 800));
        const ex = await api<{ status: string; context: Record<string, { status: string; output?: { display?: boolean; value?: string; signal?: string } }> }>(
          `/workflow-executions/${execution_id}`,
        );
        if (!["QUEUED", "RUNNING"].includes(ex.status)) {
          const signals = Object.values(ex.context)
            .filter((c) => c.output && c.output.display)
            .map((c) => c.output!.value ?? "");
          reply = signals.join("\n\n") || (ex.status === "SUCCEEDED" ? "(信号表示ノードがありません)" : `実行 ${ex.status}`);
          break;
        }
      }
      setMessages((m) => [...m, { role: "assistant", text: reply || "(応答なし)" }]);
    } catch (e) {
      show(e instanceof Error ? e.message : "実行に失敗しました", "error");
      setMessages((m) => [...m, { role: "assistant", text: "エラーが発生しました" }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="absolute bottom-4 right-4 top-16 z-20 flex w-[min(380px,calc(100%-2rem))] flex-col rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-800">
        <span className="text-sm font-semibold">チャットフロー</span>
        <button onClick={onClose} aria-label="閉じる" className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"><IconX /></button>
      </div>
      <div ref={listRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {messages.length === 0 && (
          <p className="mt-6 text-center text-xs text-zinc-400">
            メッセージを送るとワークフローが実行されます。<br />
            トリガーの出力 <code className="font-mono">{"{{trigger.message}}"}</code> で入力を参照し、<br />
            「信号表示」ノードの値がここに返答として表示されます。
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm ${
              m.role === "user" ? "bg-accent-600 text-white" : "bg-zinc-100 dark:bg-zinc-800"
            }`}>
              {m.text}
            </div>
          </div>
        ))}
        {busy && <div className="text-center text-xs text-zinc-400">実行中...</div>}
      </div>
      <div className="flex gap-2 border-t border-zinc-200 p-2.5 dark:border-zinc-800">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), send())}
          placeholder="メッセージを入力..."
          className="min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950"
        />
        <button onClick={send} disabled={busy || !input.trim()} className="rounded-xl bg-accent-600 px-3.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          送信
        </button>
      </div>
    </div>
  );
}
