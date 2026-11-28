/** Maya-S（GLM-5.3-Flash）のモデル準備と個別設定。
 *
 *  アドオン（設定 → オプション機能）はエンジンのコード導入とHIPビルドだけを持つ。
 *  モデルの取得・既存GGUFの読み込み・個別設定はModels画面に集約する。
 *  設定・目録・ファイルからの登録は共通部品（RuntimeSettings / RuntimeCatalog /
 *  RuntimeRegisterFromFile）へ。既存の probe/catalog/job と保存 API は壊さない。
 *  準備・保存ではモデルを起動しない。起動は一覧の「ロード」、または個別設定の
 *  「要求時に自動起動」「ControlDeck起動時に自動起動」による（llama.cpp と同じ）。
 */
import { DisplayNameField } from "./DisplayNameField";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { BottomSheet } from "../../components/ui";
import { FilePicker } from "../../components/FilePicker";
import { IconFolder } from "../../components/icons";
import { useExpert } from "../commander/TeamRoles";
import { CopyText, FileName, ModelStatusSummary, SettingsGroup } from "./formControls";
import { RuntimeCatalog } from "./RuntimeCatalog";
import { MODEL_FILE_PICKER_SOURCE, RuntimeRegisterFromFile } from "./RuntimeRegisterFromFile";
import { RuntimeSettings } from "./RuntimeSettings";
import { applyInspectValues, type SettingValues } from "./runtimeSettings";

export interface MayaSettings {
  model_path: string;
  context: number;
  gpu_index: number;
  parallel: number;
  ram_headroom_gb: number;
  drafting: "on" | "off";
  on_demand_start: boolean;
  idle_exclude: boolean;
  auto_start: boolean;
  thinking_budget: number;
  reasoning_effort: "low" | "medium" | "high" | "max";
}

/** ControlDeck側だけの設定。稼働中でも変更できる（Maya の設定ファイルは変わらない）。 */
const LIFECYCLE_KEYS = ["on_demand_start", "idle_exclude", "auto_start"] as const;

export interface MayaStatus {
  installed: boolean;
  configured: boolean;
  built: boolean;
  running: boolean;
  version: string;
  models_dir: string;
  settings: MayaSettings;
  warning: string;
  error: string;
}

interface MayaJob { id: string; kind: string; status: string; error: string; progress?: { status?: string } }

const DEFAULT_SETTINGS: MayaSettings = {
  model_path: "", context: 8192, gpu_index: 0, parallel: 1, ram_headroom_gb: 16, drafting: "on",
  on_demand_start: true, idle_exclude: false, auto_start: false,
  thinking_budget: 32768,
  reasoning_effort: "high",
};

/** Maya-Sの状態。settings.manage を持たない利用者には問い合わせない。 */
export function useMayaStatus() {
  const can = useAuth((s) => s.can);
  return useQuery({
    queryKey: ["maya-s-model"],
    queryFn: () => api<MayaStatus>("/features/maya-s/model"),
    enabled: can("settings.manage"),
    refetchInterval: 10000,
    retry: false,
  });
}

/** 実行中のMaya-Sモデルジョブ。終わったら状態と一覧を読み直す。 */
function useMayaJobs() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({
    queryKey: ["maya-s-jobs"],
    queryFn: () => api<MayaJob[]>("/jobs?kind=model.maya-s."),
    refetchInterval: (q) => (q.state.data ?? []).some((j) => ["queued", "running"].includes(j.status)) ? 3000 : 15000,
  });
  const active = (data ?? []).filter((j) => ["queued", "running"].includes(j.status));
  const seen = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const job of data ?? []) {
      const running = ["queued", "running"].includes(job.status);
      if (running) { seen.current.add(job.id); continue; }
      if (!seen.current.has(job.id)) continue;
      seen.current.delete(job.id);
      void qc.invalidateQueries({ queryKey: ["maya-s-model"] });
      void qc.invalidateQueries({ queryKey: ["models"] });
      void qc.invalidateQueries({ queryKey: ["features"] });
      show(job.status === "succeeded" ? "Mayaの操作が完了しました" : job.error || "Mayaの操作に失敗しました",
        job.status === "succeeded" ? "info" : "error");
    }
  }, [data]);
  return { active, refresh: () => qc.invalidateQueries({ queryKey: ["maya-s-jobs"] }) };
}


