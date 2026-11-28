/** 全ランタイム共通の設定。backend の capability（GET /models/settings/capabilities）だけを
 *  情報源にし、①モデル ②コンテキストの長さ ③同時に処理する数 ④速さの調整 ⑤起動
 *  ⑥エキスパート の順で出す。ランタイムに無い欄は出さない。
 *
 *  通常は選択肢・スライダーだけ。自由入力（数値・引数・環境変数・パス）は
 *  利用者ごとに覚えられる「エキスパート」の中だけに置く（AGENTS.md の UI ルール）。
 *  Sly の prefix cache は常時オンなので、切る選択肢を出さない。
 *
 *  保存先は capability の endpoints.instances（既存の各ランタイム API）。
 *  Maya のように登録と設定が別 API のときは親から save を渡す。
 */
import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Skeleton } from "../../components/ui";
import { useToasts } from "../../stores";
import { useExpert } from "../commander/TeamRoles";
import {
  SettingsGroup, SheetActions, Toggle, primaryButton,
} from "./formControls";
import {
  buildSaveBody, coerceField, contextLimit, dictToText, displayValue, fieldPresets,
  invalidateRuntimeQueries, isFieldVisible, mergeSettingValues, sectionFields, sectionLabels,
  textToDict, useRuntimeCapabilities, type SettingValues,
} from "./runtimeSettings";
import type { CapabilityField } from "../../api/models";

export interface RuntimeSettingsProps {
  /** backend runtime_catalog / runtime_settings の id（llama / radiance / sly-radiance / strata / maya）。 */
  runtime: string;
  /** 登録済みの値。あるなら PUT（更新）、無ければ POST（新規登録）。 */
  initial?: SettingValues | null;
  /** 親が管理する値（目録・ファイル選択で決まる model_path / alias / family / setup_id など）。 */
  values?: SettingValues | null;
  onValuesChange?: (values: SettingValues) => void;
  /** ①モデルの中に置く、目録・ファイル選択の導線。 */
  modelSlot?: ReactNode;
  /** 保存の差し替え。Maya は登録 API が別なので親が渡す。 */
  save?: (values: SettingValues) => Promise<void>;
  /** 保存の前に親が承認を取る。false を返したら何も保存せず、成功通知も出さない。 */
  beforeSave?: (values: SettingValues) => Promise<boolean>;
  onDone?: () => void;
  note?: ReactNode;
  disabled?: boolean;
  /** 省略時は利用者ごとに保存されたエキスパート設定を使う。 */
  expert?: boolean;
}

const empty = (value: unknown) => value === undefined || value === null || String(value).trim() === "";

/** 保存の前に親が決めておくべき欄。それ以外の空欄は「モデルの既定」としてそのまま送る。 */
const REQUIRED_BY_RUNTIME: Record<string, string[]> = {
  llama: ["alias", "model_path"],
  radiance: ["alias", "model_path"],
  "sly-radiance": ["alias", "model_path"],
  strata: ["alias", "family", "model"],
  maya: ["setup_id", "folder"],
};

