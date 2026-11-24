import { useMemo, useState } from "react";
import { useNewsPresets, type NewsPreset } from "../../api/news";
import { intervalLabel, keyOf } from "./sourceUtil";

const KIND_NAME: Record<string, string> = { rss: "RSS", github: "Release", codeberg: "Release", discourse: "フォーラム", reddit: "Reddit" };
const btn = "min-h-9 rounded-lg border border-zinc-300 px-2 text-xs dark:border-zinc-700 disabled:opacity-40";
type AddItem = { kind: string; value: string; name: string; interval?: number };
const asItem = (p: NewsPreset): AddItem => ({ kind: p.kind, value: p.value, name: p.name, interval: p.interval || undefined });

/** 目録（backend/app/news/catalog.json + presets.py）から、分野ごと・おすすめをまとめて取得先へ足す。足す・外すの保存はサーバー（/news/sources/manage/add・bulk）。 */
export function PresetPanel({ have, canEdit, busy, onAdd, onRemove }: {
  have: Set<string>; canEdit: boolean; busy: boolean; onAdd: (items: AddItem[]) => void; onRemove: (keys: string[]) => void;
}) {
  const { data } = useNewsPresets();
  const [group, setGroup] = useState("recommended");
  const [showExcluded, setShowExcluded] = useState(false);
  const rows = useMemo(() => {
    if (!data) return [];
    return group === "recommended" ? data.items.filter((p) => p.recommended) : data.items.filter((p) => p.group === group || p.also?.includes(group));
  }, [data, group]);
  if (!data) return <p className="mt-3 text-xs text-zinc-500">読み込み中…</p>;
  const isOn = (p: NewsPreset) => have.has(keyOf(p.kind, p.value));
  const on = rows.filter(isOn).length;
  const recommended = data.items.filter((p) => p.recommended);
  const recTodo = recommended.filter((p) => !isOn(p));
  const groupLabel = (id: string) => data.groups.find((g) => g.id === id)?.label ?? id;
  const hint = group === "recommended" ? "ローカル LLM・量子化・推論エンジン・AMD GPU と ROCm・AI エージェント・音声 AI・オープンソース" : data.groups.find((g) => g.id === group)?.hint;
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-testid="news-presets">
      <p className="text-[11px] leading-relaxed text-zinc-500">無料で読めるものだけを並べています（{data.verified} に、HTTP 200・RSS/Atom として読める・最新の記事が 90 日以内であることを確かめました）。足すと取得に加わります。全部を一度に足すと取得が重くなるので、分野を選んで足してください。</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" disabled={!canEdit || busy || recTodo.length === 0} onClick={() => onAdd(recTodo.map(asItem))} data-testid="news-preset-add-recommended"
          className="min-h-10 rounded-lg bg-accent-600 px-4 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">おすすめをまとめて足す（{recTodo.length} 件）</button>
        <span className="text-[11px] text-zinc-400">全部で {recommended.length} 件のおすすめ</span>
      </div>
      <label className="mt-3 flex items-center gap-2 text-xs">分野
        <select aria-label="分野" data-testid="news-preset-group" value={group} onChange={(e) => setGroup(e.target.value)}
          className="h-10 min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
          <option value="recommended">おすすめ（{recommended.length}）</option>
          {data.groups.map((g) => <option key={g.id} value={g.id}>{g.label}（{g.count}）</option>)}
        </select>
      </label>
      {hint && <p className="mt-1 text-[11px] text-zinc-400">{hint}</p>}
      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-zinc-500">
        <span>{on} / {rows.length} 件を取得中</span>
        {canEdit && (
          <span className="flex gap-1">
            <button type="button" className={btn} disabled={busy || on === rows.length} onClick={() => onAdd(rows.filter((p) => !isOn(p)).map(asItem))}>この分野をすべて加える</button>
            <button type="button" className={btn} disabled={busy || on === 0} onClick={() => onRemove(rows.filter(isOn).map((p) => keyOf(p.kind, p.value)))}>すべて外す</button>
          </span>
        )}
      </div>
      <ul className="mt-2 grid gap-x-4 sm:grid-cols-2" data-testid="news-preset-list">
        {rows.map((p) => (
          <li key={p.id}>
            <label className="flex min-h-11 items-start gap-2 py-1.5 text-xs">
              <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0" checked={isOn(p)} disabled={!canEdit || busy}
                onChange={(e) => (e.target.checked ? onAdd([asItem(p)]) : onRemove([keyOf(p.kind, p.value)]))} />
              <span className="min-w-0">
                <span className="font-medium break-words">{p.name}</span>
                {p.recommended && <span className="ml-1 rounded bg-accent-50 px-1 text-[10px] text-accent-700 dark:bg-accent-600/20 dark:text-accent-200">おすすめ</span>}
                <span className="ml-1 text-zinc-400">{KIND_NAME[p.kind]}{p.lang === "en" ? " · 英語" : ""}{p.interval ? ` · ${intervalLabel(p.interval)}ごと` : ""}</span>
                {p.note && <span className="block text-[11px] text-zinc-400">{p.note}</span>}
                {p.key && <span className="block text-[11px] text-amber-600 dark:text-amber-400">API キーがあるときだけ取得（取得の確認はしていません）</span>}
                {p.stale && <span className="block text-[11px] text-zinc-400">公式の発表（更新は少なめ・最新 {p.latest}）</span>}
                {group === "recommended" && <span className="block text-[11px] text-zinc-400">{groupLabel(p.group)}</span>}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {(data.excluded?.length ?? 0) > 0 && (
        <div className="mt-3 border-t border-zinc-200 pt-2 dark:border-zinc-700">
          <button type="button" className="text-xs text-zinc-500 underline" aria-expanded={showExcluded} onClick={() => setShowExcluded((v) => !v)}>入れなかったもの（{data.excluded?.length} 件）</button>
          {showExcluded && (
            <ul className="mt-2 space-y-1 text-[11px] text-zinc-500" data-testid="news-preset-excluded">
              {data.excluded?.filter((x) => group === "recommended" || x.group === group).map((x) => <li key={x.value} className="break-all"><span className="font-medium">{x.name}</span>: {x.reason}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