/** 準備済みモデルの個別設定。一覧の行から開く。 */
export function MayaSDetailSheet({ onClose }: { onClose: () => void }) {
  const { data: status } = useMayaStatus();
  return (
    <BottomSheet title="GLM-5.3-Flash · Maya-S · 個別設定" onClose={onClose} wide>
      {status ? <div className="space-y-3"><DisplayNameField runtime="maya-s" alias="maya-s" /><MayaSettingsForm status={status} onDone={onClose} /></div>
        : <p className="text-sm text-zinc-500">状態を読み込み中です</p>}
    </BottomSheet>
  );
}

/** capability に無い Maya 固有の欄（model_path・drafting・ram_headroom_gb）は
 *  「旧パス維持」のためにそのまま保持し、保存のときに既存の API へ渡す。 */
function MayaSettingsForm({ status, onDone }: {
  status: MayaStatus; onDone: () => void;
}) {
  const qc = useQueryClient();
  const expert = useExpert();
  const { active, refresh } = useMayaJobs();
  const [values, setValues] = useState<SettingValues>({});
  const [picker, setPicker] = useState(false);
  const legacy = { ...DEFAULT_SETTINGS, ...status.settings };
  const modelPath = String(values.model_path ?? legacy.model_path ?? "");
  const locked = status.running || active.length > 0;
  const apply = (patch: SettingValues) => setValues((current) => ({ ...current, ...patch }));
  const dirname = (path: string) => path.replace(/\/[^/]*$/, "");

  // REQUIRED_BY_RUNTIME.maya は setup_id と folder を要する。最初のモデルは Maya 側の
  // 決め打ち（glm-maya-s）なので、共通保存ボタンを押せるようにここで入れる（保存時は除く）。
  const initial: SettingValues = {
    ...legacy,
    setup_id: "glm-maya-s",
    folder: dirname(legacy.model_path) || status.models_dir,
  };

  const save = async (body: SettingValues) => {
    const next: Record<string, unknown> = { ...legacy };
    for (const source of [values, body]) {
      for (const [key, value] of Object.entries(source)) {
        if (key in legacy && value !== undefined) next[key] = value;
      }
    }
    next.model_path = modelPath.trim();
    const lifecycleOnly = (Object.keys(legacy) as (keyof MayaSettings)[])
      .every((key) => (LIFECYCLE_KEYS as readonly string[]).includes(key) || next[key] === legacy[key]);
    if (locked && !lifecycleOnly) {
      throw new Error("稼働中は「起動」の設定だけ変更できます。ほかはモデルを停止してから変更してください。");
    }
    // 既存の保存 API（extra="forbid"）へは MayaSettings の欄だけを渡す。
    await api("/features/maya-s/settings", { method: "PUT", json: next });
    const prepare = !status.configured
      || (!!modelPath.trim() && modelPath.trim() !== String(legacy.model_path ?? "").trim());
    if (prepare) {
      await api("/features/maya-s/model/existing-jobs", { method: "POST", json: {} });
      await refresh();
    }
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["maya-s-model"] }),
      qc.invalidateQueries({ queryKey: ["models"] }),
    ]);
  };

  const modelSlot = <div className="space-y-3">
    <SettingsGroup title="モデル" description="GGUFを変えたときは保存と一緒に準備し直します（分割モデルは先頭ファイル）">
      <div className="flex gap-2">
        <input aria-label="Maya-S GGUFパス" value={modelPath} disabled={locked}
          onChange={(e) => apply({ model_path: e.target.value })}
          placeholder="/data1tb/LLM/GLM-5.3-Flash-Maya/...-00001-of-00003.gguf"
          className="min-h-11 min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3 font-mono text-xs disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900" />
        <button type="button" disabled={locked} onClick={() => setPicker(true)} aria-label="GGUFを選ぶ"
          className="grid min-h-11 min-w-11 place-items-center rounded-xl border border-zinc-300 disabled:opacity-40 dark:border-zinc-700">
          <IconFolder />
        </button>
      </div>
      {modelPath && <p className="min-w-0 truncate font-mono text-[11px] text-zinc-500" title={modelPath}>{modelPath}</p>}
    </SettingsGroup>
    <SettingsGroup title="目録から取る" description="Maya の候補は共通目録と Maya 本体の目録で確認できます（取得した GGUF は上のパスへ入ります）"
      collapsible defaultOpen={false}>
      <RuntimeCatalog runtime="maya" expert={expert} onRegister={(path) => apply({ model_path: path })} />
    </SettingsGroup>
    <SettingsGroup title="ファイルから登録" description="/data1tb・/data2tb の中から GGUF を選びます。名前はファイル名から自動で入ります"
      collapsible defaultOpen={false}>
      <RuntimeRegisterFromFile runtime="maya" mode="file" disabled={locked} expert={expert}
        onInspect={(info) => setValues((current) => applyInspectValues(current, info.metadata, "maya"))}
        onRegistered={(info) => apply({ model_path: info.path })} />
    </SettingsGroup>
  </div>;

  // capability に無い欄と注意は、エキスパート（または要対応の印）として最後に置く。
  const note = <div className="space-y-3">
    {locked && <p className="rounded-xl bg-amber-50 p-2.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
      モデルが起動中、または準備ジョブの実行中です。起動と停止の設定は変えられます。ほかの設定は、停止・完了してから変えてください。</p>}
    {expert && <SettingsGroup title="Maya 固有" collapsible defaultOpen={false}>
      <label className="block text-xs">投機デコード（MTP）
        <select aria-label="Maya-S 投機デコード" value={String(values.drafting ?? legacy.drafting)} disabled={locked}
          onChange={(e) => apply({ drafting: e.target.value as "on" | "off" })}
          className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900">
          <option value="on">有効（推奨・この PC で約5%速い）</option><option value="off">無効</option>
        </select>
      </label>
      <label className="block text-xs">RAM の空き確保量 (GB)
        <input aria-label="Maya-S RAMの空き確保量 (GB)" type="number" min={1} max={256} step={1} disabled={locked}
          value={Number(values.ram_headroom_gb ?? legacy.ram_headroom_gb)}
          onChange={(e) => apply({ ram_headroom_gb: Number(e.target.value) })}
          className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 font-mono text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900" />
        <span className="text-[11px] text-zinc-500">小さいほどエキスパートを RAM に多く置けて速くなります（この PC の推奨 8）。</span>
      </label>
    </SettingsGroup>}
  </div>;

  return <>
    <ModelStatusSummary running={status.running} lockedNote={status.running ? "稼働中は「起動」の設定だけ変更できます。ほかはアンロードしてから変更してください。" : undefined} rows={[
      ["ランタイム", `Project Maya ${status.version || "-"} · ${status.built ? "HIPビルド済み" : "HIP未ビルド"}`],
      ["モデル", status.settings?.model_path ? <FileName path={status.settings.model_path} /> : `ダウンロード版（${status.models_dir}）`],
      ["接続先", <CopyText text="http://127.0.0.1:8099/v1" />],
    ]} />
    <RuntimeSettings runtime="maya" expert={expert}
      initial={initial} values={values} onValuesChange={setValues}
      modelSlot={modelSlot} save={save} onDone={onDone} note={note} />
    {picker && (
      <FilePicker source={MODEL_FILE_PICKER_SOURCE} mode="file" title="Maya-S の GGUF を選択（/data1tb・/data2tb）"
        initialPath={modelPath || "/data1tb/LLM"}
        onSelect={(path) => { apply({ model_path: path }); setPicker(false); }}
        onClose={() => setPicker(false)} />
    )}
  </>;
}
