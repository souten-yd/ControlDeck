import { useSearchParams } from "react-router-dom";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Toggle } from "../models/formControls";
import { DEFAULT_PREFS, LEGACY_DESIGNS, useNewsChannels, useNewsPrefs, useSaveChannels, useSavePrefs, type ChannelsConfig, type NewsChannel, type NewsDesign, type NewsDisplay, type NewsPrefs, type NewsTaste } from "../../api/news";
import { useAuth, useToasts } from "../../stores";
import { NewsSourcesSettings } from "./NewsSourcesSettings";
import { NewsRuntimeCards } from "./NewsRuntimeSettings";
import { NewsHubCard } from "./NewsHubSettings";
import { Card, field, ghost, primary } from "./settingsUi";

/** 語の入力（Enter か読点・カンマで追加、× で外す）。 */
export function TermsInput({ label, value, onChange, placeholder, disabled }: { label: string; value: string[]; onChange: (v: string[]) => void; placeholder?: string; disabled?: boolean }) {
  const [draft, setDraft] = useState("");
  const commit = (text: string) => {
    const words = text.split(/[,、\n]/).map((w) => w.trim()).filter(Boolean);
    if (words.length) onChange([...value, ...words.filter((w) => !value.some((v) => v.toLowerCase() === w.toLowerCase()))]);
    setDraft("");
  };
  return (
    <div>
      <span className="text-xs font-medium text-zinc-500">{label}</span>
      <ul className="mt-1 flex flex-wrap gap-1.5">
        {value.map((w) => (
          <li key={w} className="flex items-center gap-1 rounded-full border border-zinc-200 py-0.5 pl-3 pr-1 text-xs dark:border-zinc-700">
            {w}{!disabled && <button type="button" aria-label={`${label}から「${w}」を外す`} onClick={() => onChange(value.filter((v) => v !== w))} className="grid h-7 w-7 place-items-center rounded-full text-zinc-400 hover:text-red-500">×</button>}
          </li>
        ))}
      </ul>
      {!disabled && <input aria-label={`${label}を追加`} value={draft} placeholder={placeholder ?? "入力して Enter"} onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(draft); } }} onBlur={() => draft.trim() && commit(draft)} className={`${field} mt-1.5 w-full`} />}
    </div>
  );
}

const LANG_LABEL = { ja: "日本語", en: "英語", both: "日本語と英語" } as const;

