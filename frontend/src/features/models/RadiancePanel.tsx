/** Radiance（StillDeadcode/radiance）のモデル登録と個別設定。Strata / Maya-S と同じ並び:
 * 状態 → モデル → 基本 → 起動と停止 → 思考 → 性能 → 詳細。 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { FilePicker } from "../../components/FilePicker";
import { BottomSheet } from "../../components/ui";
import { IconFolder } from "../../components/icons";
import { useToasts } from "../../stores";
import {
  CopyText, FileName, ModelStatusSummary, PresetOrCustom, SettingsGroup, SheetActions, ThinkingDefaults, Toggle, primaryButton,
} from "./formControls";

export type RadianceKv = "bf16" | "fp8" | "fp8_v4" | "k6_v4";
export interface RadianceSettings {
  alias: string;
  model_path: string;
  port: number;
  context: number;
  kv_cache: RadianceKv;
  max_num_seqs: number;
  speculative_tokens: number | null;
  gpu_headroom_mib: number;
  reasoning_effort: "" | "none" | "low" | "medium" | "xhigh";
  thinking_budget: number;
  on_demand_start: boolean;
  idle_exclude: boolean;
  auto_start: boolean;
  loaded?: boolean;
}
interface RadianceState {
  runtime: { installed: boolean; version: string; warning: string };
  instances: RadianceSettings[];
}

const CTX_PRESETS = [32768, 65536, 131072, 200000, 262144].map((v) => ({ v, label: v.toLocaleString() }));
const DEFAULTS: RadianceSettings = {
  alias: "qwen3.8-27b-radiance", model_path: "", port: 8100, context: 131072, kv_cache: "bf16",
  max_num_seqs: 4, speculative_tokens: null, gpu_headroom_mib: 1024, reasoning_effort: "", thinking_budget: 0,
  on_demand_start: true, idle_exclude: false, auto_start: false,
};

export function useRadianceState() {
  return useQuery({
    queryKey: ["radiance-instances"], refetchInterval: 5000,
    queryFn: () => api<RadianceState>("/models/radiance/instances"),
  });
}

export function RadianceRegisterPanel({ onDone, initial, running = false }: {
  onDone: () => void; initial?: RadianceSettings; running?: boolean;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [form, setForm] = useState<RadianceSettings>({ ...DEFAULTS, ...(initial ?? {}) });
  const [saving, setSaving] = useState(false);
  const [picker, setPicker] = useState(false);
  const set = <K extends keyof RadianceSettings>(key: K, value: RadianceSettings[K]) => setForm((f) => ({ ...f, [key]: value }));
  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  const save = async () => {
    setSaving(true);
    try {
      const body = { alias: form.alias, model_path: form.model_path.trim(), port: Number(form.port),
        context: Number(form.context), kv_cache: form.kv_cache, max_num_seqs: Number(form.max_num_seqs),
        speculative_tokens: form.speculative_tokens, gpu_headroom_mib: Number(form.gpu_headroom_mib),
        reasoning_effort: form.reasoning_effort, thinking_budget: Number(form.thinking_budget),
        on_demand_start: form.on_demand_start, idle_exclude: form.idle_exclude, auto_start: form.auto_start };
      await api(initial ? `/models/radiance/instances/${encodeURIComponent(initial.alias)}` : "/models/radiance/instances",
        { method: initial ? "PUT" : "POST", json: body });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["models", "local"] }),
        qc.invalidateQueries({ queryKey: ["radiance-instances"] }),
        qc.invalidateQueries({ queryKey: ["runtime-environment"] }),
      ]);
      show(initial ? "保存しました" : "登録しました。「ロード」で起動できます");
      onDone();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    } finally { setSaving(false); }
  };
  return <div className="space-y-3">
    {!initial && <p className="text-xs text-zinc-500">手元の .rad ファイルを登録します。登録だけでは起動もダウンロードもしません。</p>}
    <SettingsGroup title="モデル" description=".rad 形式のモデル（重み・トークナイザ・先読みモデル入り）">
      {!initial && <label className="block text-xs">モデル別名（ゲートウェイで使う名前）
        <input aria-label="Radianceモデル別名" value={form.alias} onChange={(e) => set("alias", e.target.value.trim().toLowerCase())} className={`${field} font-mono`} />
      </label>}
      <label className="block text-xs">モデルファイル（.rad）
        <div className="flex gap-2">
          <input aria-label="Radiance .rad パス" value={form.model_path} disabled={running} onChange={(e) => set("model_path", e.target.value)}
            placeholder="/data1tb/LLM/.../qwen3.8-27b-iq5r-v4.rad" className={`${field} min-w-0 flex-1 font-mono text-xs`} />
          <button type="button" disabled={running} onClick={() => setPicker(true)} aria-label=".rad を選ぶ"
            className="mt-1 grid min-h-11 min-w-11 place-items-center rounded-xl border border-zinc-300 disabled:opacity-40 dark:border-zinc-700"><IconFolder className="h-4 w-4" /></button>
        </div>
      </label>
    </SettingsGroup>
    <SettingsGroup title="基本" description="扱える長さと KV キャッシュの形式">
      <label className="block text-xs">コンテキスト長（トークン）
        <div className="mt-1"><PresetOrCustom ariaLabel="Radianceコンテキスト" value={form.context} presets={CTX_PRESETS}
          placeholder="131072" disabled={running} onChange={(v) => set("context", Math.min(1048576, Math.max(4096, Number(v ?? 131072))))} /></div>
        <span className="text-[11px] text-zinc-500">Qwen3.8-27B の学習長は 262,144。長くするほど同時に持てる会話が減ります。</span>
      </label>
      <label className="block text-xs">KV キャッシュ
        <select aria-label="Radiance KVキャッシュ" value={form.kv_cache} disabled={running}
          onChange={(e) => set("kv_cache", e.target.value as RadianceKv)} className={field}>
          <option value="bf16">bf16（推奨・量子化なし）</option>
          <option value="fp8">fp8（容量 2 倍・わずかに近似）</option>
          <option value="fp8_v4">fp8_v4（さらに +28%）</option>
          <option value="k6_v4">k6_v4（さらに +46%）</option>
        </select>
      </label>
    </SettingsGroup>
    <SettingsGroup title="起動と停止" description="アイドル時の自動アンロードは共通設定で切り替えます">
      <Toggle label="使われたときに自動で起動" hint="チャットやゲートウェイから呼ばれたら起動します（Strata・Maya-S・使っていない llama.cpp のモデルは先に停止）"
        value={form.on_demand_start} onChange={(v) => set("on_demand_start", v)} />
      <Toggle label="PC起動時に自動で起動" hint="ControlDeck が立ち上がったらロードします"
        value={form.auto_start} onChange={(v) => set("auto_start", v)} />
      <Toggle label="アイドルでも止めない（常駐）" hint="共通設定の「アイドル時に自動アンロード」の対象から外します"
        value={form.idle_exclude} onChange={(v) => set("idle_exclude", v)} />
    </SettingsGroup>
    <SettingsGroup title="思考" description="要求が深さを指定しないときの既定。high / max の要求は xhigh として扱います">
      <ThinkingDefaults allowOff disabled={running} effort={form.reasoning_effort} budget={form.thinking_budget}
        onChange={(effort, budget) => setForm((f) => ({ ...f, reasoning_effort: effort as RadianceSettings["reasoning_effort"], thinking_budget: budget }))} />
    </SettingsGroup>
    <SettingsGroup title="性能" description="同時実行・先読み（投機デコード）" collapsible defaultOpen={!initial}>
      <label className="block text-xs">同時実行数
        <input aria-label="Radiance同時実行数" type="number" min={1} max={32} value={form.max_num_seqs} disabled={running}
          onChange={(e) => set("max_num_seqs", Math.max(1, Math.min(32, Number(e.target.value) || 1)))} className={field} />
        <span className="text-[11px] text-zinc-500">OpenCode のサブエージェントを並べるなら 2〜4。1 にするとその分コンテキストに回せます。</span>
      </label>
      <label className="block text-xs">先読みトークン数（DFlash2）
        <select aria-label="Radiance先読み" value={form.speculative_tokens === null ? "auto" : String(form.speculative_tokens)} disabled={running}
          onChange={(e) => set("speculative_tokens", e.target.value === "auto" ? null : Number(e.target.value))} className={field}>
          <option value="auto">自動（推奨・モデルの既定）</option>
          {[0, 3, 5, 7, 9, 11, 15].map((n) => <option key={n} value={n}>{n === 0 ? "使わない" : n}</option>)}
        </select>
        <span className="text-[11px] text-zinc-500">先読みは検証されるので答えは変わりません。</span>
      </label>
    </SettingsGroup>
    <SettingsGroup title="詳細" description="待受ポート・GPU の空き確保量" collapsible defaultOpen={false}>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-xs">ポート
          <input aria-label="Radianceポート" type="number" min={8081} max={65535} value={form.port} disabled={running}
            onChange={(e) => set("port", Number(e.target.value))} className={`${field} font-mono`} />
        </label>
        <label className="block text-xs">GPU の空き確保量（MiB）
          <input aria-label="Radiance GPU空き" type="number" min={96} max={16384} step={64} value={form.gpu_headroom_mib} disabled={running}
            onChange={(e) => set("gpu_headroom_mib", Number(e.target.value))} className={`${field} font-mono`} />
          <span className="text-[11px] text-zinc-500">Radiance はカードを残りすべて使います。Embedding などを並べるならここを増やします。</span>
        </label>
      </div>
    </SettingsGroup>
    <SheetActions>
      <button disabled={saving || !form.model_path.trim()} onClick={() => void save()} className={primaryButton}>
        {saving ? "保存中…" : initial ? "保存" : "登録"}
      </button>
    </SheetActions>
    {picker && (
      <FilePicker mode="file" title="Radiance の .rad を選択" initialPath={form.model_path || "/data1tb/LLM"}
        onSelect={(path) => { set("model_path", path); setPicker(false); }} onClose={() => setPicker(false)} />
    )}
  </div>;
}

export function RadianceDetailSheet({ alias, onClose }: { alias: string; onClose: () => void }) {
  const { data } = useRadianceState();
  const item = data?.instances.find((model) => model.alias === alias);
  const [showLog, setShowLog] = useState(false);
  const { data: log } = useQuery({
    queryKey: ["radiance-log", alias], enabled: showLog, refetchInterval: showLog ? 5000 : false,
    queryFn: () => api<{ log: string }>(`/models/radiance/instances/${encodeURIComponent(alias)}/log`),
  });
  return <BottomSheet title={`${alias} · 個別設定`} onClose={onClose} wide>
    {item ? <div className="space-y-3">
      <ModelStatusSummary running={!!item.loaded}
        lockedNote={item.loaded ? "稼働中は「起動と停止」だけ変更できます。ほかはアンロードしてから変更してください。" : undefined}
        rows={[
          ["ランタイム", `Radiance ${data?.runtime.version ?? ""}`],
          ["モデル", <FileName path={item.model_path} />],
          ["接続先", <CopyText text={`http://127.0.0.1:${item.port}/v1`} />],
        ]}
        action={<button type="button" onClick={() => setShowLog((v) => !v)}
          className="rounded-md border border-zinc-300 px-2 py-1 text-[11px] dark:border-zinc-600">{showLog ? "ログを閉じる" : "ログ"}</button>} />
      {showLog && <pre className="max-h-56 overflow-auto rounded-xl bg-zinc-950 p-3 text-[11px] leading-relaxed text-zinc-200">{log?.log || "（まだありません）"}</pre>}
      <RadianceRegisterPanel key={alias} initial={item} running={!!item.loaded} onDone={onClose} />
    </div> : <p className="text-sm text-zinc-500">モデルを読み込み中です</p>}
  </BottomSheet>;
}
