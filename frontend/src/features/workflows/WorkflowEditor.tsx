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
  reconnectEdge,
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
import { IconDots, IconPlay, IconPlus, IconTrash, IconX } from "../../components/icons";
import {
  CATEGORY_ORDER,
  JSON_SCHEMA_PRESETS,
  NODE_TYPES,
  deleteSnippet,
  loadSnippets,
  newNodeId,
  saveSnippet,
  type FieldDef,
  type Snippet,
  type TriggerInputDef,
  type ExtractorDef,
} from "./nodeTypes";
import { ScrapeViewer } from "./ScrapeViewer";
import { InfoPanel } from "./InfoPanel";
import { PreviewWorkspace } from "./PreviewWorkspace";
import { FilePicker } from "../../components/FilePicker";
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
  state: "draft" | "published";
  published_version: number | null;
  published_version_id: number | null;
  definition: { nodes: DefNode[]; edges: DefEdge[] };
}
type FlowNodeData = { def: DefNode; running?: string; pinned?: boolean };
interface PinnedData {
  id: number;
  node_id: string;
  output: unknown;
  source_execution_id: number | null;
  updated_at: string;
}
interface NodeMetadata {
  type: string;
  version: number;
  description: string;
  side_effect: "none" | "read" | "write" | "external" | "process";
  capabilities: string[];
  config_schema: Record<string, { type: string; required?: boolean; default?: unknown; recommended?: unknown; reason?: string }>;
  initial_config: Record<string, unknown>;
  input_schema: Record<string, string>;
  output_schema: Record<string, string>;
  ui_hints: {
    help?: string;
    quick_start?: string;
    variable_picker?: boolean;
    show_recommended_defaults?: boolean;
    primary_input?: string | null;
    primary_output?: string | null;
    examples?: Array<{ title: string; config: Record<string, unknown> }>;
  };
  supports: { retry: boolean; cancel: boolean; progress: boolean; dry_run: boolean };
}
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
      className={`group relative min-w-40 rounded-xl border bg-white shadow-sm transition-shadow hover:shadow-md dark:bg-zinc-900 ${
        selected ? "border-transparent ring-2 ring-accent-500" : "border-zinc-200 dark:border-zinc-700"
      } ${statusRing}`}
    >
      {def.type !== "trigger" && (
        <Handle type="target" position={Position.Left} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-zinc-400 dark:!border-zinc-900" />
      )}
      {/* カラーバー */}
      <div className="h-1 w-full rounded-t-[11px]" style={{ backgroundColor: color }} />
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
      {/* 承認ゲート/リトライのバッジ */}
      {(def.type === "human.approval" || def.config?.require_approval || Number(def.config?.retry_count) > 0) && (
        <span className="pointer-events-none absolute left-1 top-1.5 text-[9px]">
          {def.type === "human.approval" || def.config?.require_approval ? "✋" : ""}{Number(def.config?.retry_count) > 0 ? "↻" : ""}
        </span>
      )}
      {d.pinned && (
        <span className="pointer-events-none absolute right-2 top-2 rounded-full bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 shadow-sm dark:bg-amber-950/70 dark:text-amber-300" aria-label="固定データを使用中">
          📌 固定
        </span>
      )}
      {/* エラー分岐ハンドル（on_error=branch のとき） */}
      {def.config?.on_error === "branch" && def.type !== "trigger" && (
        <>
          <Handle id="error" type="source" position={Position.Bottom} style={{ left: "35%" }} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-red-500 dark:!border-zinc-900" />
          <Handle id="timeout" type="source" position={Position.Bottom} style={{ left: "65%" }} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-amber-500 dark:!border-zinc-900" />
          <span className="pointer-events-none absolute bottom-0.5 left-[35%] -translate-x-1/2 text-[8px] font-medium text-red-500">失敗</span>
          <span className="pointer-events-none absolute bottom-0.5 left-[65%] -translate-x-1/2 text-[8px] font-medium text-amber-500">時間切れ</span>
        </>
      )}
      {meta?.branches ? (
        <>
          <Handle id="true" type="source" position={Position.Right} style={{ top: "45%" }} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-emerald-500 dark:!border-zinc-900" />
          <Handle id="false" type="source" position={Position.Right} style={{ top: "75%" }} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-red-400 dark:!border-zinc-900" />
          <span className="pointer-events-none absolute right-1 top-[38%] text-[8px] font-medium text-emerald-500">真</span>
          <span className="pointer-events-none absolute right-1 top-[68%] text-[8px] font-medium text-red-400">偽</span>
        </>
      ) : meta?.loop ? (
        <>
          <Handle id="body" type="source" position={Position.Right} style={{ top: "45%" }} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-amber-500 dark:!border-zinc-900" />
          <Handle id="done" type="source" position={Position.Bottom} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-zinc-400 dark:!border-zinc-900" />
          <span className="pointer-events-none absolute right-1 top-[38%] text-[8px] font-medium text-amber-500">反復</span>
        </>
      ) : (
        <Handle type="source" position={Position.Right} className="workflow-node-handle !h-3 !w-3 !border-2 !border-white !bg-zinc-400 dark:!border-zinc-900" />
      )}
    </div>
  );
}

const nodeTypes = { cdNode: FlowNode };