function ChannelEditor({ channel, index, total, kinds, palette, disabled, onChange, onMove, onRemove }: {
  channel: NewsChannel; index: number; total: number; kinds: Record<string, string>; palette: string[]; disabled: boolean;
  onChange: (c: NewsChannel) => void; onMove: (d: -1 | 1) => void; onRemove: () => void;
}) {
  const set = (patch: Partial<NewsChannel>) => onChange({ ...channel, ...patch });
  return (
    <li data-channel-editor className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" style={{ borderLeft: `6px solid ${channel.color}` }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-bold tracking-widest text-zinc-500">CH {String(index + 1).padStart(2, "0")}</span>
        <input aria-label={`CH ${index + 1} の名前`} value={channel.name} disabled={disabled} onChange={(e) => set({ name: e.target.value })} className={`${field} min-w-0 flex-1`} />
        <input type="color" aria-label={`CH ${index + 1} の色`} value={channel.color} disabled={disabled} onChange={(e) => set({ color: e.target.value })} className="h-10 w-12 shrink-0 rounded-lg border border-zinc-300 bg-transparent p-1 dark:border-zinc-700" />
        {!disabled && (
          <span className="flex gap-1">
            <button type="button" aria-label={`${channel.name} を上へ`} disabled={index === 0} onClick={() => onMove(-1)} className={ghost}>↑</button>
            <button type="button" aria-label={`${channel.name} を下へ`} disabled={index === total - 1} onClick={() => onMove(1)} className={ghost}>↓</button>
            <button type="button" aria-label={`${channel.name} を削除`} onClick={onRemove} className={`${ghost} text-red-600`}>削除</button>
          </span>
        )}
      </div>
      {!disabled && <div className="mt-2 flex flex-wrap gap-1.5" aria-label="色の候補">{palette.map((c) => (
        <button key={c} type="button" aria-label={`色 ${c}`} onClick={() => set({ color: c })} className="h-6 w-6 rounded-full border border-zinc-300 dark:border-zinc-600" style={{ background: c, outline: c === channel.color ? "2px solid currentColor" : undefined }} />))}</div>}
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <TermsInput label={`${channel.name} の含める語`} value={channel.include} disabled={disabled} onChange={(v) => set({ include: v })} placeholder="例: llama.cpp" />
        <TermsInput label={`${channel.name} の外す語`} value={channel.exclude} disabled={disabled} onChange={(v) => set({ exclude: v })} placeholder="例: 広告" />
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <label className="text-xs text-zinc-500">言語（検索）
          <select value={channel.lang} disabled={disabled} onChange={(e) => set({ lang: e.target.value as NewsChannel["lang"] })} className={`${field} mt-1 w-full`}>
            {Object.entries(LANG_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></label>
        <label className="text-xs text-zinc-500">1 チャンネルの件数
          <input type="number" min={3} max={30} value={channel.limit} disabled={disabled} onChange={(e) => set({ limit: Number(e.target.value) })} className={`${field} mt-1 w-full`} /></label>
      </div>
      <fieldset className="mt-3"><legend className="text-xs text-zinc-500">ソースの絞り込み（記事の種類。無ければすべて）</legend>
        <div className="mt-1 flex flex-wrap gap-1.5">{Object.entries(kinds).map(([k, l]) => (
          <button key={k} type="button" disabled={disabled} aria-pressed={channel.kinds.includes(k)} onClick={() => set({ kinds: channel.kinds.includes(k) ? channel.kinds.filter((x) => x !== k) : [...channel.kinds, k] })}
            className={`min-h-9 rounded-full border px-3 text-xs ${channel.kinds.includes(k) ? "border-accent-600 bg-accent-600 text-white" : "border-zinc-300 dark:border-zinc-700"}`}>{l}</button>))}</div></fieldset>
      <div className="mt-3"><Toggle label="語で検索して取る" hint="Google ニュース・Hacker News・フォーラム・Reddit（キーがあれば）を、含める語で検索します" value={channel.search} disabled={disabled} onChange={(on) => set({ search: on })} /></div>
    </li>
  );
}

/** チャンネル（検索テーマ）の編集。保存は「保存」ボタン（形の検査はサーバー）。変更は settings.manage の人だけ。 */
function ChannelsCard() {
  const { data } = useNewsChannels();
  const save = useSaveChannels();
  const toast = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("settings.manage");
  const [draft, setDraft] = useState<ChannelsConfig | null>(null);
  useEffect(() => { if (data) setDraft({ channels: data.channels, archive_keep: data.archive_keep, interval_minutes: data.interval_minutes }); }, [data]);
  if (!data || !draft) return null;
  const dirty = JSON.stringify(draft) !== JSON.stringify({ channels: data.channels, archive_keep: data.archive_keep, interval_minutes: data.interval_minutes });
  const update = (index: number, c: NewsChannel) => setDraft({ ...draft, channels: draft.channels.map((x, i) => (i === index ? c : x)) });
  const move = (index: number, d: -1 | 1) => {
    const list = [...draft.channels];
    [list[index], list[index + d]] = [list[index + d], list[index]];
    setDraft({ ...draft, channels: list });
  };
  const add = () => setDraft({ ...draft, channels: [...draft.channels, { id: "", name: "新しいチャンネル", color: data.limits.palette[draft.channels.length % data.limits.palette.length], include: [], exclude: [], kinds: [], lang: "both", limit: 6, search: true }] });
  const commit = () => save.mutate(draft, { onSuccess: () => toast("チャンネルを保存しました", "success"), onError: (e) => toast(e instanceof Error ? e.message : "保存できませんでした", "error") });
  return (
    <Card id="news-channels" title="チャンネル（検索テーマ）" hint={`チャンネルの語で、Google ニュースの検索・Hacker News・フォーラム・Reddit（キーがあれば）を検索して取ります。既存のソースの記事も、語が合えばそのチャンネルに入ります。上から CH 01・CH 02…です。${canEdit ? "" : "変更には設定の管理権限が要ります。"}`}>
      <ul className="space-y-3">
        {draft.channels.map((c, i) => <ChannelEditor key={c.id || `new-${i}`} channel={c} index={i} total={draft.channels.length} kinds={data.kinds} palette={data.limits.palette} disabled={!canEdit}
          onChange={(next) => update(i, next)} onMove={(d) => move(i, d)} onRemove={() => setDraft({ ...draft, channels: draft.channels.filter((_, j) => j !== i) })} />)}
      </ul>
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={add} disabled={draft.channels.length >= data.limits.channels} className={ghost}>＋ チャンネルを追加</button>
          <button type="button" onClick={commit} disabled={!dirty || save.isPending} className={primary}>{save.isPending ? "保存中…" : "チャンネルを保存"}</button>
        </div>
      )}
    </Card>
  );
}

