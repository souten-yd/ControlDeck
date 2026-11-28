/** Sly-Radiance（SlyBase/vllm-sly-radiance。R9700 向けの vLLM）のモデル登録と個別設定。
 *
 *  目録・ファイルからの登録・設定は共通部品（RuntimeCatalog / RuntimeRegisterFromFile /
 *  RuntimeSettings）に任せる。Sly 固有の先読みモデル・prefill・量子化などは capability の
 *  「速さの調整」か「エキスパート」に載る。prefix cache は常時オンなので、切る UI も
 *  保存する値も出さない（backend が常に argv へ付ける）。
 */
import { DisplayNameField } from "./DisplayNameField";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { FilePicker } from "../../components/FilePicker";
import { BottomSheet } from "../../components/ui";
import { IconFolder } from "../../components/icons";
import { useExpert } from "../commander/TeamRoles";
import { CopyText, FileName, ModelStatusSummary, SettingsGroup, secondaryButton } from "./formControls";
import { RuntimeCatalog } from "./RuntimeCatalog";
import { MODEL_FILE_PICKER_SOURCE, RuntimeRegisterFromFile } from "./RuntimeRegisterFromFile";
import { RuntimeSettings } from "./RuntimeSettings";
import { applyInspectValues, nextPort, type SettingValues } from "./runtimeSettings";

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
interface FolderInfo {
  path: string; name: string; safetensors_count: number; total_bytes: number;
  quant_method: string; quant_format: string; suggested_quantization: SlyQuant; ready: boolean;
}

