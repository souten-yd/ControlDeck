import { Link } from "react-router-dom";
import { Toggle } from "../models/formControls";
import { useManageList, useNewsSources, useSaveNewsSources } from "../../api/news";
import { useAuth, useToasts } from "../../stores";
import { intervalLabel, stateOf } from "./sourceUtil";

/** 設定 → ニュース → ソース。種類ごとのスイッチと、取得先の要点（件数・失敗中）、「取得先を管理」（/news/sources）への入口。
 *  取得先の一覧・通信確認・追加と削除・間隔・目録（プリセット）はそのページに集めた（2 か所で同じことをしない。2026-11-24 利用者の要望）。
 *  変更は settings.manage の人だけ（他の人は見るだけ）。形の検査はサーバー（app/news/sources.py）がする。 */
export function NewsSourcesSettings() {
  const { data } = useNewsSources();
  const manage = useManageList();
  const save = useSaveNewsSources();
  const toast = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("settings.manage");
  if (!data) return null;
  const config = data.config;
  const persist = (next: typeof config) => save.mutate(next, { onError: (e) => toast(e instanceof Error ? e.message : "保存できませんでした", "error") });
  const items = manage.data?.items ?? [];
  const now = Date.now();
  const failing = items.filter((s) => stateOf(s, now) === "failing").length;
  const stalled = items.filter((s) => stateOf(s, now) === "stalled").length;
  const off = items.filter((s) => !s.enabled).length;
  return (
    <section aria-labelledby="news-sources-title" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 id="news-sources-title" className="text-sm font-semibold text-zinc-500">ニュースのソース</h2>
        <Link to="/news/sources" data-testid="news-manage-link" className="inline-flex min-h-10 items-center rounded-lg bg-accent-600 px-4 text-xs font-medium text-white hover:bg-accent-700">取得先を管理</Link>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-zinc-400">
        ニュースのページが集めるソースです。有料のものは使いません。取得先の一覧・通信確認・追加と削除・取得の間隔・プリセット（目録）は「取得先を管理」にあります。
        既定の取得の間隔は {intervalLabel(manage.data?.default_interval ?? 30)}（取得先ごとに変えられます）。{canEdit ? "" : "変更には設定の管理権限が要ります。"}
      </p>
      <p className="mt-2 text-xs" data-testid="news-sources-brief">
        取得先 {items.length} 件{off > 0 && ` · 無効 ${off}`}{failing > 0 && <span className="text-red-600 dark:text-red-400"> · 失敗中 {failing}</span>}{stalled > 0 && <span className="text-amber-600 dark:text-amber-400"> · 止まっている {stalled}</span>}
        {!data.reddit_key && " · Reddit は API キーが無いので取得しません（設定 → API キー）"}
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {Object.entries(data.kinds).map(([kind, label]) => (
          <Toggle key={kind} label={label} value={config.enabled[kind] !== false} disabled={!canEdit || save.isPending}
            onChange={(on) => persist({ ...config, enabled: { ...config.enabled, [kind]: on } })} />
        ))}
        <Toggle label="Hacker News（上位）" value={config.hackernews} disabled={!canEdit || save.isPending} onChange={(on) => persist({ ...config, hackernews: on })} />
        <Toggle label="Hugging Face（話題の GGUF）" value={config.huggingface} disabled={!canEdit || save.isPending} onChange={(on) => persist({ ...config, huggingface: on })} />
      </div>
    </section>
  );
}
