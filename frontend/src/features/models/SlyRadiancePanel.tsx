/** Sly-Radiance（SlyBase/vllm-sly-radiance。R9700 向けの vLLM）のモデル登録と個別設定。
 * Radiance と同じ節の並び: 状態 → モデル → 基本 → 起動と停止 → 思考 → 性能 → 詳細。
 * モデルは HF 形式のフォルダ。目録から取る（既存の HF 取得 Job）か、フォルダを選ぶ。 */
import { DisplayNameField } from "./DisplayNameField";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { FilePicker } from "../../components/FilePicker";
import { BottomSheet } from "../../components/ui";
import { IconFolder } from "../../components/icons";
import { JobProgress } from "../knowledge/JobProgress";
import { useToasts } from "../../stores";
import type { ModelLibrary } from "../../api/models";
import {
  CopyText, FileName, ModelStatusSummary, PresetOrCustom, SettingsGroup, SheetActions, Toggle, primaryButton, secondaryButton,
} from "./formControls";

export type SlyQuant = "" | "quark" | "nvfp4" | "paroquant";
export interface SlyRadianceSettings {
  alias: string;
  model_path: string;
  drafter_path: string;
  port: number;
  context: number;
  kv_cache: "fp8" | "kv4";
  max_num_seqs: number;
  speculative_tokens: number;
  gpu_memory_utilization: number;
  prefill_chunk: 2048 | 4096 | 8192;
  prefix_cache: boolean;
  reasoning_effort: "" | "none" | "low" | "medium" | "high";
  quantization: SlyQuant;
  engine_settings: Record<string, string>;
  on_demand_start: boolean;
  idle_exclude: boolean;
  auto_start: boolean;
  loaded?: boolean;
}
interface SlyState {
  runtime: { installed: boolean; version: string; warning: string };
  instances: SlyRadianceSettings[];
}
interface CatalogEntry {
  id: string; repo: string; name: string; kind: "model" | "drafter"; quantization: SlyQuant;
  size_gib: number | null; uncensored: boolean; supported: boolean; note: string; fit: string;
}
interface FolderInfo {
  path: string; name: string; safetensors_count: number; total_bytes: number;
  quant_method: string; quant_format: string; suggested_quantization: SlyQuant; ready: boolean;
}

const CTX_PRESETS = [32768, 65536, 131072, 262144].map((v) => ({ v, label: v.toLocaleString() }));
const QUANT_LABEL: Record<SlyQuant, string> = {
  "": "なし（MXFP4・標準）", quark: "Quark AWQ（--quantization quark）",
  nvfp4: "NVFP4（compressed-tensors＋MXFP4 経路）", paroquant: "ParoQuant",
};
const FIT_LABEL: Record<string, string> = { fits: "載る", tight: "ぎりぎり", no: "載らない", unknown: "" };
const DEFAULTS: SlyRadianceSettings = {
  alias: "qwen3.8-27b-sly", model_path: "", drafter_path: "", port: 8100, context: 131072, kv_cache: "fp8",
  max_num_seqs: 4, speculative_tokens: 7, gpu_memory_utilization: 0.98, prefill_chunk: 2048, prefix_cache: true,
  reasoning_effort: "", quantization: "", engine_settings: {},
  on_demand_start: true, idle_exclude: false, auto_start: false,
};
const gib = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
const dirname = (path: string) => path.replace(/\/[^/]*$/, "");

export function useSlyRadianceState() {
  return useQuery({
    queryKey: ["sly-radiance-instances"], refetchInterval: 5000,
    queryFn: () => api<SlyState>("/models/sly-radiance/instances"),
  });
}

const envToText = (env: Record<string, string>) => Object.entries(env).map(([k, v]) => `${k}=${v}`).join("\n");
const textToEnv = (text: string): Record<string, string> => Object.fromEntries(
  text.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const i = line.indexOf("=");
    return i < 0 ? [line, ""] : [line.slice(0, i).trim(), line.slice(i + 1).trim()];
  }));

