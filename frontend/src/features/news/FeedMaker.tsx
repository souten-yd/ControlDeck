import { useState } from "react";
import { checkNewsSource, generateFeed, type CheckResult, type GenerateRequest, type GeneratedFeed } from "../../api/news";
import { CheckResultView } from "./NewsSourceTools";

const ARXIV = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.SD", "eess.AS", "cs.RO", "cs.IR", "cs.NE", "cs.DC", "cs.AR", "cs.NA", "cs.SE", "cs.CR", "cs.MM", "cs.GR", "cs.MA", "cs.PF", "stat.ML"];
const field = "h-10 min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const TYPES = [["keyword", "キーワード"], ["github", "GitHub"], ["steam", "Steam"], ["arxiv", "arXiv"]] as const;
type Type = (typeof TYPES)[number][0];

/** 「フィードを作る」: キーワード（Google ニュース）・GitHub（Release・コミット・タグ）・Steam（ゲーム別）・arXiv（分野の組み合わせ）から URL を作り、通信確認をかけてから足す。
 *  URL の組み立てと入力の検査はサーバー（app/news/feedgen.py）。通信確認が失敗しても「それでも足す」は選べる。 */
export function FeedMaker({ busy, onAdd }: { busy: boolean; onAdd: (feed: GeneratedFeed) => void }) {
  const [type, setType] = useState<Type>("keyword");
  const [keyword, setKeyword] = useState("");
  const [lang, setLang] = useState<"ja" | "en">("ja");
  const [target, setTarget] = useState("");
  const [what, setWhat] = useState<"releases" | "commits" | "tags">("releases");
  const [branch, setBranch] = useState("main");
  const [cats, setCats] = useState<string[]>(["cs.AI", "cs.CL", "cs.LG"]);
  const [state, setState] = useState<{ loading: boolean; feed: GeneratedFeed | null; result: CheckResult | null; failed: string }>({ loading: false, feed: null, result: null, failed: "" });
  const request = (): GenerateRequest => type === "keyword" ? { type, keyword, lang } : type === "github" ? { type, target, what, branch } : type === "steam" ? { type, target } : { type, cats };
  const make = async () => {
    setState({ loading: true, feed: null, result: null, failed: "" });
    try {
      const feed = await generateFeed(request());
      const result = await checkNewsSource({ kind: feed.kind, value: feed.value, name: feed.name });
      setState({ loading: false, feed, result, failed: "" });
    } catch (e) { setState({ loading: false, feed: null, result: null, failed: e instanceof Error ? e.message : "作れませんでした" }); }
  };
  const reset = () => setState({ loading: false, feed: null, result: null, failed: "" });
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700" data-testid="news-feed-maker">
      <h3 className="text-xs font-medium text-zinc-500">フィードを作る</h3>
      <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">キーワード・GitHub・Steam・arXiv から取得先の URL を作り、通信確認をかけてから足します。</p>
      <div role="tablist" aria-label="作るフィードの種類" className="mt-2 flex flex-wrap gap-1.5">
        {TYPES.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={type === id} onClick={() => { setType(id); reset(); }} data-testid={`news-maker-${id}`}
            className={`min-h-9 rounded-full border px-3 text-xs ${type === id ? "border-accent-600 bg-accent-50 font-semibold dark:bg-accent-600/15" : "border-zinc-300 dark:border-zinc-700"}`}>{label}</button>
        ))}
      </div>
      <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); void make(); }}>
        {type === "keyword" && (
          <div className="flex flex-col gap-2 sm:flex-row">
            <input aria-label="キーワード" data-testid="news-maker-keyword" value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="例: ROCm LLM" className={field} />
            <select aria-label="言語" value={lang} onChange={(e) => setLang(e.target.value as "ja" | "en")} className="h-10 rounded-lg border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
              <option value="ja">日本語</option><option value="en">英語</option></select>
          </div>
        )}
        {type === "github" && (
          <div className="flex flex-col gap-2 sm:flex-row">
            <input aria-label="リポジトリ" data-testid="news-maker-target" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="owner/repo か GitHub の URL" className={field} />
            <select aria-label="取るもの" value={what} onChange={(e) => setWhat(e.target.value as typeof what)} className="h-10 rounded-lg border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
              <option value="releases">Release</option><option value="commits">コミット</option><option value="tags">タグ</option></select>
            {what === "commits" && <input aria-label="ブランチ" value={branch} onChange={(e) => setBranch(e.target.value)} className={`${field} sm:max-w-32`} />}
          </div>
        )}
        {type === "steam" && <input aria-label="Steam の App ID かストアの URL" data-testid="news-maker-target" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="例: 1091500 か https://store.steampowered.com/app/1091500/" className={field} />}
        {type === "arxiv" && (
          <fieldset className="flex flex-wrap gap-1.5">
            <legend className="sr-only">arXiv の分野</legend>
            {ARXIV.map((c) => (
              <label key={c} className={`flex min-h-9 items-center gap-1 rounded-full border px-3 text-xs ${cats.includes(c) ? "border-accent-600 bg-accent-50 dark:bg-accent-600/15" : "border-zinc-300 dark:border-zinc-700"}`}>
                <input type="checkbox" className="sr-only" checked={cats.includes(c)} onChange={(e) => setCats(e.target.checked ? [...cats, c] : cats.filter((x) => x !== c))} />{c}</label>
            ))}
          </fieldset>
        )}
        <button type="submit" disabled={busy || state.loading} data-testid="news-maker-make" className="min-h-10 rounded-lg bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">{state.loading ? "作って確認しています…" : "作って通信確認"}</button>
      </form>
      {state.failed && <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400" data-testid="news-maker-error">{state.failed}</p>}
      {state.feed && (
        <div className="mt-3 text-xs" data-testid="news-maker-result">
          <p className="font-medium">{state.feed.name}</p>
          <p className="break-all text-zinc-500">{state.feed.value}</p>
          {state.result && <CheckResultView result={state.result} testId="news-maker-check" />}
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" disabled={busy} data-testid="news-maker-add" onClick={() => { onAdd(state.feed!); reset(); }}
              className="min-h-9 rounded-lg bg-accent-600 px-3 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">{state.result?.ok ? "足す" : "それでも足す"}</button>
            <button type="button" onClick={reset} className="min-h-9 rounded-lg border border-zinc-300 px-3 text-xs dark:border-zinc-700">やめる</button>
          </div>
        </div>
      )}
    </div>
  );
}
