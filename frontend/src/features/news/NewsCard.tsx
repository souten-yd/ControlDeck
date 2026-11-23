import { useState } from "react";
import { KIND_STYLE, relativeTime, useNewsAction, type NewsAction, type NewsItem } from "../../api/news";
import { useToasts } from "../../stores";

/** 記事の画像。無い・読めないときは、種類のアイコンと色の帯を出す。 */
function Thumb({ item, className }: { item: NewsItem; className: string }) {
  const [broken, setBroken] = useState(false);
  const style = KIND_STYLE[item.kind] ?? KIND_STYLE.news;
  return item.image_url && !broken ? (
    <img src={item.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} className={`${className} object-cover`} />
  ) : (
    <div aria-hidden className={`${className} flex items-center justify-center bg-gradient-to-br ${style.band} text-3xl text-white`}>{style.icon}</div>
  );
}

const external = (url: string) => /^https?:\/\//.test(url);

export function NewsCard({ item, hero = false, hideRemoves = false, onTopic }: {
  item: NewsItem; hero?: boolean; hideRemoves?: boolean; onTopic?: (topic: string) => void;
}) {
  const act = useNewsAction();
  const toast = useToasts((s) => s.show);
  const style = KIND_STYLE[item.kind] ?? KIND_STYLE.news;
  const send = (action: NewsAction, on = true) => act.mutate({ id: item.id, action, on });
  const toggle = (action: NewsAction) => send(action, !item.state[action]);
  const hide = () => {
    send("hide", true);
    if (hideRemoves) toast("隠しました", "info", { label: "元に戻す", onClick: () => send("hide", false) });
  };
  const open = () => send("open");
  const button = (on: boolean) => `min-h-9 flex-1 rounded-lg px-1.5 text-[11px] font-medium transition ${on
    ? "bg-accent-600 text-white"
    : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"}`;
  return (
    <article data-news-card className={`flex min-w-0 overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 ${hero ? "flex-col md:flex-row" : "flex-col"}`}>
      <Thumb item={item} className={hero ? "h-44 w-full shrink-0 md:h-auto md:min-h-48 md:w-2/5" : "h-28 w-full shrink-0"} />
      <div className="flex min-w-0 flex-1 flex-col gap-2 p-3">
        <p className="flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-500">
          <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 font-medium dark:bg-zinc-800">{style.icon} {style.label}</span>
          <span className="truncate">{item.source}</span>
          <span className="shrink-0">· {relativeTime(item.published_at)}</span>
        </p>
        {item.url ? (
          <a href={item.url} {...(external(item.url) ? { target: "_blank", rel: "noopener noreferrer" } : {})} onClick={open} onAuxClick={open}
            className={`block break-words font-semibold leading-snug hover:text-accent-600 dark:hover:text-accent-400 ${hero ? "text-lg md:text-xl" : "text-sm"}`}>{item.title}</a>
        ) : <h3 className="break-words text-sm font-semibold">{item.title}</h3>}
        {item.summary && <p className={`break-words text-xs leading-relaxed text-zinc-500 ${hero ? "line-clamp-4" : "line-clamp-2"}`}>{item.summary}</p>}
        {item.topics.length > 0 && (
          <ul className="flex flex-wrap gap-1" aria-label="話題">
            {item.topics.slice(0, 4).map((topic) => (
              <li key={topic}>
                <button type="button" onClick={() => onTopic?.(topic)} className="rounded-full border border-zinc-200 px-2 py-0.5 text-[11px] text-zinc-600 hover:border-accent-400 dark:border-zinc-700 dark:text-zinc-300">{topic}</button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-auto flex gap-1 pt-1">
          <button type="button" aria-pressed={item.state.like} onClick={() => toggle("like")} className={button(item.state.like)}>👍 もっと</button>
          <button type="button" aria-pressed={item.state.dislike} onClick={() => toggle("dislike")} className={button(item.state.dislike)}>👎 興味なし</button>
          <button type="button" aria-pressed={item.state.star} onClick={() => toggle("star")} className={button(item.state.star)}>{item.state.star ? "★" : "☆"} 保存</button>
          <button type="button" onClick={hide} className={button(false)}>隠す</button>
        </div>
      </div>
    </article>
  );
}