/** 目録から取る: モデル（と先読みモデル）を選び、保存先を選んで HF から取得する。済んだらフォームへ入れる。 */
function CatalogDownload({ onModel, onDrafter }: {
  onModel: (path: string, quant: SlyQuant) => void; onDrafter: (path: string) => void;
}) {
  const show = useToasts((s) => s.show);
  const { data: catalog } = useQuery({
    queryKey: ["sly-radiance-catalog"], queryFn: () => api<{ entries: CatalogEntry[] }>("/models/sly-radiance/catalog"),
  });
  const [modelId, setModelId] = useState("swift-heretic");
  const [withDrafter, setWithDrafter] = useState(true);
  const [library, setLibrary] = useState("");
  const [libs, setLibs] = useState<ModelLibrary[]>([]);
  const [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState<Array<{ id: string; entry: CatalogEntry }>>([]);
  const entries = catalog?.entries ?? [];
  const models = entries.filter((e) => e.kind === "model");
  const drafter = entries.find((e) => e.kind === "drafter" && e.supported);
  const start = async () => {
    const wanted = [models.find((e) => e.id === modelId), withDrafter ? drafter : undefined]
      .filter((e): e is CatalogEntry => !!e && e.supported);
    setBusy(true);
    try {
      const started: Array<{ id: string; entry: CatalogEntry }> = [];
      for (const entry of wanted) {
        const list = await api<{ files: Array<{ path: string; size: number }>; total_bytes: number; libraries: ModelLibrary[] }>(
          `/models/sly-radiance/catalog/${entry.id}/files`);
        setLibs(list.libraries);
        const job = await api<{ job_id: string }>("/models/hf/download-jobs", { method: "POST", json: {
          repo: entry.repo, files: list.files.map((f) => f.path), library_id: library, expected_bytes: list.total_bytes } });
        started.push({ id: job.job_id, entry });
      }
      setJobs(started);
      show("取得を始めました。画面を閉じても続きます");
    } catch (e) {
      show(e instanceof Error ? e.message : "取得を始められませんでした", "error");
    } finally { setBusy(false); }
  };
  const finished = (entry: CatalogEntry, job: { status: string; error: string; result?: Record<string, unknown> }) => {
    const files = (job.result?.files as string[] | undefined) ?? [];
    if (job.status !== "succeeded" || !files.length) { show(job.error || "取得できませんでした", "error"); return; }
    if (entry.kind === "drafter") onDrafter(dirname(files[0]));
    else onModel(dirname(files[0]), entry.quantization);
    show(`${entry.name} を取得しました。下のフォームへ入れました`);
  };
  return <SettingsGroup title="目録から取る" description="HF から取得します（止まっても続きから取れます）。取得したフォルダは下のフォームへ入ります" collapsible defaultOpen={false}>
    <label className="block text-xs">モデル
      <select aria-label="Sly-Radiance目録のモデル" value={modelId} onChange={(e) => setModelId(e.target.value)}
        className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900">
        {models.map((e) => <option key={e.id} value={e.id} disabled={!e.supported}>
          {e.name}{e.size_gib ? ` · 約${e.size_gib} GiB` : ""}{e.uncensored ? " · 無検閲寄り" : ""}{FIT_LABEL[e.fit] ? ` · ${FIT_LABEL[e.fit]}` : ""}{e.supported ? "" : "（未対応）"}
        </option>)}
      </select>
      <span className="text-[11px] text-zinc-500">{models.find((e) => e.id === modelId)?.note}</span>
    </label>
    {drafter && <Toggle label={`先読みモデルも取る（${drafter.name}）`} hint="先読み（DFlash2）で速くなります。答えは変わりません"
      value={withDrafter} onChange={setWithDrafter} />}
    {libs.length > 0 && <label className="block text-xs">保存先
      <select aria-label="Sly-Radiance保存先" value={library} onChange={(e) => setLibrary(e.target.value)}
        className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900">
        <option value="">既定のライブラリ</option>
        {libs.map((l) => <option key={l.id} value={l.id} disabled={!l.mounted}>{l.label}</option>)}
      </select>
    </label>}
    <button type="button" disabled={busy || !catalog} onClick={() => void start()} className={secondaryButton}>
      {busy ? "確認中…" : "取得する"}
    </button>
    {jobs.map(({ id, entry }) => <div key={id}>
      <p className="text-[11px] text-zinc-500">{entry.name}</p>
      <JobProgress jobId={id} onFinished={(job) => finished(entry, job)} />
    </div>)}
  </SettingsGroup>;
}

/** 選んだフォルダの中身（safetensors の数と大きさ・量子化の種類）を読んで見せる。 */
function FolderSummary({ path, onSuggest }: { path: string; onSuggest?: (quant: SlyQuant) => void }) {
  const { data, error } = useQuery({
    queryKey: ["sly-radiance-inspect", path], enabled: !!path.trim(), retry: false,
    queryFn: () => api<FolderInfo>(`/models/sly-radiance/inspect?path=${encodeURIComponent(path.trim())}`),
  });
  if (!path.trim()) return null;
  if (error) return <p className="text-[11px] text-red-600">{error instanceof Error ? error.message : "読めません"}</p>;
  if (!data) return null;
  return <div className="text-[11px] text-zinc-500">
    <p>safetensors {data.safetensors_count} 個・{gib(data.total_bytes)}
      {data.quant_method ? ` · 量子化 ${data.quant_method}${data.quant_format ? `（${data.quant_format}）` : ""}` : ""}</p>
    {!data.ready && <p className="text-amber-600">safetensors が見つかりません（取得が途中かもしれません）</p>}
    {onSuggest && data.suggested_quantization && <button type="button" onClick={() => onSuggest(data.suggested_quantization)}
      className="mt-1 rounded-md border border-zinc-300 px-2 py-1 dark:border-zinc-600">量子化を「{QUANT_LABEL[data.suggested_quantization]}」にする</button>}
  </div>;
}

export function SlyRadianceRegisterPanel({ onDone, initial, running = false }: {
  onDone: () => void; initial?: SlyRadianceSettings; running?: boolean;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [form, setForm] = useState<SlyRadianceSettings>({ ...DEFAULTS, ...(initial ?? {}) });
  const [envText, setEnvText] = useState(envToText(initial?.engine_settings ?? {}));
  const [saving, setSaving] = useState(false);
  const [picker, setPicker] = useState<"" | "model" | "drafter">("");
  const set = <K extends keyof SlyRadianceSettings>(key: K, value: SlyRadianceSettings[K]) => setForm((f) => ({ ...f, [key]: value }));
  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  const folderRow = (label: string, aria: string, key: "model_path" | "drafter_path", which: "model" | "drafter", placeholder: string) =>
    <label className="block text-xs">{label}
      <div className="flex gap-2">
        <input aria-label={aria} value={form[key]} disabled={running} onChange={(e) => set(key, e.target.value)}
          placeholder={placeholder} className={`${field} min-w-0 flex-1 font-mono text-xs`} />
        <button type="button" disabled={running} onClick={() => setPicker(which)} aria-label={`${label}を選ぶ`}
          className="mt-1 grid min-h-11 min-w-11 place-items-center rounded-xl border border-zinc-300 disabled:opacity-40 dark:border-zinc-700"><IconFolder className="h-4 w-4" /></button>
      </div>
    </label>;
  const save = async () => {
    setSaving(true);
    try {
      const body = { ...form, model_path: form.model_path.trim(), drafter_path: form.drafter_path.trim(),
        port: Number(form.port), context: Number(form.context), max_num_seqs: Number(form.max_num_seqs),
        speculative_tokens: Number(form.speculative_tokens), gpu_memory_utilization: Number(form.gpu_memory_utilization),
        engine_settings: textToEnv(envText) };
      delete body.loaded;
      await api(initial ? `/models/sly-radiance/instances/${encodeURIComponent(initial.alias)}` : "/models/sly-radiance/instances",
        { method: initial ? "PUT" : "POST", json: body });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["models", "local"] }),
        qc.invalidateQueries({ queryKey: ["sly-radiance-instances"] }),
        qc.invalidateQueries({ queryKey: ["runtime-environment"] }),
      ]);
      show(initial ? "保存しました" : "登録しました。「ロード」で起動できます（初回はカーネルの compile で約10分）");
      onDone();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    } finally { setSaving(false); }
  };
  return <div className="space-y-3">
    {!initial && <p className="text-xs text-zinc-500">手元のモデルのフォルダを登録するか、目録から取得します。登録だけでは起動もダウンロードもしません。</p>}
    {!initial && <CatalogDownload
      onModel={(path, quant) => setForm((f) => ({ ...f, model_path: path, quantization: quant }))}
      onDrafter={(path) => set("drafter_path", path)} />}
    <SettingsGroup title="モデル" description="HF 形式のフォルダ（config.json と safetensors）。先読みモデルは任意">
      {!initial && <label className="block text-xs">モデル別名（ゲートウェイで使う名前）
        <input aria-label="Sly-Radianceモデル別名" value={form.alias} onChange={(e) => set("alias", e.target.value.trim().toLowerCase())} className={`${field} font-mono`} />
      </label>}
      {folderRow("モデルのフォルダ", "Sly-Radianceモデルのフォルダ", "model_path", "model", "/data1tb/LLM/.../Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ")}
      <FolderSummary path={form.model_path} onSuggest={running ? undefined : (q) => set("quantization", q)} />
      <label className="block text-xs">量子化の切り替え
        <select aria-label="Sly-Radiance量子化" value={form.quantization} disabled={running}
          onChange={(e) => set("quantization", e.target.value as SlyQuant)} className={field}>
          {(Object.keys(QUANT_LABEL) as SlyQuant[]).map((q) => <option key={q || "none"} value={q}>{QUANT_LABEL[q]}</option>)}
        </select>
      </label>
      {folderRow("先読みモデルのフォルダ（任意）", "Sly-Radiance先読みのフォルダ", "drafter_path", "drafter", "空なら先読みなし")}
      <FolderSummary path={form.drafter_path} />
    </SettingsGroup>
    <SettingsGroup title="基本" description="扱える長さと KV キャッシュの形式">
      <label className="block text-xs">コンテキスト長（トークン）
        <div className="mt-1"><PresetOrCustom ariaLabel="Sly-Radianceコンテキスト" value={form.context} presets={CTX_PRESETS}
          placeholder="131072" disabled={running} onChange={(v) => set("context", Math.min(262144, Math.max(4096, Number(v ?? 131072))))} /></div>
        <span className="text-[11px] text-zinc-500">上限はモデルの最大 262,144。131,072 にすると prefill が 3.5〜4.8% 速くなります（公式の実測）。</span>
      </label>
      <label className="block text-xs">KV キャッシュ
        <select aria-label="Sly-Radiance KVキャッシュ" value={form.kv_cache} disabled={running}
          onChange={(e) => set("kv_cache", e.target.value as "fp8" | "kv4")} className={field}>
          <option value="fp8">fp8（標準）</option>
          <option value="kv4">kv4（4 bit・TetraAttention・試験的）</option>
        </select>
      </label>
    </SettingsGroup>
    <SettingsGroup title="起動と停止" description="アイドル時の自動アンロードは共通設定で切り替えます">
      <Toggle label="使われたときに自動で起動" hint="チャットやゲートウェイから呼ばれたら起動します（他の大きなモデルは先に停止）。初回は約10分かかります"
        value={form.on_demand_start} onChange={(v) => set("on_demand_start", v)} />
      <Toggle label="PC起動時に自動で起動" hint="ControlDeck が立ち上がったらロードします"
        value={form.auto_start} onChange={(v) => set("auto_start", v)} />
      <Toggle label="アイドルでも止めない（常駐）" hint="共通設定の「アイドル時に自動アンロード」の対象から外します"
        value={form.idle_exclude} onChange={(v) => set("idle_exclude", v)} />
    </SettingsGroup>
    <SettingsGroup title="思考" description="要求が深さを指定しないときの既定">
      <label className="block text-xs">思考の深さ（既定）
        <select aria-label="Sly-Radiance思考の深さ" value={form.reasoning_effort} disabled={running}
          onChange={(e) => set("reasoning_effort", e.target.value as SlyRadianceSettings["reasoning_effort"])} className={field}>
          <option value="">テンプレートの既定</option>
          <option value="none">考えない</option>
          <option value="low">low</option><option value="medium">medium</option><option value="high">high</option>
        </select>
      </label>
    </SettingsGroup>
    <SettingsGroup title="性能" description="同時に答える数・先読み・prefill" collapsible defaultOpen={!initial}>
      <label className="block text-xs">同時に答える数（--max-num-seqs・1〜8）
        <input aria-label="Sly-Radiance同時実行数" type="number" min={1} max={8} value={form.max_num_seqs} disabled={running}
          onChange={(e) => set("max_num_seqs", Math.max(1, Math.min(8, Number(e.target.value) || 1)))} className={field} />
        <span className="text-[11px] text-zinc-500">8 まで（8 × 先読み 8 本の検証が上限の中）。公式の実測: 1 本 約 154 tok/s、8 本同時で合計 480 tok/s。それより多くしても待たされるだけです。</span>
      </label>
      <label className="block text-xs">先読みトークン数（DFlash2）
        <select aria-label="Sly-Radiance先読み" value={form.speculative_tokens} disabled={running}
          onChange={(e) => set("speculative_tokens", Number(e.target.value))} className={field}>
          {[0, 3, 5, 7, 9, 11, 15].map((n) => <option key={n} value={n}>{n === 0 ? "使わない" : n === 7 ? "7（推奨）" : n}</option>)}
        </select>
        <span className="text-[11px] text-zinc-500">先読みモデルのフォルダを指定したときだけ効きます。先読みは検証されるので答えは変わりません。</span>
      </label>
      <label className="block text-xs">prefill の chunk（--max-num-batched-tokens）
        <select aria-label="Sly-Radiance prefill chunk" value={form.prefill_chunk} disabled={running}
          onChange={(e) => set("prefill_chunk", Number(e.target.value) as SlyRadianceSettings["prefill_chunk"])} className={field}>
          {[2048, 4096, 8192].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
        </select>
      </label>
      <Toggle label="prefix cache" hint="同じ前置きの会話を読み直さずに続けます" value={form.prefix_cache}
        onChange={(v) => !running && set("prefix_cache", v)} />
    </SettingsGroup>
    <SettingsGroup title="詳細" description="待受ポート・GPU のメモリの使用率・追加の環境変数" collapsible defaultOpen={false}>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-xs">ポート
          <input aria-label="Sly-Radianceポート" type="number" min={8081} max={65535} value={form.port} disabled={running}
            onChange={(e) => set("port", Number(e.target.value))} className={`${field} font-mono`} />
        </label>
        <label className="block text-xs">GPU のメモリの使用率
          <input aria-label="Sly-Radiance GPUメモリ使用率" type="number" min={0.5} max={0.98} step={0.01} value={form.gpu_memory_utilization} disabled={running}
            onChange={(e) => set("gpu_memory_utilization", Number(e.target.value))} className={`${field} font-mono`} />
          <span className="text-[11px] text-zinc-500">共通の VRAM の余裕（Models → 共通設定）を引いた値に自動で抑えます。</span>
        </label>
      </div>
      <label className="block text-xs">追加の環境変数（1 行に NAME=値。RADIANCE_*・R4D_* だけ）
        <textarea aria-label="Sly-Radiance詳しい設定" value={envText} disabled={running} rows={3}
          onChange={(e) => setEnvText(e.target.value)} placeholder="RADIANCE_KV_GROUP_SIZE=8" className={`${field} py-2 font-mono text-xs`} />
      </label>
    </SettingsGroup>
    <SheetActions>
      <button disabled={saving || !form.model_path.trim()} onClick={() => void save()} className={primaryButton}>
        {saving ? "保存中…" : initial ? "保存" : "登録"}
      </button>
    </SheetActions>
    {picker && (
      <FilePicker mode="dir" title={picker === "model" ? "モデルのフォルダを選択" : "先読みモデルのフォルダを選択"}
        initialPath={(picker === "model" ? form.model_path : form.drafter_path) || "/data1tb/LLM"}
        onSelect={(path) => { set(picker === "model" ? "model_path" : "drafter_path", path); setPicker(""); }}
        onClose={() => setPicker("")} />
    )}
  </div>;
}

