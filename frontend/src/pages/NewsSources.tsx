import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  bulkCheck, checkNewsSource, relativeTime, useManageDetail, useManageList, useManageMutations, useNewsSources, useSaveNewsSources,
  type CheckResult, type GeneratedFeed, type ManagedSource,
} from "../api/news";
import { useAuth, useToasts } from "../stores";
import { CheckResultView, SiteAdder } from "../features/news/NewsSourceTools";
import { FeedMaker } from "../features/news/FeedMaker";
import { PresetPanel } from "../features/news/NewsPresetPanel";
import { INTERVAL_CHOICES, STATE_LABEL, failRate, intervalLabel, stateOf, type SourceState } from "../features/news/sourceUtil";

/** 取得先の管理（/news/sources）。ニュースの取得先（RSS・Atom・一覧の巡回・GitHub・Steam・arXiv・Reddit など）の一覧・実績・通信確認・有効/無効・間隔・追加と削除。
 *  2026-11-24 利用者「設定の中にボタンを置き、別の管理ページに飛ばして、疎通確認だったりとか、これまでの取得実績、最終取得日時などを確認、サイトごとに確認できたりとか、新しいサイトの追加、削除などができるように」。 */
const btn = "min-h-9 shrink-0 rounded-lg border border-zinc-300 px-3 text-xs hover:border-zinc-400 disabled:opacity-40 dark:border-zinc-700";
const sel = "h-9 rounded-lg border border-zinc-300 bg-white px-2 text-xs dark:border-zinc-700 dark:bg-zinc-900";
const SORTS = [["last", "最終取得が新しい順"], ["fail", "失敗の多い順"], ["total", "記事の多い順"], ["name", "名前順"]] as const;
type Sort = (typeof SORTS)[number][0];
const STATE_STYLE: Record<SourceState, string> = {
  ok: "text-emerald-700 dark:text-emerald-300", failing: "text-red-700 dark:text-red-300", stalled: "text-amber-700 dark:text-amber-300", disabled: "text-zinc-500", waiting: "text-zinc-500",
};

