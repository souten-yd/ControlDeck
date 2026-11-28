/** 共通の設定部品 RuntimeSettings の型・変換・hooks。
 *
 * 形は backend `GET /api/v1/models/settings/capabilities` の契約から読む（二重持ちしない）。
 * 欄の id は各ランタイムの保存 API の引数名そのもの。ここでは選択肢・範囲・既定を
 * そのまま使い、保存本体を作るときに `ref`（既存の別画面へ委ねる欄）と
 * Sly の prefix cache（常時オンで切る欄が無い）を取り除く。
 */
import { useQuery, type QueryClient } from "@tanstack/react-query";
import {
  getRuntimeCapabilities,
  type CapabilityField,
  type CapabilitySection,
  type RuntimeCapabilities,
} from "../../api/models";

export type SettingValues = Record<string, unknown>;

/** backend runtime_settings.SECTION_ORDER と同じ並び（表示の並びをランタイム間で揃える）。 */
export const SECTION_ORDER = ["model", "context", "parallel", "speed", "launch", "expert"] as const;

/** prefix cache は常にオン（2026-11-28 利用者の決まり）。切る欄は出さず保存にも入れない。 */
export const ALWAYS_ON_FIELD_IDS = new Set(["prefix_cache"]);

/** 保存・登録の後に更新する query。各パネルが既に使っている key も含める（no-op の invalidate を避ける）。 */
export function invalidateRuntimeQueries(qc: QueryClient, runtime: string): Promise<unknown>[] {
  // 既存の各パネルが実際に使っている key だけ（存在しない key を足さない）。
  const keys: string[][] = [["models", "local"], ["runtime-environment"]];
  if (runtime === "llama") keys.push(["llama-status"], ["models", "llama.cpp"]);
  if (runtime === "strata") keys.push(["strata-instances"]);
  if (runtime === "radiance") keys.push(["radiance-instances"]);
  if (runtime === "sly-radiance") keys.push(["sly-radiance-instances"]);
  if (runtime === "maya") keys.push(["maya-s-model"], ["maya-catalog"], ["maya-s-jobs"]);
  return keys.map((key) => qc.invalidateQueries({ queryKey: key }));
}

export function useRuntimeCapabilities(runtime: string) {
  return useQuery({
    queryKey: ["runtime-capabilities", runtime],
    queryFn: getRuntimeCapabilities,
    staleTime: 5 * 60 * 1000,
    select: (data) => data.runtimes[runtime],
  });
}

export function capabilityDefaults(cap: RuntimeCapabilities | undefined | null): SettingValues {
  const out: SettingValues = {};
  for (const section of cap?.sections ?? []) {
    for (const field of section.fields) {
      if (ALWAYS_ON_FIELD_IDS.has(field.id) || field.type === "ref") continue;
      out[field.id] = field.default;
    }
  }
  return out;
}

export function sectionFields(cap: RuntimeCapabilities | undefined | null, sectionId: string): CapabilityField[] {
  return cap?.sections.find((section) => section.id === sectionId)?.fields ?? [];
}

/** 初期値（登録済みの値）・親が管理する値（ファイル選択で決まる path / alias など）を capability の既定へ重ねる。 */
export function mergeSettingValues(
  cap: RuntimeCapabilities | undefined | null,
  initial: SettingValues | undefined | null,
  controlled: SettingValues | undefined | null,
): SettingValues {
  const merged = capabilityDefaults(cap);
  for (const [key, value] of Object.entries(initial ?? {})) {
    if (value !== undefined) merged[key] = value;
  }
  for (const [key, value] of Object.entries(controlled ?? {})) {
    if (value !== undefined) merged[key] = value;
  }
  return merged;
}

/** 保存 API へ渡す本体。常時オンの欄・保存 API に無い ref は入れない。
 *
 *  capability に無い既存値も `save_keys`（保存 API の引数一覧）にあれば残す。旧フォームの
 *  引数を共通 UI へ移す途中で値が消えるのを防ぐため（extra="forbid" の backend に
 *  合わせ、それ以外のキーは絶対に送らない）。`initial` は編集中の値より優先しない。
 */
export function buildSaveBody(
  cap: RuntimeCapabilities, values: SettingValues, initial?: SettingValues | null,
): SettingValues {
  const allowed = cap.save_keys ?? [];
  const body: SettingValues = {};
  const fieldIds = new Set(cap.sections.flatMap((section) => section.fields.map((field) => field.id)));
  // capability に欄が無い既存値（温度など）は、initial より編集中の values を優先する。
  // そうしないと親が値を書き換えても initial の古い値が土台として残ってしまう。
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && allowed.includes(key) && !fieldIds.has(key)
      && !ALWAYS_ON_FIELD_IDS.has(key)) body[key] = value;
  }
  for (const [key, value] of Object.entries(initial ?? {})) {
    if (value !== undefined && allowed.includes(key) && body[key] === undefined
      && !ALWAYS_ON_FIELD_IDS.has(key)) body[key] = value;
  }
  for (const section of cap.sections) {
    for (const field of section.fields) {
      if (ALWAYS_ON_FIELD_IDS.has(field.id)) continue;
      const raw = values[field.id] !== undefined ? values[field.id] : initial?.[field.id];
      if (raw === undefined) continue;
      // ref は別画面・slot への導線。保存 API が同じ名前を受け付けるときだけ送る
      // （Maya の setup_id/folder など）。
      if (field.type === "ref" && !allowed.includes(field.id)) continue;
      body[field.id] = coerceField(field, raw, contextLimit(values));
    }
  }
  return body;
}