export function SlyRadianceDetailSheet({ alias, onClose }: { alias: string; onClose: () => void }) {
  const { data } = useSlyRadianceState();
  const item = data?.instances.find((model) => model.alias === alias);
  const [showLog, setShowLog] = useState(false);
  const { data: log } = useQuery({
    queryKey: ["sly-radiance-log", alias], enabled: showLog, refetchInterval: showLog ? 5000 : false,
    queryFn: () => api<{ log: string }>(`/models/sly-radiance/instances/${encodeURIComponent(alias)}/log`),
  });
  return <BottomSheet title={`${alias} · 個別設定`} onClose={onClose} wide>
    {item ? <div className="space-y-3">
      <DisplayNameField runtime="sly-radiance" alias={alias} />
      <ModelStatusSummary running={!!item.loaded}
        lockedNote={item.loaded ? "稼働中は「起動と停止」だけ変更できます。ほかはアンロードしてから変更してください。" : undefined}
        rows={[
          ["ランタイム", `Sly-Radiance ${data?.runtime.version ?? ""}`],
          ["モデル", <FileName path={item.model_path} />],
          ["接続先", <CopyText text={`http://127.0.0.1:${item.port}/v1`} />],
        ]}
        action={<button type="button" onClick={() => setShowLog((v) => !v)}
          className="rounded-md border border-zinc-300 px-2 py-1 text-[11px] dark:border-zinc-600">{showLog ? "ログを閉じる" : "ログ"}</button>} />
      {showLog && <pre className="max-h-56 overflow-auto rounded-xl bg-zinc-950 p-3 text-[11px] leading-relaxed text-zinc-200">{log?.log || "（まだありません）"}</pre>}
      <SlyRadianceRegisterPanel key={alias} initial={item} running={!!item.loaded} onDone={onClose} />
    </div> : <p className="text-sm text-zinc-500">モデルを読み込み中です</p>}
  </BottomSheet>;
}
