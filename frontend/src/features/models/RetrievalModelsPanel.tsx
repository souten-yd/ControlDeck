import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { ConfirmDialog } from "../../components/ui";

/** Models の「Embed / Reranker」タブの中身。
 * 2026-11-20 利用者「Models タブで導入できるように。どのモデルを使うか選べるように」「表示がそろっていない
 * （GPU の見出しの中に 2 つ、上の 3 つは別々）。GPU 常駐のボタンも上の方には無いのでそろえて」。
 * → 埋め込み・reranker を役割ごとに並べ、CPU / GPU（ONNX）と GPU（llama.cpp）を同じカードで出す。 */

interface CpuReranker {
  id: string; name: string; installed: boolean; installing: boolean; job_id: string | null; model: string;
  model_bytes: number; device: string; runtime: string; accuracy: string; speed: string; memory: string;
  license: string; error: string; running: boolean; resident: boolean;
}

interface RetrievalState {
  cpu_rerankers: CpuReranker[];
  gpu_reranker_installed: boolean;
  selection: { chat: string; knowledge: string };
  choices: string[];
  in_use: { chat: string; knowledge: string };
  ruri_device: "cpu" | "gpu";
}

interface RolePreset {
  id: string; label: string; description: string; role: string; alias: string;
  file_exists: boolean; installed: boolean; loaded: boolean; idle_exclude: boolean; runtime_status: string;
  device: "cpu" | "gpu";
}

interface JobInfo { id: string; status: string; progress: { status?: string; completed?: number | null; total?: number | null }; error: string }

const SHORT: Record<string, string> = {
  "xsmall-reranker": "xsmall（CPU・int8・軽い）",
  ruri: "ruri（おすすめ。CPU / GPU はカードで切り替え）",
  "ruri-reranker": "ruri（CPU・int8）",
  "ruri-reranker-gpu": "ruri（GPU・fp16）",
  gpu: "Qwen3-Reranker-4B（GPU・llama.cpp）",
  none: "並べ替えない（検索の順）",
};

const USES: { key: "chat" | "knowledge"; label: string; hint: string }[] = [
  { key: "chat", label: "会話の資料", hint: "AI アシスタントの会話で資料を探すとき（おすすめ: ruri の GPU。GPU が使えないときは xsmall）" },
  { key: "knowledge", label: "Knowledge・Workflow", hint: "Knowledge の検索と Workflow の RAG（おすすめ: ruri の GPU。GPU が使えないときは CPU の ruri）" },
];

/** 1 枚のカードに出す内容（CPU / GPU の ONNX のものと、llama.cpp のプリセットで共通） */
interface DeviceToggle { value: "cpu" | "gpu"; onChange?: (device: "cpu" | "gpu") => void; note: string }

interface Card {
  key: string; name: string; device: "CPU" | "GPU"; recommended: boolean; description: string; needsLlama: boolean;
  toggle: DeviceToggle;
  installed: boolean; installing: boolean; running: boolean; resident: boolean; jobId?: string;
  onInstall: () => void; onLoad: () => void; onUnload: () => void; onUninstall: () => void; onResident: (value: boolean) => void;
}

