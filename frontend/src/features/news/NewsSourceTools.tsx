import { useState } from "react";
import { checkNewsSource, discoverNewsSite, type CheckResult, type DiscoverResult, type NewsConfig } from "../../api/news";

const btn = "min-h-9 shrink-0 rounded-lg border border-zinc-300 px-3 text-xs hover:border-zinc-400 disabled:opacity-40 dark:border-zinc-700";
const field = "h-10 min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";

/** 通信確認の結果（取れた件数・最新の見出し 3 件・かかった時間・HTTP の状態・失敗の理由）。 */
export function CheckResultView({ result, testId = "news-check-result" }: { result: CheckResult; testId?: string }) {
  const info = [result.status ? `HTTP ${result.status}` : "", `${(result.ms / 1000).toFixed(1)} 秒`].filter(Boolean).join(" · ");
  return (
    <div data-testid={testId} role="status" className={`mt-1 rounded-lg border px-3 py-2 text-xs ${result.ok ? "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30" : "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/30"}`}>
      {result.ok ? (
        <p className="font-medium text-emerald-700 dark:text-emerald-300">取れました: {result.count} 件{result.latest ? ` · 最新 ${result.latest}` : ""} <span className="font-normal text-zinc-500">（{info}）</span></p>
      ) : (
        <p className="font-medium text-red-700 dark:text-red-300">{result.error?.message ?? "取れません（理由は不明）"} <span className="font-normal text-zinc-500">（{info}）</span></p>
      )}
      {!result.ok && result.error?.location && <p className="mt-1 break-all text-zinc-500">移動先: {result.error.location}</p>}
      {result.titles.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-zinc-600 dark:text-zinc-300">
          {result.titles.map((t) => <li key={t} className="break-words">{t}</li>)}
        </ul>
      )}
    </div>
  );
}

/** ソース 1 つの「通信確認」ボタンと、その結果。押すとその場で 1 回取る（保存はしない）。 */
export function CheckButton({ kind, value, name = "", label = "通信確認", testId, onResult }: { kind: string; value: string; name?: string; label?: string; testId?: string; onResult?: (r: CheckResult | null, failed: string) => void }) {
  const [state, setState] = useState<{ busy: boolean; result: CheckResult | null; failed: string }>({ busy: false, result: null, failed: "" });
  const run = async () => {
    setState({ busy: true, result: null, failed: "" });
    onResult?.(null, "");
    try { const result = await checkNewsSource({ kind, value, name }); setState({ busy: false, result, failed: "" }); onResult?.(result, ""); }
    catch (e) { const failed = e instanceof Error ? e.message : "確認できませんでした"; setState({ busy: false, result: null, failed }); onResult?.(null, failed); }
  };
  return (
    <>
      <button type="button" className={btn} disabled={state.busy} onClick={run} data-testid={testId ?? `news-check-${kind}`}>{state.busy ? "確認中…" : label}</button>
      {!onResult && (state.result || state.failed) && (
        <div className="basis-full">{state.result ? <CheckResultView result={state.result} /> : <p role="alert" className="mt-1 text-xs text-red-600">{state.failed}</p>}</div>
      )}
    </>
  );
}

const MODES = { feed: "RSS として読めました", feeds: "RSS の候補が見つかりました", page: "RSS は見つかりませんでした（記事の一覧のページを巡回できます）", none: "取り込めませんでした" };

