import { useMemo, useState } from "react";
import { BottomSheet } from "../../components/ui";
import type { WorkflowGroup } from "./largeFlow";

export interface OutlineNode {
  id: string;
  name: string;
  type: string;
  disabled?: boolean;
}

export function WorkflowOutline({
  nodes, groups, readOnly, onFocus, onToggleGroup, onRenameGroup, onUngroup, onClose,
}: {
  nodes: OutlineNode[];
  groups: WorkflowGroup[];
  readOnly: boolean;
  onFocus: (id: string) => void;
  onToggleGroup: (id: string) => void;
  onRenameGroup: (id: string, name: string) => void;
  onUngroup: (id: string) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const needle = search.trim().toLocaleLowerCase();
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const groupedIds = useMemo(() => new Set(groups.flatMap((group) => group.node_ids)), [groups]);
  const matches = (node: OutlineNode) => !needle || `${node.name} ${node.id} ${node.type}`.toLocaleLowerCase().includes(needle);
  const ungrouped = nodes.filter((node) => !groupedIds.has(node.id) && matches(node));
  return (
    <BottomSheet title="フロー内を検索・移動" onClose={onClose} wide stable>
      <div className="space-y-3">
        <label className="block text-xs font-medium text-zinc-500" htmlFor="workflow-outline-search">ノード検索</label>
        <input id="workflow-outline-search" autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名前・ID・種類" className="min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-base outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-950 sm:text-sm" />
        <p className="text-xs text-zinc-400">{nodes.length}ノード · {groups.length}グループ</p>
        {groups.map((group) => {
          const members = group.node_ids.map((id) => byId.get(id)).filter((node): node is OutlineNode => Boolean(node)).filter(matches);
          if (needle && members.length === 0 && !group.name.toLocaleLowerCase().includes(needle)) return null;
          return <section key={group.id} className="rounded-xl border border-zinc-200 bg-zinc-50/60 p-2 dark:border-zinc-700 dark:bg-zinc-800/40">
            <div className="flex items-center gap-1.5">
              <button type="button" onClick={() => onToggleGroup(group.id)} aria-label={`${group.name}を${group.collapsed ? "展開" : "折りたたむ"}`} className="grid h-11 w-11 shrink-0 place-items-center rounded-lg hover:bg-zinc-200 dark:hover:bg-zinc-700">{group.collapsed ? "▸" : "▾"}</button>
              <input value={group.name} onChange={(event) => onRenameGroup(group.id, event.target.value)} disabled={readOnly} aria-label={`${group.name}の名前`} className="min-h-11 min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 text-sm font-semibold focus:border-accent-500 focus:outline-none" />
              {!readOnly && <button type="button" onClick={() => onUngroup(group.id)} className="min-h-11 rounded-lg px-2 text-xs text-zinc-500 hover:bg-zinc-200 dark:hover:bg-zinc-700">解除</button>}
            </div>
            {!group.collapsed && <div className="ml-6 border-l border-zinc-200 pl-2 dark:border-zinc-700">{members.map((node) => <NodeRow key={node.id} node={node} onFocus={onFocus} />)}</div>}
          </section>;
        })}
        {ungrouped.map((node) => <NodeRow key={node.id} node={node} onFocus={onFocus} />)}
        {groups.length === 0 && ungrouped.length === 0 && <p className="rounded-xl border border-dashed border-zinc-300 p-4 text-center text-sm text-zinc-400 dark:border-zinc-700">一致するノードがありません</p>}
      </div>
    </BottomSheet>
  );
}

function NodeRow({ node, onFocus }: { node: OutlineNode; onFocus: (id: string) => void }) {
  return <button type="button" onClick={() => onFocus(node.id)} aria-label={`${node.name}へ移動`} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left hover:bg-accent-50 focus:outline-none focus:ring-2 focus:ring-accent-500/30 dark:hover:bg-accent-950/30">
    <span className="min-w-0 flex-1 truncate text-sm font-medium">{node.name}</span>
    {node.disabled && <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-700">無効</span>}
    <code className="shrink-0 text-[10px] text-zinc-400">{node.type}</code>
  </button>;
}