export function RetrievalModelsPanel({ llamaInstalled }: { llamaInstalled: boolean }) {
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const editable = can("workflows.edit");
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const [acting, setActing] = useState<string | null>(null);
  const [removing, setRemoving] = useState<{ name: string; message: string; run: () => Promise<void> } | null>(null);
  const { data } = useQuery({
    queryKey: ["retrieval-models"],
    queryFn: () => api<RetrievalState>("/models/retrieval"),
    refetchInterval: Object.keys(jobs).length ? 3000 : 10000,
  });
  const { data: presets } = useQuery({
    queryKey: ["role-presets"],
    queryFn: () => api<{ presets: RolePreset[] }>("/models/llama/role-presets"),
    refetchInterval: 8000,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["retrieval-models"] });
    void qc.invalidateQueries({ queryKey: ["role-presets"] });
  };
  const run = async (key: string, action: () => Promise<unknown>, ok?: string) => {
    setActing(key);
    try { await action(); if (ok) show(ok); refresh(); }
    catch (e) { show(e instanceof Error ? e.message : "失敗しました", "error"); }
    finally { setActing(null); }
  };
  const select = (key: "chat" | "knowledge", value: string) =>
    run(`select-${key}`, () => api("/models/retrieval/selection", { method: "PUT", json: { [key]: value } }));

  if (!data) return null;

  const setRuriDevice = (device: "cpu" | "gpu") =>
    void run("ruri", () => api("/models/retrieval/ruri-device", { method: "PUT", json: { device } }),
      device === "gpu" ? "ruri を GPU（fp16）で動かします" : "ruri を CPU（int8）で動かします");
  const onnxCard = (model: CpuReranker, toggle: DeviceToggle, name = model.name): Card => ({
    key: model.id, name, device: model.device === "gpu" ? "GPU" : "CPU", toggle,
    recommended: model.id.startsWith("ruri"), needsLlama: false,
    description: `${model.accuracy}。${model.speed}。${model.memory}`,
    installed: model.installed, installing: model.installing, running: model.running, resident: model.resident,
    jobId: jobs[model.id] ?? model.job_id ?? undefined,
    onInstall: () => void run(model.id, async () => {
      const res = await api<{ job_id: string }>(`/models/retrieval/cpu/${model.id}/install-jobs`, { method: "POST", json: {} });
      setJobs((prev) => ({ ...prev, [model.id]: res.job_id }));
    }, `${model.name} の導入を始めました（サーバー側で続きます）`),
    onLoad: () => void run(model.id, () => api(`/models/retrieval/cpu/${model.id}/load`, { method: "POST", json: {} }), "ロードしました"),
    onUnload: () => void run(model.id, () => api(`/models/retrieval/cpu/${model.id}/unload`, { method: "POST", json: {} }), "アンロードしました"),
    onUninstall: () => setRemoving({
      name: model.name, message: "使わなくなります。変換済みのモデルは残すので、入れ直すときに取り直しません。",
      run: () => run(model.id, () => api(`/models/retrieval/cpu/${model.id}/uninstall`, { method: "POST", json: {} }), `${model.name} をアンインストールしました`),
    }),
    onResident: (value) => void run(model.id, () => api(`/models/retrieval/cpu/${model.id}/resident`, { method: "PUT", json: { value } })),
  });
  const presetCard = (preset: RolePreset): Card => ({
    key: preset.id, name: preset.label, device: preset.device === "cpu" ? "CPU" : "GPU",
    recommended: preset.role === "embedding", needsLlama: true,
    toggle: {
      value: preset.device,
      onChange: preset.installed ? (device) => void run(preset.id,
        () => api(`/models/retrieval/preset/${preset.id}/device`, { method: "PUT", json: { device } }),
        `${preset.label} を${device === "gpu" ? " GPU" : " CPU"} で動かします（次に使うときから）`) : undefined,
      note: preset.role === "embedding"
        ? "CPU: VRAM を使わない。検索の質問は速い（0.02 秒）が、資料の保存は GPU の約 20 倍遅い"
        : "CPU: VRAM を使わないが、並べ替えがかなり遅い",
    },
    description: preset.description,
    installed: preset.installed, installing: false, running: preset.runtime_status === "RUNNING" || preset.loaded,
    resident: preset.idle_exclude,
    onInstall: () => void run(preset.id, () => api(`/models/llama/role-presets/${preset.id}/install-jobs`, { method: "POST", json: {} }),
      `${preset.label} の導入を始めました（サーバー側で続きます）`),
    onLoad: () => void run(preset.id, () => api(`/models/providers/llama.cpp/models/${encodeURIComponent(preset.alias)}/load`, { method: "POST", json: {} }), "ロードしました"),
    onUnload: () => void run(preset.id, () => api(`/models/providers/llama.cpp/models/${encodeURIComponent(preset.alias)}/unload`, { method: "POST", json: {} }), "アンロードしました"),
    onUninstall: () => setRemoving({
      name: preset.label, message: "止めて登録を消し、モデルファイル（GGUF）も消します。もう一度使うときは導入し直します。",
      run: () => run(preset.id, () => api(`/models/llama/role-presets/${preset.id}/uninstall`, { method: "POST", json: {} }), `${preset.label} をアンインストールしました`),
    }),
    onResident: (value) => void run(preset.id, () => api(`/models/llama/instances/${encodeURIComponent(preset.alias)}`, { method: "PUT", json: { idle_exclude: value } })),
  });
  const embeddings = (presets?.presets ?? []).filter((p) => p.role === "embedding").map(presetCard);
  // ruri は CPU（int8）と GPU（fp16）を 1 枚のカードにまとめ、トグルで切り替える
  const ruri = data.cpu_rerankers.find((m) => m.id === (data.ruri_device === "gpu" ? "ruri-reranker-gpu" : "ruri-reranker"));
  const xsmall = data.cpu_rerankers.find((m) => m.id === "xsmall-reranker");
  const rerankers = [
    ...(ruri ? [onnxCard(ruri, {
      value: data.ruri_device, onChange: setRuriDevice,
      note: data.ruri_device === "gpu" ? "GPU: 速い（0.4 秒）。VRAM 約 1.3 GB" : "CPU: VRAM を使わないが遅い（8〜12 秒）",
    }, "ruri-v3-reranker-310m")] : []),
    ...(xsmall ? [onnxCard(xsmall, { value: "cpu", note: "CPU のみ（GPU が使えないときの控え）" })] : []),
    ...(presets?.presets ?? []).filter((p) => p.role === "reranker").map(presetCard),
  ];
  const available = (choice: string) => choice === "auto" || choice === "none"
    || (choice === "gpu" ? data.gpu_reranker_installed
      : data.cpu_rerankers.some((m) => (choice === "ruri" ? m.id.startsWith("ruri") : m.id === choice) && m.installed));
  const label = (choice: string) => choice === "auto" ? "自動（おすすめ）" : SHORT[choice] ?? choice;
  const jobDone = (key: string) => () => {
    setJobs((prev) => { const next = { ...prev }; delete next[key]; return next; });
    refresh();
  };

  return (
    <div className="space-y-4" data-retrieval-models>
      <div className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
        <p className="text-sm font-semibold">使う reranker</p>
        <p className="mt-0.5 text-xs text-zinc-400">検索の候補を、質問との関係の深さで並べ直すモデルです。自動は、ruri の GPU → CPU のもの → Qwen3-Reranker-4B の順に、入っているものを使います。</p>
        <div className="mt-3 space-y-3">
          {USES.map((use) => (
            <label key={use.key} className="block">
              <span className="text-xs font-medium">{use.label}</span>
              <span className="block text-[11px] text-zinc-400">{use.hint}</span>
              <select value={data.selection[use.key]} disabled={!editable} onChange={(e) => void select(use.key, e.target.value)}
                className="mt-1 w-full rounded-xl border border-zinc-200 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                {data.choices.map((choice) => (
                  <option key={choice} value={choice} disabled={!available(choice)}>
                    {label(choice)}{available(choice) ? "" : "（未導入）"}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[11px] text-zinc-500" data-in-use={use.key}>いま使っているもの: {
                data.in_use[use.key] === "gpu" && !data.gpu_reranker_installed ? "なし（reranker が未導入のため検索の順）"
                  : SHORT[data.in_use[use.key]] ?? data.in_use[use.key]}</span>
            </label>
          ))}
        </div>
      </div>
      <Section title="埋め込み" hint="資料をベクトルにして検索するモデル。資料の保存と、検索の質問に使います。">
        {embeddings.map((card) => (
          <HelperCard key={card.key} card={card} role="埋め込み" editable={editable} busy={acting === card.key}
            llamaInstalled={llamaInstalled} onJobDone={jobDone(card.key)} />
        ))}
      </Section>
      <Section title="reranker" hint="検索の候補を並べ直すモデル。上の「使う reranker」で選びます。">
        {rerankers.map((card) => (
          <HelperCard key={card.key} card={card} role="再ランク" editable={editable} busy={acting === card.key}
            llamaInstalled={llamaInstalled} onJobDone={jobDone(card.key)} />
        ))}
      </Section>
      <p className="px-0.5 text-[11px] text-zinc-400">
        GPU で動くものは、大きな LLM を起動・切り替える前に止めます（常駐でも止めます。VRAM 不足を防ぐため）。次に使うときは数秒で載り直します。
      </p>
      {removing && (
        <ConfirmDialog title={`${removing.name} をアンインストールしますか？`} message={removing.message} confirmLabel="アンインストール"
          onConfirm={() => { void removing.run(); setRemoving(null); }} onClose={() => setRemoving(null)} />
      )}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="px-0.5">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="text-[11px] text-zinc-400">{hint}</p>
      </div>
      {children}
    </section>
  );
}