export function coerceField(field: CapabilityField, raw: unknown, limit?: number | null): unknown {
  if (field.type === "bool") return Boolean(raw);
  if (field.type === "int" || field.type === "float") {
    // null は「自動」「既定」を表す保存 API の値（speculative_tokens・kv_resident など）。
    // Number(null) === 0 で上書きすると意味が変わるのでそのまま通す。
    if (raw === null) return null;
    const num = typeof raw === "number" ? raw : Number(raw);
    if (!Number.isFinite(num)) return field.default;
    const low = field.min ?? -Infinity;
    // ファイルから読んだモデルの上限で頭を締める（API の上限は 1M でも GGUF は 32K など）。
    const high = Math.min(field.max ?? Infinity, contextField(limit, field));
    const clamped = Math.min(high, Math.max(low, num));
    return field.type === "int" ? Math.round(clamped) : clamped;
  }
  return raw;
}

function contextField(limit: number | null | undefined, field: CapabilityField): number {
  if (limit === null || limit === undefined || !Number.isFinite(limit)) return Infinity;
  return field.id === "ctx_size" || field.id === "context" ? limit : Infinity;
}

/** ファイルのメタデータから読んだコンテキスト上限（保存 API には渡さない補助値）。 */
export function contextLimit(values: SettingValues | undefined | null): number | null {
  const raw = values?.context_limit;
  const num = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(num) && num > 0 ? num : null;
}

/** 選択肢（select）か、コンテキスト長のよく使う値（int）を返す。無ければスライダー/+expert の数値入力。 */
export function fieldPresets(
  field: CapabilityField, limit?: number | null,
): Array<{ v: string | number; label: string }> | null {
  if (field.type === "select" && field.choices?.length) {
    if (field.id === "context" && typeof field.choices[0] === "string") {
      // Strata は "<N>k"。モデルの上限が分かっていればそれより長い選択肢を出さない。
      const ceiling = limit ?? Infinity;
      const choices = field.choices.filter((choice) => {
        const k = /^([0-9]+)k$/.exec(String(choice));
        return !k || Number(k[1]) * 1024 <= ceiling;
      });
      if (choices.length) return choices.map((choice) => ({ v: choice, label: `${choice}` }));
    }
    return field.choices.map((choice) => ({
      v: choice,
      label: choice === "" ? "モデルの既定" : `${choice}`,
    }));
  }
  if (field.type === "int" && (field.id === "ctx_size" || field.id === "context")) {
    const high = Math.min(field.max ?? Infinity, contextField(limit, field));
    const presets = [8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576]
      .filter((v) => v >= (field.min ?? 0) && v <= high)
      .map((v) => ({ v, label: v.toLocaleString() }));
    // llama.cpp の 0 は「GGUF のメタデータに合わせる」。数値の自由入力は expert だけ。
    if ((field.min ?? 0) === 0) presets.unshift({ v: 0, label: "モデルの上限に合わせる" });
    return presets.length ? presets : null;
  }
  return null;
}

/** ファイルの inspect 結果から、モデル上限とコンテキストの初期値を作る。
 *  上限そのものは保存しない補助値（context_limit）として渡し、共通設定側で選択肢を締める。 */
export function applyInspectValues(
  values: SettingValues, metadata: Record<string, unknown>, runtime: string,
): SettingValues {
  const raw = metadata.context_length ?? metadata.max_position_embeddings ?? metadata.n_ctx;
  const num = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(num) || num < 4096) return values;
  const out: SettingValues = { ...values, context_limit: num };
  if (runtime === "strata") {
    const k = Math.floor(num / 1024);
    if (k >= 4 && k <= 1024) out.context = `${k}k`;
  } else {
    const ctx = Math.max(4096, Math.min(num, 1_048_576));
    out.context = ctx;
    out.ctx_size = ctx;
  }
  return out;
}

/** 既存インスタンスと被らないポートを選ぶ（追加の取得・起動はしない）。 */
export function nextPort(base: number, used: number[]): number {
  const taken = new Set(used.filter((v) => Number.isFinite(v)));
  let port = Math.max(base, ...taken);
  while (taken.has(port)) port += 1;
  return port;
}

export function displayValue(field: CapabilityField, value: unknown): string {
  if (value === undefined || value === null || value === "") return "未設定";
  if (field.type === "dict" && typeof value === "object") return dictToText(value);
  return String(value);
}

export function dictToText(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${key}=${String(item)}`)
    .join("\n");
}

export function textToDict(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n").map((item) => item.trim()).filter(Boolean)) {
    const at = line.indexOf("=");
    if (at < 0) continue;
    const key = line.slice(0, at).trim();
    if (key) out[key] = line.slice(at + 1).trim();
  }
  return out;
}

/** 欄を出すか。通常は毎回使う欄だけ。細かい数値・引数・環境変数は expert の中だけ。 */
export function isFieldVisible(field: CapabilityField, expert: boolean): boolean {
  if (ALWAYS_ON_FIELD_IDS.has(field.id)) return false;
  if (field.expert && !expert) return false;
  if (field.type === "ref") return false; // 別画面・ファイル選択の導線（slot）で扱う
  return true;
}

export function sectionLabels(cap: RuntimeCapabilities | undefined | null): CapabilitySection[] {
  const sections = cap?.sections ?? [];
  return [...sections].sort((a, b) => {
    const ai = SECTION_ORDER.indexOf(a.id as (typeof SECTION_ORDER)[number]);
    const bi = SECTION_ORDER.indexOf(b.id as (typeof SECTION_ORDER)[number]);
    return (ai < 0 ? SECTION_ORDER.length : ai) - (bi < 0 ? SECTION_ORDER.length : bi);
  });
}
