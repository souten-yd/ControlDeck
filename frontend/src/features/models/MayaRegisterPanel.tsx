/** Maya（Project Maya）のモデルを登録する。目録・ファイルからの登録・設定は共通部品へ。
 *
 *  Maya 本体の目録（probe: GET /features/maya-s/catalog）とダウンロード／登録の Job は
 *  そのまま使う。共通目録で取得したフォルダや FilePicker の選択を probe へ渡し、
 *  選んだ setup_id を共通設定のフォームへ入れる。ダウンロードは利用者が
 *  登録の操作を押したときだけ行う。最初のモデル（GLM-5.3-Flash · Maya-S、alias maya-s）は
 *  従来どおり MayaSDetailSheet で扱う。
 */
import { DisplayNameField } from "./DisplayNameField";
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { BottomSheet, ConfirmDialog } from "../../components/ui";
import { FilePicker } from "../../components/FilePicker";
import { IconFolder } from "../../components/icons";
import { useExpert } from "../commander/TeamRoles";
import { CopyText, PresetOrCustom, SettingsGroup, SheetActions, Toggle, primaryButton, secondaryButton } from "./formControls";
import { RuntimeCatalog } from "./RuntimeCatalog";
import { MODEL_FILE_PICKER_SOURCE, RuntimeRegisterFromFile } from "./RuntimeRegisterFromFile";
import { RuntimeSettings } from "./RuntimeSettings";
import type { SettingValues } from "./runtimeSettings";

export interface MayaQuantFile { name: string; path: string; size: number | null; present: boolean; required: boolean; what: string; sha256?: string }
export interface MayaQuant {
  family: "glm" | "deepseek" | "minimax" | "hy4";
  setup_id: string;
  tier: string;
  model: string;
  size_gb: number;
  memory_gb: number;
  published?: boolean;
  alias: string;
  registered: string;
  ready: boolean;
  missing_gb: number;
  ngram_gb?: number;
  hf?: string;
  folder_name?: string;
  folder_error?: string;
  blockers: string[];
  error?: string;
  files?: MayaQuantFile[];
  /** 「ダウンロードして登録」の見積もり（フォルダを選んだとき）。 */
  download_root?: string;
  download_gb?: number;
  free_gb?: number;
  download_files?: string[];
  can_download?: boolean;
}
export interface MayaCatalog { folder: string; memory: { ram_gb: number; vram_gb: number }; quants: MayaQuant[] }

const FAMILY_ORDER: MayaQuant["family"][] = ["glm", "deepseek", "minimax", "hy4"];

export function mayaModelName(model: string, tier: string) {
  return `${model} · ${tier}`;
}

function status(q: MayaQuant, folder: string): { text: string; tone: "ok" | "warn" | "muted" } {
  if (q.registered) return { text: `登録済み（${q.registered}）`, tone: "muted" };
  if (q.published === false) return { text: "まだ公開されていません", tone: "muted" };
  if (q.error) return { text: "一覧を読めません", tone: "warn" };
  if (!folder) return { text: "フォルダを選ぶと確認します", tone: "muted" };
  if (q.folder_error) return { text: "フォルダ名が違います", tone: "warn" };
  if (q.missing_gb > 0 || (q.files ?? []).some((f) => f.required && !f.present)) {
    return { text: q.missing_gb > 0 ? `あと約 ${q.missing_gb} GB 足りません` : "足りないファイルがあります", tone: "warn" };
  }
  if (q.blockers.length) return { text: "準備が足りません", tone: "warn" };
  return { text: "ファイルがそろっています", tone: "ok" };
}

const TONE = {
  ok: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  muted: "text-zinc-500",
};