/** 「サイトを追加」: URL から RSS の候補を探し、無ければ記事の一覧のページの巡回を選べる。保存の前に結果（件数・最新の見出し）を見せ、取れないものは警告する（それでも保存はできる）。 */
export function SiteAdder({ config, busy, onChange }: { config: NewsConfig; busy: boolean; onChange: (next: NewsConfig) => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [state, setState] = useState<{ loading: boolean; result: DiscoverResult | null; failed: string }>({ loading: false, result: null, failed: "" });
  const discover = async () => {
    setState({ loading: true, result: null, failed: "" });
    try {
      const result = await discoverNewsSite(url.trim());
      if (!name) setName(result.title);
      setState({ loading: false, result, failed: "" });
    } catch (e) { setState({ loading: false, result: null, failed: e instanceof Error ? e.message : "調べられませんでした" }); }
  };
  const finish = (next: NewsConfig) => { onChange(next); setState({ loading: false, result: null, failed: "" }); setUrl(""); setName(""); };
  const label = name.trim() || state.result?.title || "";
  const addFeed = (feedUrl: string) => finish({ ...config, rss: [...config.rss.filter((f) => f.url !== feedUrl), { name: label || new URL(feedUrl).hostname, url: feedUrl }] });
  const addPage = () => { const u = state.result?.url ?? url.trim(); finish({ ...config, pages: [...(config.pages ?? []).filter((p) => p.url !== u), { name: label || new URL(u).hostname, url: u }] }); };
  const r = state.result;
  return (
    <div className="mt-4 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-testid="news-site-adder">
      <h3 className="text-xs font-medium text-zinc-500">サイトを追加</h3>
      <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">サイトのアドレスを入れると、RSS を探して取れるか確かめます。RSS が無いサイトは、記事の一覧のページを巡回できます（robots.txt を守り、同じサイトの中だけ・1 時間に 1 回・30 件まで）。ログインが要るサイトやロボット確認のあるサイトは取れません。</p>
      <form className="mt-2 flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); if (url.trim()) void discover(); }}>
        <input aria-label="サイトの URL" data-testid="news-site-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/" className={field} />
        <button type="submit" disabled={busy || state.loading || !url.trim()} className="min-h-10 shrink-0 rounded-lg bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40" data-testid="news-site-discover">{state.loading ? "調べています…" : "調べる"}</button>
      </form>
      {state.failed && <p role="alert" className="mt-2 text-xs text-red-600">{state.failed}</p>}
      {r && (
        <div className="mt-3 space-y-2 text-xs" data-testid="news-site-result">
          <p className={r.error ? "font-medium text-red-600 dark:text-red-400" : "font-medium"}>{r.error ? `警告: ${r.error.message}` : MODES[r.mode]}{r.title && ` — ${r.title}`}</p>
          {!r.error && (
            <label className="flex items-center gap-2">表示名
              <input aria-label="表示名" value={name} onChange={(e) => setName(e.target.value)} className={`${field} h-9`} /></label>
          )}
          {r.feeds.map((f) => (
            <div key={f.url} className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-700" data-testid="news-site-candidate">
              <p className="break-all font-medium">{f.url}</p>
              <p className="text-zinc-500">{f.count} 件 · 最新 {f.latest}</p>
              <ul className="mt-1 list-disc pl-4 text-zinc-600 dark:text-zinc-300">{f.titles.map((t) => <li key={t} className="break-words">{t}</li>)}</ul>
              <button type="button" disabled={busy} onClick={() => addFeed(f.url)} className="mt-2 min-h-9 rounded-lg bg-accent-600 px-3 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">この RSS を追加</button>
            </div>
          ))}
          {r.page && (
            <div className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-700" data-testid="news-site-page">
              <p className="font-medium">記事の一覧のページを巡回する</p>
              <p className="text-zinc-500">{r.page.count} 件の記事らしいリンクが見つかりました。</p>
              <ul className="mt-1 list-disc pl-4 text-zinc-600 dark:text-zinc-300">{r.page.titles.map((t) => <li key={t} className="break-words">{t}</li>)}</ul>
              {!r.page.robots && <p className="mt-1 text-red-600 dark:text-red-400">このページは robots.txt が巡回を許していません。追加しても取得されません。</p>}
              <button type="button" disabled={busy} onClick={addPage} className="mt-2 min-h-9 rounded-lg border border-zinc-300 px-3 text-xs font-medium hover:border-zinc-400 disabled:opacity-40 dark:border-zinc-700">{r.page.robots ? "巡回で追加" : "それでも追加"}</button>
            </div>
          )}
          {r.error && (
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={busy} onClick={addPage} className={btn} data-testid="news-site-force">それでも一覧のページとして追加</button>
              <button type="button" onClick={() => setState({ loading: false, result: null, failed: "" })} className={btn}>やめる</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
