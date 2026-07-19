/** サンプルブック — サンプルワークフロー集 + ノードリファレンス。
 *
 * サンプルを選ぶと「コピーして使う」でメインのワークフロー一覧へ登録し、
 * そのままエディタで開いてベースとして開発できる。
 */
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { IconX } from "../../components/icons";
import { CATEGORY_ORDER, NODE_DOCS, NODE_TYPES } from "./nodeTypes";

interface Sample {
  id: string;
  title: string;
  icon: string;
  category: string;
  desc: string;
  usage: string;
  node_count: number;
  node_types: string[];
  definition: { nodes: { id: string; type: string; name: string }[]; edges: unknown[] };
}

const SAMPLE_CATEGORIES = ["すべて", "入門", "AI・RAG", "情報収集", "運用自動化"];

export default function SampleBook({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<"samples" | "nodes">("samples");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-[2px] sm:items-center"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label="サンプルブック"
        className="flex h-[92dvh] w-full max-w-[100dvw] flex-col rounded-t-2xl bg-white shadow-xl dark:bg-zinc-900 sm:h-[85dvh] sm:w-[880px] sm:rounded-2xl"
      >
        <div className="flex items-center gap-2 border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800 sm:px-5 sm:py-3">
          <h2 className="shrink-0 text-base font-semibold">📖<span className="hidden sm:inline"> サンプルブック</span></h2>
          <div className="flex min-w-0 rounded-xl bg-zinc-100 p-0.5 text-[13px] dark:bg-zinc-800 sm:ml-2 sm:text-sm">
            {(
              [
                ["samples", "サンプル"],
                ["nodes", "ノード"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`whitespace-nowrap rounded-[10px] px-3 py-1.5 font-medium transition ${
                  tab === key
                    ? "bg-white shadow-sm dark:bg-zinc-700"
                    : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                }`}
              >
                {label}
                {key === "nodes" && <span className="hidden sm:inline">リファレンス</span>}
              </button>
            ))}
          </div>
          <button
            onClick={onClose}
            aria-label="閉じる"
            className="ml-auto shrink-0 rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <IconX />
          </button>
        </div>
        {tab === "samples" ? <SamplesTab onClose={onClose} /> : <NodeReferenceTab />}
      </div>
    </div>,
    document.body,
  );
}

// ---- サンプル一覧 + 詳細 ----

