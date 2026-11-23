import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Toggle } from "../models/formControls";
import { useLlmOptions, useNewsChannels, useNewsSettings, useRunDigest, useSaveChannels, useSaveNewsSettings, type LlmOptions, type NewsRuntimeSettings } from "../../api/news";
import { useAuth, useToasts } from "../../stores";
import { Card, field, ghost, primary } from "./settingsUi";
import { sizeText, timeText } from "./ImagegenTiers";

const label = "block text-xs text-zinc-500";

/** モデルの選択肢。ローカル → クラウドの無料（印つき）→ クラウドの有料（警告つき。2026-11-23 利用者「有料モデルをニュースに使っても良い」）。 */
function ModelSelect({ name, value, options, disabled, onChange }: { name: string; value: string; options: LlmOptions | undefined; disabled: boolean; onChange: (v: string) => void }) {
  const known = [...(options?.local ?? []).map((m) => m.id), ...(options?.cloud ?? []).map((m) => m.id)];
  const free = (options?.cloud ?? []).filter((m) => m.free);
  const paid = (options?.cloud ?? []).filter((m) => !m.free);
  return (
    <select aria-label={name} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={`${field} mt-1 w-full`}>
      {!known.includes(value) && <option value={value}>{value}（一覧にありません）</option>}
      <optgroup label="ローカル">{(options?.local ?? [{ id: "auto", name: "auto" }]).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>
      {free.length > 0 && <optgroup label="クラウド（無料）">{free.map((m) => <option key={m.id} value={m.id}>無料 · {m.name}</option>)}</optgroup>}
      {paid.length > 0 && <optgroup label="クラウド（有料）">{paid.map((m) => <option key={m.id} value={m.id}>有料 · {m.name}</option>)}</optgroup>}
    </select>
  );
}

/** 選んだモデルへの案内（クラウドで外部への送信が許可されていない・有料）。 */
function ModelNote({ model, options }: { model: string; options: LlmOptions | undefined }) {
  if (!model.startsWith("cloud:")) return <p className="mt-1 text-[11px] text-zinc-500">ローカルのモデルです。使うときにロードされます（GPU は他の処理と同じ決まりで取り合います）。</p>;
  const found = options?.cloud.find((m) => m.id === model);
  return (
    <div className="mt-1 space-y-1 text-[11px]">
      {found?.free && <p><span className="rounded bg-emerald-600/15 px-1.5 py-0.5 font-semibold text-emerald-700 dark:text-emerald-400">無料</span> <span className="text-zinc-500">題・抜粋・出どころ（公開情報）だけを送ります。</span></p>}
      {found && !found.free && <p role="alert" className="text-amber-700 dark:text-amber-400">有料のモデルです。使った分の費用がかかります（Models の月の上限額が効きます）。</p>}
      {options && !options.egress && (
        <p role="alert" data-testid="egress-missing" className="rounded-lg border border-amber-600/40 bg-amber-500/10 p-2 text-amber-800 dark:text-amber-300">
          外部への送信が許可されていません。このままでは、まとめは作られません（ローカルへは切り替えません）。
          Models の「フロンティアモデル → 外部への送信」で「{options.caller_label}」を許可してください。{" "}
          <Link to="/models" className="font-semibold underline">Models を開く</Link>
        </p>
      )}
    </div>
  );
}

export function NewsRuntimeCards() {
  const { data } = useNewsSettings();
  const channels = useNewsChannels();
  const options = useLlmOptions();
  const save = useSaveNewsSettings();
  const saveChannels = useSaveChannels();
  const runDigest = useRunDigest();
  const toast = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("settings.manage");
  const [draft, setDraft] = useState<NewsRuntimeSettings | null>(null);
  const [interval, setInterval] = useState("");
  useEffect(() => { if (data) setDraft(data.settings); }, [data]);
  useEffect(() => { if (channels.data) setInterval(String(channels.data.interval_minutes)); }, [channels.data]);
  if (!data || !draft || !channels.data) return null;
  const weekdays = data.weekdays;
  const set = (patch: Partial<NewsRuntimeSettings>) => setDraft({ ...draft, ...patch });
  const dirty = JSON.stringify(draft) !== JSON.stringify(data.settings) || interval !== String(channels.data.interval_minutes);
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "保存できませんでした", "error");
  const commit = () => {
    save.mutate(draft, { onSuccess: () => {
      if (interval !== String(channels.data!.interval_minutes))
        saveChannels.mutate({ channels: channels.data!.channels, archive_keep: channels.data!.archive_keep, interval_minutes: Number(interval) }, { onSuccess: () => toast("保存しました", "success"), onError: fail });
      else toast("保存しました", "success");
    }, onError: fail });
  };
  const make = (period: "daily" | "weekly") => runDigest.mutate(period, {
    onSuccess: (r) => toast(r.error && !r.body ? `まとめを作りませんでした: ${r.error}` : r.fallback ? "LLM が使えなかったので、見出しの一覧のまとめを作りました" : "まとめを作りました", r.error && !r.body ? "error" : "success"),
    onError: fail,
  });
  const saveButton = canEdit && <button type="button" onClick={commit} disabled={!dirty || save.isPending || saveChannels.isPending} className={primary}>保存</button>;
  const dis = !canEdit;
  return (
    <>
      <Card id="news-update" title="更新のタイミング" hint={`ニュースのページの取得・ホームのフィード・日刊と週刊のまとめを、別々に決めます。時刻はサーバーの地域の時刻です。${canEdit ? "" : "変更には設定の管理権限が要ります。"}`}>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className={label}>ニュースのページの取得の間隔（分。15〜360）
            <input type="number" aria-label="取得の間隔" min={15} max={360} value={interval} disabled={dis} onChange={(e) => setInterval(e.target.value)} className={`${field} mt-1 w-full`} /></label>
          <label className={label}>ホームのフィードの更新の間隔（分。画面の読み直し。1〜240）
            <input type="number" aria-label="ホームのフィードの更新の間隔" min={1} max={240} value={draft.feed_refresh_minutes} disabled={dis}
              onChange={(e) => set({ feed_refresh_minutes: Number(e.target.value) })} className={`${field} mt-1 w-full`} /></label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Toggle label="日刊のまとめ（今朝のまとめ）を作る" value={draft.digest_daily.enabled} disabled={dis} onChange={(on) => set({ digest_daily: { ...draft.digest_daily, enabled: on } })} />
            <label className={label}>日刊の時刻
              <input type="time" aria-label="日刊の時刻" value={draft.digest_daily.time} disabled={dis || !draft.digest_daily.enabled} onChange={(e) => set({ digest_daily: { ...draft.digest_daily, time: e.target.value } })} className={`${field} mt-1 w-full`} /></label>
          </div>
          <div className="space-y-2">
            <Toggle label="週刊のまとめ（今週のまとめ）を作る" value={draft.digest_weekly.enabled} disabled={dis} onChange={(on) => set({ digest_weekly: { ...draft.digest_weekly, enabled: on } })} />
            <div className="grid grid-cols-2 gap-2">
              <label className={label}>週刊の曜日
                <select aria-label="週刊の曜日" value={draft.digest_weekly.weekday} disabled={dis || !draft.digest_weekly.enabled} onChange={(e) => set({ digest_weekly: { ...draft.digest_weekly, weekday: Number(e.target.value) } })} className={`${field} mt-1 w-full`}>
                  {weekdays.map((w, i) => <option key={w} value={i}>{w}曜日</option>)}</select></label>
              <label className={label}>週刊の時刻
                <input type="time" aria-label="週刊の時刻" value={draft.digest_weekly.time} disabled={dis || !draft.digest_weekly.enabled} onChange={(e) => set({ digest_weekly: { ...draft.digest_weekly, time: e.target.value } })} className={`${field} mt-1 w-full`} /></label>
            </div>
          </div>
        </div>
        <div className="space-y-2">
          <Toggle label="取得しない時間帯を決める" hint="この間は記事を取りに行きません（まとめの時刻には影響しません）" value={draft.quiet.enabled} disabled={dis} onChange={(on) => set({ quiet: { ...draft.quiet, enabled: on } })} />
          {draft.quiet.enabled && (
            <div className="grid grid-cols-2 gap-2">
              <label className={label}>開始<input type="time" aria-label="取得しない時間帯の開始" value={draft.quiet.start} disabled={dis} onChange={(e) => set({ quiet: { ...draft.quiet, start: e.target.value } })} className={`${field} mt-1 w-full`} /></label>
              <label className={label}>終わり<input type="time" aria-label="取得しない時間帯の終わり" value={draft.quiet.end} disabled={dis} onChange={(e) => set({ quiet: { ...draft.quiet, end: e.target.value } })} className={`${field} mt-1 w-full`} /></label>
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {saveButton}
          {canEdit && <>
            <button type="button" onClick={() => make("daily")} disabled={runDigest.isPending} className={ghost}>今すぐ日刊のまとめを作る</button>
            <button type="button" onClick={() => make("weekly")} disabled={runDigest.isPending} className={ghost}>今すぐ週刊のまとめを作る</button>
          </>}
          {runDigest.isPending && <span role="status" className="text-xs text-zinc-500">作っています…</span>}
        </div>
      </Card>

      <Card id="news-llm" title="使う LLM" hint="用途ごとに選べます。ニュースは公開情報なので、OpenRouter の無料のモデルをおすすめします（ローカルのモデルでも構いません）。LLM に送るのは記事の題・抜粋・出どころだけで、チャットの題・好み・ControlDeck の出来事は送りません。">
        <label className={label}>日刊・週刊のまとめ
          <ModelSelect name="まとめのモデル" value={draft.llm.digest_model} options={options.data} disabled={dis} onChange={(v) => set({ llm: { ...draft.llm, digest_model: v } })} /></label>
        <ModelNote model={draft.llm.digest_model} options={options.data} />
        <div className="space-y-2">
          <Toggle label="記事の一言要約と話題の語付け" hint="新しく取った記事に付けます（既定は切）" value={draft.llm.gist_enabled} disabled={dis} onChange={(on) => set({ llm: { ...draft.llm, gist_enabled: on } })} />
          {draft.llm.gist_enabled && <>
            <label className={label}>一言要約のモデル
              <ModelSelect name="一言要約のモデル" value={draft.llm.gist_model} options={options.data} disabled={dis} onChange={(v) => set({ llm: { ...draft.llm, gist_model: v } })} /></label>
            <ModelNote model={draft.llm.gist_model} options={options.data} />
          </>}
        </div>
        <p className="text-[11px] text-zinc-500">LLM が使えないときは、LLM なしの「見出しの一覧」のまとめを作り、印を付けます。外部への送信が許可されていないときは、まとめを作りません。OpenRouter のデータの扱いが厳しい（収集する提供元へ送らない）と、無料のモデルが選ばれないことがあります（Models の設定）。</p>
        {saveButton}
      </Card>

      <Card id="news-imagegen" title="画像の無い記事のイメージ画像（AI）" hint="取った記事に画像が無いとき、見出しからイメージ画像を作ります。ニュース専用のモデルを、この PC の CPU と RAM だけで動かし（GPU は使いません）、作り終えたらすぐ解放します。小・中・大で、速さと絵の良さを選べます。人物と文字は描かせない言い回しにしています。画面には「AI 生成のイメージ」と出ます。">
        <Toggle label="画像の無い記事に AI でイメージ画像を作る" hint="既定は切。取得のあと、新しい記事から順にまとめて作ります" value={draft.imagegen.enabled} disabled={dis} onChange={(on) => set({ imagegen: { ...draft.imagegen, enabled: on } })} />
        <fieldset className="space-y-2" disabled={dis || !draft.imagegen.enabled}>
          <legend className={label}>モデルの精度（高いほど絵は良くなりますが、遅く、容量・RAM も増えます）</legend>
          <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="画像生成のモデルの精度">
            {(data.imagegen_tiers ?? []).map((t) => {
              const on = draft.imagegen.tier === t.id;
              return (
                <label key={t.id} data-testid={`imagegen-tier-option-${t.id}`}
                  className={`block cursor-pointer rounded-xl border p-2.5 text-xs ${on ? "border-accent-600 bg-accent-50 dark:bg-accent-600/10" : "border-zinc-200 dark:border-zinc-700"}`}>
                  <span className="flex items-center gap-2 text-sm font-semibold">
                    <input type="radio" name="imagegen-tier" value={t.id} checked={on} onChange={() => set({ imagegen: { ...draft.imagegen, tier: t.id } })} className="h-4 w-4 accent-accent-600" />
                    {t.label}{t.id === "small" && <span className="text-[10px] font-normal text-zinc-500">（速い・既定）</span>}
                  </span>
                  <span className="mt-1 block text-zinc-500">{t.note}</span>
                  <span className="mt-1 block text-zinc-500">1 枚 {timeText(t.time_seconds)} · モデル {sizeText(t.megabytes)}</span>
                  <span className="block text-zinc-500">RAM 約 {t.ram_gb}GB · 1 回 最大 {t.max_per_run} 枚</span>
                  {!t.installed && <span className="mt-1 block font-medium text-amber-700 dark:text-amber-400">未導入</span>}
                </label>
              );
            })}
          </div>
        </fieldset>
        {draft.imagegen.enabled && (data.imagegen_tiers ?? []).some((t) => t.id === draft.imagegen.tier && !t.installed) && (
          <p role="alert" data-testid="imagegen-missing" className="rounded-lg border border-amber-600/40 bg-amber-500/10 p-2 text-[11px] text-amber-800 dark:text-amber-300">
            選んだ精度のモデルが未導入です。このままでは何も作りません。機能と拡張の「ニュースの画像生成（CPU）」で、この精度のモデルを導入してください。{" "}
            <Link to="/settings/features#feature-news-imagegen" className="font-semibold underline">機能と拡張を開く</Link>
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <label className={label}>使う CPU スレッド数（1〜16。低い優先度で動かします）
            <input type="number" aria-label="画像生成のスレッド数" min={1} max={16} value={draft.imagegen.threads} disabled={dis || !draft.imagegen.enabled}
              onChange={(e) => set({ imagegen: { ...draft.imagegen, threads: Number(e.target.value) } })} className={`${field} mt-1 w-full`} /></label>
          <label className={label}>1 回の取得で作る枚数の上限（1〜100。精度ごとの上限も効きます）
            <input type="number" aria-label="画像生成の件数" min={1} max={100} value={draft.imagegen.max_per_run} disabled={dis || !draft.imagegen.enabled}
              onChange={(e) => set({ imagegen: { ...draft.imagegen, max_per_run: Number(e.target.value) } })} className={`${field} mt-1 w-full`} /></label>
        </div>
        {saveButton}
      </Card>

      <Card id="news-rag" title="検索（RAG）への蓄積" hint="取った記事を知識基盤（分野「ニュース」。公開情報）へ貯め、Knowledge の横断検索・AI アシスタントの知識の道具・ニュースの「意味で探す」に使います。ニュースのデータベース（news.sqlite3）とは別のファイルです。">
        <Toggle label="取った記事を RAG に貯める" hint="題・抜粋・出どころ・URL・日付・チャンネル・話題の語を 1 記事 1 文書に（同じ URL は更新）" value={draft.rag.enabled} disabled={dis} onChange={(on) => set({ rag: { ...draft.rag, enabled: on } })} />
        <Toggle label="本文も取り込む" hint="記事のページを読んで足します（外へ通信します。1 回の取得で 30 件まで。既定は切）" value={draft.rag.fulltext} disabled={dis || !draft.rag.enabled} onChange={(on) => set({ rag: { ...draft.rag, fulltext: on } })} />
        <label className={label}>古い記事をニュースのデータベースから消すとき
          <select aria-label="古い記事を消すときの RAG" value={draft.rag.on_prune} disabled={dis} onChange={(e) => set({ rag: { ...draft.rag, on_prune: e.target.value as "keep" | "delete" } })} className={`${field} mt-1 w-full`}>
            <option value="keep">RAG には残す（アーカイブの串刺し検索に使える）</option>
            <option value="delete">RAG からも一緒に消す</option>
          </select></label>
        {saveButton}
      </Card>
    </>
  );
}