const gib = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GiB`;

export function useSlyRadianceState() {
  return useQuery({
    queryKey: ["sly-radiance-instances"], refetchInterval: 5000,
    queryFn: () => api<SlyState>("/models/sly-radiance/instances"),
  });
}

/** 選んだフォルダの中身を調べる。取得が途中のときの注意は常に出し、数値・量子化の提案は
 *  エキスパートの中だけに置く（AGENTS.md「不要な表示は隠し、エキスパートで開く」）。 */
function FolderSummary({ path, expert, onSuggest }: {
  path: string; expert: boolean; onSuggest?: (quant: SlyQuant) => void;
}) {
  const { data, error } = useQuery({
    queryKey: ["sly-radiance-inspect", path], enabled: !!path.trim(), retry: false,
    queryFn: () => api<FolderInfo>(`/models/sly-radiance/inspect?path=${encodeURIComponent(path.trim())}`),
  });
  if (!path.trim()) return null;
  if (error) return <p className="text-[11px] text-red-600">{error instanceof Error ? error.message : "読めません"}</p>;
  if (!data) return null;
  return <div className="text-[11px] text-zinc-500">
    {!data.ready && <p className="text-amber-600">safetensors が見つかりません（取得が途中かもしれません）</p>}
    {expert && <>
      <p>safetensors {data.safetensors_count} 個・{gib(data.total_bytes)}
        {data.quant_method ? ` · 量子化 ${data.quant_method}${data.quant_format ? `（${data.quant_format}）` : ""}` : ""}</p>
      {onSuggest && data.suggested_quantization && <button type="button" onClick={() => onSuggest(data.suggested_quantization)}
        className="mt-1 rounded-md border border-zinc-300 px-2 py-1 dark:border-zinc-600">量子化を「{data.suggested_quantization}」にする</button>}
    </>}
  </div>;
}

export function SlyRadianceRegisterPanel({ onDone, initial, running = false }: {
  onDone: () => void; initial?: SlyRadianceSettings; running?: boolean;
}) {
  const expert = useExpert();
  const { data: state } = useSlyRadianceState();
  const [values, setValues] = useState<SettingValues>({});
  const [drafterPicker, setDrafterPicker] = useState(false);
  const apply = (patch: SettingValues) => setValues((current) => ({ ...current, ...patch }));
  const modelPath = String(values.model_path ?? initial?.model_path ?? "");
  const drafterPath = String(values.drafter_path ?? initial?.drafter_path ?? "");
  // 既存のインスタンスとポートが被らないようにする（取得や起動はしない）。
  const port = nextPort(8100, (state?.instances ?? []).map((item) => item.port));

  // 目録からの取得・ファイル選択で決まった値を共通設定のフォームへ渡す（登録自体は下の保存で行う）。
  const catalogSlot = initial ? null : <div className="space-y-3">
    <SettingsGroup title="目録から取得" description="HF から取得します（停止・再開できます）。取得したフォルダは下の設定へ入ります"
      collapsible defaultOpen={false}>
      <RuntimeCatalog runtime="sly-radiance" expert={expert} onRegister={(path, entry) => {
        // 先読みは model_path を上書きしない（続けてモデルを取ったときに消えないように）。
        if (entry.kind === "drafter") apply({ drafter_path: path });
        else apply({ model_path: path, port, ...(entry.quantization ? { quantization: entry.quantization } : {}) });
      }} />
    </SettingsGroup>
    <SettingsGroup title="ファイルから登録" description="/data1tb・/data2tb の中からフォルダを選びます。名前はファイル名から自動で入り、下の設定へ入ります（保存は最後のボタン）"
      collapsible defaultOpen={false}>
      <RuntimeRegisterFromFile runtime="sly-radiance" mode="dir" disabled={running} expert={expert}
        onInspect={(info) => setValues((current) => applyInspectValues(current, info.metadata, "sly-radiance"))}
        onRegistered={(info, alias) => apply({ model_path: info.path, alias, port })} />
    </SettingsGroup>
  </div>;

  const modelSlot = <div className="space-y-2">
    {catalogSlot}
    <FolderSummary path={modelPath} expert={expert}
      onSuggest={running ? undefined : (quant) => apply({ quantization: quant })} />
    {expert && <div>
      <button type="button" disabled={running} onClick={() => setDrafterPicker(true)} className={secondaryButton}>
        <IconFolder className="mr-1 inline h-4 w-4" />先読みモデルのフォルダを選ぶ
      </button>
      {drafterPath && <span className="ml-2 font-mono text-[11px] text-zinc-500">{drafterPath}</span>}
    </div>}
  </div>;

  // ゲートウェイ名（alias）は登録時に決め、個別設定では変えない（backend も変更を 422 で断る）。
  // 表示名は DetailSheet の DisplayNameField から変更する。
  const save = initial ? async (body: SettingValues) => {
    await api(`/models/sly-radiance/instances/${encodeURIComponent(initial.alias)}`,
      { method: "PUT", json: { ...body, alias: initial.alias } });
  } : undefined;

  return <div className="space-y-3">
    {!initial && <p className="text-xs text-zinc-500">手元のモデルのフォルダを選ぶか、目録から取得します。登録だけでは起動もしません。</p>}
    <RuntimeSettings runtime="sly-radiance" expert={expert} disabled={running}
      initial={initial ? { ...initial } : undefined}
      values={values} onValuesChange={setValues}
      modelSlot={modelSlot} save={save} onDone={onDone}
      note={running
        ? <p className="text-[11px] text-amber-700 dark:text-amber-300">稼働中は設定を変更できません。モデルを停止してから変更してください。</p>
        : initial ? <p className="text-[11px] text-zinc-500">ゲートウェイ名（alias）は変えません。一覧に出る名前は上の「表示名」から変更できます。</p> : undefined}
    />
    {drafterPicker && <FilePicker source={MODEL_FILE_PICKER_SOURCE} mode="dir"
      title="先読みモデルのフォルダを選択（/data1tb・/data2tb）" initialPath={drafterPath || "/data1tb/LLM"}
      onSelect={(path) => { apply({ drafter_path: path }); setDrafterPicker(false); }}
      onClose={() => setDrafterPicker(false)} />}
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
        lockedNote={item.loaded ? "稼働中は設定を変更できません。アンロードしてから変更してください。" : undefined}
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