function SamplesTab({ onClose }: { onClose: () => void }) {
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);
  const navigate = useNavigate();
  const [category, setCategory] = useState("すべて");
  const [selected, setSelected] = useState<Sample | null>(null);

  const { data: samples, isLoading } = useQuery({
    queryKey: ["workflow-samples"],
    queryFn: () => api<Sample[]>("/workflows/samples"),
    staleTime: 10 * 60 * 1000,
  });

  // アシスタントで選択済みの LLM（localStorage）があれば、サンプル既定モデルを差し替えて登録する
  const install = useMutation({
    mutationFn: (s: Sample) => {
      let llm = { baseUrl: "", model: "" };
      try {
        const saved = JSON.parse(localStorage.getItem("cd-assistant-settings") || "{}");
        llm = { baseUrl: saved.baseUrl || "", model: saved.model || "" };
      } catch {
        /* 設定なし */
      }
      return api<{ id: number }>(`/workflows/samples/${s.id}/install`, {
        method: "POST",
        json: { base_url: llm.baseUrl, model: llm.model },
      });
    },
    onSuccess: (wf, s) => {
      show(`「${s.title}」をコピーしました。編集して自分用にカスタマイズできます`);
      onClose();
      navigate(`/workflows/${wf.id}`);
    },
    onError: (e) => show(e instanceof Error ? e.message : "コピーに失敗しました", "error"),
  });

  const filtered = (samples ?? []).filter((s) => category === "すべて" || s.category === category);

  const list = (
    <div className="flex h-full flex-col">
      <div className="flex gap-1.5 overflow-x-auto px-4 py-3 sm:px-5">
        {SAMPLE_CATEGORIES.map((c) => (
          <button
            key={c}
            onClick={() => setCategory(c)}
            className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium transition ${
              category === c
                ? "bg-accent-600 text-white"
                : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto px-4 pb-4 sm:px-5">
        {isLoading && <p className="py-8 text-center text-sm text-zinc-400">読み込み中...</p>}
        {filtered.map((s) => (
          <button
            key={s.id}
            onClick={() => setSelected(s)}
            className={`block w-full rounded-2xl border p-3.5 text-left transition ${
              selected?.id === s.id
                ? "border-accent-500 bg-accent-50/50 dark:border-accent-500 dark:bg-accent-600/10"
                : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700"
            }`}
          >
            <div className="flex items-center gap-2.5">
              <span className="text-xl">{s.icon}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{s.title}</p>
                <p className="mt-0.5 line-clamp-2 text-xs text-zinc-500 dark:text-zinc-400">{s.desc}</p>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {s.node_types.map((t, i) => {
                const def = NODE_TYPES[t];
                return (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded-md bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
                  >
                    <span>{def?.icon ?? "▢"}</span>
                    {def?.label ?? t}
                  </span>
                );
              })}
            </div>
          </button>
        ))}
      </div>
    </div>
  );

  const detail = selected && (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <button
          onClick={() => setSelected(null)}
          className="mb-3 text-xs text-accent-600 dark:text-accent-400 md:hidden"
        >
          ← サンプル一覧へ戻る
        </button>
        <div className="flex items-center gap-3">
          <span className="text-3xl">{selected.icon}</span>
          <div>
            <p className="text-[11px] font-medium text-accent-600 dark:text-accent-400">{selected.category}</p>
            <h3 className="text-base font-semibold">{selected.title}</h3>
          </div>
        </div>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">{selected.desc}</p>

        {/* ノード構成（フロー順） */}
        <p className="mt-4 mb-1.5 text-xs font-semibold text-zinc-500">ノード構成（{selected.node_count} 個）</p>
        <div className="flex flex-wrap items-center gap-1">
          {selected.definition.nodes.map((n, i) => {
            const def = NODE_TYPES[n.type];
            return (
              <span key={n.id} className="flex items-center gap-1">
                {i > 0 && <span className="text-zinc-300 dark:text-zinc-600">→</span>}
                <span
                  className="inline-flex items-center gap-1 rounded-lg border border-zinc-200 px-2 py-1 text-xs dark:border-zinc-700"
                  style={{ borderLeftColor: def?.color, borderLeftWidth: 3 }}
                >
                  <span>{def?.icon}</span>
                  {n.name || def?.label || n.type}
                </span>
              </span>
            );
          })}
        </div>

        <p className="mt-4 mb-1.5 text-xs font-semibold text-zinc-500">使い方</p>
        <div className="whitespace-pre-wrap rounded-xl bg-zinc-50 p-3.5 text-[13px] leading-relaxed text-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-300">
          {selected.usage}
        </div>
      </div>
      {can("workflows.edit") && (
        <div className="border-t border-zinc-200 px-4 py-3 dark:border-zinc-800 sm:px-6">
          <button
            onClick={() => install.mutate(selected)}
            disabled={install.isPending}
            className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-60"
          >
            {install.isPending ? "コピー中..." : "このサンプルをコピーして使う"}
          </button>
          <p className="mt-1.5 text-center text-[11px] text-zinc-400">
            ワークフロー一覧にコピーが登録され、エディタが開きます（サンプル自体は変更されません）
          </p>
        </div>
      )}
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1">
      <div className={`min-h-0 flex-1 md:max-w-[360px] md:border-r md:border-zinc-200 md:dark:border-zinc-800 ${selected ? "hidden md:block" : ""}`}>
        {list}
      </div>
      <div className={`min-h-0 flex-1 ${selected ? "" : "hidden md:grid md:place-items-center"}`}>
        {detail ?? (
          <p className="p-8 text-center text-sm text-zinc-400">
            左のリストからサンプルを選ぶと
            <br />
            詳しい使い方が表示されます
          </p>
        )}
      </div>
    </div>
  );
}

// ---- ノードリファレンス ----

function NodeReferenceTab() {
  const [selectedType, setSelectedType] = useState<string | null>(null);

  const grouped = useMemo(() => {
    const g = new Map<string, [string, (typeof NODE_TYPES)[string]][]>();
    for (const cat of CATEGORY_ORDER) g.set(cat, []);
    for (const [type, def] of Object.entries(NODE_TYPES)) {
      if (type === "trigger") continue;
      (g.get(def.category) ?? g.set(def.category, []).get(def.category)!).push([type, def]);
    }
    g.set("トリガー", [["trigger", NODE_TYPES.trigger]]);
    return [
      ["トリガー", g.get("トリガー")!] as const,
      ...CATEGORY_ORDER.filter((c) => (g.get(c) ?? []).length > 0).map((c) => [c, g.get(c)!] as const),
    ];
  }, []);

  const def = selectedType ? NODE_TYPES[selectedType] : null;

  const list = (
    <div className="h-full overflow-y-auto px-4 py-3 sm:px-5">
      {grouped.map(([cat, items]) => (
        <div key={cat} className="mb-3">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">{cat}</p>
          <div className="space-y-1">
            {items.map(([type, d]) => (
              <button
                key={type}
                onClick={() => setSelectedType(type)}
                className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-sm transition ${
                  selectedType === type
                    ? "bg-accent-50 dark:bg-accent-600/15"
                    : "hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
                }`}
              >
                <span
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-sm text-white"
                  style={{ backgroundColor: d.color }}
                >
                  {d.icon}
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">{d.label}</span>
                  <span className="block truncate text-[11px] text-zinc-400">{type}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );

  const detail = def && selectedType && (
    <div className="h-full overflow-y-auto px-4 py-4 sm:px-6">
      <button
        onClick={() => setSelectedType(null)}
        className="mb-3 text-xs text-accent-600 dark:text-accent-400 md:hidden"
      >
        ← ノード一覧へ戻る
      </button>
      <div className="flex items-center gap-3">
        <span
          className="grid h-10 w-10 place-items-center rounded-xl text-lg text-white"
          style={{ backgroundColor: def.color }}
        >
          {def.icon}
        </span>
        <div>
          <h3 className="text-base font-semibold">{def.label}</h3>
          <p className="text-[11px] text-zinc-400">
            {selectedType} · {def.category}
          </p>
        </div>
      </div>
      {def.desc && <p className="mt-2.5 text-sm text-zinc-600 dark:text-zinc-300">{def.desc}</p>}

      {NODE_DOCS[selectedType] && (
        <div className="mt-3 whitespace-pre-wrap rounded-xl bg-zinc-50 p-3.5 text-[13px] leading-relaxed text-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-300">
          {NODE_DOCS[selectedType]}
        </div>
      )}

      {def.fields.length > 0 && (
        <>
          <p className="mt-4 mb-1.5 text-xs font-semibold text-zinc-500">設定項目</p>
          <ul className="space-y-1 text-[13px]">
            {def.fields.map((f) => (
              <li key={f.key} className="flex gap-2">
                <code className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  {f.key}
                </code>
                <span className="text-zinc-600 dark:text-zinc-300">
                  {f.label}
                  {f.hint && <span className="text-zinc-400">（{f.hint}）</span>}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {def.outputs && def.outputs.length > 0 && (
        <>
          <p className="mt-4 mb-1.5 text-xs font-semibold text-zinc-500">出力（変数として参照可能）</p>
          <ul className="space-y-1 text-[13px]">
            {def.outputs.map((o) => (
              <li key={o.key} className="flex gap-2">
                <code className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  {"{{ID." + o.key + "}}"}
                </code>
                <span className="text-zinc-600 dark:text-zinc-300">{o.label}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {(def.branches || def.loop) && (
        <p className="mt-4 rounded-xl bg-amber-50 p-3 text-[13px] text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
          {def.branches
            ? "このノードは true / false の 2 方向へ分岐します。エッジを引くハンドルで行き先が決まります。"
            : "このノードは body（繰り返し）/ done（完了後）の 2 方向へ分岐します。"}
        </p>
      )}
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1">
      <div className={`min-h-0 flex-1 md:max-w-[300px] md:border-r md:border-zinc-200 md:dark:border-zinc-800 ${selectedType ? "hidden md:block" : ""}`}>
        {list}
      </div>
      <div className={`min-h-0 flex-1 ${selectedType ? "" : "hidden md:grid md:place-items-center"}`}>
        {detail ?? (
          <p className="p-8 text-center text-sm text-zinc-400">
            ノードを選ぶと詳しい使い方と
            <br />
            設定・出力の説明が表示されます
          </p>
        )}
      </div>
    </div>
  );
}