const when = (iso: string) => (iso ? new Date(iso).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");

/** 直近 7 日の成功率の棒（高さ = 成功率。取っていない日は点）。数値は title と読み上げに入れる。 */
function WeekBars({ week }: { week: (number | null)[] }) {
  const text = week.map((v, i) => `${6 - i} 日前: ${v === null ? "取得なし" : `${v}%`}`).join("、");
  return (
    <span className="inline-flex h-5 items-end gap-0.5" role="img" aria-label={`直近 7 日の成功率 ${text}`} title={text} data-testid="news-week-bars">
      {week.map((v, i) => v === null
        ? <span key={i} className="h-1 w-1.5 rounded-full bg-zinc-300 dark:bg-zinc-600" />
        : <span key={i} style={{ height: `${Math.max(12, v)}%` }} className={`w-1.5 rounded-sm ${v >= 80 ? "bg-emerald-500" : v >= 40 ? "bg-amber-500" : "bg-red-500"}`} />)}
    </span>
  );
}

function IntervalSelect({ value, def, disabled, onChange, label }: { value: number; def: number; disabled?: boolean; onChange: (m: number) => void; label: string }) {
  const options = [...new Set([...INTERVAL_CHOICES, ...(value ? [value] : [])])].sort((a, b) => a - b);
  return (
    <select aria-label={label} className={sel} disabled={disabled} value={value} data-testid="news-interval" onChange={(e) => onChange(Number(e.target.value))}>
      <option value={0}>既定（{intervalLabel(def)}）</option>
      {options.map((m) => <option key={m} value={m}>{intervalLabel(m)}</option>)}
    </select>
  );
}

function Detail({ s, canEdit, def, groupName, onToggle, onInterval, onDelete }: {
  s: ManagedSource; canEdit: boolean; def: number; groupName: (id: string) => string; onToggle: () => void; onInterval: (m: number) => void; onDelete: () => void;
}) {
  const { data } = useManageDetail(s.key);
  const [check, setCheck] = useState<{ busy: boolean; result: CheckResult | null; failed: string }>({ busy: false, result: null, failed: "" });
  const [confirm, setConfirm] = useState(false);
  const run = async () => {
    setCheck({ busy: true, result: null, failed: "" });
    try { setCheck({ busy: false, result: await checkNewsSource({ kind: s.kind, value: s.value, name: s.name }), failed: "" }); }
    catch (e) { setCheck({ busy: false, result: null, failed: e instanceof Error ? e.message : "確認できませんでした" }); }
  };
  return (
    <div className="mt-2 space-y-3 border-t border-zinc-200 pt-3 text-xs dark:border-zinc-700" data-testid="news-source-detail">
      <p className="break-all text-zinc-500">{s.value || s.name}{s.groups.length > 0 && ` · ${s.groups.map(groupName).join(" / ")}`}</p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btn} disabled={check.busy} onClick={run} data-testid="news-detail-check">{check.busy ? "確認中…" : "通信確認"}</button>
        {canEdit && <button type="button" className={btn} onClick={onToggle} data-testid="news-detail-toggle">{s.enabled ? "無効にする" : "有効にする"}</button>}
        {canEdit && <label className="flex items-center gap-1">間隔 <IntervalSelect value={s.interval} def={def} onChange={onInterval} label={`${s.name} の取得の間隔`} /></label>}
        {canEdit && !confirm && <button type="button" className={`${btn} text-red-600`} onClick={() => setConfirm(true)} data-testid="news-detail-delete">削除</button>}
        {canEdit && confirm && (
          <span className="flex items-center gap-1">この取得先を削除しますか？（取得済みの記事は残ります）
            <button type="button" className={`${btn} border-red-400 text-red-600`} onClick={onDelete} data-testid="news-detail-delete-confirm">削除する</button>
            <button type="button" className={btn} onClick={() => setConfirm(false)}>やめる</button></span>
        )}
      </div>
      {check.result && <CheckResultView result={check.result} testId="news-detail-check-result" />}
      {check.failed && <p role="alert" className="text-red-600">{check.failed}</p>}
      <div>
        <h4 className="font-medium text-zinc-500">取得の履歴（直近 {data?.history.length ?? 0} 回）</h4>
        {data && data.history.length === 0 && <p className="mt-1 text-zinc-400">まだ取得の記録がありません。</p>}
        {data && data.history.length > 0 && (
          <div className="mt-1 max-h-64 overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
            <table className="w-full text-left text-[11px]" data-testid="news-history">
              <thead className="sticky top-0 bg-zinc-50 text-zinc-500 dark:bg-zinc-800"><tr><th className="px-2 py-1">日時</th><th className="px-2">結果</th><th className="px-2">HTTP</th><th className="px-2">記事</th><th className="px-2">新着</th><th className="px-2">時間</th></tr></thead>
              <tbody>
                {data.history.map((h) => (
                  <tr key={h.at + h.count + h.error} className="border-t border-zinc-100 align-top dark:border-zinc-800">
                    <td className="whitespace-nowrap px-2 py-1">{when(h.at)}</td>
                    <td className={`px-2 ${h.ok ? "" : "text-red-600 dark:text-red-400"}`}>{h.ok ? (h.skipped ? `見送り: ${h.skipped}` : "成功") : `失敗: ${h.error}`}</td>
                    <td className="px-2">{h.status ?? "—"}</td><td className="px-2">{h.count}</td><td className="px-2">{h.new}</td><td className="whitespace-nowrap px-2">{(h.ms / 1000).toFixed(1)} 秒</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {data && data.articles.length > 0 && (
        <div>
          <h4 className="font-medium text-zinc-500">このソースから入った最近の記事</h4>
          <ul className="mt-1 space-y-0.5" data-testid="news-detail-articles">
            {data.articles.map((a) => <li key={a.id} className="break-words"><Link className="text-accent-700 underline dark:text-accent-300" to={`/news/read/${a.id}`}>{a.title}</Link> <span className="text-zinc-400">{relativeTime(a.published_at)}</span></li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function NewsSourcesPage() {
  const { data } = useManageList();
  const cfg = useNewsSources();
  const save = useSaveNewsSources();
  const { bulk, add } = useManageMutations();
  const toast = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("settings.manage");
  const [tab, setTab] = useState<"list" | "add">("list");
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("");
  const [status, setStatus] = useState<"" | SourceState>("");
  const [sort, setSort] = useState<Sort>("last");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, CheckResult>>({});
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkInterval, setBulkInterval] = useState(60);
  const now = Date.now();
  const items = data?.items ?? [];
  const groupName = (id: string) => data?.groups.find((g) => g.id === id)?.label ?? id;
  const counts = useMemo(() => {
    const c: Record<SourceState, number> = { ok: 0, failing: 0, stalled: 0, disabled: 0, waiting: 0 };
    for (const s of items) c[stateOf(s, now)] += 1;
    return c;
  }, [items, now]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = items.filter((s) => (!needle || `${s.name} ${s.value} ${s.domain}`.toLowerCase().includes(needle)) && (!group || s.groups.includes(group)) && (!status || stateOf(s, now) === status));
    const by: Record<Sort, (a: ManagedSource, b: ManagedSource) => number> = {
      last: (a, b) => (Date.parse(b.last_at) || 0) - (Date.parse(a.last_at) || 0),
      fail: (a, b) => failRate(b) - failRate(a) || Number(b.last_ok === false) - Number(a.last_ok === false),
      total: (a, b) => b.total - a.total,
      name: (a, b) => a.name.localeCompare(b.name, "ja"),
    };
    return [...list].sort(by[sort]);
  }, [items, q, group, status, sort, now]);
  if (!data) return <p className="p-4 text-sm text-zinc-500">読み込み中…</p>;
  const have = new Set(items.map((s) => s.key));
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "保存できませんでした", "error");
  const doBulk = (action: "enable" | "disable" | "delete" | "interval", keys: string[], interval?: number, message?: string) =>
    bulk.mutate({ action, keys, interval }, { onSuccess: () => { if (message) toast(message, "success"); setPicked(new Set()); if (action === "delete" && open && keys.includes(open)) setOpen(null); }, onError: fail });
  const doAdd = (list: { kind: string; value: string; name?: string; interval?: number }[]) =>
    add.mutate(list, { onSuccess: (r) => toast(r.added ? `${r.added} 件を足しました` : "すでに入っています", "success"), onError: fail });
  const addGenerated = (f: GeneratedFeed) => doAdd([{ kind: f.kind, value: f.value, name: f.name }]);
  const runChecks = async (keys: string[]) => {
    setBulkBusy(true);
    try {
      const r = await bulkCheck(keys);
      setChecks((c) => ({ ...c, ...Object.fromEntries(r.results.map((x) => [x.key, x])) }));
      toast(`${r.results.filter((x) => x.ok).length} / ${r.results.length} 件で取れました`, "success");
    } catch (e) { fail(e); }
    setBulkBusy(false);
  };
  const keys = [...picked];
  const busy = bulk.isPending || add.isPending || save.isPending;
  const allPicked = rows.length > 0 && rows.every((s) => picked.has(s.key));
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4" data-testid="news-sources-page">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link to="/news" className="text-xs text-zinc-500 underline">← ニュースへ戻る</Link>
          <h1 className="text-lg font-semibold">取得先の管理</h1>
        </div>
        <Link to="/settings/news" className={`${btn} inline-flex items-center`}>ニュースの設定</Link>
      </header>
      <p className="text-xs leading-relaxed text-zinc-500" data-testid="news-sources-summary">
        全 {items.length} 件 · 正常 {counts.ok} · 失敗中 {counts.failing} · 止まっている {counts.stalled} · 無効 {counts.disabled} · 未取得 {counts.waiting}。
        間隔は取得先ごとに決められます（既定 {intervalLabel(data.default_interval)}）。{!data.reddit_key && " Reddit は API キー（設定 → API キー）が無いので取得しません。"}
      </p>
      <div role="tablist" aria-label="表示" className="flex gap-1.5">
        {([["list", "取得先の一覧"], ["add", "追加・目録"]] as const).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)} data-testid={`news-tab-${id}`}
            className={`min-h-10 rounded-full border px-4 text-sm ${tab === id ? "border-accent-600 bg-accent-50 font-semibold dark:bg-accent-600/15" : "border-zinc-300 dark:border-zinc-700"}`}>{label}</button>
        ))}
      </div>
      {tab === "list" && (
        <section aria-label="取得先の一覧" className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <input aria-label="名前・URL で探す" data-testid="news-src-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="名前・URL で探す" className="h-9 min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900 sm:max-w-xs" />
            <select aria-label="分野" className={sel} value={group} onChange={(e) => setGroup(e.target.value)} data-testid="news-src-group">
              <option value="">すべての分野</option>{data.groups.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}</select>
            <select aria-label="状態" className={sel} value={status} onChange={(e) => setStatus(e.target.value as "" | SourceState)} data-testid="news-src-status">
              <option value="">すべての状態</option>{(["failing", "stalled", "disabled", "ok", "waiting"] as const).map((k) => <option key={k} value={k}>{STATE_LABEL[k]}</option>)}</select>
            <select aria-label="並べ替え" className={sel} value={sort} onChange={(e) => setSort(e.target.value as Sort)} data-testid="news-src-sort">
              {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </div>
          {canEdit && (
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200 p-2 text-xs dark:border-zinc-700" data-testid="news-bulk-bar">
              <label className="flex min-h-9 items-center gap-1"><input type="checkbox" checked={allPicked} onChange={(e) => setPicked(e.target.checked ? new Set(rows.map((s) => s.key)) : new Set())} data-testid="news-pick-all" /> 表示中をすべて選ぶ</label>
              <span className="text-zinc-500" data-testid="news-picked">{picked.size} 件を選択中</span>
              <button type="button" className={btn} disabled={!picked.size || bulkBusy} onClick={() => void runChecks(keys.slice(0, 40))} data-testid="news-bulk-check">{bulkBusy ? "確認中…" : "通信確認"}</button>
              <button type="button" className={btn} disabled={!picked.size || busy} onClick={() => doBulk("enable", keys)} data-testid="news-bulk-enable">有効にする</button>
              <button type="button" className={btn} disabled={!picked.size || busy} onClick={() => doBulk("disable", keys)} data-testid="news-bulk-disable">無効にする</button>
              <span className="flex items-center gap-1"><IntervalSelect value={bulkInterval} def={data.default_interval} onChange={setBulkInterval} label="まとめて変える間隔" />
                <button type="button" className={btn} disabled={!picked.size || busy} onClick={() => doBulk("interval", keys, bulkInterval)} data-testid="news-bulk-interval">間隔を変える</button></span>
              <button type="button" className={`${btn} text-red-600`} disabled={!picked.size || busy}
                onClick={() => { if (window.confirm(`${picked.size} 件の取得先を削除しますか？（取得済みの記事は残ります）`)) doBulk("delete", keys, undefined, "削除しました"); }} data-testid="news-bulk-delete">削除</button>
            </div>
          )}
          <p className="text-[11px] text-zinc-400">{rows.length} 件を表示</p>
          <ul className="space-y-2" data-testid="news-src-list">
            {rows.map((s) => {
              const st = stateOf(s, now);
              const c = checks[s.key];
              return (
                <li key={s.key} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-testid="news-src-row" data-state={st}>
                  <div className="flex flex-wrap items-start gap-2 sm:flex-nowrap">
                    {canEdit && <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" aria-label={`${s.name} を選ぶ`} checked={picked.has(s.key)} data-testid="news-pick"
                      onChange={(e) => setPicked((p) => { const n = new Set(p); if (e.target.checked) n.add(s.key); else n.delete(s.key); return n; })} />}
                    <div className="min-w-[10rem] flex-1 basis-[calc(100%-2rem)] sm:basis-0">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                        <button type="button" className="text-left text-sm font-medium break-words" aria-expanded={open === s.key} onClick={() => setOpen(open === s.key ? null : s.key)} data-testid="news-src-open">{s.name}</button>
                        <span className="rounded bg-zinc-100 px-1.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{data.formats[s.format] ?? s.format}</span>
                        <span className={`text-[11px] font-medium ${STATE_STYLE[st]}`} data-testid="news-src-state">{STATE_LABEL[st]}</span>
                      </div>
                      <p className="break-all text-[11px] text-zinc-500">{s.domain}{s.groups.length > 0 && ` · ${s.groups.slice(0, 2).map(groupName).join(" / ")}`}</p>
                      <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] sm:grid-cols-4 lg:grid-cols-6">
                        <div><dt className="text-zinc-400">最終取得</dt><dd>{when(s.last_at)}</dd></div>
                        <div><dt className="text-zinc-400">最終成功</dt><dd>{when(s.last_ok_at)}</dd></div>
                        <div className="col-span-2"><dt className="text-zinc-400">直近の結果</dt>
                          <dd className={s.last_ok === false ? "break-words text-red-600 dark:text-red-400" : "break-words"}>{!s.last_at ? "まだ取っていません" : s.last_ok === false ? s.last_error : s.last_skipped ? `見送り: ${s.last_skipped}` : `${s.last_count} 件`}{s.last_status && !s.last_error.includes(String(s.last_status)) ? ` (HTTP ${s.last_status})` : ""}</dd></div>
                        <div><dt className="text-zinc-400">7 日の成功率</dt><dd><WeekBars week={s.week} /></dd></div>
                        <div><dt className="text-zinc-400">記事の累計</dt><dd>{s.total}</dd></div>
                      </dl>
                    </div>
                    <div className="flex w-full shrink-0 items-center justify-between gap-1 sm:w-auto sm:flex-col sm:items-end">
                      {canEdit && <IntervalSelect value={s.interval} def={data.default_interval} disabled={busy} label={`${s.name} の取得の間隔`} onChange={(m) => doBulk("interval", [s.key], m)} />}
                      {canEdit && (
                        <label className="flex min-h-9 items-center gap-1 text-[11px]">
                          <input type="checkbox" role="switch" checked={s.enabled} disabled={busy} aria-label={`${s.name} を取得する`} data-testid="news-src-enabled" onChange={(e) => doBulk(e.target.checked ? "enable" : "disable", [s.key])} />有効</label>
                      )}
                    </div>
                  </div>
                  {c && <CheckResultView result={c} testId="news-src-check-result" />}
                  {open === s.key && (
                    <Detail s={s} canEdit={canEdit} def={data.default_interval} groupName={groupName}
                      onToggle={() => doBulk(s.enabled ? "disable" : "enable", [s.key])} onInterval={(m) => doBulk("interval", [s.key], m)} onDelete={() => doBulk("delete", [s.key], undefined, "削除しました")} />
                  )}
                </li>
              );
            })}
            {rows.length === 0 && <li className="p-4 text-center text-xs text-zinc-400">条件に合う取得先はありません。</li>}
          </ul>
        </section>
      )}
      {tab === "add" && (
        <section aria-label="追加" className="space-y-4">
          {!canEdit && <p className="text-xs text-zinc-500">追加と削除には設定の管理権限が要ります。</p>}
          <PresetPanel have={have} canEdit={canEdit} busy={busy} onAdd={doAdd} onRemove={(ks) => doBulk("delete", ks)} />
          {canEdit && <FeedMaker busy={busy} onAdd={addGenerated} />}
          {canEdit && cfg.data && <SiteAdder config={cfg.data.config} busy={busy} onChange={(next) => save.mutate(next, { onSuccess: () => toast("足しました", "success"), onError: fail })} />}
        </section>
      )}
    </div>
  );
}
