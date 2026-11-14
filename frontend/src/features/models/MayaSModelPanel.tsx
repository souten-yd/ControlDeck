/** Maya-S（GLM-5.3-Flash）のモデル準備と個別設定。
 *
 * アドオン（設定 → オプション機能）はエンジンのコード導入とHIPビルドだけを持つ。
 * モデルの取得・既存GGUFの読み込み・個別設定はModels画面に集約する。
 * 準備・保存ではモデルを起動しない。起動は一覧の「ロード」、または個別設定の
 * 「要求時に自動起動」「ControlDeck起動時に自動起動」による（llama.cpp と同じ）。 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { BottomSheet, ConfirmDialog } from "../../components/ui";
import { FilePicker } from "../../components/FilePicker";
import { IconFolder } from "../../components/icons";
import { CTX_PRESETS, ModelStatusSummary, PresetOrCustom, SettingsGroup, SheetActions, Toggle, primaryButton, secondaryButton } from "./formControls";

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
  max_output_tokens: number;
  thinking_budget: number;
}

/** ControlDeck側だけの設定。稼働中でも変更できる（Maya の設定ファイルは変わらない）。 */
const LIFECYCLE_KEYS = ["on_demand_start", "idle_exclude", "auto_start", "max_output_tokens"] as const;
// GLM-5.3-Flash は最大 1M。Maya は VRAM を超える KV を RAM へ逃がす。
const MAYA_CTX_PRESETS = [...CTX_PRESETS.filter((p) => Number(p.v) >= 8192),
  ...[524288, 1048576].map((v) => ({ v, label: v.toLocaleString() }))];

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
  max_output_tokens: 65536, thinking_budget: 32768,
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
      show(job.status === "succeeded" ? "Maya-Sの操作が完了しました" : job.error || "Maya-Sの操作に失敗しました",
        job.status === "succeeded" ? "info" : "error");
    }
  }, [data]);
  return { active, refresh: () => qc.invalidateQueries({ queryKey: ["maya-s-jobs"] }) };
}