function edgeStyle(branch?: string | null): React.CSSProperties {
  if (branch === "error") return { strokeWidth: 2, stroke: "#ef4444", strokeDasharray: "6 4" };
  if (branch === "timeout") return { strokeWidth: 2, stroke: "#f59e0b", strokeDasharray: "3 4" };
  return { strokeWidth: 2 };
}

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
      style: edgeStyle(e.branch),
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
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [executionsOpen, setExecutionsOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [ctxMenu, setCtxMenu] = useState<{ nodeId: string; x: number; y: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const readOnly = !can("workflows.edit");

  const { data: wf } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => api<WorkflowDetail>(`/workflows/${workflowId}`),
    staleTime: Infinity,
  });
  const { data: nodeCatalog } = useQuery({
    queryKey: ["workflow-node-catalog"],
    queryFn: () => api<NodeMetadata[]>("/workflows/node-catalog"),
    staleTime: Infinity,
  });
  const { data: pinnedData } = useQuery({
    queryKey: ["workflow-pinned-data", workflowId],
    queryFn: () => api<PinnedData[]>(`/workflows/${workflowId}/pinned-data`),
    enabled: can("workflows.run"),
  });

  useEffect(() => {
    if (!wf) return;
    const flow = toFlow(wf.definition);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    setName(wf.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wf]);

  useEffect(() => {
    const pinnedIds = new Set((pinnedData ?? []).map((item) => item.node_id));
    setNodes((current) => current.map((node) => {
      const data = node.data as FlowNodeData;
      const pinned = pinnedIds.has(node.id);
      return data.pinned === pinned ? node : { ...node, data: { ...data, pinned } };
    }));
  }, [pinnedData, setNodes]);

  const markDirty = useCallback(() => setDirty(true), []);

  const onConnect = useCallback(
    (conn: Connection) => {
      setEdges((eds) =>
        addEdge({ ...conn, animated: true, markerEnd: { type: MarkerType.ArrowClosed }, style: edgeStyle(conn.sourceHandle) }, eds),
      );
      if (conn.source && conn.target) {
        setNodes((current) => {
          const source = current.find((node) => node.id === conn.source);
          const target = current.find((node) => node.id === conn.target);
          if (!source || !target) return current;
          const sourceDef = (source.data as FlowNodeData).def;
          const targetDef = (target.data as FlowNodeData).def;
          const targetMeta = nodeCatalog?.find((item) => item.type === targetDef.type);
          const inputKey = targetMeta?.ui_hints.primary_input;
          if (!inputKey || targetDef.config?.[inputKey] !== undefined && targetDef.config?.[inputKey] !== "") return current;
          const triggerInput = sourceDef.type === "trigger"
            ? ((sourceDef.config?.inputs as TriggerInputDef[] | undefined) ?? [])[0]?.key
            : undefined;
          const sourceMeta = nodeCatalog?.find((item) => item.type === sourceDef.type);
          const outputKey = triggerInput ?? sourceMeta?.ui_hints.primary_output ?? Object.keys(sourceMeta?.output_schema ?? {})[0];
          if (!outputKey) return current;
          return current.map((node) => node.id !== conn.target ? node : {
            ...node,
            data: {
              ...(node.data as FlowNodeData),
              def: { ...targetDef, config: { ...targetDef.config, [inputKey]: `{{${sourceDef.id}.${outputKey}}}` } },
            },
          });
        });
      }
      markDirty();
    },
    [setEdges, setNodes, markDirty, nodeCatalog],
  );

  const onReconnect = useCallback(
    (oldEdge: Edge, connection: Connection) => {
      setEdges((current) => reconnectEdge(oldEdge, connection, current));
      setSelectedEdge(oldEdge.id);
      markDirty();
    },
    [markDirty, setEdges],
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
      return true;
    } catch (e) {
      show(e instanceof Error ? e.message : "保存に失敗しました", "error");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    if (dirty && !await save()) return;
    setPublishing(true);
    try {
      const result = await api<{ version: number; warnings: string[] }>(`/workflows/${workflowId}/publish`, { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
      show(`バージョン ${result.version} を公開しました${result.warnings.length ? `（警告 ${result.warnings.length}件）` : ""}`);
    } catch (error) {
      show(error instanceof Error ? error.message : "公開検証に失敗しました", "error");
    } finally {
      setPublishing(false);
    }
  };

  const [runInputsOpen, setRunInputsOpen] = useState(false);
  const [startingRun, setStartingRun] = useState(false);

  const doRun = async (input?: Record<string, unknown>) => {
    setStartingRun(true);
    try {
      const result = await api<{ execution_id: number; version?: number; published?: boolean }>(
        readOnly ? `/workflows/${workflowId}/run` : `/workflows/${workflowId}/validate-publish-run`,
        { method: "POST", json: input ? { input } : {} },
      );
      if (!readOnly) await qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
      show(result.published
        ? `最新の下書きを v${result.version} として公開し、実行を開始しました`
        : readOnly ? "公開版の実行を開始しました" : `公開中の v${result.version} を実行しました`);
      setInfoOpen(true); // 情報パネルでライブ状況を表示
      setPreviewOpen(false);
    } catch (e) {
      show(e instanceof Error ? e.message : "実行に失敗しました", "error");
    } finally {
      setStartingRun(false);
    }
  };

  // 情報パネルからのライブ状態をキャンバスのノードに反映（点灯）
  const applyStatuses = useCallback(
    (statuses: Record<string, string>) => {
      setNodes((ns) =>
        ns.map((n) => {
          const s = statuses[n.id];
          const mapped = s === "RETRYING" ? "RUNNING" : s === "TIMED_OUT" ? "FAILED" : s;
          if ((n.data as FlowNodeData).running === mapped) return n;
          return { ...n, data: { ...(n.data as FlowNodeData), running: mapped } };
        }),
      );
    },
    [setNodes],
  );

  const run = async () => {
    if (dirty && !await save()) return;
    const trigger = nodes.map((n) => (n.data as FlowNodeData).def).find((d) => d.type === "trigger");
    const inputs = (trigger?.config?.inputs as TriggerInputDef[] | undefined) ?? [];
    if (inputs.length > 0) {
      setRunInputsOpen(true); // 入力フィールドが定義されていれば値を聞いてから実行
      return;
    }
    await doRun();
  };

  const addNode = (type: string, at?: { x: number; y: number }) => {
    const id = newNodeId();
    const meta = NODE_TYPES[type];
    const initialConfig = nodeCatalog?.find((item) => item.type === type)?.initial_config ?? {};
    const def: DefNode = { id, type, name: meta.label, config: structuredClone(initialConfig) };
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
          style: edgeStyle(e.branch),
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

  const removeEdge = (id: string) => {
    setEdges((current) => current.filter((edge) => edge.id !== id));
    setSelectedEdge(null);
    markDirty();
  };

  const updateNodeDef = (id: string, patch: Partial<DefNode>) => {
    setNodes((ns) =>
      ns.map((n) => (n.id === id ? { ...n, data: { ...(n.data as FlowNodeData), def: { ...(n.data as FlowNodeData).def, ...patch } } } : n)),
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
        <span className={`hidden shrink-0 rounded-full px-2 py-1 text-[9px] font-semibold sm:inline ${wf?.state === "published" && !dirty ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"}`}>
          {wf?.state === "published" && !dirty ? `公開 v${wf.published_version}` : "編集中"}
        </span>
        <DropdownMenu
          ariaLabel="その他メニュー"
          trigger={<IconDots />}
          items={[
            { label: "実行履歴", onSelect: () => setExecutionsOpen(true) },
            ...(can("workflows.run") ? [{ label: "確認・テスト", onSelect: () => { setPreviewOpen(true); setInfoOpen(false); } }] : []),
            { label: "JSON を出力", onSelect: exportJson },
            ...(readOnly ? [] : [
              { label: "アプリ化", onSelect: () => navigate(`/workflows/${workflowId}/app`) },
              { label: "実行せず公開", onSelect: () => void publish() },
              { label: "JSON を読み込み", onSelect: () => fileRef.current?.click() },
              { label: "選択をスニペット保存", onSelect: saveAsSnippet },
            ]),
          ]}
        />
        {!readOnly && (
          <button onClick={save} disabled={saving || !dirty} className="hidden rounded-xl bg-zinc-100 px-3.5 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300 sm:block">
            {saving ? "保存中..." : dirty ? "保存" : "保存済み"}
          </button>
        )}
        {!readOnly && <span className={`shrink-0 text-[9px] sm:hidden ${dirty ? "text-amber-600" : "text-zinc-400"}`}>{saving ? "保存中" : dirty ? "未保存" : "保存済"}</span>}
        {can("workflows.run") && (
          <button
            onClick={() => { setPreviewOpen(true); setInfoOpen(false); }}
            aria-label="確認・テストを開く"
            className="min-h-9 rounded-xl border border-accent-300 px-3 text-sm font-medium text-accent-700 hover:bg-accent-50 dark:border-accent-700 dark:text-accent-300 dark:hover:bg-accent-950/30"
          >
            確認・テスト
          </button>
        )}
        {can("workflows.run") && (
          <button onClick={run} disabled={startingRun || publishing} className="flex items-center gap-1 rounded-xl bg-accent-600 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50">
            <IconPlay /> {startingRun ? "確認中…" : readOnly ? "公開版を実行" : "検証して実行"}
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
          onReconnect={onReconnect}
          onNodeClick={(_e, n) => { setSelectedEdge(null); setSelected(n.id); }}
          onEdgeClick={(_e, edge) => { setSelected(null); setSelectedEdge(edge.id); }}
          onPaneClick={() => { setSelected(null); setSelectedEdge(null); setCtxMenu(null); }}
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
          edgesReconnectable={!readOnly}
          edgesFocusable
          connectionRadius={32}
          reconnectRadius={36}
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

        {selectedEdge && !readOnly && edges.some((edge) => edge.id === selectedEdge) && (
          <div
            role="toolbar"
            aria-label="接続線の操作"
            className="absolute bottom-20 left-1/2 z-20 flex max-w-[calc(100%-2rem)] -translate-x-1/2 items-center gap-2 rounded-2xl border border-zinc-200 bg-white/95 p-1.5 pl-3 shadow-xl backdrop-blur dark:border-zinc-700 dark:bg-zinc-900/95"
          >
            <span className="truncate text-xs text-zinc-500">端の丸をドラッグして付け替え</span>
            <button
              type="button"
              onClick={() => removeEdge(selectedEdge)}
              aria-label="選択した接続線を削除"
              className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
            >
              <IconTrash />
            </button>
          </div>
        )}

        {/* 実行デバッグパネル */}
        <div className="absolute right-4 top-4 z-10 flex gap-2">
          <button
            onClick={() => { setInfoOpen((v) => !v); if (!infoOpen) setPreviewOpen(false); }}
            aria-label="実行情報"
            title="実行状況・処理内容・経過時間・強制停止・履歴・バージョン"
            className={`flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-medium shadow-md ${
              infoOpen ? "bg-accent-600 text-white" : "bg-white text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
            }`}
          >
            実行・デバッグ
          </button>
        </div>

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

        {previewOpen && (
          <PreviewWorkspace
            workflowId={workflowId}
            definition={buildDefinition()}
            inputs={((nodes.map((n) => (n.data as FlowNodeData).def).find((d) => d.type === "trigger")?.config?.inputs as TriggerInputDef[] | undefined) ?? [])}
            dirty={dirty}
            onSave={save}
            onExecution={() => { setInfoOpen(false); }}
            onClose={() => setPreviewOpen(false)}
          />
        )}
        {infoOpen && (
          <InfoPanel
            workflowId={workflowId}
            nodeNames={Object.fromEntries(
              nodes.map((n) => {
                const d = (n.data as FlowNodeData).def;
                return [n.id, { name: d.name || NODE_TYPES[d.type]?.label || n.id, type: d.type }];
              }),
            )}
            onStatuses={applyStatuses}
            onClose={() => setInfoOpen(false)}
          />
        )}
      </div>

      {paletteOpen && <NodePalette onAdd={addNode} onSnippet={insertSnippet} onClose={() => setPaletteOpen(false)} />}

      {selectedDef && (
        <NodeConfigSheet
          workflowId={workflowId}
          def={selectedDef}
          allDefs={nodes.map((n) => (n.data as FlowNodeData).def)}
          edgeList={edges.map((e) => ({ source: e.source, target: e.target }))}
          readOnly={readOnly}
          onChange={(patch) => updateNodeDef(selectedDef.id, patch)}
          dirty={dirty}
          onSave={async () => { await save(); }}
          onDelete={selectedDef.type !== "trigger" ? () => removeNode(selectedDef.id) : undefined}
          onClose={() => setSelected(null)}
        />
      )}

      {runInputsOpen && (
        <RunInputsSheet
          inputs={((nodes.map((n) => (n.data as FlowNodeData).def).find((d) => d.type === "trigger")?.config?.inputs as TriggerInputDef[] | undefined) ?? [])}
          onRun={(values) => {
            setRunInputsOpen(false);
            void doRun(values);
          }}
          onClose={() => setRunInputsOpen(false)}
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
  const [search, setSearch] = useState("");
  const [availableOnly, setAvailableOnly] = useState(true);
  const [favorites, setFavorites] = useState<string[]>(() => {
    try {
      const value = JSON.parse(localStorage.getItem("control-deck.workflow-node-favorites") || "[]");
      return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
    } catch {
      return [];
    }
  });
  const { data: backendMetadata } = useQuery({
    queryKey: ["workflow-node-catalog"],
    queryFn: () => api<NodeMetadata[]>("/workflows/node-catalog"),
    staleTime: Infinity,
  });
  const registered = useMemo(() => new Set(backendMetadata?.map((item) => item.type) ?? []), [backendMetadata]);
  const toggleFavorite = (type: string) => {
    const next = favorites.includes(type) ? favorites.filter((item) => item !== type) : [...favorites, type];
    setFavorites(next);
    try {
      localStorage.setItem("control-deck.workflow-node-favorites", JSON.stringify(next));
    } catch {
      // storageを無効化したブラウザでも、その画面内のお気に入り操作は維持する。
    }
  };
  const byCategory = useMemo(() => {
    const map: Record<string, [string, (typeof NODE_TYPES)[string], boolean][]> = {};
    const needle = search.trim().toLocaleLowerCase();
    for (const [type, meta] of Object.entries(NODE_TYPES)) {
      if (type === "trigger") continue;
      const available = registered.has(type);
      if (availableOnly && !available) continue;
      if (needle && !`${type} ${meta.label} ${meta.desc ?? ""} ${meta.category}`.toLocaleLowerCase().includes(needle)) continue;
      (map[meta.category] ??= []).push([type, meta, available]);
    }
    return map;
  }, [availableOnly, registered, search]);
  const favoriteEntries = useMemo(
    () => Object.values(byCategory).flat().filter(([type]) => favorites.includes(type)),
    [byCategory, favorites],
  );
  const visibleCount = Object.values(byCategory).reduce((total, entries) => total + entries.length, 0);
  const nodeCard = ([type, meta, available]: [string, (typeof NODE_TYPES)[string], boolean]) => (
    <div key={type} className={`flex items-center rounded-xl border ${available ? "border-zinc-200 dark:border-zinc-700" : "border-dashed border-zinc-200 opacity-60 dark:border-zinc-700"}`}>
      <button
        onClick={() => available && onAdd(type)}
        disabled={!available}
        title={meta.desc}
        className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left hover:bg-accent-50/40 disabled:cursor-not-allowed dark:hover:bg-accent-600/10"
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-sm" style={{ backgroundColor: `${meta.color}1a`, color: meta.color }}>
          {meta.icon}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{meta.label}</span>
          <span className="block truncate text-[10px] text-zinc-400">{available ? (meta.desc || type) : "未導入・利用不可"}</span>
        </span>
      </button>
      <button
        type="button"
        onClick={() => toggleFavorite(type)}
        aria-label={`${meta.label}を${favorites.includes(type) ? "お気に入りから削除" : "お気に入りに追加"}`}
        className="mr-1 rounded-lg p-2 text-lg text-amber-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
      >
        {favorites.includes(type) ? "★" : "☆"}
      </button>
    </div>
  );

  return (
    <BottomSheet title="ノードを追加" onClose={onClose} wide>
      <div className="mb-4 space-y-2">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="ノードを検索"
          placeholder="名前・type・説明・カテゴリを検索"
          className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        />
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-500">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={availableOnly} onChange={(event) => setAvailableOnly(event.target.checked)} />
            利用可能なノードのみ
          </label>
          <span>{backendMetadata ? `${visibleCount}件` : "利用可能ノードを確認中..."}</span>
        </div>
      </div>
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
      {favoriteEntries.length > 0 && (
        <div className="mb-3">
          <p className="mb-1 px-1 text-xs font-medium text-amber-600 dark:text-amber-400">お気に入り</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{favoriteEntries.map(nodeCard)}</div>
        </div>
      )}
      {CATEGORY_ORDER.filter((c) => byCategory[c]).map((category) => (
        <div key={category} className="mb-3">
          <p className="mb-1 px-1 text-xs font-medium text-zinc-400">{category}</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{byCategory[category].map(nodeCard)}</div>
        </div>
      ))}
      {backendMetadata && visibleCount === 0 && (
        <p className="rounded-xl bg-zinc-50 p-4 text-center text-sm text-zinc-500 dark:bg-zinc-800/60">条件に一致するノードはありません</p>
      )}
    </BottomSheet>
  );
}

// ---- ノード設定フォーム ----

/** 対象ノードの上流ノード（データを参照できるノード）を逆向き BFS で求める */
function upstreamDefs(nodeId: string, defs: DefNode[], edgeList: { source: string; target: string }[]): DefNode[] {
  const byId = new Map(defs.map((d) => [d.id, d]));
  const seen = new Set<string>();
  const queue = edgeList.filter((e) => e.target === nodeId).map((e) => e.source);
  const result: DefNode[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    const d = byId.get(id);
    if (d) result.push(d);
    queue.push(...edgeList.filter((e) => e.target === id).map((e) => e.source));
  }
  return result;
}

function NodeConfigSheet({
  workflowId,
  def,
  allDefs,
  edgeList,
  readOnly,
  dirty,
  onSave,
  onChange,
  onDelete,
  onClose,
}: {
  workflowId: number;
  def: DefNode;
  allDefs: DefNode[];
  edgeList: { source: string; target: string }[];
  readOnly: boolean;
  dirty: boolean;
  onSave: () => Promise<void>;
  onChange: (patch: Partial<DefNode>) => void;
  onDelete?: () => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"settings" | "input" | "output" | "run" | "error" | "details">("settings");
  const meta = NODE_TYPES[def.type];
  const { data: backendMetadata } = useQuery({
    queryKey: ["workflow-node-catalog"],
    queryFn: () => api<NodeMetadata[]>("/workflows/node-catalog"),
    staleTime: Infinity,
  });
  const nodeMetadata = backendMetadata?.find((item) => item.type === def.type);
  const { data: apps } = useQuery({
    queryKey: ["apps"],
    queryFn: () => api<ManagedApp[]>("/apps"),
    enabled: meta?.fields.some((f) => f.type === "app") ?? false,
  });
  const { data: workflowList } = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api<{ id: number; name: string }[]>("/workflows"),
    enabled: meta?.fields.some((f) => f.type === "workflow") ?? false,
  });
  const config = def.config ?? {};
  const setConfig = (key: string, value: unknown) => onChange({ config: { ...config, [key]: value } });
  const applyRecommended = () => {
    if (!nodeMetadata) return;
    const next = { ...config };
    for (const [key, value] of Object.entries(nodeMetadata.initial_config ?? {})) {
      if (next[key] === undefined || next[key] === "" || next[key] === null) next[key] = structuredClone(value);
    }
    for (const [key, schema] of Object.entries(nodeMetadata.config_schema)) {
      if ((next[key] === undefined || next[key] === "" || next[key] === null) && schema.recommended !== undefined) {
        next[key] = structuredClone(schema.recommended);
      }
    }
    onChange({ config: next });
  };
  const insertAtCursor = (key: string, expression: string) => {
    const element = document.getElementById(`node-config-${def.id}-${key}`) as HTMLInputElement | HTMLTextAreaElement | null;
    const current = String(config[key] ?? "");
    const start = element?.selectionStart ?? current.length;
    const end = element?.selectionEnd ?? start;
    setConfig(key, `${current.slice(0, start)}${expression}${current.slice(end)}`);
    requestAnimationFrame(() => {
      const nextElement = document.getElementById(`node-config-${def.id}-${key}`) as HTMLInputElement | HTMLTextAreaElement | null;
      nextElement?.focus();
      nextElement?.setSelectionRange(start + expression.length, start + expression.length);
    });
  };

  // webhook トリガー: トークン未設定なら自動生成
  useEffect(() => {
    if (def.type === "trigger" && config.mode === "webhook" && !config.webhook_token && !readOnly) {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      setConfig("webhook_token", Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [def.type, config.mode]);
  const visibleFields = (meta?.fields ?? []).filter((f) => !f.showIf || String(config[f.showIf.key] ?? "") === f.showIf.value);
  const upstream = useMemo(() => upstreamDefs(def.id, allDefs, edgeList), [def.id, allDefs, edgeList]);
  // 上流で定義された名前付き変数（出力変数名）
  const namedVars = useMemo(
    () => upstream.map((d) => String(d.config?.output_var ?? "").trim()).filter(Boolean),
    [upstream],
  );
  const { data: latestExecutions } = useQuery({
    queryKey: ["executions", workflowId],
    queryFn: () => api<Array<{ id: number }>>(`/workflow-executions?workflow_id=${workflowId}&limit=1`),
  });
  const latestExecutionId = latestExecutions?.[0]?.id;
  const { data: latestExecution } = useQuery({
    queryKey: ["execution", latestExecutionId],
    queryFn: () => api<{ context: Record<string, { output?: unknown; status: string; finished_at?: string; error?: string; error_context?: Record<string, unknown> }> }>(`/workflow-executions/${latestExecutionId}`),
    enabled: latestExecutionId !== undefined,
  });
  const lastEntry = latestExecution?.context[def.id];
  const lastErrorContext = lastEntry?.error_context
    ?? (lastEntry?.output as { error?: unknown } | undefined)?.error;

  const inspectorTabs = [
    ["settings", "設定"], ["input", "入力"], ["output", "出力"],
    ["run", "実行"], ["error", "エラー"], ["details", "詳細"],
  ] as const;

  return (
    <BottomSheet
      title={meta?.label ?? def.type}
      onClose={onClose}
      wide
      stable
      headerActions={onDelete && !readOnly ? (
        <button
          type="button"
          onClick={onDelete}
          aria-label="このノードを削除"
          title="このノードを削除"
          className="grid h-11 w-11 place-items-center rounded-xl text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
        >
          <IconTrash />
        </button>
      ) : undefined}
    >
      <div className="-mx-1 mb-3 flex gap-1 overflow-x-auto pb-1" role="tablist" aria-label="ノードインスペクタ">
        {inspectorTabs.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)} className={`shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-medium ${tab === key ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-400" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}>{label}</button>
        ))}
      </div>
      {meta?.desc && <p className="mb-3 rounded-lg bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-800/60">{meta.desc}</p>}
      {nodeMetadata && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 text-[10px]">
          <span className={`rounded-full px-2 py-1 font-medium ${
            nodeMetadata.side_effect === "none" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400" :
            nodeMetadata.side_effect === "read" ? "bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-400" :
            "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400"
          }`}>副作用: {nodeMetadata.side_effect}</span>
          {nodeMetadata.capabilities.map((capability) => (
            <span key={capability} className="rounded-full bg-zinc-100 px-2 py-1 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">{capability}</span>
          ))}
        </div>
      )}
      {tab === "settings" && <div className="space-y-4">
        {nodeMetadata && !readOnly && (
          <div className="flex items-start justify-between gap-3 rounded-xl border border-accent-200 bg-accent-50/50 p-3 dark:border-accent-800 dark:bg-accent-950/20">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-accent-800 dark:text-accent-300">迷ったら推奨設定で開始</p>
              <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">空欄だけを補完します。入力済みの値、URL、Secret、環境固有設定は変更しません。</p>
            </div>
            <button type="button" onClick={applyRecommended} className="min-h-11 shrink-0 rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white">推奨値を適用</button>
          </div>
        )}
        {nodeMetadata && (
          <details className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
            <summary className="cursor-pointer text-xs font-semibold text-zinc-700 dark:text-zinc-200">このノードの使い方・推奨理由・構成例</summary>
            <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-zinc-500">{nodeMetadata.ui_hints.help || nodeMetadata.description}</p>
            {nodeMetadata.ui_hints.quick_start && <p className="mt-2 rounded-lg bg-accent-50 p-2.5 text-xs leading-relaxed text-accent-800 dark:bg-accent-950/30 dark:text-accent-300"><strong>最短手順:</strong> {nodeMetadata.ui_hints.quick_start}</p>}
            {(nodeMetadata.ui_hints.examples ?? []).length > 0 && <p className="mt-2 text-[11px] text-zinc-400">具体的な設定例は「詳細」タブから確認・反映できます。</p>}
          </details>
        )}
        <Field label="表示名">
          <input value={def.name ?? ""} onChange={(e) => onChange({ name: e.target.value })} disabled={readOnly} className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        </Field>
        {def.type === "llm.chat" && !readOnly && (
          <LlmEndpointDetect
            onPick={(base, model) => {
              const next = { ...config, base_url: base } as Record<string, unknown>;
              if (model) next.model = model;
              onChange({ config: next });
            }}
          />
        )}
        {visibleFields.map((f) => {
          const schema = nodeMetadata?.config_schema[f.key];
          return (
          <Field key={f.key} label={`${f.label}${schema?.required ? "（必須）" : ""}`} hint={f.hint}>
            <ConfigInput inputId={`node-config-${def.id}-${f.key}`} field={f} value={config[f.key]} disabled={readOnly} apps={apps} workflows={workflowList} scrapeUrl={String(config.url ?? "")} onChange={(v) => setConfig(f.key, v)} />
            {(schema?.recommended !== undefined || schema?.reason) && (
              <div className="mt-1.5 rounded-lg bg-zinc-50 px-2.5 py-2 text-[11px] leading-relaxed text-zinc-500 dark:bg-zinc-800/60">
                {schema.recommended !== undefined && <p><strong className="text-zinc-600 dark:text-zinc-300">推奨:</strong> <code className="break-all font-mono">{typeof schema.recommended === "string" ? schema.recommended : JSON.stringify(schema.recommended)}</code></p>}
                {schema.reason && <p className={schema.recommended !== undefined ? "mt-1" : ""}><strong className="text-zinc-600 dark:text-zinc-300">理由:</strong> {schema.reason}</p>}
              </div>
            )}
            {f.key === "json_schema" && !readOnly && (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {JSON_SCHEMA_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => setConfig("json_schema", JSON.stringify(p.schema, null, 2))}
                    className="rounded-lg bg-zinc-100 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            )}
            {!readOnly && (f.type === "text" || f.type === "textarea" || f.type === "code") && (
              <VarPicker
                upstream={upstream}
                namedVars={namedVars}
                directIds={new Set(edgeList.filter((edge) => edge.target === def.id).map((edge) => edge.source))}
                metadata={backendMetadata ?? []}
                executionContext={latestExecution?.context}
                expectedType={schema?.type}
                onInsert={(expr) => insertAtCursor(f.key, expr)}
              />
            )}
          </Field>
        )})}
        {def.type !== "trigger" && (
          <Field label="出力変数名（任意）" hint={"設定すると全後段から {{vars.名前.フィールド}} で参照できます"}>
            <input
              value={String(config.output_var ?? "")}
              onChange={(e) => setConfig("output_var", e.target.value.replace(/[^\w-]/g, ""))}
              disabled={readOnly}
              placeholder="result"
              className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
            />
          </Field>
        )}
      </div>}
      {tab === "input" && (
        <div className="space-y-2">
          <p className="text-xs text-zinc-400">このノードへ到達できる上流変数と、直近実行の値です。</p>
          {upstream.length === 0 ? <p className="rounded-xl border border-dashed border-zinc-300 p-3 text-xs text-zinc-400 dark:border-zinc-700">上流ノードはありません。</p> : upstream.map((node) => {
            const entry = latestExecution?.context[node.id];
            return <div key={node.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><div className="flex gap-2 text-xs"><strong className="min-w-0 flex-1 truncate">{node.name || NODE_TYPES[node.type]?.label || node.id}</strong><code className="text-[10px] text-zinc-400">{node.type}</code></div><p className="mt-1 font-mono text-[10px] text-zinc-400">{node.id} · {entry?.status || "未実行"}</p>{entry?.output !== undefined && <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-50 p-2 font-mono text-[10px] dark:bg-zinc-950">{JSON.stringify(entry.output, null, 2)}</pre>}</div>;
          })}
        </div>
      )}
      {tab === "output" && (
        <div className="space-y-3">
          <Field label="出力 schema"><pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-50 p-3 font-mono text-xs dark:bg-zinc-950">{JSON.stringify(nodeMetadata?.output_schema ?? Object.fromEntries((meta?.outputs ?? []).map((item) => [item.key, "unknown"])), null, 2)}</pre></Field>
          <Field label="直近実行値">{lastEntry?.output !== undefined ? <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-50 p-3 font-mono text-xs dark:bg-zinc-950">{JSON.stringify(lastEntry.output, null, 2)}</pre> : <p className="rounded-xl border border-dashed border-zinc-300 p-3 text-xs text-zinc-400 dark:border-zinc-700">実行値はまだありません。</p>}</Field>
        </div>
      )}
      {tab === "run" && (
        <div className="space-y-3">
          {def.type !== "trigger" && def.type !== "control.loop" && def.type !== "human.approval" && !readOnly ? (
            <NodeTestRunner
              workflowId={workflowId}
              nodeId={def.id}
              type={def.type}
              config={config}
              latestExecutionId={latestExecutionId}
              latestOutput={lastEntry?.output}
              dirty={dirty}
              onSave={onSave}
            />
          ) : <p className="text-xs text-zinc-400">このノードは単体previewの対象外です。</p>}
        </div>
      )}
      {tab === "error" && (def.type === "trigger" ? <p className="text-xs text-zinc-400">トリガーにはノード単位のエラー処理設定はありません。</p> : (
        <div className="space-y-3">
          <ControlSection config={config} readOnly={readOnly} setConfig={setConfig} />
          {lastErrorContext !== undefined && lastErrorContext !== null && (
            <Field label="直近の Error Context" hint="secret・Authorization・API keyは保存前に伏せ字化されます">
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-red-50 p-3 font-mono text-[10px] text-red-800 dark:bg-red-950/30 dark:text-red-200">{JSON.stringify(lastErrorContext, null, 2)}</pre>
            </Field>
          )}
        </div>
      ))}
      {tab === "details" && (
        <div className="space-y-3 text-xs">
          {nodeMetadata && (
            <section className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
              <h3 className="font-semibold text-zinc-700 dark:text-zinc-200">使い方と構成例</h3>
              {nodeMetadata.ui_hints.help && <p className="mt-2 whitespace-pre-wrap leading-relaxed text-zinc-500">{nodeMetadata.ui_hints.help}</p>}
              {nodeMetadata.ui_hints.quick_start && <p className="mt-2 rounded-lg bg-accent-50 p-2.5 leading-relaxed text-accent-800 dark:bg-accent-950/30 dark:text-accent-300"><strong>最短手順:</strong> {nodeMetadata.ui_hints.quick_start}</p>}
              {(nodeMetadata.ui_hints.examples ?? []).map((example) => (
                <div key={example.title} className="mt-2 rounded-lg bg-zinc-50 p-2.5 dark:bg-zinc-950">
                  <p className="font-medium">{example.title}</p>
                  <pre className="mt-1 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] text-zinc-500">{JSON.stringify(example.config, null, 2)}</pre>
                  {!readOnly && <button type="button" onClick={() => onChange({ config: { ...config, ...example.config } })} className="mt-2 min-h-9 rounded-lg border border-zinc-300 px-2.5 text-[11px] font-medium dark:border-zinc-700">この例を設定へ反映</button>}
                </div>
              ))}
            </section>
          )}
          <dl className="grid grid-cols-[7rem_1fr] gap-x-2 gap-y-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><dt className="text-zinc-400">node ID</dt><dd className="break-all font-mono">{def.id}</dd><dt className="text-zinc-400">type</dt><dd className="font-mono">{def.type}</dd><dt className="text-zinc-400">version</dt><dd className="num">{nodeMetadata?.version ?? 1}</dd><dt className="text-zinc-400">side effect</dt><dd>{nodeMetadata?.side_effect ?? "unknown"}</dd><dt className="text-zinc-400">capabilities</dt><dd>{nodeMetadata?.capabilities.join(", ") || "なし"}</dd></dl>
          <Field label="JSON 設定"><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-50 p-3 font-mono text-xs dark:bg-zinc-950">{JSON.stringify(def, null, 2)}</pre></Field>
          <p className="text-zinc-400">参照式: <code className="font-mono">{"{{"}{def.id}.フィールド{"}}"}</code></p>
        </div>
      )}
    </BottomSheet>
  );
}

/** 実行制御（全ノード共通）: リトライ / 失敗時の挙動 / 承認 / 合流 */
function ControlSection({
  config,
  readOnly,
  setConfig,
}: {
  config: Record<string, unknown>;
  readOnly: boolean;
  setConfig: (key: string, value: unknown) => void;
}) {
  const active =
    Number(config.retry_count) > 0 || !!config.require_approval ||
    Number(config.node_timeout) > 0 || (config.on_error && config.on_error !== "stop") || config.join === "all";
  const [open, setOpen] = useState(false);
  const cls = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-700">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3.5 py-2.5 text-xs font-medium text-zinc-600 dark:text-zinc-300"
      >
        <span>⚙ 実行制御（リトライ・失敗時・承認・合流）</span>
        <span className="text-zinc-400">{active && !open ? "設定あり " : ""}{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-zinc-200 px-3.5 py-3 dark:border-zinc-700">
          <div className="grid grid-cols-2 gap-2">
            <Field label="リトライ回数（0-5）">
              <input type="number" min={0} max={5} value={String(config.retry_count ?? 0)} disabled={readOnly}
                onChange={(e) => setConfig("retry_count", Math.max(0, Math.min(5, Number(e.target.value) || 0)))} className={cls} />
            </Field>
            <Field label="リトライ間隔（秒）">
              <input type="number" min={0} value={String(config.retry_wait ?? 5)} disabled={readOnly}
                onChange={(e) => setConfig("retry_wait", Number(e.target.value) || 0)} className={cls} />
            </Field>
          </div>
          <Field label="ノードのtimeout（秒）" hint="空欄はノード種別ごとの安全な既定値。0.1〜ワークフロー上限へ制限されます">
            <input type="number" min={0.1} step={0.1} value={String(config.node_timeout ?? "")} disabled={readOnly}
              placeholder="既定値を使用"
              onChange={(e) => setConfig("node_timeout", e.target.value === "" ? undefined : Math.max(0.1, Number(e.target.value) || 0.1))} className={cls} />
          </Field>
          <Field label="失敗したとき" hint={config.on_error === "branch" ? "ノード下部の赤い「失敗」と橙の「時間切れ」を個別に接続できます。時間切れ未接続時は失敗経路へ合流します" : undefined}>
            <select value={String(config.on_error ?? "stop")} disabled={readOnly}
              onChange={(e) => setConfig("on_error", e.target.value)} className={cls}>
              <option value="stop">フロー全体を停止（既定）</option>
              <option value="continue">無視して次へ進む</option>
              <option value="branch">「失敗時」の枝へ分岐</option>
            </select>
          </Field>
          <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2.5 dark:border-zinc-700">
            <span className="text-xs">✋ 実行前に承認を求める<span className="block text-[10px] text-zinc-400">情報パネルから承認/却下するまで一時停止します</span></span>
            <input type="checkbox" checked={!!config.require_approval} disabled={readOnly}
              onChange={(e) => setConfig("require_approval", e.target.checked || undefined)} className="h-4 w-4" />
          </label>
          <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2.5 dark:border-zinc-700">
            <span className="text-xs">⇥ 全入力を待って合流<span className="block text-[10px] text-zinc-400">複数の枝が全て終わってから 1 回だけ実行します（並列の待ち合わせ）</span></span>
            <input type="checkbox" checked={config.join === "all"} disabled={readOnly}
              onChange={(e) => setConfig("join", e.target.checked ? "all" : undefined)} className="h-4 w-4" />
          </label>
        </div>
      )}
    </div>
  );
}

/** ノード単体テスト: 安全preview、cache入力、固定データ、途中再開を同じ場所で扱う。 */
function NodeTestRunner({
  workflowId, nodeId, type, config, latestExecutionId, latestOutput, dirty, onSave,
}: {
  workflowId: number;
  nodeId: string;
  type: string;
  config: Record<string, unknown>;
  latestExecutionId?: number;
  latestOutput?: unknown;
  dirty: boolean;
  onSave: () => Promise<void>;
}) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [testedOutput, setTestedOutput] = useState<{ value: unknown; sourceExecutionId: number | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [inputMode, setInputMode] = useState<"latest_success" | "execution" | "manual" | "pinned">("latest_success");
  const [manualText, setManualText] = useState("{}");
  const [versionMode, setVersionMode] = useState<"current" | "historical">("current");
  const { data: pins } = useQuery({
    queryKey: ["workflow-pinned-data", workflowId],
    queryFn: () => api<PinnedData[]>(`/workflows/${workflowId}/pinned-data`),
  });
  const pin = pins?.find((item) => item.node_id === nodeId);
  const pinnableOutput = testedOutput?.value ?? latestOutput;
  const pinSourceExecutionId = testedOutput ? testedOutput.sourceExecutionId : latestExecutionId;

  const safePreview = async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await api("/workflows/test-node", { method: "POST", json: { type, config, dry_run: true } }));
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : "失敗しました" });
    } finally {
      setBusy(false);
    }
  };

  const nodeTest = async () => {
    let manualContext: Record<string, unknown> = {};
    if (inputMode === "manual") {
      try {
        const parsed: unknown = JSON.parse(manualText);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
        manualContext = parsed as Record<string, unknown>;
      } catch {
        show("手動入力はJSON objectで指定してください", "error");
        return;
      }
    }
    setBusy(true);
    setResult(null);
    try {
      const response = await api<Record<string, unknown>>(`/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/test`, {
        method: "POST",
        json: {
          input_mode: inputMode,
          execution_id: inputMode === "execution" ? latestExecutionId : undefined,
          manual_context: manualContext,
          config_override: config,
        },
      });
      setResult(response);
      if (response.ok && "output" in response) {
        setTestedOutput({
          value: response.output,
          sourceExecutionId: typeof response.source_execution_id === "number" ? response.source_execution_id : null,
        });
      }
    } catch (error) {
      setResult({ ok: false, error: error instanceof Error ? error.message : "単体実行に失敗しました" });
    } finally {
      setBusy(false);
    }
  };

  const runGraph = async (kind: "to" | "from") => {
    if (dirty) await onSave();
    setBusy(true);
    try {
      const path = kind === "to"
        ? `/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/run-to`
        : `/workflows/${workflowId}/executions/${latestExecutionId}/resume-from/${encodeURIComponent(nodeId)}`;
      const json = kind === "to" ? {} : { version_mode: versionMode };
      const response = await api<{ execution_id: number }>(path, { method: "POST", json });
      await qc.invalidateQueries({ queryKey: ["executions", workflowId] });
      show(`${kind === "to" ? "このノードまで" : "このノードから"}実行を開始しました（#${response.execution_id}）`);
    } catch (error) {
      show(error instanceof Error ? error.message : "部分実行に失敗しました", "error");
    } finally {
      setBusy(false);
    }
  };

  const togglePin = async () => {
    setBusy(true);
    try {
      if (pin) {
        await api(`/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/pinned-data`, { method: "DELETE" });
        show("固定データを解除しました");
      } else {
        await api(`/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/pinned-data`, {
          method: "PUT", json: { output: pinnableOutput, source_execution_id: pinSourceExecutionId },
        });
        show("ノード出力を固定しました");
        setInputMode("pinned");
      }
      await qc.invalidateQueries({ queryKey: ["workflow-pinned-data", workflowId] });
    } catch (error) {
      show(error instanceof Error ? error.message : "固定データの更新に失敗しました", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={safePreview}
        disabled={busy}
        className="w-full rounded-xl bg-zinc-100 py-2 text-xs font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-50 dark:bg-zinc-800 dark:text-zinc-300"
      >
        {busy ? "確認中..." : "🛡 このノードを安全プレビュー"}
      </button>
      <p className="mt-1 text-[10px] text-zinc-400">executor・外部通信・書き込み・secret復号は行いません</p>
      <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
        <Field label="単体実行に使う入力">
          <select value={inputMode} onChange={(event) => setInputMode(event.target.value as typeof inputMode)} className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-900">
            <option value="latest_success">最新の成功実行</option>
            <option value="execution" disabled={!latestExecutionId}>直近の実行 #{latestExecutionId ?? "なし"}</option>
            <option value="manual">手動JSON入力</option>
            <option value="pinned" disabled={!pin}>固定データ{pin ? ` #${pin.id}` : "（なし）"}</option>
          </select>
        </Field>
        {inputMode === "manual" && (
          <textarea aria-label="単体実行の手動JSON入力" value={manualText} onChange={(event) => setManualText(event.target.value)} rows={5} spellCheck={false} className="mt-2 w-full rounded-xl border border-zinc-300 bg-white p-3 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900" />
        )}
        <button type="button" onClick={nodeTest} disabled={busy || (inputMode === "execution" && !latestExecutionId) || (inputMode === "pinned" && !pin)} className="mt-2 min-h-11 w-full rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white disabled:opacity-50">
          {busy ? "実行中…" : inputMode === "manual" ? "編集した入力でこのノードだけ実行" : "このノードだけ実行"}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" onClick={() => void runGraph("to")} disabled={busy} className="min-h-11 rounded-xl border border-zinc-300 px-2 text-xs font-medium dark:border-zinc-700">このノードまで実行</button>
        <button type="button" onClick={togglePin} disabled={busy || (!pin && pinnableOutput === undefined)} className="min-h-11 rounded-xl border border-zinc-300 px-2 text-xs font-medium disabled:opacity-50 dark:border-zinc-700">{pin ? "📌 固定を解除" : "出力を固定"}</button>
      </div>
      <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
        <Field label="途中から再実行する定義">
          <select value={versionMode} onChange={(event) => setVersionMode(event.target.value as typeof versionMode)} className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-900">
            <option value="current">現在のフロー</option>
            <option value="historical">当時のフロー</option>
          </select>
        </Field>
        <button type="button" onClick={() => void runGraph("from")} disabled={busy || !latestExecutionId} className="mt-2 min-h-11 w-full rounded-xl border border-accent-300 px-3 text-xs font-semibold text-accent-700 disabled:opacity-50 dark:border-accent-700 dark:text-accent-300">このノードから再実行</button>
        <p className="mt-1 text-[10px] text-zinc-400">上流は実行 #{latestExecutionId ?? "-"} の保存済み入力を使い、このノード以降だけを再計算します。</p>
      </div>
      {result && (
        <div className={`mt-1.5 rounded-xl border p-2.5 ${result.ok ? "border-emerald-300 dark:border-emerald-800" : "border-red-300 dark:border-red-800"}`}>
          <p className={`text-xs font-medium ${result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
            {result.ok ? "✓ 実行可能な設定" : "✗ 設定を確認してください"}
          </p>
          {typeof result.error === "string" && <p className="mt-1 text-[11px] text-red-500">{result.error}</p>}
          <pre className="mt-1 max-h-48 overflow-auto rounded bg-zinc-50 p-2 font-mono text-[10px] dark:bg-zinc-950">{JSON.stringify(result, null, 1)}</pre>
        </div>
      )}
    </div>
  );
}

/** 変数ピッカー: 上流ノードの出力から選んで {{id.key}} を挿入する */
function VarPicker({
  upstream,
  namedVars,
  directIds,
  metadata,
  executionContext,
  expectedType,
  onInsert,
}: {
  upstream: DefNode[];
  namedVars: string[];
  directIds: Set<string>;
  metadata: NodeMetadata[];
  executionContext?: Record<string, { output?: unknown; status: string }>;
  expectedType?: string;
  onInsert: (expr: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  if (upstream.length === 0 && namedVars.length === 0) return null;
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const renderNode = (d: DefNode) => {
    const m = NODE_TYPES[d.type];
    const nodeMeta = metadata.find((item) => item.type === d.type);
    // トリガーは定義済み入力フィールドも変数として提示
    const extra: { key: string; label: string; type?: string }[] =
      d.type === "trigger"
        ? ((d.config?.inputs as TriggerInputDef[] | undefined) ?? []).map((i) => ({ key: i.key, label: i.label || i.key, type: i.type }))
        : [];
    const scrapeOuts: { key: string; label: string; type?: string }[] =
      d.type === "web.scrape"
        ? ((d.config?.extractors as ExtractorDef[] | undefined) ?? [])
            .filter((x) => x.name)
            .map((x) => ({ key: x.name, label: x.name, type: "string" }))
        : [];
    const errorOuts: { key: string; label: string; type?: string }[] =
      d.config?.on_error === "branch"
        ? [
            { key: "error.message", label: "error.message", type: "string" },
            { key: "error.code", label: "error.code", type: "string" },
            { key: "error.retryable", label: "error.retryable", type: "boolean" },
            { key: "error.attempt", label: "error.attempt", type: "integer" },
            { key: "error.timestamp", label: "error.timestamp", type: "datetime" },
            { key: "error.input_summary", label: "error.input_summary", type: "object" },
          ]
        : [];
    const catalogOuts = Object.entries(nodeMeta?.output_schema ?? {}).map(([key, type]) => ({
      key, label: m?.outputs?.find((item) => item.key === key)?.label ?? key, type,
    }));
    const known = new Set(catalogOuts.map((item) => item.key));
    const legacyOuts = (m?.outputs ?? []).filter((item) => !known.has(item.key)).map((item) => ({ ...item, type: "any" }));
    const outs = [...catalogOuts, ...legacyOuts, ...scrapeOuts, ...extra, ...errorOuts].filter((output) => {
      if (!normalizedSearch) return true;
      return `${d.name ?? ""} ${d.id} ${d.type} ${output.key} ${output.label} ${output.type ?? ""}`.toLocaleLowerCase().includes(normalizedSearch);
    });
    if (outs.length === 0) return null;
    const outputValue = executionContext?.[d.id]?.output;
    return (
      <div key={d.id}>
        <p className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-zinc-500">
          <span style={{ color: m?.color }}>{m?.icon}</span>
          <span className="min-w-0 truncate">{d.name || m?.label}</span> <code className="font-mono text-[10px] text-zinc-400">{d.id}</code>
        </p>
        <div className="space-y-1">
          {outs.map((o) => {
            const sample = o.key.includes(".")
              ? undefined
              : outputValue && typeof outputValue === "object" ? (outputValue as Record<string, unknown>)[o.key] : undefined;
            return (
              <button
                key={o.key}
                type="button"
                onClick={() => onInsert(`{{${d.id}.${o.key}}}`)}
                title={`{{${d.id}.${o.key}}}`}
                className="flex min-h-10 w-full items-center gap-2 rounded-lg bg-zinc-50 px-2.5 text-left hover:bg-accent-100 dark:bg-zinc-800 dark:hover:bg-accent-950/40"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[11px] text-zinc-700 dark:text-zinc-200">{o.label}</span>
                  <span className="block truncate font-mono text-[9px] text-zinc-400">{`{{${d.id}.${o.key}}}`}{sample !== undefined ? ` · ${JSON.stringify(sample).slice(0, 80)}` : ""}</span>
                </span>
                <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] ${expectedType && o.type && expectedType !== "string" && expectedType !== o.type && o.type !== "any" ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300" : "bg-zinc-200 text-zinc-500 dark:bg-zinc-700 dark:text-zinc-300"}`}>{o.type ?? "any"}</span>
              </button>
            );
          })}
        </div>
      </div>
    );
  };
  const direct = upstream.filter((node) => directIds.has(node.id));
  const other = upstream.filter((node) => !directIds.has(node.id));
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-medium text-accent-600 dark:text-accent-400"
      >
        {open ? "▾ 変数を挿入" : "▸ 変数を挿入（前段ノードの出力から選択）"}
      </button>
      {open && (
        <div className="mt-1.5 rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="ノード名・変数名・型で検索" aria-label="上流変数を検索" className="mb-2 min-h-11 w-full rounded-lg border border-zinc-300 bg-white px-3 text-xs dark:border-zinc-700 dark:bg-zinc-900" />
          <div className="max-h-72 space-y-3 overflow-y-auto overscroll-contain pr-1">
            {direct.length > 0 && <section><p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-accent-600">直前ノード</p><div className="space-y-2">{direct.map(renderNode)}</div></section>}
            {other.length > 0 && <section><p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-400">その他の上流ノード</p><div className="space-y-2">{other.map(renderNode)}</div></section>}
          {namedVars.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] font-medium text-zinc-500">名前付き変数</p>
              <div className="flex flex-wrap gap-1">
                {namedVars.map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => onInsert(`{{vars.${v}}}`)}
                    className="rounded-md bg-amber-50 px-1.5 py-0.5 font-mono text-[11px] text-amber-700 hover:bg-amber-100 dark:bg-amber-950/50 dark:text-amber-400"
                  >
                    vars.{v}
                  </button>
                ))}
              </div>
            </div>
          )}
          </div>
        </div>
      )}
    </div>
  );
}

/** 稼働中の OpenAI 互換サーバー検出（LLM ノード用） */
function LlmEndpointDetect({ onPick }: { onPick: (baseUrl: string, model?: string) => void }) {
  const [results, setResults] = useState<{ base_url: string; models: string[] }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const detect = async () => {
    setBusy(true);
    try {
      setResults(await api<{ base_url: string; models: string[] }[]>("/workflows/llm-endpoints"));
    } catch {
      setResults([]);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/60">
      <div className="flex items-center justify-between">
        <span className="text-xs text-zinc-500">稼働中の LLM サーバー</span>
        <button type="button" onClick={detect} disabled={busy} className="rounded-lg bg-white px-2.5 py-1 text-xs font-medium text-accent-600 shadow-sm disabled:opacity-40 dark:bg-zinc-900 dark:text-accent-400">
          {busy ? "検出中..." : "検出"}
        </button>
      </div>
      {results !== null && (
        results.length === 0 ? (
          <p className="mt-1.5 text-xs text-zinc-400">見つかりませんでした（Ollama / llama.cpp / LM Studio 等の稼働を確認）</p>
        ) : (
          <div className="mt-1.5 space-y-1.5">
            {results.map((r) => (
              <div key={r.base_url}>
                <button type="button" onClick={() => onPick(r.base_url)} className="font-mono text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">
                  {r.base_url}
                </button>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {r.models.slice(0, 8).map((mo) => (
                    <button key={mo} type="button" onClick={() => onPick(r.base_url, mo)} className="rounded-md bg-white px-1.5 py-0.5 font-mono text-[11px] text-zinc-600 shadow-sm hover:text-accent-700 dark:bg-zinc-900 dark:text-zinc-300">
                      {mo}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}

function ConfigInput({
  inputId, field, value, disabled, apps, workflows, scrapeUrl, onChange,
}: {
  inputId?: string;
  field: FieldDef;
  value: unknown;
  disabled: boolean;
  apps?: ManagedApp[];
  workflows?: { id: number; name: string }[];
  scrapeUrl?: string;
  onChange: (v: unknown) => void;
}) {
  const cls = "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  if (field.type === "inputs") {
    return <TriggerInputsEditor value={(value as TriggerInputDef[]) ?? []} disabled={disabled} onChange={onChange} />;
  }
  if (field.type === "workflow") {
    return (
      <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} disabled={disabled} className={cls}>
        <option value="">選択してください</option>
        {workflows?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
      </select>
    );
  }
  if (field.type === "extractors") {
    return <ExtractorsField value={(value as ExtractorDef[]) ?? []} url={scrapeUrl ?? ""} disabled={disabled} onChange={onChange} />;
  }
  if (field.type === "select") {
    return (
      <select value={String(value ?? field.options?.[0]?.value ?? "")} onChange={(e) => onChange(e.target.value)} disabled={disabled} className={cls}>
        {field.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  }
  if (field.type === "checkbox") {
    return <label className="flex min-h-11 items-center justify-between rounded-xl border border-zinc-300 px-3 dark:border-zinc-700"><span className="text-xs text-zinc-500">{value ? "有効" : "無効"}</span><input type="checkbox" checked={Boolean(value)} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="h-5 w-5" /></label>;
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
        id={inputId}
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
      id={inputId}
      type={field.type === "number" ? "number" : "text"}
      value={String(value ?? "")}
      onChange={(e) => onChange(field.type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
      disabled={disabled}
      placeholder={field.placeholder}
      className={cls}
    />
  );
}

/** トリガーの入力フィールド定義エディタ（Dify の User Input 相当） */
function TriggerInputsEditor({
  value,
  disabled,
  onChange,
}: {
  value: TriggerInputDef[];
  disabled: boolean;
  onChange: (v: TriggerInputDef[]) => void;
}) {
  const update = (i: number, patch: Partial<TriggerInputDef>) =>
    onChange(value.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const cls = "rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-xs dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <div className="space-y-2">
      {value.map((inp, i) => (
        <div key={i} className="rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
          <div className="flex flex-wrap items-center gap-1.5">
            <input value={inp.key} onChange={(e) => update(i, { key: e.target.value.replace(/[^\w-]/g, "") })} disabled={disabled} placeholder="変数名" className={`${cls} w-24 font-mono`} />
            <input value={inp.label ?? ""} onChange={(e) => update(i, { label: e.target.value })} disabled={disabled} placeholder="ラベル" className={`${cls} min-w-0 flex-1`} />
            <select value={inp.type} onChange={(e) => update(i, { type: e.target.value as TriggerInputDef["type"] })} disabled={disabled} className={cls}>
              <option value="text">テキスト</option>
              <option value="paragraph">長文</option>
              <option value="number">数値</option>
              <option value="boolean">真偽</option>
              <option value="select">選択</option>
              <option value="multi_select">複数選択</option>
              <option value="date">日付</option>
              <option value="datetime">日時</option>
              <option value="file">ファイル</option>
              <option value="file_list">複数ファイル</option>
              <option value="json">JSON</option>
              <option value="key_value">Key-value</option>
              <option value="secret_reference">Secret参照</option>
            </select>
            <label className="flex items-center gap-1 text-[11px] text-zinc-500">
              <input type="checkbox" checked={!!inp.required} onChange={(e) => update(i, { required: e.target.checked })} disabled={disabled} />必須
            </label>
            {!disabled && (
              <button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="削除" className="px-1 text-zinc-400 hover:text-red-500">×</button>
            )}
          </div>
          {(inp.type === "select" || inp.type === "multi_select") && (
            <input value={inp.options ?? ""} onChange={(e) => update(i, { options: e.target.value })} disabled={disabled} placeholder="選択肢（カンマ区切り: A,B,C）" className={`${cls} mt-1.5 w-full`} />
          )}
          <div className="mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            <input value={inp.description ?? ""} onChange={(e) => update(i, { description: e.target.value })} disabled={disabled} placeholder="説明" className={cls} />
            <input value={inp.placeholder ?? ""} onChange={(e) => update(i, { placeholder: e.target.value })} disabled={disabled} placeholder="placeholder" className={cls} />
            <input value={typeof inp.default === "string" || typeof inp.default === "number" ? String(inp.default) : ""} onChange={(e) => update(i, { default: inp.type === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value })} disabled={disabled} placeholder="初期値" className={cls} />
            <input type="number" min={1} value={inp.maxLength ?? ""} onChange={(e) => update(i, { maxLength: e.target.value ? Number(e.target.value) : undefined })} disabled={disabled} placeholder="最大長" className={cls} />
          </div>
        </div>
      ))}
      {!disabled && (
        <button
          type="button"
          onClick={() => onChange([...value, { key: `input${value.length + 1}`, label: "", type: "text" }])}
          className="w-full rounded-xl border border-dashed border-zinc-300 py-2 text-xs font-medium text-zinc-500 hover:border-accent-400 hover:text-accent-600 dark:border-zinc-700"
        >
          + 入力フィールドを追加
        </button>
      )}
    </div>
  );
}

/** 実行時の入力ダイアログ（トリガーの入力フィールド定義に基づく） */
function RunInputsSheet({
  inputs,
  onRun,
  onClose,
}: {
  inputs: TriggerInputDef[];
  onRun: (values: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [filePick, setFilePick] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setValues((prev) => ({ ...prev, [k]: v }));
  const missing = inputs.filter((i) => i.required && !String(values[i.key] ?? "").trim());
  const cls = "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <BottomSheet title="実行時の入力" onClose={onClose}>
      <div className="space-y-3">
        {inputs.map((inp) => (
          <Field key={inp.key} label={`${inp.label || inp.key}${inp.required ? " *" : ""}`}>
            {inp.type === "paragraph" ? (
              <textarea aria-label={`${inp.label || inp.key}${inp.required ? " *" : ""}`} value={String(values[inp.key] ?? "")} onChange={(e) => set(inp.key, e.target.value)} rows={3} className={cls} />
            ) : inp.type === "number" ? (
              <input aria-label={`${inp.label || inp.key}${inp.required ? " *" : ""}`} type="number" value={String(values[inp.key] ?? "")} onChange={(e) => set(inp.key, e.target.value === "" ? "" : Number(e.target.value))} className={cls} />
            ) : inp.type === "select" ? (
              <select aria-label={`${inp.label || inp.key}${inp.required ? " *" : ""}`} value={String(values[inp.key] ?? "")} onChange={(e) => set(inp.key, e.target.value)} className={cls}>
                <option value="">選択してください</option>
                {(inp.options ?? "").split(",").map((o) => o.trim()).filter(Boolean).map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : inp.type === "file" ? (
              <div className="flex gap-1.5">
                <input aria-label={`${inp.label || inp.key}${inp.required ? " *" : ""}`} value={String(values[inp.key] ?? "")} onChange={(e) => set(inp.key, e.target.value)} placeholder="/path/to/file" className={`${cls} min-w-0 flex-1 font-mono text-xs`} />
                <button type="button" aria-label={`${inp.label || inp.key}を選択`} onClick={() => setFilePick(inp.key)} className="shrink-0 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">📁</button>
              </div>
            ) : (
              <input aria-label={`${inp.label || inp.key}${inp.required ? " *" : ""}`} value={String(values[inp.key] ?? "")} onChange={(e) => set(inp.key, e.target.value)} className={cls} />
            )}
          </Field>
        ))}
        <button
          onClick={() => onRun(values)}
          disabled={missing.length > 0}
          className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
        >
          実行
        </button>
      </div>
      {filePick && (
        <FilePicker
          mode="file"
          title="ファイルを選択"
          onSelect={(p) => {
            set(filePick, p);
            setFilePick(null);
          }}
          onClose={() => setFilePick(null)}
        />
      )}
    </BottomSheet>
  );
}

/** Web スクレイピングの抽出項目フィールド（コンパクト編集 + ビューワ起動） */
function ExtractorsField({
  value,
  url,
  disabled,
  onChange,
}: {
  value: ExtractorDef[];
  url: string;
  disabled: boolean;
  onChange: (v: ExtractorDef[]) => void;
}) {
  const [viewerOpen, setViewerOpen] = useState(false);
  return (
    <div className="space-y-2">
      {value.length === 0 ? (
        <p className="text-xs text-zinc-400">抽出項目がありません。ビューワでページから選択できます。</p>
      ) : (
        <ul className="space-y-1">
          {value.map((ex, i) => (
            <li key={i} className="flex items-center gap-2 rounded-lg border border-zinc-200 px-2 py-1.5 text-xs dark:border-zinc-700">
              <code className="shrink-0 font-mono font-medium text-accent-600 dark:text-accent-400">{ex.name || "(無名)"}</code>
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-400">{ex.selector}</span>
              <span className="shrink-0 text-[10px] text-zinc-400">{ex.multiple ? "複数" : "単体"}</span>
              {!disabled && (
                <button onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="削除" className="shrink-0 text-zinc-400 hover:text-red-500">×</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!disabled && (
        <button
          type="button"
          onClick={() => setViewerOpen(true)}
          className="w-full rounded-xl bg-accent-50 py-2 text-xs font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400"
        >
          🔍 抽出ビューワを開く（クリックで選択・結果を確認）
        </button>
      )}
      {viewerOpen && (
        <ScrapeViewer url={url} extractors={value} onChange={onChange} onClose={() => setViewerOpen(false)} />
      )}
    </div>
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

interface ExecutionNodeRun {
  id: number;
  node_id: string;
  node_type: string;
  status: string;
  outputs: unknown;
  error: { message?: string };
  elapsed_ms: number | null;
  attempt: number;
  retry_count: number;
  token_usage: Record<string, unknown>;
}

function ExecutionsSheet({ workflowId, onClose }: { workflowId: number; onClose: () => void }) {
  const [detailId, setDetailId] = useState<number | null>(null);
  const [retrying, setRetrying] = useState<"current" | "historical" | null>(null);
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const { data: executions } = useQuery({
    queryKey: ["executions", workflowId],
    queryFn: () => api<ExecutionSummary[]>(`/workflow-executions?workflow_id=${workflowId}`),
    refetchInterval: 2000,
  });
  const { data: detail } = useQuery({
    queryKey: ["execution", detailId],
    queryFn: () => api<ExecutionSummary & { workflow_version_id: number | null; context: Record<string, { status: string; output?: unknown; error?: string }> }>(`/workflow-executions/${detailId}`),
    enabled: detailId !== null,
    refetchInterval: (q) => (q.state.data && ["QUEUED", "RUNNING"].includes(q.state.data.status) ? 1500 : false),
  });
  const { data: nodeRuns } = useQuery({
    queryKey: ["execution-node-runs", workflowId, detailId],
    queryFn: () => api<ExecutionNodeRun[]>(`/workflows/${workflowId}/executions/${detailId}/nodes`),
    enabled: detailId !== null,
    refetchInterval: detail && ["QUEUED", "RUNNING", "WAITING"].includes(detail.status) ? 1500 : false,
  });
  const retryExecution = async (versionMode: "current" | "historical") => {
    if (detailId === null) return;
    setRetrying(versionMode);
    try {
      const result = await api<{ execution_id: number }>(`/workflows/${workflowId}/executions/${detailId}/retry`, {
        method: "POST", json: { version_mode: versionMode },
      });
      await qc.invalidateQueries({ queryKey: ["executions", workflowId] });
      setDetailId(result.execution_id);
      show(versionMode === "historical" ? "当時のフローで再実行しました" : "現在のフローで再実行しました");
    } catch (error) {
      show(error instanceof Error ? error.message : "再実行に失敗しました", "error");
    } finally {
      setRetrying(null);
    }
  };
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
          <div className="flex flex-wrap items-center gap-2">
            <p className={`text-sm font-medium ${statusCls[detail.status] ?? ""}`}>{detail.status}</p>
            {detail.workflow_version_id && <span className="rounded-full bg-zinc-100 px-2 py-1 font-mono text-[10px] text-zinc-500 dark:bg-zinc-800">version #{detail.workflow_version_id}</span>}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" disabled={retrying !== null} onClick={() => void retryExecution("current")} className="min-h-11 rounded-xl bg-accent-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">{retrying === "current" ? "再実行中…" : "現在のフローで再実行"}</button>
            <button type="button" disabled={retrying !== null || !detail.workflow_version_id} onClick={() => void retryExecution("historical")} className="min-h-11 rounded-xl border border-zinc-300 px-3 py-2 text-xs font-semibold disabled:opacity-50 dark:border-zinc-700">{retrying === "historical" ? "再実行中…" : "当時のフローで再実行"}</button>
          </div>
          {detail.error && <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600 dark:bg-red-950/40 dark:text-red-400">{detail.error}</p>}
          {(nodeRuns ?? Object.entries(detail.context).map(([node_id, row], index) => ({
            id: index, node_id, node_type: "", status: row.status, outputs: row.output,
            error: { message: row.error }, elapsed_ms: null, attempt: 0, retry_count: 0, token_usage: {},
          }))).map((run) => (
            <div key={run.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="mb-1 flex items-center justify-between text-xs font-medium">
                <code className="font-mono">{run.node_id}</code>
                <span className={statusCls[run.status] ?? "text-zinc-400"}>{run.status}</span>
              </p>
              <p className="mb-1 text-[10px] text-zinc-400">{run.node_type}{run.elapsed_ms !== null ? ` · ${run.elapsed_ms}ms` : ""}{run.retry_count ? ` · retry ${run.retry_count}` : ""}</p>
              {run.error?.message && <p className="text-xs text-red-500">{run.error.message}</p>}
              {run.outputs !== undefined && Object.keys((run.outputs as Record<string, unknown>) ?? {}).length > 0 && (
                <pre className="mt-1 max-h-32 overflow-auto rounded bg-zinc-50 p-2 font-mono text-[11px] dark:bg-zinc-950">{JSON.stringify(run.outputs, null, 1)}</pre>
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
