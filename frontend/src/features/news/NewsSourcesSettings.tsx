import { useEffect, useState } from "react";
import { Toggle } from "../models/formControls";
import { useNewsSources, useSaveNewsSources, type NewsConfig } from "../../api/news";
import { useAuth, useToasts } from "../../stores";

type ListKey = "github" | "codeberg" | "discourse" | "reddit";
const LISTS: { key: ListKey; label: string; placeholder: string }[] = [
  { key: "github", label: "GitHub のリポジトリ（Release）", placeholder: "owner/repo" },
  { key: "codeberg", label: "Codeberg のリポジトリ（Release）", placeholder: "owner/repo" },
  { key: "discourse", label: "Discourse のフォーラム（最新の話題）", placeholder: "forum.example.com" },
  { key: "reddit", label: "Reddit の subreddit（API キーがあるときだけ）", placeholder: "LocalLLaMA" },
];
const input = "h-10 min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";

/** 設定 → ニュース → ソース。種類ごとのスイッチと、RSS・リポジトリ・Discourse・subreddit の追加と削除。
 *  変更は settings.manage の人だけ（他の人は見るだけ）。形の検査はサーバー（app/news/sources.py）がする。 */
export function NewsSourcesSettings() {
  const { data } = useNewsSources();
  const save = useSaveNewsSources();
  const toast = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("settings.manage");
  const [config, setConfig] = useState<NewsConfig | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [feed, setFeed] = useState({ name: "", url: "" });
  useEffect(() => { if (data) setConfig(data.config); }, [data]);
  if (!config || !data) return null;

  const persist = (next: NewsConfig) => {
    setConfig(next);
    save.mutate(next, {
      onSuccess: (r) => setConfig(r.config),
      onError: (e) => { setConfig(data.config); toast(e instanceof Error ? e.message : "保存できませんでした", "error"); },
    });
  };
  const add = (key: ListKey) => {
    const value = (drafts[key] ?? "").trim();
    if (value && !config[key].includes(value)) persist({ ...config, [key]: [...config[key], value] });
    setDrafts({ ...drafts, [key]: "" });
  };
  const remove = (key: ListKey, value: string) => persist({ ...config, [key]: config[key].filter((v) => v !== value) });

  return (
    <section aria-labelledby="news-sources-title" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 id="news-sources-title" className="text-sm font-semibold text-zinc-500">ニュースのソース</h2>
      <p className="mt-1 text-xs leading-relaxed text-zinc-400">ニュースのページが 30 分ごとに集めるソースです。有料のものは使いません。{canEdit ? "" : "変更には設定の管理権限が要ります。"}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {Object.entries(data.kinds).map(([kind, label]) => (
          <Toggle key={kind} label={label} value={config.enabled[kind] !== false} disabled={!canEdit || save.isPending}
            onChange={(on) => persist({ ...config, enabled: { ...config.enabled, [kind]: on } })} />
        ))}
        <Toggle label="Hacker News（上位）" value={config.hackernews} disabled={!canEdit || save.isPending} onChange={(on) => persist({ ...config, hackernews: on })} />
        <Toggle label="Hugging Face（話題の GGUF）" value={config.huggingface} disabled={!canEdit || save.isPending} onChange={(on) => persist({ ...config, huggingface: on })} />
      </div>
      <div className="mt-4">
        <h3 className="text-xs font-medium text-zinc-500">RSS・Atom のフィード</h3>
        <ul className="mt-2 space-y-1">
          {config.rss.map((f) => (
            <li key={f.url} className="flex items-center gap-2 text-xs">
              <span className="min-w-0 flex-1 break-all"><span className="font-medium">{f.name}</span> <span className="text-zinc-500">{f.url}</span></span>
              {canEdit && <button type="button" aria-label={`${f.name} を外す`} onClick={() => persist({ ...config, rss: config.rss.filter((x) => x.url !== f.url) })} className="min-h-9 shrink-0 rounded-lg px-2 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30">削除</button>}
            </li>
          ))}
        </ul>
        {canEdit && (
          <form className="mt-2 flex flex-col gap-2 sm:flex-row" onSubmit={(e) => {
            e.preventDefault();
            if (feed.url.trim()) persist({ ...config, rss: [...config.rss, { name: feed.name.trim(), url: feed.url.trim() }] });
            setFeed({ name: "", url: "" });
          }}>
            <input aria-label="フィードの名前" value={feed.name} onChange={(e) => setFeed({ ...feed, name: e.target.value })} placeholder="名前" className={`${input} sm:max-w-40`} />
            <input aria-label="フィードの URL" value={feed.url} onChange={(e) => setFeed({ ...feed, url: e.target.value })} placeholder="https://example.com/feed.xml" className={input} />
            <button type="submit" disabled={save.isPending} className="min-h-10 shrink-0 rounded-lg bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">フィードを追加</button>
          </form>
        )}
      </div>
      {LISTS.map(({ key, label, placeholder }) => (
        <div key={key} className="mt-4">
          <h3 className="text-xs font-medium text-zinc-500">{label}</h3>
          {key === "reddit" && !data.reddit_key && <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">Reddit のキーが未設定なので取得しません（設定 → API キー）。</p>}
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {config[key].map((value) => (
              <li key={value} className="flex items-center gap-1 rounded-full border border-zinc-200 py-0.5 pl-3 pr-1 text-xs dark:border-zinc-700">
                {value}
                {canEdit && <button type="button" aria-label={`${value} を外す`} onClick={() => remove(key, value)} className="grid h-7 w-7 place-items-center rounded-full text-zinc-400 hover:text-red-500">×</button>}
              </li>
            ))}
          </ul>
          {canEdit && (
            <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); add(key); }}>
              <input aria-label={`${label}を追加`} value={drafts[key] ?? ""} onChange={(e) => setDrafts({ ...drafts, [key]: e.target.value })} placeholder={placeholder} className={input} />
              <button type="submit" disabled={save.isPending} className="min-h-10 shrink-0 rounded-lg border border-zinc-300 px-4 text-sm font-medium hover:border-zinc-400 disabled:opacity-40 dark:border-zinc-700">追加</button>
            </form>
          )}
        </div>
      ))}
    </section>
  );
}