export function MayaRegisterPanel({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const expert = useExpert();
  const [values, setValues] = useState<SettingValues>({ context: 65536 });
  const [folder, setFolder] = useState("");
  const [typed, setTyped] = useState("");
  const [picker, setPicker] = useState(false);
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const apply = (patch: SettingValues) => setValues((current) => ({ ...current, ...patch }));
  const choose = (setupId: string) => { setSelected(setupId); apply({ setup_id: setupId }); };
  const chooseFolder = (path: string) => {
    setTyped(path); setFolder(path); apply({ folder: path }); setPicker(false);
  };
  // Typing a path checks it after a short pause; the picker checks at once.
  useEffect(() => {
    const t = window.setTimeout(() => {
      const next = typed.trim();
      setFolder(next);
      setValues((current) => ({ ...current, folder: next }));
    }, 600);
    return () => window.clearTimeout(t);
  }, [typed]);
  const { data, isFetching, error } = useQuery({
    queryKey: ["maya-catalog", folder],
    queryFn: () => api<MayaCatalog>(`/features/maya-s/catalog${folder ? `?folder=${encodeURIComponent(folder)}` : ""}`),
    retry: false,
  });
  const quants = data?.quants ?? [];
  const pick = quants.find((q) => q.setup_id === selected);
  const pool = data ? data.memory.ram_gb + data.memory.vram_gb : 0;
  const groups = useMemo(() => FAMILY_ORDER.map((family) => ({
    family, model: quants.find((q) => q.family === family)?.model ?? family,
    rows: quants.filter((q) => q.family === family),
  })).filter((g) => g.rows.length), [quants]);

  const canDownload = Boolean(pick && !pick.ready && !pick.registered && pick.can_download && (pick.download_files ?? []).length
    && !pick.blockers.length);

  // 既存の Job API はそのまま使う。共通設定の保存ボタンから呼ぶため、ここでは
  // トーストと onDone は出さない（共通部品が出す）。
  const download = async (context: number) => {
    if (!pick) return;
    await api<{ job_id: string; alias: string }>("/features/maya-s/models/download-jobs", {
      method: "POST", json: { setup_id: pick.setup_id, folder: data?.folder || folder, context, gpu_index: 0 },
    });
    await qc.invalidateQueries({ queryKey: ["maya-s-jobs"] });
  };
  const register = async (start: boolean, context: number) => {
    if (!pick) return;
    await api<{ job_id: string; alias: string }>("/features/maya-s/models/register-jobs", {
      method: "POST", json: { setup_id: pick.setup_id, folder: data?.folder || folder, context, gpu_index: 0, start },
    });
    await qc.invalidateQueries({ queryKey: ["maya-s-jobs"] });
  };

  // 「登録して起動」は主操作の代替（共通の保存ボタンは登録だけ）。
  const startNow = async () => {
    if (!pick) return;
    setSaving(true);
    try {
      await register(true, Number(values.context ?? 65536));
      show(`${mayaModelName(pick.model, pick.tier)} の登録と起動を始めました。ジョブ一覧で進捗を確認できます`);
      onDone();
    } catch (e) {
      show(e instanceof Error ? e.message : "登録できませんでした", "error");
    } finally { setSaving(false); }
  };

  const save = async (body: SettingValues) => {
    if (!pick) throw new Error("Maya の目録からモデルを選んでください");
    const context = Number(body.context ?? 65536);
    if (pick.ready && !pick.registered) await register(false, context);
    else if (canDownload) await download(context);
    else throw new Error("ファイルが揃っていません。別のフォルダを選んでください");
  };

  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  // 目録・ファイル選択・既存の probe はすべて ①モデルの中へ。②以降は共通部品が出す。
  const modelSlot = <div className="space-y-3">
    <SettingsGroup title="目録から取る" description="HF の候補を取得します（停止・再開できます）。取得したフォルダは Maya の目録で確認します"
      collapsible defaultOpen={false}>
      <RuntimeCatalog runtime="maya" expert={expert} onRegister={(path, entry) => {
        const dir = path.replace(/\/[^/]*$/, "");
        setTyped(dir); setFolder(dir); apply({ folder: dir });
        const match = quants.find((q) => q.setup_id === entry.id);
        choose(match?.setup_id ?? entry.id);
      }} />
    </SettingsGroup>
    <SettingsGroup title="ファイルから登録" description="/data1tb・/data2tb の中から GGUF を選びます。選んだフォルダを Maya の目録で確認します"
      collapsible defaultOpen={false}>
      <RuntimeRegisterFromFile runtime="maya" mode="file" expert={expert}
        onRegistered={(info) => {
          const dir = info.kind === "folder" ? info.path : info.path.replace(/\/[^/]*$/, "");
          setTyped(dir); setFolder(dir); apply({ folder: dir });
        }} />
    </SettingsGroup>
    <SettingsGroup title="Maya の目録" description="保存先のフォルダを選び、量子化を選びます。ファイルがそろっていればそのまま登録、足りなければダウンロードして登録します">
      <div className="flex gap-2">
        <input aria-label="Mayaモデルのフォルダ" value={typed} onChange={(e) => setTyped(e.target.value)}
          placeholder="/data1tb/LLM/GLM-5.3-Flash-Maya" className={`${field} min-w-0 flex-1 font-mono text-xs`} />
        <button type="button" onClick={() => setPicker(true)} aria-label="フォルダを選ぶ"
          className="mt-1 grid min-h-11 min-w-11 place-items-center rounded-xl border border-zinc-300 dark:border-zinc-700"><IconFolder className="h-4 w-4" /></button>
      </div>
      {error && <p role="alert" className="text-xs text-red-600">{error instanceof Error ? error.message : "読めませんでした"}</p>}
      {data && <p className="num text-[11px] text-zinc-500">この PC: RAM {data.memory.ram_gb} GB ＋ VRAM {data.memory.vram_gb} GB ＝ {pool.toFixed(0)} GB</p>}
      {isFetching && !data && <p className="text-xs text-zinc-500">読み込み中…</p>}
      <div role="radiogroup" aria-label="Mayaの量子化" className="space-y-3">
        {groups.map((g) => <div key={g.family}>
          <p className="mb-1 text-xs font-semibold text-zinc-600 dark:text-zinc-300">{g.model}</p>
          <ul className="space-y-1.5">
            {g.rows.map((q) => {
              const s = status(q, data?.folder ?? "");
              const fits = !pool || !q.memory_gb || q.memory_gb <= pool;
              const disabled = Boolean(q.registered) || q.published === false;
              return <li key={q.setup_id}>
                <button type="button" role="radio" aria-checked={selected === q.setup_id} disabled={disabled}
                  onClick={() => choose(q.setup_id)}
                  className={`flex min-h-11 w-full items-start justify-between gap-3 rounded-xl border px-3 py-2 text-left disabled:opacity-50 ${selected === q.setup_id ? "border-accent-500 bg-accent-50/60 dark:bg-accent-950/30" : "border-zinc-200 dark:border-zinc-700"}`}>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{q.tier}</span>
                    <span className={`block text-[11px] ${TONE[s.tone]}`}>{s.text}</span>
                  </span>
                  <span className="num shrink-0 text-right text-[11px] text-zinc-500">
                    {q.size_gb ? `${q.size_gb} GB` : "—"}
                    {q.memory_gb > 0 && <span className={`block ${fits ? "" : "text-amber-700 dark:text-amber-400"}`}>
                      メモリ {q.memory_gb} GB{fits ? "" : "（足りない）"}</span>}
                  </span>
                </button>
              </li>;
            })}
          </ul>
        </div>)}
      </div>
    </SettingsGroup>

    {pick && <SettingsGroup title={mayaModelName(pick.model, pick.tier)} description={`ゲートウェイでの名前: ${pick.alias}`}>
      {pick.family === "hy4" && <p className="text-[11px] text-zinc-500">Tencent Hy License。{pick.ngram_gb
        ? <>n-gram テーブル（約 {pick.ngram_gb} GB、全サイズ共通）は SSD から読み、RAM には載せません。</>
        : null}</p>}
      {pick.folder_error && <p className="text-xs text-amber-700 dark:text-amber-400">{pick.folder_error}</p>}
      {pick.blockers.map((b) => <p key={b} className="text-xs text-amber-700 dark:text-amber-400">{b}</p>)}
      {pick.error && <p className="text-xs text-red-600">{pick.error}</p>}
      {data?.folder && (pick.files ?? []).some((f) => !f.present) && <div className="space-y-1">
        <p className="text-xs font-medium">足りないファイル</p>
        <ul className="space-y-0.5">
          {(pick.files ?? []).filter((f) => !f.present).map((f) => <li key={f.path} className="num flex justify-between gap-2 text-[11px] text-zinc-500">
            <span className="min-w-0 truncate font-mono" title={f.path}>{f.name}{f.required ? "" : "（任意）"}</span>
            <span className="shrink-0">{f.size ? `${(f.size / 1e9).toFixed(1)} GB` : ""}</span>
          </li>)}
        </ul>
      </div>}
      {data?.folder && !pick.ready && !pick.registered && (pick.download_files ?? []).length > 0 && <div className="space-y-1 rounded-xl bg-zinc-50 p-3 text-xs dark:bg-zinc-800/60">
        <p className="font-medium">ダウンロードして登録</p>
        <p className="num text-[11px] text-zinc-600 dark:text-zinc-300">約 {pick.download_gb} GB · 空き {pick.free_gb} GB</p>
        <p className="break-all font-mono text-[11px] text-zinc-500">保存先: {pick.download_root}</p>
        {!pick.can_download && <p className="text-[11px] text-amber-700 dark:text-amber-400">空き容量が足りません。別のフォルダを選んでください。</p>}
      </div>}
      {pick.hf && !pick.ready && <details className="text-[11px]">
        <summary className="min-h-11 cursor-pointer py-2 text-xs text-zinc-500">自分で取得する場合のコマンド</summary>
        <div className="overflow-x-auto rounded-lg bg-zinc-100 p-2 dark:bg-zinc-800"><CopyText text={pick.hf} /></div>
      </details>}
      {pick.ready && !pick.registered && <button type="button" disabled={saving} onClick={() => void startNow()} className={secondaryButton}>
        {saving ? "開始中…" : "登録して起動"}</button>}
    </SettingsGroup>}
  </div>;

  return <div className="space-y-3">
    <RuntimeSettings runtime="maya" expert={expert}
      values={values} onValuesChange={setValues}
      modelSlot={modelSlot} save={save} onDone={onDone}
      note={<p className="text-[11px] text-zinc-500">
        {!pick ? "フォルダを選び、Maya の目録からモデルを選んでください（フォルダを変えると選択は解けます）"
          : pick.ready && !pick.registered ? "ファイルが揃っています。登録には数分〜十数分かかります（エンジンの準備を含む）"
            : canDownload ? `足りないファイルをダウンロードしてから登録します（約 ${pick.download_gb} GB・空き ${pick.free_gb} GB）`
              : "ファイルが揃うと登録できます"}
      </p>} />
    {picker && <FilePicker source={MODEL_FILE_PICKER_SOURCE} mode="dir" title="Maya のモデルのフォルダを選択（/data1tb・/data2tb）"
      initialPath={folder || "/data1tb/LLM"}
      onSelect={chooseFolder} onClose={() => setPicker(false)} />}
  </div>;
}

interface MayaInstanceSettings {
  family: "glm" | "deepseek" | "minimax" | "hy4";
  context: number; parallel: number; gpu_index: number;
  reasoning_effort: "low" | "medium" | "high" | "max"; thinking_budget: number;
  temperature: number; top_p: number; vram_reserve_mib: number;
  on_demand_start: boolean; idle_exclude: boolean; auto_start: boolean; running: boolean;
  dspark?: "on" | "off"; cpu_threads?: number; vram_experts?: number;
  mmap_experts?: "auto" | "on" | "off"; ssd_cache?: string; prefill_chunk?: number;
  /** 画像の読み取り（vision tower を pack に入れたときだけ選べる） */
  vision?: "on" | "off"; vision_available?: boolean;
}

const settingField = "min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
const LIFECYCLE = ["on_demand_start", "idle_exclude", "auto_start"] as const;

/** 登録したモデル（DS-XS など）の調整値。DeepSeek のエンジン（maya-ds4）の起動オプションも扱う。 */
function MayaInstanceSettingsForm({ alias, title }: { alias: string; title: string }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({
    queryKey: ["maya-instance-settings", alias],
    queryFn: () => api<MayaInstanceSettings>(`/features/maya-s/models/${encodeURIComponent(alias)}/settings`),
  });
  const [draft, setDraft] = useState<MayaInstanceSettings | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (data) setDraft(data); }, [data]);
  if (!draft || !data) return <p className="text-xs text-zinc-500">設定を読み込み中…</p>;
  const set = <K extends keyof MayaInstanceSettings>(key: K, value: MayaInstanceSettings[K]) => setDraft({ ...draft, [key]: value });
  const changed = (Object.keys(draft) as (keyof MayaInstanceSettings)[]).filter((k) => k !== "running" && draft[k] !== data[k]);
  const engineChanged = changed.some((k) => !(LIFECYCLE as readonly string[]).includes(k));
  const locked = data.running;   // 起動中はエンジンの設定を変えられない（起動と停止だけ）
  const ds = draft.family === "deepseek";
  const save = async () => {
    setSaving(true);
    try {
      const body = Object.fromEntries(changed.map((k) => [k, draft[k]]));
      const result = await api<MayaInstanceSettings>(`/features/maya-s/models/${encodeURIComponent(alias)}/settings`, { method: "PUT", json: body });
      qc.setQueryData(["maya-instance-settings", alias], result);
      await qc.invalidateQueries({ queryKey: ["models"] });
      show(engineChanged ? `${title} の設定を保存しました（次の起動から効きます）` : "保存しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    } finally {
      setSaving(false);
    }
  };
  const num = (key: "cpu_threads" | "prefill_chunk" | "gpu_index", label: string, max: number, hint: string) => (
    <label className="block text-xs">{label}
      <input type="number" min={0} max={max} value={draft[key] ?? 0} disabled={locked} aria-label={`${title} ${label}`}
        onChange={(e) => set(key, Math.max(0, Math.min(max, Number(e.target.value) || 0)))} className={`mt-1 ${settingField}`} />
      <span className="text-[11px] text-zinc-500">{hint}</span>
    </label>
  );
  return <>
    {locked && <p className="rounded-xl bg-amber-50 p-2.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
      起動中です。起動と停止の設定は変えられます。ほかの設定は、モデルを停止してから変えてください。</p>}
    <SettingsGroup title="基本" description="扱える長さ。長くするほど KV キャッシュに RAM・VRAM を使います">
      <label className="block text-xs">コンテキスト長
        <div className="mt-1"><PresetOrCustom ariaLabel={`${title} コンテキスト長`} value={draft.context} disabled={locked}
          presets={[32768, 65536, 131072, 262144, 524288, 1048576].map((v) => ({ v, label: v.toLocaleString() }))}
          placeholder="65536" onChange={(v) => set("context", Math.max(8192, Math.min(1048576, Number(v ?? 65536))))} /></div>
      </label>
    </SettingsGroup>
    <SettingsGroup title="起動と停止" description="Maya のモデル全体で共通（同時に動く Maya は 1 つ）">
      <Toggle label="使われたときに自動で起動" value={draft.on_demand_start} onChange={(v) => set("on_demand_start", v)}
        hint="チャットやゲートウェイから呼ばれたら起動します（ほかのローカル LLM は先に停止）" />
      <Toggle label="PC起動時に自動で起動" value={draft.auto_start} onChange={(v) => set("auto_start", v)} />
      <Toggle label="アイドルでも止めない（常駐）" value={draft.idle_exclude} onChange={(v) => set("idle_exclude", v)}
        hint="共通設定の「アイドル時に自動アンロード」の対象から外します" />
    </SettingsGroup>
    <SettingsGroup title="思考と生成">
      <label className="block text-xs">思考の深さ（既定）
        <select aria-label={`${title} 思考の深さ`} value={draft.reasoning_effort} disabled={locked}
          onChange={(e) => set("reasoning_effort", e.target.value as MayaInstanceSettings["reasoning_effort"])} className={`mt-1 ${settingField}`}>
          <option value="low">low（最速）</option><option value="medium">medium</option>
          <option value="high">high（推奨）</option><option value="max">max（最も深い・時間がかかる）</option>
        </select>
      </label>
      <label className="block text-xs">思考の上限（トークン）
        <div className="mt-1"><PresetOrCustom ariaLabel={`${title} 思考の上限`} value={draft.thinking_budget} disabled={locked}
          presets={[{ v: 0, label: "エンジン既定" }, { v: 8192, label: "8,192" }, { v: 16384, label: "16,384" }, { v: 32768, label: "32,768" }]}
          placeholder="0" onChange={(v) => set("thinking_budget", Math.max(0, Math.min(131072, Number(v ?? 0))))} /></div>
      </label>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-xs">temperature
          <input type="number" step={0.05} min={0} max={2} value={draft.temperature} disabled={locked} aria-label={`${title} temperature`}
            onChange={(e) => set("temperature", Math.max(0, Math.min(2, Number(e.target.value))))} className={`mt-1 ${settingField}`} />
        </label>
        <label className="block text-xs">top_p
          <input type="number" step={0.01} min={0.01} max={1} value={draft.top_p} disabled={locked} aria-label={`${title} top_p`}
            onChange={(e) => set("top_p", Math.max(0.01, Math.min(1, Number(e.target.value))))} className={`mt-1 ${settingField}`} />
        </label>
      </div>
      <span className="text-[11px] text-zinc-500">要求が値を指定しないときに使います。モデルカードの推奨は 1.0 / 1.0 です。</span>
    </SettingsGroup>
    {ds && <SettingsGroup title="画像の読み取り（vision）" description="画像を読むかどうか（変えたら起動し直すと効きます）">
      <label className="block text-xs">読み取り
        <select aria-label={`${title} 画像の読み取り`} value={draft.vision ?? "off"} disabled={locked || !draft.vision_available}
          onChange={(e) => set("vision", e.target.value as "on" | "off")} className={`mt-1 ${settingField}`}>
          <option value="on">読む</option><option value="off">読まない（文字だけ）</option>
        </select>
        <span className="text-[11px] text-zinc-500">
          {draft.vision_available
            ? "画像の部品の重み（約 1 GB）は RAM に置き、読むときだけ VRAM を約 320 MB 使います。"
            : "画像の部品（vision tower）が入っていません。Maya の設定し直しで入れると選べます。"}
        </span>
      </label>
    </SettingsGroup>}
    {ds && <SettingsGroup title="性能（DeepSeek）" description="エンジン maya-ds4 の起動オプション" collapsible defaultOpen={false}>
      <label className="block text-xs">下書き（DSpark・投機デコード）
        <select aria-label={`${title} 下書き`} value={draft.dspark} disabled={locked}
          onChange={(e) => set("dspark", e.target.value as "on" | "off")} className={`mt-1 ${settingField}`}>
          <option value="on">有効（推奨）</option><option value="off">無効</option>
        </select>
      </label>
      <label className="block text-xs">エキスパートを VRAM に置く割合
        <div className="mt-1"><PresetOrCustom ariaLabel={`${title} VRAM に置く割合`} value={draft.vram_experts ?? 0} disabled={locked}
          presets={[{ v: 0, label: "自動（推奨）" }, { v: 0.25, label: "25%" }, { v: 0.5, label: "50%" }, { v: 0.75, label: "75%" }]}
          placeholder="0" onChange={(v) => set("vram_experts", Math.max(0, Math.min(1, Number(v ?? 0))))} /></div>
        <span className="text-[11px] text-zinc-500">残りは RAM と SSD から読みます。KV キャッシュに VRAM を回したいときは下げます。</span>
      </label>
      <label className="block text-xs">モデルファイルの読み方（mmap）
        <select aria-label={`${title} mmap`} value={draft.mmap_experts} disabled={locked}
          onChange={(e) => set("mmap_experts", e.target.value as "auto" | "on" | "off")} className={`mt-1 ${settingField}`}>
          <option value="auto">自動（推奨）</option><option value="on">メモリに映す（RAM のキャッシュを使う）</option><option value="off">都度読む</option>
        </select>
      </label>
      <label className="block text-xs">SSD キャッシュ
        <select aria-label={`${title} SSD キャッシュ`} value={["off", "auto"].includes(draft.ssd_cache ?? "off") ? draft.ssd_cache : "custom"} disabled={locked}
          onChange={(e) => set("ssd_cache", e.target.value === "custom" ? "32" : e.target.value)} className={`mt-1 ${settingField}`}>
          <option value="off">使わない（既定）</option><option value="auto">自動</option><option value="custom">大きさを指定（GB）</option>
        </select>
        {!["off", "auto"].includes(draft.ssd_cache ?? "off") && (
          <input type="number" min={1} max={9999} value={Number(draft.ssd_cache)} disabled={locked} aria-label={`${title} SSD キャッシュの大きさ`}
            onChange={(e) => set("ssd_cache", String(Math.max(1, Math.min(9999, Number(e.target.value) || 1))))} className={`mt-1 ${settingField}`} />
        )}
        <span className="text-[11px] text-zinc-500">よく使うエキスパートを RAM の置き場に並べ替えて持ちます（試験的）。</span>
      </label>
      <div className="grid gap-2 sm:grid-cols-2">
        {num("cpu_threads", "CPU スレッド数", 256, "0 は自動")}
        {num("prefill_chunk", "先読みの単位（トークン）", 65536, "0 は自動")}
      </div>
    </SettingsGroup>}
    <SettingsGroup title="詳細" collapsible defaultOpen={false}>
      {num("gpu_index", "ROCm GPU 番号", 15, "")}
      <p className="text-[11px] text-zinc-500">VRAM の余白: {draft.vram_reserve_mib.toLocaleString()} MiB（共通設定の「VRAM マージン」で変わります）</p>
    </SettingsGroup>
    <SheetActions>
      <button type="button" onClick={() => void save()} disabled={saving || changed.length === 0 || (locked && engineChanged)} className={primaryButton}>
        {saving ? "保存中…" : "保存"}
      </button>
    </SheetActions>
  </>;
}