function HelperCard({ card, role, editable, busy, llamaInstalled, onJobDone }: {
  card: Card; role: string; editable: boolean; busy: boolean; llamaInstalled: boolean; onJobDone: () => void;
}) {
  const { data: job } = useQuery({
    queryKey: ["job", card.jobId], enabled: Boolean(card.jobId), refetchInterval: 2000,
    queryFn: async () => {
      const value = await api<JobInfo>(`/jobs/${card.jobId}`);
      if (value.status !== "running" && value.status !== "queued") onJobDone();
      return value;
    },
  });
  const installing = card.installing || Boolean(job && (job.status === "running" || job.status === "queued"));
  const progress = job?.progress;
  const percent = progress?.total ? Math.round(((progress.completed ?? 0) / progress.total) * 100) : null;
  const button = "rounded-xl px-3 py-1.5 text-xs font-medium disabled:cursor-wait disabled:opacity-50";
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900" id={`retrieval-${card.key}`}>
      <div className="flex items-start gap-3">
        <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${card.running ? "bg-emerald-500" : card.installed ? "bg-zinc-300 dark:bg-zinc-600" : "bg-zinc-200 dark:bg-zinc-700"}`}
          title={card.running ? "稼働中" : card.installed ? "停止中" : "未導入"} />
        <div className="min-w-0 flex-1">
          <p className="break-words text-sm font-semibold">{card.name}</p>
          <p className="mt-1 flex flex-wrap gap-1">
            <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-900/50 dark:text-violet-300">{role}</span>
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${card.device === "GPU" ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300" : "bg-sky-100 text-sky-700 dark:bg-sky-900/50 dark:text-sky-300"}`}>{card.device}</span>
            {card.recommended && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">おすすめ</span>}
          </p>
          <p className="mt-1 text-xs text-zinc-400">{card.description}</p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <div role="group" aria-label="動かす所" className="inline-flex rounded-xl bg-zinc-100 p-0.5 dark:bg-zinc-800" data-device-toggle={card.key}>
          {(["cpu", "gpu"] as const).map((device) => (
            <button key={device} type="button" aria-pressed={card.toggle.value === device}
              disabled={!editable || !card.toggle.onChange || busy || card.toggle.value === device}
              onClick={() => card.toggle.onChange?.(device)}
              className={`rounded-lg px-3 py-1 text-xs font-medium ${card.toggle.value === device
                ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white" : "text-zinc-500 disabled:opacity-40"}`}>
              {device.toUpperCase()}
            </button>
          ))}
        </div>
        <span className="min-w-0 flex-1 text-[11px] text-zinc-400">{card.toggle.note}</span>
      </div>
      {editable && (
        <div className="mt-3 flex flex-wrap items-center justify-end gap-1.5">
          {card.installed ? (
            <>
              <button onClick={card.running ? card.onUnload : card.onLoad} disabled={busy}
                className={`${button} bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300`}>
                {busy ? "…" : card.running ? "アンロード" : "ロード"}
              </button>
              <button onClick={card.onUninstall} disabled={busy}
                className={`${button} text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40`}>アンインストール</button>
            </>
          ) : (
            <button onClick={card.onInstall} disabled={installing || busy || (card.needsLlama && !llamaInstalled)}
              className={`${button} bg-accent-600 px-3.5 text-white hover:bg-accent-700`}>
              {installing ? "導入中…" : "導入"}
            </button>
          )}
        </div>
      )}
      {installing && progress?.status && (
        <p className="mt-2 text-[11px] text-zinc-500">{progress.status}{percent !== null ? `（${percent}%）` : ""}</p>
      )}
      {job?.status === "failed" && <p className="mt-2 break-words text-[11px] text-red-600">導入に失敗しました: {job.error}</p>}
      {card.installed && editable && (
        <label className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-zinc-100 px-3 py-2 dark:border-zinc-800">
          <span className="text-xs">常駐（使われなくなっても止めない）
            <span className="block text-[10px] text-zinc-400">
              {card.device === "GPU" ? "検索の最初の応答が速くなる代わりに VRAM を使い続けます（大きな LLM の起動前には止めます）"
                : "検索の最初の応答が速くなる代わりに RAM を使い続けます"}
            </span>
          </span>
          <input type="checkbox" checked={card.resident} onChange={(e) => card.onResident(e.target.checked)} className="h-4 w-4 shrink-0" />
        </label>
      )}
    </div>
  );
}