const DESIGNS: { id: NewsDesign; label: string; hint: string }[] = [
  { id: "broadcast", label: "放送局", hint: "ダーク・黄のアクセント・ON AIR のモニターとテロップ" },
  { id: "magazine", label: "マガジン", hint: "白地・太い見出し・紫。左に大きな 1 本、右に LATEST" },
  { id: "newspaper", label: "新聞", hint: "紺の帯・数字の帯・右に時刻つきの速報" },
  { id: "finance", label: "経済紙", hint: "サーモンの地・セリフの見出し・細い罫線" },
  { id: "text", label: "テキスト一覧", hint: "オレンジの帯・番号つきの密な見出し・画像なし" },
];

/** デザインの見本（CSS で描いた縮小図。画像ファイルは使わない）。 */
function DesignThumb({ id }: { id: NewsDesign }) {
  const box = (style: CSSProperties) => <i style={{ display: "block", ...style }} />;
  const line = (w: string, c: string, h = 3) => box({ width: w, height: h, background: c, borderRadius: 1 });
  const frame = (bg: string, children: ReactNode) => <div aria-hidden className="h-24 w-full overflow-hidden rounded-lg border border-zinc-300 p-1.5 dark:border-zinc-600" style={{ background: bg, display: "flex", flexDirection: "column", gap: 4 }}>{children}</div>;
  if (id === "broadcast") return frame("#080a0f", <>
    {box({ height: 5, background: "#f5c518", width: "100%", borderRadius: 1 })}
    <div style={{ display: "flex", gap: 4, flex: 1 }}><div style={{ flex: 5, display: "flex", flexDirection: "column", gap: 3, justifyContent: "center" }}>{line("90%", "#f5f3ee", 5)}{line("70%", "#f5f3ee", 5)}{line("50%", "#9aa0ab")}</div>
      {box({ flex: 6, background: "linear-gradient(135deg,#7c3aed,#f5c518)", border: "2px solid #2a303c", borderRadius: 5 })}</div>
    <div style={{ display: "flex", gap: 3 }}>{[1, 2, 3].map((n) => <div key={n} style={{ flex: 1 }}>{box({ height: 14, background: "#232a36", borderRadius: 2 })}</div>)}</div></>);
  if (id === "magazine") return frame("#ffffff", <>
    <div style={{ display: "flex", gap: 3, alignItems: "center" }}>{line("14%", "#0d0d0f", 2)}<span style={{ color: "#999", fontSize: 7, lineHeight: 1 }}>/</span>{line("14%", "#0d0d0f", 2)}<span style={{ color: "#999", fontSize: 7, lineHeight: 1 }}>/</span>{line("14%", "#0d0d0f", 2)}</div>
    <div style={{ display: "flex", gap: 5, flex: 1 }}><div style={{ flex: 7, display: "flex", flexDirection: "column", gap: 3 }}>{box({ flex: 1, background: "linear-gradient(135deg,#5b2bff,#f472b6)", borderRadius: 2 })}{line("85%", "#0d0d0f", 5)}</div>
      <div style={{ flex: 4, display: "flex", flexDirection: "column", gap: 3, borderTop: "3px solid #5b2bff", paddingTop: 2 }}>{[0, 1, 2].map((n) => <div key={n} style={{ display: "flex", gap: 2 }}>{box({ width: 10, height: 10, background: "#c4b5fd", borderRadius: 1, flex: "0 0 auto" })}<div style={{ flex: 1 }}>{line("100%", "#333", 2)}<div style={{ height: 2 }} />{line("60%", "#333", 2)}</div></div>)}</div></div></>);
  if (id === "newspaper") return frame("#ffffff", <>
    {box({ height: 7, background: "#0a2a5e", borderRadius: 1 })}
    <div style={{ display: "flex", gap: 2 }}>{[0, 1, 2, 3].map((n) => <div key={n} style={{ flex: 1, borderRight: "1px solid #d7dce3" }}>{line("60%", "#17191c", 5)}{box({ height: 2 })}{line("40%", n % 2 ? "#d6336c" : "#1c7ed6", 2)}</div>)}</div>
    <div style={{ display: "flex", gap: 5, flex: 1 }}><div style={{ flex: 8, display: "flex", gap: 3 }}>{box({ flex: 1, background: "linear-gradient(135deg,#0a2a5e,#9aa3b2)", borderRadius: 1 })}<div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>{line("100%", "#17191c", 4)}{line("80%", "#17191c", 4)}{line("90%", "#999", 2)}</div></div>
      <div style={{ flex: 4, borderLeft: "1px solid #d7dce3", paddingLeft: 3, display: "flex", flexDirection: "column", gap: 3 }}>{[0, 1, 2].map((n) => <div key={n} style={{ display: "flex", gap: 2 }}>{line("22%", "#0a2a5e", 2)}{line("70%", "#444", 2)}</div>)}</div></div></>);
  if (id === "finance") return frame("#fff1e5", <>
    <div style={{ display: "flex", gap: 5, borderBottom: "1px solid #e4d3c4", paddingBottom: 3 }}><div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 3 }}>{line("95%", "#33302e", 5)}{line("70%", "#33302e", 5)}{line("85%", "#74706b", 2)}</div>{box({ width: 30, height: 24, background: "linear-gradient(135deg,#990f3d,#f2a58b)" })}</div>
    <div style={{ display: "flex", gap: 4, flex: 1 }}>{[0, 1, 2].map((n) => <div key={n} style={{ flex: 1, borderTop: "1px solid #33302e", paddingTop: 2, display: "flex", flexDirection: "column", gap: 3 }}>{line("90%", "#33302e", 3)}{line("70%", "#74706b", 2)}{line("85%", "#33302e", 3)}{line("60%", "#74706b", 2)}</div>)}</div></>);
  return frame("#f6f6ef", <>
    {box({ height: 8, background: "#ff6600", borderRadius: 1 })}
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>{[1, 2, 3, 4, 5].map((n) => <div key={n} style={{ display: "flex", gap: 3, alignItems: "center" }}><span style={{ fontSize: 6, color: "#828282", width: 7, lineHeight: 1 }}>{n}.</span><div style={{ flex: 1 }}>{line(`${95 - n * 8}%`, "#1b1b1b", 3)}</div></div>)}</div></>);
}