/** 追加で登録した Maya のモデルの個別画面（情報と削除）。 */
export function MayaModelSheet({ alias, title, update, onClose }: {
  alias: string; title: string; update?: { gb: number; setupId: string; folder: string }; onClose: () => void;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [confirm, setConfirm] = useState(false);
  const [confirmUpdate, setConfirmUpdate] = useState(false);
  const startUpdate = async () => {
    if (!update) return;
    setConfirmUpdate(false);
    try {
      await api("/features/maya-s/models/download-jobs", {
        method: "POST", json: { setup_id: update.setupId, folder: update.folder, replace: alias },
      });
      await qc.invalidateQueries({ queryKey: ["maya-s-jobs"] });
      show(`${title} の新しい版のダウンロードを始めました。終わると準備し直します（古いファイルは残します）`);
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "更新を始められませんでした", "error");
    }
  };
  const remove = async () => {
    try {
      await api(`/features/maya-s/models/${encodeURIComponent(alias)}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["models"] });
      show("登録を削除しました（モデルのファイルは残しています）");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "削除できませんでした", "error");
    }
  };
  return <BottomSheet title={`${title} · 個別設定`} onClose={onClose} wide>
    <div className="space-y-3">
      <DisplayNameField runtime="maya-s" alias={alias} />
      <SettingsGroup title="モデル">
        <p className="text-xs">ゲートウェイでの名前: <CopyText text={alias} /></p>
        <p className="text-[11px] text-zinc-500">起動・停止は一覧の「ロード」で行います。Maya のモデルは一度に 1 つだけ動きます（ほかの Maya のモデルは先に止まります）。</p>
      </SettingsGroup>
      <MayaInstanceSettingsForm alias={alias} title={title} />
      {update && <SettingsGroup title="新しい版あり" description="Maya の更新で、このモデルのファイルに新しい版が出ました">
        <p className="num text-xs">ダウンロード 約 {update.gb} GB → <span className="break-all font-mono text-[11px]">{update.folder}</span></p>
        <button onClick={() => setConfirmUpdate(true)} className={primaryButton}>更新（ダウンロード {update.gb} GB）</button>
      </SettingsGroup>}
      <div className="flex justify-end">
        <button onClick={() => setConfirm(true)} className={`${secondaryButton} text-red-600`}>登録を削除</button>
      </div>
    </div>
    {confirmUpdate && update && <ConfirmDialog title={`${title} を更新`} danger={false}
      message={`新しい版のファイル 約 ${update.gb} GB を ${update.folder} にダウンロードし、検証して準備し直します。設定（調整値・思考）はそのまま、古いファイルは残します。`}
      confirmLabel="ダウンロードを開始" onConfirm={() => void startUpdate()} onClose={() => setConfirmUpdate(false)} />}
    {confirm && <ConfirmDialog title="登録を削除" message={`${title}（${alias}）の登録を削除します。ダウンロードしたモデルのファイルは残します。`}
      confirmLabel="削除" onConfirm={() => { setConfirm(false); void remove(); }} onClose={() => setConfirm(false)} />}
  </BottomSheet>;
}
