/** React Flow ベースのワークフローエディター（遅延ロードチャンク）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Background,
  Controls,
  Handle,
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
import { BottomSheet } from "../../components/ui";
import { IconPlay, IconPlus, IconX } from "../../components/icons";
import { NODE_TYPES, newNodeId, type FieldDef } from "./nodeTypes";
import type { ManagedApp } from "../../types";

interface DefNode {
  id: string;
  type: string;
  name?: string;
  config?: Record<string, unknown>;
  position?: { x: number; y: number };
}

interface DefEdge {
  id?: string;
  source: string;
  target: string;
  branch?: string | null;
}

interface WorkflowDetail {
  id: number;
  name: string;
  enabled: boolean;
  definition: { nodes: DefNode[]; edges: DefEdge[] };
}

type FlowNodeData = { def: DefNode };

// ---- カスタムノード ----
function FlowNode({ data, selected }: NodeProps) {
  const def = (data as FlowNodeData).def;
  const meta = NODE_TYPES[def.type];
  return (
    <div
      className={`min-w-36 rounded-xl border bg-white px-3 py-2 shadow-sm dark:bg-zinc-900 ${
        selected ? "border-accent-500 ring-2 ring-accent-500/30" : "border-zinc-200 dark:border-zinc-700"
      }`}
    >
      {def.type !== "trigger" && (
        <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !bg-zinc-400" />
      )}
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: meta?.color ?? "#888" }} />
        <div className="min-w-0">
          <p className="truncate text-xs font-medium">{def.name || meta?.label || def.type}</p>
          <p className="text-[10px] text-zinc-400">{meta?.label}</p>
        </div>
      </div>
      {meta?.branches ? (
        <>
          <Handle id="true" type="source" position={Position.Right} style={{ top: "35%" }} className="!h-2.5 !w-2.5 !bg-emerald-500" />
          <Handle id="false" type="source" position={Position.Right} style={{ top: "70%" }} className="!h-2.5 !w-2.5 !bg-red-400" />
          <span className="pointer-events-none absolute -right-7 top-[22%] text-[9px] text-emerald-500">真</span>
          <span className="pointer-events-none absolute -right-7 top-[58%] text-[9px] text-red-400">偽</span>
        </>
      ) : (
        <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !bg-zinc-400" />
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
      position: n.position ?? { x: 80 + i * 200, y: 120 },
      data: { def: n },
    })),
    edges: (def.edges ?? []).map((e, i) => ({
      id: e.id ?? `e${i}`,
      source: e.source,
      target: e.target,
      sourceHandle: e.branch ?? undefined,
      animated: true,
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
  const [saving, setSaving] = useState(false);
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
      setEdges((eds) => addEdge({ ...conn, animated: true }, eds));
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
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        branch: e.sourceHandle ?? null,
      })),
    };
  }, [nodes, edges]);

  const save = async () => {
    setSaving(true);
    try {
      await api(`/workflows/${workflowId}`, {
        method: "PATCH",
        json: { name, definition: buildDefinition() },
      });
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

  const addNode = (type: string) => {
    const id = newNodeId();
    const meta = NODE_TYPES[type];
    const def: DefNode = { id, type, name: meta.label, config: {} };
    setNodes((ns) => [
      ...ns,
      {
        id,
        type: "cdNode",
        position: { x: 120 + ns.length * 40, y: 80 + ns.length * 50 },
        data: { def },
      },
    ]);
    setPaletteOpen(false);
    setSelected(id);
    markDirty();
  };

  const removeNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
    setSelected(null);
    markDirty();
  };

  const updateNodeDef = (id: string, patch: Partial<DefNode>) => {
    setNodes((ns) =>
      ns.map((n) =>
        n.id === id
          ? { ...n, data: { def: { ...(n.data as FlowNodeData).def, ...patch } } }
          : n,
      ),
    );
    markDirty();
  };

  const selectedDef = useMemo(() => {
    const node = nodes.find((n) => n.id === selected);
    return node ? (node.data as FlowNodeData).def : null;
  }, [nodes, selected]);

  return (
    <div className="flex h-full flex-col">
      {/* ツールバー */}
      <div className="flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <button
          onClick={() => navigate("/workflows")}
          aria-label="一覧へ戻る"
          className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          <IconX />
        </button>
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            markDirty();
          }}
          disabled={readOnly}
          aria-label="ワークフロー名"
          className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 py-1.5 text-sm font-medium hover:border-zinc-200 focus:border-accent-500 focus:outline-none dark:hover:border-zinc-700"
        />
        <button
          onClick={() => setExecutionsOpen(true)}
          className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          履歴
        </button>
        {!readOnly && (
          <button
            onClick={save}
            disabled={saving || !dirty}
            className="rounded-xl bg-zinc-100 px-3.5 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300"
          >
            {saving ? "保存中..." : dirty ? "保存" : "保存済み"}
          </button>
        )}
        {can("workflows.run") && (
          <button
            onClick={run}
            className="flex items-center gap-1 rounded-xl bg-accent-600 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-accent-700"
          >
            <IconPlay /> 実行
          </button>
        )}
      </div>

      {/* キャンバス */}
      <div className="relative min-h-0 flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={(c) => {
            onNodesChange(c);
            if (c.some((ch) => ch.type === "position" || ch.type === "remove")) markDirty();
          }}
          onEdgesChange={(c) => {
            onEdgesChange(c);
            if (c.some((ch) => ch.type === "remove")) markDirty();
          }}
          onConnect={onConnect}
          onNodeClick={(_e, n) => setSelected(n.id)}
          onPaneClick={() => setSelected(null)}
          nodeTypes={nodeTypes}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          fitView
          proOptions={{ hideAttribution: true }}
          className="!bg-zinc-50 dark:!bg-zinc-950"
        >
          <Background gap={20} />
          <Controls showInteractive={false} className="!bottom-6" />
        </ReactFlow>

        {!readOnly && (
          <button
            onClick={() => setPaletteOpen(true)}
            aria-label="ノードを追加"
            className="absolute bottom-6 right-4 z-10 grid place-items-center rounded-2xl bg-accent-600 p-3.5 text-xl text-white shadow-lg hover:bg-accent-700"
          >
            <IconPlus />
          </button>
        )}
      </div>

      {/* ノードパレット */}
      {paletteOpen && (
        <BottomSheet title="ノードを追加" onClose={() => setPaletteOpen(false)}>
          {Object.entries(
            Object.entries(NODE_TYPES)
              .filter(([t]) => t !== "trigger")
              .reduce<Record<string, [string, (typeof NODE_TYPES)[string]][]>>((acc, [t, meta]) => {
                (acc[meta.category] ??= []).push([t, meta]);
                return acc;
              }, {}),
          ).map(([category, items]) => (
            <div key={category} className="mb-3">
              <p className="mb-1 px-1 text-xs text-zinc-400">{category}</p>
              <div className="grid grid-cols-2 gap-2">
                {items.map(([type, meta]) => (
                  <button
                    key={type}
                    onClick={() => addNode(type)}
                    className="flex items-center gap-2 rounded-xl border border-zinc-200 px-3 py-2.5 text-left text-sm hover:border-accent-400 dark:border-zinc-700"
                  >
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: meta.color }} />
                    {meta.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </BottomSheet>
      )}

      {/* ノード設定シート */}
      {selectedDef && (
        <NodeConfigSheet
          def={selectedDef}
          readOnly={readOnly}
          onChange={(patch) => updateNodeDef(selectedDef.id, patch)}
          onDelete={selectedDef.type !== "trigger" ? () => removeNode(selectedDef.id) : undefined}
          onClose={() => setSelected(null)}
        />
      )}

      {/* 実行履歴 */}
      {executionsOpen && (
        <ExecutionsSheet workflowId={workflowId} onClose={() => setExecutionsOpen(false)} />
      )}
    </div>
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

  const setConfig = (key: string, value: unknown) =>
    onChange({ config: { ...config, [key]: value } });

  const visibleFields = (meta?.fields ?? []).filter(
    (f) => !f.showIf || String(config[f.showIf.key] ?? "") === f.showIf.value,
  );

  return (
    <BottomSheet title={meta?.label ?? def.type} onClose={onClose}>
      <div className="space-y-4">
        <Field label="表示名">
          <input
            value={def.name ?? ""}
            onChange={(e) => onChange({ name: e.target.value })}
            disabled={readOnly}
            className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          />
        </Field>
        {visibleFields.map((f) => (
          <Field key={f.key} label={f.label} hint={f.hint}>
            <ConfigInput
              field={f}
              value={config[f.key]}
              disabled={readOnly}
              apps={apps}
              onChange={(v) => setConfig(f.key, v)}
            />
          </Field>
        ))}
        <p className="text-xs text-zinc-400">
          ノード ID: <code className="font-mono">{def.id}</code>（他ノードから{" "}
          <code className="font-mono">{"{{"}{def.id}.フィールド{"}}"}</code> で参照）
        </p>
        {onDelete && !readOnly && (
          <button
            onClick={onDelete}
            className="w-full rounded-xl bg-red-50 py-2.5 text-sm font-medium text-red-600 hover:bg-red-100 dark:bg-red-950/40 dark:text-red-400"
          >
            このノードを削除
          </button>
        )}
      </div>
    </BottomSheet>
  );
}

function ConfigInput({
  field,
  value,
  disabled,
  apps,
  onChange,
}: {
  field: FieldDef;
  value: unknown;
  disabled: boolean;
  apps?: ManagedApp[];
  onChange: (v: unknown) => void;
}) {
  const cls =
    "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  if (field.type === "select") {
    return (
      <select value={String(value ?? field.options?.[0]?.value ?? "")} onChange={(e) => onChange(e.target.value)} disabled={disabled} className={cls}>
        {field.options?.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  }
  if (field.type === "app") {
    return (
      <select
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        disabled={disabled}
        className={cls}
      >
        <option value="">選択してください</option>
        {apps?.map((a) => (
          <option key={a.id} value={a.id}>{a.name}</option>
        ))}
      </select>
    );
  }
  if (field.type === "textarea") {
    return (
      <textarea
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={3}
        placeholder={field.placeholder}
        className={`${cls} font-mono text-xs`}
      />
    );
  }
  return (
    <input
      type={field.type === "number" ? "number" : "text"}
      value={String(value ?? "")}
      onChange={(e) =>
        onChange(field.type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)
      }
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
    queryFn: () =>
      api<ExecutionSummary & { context: Record<string, { status: string; output?: unknown; error?: string }> }>(
        `/workflow-executions/${detailId}`,
      ),
    enabled: detailId !== null,
    refetchInterval: (q) =>
      q.state.data && ["QUEUED", "RUNNING"].includes(q.state.data.status) ? 1500 : false,
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
                  <span className={`w-16 shrink-0 text-xs font-medium ${statusCls[ex.status] ?? "text-zinc-400"}`}>
                    {ex.status}
                  </span>
                  <span className="num min-w-0 flex-1 truncate text-xs text-zinc-400">
                    {new Date(ex.started_at + (ex.started_at.endsWith("Z") ? "" : "Z")).toLocaleString("ja-JP")}
                    {" · "}
                    {ex.trigger_type === "manual" ? "手動" : "スケジュール"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : detail ? (
        <div className="space-y-3">
          <p className={`text-sm font-medium ${statusCls[detail.status] ?? ""}`}>{detail.status}</p>
          {detail.error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600 dark:bg-red-950/40 dark:text-red-400">
              {detail.error}
            </p>
          )}
          {Object.entries(detail.context).map(([nodeId, r]) => (
            <div key={nodeId} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
              <p className="mb-1 flex items-center justify-between text-xs font-medium">
                <code className="font-mono">{nodeId}</code>
                <span className={statusCls[r.status] ?? "text-zinc-400"}>{r.status}</span>
              </p>
              {r.error && <p className="text-xs text-red-500">{r.error}</p>}
              {r.output !== undefined && (
                <pre className="mt-1 max-h-32 overflow-auto rounded bg-zinc-50 p-2 font-mono text-[11px] dark:bg-zinc-950">
                  {JSON.stringify(r.output, null, 1)}
                </pre>
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
