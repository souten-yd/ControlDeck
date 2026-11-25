import { useEffect, useState } from "react";
import { Toggle } from "../models/formControls";
import {
  useConfirmHub, useDisconnectHub, useDiscoverHub, useHubJobsNow, useHubSyncNow, useNewsChannels, useNewsHub, useNewsSettings, usePairHub, useSaveNewsSettings, useSendHubSecret,
  type HubFound, type NewsHubSettings, type NewsHubSource,
} from "../../api/news";
import { useAuth, useToasts } from "../../stores";
import { Card, field, ghost, primary } from "./settingsUi";

const label = "block text-xs text-zinc-500";
const SOURCES: { id: NewsHubSource; name: string; note: string }[] = [
  { id: "local", name: "ローカル", note: "いままでどおり、この PC が自分で集めます（既定）" },
  { id: "hub", name: "ハブ", note: "ハブから受け取ります（自分では集めません）" },
  { id: "auto", name: "自動", note: "ハブが答えるあいだはハブ。続けて 2 回答えなければ自分で集め、答えたらハブへ戻ります" },
];
const KINDS: [string, string][] = [["items", "記事"], ["entities", "物（モデル・組織など）"], ["events", "イベント（同じ出来事の記事のまとまり）"]];
const CATEGORIES: [string, string][] = [["release", "更新"], ["news", "ニュース"], ["model", "モデル"], ["forum", "フォーラム"], ["reddit", "Reddit"]];
const JOB_KINDS: [string, string][] = [["summarize_hq", "重要なイベントの要約（まとめのモデル）"], ["extract_retry", "取り出しのやり直し（一言要約のモデル）"], ["image", "画像の無い重要な記事の画像（CPU の画像生成）"]];
const STATUS_TEXT: Record<string, string> = { done: "成功", failed: "失敗", rejected: "ハブが断った", lease_conflict: "別の係が借りていた" };

function toggleIn(list: string[], value: string, on: boolean): string[] {
  return on ? [...list, value] : list.filter((v) => v !== value);
}

function when(value: string): string {
  return value ? new Date(value).toLocaleString("ja-JP", { hour12: false }) : "まだありません";
}