/** 未準備のときだけ一覧の上に出す。ダウンロードか既存GGUFかを選ぶ。 */
export function MayaSSetupCard({ status }: { status: MayaStatus }) {
  const show = useToasts((s) => s.show);
  const { active, refresh } = useMayaJobs();
  const [confirming, setConfirming] = useState(false);
  const [gpu, setGpu] = useState(status.settings?.gpu_index ?? 0);
  const [existingOpen, setExistingOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const working = busy || active.length > 0;

  const download = async () => {
    setConfirming(false);
    setBusy(true);
    try {
      await api("/features/maya-s/model/prepare-jobs", { method: "POST", json: { gpu_index: gpu } });
      show("Maya-S（約96.5GB）のダウンロードとHIP準備を開始しました。完了後に「ロード」で起動できます");
      await refresh();
    } catch (e) {
      show(e instanceof Error ? e.message : "Maya-Sのダウンロードを開始できませんでした", "error");
    } finally { setBusy(false); }
  };

  return (
    <div className="mb-3 rounded-2xl border border-dashed border-zinc-300 p-4 dark:border-zinc-700">
      <p className="text-sm font-semibold">Maya-S（GLM-5.3-Flash）</p>
      <p className="mt-1 text-xs text-zinc-500">
        モデルが未準備です。約96.5GBをダウンロードするか、手元にある Maya-S の GGUF を読み込んでください。
        どちらもモデルは起動しません。準備後に一覧の「ロード」で起動します。
      </p>
      {!status.built && (
        <p className="mt-1 text-[11px] text-zinc-400">HIPエンジンは未ビルドです。準備の途中でビルドされます（設定 → オプション機能から先にビルドもできます）。</p>
      )}
      {active.length > 0 ? (
        <p className="mt-3 text-xs text-accent-700 dark:text-accent-400" role="status">
          準備中: {active[0].progress?.status || "処理中"}（ブラウザを閉じても継続します）
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" disabled={working} onClick={() => setConfirming(true)}
            className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
            モデルをダウンロード（約96.5GB）
          </button>
          <button type="button" disabled={working} onClick={() => setExistingOpen(true)}
            className="min-h-11 rounded-xl border border-zinc-300 px-4 text-sm font-medium hover:bg-zinc-50 disabled:opacity-40 dark:border-zinc-700 dark:hover:bg-zinc-800">
            既存のモデルを読み込む
          </button>
        </div>
      )}
      {confirming && (
        <ConfirmDialog title="Maya-Sをダウンロードしますか？" danger={false}
          message="GLM-5.3-Flash Maya-Sのモデル約96.5GBをSSDにダウンロードして検証し、HIPエンジンを準備します。110GB以上の空き容量が必要で、数時間かかることがあります。モデルは起動しません。"
          confirmLabel="ダウンロードを開始" busy={busy}
          disabled={!Number.isInteger(gpu) || gpu < 0 || gpu > 15}
          onConfirm={() => void download()} onClose={() => setConfirming(false)}>
          <label className="mt-3 flex items-center justify-between gap-2 text-xs">ROCm GPU番号
            <input aria-label="Maya-S GPU番号" type="number" min={0} max={15} value={gpu}
              onChange={(e) => setGpu(Number(e.target.value))}
              className="w-20 rounded-lg border border-zinc-300 bg-transparent px-2 py-2 dark:border-zinc-600" />
          </label>
          <p className="mt-2 break-all text-[11px] text-zinc-500">保存先: {status.models_dir}</p>
        </ConfirmDialog>
      )}
      {existingOpen && (
        <BottomSheet title="Maya-S · 既存のモデルを読み込む" onClose={() => setExistingOpen(false)} wide>
          <MayaSettingsForm status={status} mode="existing" onDone={() => { setExistingOpen(false); void refresh(); }} />
        </BottomSheet>
      )}
    </div>
  );
}

/** 準備済みモデルの個別設定。一覧の行から開く。 */
export function MayaSDetailSheet({ onClose }: { onClose: () => void }) {
  const { data: status } = useMayaStatus();
  return (
    <BottomSheet title="Maya-S · モデル個別設定" onClose={onClose} wide>
      {status ? <MayaSettingsForm status={status} mode="detail" onDone={onClose} />
        : <p className="text-sm text-zinc-500">状態を読み込み中です</p>}
    </BottomSheet>
  );
}

function MayaSettingsForm({ status, mode, onDone }: {
  status: MayaStatus; mode: "existing" | "detail"; onDone: () => void;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { active, refresh } = useMayaJobs();
  const [settings, setSettings] = useState<MayaSettings>({ ...DEFAULT_SETTINGS, ...status.settings });
  const [picker, setPicker] = useState(false);
  const [saving, setSaving] = useState(false);
  const saved = { ...DEFAULT_SETTINGS, ...status.settings };
  const onlyLifecycle = (Object.keys(settings) as (keyof MayaSettings)[])
    .every((key) => (LIFECYCLE_KEYS as readonly string[]).includes(key) || settings[key] === saved[key]);
  const locked = status.running || active.length > 0;
  // 起動・停止の扱いだけなら稼働中でも保存できる。
  const saveLocked = active.length > 0 || (status.running && !onlyLifecycle);
  const modelChanged = settings.model_path !== (status.settings?.model_path ?? "");
  // 既存モデル読み込みはパス必須。個別設定では、パスを変えたときだけ再準備が要る。
  const needsPrepare = mode === "existing" || (modelChanged && !!settings.model_path) || !status.configured;
  const field = "min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";

  const save = async (prepare: boolean) => {
    if (prepare && !settings.model_path.trim()) {
      show("Maya-S の GGUF（分割モデルは先頭ファイル）を指定してください", "error");
      return;
    }
    setSaving(true);
    try {
      await api("/features/maya-s/settings", { method: "PUT", json: { ...settings, model_path: settings.model_path.trim() } });
      if (prepare) {
        await api("/features/maya-s/model/existing-jobs", { method: "POST", json: {} });
        show("既存モデルの準備を開始しました（ダウンロード・起動はしません）");
        await refresh();
      } else {
        show(onlyLifecycle ? "Maya-Sの起動・停止の設定を保存しました" : "Maya-Sの設定を保存しました。次回のロードから反映されます");
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["maya-s-model"] }),
        qc.invalidateQueries({ queryKey: ["models"] }),
      ]);
      onDone();
    } catch (e) {
      show(e instanceof Error ? e.message : "Maya-Sの設定を保存できませんでした", "error");
    } finally { setSaving(false); }
  };

  const num = (key: "gpu_index" | "parallel" | "ram_headroom_gb" | "thinking_budget", label: string, min: number, max: number, step = 1, hint?: string) => (
    <label className="block text-xs">{label}
      <input aria-label={`Maya-S ${label}`} type="number" min={min} max={max} step={step} disabled={locked}
        value={settings[key]} className={`mt-1 ${field}`}
        onChange={(e) => setSettings({ ...settings, [key]: Number(e.target.value) })} />
      {hint && <span className="text-[10px] text-zinc-500">{hint}</span>}
    </label>
  );

  return (
    <div className="space-y-3 text-sm">
      {mode === "detail" && (
        <ModelStatusSummary running={status.running} rows={[
          ["ランタイム", `Project Maya ${status.version || "-"} · ${status.built ? "HIPビルド済み" : "HIP未ビルド"}`],
          ["モデル", <span className="font-mono">{status.settings?.model_path || `ダウンロード版（${status.models_dir}）`}</span>],
          ["接続先", <span className="font-mono">http://127.0.0.1:8099/v1</span>],
        ]} />
      )}
      <SettingsGroup title="モデル" description={mode === "detail" ? "GGUFを変えたときは「保存して準備し直す」" : "手元の Maya-S の GGUF（分割モデルは先頭ファイル）"}>
        <div className="flex gap-2">
          <input aria-label="Maya-S GGUFパス" value={settings.model_path} disabled={locked}
            onChange={(e) => setSettings({ ...settings, model_path: e.target.value })}
            placeholder="/data1tb/LLM/GLM-5.3-Flash-Maya/.../GLM-5.3-Flash-Maya-S-...-00001-of-00003.gguf"
            className={`min-w-0 flex-1 font-mono text-xs ${field}`} />
          <button type="button" disabled={locked} onClick={() => setPicker(true)} aria-label="GGUFを選ぶ"
            className="grid min-h-11 min-w-11 place-items-center rounded-xl border border-zinc-300 disabled:opacity-40 dark:border-zinc-700">
            <IconFolder />
          </button>
        </div>
      </SettingsGroup>
      <SettingsGroup title="基本" description="扱える長さと、1回に書ける長さ">
        <label className="block text-xs">コンテキスト長（CTX）
          <div className="mt-1">
            <PresetOrCustom ariaLabel="Maya-S コンテキスト長" value={settings.context} presets={MAYA_CTX_PRESETS} placeholder="8192" disabled={locked}
              onChange={(v) => setSettings({ ...settings, context: Math.min(1048576, Math.max(1024, Number(v ?? 8192))) })} />
          </div>
          <span className="text-[10px] text-zinc-500">GLM-5.3-Flash は最大 1M。この PC では 1M でも速度はほぼ変わりません（KV は RAM に置かれます）。</span>
        </label>
        <label className="block text-xs">出力の上限（思考＋回答のトークン）
          <div className="mt-1">
            <PresetOrCustom ariaLabel="Maya-S 出力の上限" value={settings.max_output_tokens}
              presets={[16384, 32768, 65536, 131072].map((v) => ({ v, label: v.toLocaleString() }))} placeholder="65536"
              onChange={(v) => setSettings({ ...settings, max_output_tokens: Math.min(262144, Math.max(1024, Number(v ?? 65536))) })} />
          </div>
          <span className="text-[10px] text-zinc-500">OpenCode へ伝える上限。思考も出力に数えるため、思考の上限より十分大きくします（コンテキストの半分まで）。稼働中も変更できます。</span>
        </label>
      </SettingsGroup>
      {mode === "detail" && (
        <SettingsGroup title="起動と停止" description="稼働中でも変更できます。アイドル時の自動アンロードは Models の共通設定で切り替えます">
          <Toggle label="要求時に自動起動" hint="チャット・ゲートウェイから使われたときに起動します。Strata や使っていない llama.cpp のモデルは先に停止します"
            value={settings.on_demand_start} onChange={(v) => setSettings({ ...settings, on_demand_start: v })} />
          <Toggle label="共通アイドル停止から除外" hint="アイドル時の自動アンロードの対象から外し、常駐させます"
            value={settings.idle_exclude} onChange={(v) => setSettings({ ...settings, idle_exclude: v })} />
          <Toggle label="ControlDeck起動時に自動起動" hint="PC起動後に ControlDeck が立ち上がったら起動します。Strata と両方に付けた場合は Maya-S が優先です"
            value={settings.auto_start} onChange={(v) => setSettings({ ...settings, auto_start: v })} />
        </SettingsGroup>
      )}
      <SettingsGroup title="思考">
        {num("thinking_budget", "思考の上限（トークン・0で無制限）", 0, 131072, 1024,
          "超えると思考を閉じて回答を始めます。要求ごとの思考の深さ（low〜max）は使う側で指定します。")}
      </SettingsGroup>
      <SettingsGroup title="性能" description="投機デコード・同時実行" collapsible defaultOpen={false}>
        <label className="block text-xs">投機デコード（MTP）
          <select aria-label="Maya-S 投機デコード" value={settings.drafting} disabled={locked}
            onChange={(e) => setSettings({ ...settings, drafting: e.target.value as "on" | "off" })}
            className={`mt-1 ${field}`}>
            <option value="on">有効（推奨・この PC で約5%速い）</option>
            <option value="off">無効</option>
          </select>
        </label>
        {num("parallel", "同時実行数", 1, 8, 1, "2以上にすると KV を VRAM に置くため、エキスパートの置き場が減ります。")}
      </SettingsGroup>
      <SettingsGroup title="詳細" description="GPU 番号・RAM の空き確保量" collapsible defaultOpen={false}>
        <div className="grid gap-2 sm:grid-cols-2">
          {num("gpu_index", "ROCm GPU番号", 0, 15)}
          {num("ram_headroom_gb", "RAMの空き確保量 (GB)", 1, 256, 1, "小さいほどエキスパートを RAM に多く置けて速くなります（この PC の推奨 8）。")}
        </div>
      </SettingsGroup>
      <SheetActions note={locked ? (status.running
        ? "稼働中は「起動と停止」と出力の上限だけ変更できます。ほかはアンロードしてから変更してください。"
        : "準備ジョブの完了後に変更できます。") : undefined}>
        {mode === "detail" && !needsPrepare && (
          <button type="button" disabled={saving || saveLocked} onClick={() => void save(false)} className={primaryButton}>
            {saving ? "保存中…" : "設定を保存"}
          </button>
        )}
        {(needsPrepare || mode === "detail") && (
          <button type="button" disabled={saving || locked || !settings.model_path.trim()} onClick={() => void save(true)}
            className={needsPrepare ? primaryButton : secondaryButton}>
            {saving ? "保存中…" : mode === "existing" ? "読み込んで準備" : "保存して準備し直す"}
          </button>
        )}
      </SheetActions>
      {picker && (
        <FilePicker mode="file" title="Maya-S の GGUF を選択"
          initialPath={settings.model_path || "/data1tb/LLM"}
          onSelect={(path) => { setSettings({ ...settings, model_path: path }); setPicker(false); }}
          onClose={() => setPicker(false)} />
      )}
    </div>
  );
}
