import { TranslatedMark } from "./TranslatedMark";
import { useState, type CSSProperties } from "react";
import { KIND_STYLE, relativeTime, useNewsAction, type NewsAction, type NewsItem } from "../../api/news";
import { useToasts } from "../../stores";
import { chNo } from "./newsTheme";
import { ArticleLink } from "./ArticleLink";

export interface CardChannel { id: string; name: string; color: string; number: number }

/** 16:9 の画像。無い・読めないときは、大きな箱にせず、チャンネルの色の細いアクセントライン（高さ 5px）だけにする
 *  （2026-11-23 利用者「無意味なアイコンを貼ったデカデカとしたエリア…画像がないんだったらアクセントラインだけにして、表示を小さくする」）。 */
export function Media({ item, color, className = "card-media", children }: { item: NewsItem; color?: string; className?: string; children?: React.ReactNode }) {
  const [broken, setBroken] = useState(false);
  const has = Boolean(item.image_url) && !broken;
  return (
    <div className={`${className}${has ? "" : " nw-noimg"}`} data-noimg={has ? undefined : "true"} style={color ? ({ "--ch": color } as CSSProperties) : undefined}>
      {has && <img src={item.image_url!} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />}
      {has && item.image_ai && <span className="nw-ai-mark" data-testid="ai-image-mark">AI 生成のイメージ</span>}
      {has && children}
    </div>
  );
}


/** 放送局のカード。label は NOW・TODAY・THIS WEEK など（札）。read は既読を薄くするとき。 */
export function NewsCard({ item, channel, label, hideRemoves = false, dimRead = false, onTopic }: {
  item: NewsItem; channel?: CardChannel | null; label?: string; hideRemoves?: boolean; dimRead?: boolean; onTopic?: (topic: string) => void;
}) {
  const act = useNewsAction();
  const toast = useToasts((s) => s.show);
  const style = KIND_STYLE[item.kind] ?? KIND_STYLE.news;
  const color = channel?.color;
  const send = (action: NewsAction, on = true) => act.mutate({ id: item.id, action, on });
  const toggle = (action: NewsAction) => send(action, !item.state[action]);
  const hide = () => {
    send("hide", true);
    if (hideRemoves) toast("隠しました", "info", { label: "元に戻す", onClick: () => send("hide", false) });
  };
  const open = () => send("open");
  const archived = item.archived === true;      // RAG にだけ残る記事: 操作の相手（id）が無い
  return (
    <article data-news-card className={`nw-card${dimRead && item.state.open ? " read" : ""}`} style={color ? ({ "--ch": color } as CSSProperties) : undefined}>
      <Media item={item} color={color}>{label && <span className="nw-badge">{label}</span>}</Media>
      <div className="card-body">
        <p className="card-meta">
          <span className="dot" aria-hidden />
          {channel && <span className="nw-kicker" style={{ color: "inherit" }}>{chNo(channel.number)}</span>}
          <span>{style.icon} {style.label}</span>
          <span className="src">{item.source}</span>
          <span>· {relativeTime(item.published_at)}</span>
        </p>
        <h3 className="card-title"><ArticleLink item={item} onOpen={open}>{item.title}</ArticleLink></h3>
        {(item.summary || item.snippet) && <p className="card-sum">{item.summary || item.snippet}</p>}
        <TranslatedMark item={item} />
        {archived && <p className="nw-muted" style={{ margin: 0, fontSize: ".72rem" }}>アーカイブ（RAG にだけ残っている記事）</p>}
        {item.topics.length > 0 && (
          <ul className="card-chips" aria-label="話題">
            {item.topics.slice(0, 4).map((topic) => <li key={topic}><button type="button" onClick={() => onTopic?.(topic)}>{topic}</button></li>)}
          </ul>
        )}
        <div className="card-foot">
          {item.url ? <ArticleLink item={item} onOpen={open} className="card-read" label="読む →">読む →</ArticleLink> : <span />}
          {!archived && <div className="card-acts" role="group" aria-label="操作">
            <button type="button" aria-pressed={item.state.like} onClick={() => toggle("like")} title="もっと見たい">👍<span className="sr-only"> もっと</span></button>
            <button type="button" aria-pressed={item.state.dislike} onClick={() => toggle("dislike")} title="興味なし">👎<span className="sr-only"> 興味なし</span></button>
            <button type="button" aria-pressed={item.state.star} onClick={() => toggle("star")} title="保存">{item.state.star ? "★" : "☆"}<span className="sr-only"> 保存</span></button>
            <button type="button" onClick={hide}>隠す</button>
          </div>}
        </div>
      </div>
    </article>
  );
}
