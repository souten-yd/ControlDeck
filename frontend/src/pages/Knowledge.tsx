/** Knowledge（RAG）管理ページ。コレクション/ドキュメントの登録・削除・検索テスト・設定。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../components/ui";
import { FilePicker } from "../components/FilePicker";
import { IconFolder, IconPlus, IconSearch, IconTrash } from "../components/icons";

interface Collection {
  collection: string;
  chunks: number;
  documents: number;
  strategy: string;
  search_mode: string;
  embed_model: string;
  description: string;
}

interface RagConfig {
  embed_base_url: string;
  embed_model: string;
  strategy: string;
  size: number;
  overlap: number;
  parent_mode: string;
  parent_size: number;
  search_mode: string;
  hybrid_weight: number;
  description: string;
}

interface Defaults {
  config: RagConfig;
  strategies: { value: string; label: string }[];
  search_modes: string[];
}

const MODE_LABEL: Record<string, string> = { vector: "ベクトル", fulltext: "全文", hybrid: "ハイブリッド" };

export default function KnowledgePage() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [creating, setCreating] = useState(false);
  const [openName, setOpenName] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const { data: cols, isLoading } = useQuery({
    queryKey: ["knowledge"],
    queryFn: () => api<Collection[]>("/knowledge/collections"),
  });
  const del = useMutation({
    mutationFn: (name: string) => api(`/knowledge/collections/${name}`, { method: "DELETE" }),
    onSuccess: () => {
      show("削除しました");
      setDeleting(null);
      qc.invalidateQueries({ queryKey: ["knowledge"] });
    },
  });

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Knowledge</h1>
        {can("workflows.edit") && (
          <button onClick={() => setCreating(true)} className="flex items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700">
            <IconPlus /> コレクション作成
          </button>
        )}
      </div>
      <p className="mb-4 text-xs text-zinc-400">
        RAG 用ナレッジベース。文書を取り込み、チャンク戦略・検索方式を設定して、ワークフローの RAG 検索ノードから利用できます。
      </p>

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : !cols || cols.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-400">コレクションがありません。作成して文書を取り込みましょう。</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {cols.map((c) => (
            <li key={c.collection} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex items-center gap-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent-600/15 text-accent-600 dark:text-accent-400">📚</span>
                <button onClick={() => setOpenName(c.collection)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-sm font-semibold">{c.collection}</p>
                  <p className="num truncate text-xs text-zinc-400">
                    {c.documents} 文書 · {c.chunks} チャンク · {c.strategy} · 検索 {MODE_LABEL[c.search_mode] ?? c.search_mode}
                  </p>
                </button>
                {can("workflows.edit") && (
                  <button onClick={() => setDeleting(c.collection)} aria-label="削除" className="rounded-lg p-2 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40">
                    <IconTrash />
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {creating && <CreateSheet onClose={() => setCreating(false)} onDone={() => qc.invalidateQueries({ queryKey: ["knowledge"] })} />}
      {openName && <CollectionSheet name={openName} onClose={() => setOpenName(null)} />}
      {deleting && (
        <ConfirmDialog
          title={`「${deleting}」を削除しますか？`}
          message="コレクションと取り込んだ全文書が削除されます。取り消せません。"
          confirmLabel="削除する"
          busy={del.isPending}
          onConfirm={() => del.mutate(deleting)}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

/** チャンク戦略・検索方式などの共通フォーム */
function ConfigForm({ cfg, defaults, onChange }: { cfg: RagConfig; defaults: Defaults; onChange: (patch: Partial<RagConfig>) => void }) {
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  // Model画面（Embed/Rerankerタブ）で管理しているモデルから選ぶ（手動入力なし）。
  // 現在値が管理モデルに無い場合は推奨（先頭=BGE-M3等のllama instance優先）を自動選択する。
  const { data: embedOptions } = useQuery({
    queryKey: ["embedding-endpoints"],
    queryFn: () => api<{ endpoints: { label: string; base_url: string; model: string }[] }>("/models/embedding-endpoints"),
    staleTime: 30_000,
  });
  const endpoints = embedOptions?.endpoints ?? [];
  const selectedManaged = endpoints.find(
    (ep) => ep.base_url === cfg.embed_base_url && ep.model === cfg.embed_model,
  );
  useEffect(() => {
    if (!selectedManaged && endpoints.length > 0) {
      onChange({ embed_base_url: endpoints[0].base_url, embed_model: endpoints[0].model });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedOptions]);
  return (
    <div className="space-y-3">
      <L label="埋め込みモデル（Model画面で管理・推奨を自動選択）">
        {endpoints.length > 0 ? (
          <select
            value={selectedManaged ? `${selectedManaged.base_url}|${selectedManaged.model}` : ""}
            onChange={(e) => {
              const [base, model] = e.target.value.split("|");
              onChange({ embed_base_url: base, embed_model: model });
            }}
            className={input}
          >
            {endpoints.map((ep) => (
              <option key={`${ep.base_url}|${ep.model}`} value={`${ep.base_url}|${ep.model}`}>{ep.label}</option>
            ))}
          </select>
        ) : (
          <p className="rounded-xl border border-dashed border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
            埋め込みモデルが未導入です。Model画面の「Embed / Reranker」タブから BGE-M3 を導入してください。
          </p>
        )}
      </L>
      <L label="チャンク戦略">
        <select value={cfg.strategy} onChange={(e) => onChange({ strategy: e.target.value })} className={input}>
          {defaults.strategies.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </L>
      <div className="grid grid-cols-2 gap-3">
        <L label={cfg.strategy === "parent_child" ? "子チャンク文字数" : "チャンク文字数"}>
          <input type="number" value={cfg.size} onChange={(e) => onChange({ size: Number(e.target.value) })} className={input} />
        </L>
        <L label="オーバーラップ">
          <input type="number" value={cfg.overlap} onChange={(e) => onChange({ overlap: Number(e.target.value) })} className={input} />
        </L>
      </div>
      {cfg.strategy === "parent_child" && (
        <div className="grid grid-cols-2 gap-3">
          <L label="親モード">
            <select value={cfg.parent_mode} onChange={(e) => onChange({ parent_mode: e.target.value })} className={input}>
              <option value="paragraph">段落</option>
              <option value="full_doc">文書全体</option>
            </select>
          </L>
          <L label="親チャンク文字数">
            <input type="number" value={cfg.parent_size} onChange={(e) => onChange({ parent_size: Number(e.target.value) })} className={input} disabled={cfg.parent_mode === "full_doc"} />
          </L>
        </div>
      )}
      <L label="検索方式">
        <select value={cfg.search_mode} onChange={(e) => onChange({ search_mode: e.target.value })} className={input}>
          <option value="hybrid">ハイブリッド（ベクトル+全文・推奨）</option>
          <option value="vector">ベクトルのみ</option>
          <option value="fulltext">全文（キーワード）のみ</option>
        </select>
      </L>
      {cfg.search_mode === "hybrid" && (
        <L label={`ハイブリッド重み（${cfg.hybrid_weight} = 全文↔ベクトル）`}>
          <input type="range" min={0} max={1} step={0.1} value={cfg.hybrid_weight} onChange={(e) => onChange({ hybrid_weight: Number(e.target.value) })} className="w-full" />
        </L>
      )}
    </div>
  );
}

function CreateSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const { data: defaults } = useQuery({ queryKey: ["knowledge-defaults"], queryFn: () => api<Defaults>("/knowledge/defaults") });
  const [name, setName] = useState("");
  const [cfg, setCfg] = useState<RagConfig | null>(null);
  const eff = cfg ?? defaults?.config ?? null;
  const create = useMutation({
    mutationFn: () => api("/knowledge/collections", { method: "POST", json: { name: name.trim(), config: eff } }),
    onSuccess: () => {
      show("作成しました");
      onDone();
      onClose();
    },
    onError: (e) => show(e instanceof Error ? e.message : "作成に失敗しました", "error"),
  });
  if (!defaults || !eff) return null;
  return (
    <BottomSheet title="コレクションを作成" onClose={onClose} wide>
      <div className="space-y-3">
        <L label="名前（英数・ハイフン・アンダースコア）">
          <input value={name} onChange={(e) => setName(e.target.value.replace(/[^A-Za-z0-9_-]/g, ""))} placeholder="my-docs" className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        </L>
        <ConfigForm cfg={eff} defaults={defaults} onChange={(p) => setCfg({ ...eff, ...p })} />
        <button onClick={() => create.mutate()} disabled={!name.trim() || create.isPending} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          {create.isPending ? "作成中..." : "作成"}
        </button>
      </div>
    </BottomSheet>
  );
}

interface Doc { id: number; source: string; added_at: number; chunk_count: number; strategy: string }

function CollectionSheet({ name, onClose }: { name: string; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [tab, setTab] = useState<"docs" | "add" | "search" | "graph" | "settings">("docs");
  const { data, isLoading } = useQuery({
    queryKey: ["knowledge", name],
    queryFn: () => api<{ collection: string; config: RagConfig; documents: Doc[] }>(`/knowledge/collections/${name}`),
  });
  const { data: defaults } = useQuery({ queryKey: ["knowledge-defaults"], queryFn: () => api<Defaults>("/knowledge/defaults") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["knowledge", name] });
  const delDoc = useMutation({
    mutationFn: (id: number) => api(`/knowledge/collections/${name}/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => { show("文書を削除しました"); refresh(); qc.invalidateQueries({ queryKey: ["knowledge"] }); },
  });

  const tabs: [typeof tab, string][] = [["docs", "文書"], ["add", "取り込み"], ["search", "検索テスト"], ["graph", "グラフ"], ["settings", "設定"]];

  return (
    <BottomSheet title={name} onClose={onClose} wide>
      <div className="mb-3 flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
        {tabs.map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)} className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${tab === t ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>
            {label}
          </button>
        ))}
      </div>

      {isLoading || !data ? (
        <Skeleton className="h-24" />
      ) : tab === "docs" ? (
        data.documents.length === 0 ? (
          <p className="py-6 text-center text-sm text-zinc-400">文書がありません。「取り込み」から追加してください。</p>
        ) : (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {data.documents.map((d) => (
              <li key={d.id} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{d.source}</p>
                  <p className="num text-xs text-zinc-400">{d.chunk_count} チャンク · {d.strategy} · {new Date(d.added_at * 1000).toLocaleString("ja-JP")}</p>
                </div>
                {can("workflows.edit") && (
                  <button onClick={() => delDoc.mutate(d.id)} aria-label="削除" className="rounded-lg p-2 text-zinc-400 hover:text-red-600"><IconTrash /></button>
                )}
              </li>
            ))}
          </ul>
        )
      ) : tab === "add" ? (
        <AddDocForm name={name} onDone={() => { refresh(); qc.invalidateQueries({ queryKey: ["knowledge"] }); }} />
      ) : tab === "search" ? (
        <SearchForm name={name} defaultMode={data.config.search_mode} />
      ) : tab === "graph" ? (
        <GraphTab name={name} />
      ) : defaults ? (
        <SettingsForm name={name} config={data.config} defaults={defaults} onDone={refresh} />
      ) : null}
    </BottomSheet>
  );
}

function AddDocForm({ name, onDone }: { name: string; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [src, setSrc] = useState<"text" | "url" | "file">("text");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [source, setSource] = useState("");
  const [pick, setPick] = useState(false);
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  const [jobId, setJobId] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/knowledge/collections/${name}/ingest-jobs`, { method: "POST", json: { source, text: src === "text" ? text : "", url: src === "url" ? url : "", path: src === "file" ? path : "" } }),
    onSuccess: ({ job_id }) => setJobId(job_id),
    onError: (e) => show(e instanceof Error ? e.message : "取り込み開始に失敗しました", "error"),
  });
  return (
    <div className="space-y-3">
      <div className="flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
        {(["text", "url", "file"] as const).map((s) => (
          <button key={s} onClick={() => setSrc(s)} className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${src === s ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>
            {s === "text" ? "テキスト" : s === "url" ? "URL" : "ファイル"}
          </button>
        ))}
      </div>
      {src === "text" && <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="取り込むテキスト" className={input} />}
      {src === "url" && <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/doc" className={`${input} font-mono text-xs`} />}
      {src === "file" && (
        <div className="flex gap-1.5">
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/file.md" className={`${input} min-w-0 flex-1 font-mono text-xs`} />
          <button onClick={() => setPick(true)} className="shrink-0 rounded-xl border border-zinc-300 px-3 dark:border-zinc-700"><IconFolder /></button>
        </div>
      )}
      <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="出典名（任意）" className={input} />
      <button onClick={() => add.mutate()} disabled={add.isPending || jobId !== null || (src === "text" ? !text.trim() : src === "url" ? !url.trim() : !path.trim())} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
        {jobId !== null ? "取り込み中...（サーバー側で継続）" : "取り込む"}
      </button>
      {jobId && (
        <RagJobProgress
          jobId={jobId}
          onFinished={(job) => {
            setJobId(null);
            if (job.status === "succeeded") {
              show(`${(job.result as { added_chunks?: number } | undefined)?.added_chunks ?? "?"} チャンクを取り込みました`);
              setText(""); setUrl(""); setPath("");
              onDone();
            } else {
              show(job.error || "取り込みに失敗しました", "error");
            }
          }}
        />
      )}
      {pick && <FilePicker mode="file" title="ファイルを選択" onSelect={(p) => { setPath(p); setPick(false); }} onClose={() => setPick(false)} />}
    </div>
  );
}

function GraphTab({ name }: { name: string }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  // 抽出LLMはModel設定の稼働API（選択中ランタイム優先）から選ぶ
  const { data: endpoints } = useQuery({
    queryKey: ["llm-endpoints"],
    queryFn: () => api<{ base_url: string; models: string[]; selected?: boolean }[]>("/workflows/llm-endpoints"),
    staleTime: 60_000,
  });
  const llmOptions = (endpoints ?? []).flatMap((ep) =>
    ep.models.map((m) => ({ base: ep.base_url, model: m, selected: !!ep.selected })),
  );
  const [choice, setChoice] = useState("");
  useEffect(() => {
    if (!choice && llmOptions.length > 0) {
      const preferred = llmOptions.find((o) => o.selected) ?? llmOptions[0];
      setChoice(`${preferred.base}|${preferred.model}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoints]);
  const [llmBase, llmModel] = choice.split("|");
  const { data: stats } = useQuery({
    queryKey: ["knowledge-graph", name],
    queryFn: () => api<{ triples: number; entities: number; sample: { s: string; p: string; o: string }[] }>(`/knowledge/collections/${name}/graph`),
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const build = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/knowledge/collections/${name}/graph-jobs`, { method: "POST", json: { base_url: llmBase, model: llmModel } }),
    onSuccess: ({ job_id }) => setJobId(job_id),
    onError: (e) => show(e instanceof Error ? e.message : "構築開始に失敗", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <div className="space-y-3">
      <p className="rounded-lg bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-800/60">
        GraphRAG: LLM で文書からエンティティと関係を抽出し知識グラフを構築します。検索時に「グラフ拡張」を選ぶと関連事実が文脈に加わります。
      </p>
      <L label="抽出に使う LLM（Model設定の稼働APIから選択・停止中は自動起動）">
        <select value={choice} onChange={(e) => setChoice(e.target.value)} className={input}>
          {llmOptions.length === 0 && <option value="">稼働中のLLMがありません</option>}
          {llmOptions.map((o) => (
            <option key={`${o.base}|${o.model}`} value={`${o.base}|${o.model}`}>{o.model} — {o.base}</option>
          ))}
        </select>
      </L>
      <button onClick={() => build.mutate()} disabled={build.isPending || jobId !== null || !choice} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
        {jobId !== null ? "構築中...（サーバー側で継続）" : "グラフを構築 / 再構築"}
      </button>
      {jobId && (
        <RagJobProgress
          jobId={jobId}
          onFinished={(job) => {
            setJobId(null);
            if (job.status === "succeeded") {
              const r = job.result as { triples?: number; entities?: number } | undefined;
              show(`グラフ構築: ${r?.triples ?? "?"} 事実 / ${r?.entities ?? "?"} エンティティ`);
              qc.invalidateQueries({ queryKey: ["knowledge-graph", name] });
            } else {
              show(job.error || "構築に失敗しました", "error");
            }
          }}
        />
      )}
      {stats && (
        <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
          <p className="num mb-2 text-xs text-zinc-500">{stats.triples} 事実 · {stats.entities} エンティティ</p>
          {stats.sample.length === 0 ? (
            <p className="text-xs text-zinc-400">まだグラフがありません。上のボタンで構築してください。</p>
          ) : (
            <ul className="max-h-56 space-y-1 overflow-y-auto">
              {stats.sample.map((t, i) => (
                <li key={i} className="text-xs text-zinc-600 dark:text-zinc-300">
                  <span className="font-medium">{t.s}</span> <span className="text-zinc-400">—{t.p}→</span> <span className="font-medium">{t.o}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function SearchForm({ name, defaultMode }: { name: string; defaultMode: string }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState(defaultMode);
  const search = useMutation({
    mutationFn: () => api<{ matches: { score: number; text: string; context: string }[]; mode: string }>(`/knowledge/collections/${name}/search`, { method: "POST", json: { question: q, top_k: 5, mode } }),
  });
  const input = "rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && q.trim() && search.mutate()} placeholder="検索クエリ" className={`${input} min-w-0 flex-1`} />
        <select value={mode} onChange={(e) => setMode(e.target.value)} className={input}>
          <option value="hybrid">ハイブリッド</option>
          <option value="vector">ベクトル</option>
          <option value="fulltext">全文</option>
          <option value="graph">グラフ拡張</option>
        </select>
        <button onClick={() => search.mutate()} disabled={!q.trim() || search.isPending} className="shrink-0 rounded-xl bg-accent-600 px-3 text-white disabled:opacity-40"><IconSearch /></button>
      </div>
      {search.data && (
        <div className="space-y-2">
          {search.data.matches.length === 0 ? (
            <p className="text-sm text-zinc-400">一致なし</p>
          ) : (
            search.data.matches.map((m, i) => (
              <div key={i} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
                <p className="mb-1 num text-xs text-accent-600 dark:text-accent-400">スコア {m.score}</p>
                <p className="whitespace-pre-wrap text-xs text-zinc-600 dark:text-zinc-300">{m.context.slice(0, 400)}{m.context.length > 400 ? "…" : ""}</p>
              </div>
            ))
          )}
        </div>
      )}
      {search.isError && <p className="text-xs text-red-500">{search.error instanceof Error ? search.error.message : "検索失敗"}</p>}
    </div>
  );
}

function SettingsForm({ name, config, defaults, onDone }: { name: string; config: RagConfig; defaults: Defaults; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [cfg, setCfg] = useState<RagConfig>(config);
  const save = useMutation({
    mutationFn: () => api(`/knowledge/collections/${name}`, { method: "PATCH", json: { config: cfg } }),
    onSuccess: () => { show("設定を保存しました"); onDone(); },
    onError: (e) => show(e instanceof Error ? e.message : "保存に失敗しました", "error"),
  });
  return (
    <div className="space-y-3">
      <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
        戦略・埋め込みモデルの変更は、以後に取り込む文書に適用されます（既存チャンクは再取り込みで反映）。
      </p>
      <ConfigForm cfg={cfg} defaults={defaults} onChange={(p) => setCfg({ ...cfg, ...p })} />
      <button onClick={() => save.mutate()} disabled={save.isPending} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
        {save.isPending ? "保存中..." : "設定を保存"}
      </button>
    </div>
  );
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>
      {children}
    </label>
  );
}


/** RAGジョブ（取り込み/グラフ構築）の進捗表示。サーバー側で継続実行される。 */
function RagJobProgress({ jobId, onFinished }: {
  jobId: string;
  onFinished: (job: { status: string; error: string; result?: Record<string, unknown> }) => void;
}) {
  const { data: job } = useQuery({
    queryKey: ["rag-job", jobId],
    queryFn: () => api<{ status: string; error: string; result?: Record<string, unknown>; progress?: { status?: string; completed?: number | null; total?: number | null } }>(`/jobs/${jobId}`),
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.status) ? false : 1200),
  });
  const status = job?.status;
  useEffect(() => {
    if (job && status && !["queued", "running"].includes(status)) onFinished(job);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);
  if (!job) return null;
  const pct = job.progress?.total ? Math.round(((job.progress.completed ?? 0) / job.progress.total) * 100) : null;
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <p className="truncate text-xs text-zinc-500">
        {job.status === "queued" ? "開始待ち" : job.progress?.status || "処理中..."}
        {pct !== null && ` · ${pct}%`}
      </p>
      {pct !== null && (
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div className="h-full rounded-full bg-accent-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
      )}
      <p className="mt-1 text-[10px] text-zinc-400">サーバー側で実行中 — ブラウザを閉じても継続します</p>
    </div>
  );
}