export function RuntimeSettings({
  runtime, initial, values, onValuesChange, modelSlot, save, beforeSave, onDone, note,
  disabled = false, expert: expertProp,
}: RuntimeSettingsProps) {
  const storedExpert = useExpert();
  const expert = expertProp ?? storedExpert;
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const { data: cap, isLoading, error } = useRuntimeCapabilities(runtime);
  const [form, setForm] = useState<SettingValues>({});
  const [saving, setSaving] = useState(false);

  // 親の値は参照が変わるたびに戻すのではなく、中身が変わったときだけ取り込む。
  const initialKey = JSON.stringify(initial ?? {});
  const valuesKey = JSON.stringify(values ?? {});
  useEffect(() => {
    if (!cap) return;
    const merged = mergeSettingValues(cap, JSON.parse(initialKey), JSON.parse(valuesKey));
    // ファイル名をそのまま別名に（利用者の「なるべく入力しない」決まり）。Sly の検証に
    // 合わせて小文字英数字・.・- だけにする。親が渡した alias は優先する。
    if (!merged.alias && typeof merged.model_path === "string" && merged.model_path) {
      const stem = merged.model_path.split("/").pop()?.replace(/\.[^.]+$/, "") ?? "";
      const suggestion = stem.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
      if (suggestion) merged.alias = suggestion;
    }
    setForm(merged);
  }, [cap, initialKey, valuesKey]);

  const requiredIds = REQUIRED_BY_RUNTIME[runtime] ?? [];
  const missing = requiredIds.filter((id) => empty(form[id]));
  const missingLabels = missing.map((id) =>
    (cap?.sections ?? []).flatMap((section) => section.fields).find((field) => field.id === id)?.label ?? id);

  const setField = (id: string, value: unknown) => {
    const next = { ...form, [id]: value };
    setForm(next);
    onValuesChange?.(next);
  };

  const doSave = async () => {
    if (!cap) return;
    setSaving(true);
    try {
      const body = buildSaveBody(cap, form, initial);
      // 高負荷な取得などを親が確認する。承認されなければ保存も成功通知もしない。
      if (beforeSave && !(await beforeSave(body))) return;
      if (save) {
        await save(body);
      } else if (runtime === "maya") {
        // Maya は登録（setup_id + folder）と設定（alias ごとの API）が別。
        throw new Error("Maya は専用の登録画面から保存してください");
      } else {
        const endpoint = cap.endpoints?.instances;
        if (!endpoint) throw new Error("このランタイムの保存先が分かりません");
        // capability の endpoint は /api/v1 付きの完全パス。api() が前置するので外す。
        const path = endpoint.replace(/^\/api\/v1/, "");
        const alias = String(initial?.alias ?? form.alias ?? "");
        await api(initial?.alias ? `${path}/${encodeURIComponent(alias)}` : path,
          { method: initial?.alias ? "PUT" : "POST", json: body });
      }
      await Promise.all(invalidateRuntimeQueries(qc, runtime));
      show(initial ? "設定を保存しました" : "登録しました。ロードで起動できます");
      onDone?.();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    } finally {
      setSaving(false);
    }
  };

  const limit = contextLimit(form);
  const renderField = (field: CapabilityField): ReactNode => {
    if (!isFieldVisible(field, expert)) return null;
    const value = form[field.id];
    const presets = fieldPresets(field, limit);
    const bound = limit !== null && (field.id === "ctx_size" || field.id === "context")
      ? <span className="mt-1 block text-[11px] text-zinc-500">ファイルの上限 {limit.toLocaleString()} トークンまで</span>
      : null;
    const label = <span className="mb-1 block text-xs font-medium text-zinc-500">{field.label}</span>;
    const hint = field.note ? <span className="mt-1 block text-[11px] text-zinc-500">{field.note}</span> : null;

    // 別名はファイル名から自動で入るが、登録の必須欄なので通常モードでも直せるようにする
    // （細かい設定ではなく名前そのもの。エキスパートに隠すと登録が行き詰まる）。
    if (field.id === "alias") {
      return <label key={field.id} className="block">{label}
        <input aria-label={field.label} disabled={disabled} value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) => setField(field.id, e.target.value)}
          className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        {hint}
      </label>;
    }
    if (field.type === "bool") {
      return <Toggle key={field.id} label={field.label} hint={field.note}
        value={Boolean(value)} disabled={disabled} onChange={(v) => setField(field.id, v)} />;
    }
    if (presets) {
      const custom = !expert && !presets.some((p) => p.v === value);
      return <label key={field.id} className="block">{label}
        <select aria-label={field.label} disabled={disabled} value={custom ? "" : String(value ?? "")}
          onChange={(e) => {
            const picked = presets.find((p) => String(p.v) === e.target.value);
            if (picked) setField(field.id, picked.v);
          }}
          className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900">
          {custom && <option value="">{displayValue(field, value)}（現在の値）</option>}
          {presets.map((p) => <option key={String(p.v)} value={String(p.v)}>{p.label}</option>)}
        </select>
        {bound}
        {expert && field.type === "int" && (
          <input aria-label={`${field.label}（数値）`} type="number" disabled={disabled}
            value={value === undefined || value === null ? "" : String(value)}
            min={field.min} max={field.max} step={field.step ?? 1}
            onChange={(e) => setField(field.id, e.target.value === "" ? null : Number(e.target.value))}
            className="mt-1.5 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        )}
        {hint}
      </label>;
    }
    if (field.type === "int" || field.type === "float") {
      const min = field.min ?? 0;
      const max = Math.min(
        field.max ?? (field.type === "float" ? 1 : 100),
        limit !== null && (field.id === "ctx_size" || field.id === "context") ? limit : Infinity,
      );
      const step = field.step ?? (field.type === "float" ? 0.01 : 1);
      const numeric = typeof value === "number" ? value : Number(value ?? min);
      return <label key={field.id} className="block">{label}
        <div className="flex items-center gap-2">
          <input type="range" aria-label={field.label} disabled={disabled} min={min} max={max} step={step}
            value={Number.isFinite(numeric) ? Math.min(max, Math.max(min, numeric)) : min}
            onChange={(e) => setField(field.id, coerceField(field, Number(e.target.value), limit))}
            className="min-w-0 flex-1" />
          <span className="num min-w-14 shrink-0 text-right text-xs">{displayValue(field, value)}</span>
        </div>
        {bound}
        {expert && (
          <input aria-label={`${field.label}（数値入力）`} type="number" disabled={disabled}
            value={value === undefined || value === null ? "" : String(value)}
            min={min} max={max} step={step}
            onChange={(e) => setField(field.id, e.target.value === "" ? null : Number(e.target.value))}
            className="mt-1.5 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        )}
        {hint}
      </label>;
    }
    if (field.type === "dict" && expert) {
      return <label key={field.id} className="block">{label}
        <textarea aria-label={field.label} disabled={disabled} rows={3} value={dictToText(value)}
          onChange={(e) => setField(field.id, textToDict(e.target.value))}
          placeholder="KEY=value（1行に1つ）"
          className="mt-1 w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900" />
        {hint}
      </label>;
    }
    // text / path は expert だけ編集できる。通常は値を読むだけ（空の入力欄は出さない）。
    return <div key={field.id}>
      {label}
      <div className="flex min-h-11 items-center justify-between gap-2 rounded-xl border border-zinc-200 px-3 dark:border-zinc-700">
        <span className="min-w-0 truncate text-xs">{displayValue(field, value)}</span>
        {expert && (
          <input aria-label={field.label} disabled={disabled}
            value={value === undefined || value === null ? "" : String(value)}
            onChange={(e) => setField(field.id, e.target.value)}
            className="min-w-0 flex-1 bg-transparent text-right font-mono text-xs outline-none" />
        )}
        {!expert && value !== undefined && value !== null && String(value) !== "" && (
          <span className="shrink-0 text-[10px] text-zinc-400">エキスパートで変更</span>
        )}
      </div>
      {hint}
    </div>;
  };

  return (
    <div data-runtime-settings={runtime} data-expert={expert ? "on" : "off"} className="space-y-3">
      {error && <p className="text-xs text-red-600">{error instanceof Error ? error.message : "設定の形を取得できませんでした"}</p>}
      {isLoading && <Skeleton className="h-24" />}
      {modelSlot}
      {sectionLabels(cap).map((section) => {
        const fields = sectionFields(cap, section.id).filter((field) => isFieldVisible(field, expert));
        if (fields.length === 0) return null;
        return <SettingsGroup key={section.id} title={section.label} collapsible={section.id === "expert"}>
          {fields.map(renderField)}
        </SettingsGroup>;
      })}
      {expert && cap?.always_on?.map((item) => (
        <p key={item.id} className="rounded-xl border border-dashed border-zinc-200 px-3 py-2 text-[11px] text-zinc-500 dark:border-zinc-700">
          {item.note}
        </p>
      ))}
      {note}
      <SheetActions note={missing.length ? `①で ${missingLabels.join("・")} を決めてください`
        : runtime === "maya" && !save ? "Maya は専用の登録画面から保存します" : undefined}>
        <button type="button" data-testid="runtime-settings-save" onClick={() => void doSave()}
          disabled={!cap || saving || disabled || missing.length > 0 || (runtime === "maya" && !save)} className={primaryButton}>
          {saving ? "保存中…" : initial ? "設定を保存" : "登録する"}
        </button>
      </SheetActions>
    </div>
  );
}
