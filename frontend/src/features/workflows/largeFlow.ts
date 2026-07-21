import type { Edge, Node } from "@xyflow/react";

export interface WorkflowGroup {
  id: string;
  name: string;
  node_ids: string[];
  collapsed?: boolean;
  color?: string;
}

export interface LayoutNodeInput { id: string; width?: number; height?: number }
export interface LayoutEdgeInput { source: string; target: string }
export interface LayoutResult { id: string; x: number; y: number }

/** Cycleを含む定義でも停止する、決定的な左→右layer配置。Workerとtestから共用する。 */
export function computeLayeredLayout(nodes: LayoutNodeInput[], edges: LayoutEdgeInput[]): LayoutResult[] {
  const ids = new Set(nodes.map((node) => node.id));
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, [] as string[]]));
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target) || edge.source === edge.target) continue;
    outgoing.get(edge.source)!.push(edge.target);
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
  }
  const queue = nodes.map((node) => node.id).filter((id) => incoming.get(id) === 0).sort();
  const layer = new Map<string, number>();
  const visited = new Set<string>();
  while (queue.length) {
    const id = queue.shift()!;
    if (visited.has(id)) continue;
    visited.add(id);
    const base = layer.get(id) ?? 0;
    for (const target of [...(outgoing.get(id) ?? [])].sort()) {
      layer.set(target, Math.max(layer.get(target) ?? 0, base + 1));
      incoming.set(target, (incoming.get(target) ?? 1) - 1);
      if (incoming.get(target) === 0) queue.push(target);
    }
    queue.sort();
  }
  // Cycle部分は安定したID順で後段layerへ置き、無限探索しない。
  let cycleLayer = Math.max(0, ...layer.values()) + 1;
  for (const id of [...ids].sort()) if (!visited.has(id)) layer.set(id, cycleLayer++);
  const rows = new Map<number, string[]>();
  for (const id of [...ids].sort()) {
    const key = layer.get(id) ?? 0;
    (rows.get(key) ?? rows.set(key, []).get(key)!).push(id);
  }
  const result: LayoutResult[] = [];
  for (const [column, row] of [...rows.entries()].sort((a, b) => a[0] - b[0])) {
    row.forEach((id, index) => result.push({ id, x: 80 + column * 260, y: 80 + index * 140 }));
  }
  return result;
}

export function groupPosition(group: WorkflowGroup, nodes: Node[]): { x: number; y: number } {
  const members = nodes.filter((node) => group.node_ids.includes(node.id));
  if (!members.length) return { x: 80, y: 80 };
  return {
    x: members.reduce((sum, node) => sum + node.position.x, 0) / members.length,
    y: members.reduce((sum, node) => sum + node.position.y, 0) / members.length,
  };
}

/** 折りたたみgroupのmemberをsummary nodeへ射影する。canonical graphは変更しない。 */
export function collapseGraph(nodes: Node[], edges: Edge[], groups: WorkflowGroup[]): { nodes: Node[]; edges: Edge[] } {
  const collapsed = groups.filter((group) => group.collapsed && group.node_ids.length > 0);
  if (!collapsed.length) return { nodes, edges };
  const owner = new Map<string, WorkflowGroup>();
  for (const group of collapsed) for (const id of group.node_ids) if (!owner.has(id)) owner.set(id, group);
  const visible = nodes.filter((node) => !owner.has(node.id));
  for (const group of collapsed) {
    visible.push({
      id: `__group__${group.id}`,
      type: "cdGroup",
      position: groupPosition(group, nodes),
      data: { group },
      selectable: true,
    });
  }
  const mapped: Edge[] = [];
  const seen = new Set<string>();
  for (const edge of edges) {
    const source = owner.has(edge.source) ? `__group__${owner.get(edge.source)!.id}` : edge.source;
    const target = owner.has(edge.target) ? `__group__${owner.get(edge.target)!.id}` : edge.target;
    if (source === target) continue;
    const key = `${source}\0${target}\0${edge.sourceHandle ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    mapped.push({ ...edge, id: `collapsed-${mapped.length}-${edge.id}`, source, target });
  }
  return { nodes: visible, edges: mapped };
}
