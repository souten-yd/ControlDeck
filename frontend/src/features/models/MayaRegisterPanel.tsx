/** Maya（Project Maya）のモデルを、手元にあるファイルから登録する。
 *
 * ここではダウンロードしない。足りないファイルは、取得するコマンドを見せるだけ。
 * 最初のモデル（GLM-5.3-Flash · Maya-S、alias maya-s）は従来どおり MayaSDetailSheet で扱う。 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { BottomSheet, ConfirmDialog } from "../../components/ui";
import { FilePicker } from "../../components/FilePicker";
import { IconFolder } from "../../components/icons";
import { CopyText, PresetOrCustom, SettingsGroup, SheetActions, primaryButton, secondaryButton } from "./formControls";

export interface MayaQuantFile { name: string; path: string; size: number | null; present: boolean; required: boolean; what: string }
export interface MayaQuant {
  family: "glm" | "deepseek" | "minimax";
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
  hf?: string;
  folder_name?: string;
  folder_error?: string;
  blockers: string[];
  error?: string;
  files?: MayaQuantFile[];
}
export interface MayaCatalog { folder: string; memory: { ram_gb: number; vram_gb: number }; quants: MayaQuant[] }

const FAMILY_ORDER: MayaQuant["family"][] = ["glm", "deepseek", "minimax"];
const CTX = [32768, 65536, 131072, 262144, 524288, 1048576].map((v) => ({ v, label: v.toLocaleString() }));

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
  const [folder, setFolder] = useState("");
  const [typed, setTyped] = useState("");
  const [picker, setPicker] = useState(false);
  const [selected, setSelected] = useState("");
  const [context, setContext] = useState(65536);
  const [saving, setSaving] = useState(false);
  // Typing a path checks it after a short pause; the picker checks at once.
  useEffect(() => {
    const t = window.setTimeout(() => setFolder(typed.trim()), 600);
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

  const register = async (start: boolean) => {
    if (!pick) return;
    setSaving(true);
    try {
      const job = await api<{ job_id: string; alias: string }>("/features/maya-s/models/register-jobs", {
        method: "POST", json: { setup_id: pick.setup_id, folder: data?.folder || folder, context, gpu_index: 0, start },
      });
      await qc.invalidateQueries({ queryKey: ["maya-s-jobs"] });
      show(`${mayaModelName(pick.model, pick.tier)} の登録を始めました（${job.alias}）。ジョブ一覧で進捗を確認できます`);
      onDone();
    } catch (e) {
      show(e instanceof Error ? e.message : "登録できませんでした", "error");
    } finally { setSaving(false); }
  };

  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return <div className="space-y-3">
    <p className="text-xs text-zinc-500">手元にある Maya の量子化ファイルを登録します。ここではダウンロードしません。足りないファイルは取得のコマンドを出します。</p>
    <SettingsGroup title="フォルダ" description="hf download の --local-dir に指定したフォルダ">
      <div className="flex gap-2">
        <input aria-label="Mayaモデルのフォルダ" value={typed} onChange={(e) => setTyped(e.target.value)}
          placeholder="/data1tb/LLM/GLM-5.3-Flash-Maya" className={`${field} min-w-0 flex-1 font-mono text-xs`} />
        <button type="button" onClick={() => setPicker(true)} aria-label="フォルダを選ぶ"
          className="mt-1 grid min-h-11 min-w-11 place-items-center rounded-xl border border-zinc-300 dark:border-zinc-700"><IconFolder className="h-4 w-4" /></button>
      </div>
      {error && <p role="alert" className="text-xs text-red-600">{error instanceof Error ? error.message : "読めませんでした"}</p>}
      {data && <p className="num text-[11px] text-zinc-500">この PC: RAM {data.memory.ram_gb} GB ＋ VRAM {data.memory.vram_gb} GB ＝ {pool.toFixed(0)} GB</p>}
    </SettingsGroup>

    <SettingsGroup title="量子化" description="モデル · 段階（XS・S・M・L は専門家の重みのビット数）">
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
                  onClick={() => setSelected(q.setup_id)}
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
      {pick.hf && !pick.ready && <div className="space-y-1 text-[11px]">
        <p className="text-xs font-medium">取得のコマンド（ターミナルで実行）</p>
        <div className="overflow-x-auto rounded-lg bg-zinc-100 p-2 dark:bg-zinc-800"><CopyText text={pick.hf} /></div>
      </div>}
      <label className="block text-xs">コンテキスト長（トークン）
        <div className="mt-1"><PresetOrCustom ariaLabel="Mayaコンテキスト" value={context} presets={CTX} placeholder="65536"
          onChange={(v) => setContext(Math.min(1048576, Math.max(1024, Number(v ?? 65536))))} /></div>
        <span className="text-[11px] text-zinc-500">KV は RAM へ逃がせます。長いほど RAM を使います。</span>
      </label>
    </SettingsGroup>}

    <SheetActions note={pick && !pick.ready ? "ファイルがそろうと登録できます" : "登録には数分〜十数分かかります（エンジンの準備を含む）"}>
      <button disabled={!pick?.ready || saving} onClick={() => void register(true)} className={secondaryButton}>登録して起動</button>
      <button disabled={!pick?.ready || saving} onClick={() => void register(false)} className={primaryButton}>{saving ? "開始中…" : "登録"}</button>
    </SheetActions>
    {picker && <FilePicker mode="dir" title="Maya のモデルのフォルダを選択" initialPath={folder || "/data1tb/LLM"}
      onSelect={(path) => { setTyped(path); setFolder(path); setPicker(false); }} onClose={() => setPicker(false)} />}
  </div>;
}

/** 追加で登録した Maya のモデルの個別画面（情報と削除）。 */
export function MayaModelSheet({ alias, title, onClose }: { alias: string; title: string; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [confirm, setConfirm] = useState(false);
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
      <SettingsGroup title="モデル" description="Maya の設定は登録時のセットアップで決まります">
        <p className="text-xs">ゲートウェイでの名前: <CopyText text={alias} /></p>
        <p className="text-[11px] text-zinc-500">起動・停止は一覧の「ロード」で行います。Maya のモデルは一度に 1 つだけ動きます（ほかの Maya のモデルは先に止まります）。</p>
      </SettingsGroup>
      <SheetActions>
        <button onClick={() => setConfirm(true)} className={`${secondaryButton} text-red-600`}>登録を削除</button>
      </SheetActions>
    </div>
    {confirm && <ConfirmDialog title="登録を削除" message={`${title}（${alias}）の登録を削除します。ダウンロードしたモデルのファイルは残します。`}
      confirmLabel="削除" onConfirm={() => { setConfirm(false); void remove(); }} onClose={() => setConfirm(false)} />}
  </BottomSheet>;
}