const SELECT = <T extends string>(label: string, value: T, options: [T, string][], onChange: (v: T) => void) => (
  <label className="block text-xs text-zinc-500">{label}
    <select value={value} onChange={(e) => onChange(e.target.value as T)} className={`${field} mt-1 w-full`}>{options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
);

/** 表示（利用者ごと）と好み（利用者ごと）。変更はすぐ保存する。 */
function PrefsCards() {
  const { data } = useNewsPrefs();
  const save = useSavePrefs();
  const prefs: NewsPrefs = data ?? DEFAULT_PREFS;
  const [muteSources, setMuteSources] = useState<string[]>([]);
  useEffect(() => setMuteSources(prefs.taste.mute_sources), [prefs.taste.mute_sources]);
  if (!data) return null;      // 読み込む前に押した操作が、後から届く保存済みの値で巻き戻らないようにする
  const display = (patch: Partial<NewsDisplay>) => save.mutate({ ...prefs, display: { ...prefs.display, ...patch } });
  const taste = (patch: Partial<NewsTaste>) => save.mutate({ ...prefs, taste: { ...prefs.taste, ...patch } });
  const d = prefs.display, t = prefs.taste;
  return (
    <>
      <Card id="news-display" title="表示のデザイン" hint="この設定はあなたのアカウントに保存されます（端末ごとではありません）。">
        <div role="radiogroup" aria-label="デザイン" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {DESIGNS.map((x) => (
            <button key={x.id} type="button" role="radio" aria-checked={(LEGACY_DESIGNS[d.design] ?? d.design) === x.id} onClick={() => display({ design: x.id })}
              data-design-option={x.id} className={`min-h-16 space-y-2 rounded-xl border p-3 text-left ${(LEGACY_DESIGNS[d.design] ?? d.design) === x.id ? "border-accent-600 bg-accent-50 dark:bg-accent-600/15" : "border-zinc-200 dark:border-zinc-700"}`}>
              <DesignThumb id={x.id} /><span className="block text-sm font-semibold">{x.label}</span><span className="block text-[11px] text-zinc-500">{x.hint}</span></button>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {SELECT("文字の大きさ", d.font_size, [["s", "小"], ["m", "ふつう"], ["l", "大"]], (v) => display({ font_size: v }))}
          {SELECT("密度", d.density, [["relaxed", "ゆったり"], ["normal", "ふつう"], ["tight", "ぎっしり"]], (v) => display({ density: v }))}
          {SELECT("列数（PC）", d.columns, [["auto", "自動"], ["1", "1 列"], ["2", "2 列"], ["3", "3 列"]], (v) => display({ columns: v }))}
          <div className="flex items-end gap-2">
            <label className="block text-xs text-zinc-500">アクセント色
              <input type="color" aria-label="アクセント色" value={d.accent || "#f5c518"} onChange={(e) => display({ accent: e.target.value })} className="mt-1 block h-10 w-16 rounded-lg border border-zinc-300 bg-transparent p-1 dark:border-zinc-700" /></label>
            <button type="button" onClick={() => display({ accent: "" })} disabled={!d.accent} className={ghost}>デザインの色に戻す</button>
          </div>
        </div>
        <label className="block text-xs text-zinc-500">ホームに出す記事の数（1〜10）
          <input type="number" aria-label="ホームに出す記事の数" min={1} max={10} value={d.home_items ?? 5}
            onChange={(e) => { const n = Math.round(Number(e.target.value)); if (n >= 1 && n <= 10) display({ home_items: n }); }} className={`${field} mt-1 w-32`} /></label>
        <div className="grid gap-2 sm:grid-cols-2">
          <Toggle label="画像を出す" value={d.images} onChange={(v) => display({ images: v })} />
          <Toggle label="ON AIR（モニター）を出す" value={d.onair} onChange={(v) => display({ onair: v })} />
          <Toggle label="テロップを出す" value={d.ticker} onChange={(v) => display({ ticker: v })} />
          <Toggle label="数字の区画を出す" value={d.numbers} onChange={(v) => display({ numbers: v })} />
          <Toggle label="動きを減らす" hint="テロップ・点滅を止めます（OS の設定でも止まります）" value={d.reduce_motion} onChange={(v) => display({ reduce_motion: v })} />
        </div>
      </Card>
      <Card id="news-taste" title="好み" hint="おすすめの順位と、出さない記事の設定です（あなたのアカウントに保存されます）。">
        <label className="block text-xs text-zinc-500">おすすめの強さ（新しさ重視 ⇔ 好み重視）: <span className="num">{Math.round(t.strength * 100)}%</span>
          <input type="range" aria-label="おすすめの強さ" min={0} max={1} step={0.05} value={t.strength} onChange={(e) => taste({ strength: Number(e.target.value) })} className="mt-1 block w-full" /></label>
        <label className="block text-xs text-zinc-500">好みの半減の日数（過去の操作の重みが半分になる日数）
          <input type="number" aria-label="半減の日数" min={3} max={180} value={t.half_life_days} onChange={(e) => taste({ half_life_days: Number(e.target.value) })} className={`${field} mt-1 w-32`} /></label>
        <div className="grid gap-2 sm:grid-cols-2">
          <Toggle label="チャットの話題を使う" hint="秘密の会話は使いません" value={t.use_chat} onChange={(v) => taste({ use_chat: v })} />
          <Toggle label="既読を薄くする" value={t.dim_read} onChange={(v) => taste({ dim_read: v })} />
        </div>
        <TermsInput label="ミュートの語" value={t.mute_terms} onChange={(v) => taste({ mute_terms: v })} placeholder="題・抜粋にあれば出さない語" />
        <TermsInput label="ミュートのソース" value={muteSources} onChange={(v) => { setMuteSources(v); taste({ mute_sources: v }); }} placeholder="出どころの名前（例: Phoronix）" />
      </Card>
    </>
  );
}

function ArchiveCard() {
  const { data } = useNewsChannels();
  const save = useSaveChannels();
  const toast = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("settings.manage");
  const [keep, setKeep] = useState("");
  useEffect(() => { if (data) setKeep(String(data.archive_keep)); }, [data]);
  if (!data) return null;
  const commit = () => save.mutate({ channels: data.channels, archive_keep: Number(keep), interval_minutes: data.interval_minutes },
    { onSuccess: () => toast("保存しました", "success"), onError: (e) => toast(e instanceof Error ? e.message : "保存できませんでした", "error") });
  return (
    <Card id="news-archive" title="アーカイブ" hint="古い記事は消えずに残ります。上限を超えたときだけ、古いものから消します（☆ 保存した記事は消しません）。">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs text-zinc-500">残す件数（1,000〜200,000）
          <input type="number" aria-label="残す件数" min={1000} max={200000} step={1000} value={keep} disabled={!canEdit} onChange={(e) => setKeep(e.target.value)} className={`${field} mt-1 w-full`} /></label>
      </div>
      {canEdit && <button type="button" onClick={commit} disabled={save.isPending} className={primary}>保存</button>}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-zinc-500">書き出し</span>
        <a href="/api/v1/news/export?format=json" download className={`${ghost} inline-flex items-center`}>JSON</a>
        <a href="/api/v1/news/export?format=csv" download className={`${ghost} inline-flex items-center`}>CSV</a>
      </div>
    </Card>
  );
}

const TABS = [["basic", "基本"], ["sources", "取得先"], ["llm", "LLM"], ["hub", "ニュースハブ"], ["image", "画像"], ["display", "表示"], ["store", "蓄積"]] as const;
type NewsTab = typeof TABS[number][0];

/** 設定 → ニュース。節ごとのタブで 1 つだけ出す（縦に長くしない）。タブは URL の ?tab= に残す。 */
export function NewsSettings() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab");
  const tab: NewsTab = TABS.find(([id]) => id === requested)?.[0] ?? "basic";
  const setTab = (id: NewsTab) => setSearchParams((prev) => {
    const next = new URLSearchParams(prev);
    if (id === "basic") next.delete("tab"); else next.set("tab", id);
    return next;
  });
  return (
    <>
      <div role="tablist" aria-label="ニュースの設定" data-news-tabs className="-mx-4 flex gap-1.5 overflow-x-auto overscroll-x-contain px-4 pb-1 [scrollbar-width:none] md:mx-0 md:flex-wrap md:px-0">
        {TABS.map(([id, name]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`min-h-11 shrink-0 whitespace-nowrap rounded-full border px-3.5 text-sm ${tab === id
              ? "border-accent-500/50 bg-accent-50 font-medium text-accent-700 dark:border-accent-500/40 dark:bg-accent-600/15 dark:text-accent-300"
              : "border-zinc-200 bg-white hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800/70"}`}>{name}</button>
        ))}
      </div>
      {tab === "basic" && <><ChannelsCard /><NewsRuntimeCards part="update" /></>}
      {tab === "sources" && <NewsSourcesSettings />}
      {tab === "llm" && <NewsRuntimeCards part="llm" />}
      {tab === "hub" && <NewsHubCard />}
      {tab === "image" && <NewsRuntimeCards part="imagegen" />}
      {tab === "display" && <PrefsCards />}
      {tab === "store" && <><NewsRuntimeCards part="rag" /><ArchiveCard /></>}
    </>
  );
}