/** 設定 → ニュース →「QNAP ニュースハブとつなぐ」。出どころ・つなぐ（指紋の見比べ）・鍵を渡す・受け取る範囲・機能の入切・仕事を借りる・診断。 */
export function NewsHubCard() {
  const settings = useNewsSettings();
  const channels = useNewsChannels();
  const hub = useNewsHub();
  const save = useSaveNewsSettings();
  const pair = usePairHub();
  const confirm = useConfirmHub();
  const disconnect = useDisconnectHub();
  const discover = useDiscoverHub();
  const [found, setFound] = useState<{ found: HubFound[]; tried: string[] } | null>(null);
  const sendSecret = useSendHubSecret();
  const syncNow = useHubSyncNow();
  const jobsNow = useHubJobsNow();
  const toast = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("settings.manage");
  const [draft, setDraft] = useState<NewsHubSettings | null>(null);
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");
  const [confirmingCut, setConfirmingCut] = useState(false);
  useEffect(() => { if (settings.data?.settings.hub) setDraft(settings.data.settings.hub); }, [settings.data]);
  if (!settings.data || !draft || !hub.data) return null;
  const st = hub.data;
  const dis = !canEdit;
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings.data.settings.hub);
  const set = (patch: Partial<NewsHubSettings>) => setDraft({ ...draft, ...patch });
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "できませんでした", "error");
  const commit = () => save.mutate({ ...settings.data!.settings, hub: draft }, { onSuccess: () => toast("保存しました", "success"), onError: fail });
  const effective = st.effective_source === "hub" ? "ハブから受け取っています" : "この PC が自分で集めています";
  return (
    <Card id="news-hub" title="QNAP ニュースハブとつなぐ" hint="QNAP の NAS で動くニュースハブ（記事の正本）とつなぎ、記事・物・イベントの差分を受け取ります。ハブの NAS が動いていれば、この PC の電源が切れていてもハブは集め続けます。この画面の設定は、ハブ側の設定とは別です。">
      <div className="space-y-2" data-testid="hub-source">
        <span className={label}>データの出どころ</span>
        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="データの出どころ">
          {SOURCES.map((o) => (
            <label key={o.id} className={`block cursor-pointer rounded-xl border p-2.5 text-xs ${draft.source === o.id ? "border-accent-600 bg-accent-50 dark:bg-accent-600/10" : "border-zinc-200 dark:border-zinc-700"}`}>
              <span className="flex items-center gap-2 text-sm font-semibold">
                <input type="radio" name="hub-source" value={o.id} checked={draft.source === o.id} disabled={dis} onChange={() => set({ source: o.id })} className="h-4 w-4 accent-accent-600" />{o.name}
              </span>
              <span className="mt-1 block text-zinc-500">{o.note}</span>
            </label>
          ))}
        </div>
        <p className="text-xs text-zinc-500" data-testid="hub-effective">いま: <b>{effective}</b>{draft.source !== "local" && !st.verified && "（ハブとつないで、指紋を確かめるまでは自分で集めます）"}</p>
      </div>

      {!st.linked && canEdit && (
        <form className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-testid="hub-pair-form"
          onSubmit={(e) => { e.preventDefault(); pair.mutate({ url, code, name: "ControlDeck" }, { onSuccess: () => { setCode(""); toast("つなぎました。指紋を見比べてください", "success"); }, onError: fail }); }}>
          <p className="text-xs font-semibold">ハブとつなぐ</p>
          <p className="text-[11px] text-zinc-500">ハブの画面の「ControlDeck とつなぐ」で番号（6 桁・10 分有効）を出し、ハブの URL と一緒に入れてください。</p>
          <label className={label}>ハブの URL（ポートを省くと 8787 になります）
            <input aria-label="ハブの URL" inputMode="url" autoComplete="off" placeholder="http://nas.local:8787" value={url} onChange={(e) => setUrl(e.target.value)} className={`${field} mt-1 w-full`} /></label>
          <div className="space-y-2" data-testid="hub-discover">
            <button type="button" className={ghost} disabled={discover.isPending} onClick={() => discover.mutate(undefined, { onSuccess: setFound, onError: fail })}>{discover.isPending ? "探しています…" : "探す"}</button>
            <span className="ml-2 text-[11px] text-zinc-500">この PC と、同じネットワーク（LAN）のハブを探します。見つけても、つなぐには番号と指紋の見比べが要ります。</span>
            {found && found.found.length === 0 && (
              <p className="text-[11px] text-zinc-600 dark:text-zinc-400" role="status" data-testid="hub-discover-none">見つかりませんでした。ハブが LAN に出ているか（待ち受けが 0.0.0.0 か）・発見の応答が入か・ファイアウォールが UDP 8788 を止めていないかを確かめ、ハブの「ControlDeck とつなぐ」に出る URL を入れてください。試した場所: {found.tried.join("、")}</p>
            )}
            {found && found.found.length > 0 && (
              <ul className="space-y-1.5" data-testid="hub-discover-list">
                {found.found.map((h) => (
                  <li key={h.url}>
                    <button type="button" className="block w-full rounded-xl border border-zinc-200 p-2 text-left text-xs hover:border-accent-600 dark:border-zinc-700" onClick={() => setUrl(h.url)}>
                      <span className="block font-semibold">{h.name} <span className="font-normal text-zinc-500">{h.version && `v${h.version}`}</span></span>
                      <span className="block break-all font-mono text-zinc-600 dark:text-zinc-400">{h.url}</span>
                      <span className="block break-all font-mono tabular-nums text-zinc-500">{h.fingerprint ? `指紋 ${h.fingerprint}` : "指紋はつなぐときに出ます"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <label className={label}>つなぐ番号（6 桁）
            <input aria-label="つなぐ番号" inputMode="numeric" autoComplete="off" maxLength={6} placeholder="000000" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className={`${field} mt-1 w-40 tabular-nums`} /></label>
          <button type="submit" disabled={pair.isPending || !url.trim() || code.length !== 6} className={primary}>つなぐ</button>
        </form>
      )}

      {st.linked && !st.verified && (
        <div role="group" aria-label="指紋の確認" className="space-y-2 rounded-xl border border-amber-600/40 bg-amber-500/10 p-3" data-testid="hub-fingerprints">
          <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">指紋を見比べてください（途中のなりすましを防ぐのは、この確認だけです）</p>
          <dl className="space-y-1 text-xs">
            <div><dt className="text-zinc-500">ハブの指紋（ハブの公開鍵から、この PC が計算）</dt><dd className="font-mono text-sm tabular-nums" data-testid="hub-fp-hub">{st.hub_fingerprint}</dd></div>
            <div><dt className="text-zinc-500">この ControlDeck の指紋</dt><dd className="font-mono text-sm tabular-nums" data-testid="hub-fp-mine">{st.my_fingerprint}</dd></div>
          </dl>
          <p className="text-[11px] text-zinc-600 dark:text-zinc-400">ハブの画面の指紋（ハブ側）と、ハブの「つないだ ControlDeck」の一覧の指紋（こちら側）が、それぞれ上と同じなら「同じ」を押します。違うときは「違う」を押し、ハブの画面で相手を取り消してください。</p>
          {canEdit && <div className="flex flex-wrap gap-2">
            <button type="button" className={primary} disabled={confirm.isPending} onClick={() => confirm.mutate(true, { onSuccess: () => toast("使い始めました", "success"), onError: fail })}>同じ（使い始める）</button>
            <button type="button" className={ghost} disabled={confirm.isPending} onClick={() => confirm.mutate(false, { onSuccess: () => toast("つなぎを破棄しました。ハブの画面で相手を取り消してください", "success"), onError: fail })}>違う（破棄する）</button>
          </div>}
        </div>
      )}

      {st.linked && st.verified && (
        <div className="space-y-2 rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-700" data-testid="hub-linked">
          <p><span className="rounded bg-emerald-600/15 px-1.5 py-0.5 font-semibold text-emerald-700 dark:text-emerald-400">つながっています</span> <span className="break-all text-zinc-500">{st.url}</span></p>
          <p className="break-all font-mono tabular-nums text-zinc-500">ハブ {st.hub_fingerprint} ・ この PC {st.my_fingerprint}</p>
          {canEdit && <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={ghost} data-testid="hub-send-secret" disabled={sendSecret.isPending || !st.openrouter_key}
              onClick={() => sendSecret.mutate(undefined, { onSuccess: () => toast("OpenRouter の鍵をハブへ渡しました", "success"), onError: fail })}>OpenRouter の鍵をハブに渡す</button>
            {!confirmingCut ? <button type="button" className={ghost} onClick={() => setConfirmingCut(true)}>つなぎを切る</button> : (
              <span className="flex items-center gap-2" role="alert">
                <span className="text-red-600">保存したトークンと cursor を捨てます。取り込んだ記事は残ります。</span>
                <button type="button" className="min-h-10 rounded-lg border border-red-500 px-3 text-xs font-medium text-red-600" disabled={disconnect.isPending}
                  onClick={() => disconnect.mutate(undefined, { onSuccess: () => { setConfirmingCut(false); toast("切りました。ハブの画面でも相手を取り消してください", "success"); }, onError: fail })}>切る</button>
                <button type="button" className={ghost} onClick={() => setConfirmingCut(false)}>やめる</button>
              </span>
            )}
          </div>}
          <p className="text-[11px] text-zinc-500" data-testid="hub-secret-note">
            鍵は押したときだけ、ハブの公開鍵で封じて送ります（LAN の中でも平文では流しません。値は画面にもログにも出しません）。渡すのは鍵だけです。
            {!st.openrouter_key && " ControlDeck に OpenRouter の鍵が入っていないので、先に設定 → API キーで入れてください。"}
            {st.secret.sent_at && ` 最後に渡した時刻: ${when(st.secret.sent_at)}`}
            {st.secret.error && <span className="text-red-600"> 失敗: {st.secret.error}</span>}
          </p>
        </div>
      )}

      <fieldset className="space-y-2" disabled={dis}>
        <legend className={label}>受け取る範囲</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {KINDS.map(([id, name]) => <label key={id} className="flex min-h-9 items-center gap-1.5"><input type="checkbox" checked={draft.kinds.includes(id)} onChange={(e) => set({ kinds: toggleIn(draft.kinds, id, e.target.checked) })} className="h-4 w-4" />{name}</label>)}
        </div>
        <p className="text-[11px] text-zinc-500">分類（空ならすべて）</p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {CATEGORIES.map(([id, name]) => <label key={id} className="flex min-h-9 items-center gap-1.5"><input type="checkbox" checked={draft.categories.includes(id)} onChange={(e) => set({ categories: toggleIn(draft.categories, id, e.target.checked) })} className="h-4 w-4" />{name}</label>)}
        </div>
        <p className="text-[11px] text-zinc-500">チャンネル（空ならすべて）</p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {(channels.data?.channels ?? []).map((c) => <label key={c.id} className="flex min-h-9 items-center gap-1.5"><input type="checkbox" checked={draft.channels.includes(c.id)} onChange={(e) => set({ channels: toggleIn(draft.channels, c.id, e.target.checked) })} className="h-4 w-4" />{c.name}</label>)}
        </div>
        <label className={label}>差分を取る間隔（分。1〜240）
          <input type="number" min={1} max={240} aria-label="ハブの同期の間隔" value={draft.sync_minutes} onChange={(e) => set({ sync_minutes: Math.max(1, Math.min(240, Number(e.target.value) || 1)) })} className={`${field} mt-1 w-28`} /></label>
        <p className="text-[11px] text-zinc-500">範囲を変えて保存すると、次の同期で最初から取り直します（同じ記事は二重になりません）。</p>
      </fieldset>

      <div className="space-y-2">
        <span className={label}>ハブから受け取った記事への機能</span>
        <Toggle label="画像の無い記事にイメージ画像を作る（CPU）" hint="切にすると、ハブの記事の画像は作りません（自分で集めた記事は「画像の無い記事のイメージ画像」の設定のまま）" value={draft.features.imagegen} disabled={dis} onChange={(on) => set({ features: { ...draft.features, imagegen: on } })} />
        <Toggle label="リーダー（記事を ControlDeck の中で読む）" hint="切にすると、ハブの記事のリーダーは開きません" value={draft.features.reader} disabled={dis} onChange={(on) => set({ features: { ...draft.features, reader: on } })} />
        <Toggle label="知識（RAG）への取り込み" hint="「検索（RAG）への蓄積」が入のときだけ、ハブの記事も知識の分野「ニュース」へ入れます" value={draft.features.rag} disabled={dis} onChange={(on) => set({ features: { ...draft.features, rag: on } })} />
      </div>

      <div className="space-y-2" data-testid="hub-jobs-settings">
        <Toggle label="ハブの仕事を借りて処理する" hint="ハブの NAS には重い LLM・画像生成が無いので、この PC の既存の処理を貸します（既定は切）" value={draft.jobs.enabled} disabled={dis} onChange={(on) => set({ jobs: { ...draft.jobs, enabled: on } })} />
        {draft.jobs.enabled && <>
          <fieldset className="space-y-1" disabled={dis}>
            <legend className={label}>借りる種類</legend>
            {JOB_KINDS.map(([id, name]) => <label key={id} className="flex min-h-9 items-center gap-1.5 text-xs"><input type="checkbox" checked={draft.jobs.kinds.includes(id)} onChange={(e) => set({ jobs: { ...draft.jobs, kinds: toggleIn(draft.jobs.kinds, id, e.target.checked) } })} className="h-4 w-4" />{name}</label>)}
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={label}>1 回に借りる件数（1〜20）
              <input type="number" min={1} max={20} aria-label="仕事を借りる件数" value={draft.jobs.max_per_run} disabled={dis} onChange={(e) => set({ jobs: { ...draft.jobs, max_per_run: Math.max(1, Math.min(20, Number(e.target.value) || 1)) } })} className={`${field} mt-1 w-full`} /></label>
            <label className={label}>借りる間隔（分。1〜240）
              <input type="number" min={1} max={240} aria-label="仕事を借りる間隔" value={draft.jobs.interval_minutes} disabled={dis} onChange={(e) => set({ jobs: { ...draft.jobs, interval_minutes: Math.max(1, Math.min(240, Number(e.target.value) || 1)) } })} className={`${field} mt-1 w-full`} /></label>
          </div>
          <p className="text-[11px] text-zinc-500" data-testid="hub-jobs-note">
            記事は公開情報なので機密はありません。ただし<b>要約・取り出しは「使う LLM」のモデルで動かすので、ローカルのモデルを選んでいると、この PC の GPU を使います</b>（使うときにロードされます）。クラウドのモデルを選んでいるときは、外部への送信の許可が要ります（無ければその種類は借りません）。
            同時に動かすのは 1 本です。取得しない時間帯は借りません。
            {draft.jobs.kinds.includes("image") && !st.imagegen_ready && <span className="text-amber-700 dark:text-amber-400"> 画像は、機能と拡張の「ニュースの画像生成（CPU）」が未導入のため借りません。</span>}
          </p>
        </>}
      </div>
      {canEdit && <button type="button" onClick={commit} disabled={!dirty || save.isPending} className={primary}>保存</button>}

      <div className="space-y-2 rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-700" data-testid="hub-diagnostics">
        <p className="font-semibold">診断</p>
        <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
          <div><dt className="text-zinc-500">つないだ状態</dt><dd data-testid="hub-diag-state">{!st.linked ? "つないでいません" : st.verified ? "つながっています（指紋を確認済み）" : "指紋の確認待ち"}</dd></div>
          <div><dt className="text-zinc-500">ハブの応答</dt><dd>{st.sync.reachable === null ? "まだ確かめていません" : st.sync.reachable ? "答えています" : `答えません（続けて ${st.sync.failures} 回）`}</dd></div>
          <div><dt className="text-zinc-500">最後の同期（成功）</dt><dd className="tabular-nums" data-testid="hub-diag-last">{when(st.sync.last_ok_at)}</dd></div>
          <div><dt className="text-zinc-500">cursor / ハブの最新</dt><dd className="tabular-nums" data-testid="hub-diag-cursor">{st.cursor} / {st.latest}</dd></div>
          <div><dt className="text-zinc-500">前回の同期で入れた数</dt><dd className="tabular-nums">記事 {st.sync.counts.items ?? 0}・物 {st.sync.counts.entities ?? 0}・イベント {st.sync.counts.events ?? 0}・消した {st.sync.counts.removed ?? 0}</dd></div>
          <div><dt className="text-zinc-500">借りた仕事の成否</dt><dd className="tabular-nums" data-testid="hub-diag-jobs">成功 {st.jobs.done}・失敗 {st.jobs.failed}・ハブが断った {st.jobs.rejected}</dd></div>
        </dl>
        {st.sync.last_error && <p role="alert" data-testid="hub-diag-error" className="rounded-lg border border-red-500/40 bg-red-500/10 p-2 text-red-700 dark:text-red-300">同期の失敗: {st.sync.last_error}</p>}
        {st.jobs.last_error && <p role="status" className="rounded-lg border border-amber-600/40 bg-amber-500/10 p-2 text-amber-800 dark:text-amber-300">仕事: {st.jobs.last_error}</p>}
        {st.jobs.recent.length > 0 && <ul className="space-y-0.5 text-[11px] text-zinc-500" aria-label="最近の仕事">
          {st.jobs.recent.slice(0, 6).map((r, i) => <li key={`${r.at}-${i}`} className="tabular-nums">{when(r.at)} · {r.kind} · {STATUS_TEXT[r.status] ?? r.status}{r.error ? `（${r.error}）` : ""}</li>)}
        </ul>}
        {canEdit && st.verified && <div className="flex flex-wrap gap-2">
          <button type="button" className={ghost} disabled={syncNow.isPending} onClick={() => syncNow.mutate(false, { onSuccess: (r) => toast(r.result.ok ? "同期しました" : `同期できませんでした: ${r.result.error || r.result.skipped}`, r.result.ok ? "success" : "error"), onError: fail })}>今すぐ同期する</button>
          <button type="button" className={ghost} disabled={syncNow.isPending} onClick={() => syncNow.mutate(true, { onSuccess: () => toast("最初から取り直しました", "success"), onError: fail })}>最初から取り直す</button>
          <button type="button" className={ghost} disabled={jobsNow.isPending} onClick={() => jobsNow.mutate(undefined, { onSuccess: (r) => toast(r.result.error ? `仕事を借りられませんでした: ${r.result.error}` : `仕事 ${r.result.claimed} 件を処理しました`, r.result.error ? "error" : "success"), onError: fail })}>今すぐ仕事を借りる</button>
        </div>}
      </div>
    </Card>
  );
}
