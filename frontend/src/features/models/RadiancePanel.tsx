/** Radiance（StillDeadcode/radiance）のモデル登録と個別設定。
 *
 *  目録・ファイルからの登録・設定は共通部品（RuntimeCatalog / RuntimeRegisterFromFile /
 *  RuntimeSettings）に任せる。並び・エキスパートの扱いは Sly-Radiance と同じ（①〜⑥）。
 *  .rad の候補は知られているが backend の catalog は `acquire=none` なので、
 *  この画面では取得ボタンを出さず案内だけにする（ファイル選択で登録する）。
 *  登録済みの .rad のパスはそのまま維持する。
 */
import { DisplayNameField } from "./DisplayNameField";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { BottomSheet } from "../../components/ui";
import { useExpert } from "../commander/TeamRoles";
import { CopyText, FileName, ModelStatusSummary, SettingsGroup } from "./formControls";
import { RuntimeCatalog } from "./RuntimeCatalog";
import { RuntimeRegisterFromFile } from "./RuntimeRegisterFromFile";
import { RuntimeSettings } from "./RuntimeSettings";
import type { SettingValues } from "./runtimeSettings";

export type RadianceKv = "bf16" | "i8" | "fp8";
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
  /** 会話ごとの状態を f16、画像用エンコーダーを RAM へ。KV に VRAM を回す（速さは変わらない） */
  memory_saver: boolean;
  /** VRAM に入らない重みを RAM へ（長くできるが生成が大きく遅くなる） */
  ram_offload: boolean;
  on_demand_start: boolean;
  idle_exclude: boolean;
  auto_start: boolean;
  loaded?: boolean;
}
interface RadianceState {
  runtime: { installed: boolean; version: string; warning: string };
  instances: RadianceSettings[];
}

export function useRadianceState() {
  return useQuery({
    queryKey: ["radiance-instances"], refetchInterval: 5000,
    queryFn: () => api<RadianceState>("/models/radiance/instances"),
  });
}

export function RadianceRegisterPanel({ onDone, initial, running = false }: {
  onDone: () => void; initial?: RadianceSettings; running?: boolean;
}) {
  const expert = useExpert();
  const [values, setValues] = useState<SettingValues>({});
  const apply = (patch: SettingValues) => setValues((current) => ({ ...current, ...patch }));
  const modelPath = String(values.model_path ?? initial?.model_path ?? "");

  // 目録・ファイル選択で決まった値は共通設定のフォームへ渡す（登録自体は下の保存で行う）。
  const catalogSlot = initial ? null : <div className="space-y-3">
    <SettingsGroup title="目録から探す" description=".rad の候補は公式の README や HF の StillDeadcode リポジトリで確認できます。ControlDeck からは取得しません（acquire=none）"
      collapsible defaultOpen={false}>
      <RuntimeCatalog runtime="radiance" expert={expert}
        onRegister={(path) => apply({ model_path: path })} />
    </SettingsGroup>
    <SettingsGroup title="ファイルから登録" description="/data1tb・/data2tb の中から .rad を選びます。名前はファイル名から自動で入り、下の設定へ入ります（保存は最後のボタン）"
      collapsible defaultOpen={false}>
      <RuntimeRegisterFromFile runtime="radiance" mode="file" disabled={running} expert={expert}
        onRegistered={(info, alias) => apply({ model_path: info.path, alias })} />
    </SettingsGroup>
  </div>;

  const modelSlot = <div className="space-y-2">
    {catalogSlot}
    {modelPath && <p className="min-w-0 truncate font-mono text-[11px] text-zinc-500" title={modelPath}>{modelPath}</p>}
  </div>;

  // ゲートウェイ名（alias）は登録時に決め、個別設定では変えない（backend も変更を 422 で断る）。
  // 表示名は DetailSheet の DisplayNameField から変更する。
  const save = initial ? async (body: SettingValues) => {
    await api(`/models/radiance/instances/${encodeURIComponent(initial.alias)}`,
      { method: "PUT", json: { ...body, alias: initial.alias } });
  } : undefined;

  return <div className="space-y-3">
    {!initial && <p className="text-xs text-zinc-500">手元の .rad ファイルを選びます。取得・登録は別々で、登録だけでは起動もしません。</p>}
    <RuntimeSettings runtime="radiance" expert={expert}
      initial={initial ? { ...initial } : undefined}
      values={values} onValuesChange={setValues}
      modelSlot={modelSlot} save={save} onDone={onDone}
      note={running
        ? <p className="text-[11px] text-amber-700 dark:text-amber-300">稼働中は「起動」の設定だけ変更できます。ほかはモデルを停止してから変更してください。</p>
        : initial ? <p className="text-[11px] text-zinc-500">ゲートウェイ名（alias）は変えません。一覧に出る名前は上の「表示名」から変更できます。</p> : undefined}
    />
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
      <DisplayNameField runtime="radiance" alias={alias} />
      <ModelStatusSummary running={!!item.loaded}
        lockedNote={item.loaded ? "稼働中は「起動」の設定だけ変更できます。ほかはアンロードしてから変更してください。" : undefined}
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
