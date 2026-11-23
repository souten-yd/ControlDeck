import { useMemo, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import {
  DEFAULT_PREFS, KIND_STYLE, LEGACY_DESIGNS, relativeTime, useDigests, useNewsAction, useNewsChannels, useNewsItems, useNewsPrefs, useNewsSettings,
  type Digest, type NewsItem,
} from "../../api/news";
import { Skeleton } from "../../components/ui";
import { Media } from "./NewsCard";
import { chNo, newsRootProps } from "./newsTheme";
import "../../styles/news.css";

const SHOWN = 5;

/** 今日の日付（YYYY-MM-DD・利用者の端末の時刻）。日刊のまとめの day と比べる。 */
const today = () => new Date().toLocaleDateString("en-CA");

/** 今日の日刊のまとめの本文の最初の 1〜2 文（平文）。今日の分でない・作られなかった・見出しの一覧（LLM なし）なら null。 */
export function digestLead(digest: Digest | null | undefined, day = today()): string | null {
  if (!digest || digest.period !== "daily" || digest.day !== day || !digest.body || digest.fallback) return null;
  const line = digest.body.split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith("#"));
  if (!line) return null;
  const plain = line.replace(/^[-・*]\s*/, "").replace(/\s*\[\d{1,3}\]/g, "").trim();
  const sentences = plain.match(/[^。！？]+[。！？]?/g) ?? [plain];
  const text = sentences.slice(0, 2).join("").trim();
  return text || null;
}

const hostOf = (url: string | null) => { try { return url ? new URL(url).hostname.replace(/^www\./, "") : ""; } catch { return ""; } };
const linkProps = (url: string) => (/^https?:/.test(url) ? { target: "_blank", rel: "noopener noreferrer" } : {});

/** ホームの小さなニュースのカード: 今朝のまとめ・おすすめの 1 位（画像つき）・残りの見出し。見た目はニュースのページの表示のデザインに合わせる。 */
export function NewsHomeCard() {
  const feedMinutes = useNewsSettings().data?.settings.feed_refresh_minutes ?? 0;      // 設定「ホームのフィードの更新の間隔」
  const { data, isLoading, isError } = useNewsItems({ view: "recommended", kinds: "", q: "", date: "", topic: "", source: "" }, true, feedMinutes);
  const prefs = useNewsPrefs().data ?? DEFAULT_PREFS;
  const channels = useNewsChannels().data?.channels ?? [];
  const digest = useDigests().data?.latest.daily;
  const act = useNewsAction();
  const items = (data?.pages[0]?.items ?? []).filter((i) => !i.state.hide).slice(0, SHOWN);
  const design = LEGACY_DESIGNS[prefs.display.design] ?? prefs.display.design;
  const lead = useMemo(() => digestLead(digest), [digest]);
  if (isError) return null;

  const channelOf = (item: NewsItem) => {
    const index = channels.findIndex((c) => c.id === item.channel);
    return index < 0 ? null : { ...channels[index], number: index + 1 };
  };
  const style = (item: NewsItem) => { const c = channelOf(item); return c ? ({ "--ch": c.color } as CSSProperties) : undefined; };
  const title = (item: NewsItem) => item.url
    ? <a href={item.url} {...linkProps(item.url)} onClick={() => act.mutate({ id: item.id, action: "open" })}>{item.title}</a>
    : <>{item.title}</>;
  const meta = (item: NewsItem) => {
    const c = channelOf(item);
    const kind = KIND_STYLE[item.kind] ?? KIND_STYLE.news;
    return (
      <p className="card-meta" style={{ margin: 0 }}>
        {c ? <span className="nwd-tag">{chNo(c.number)} {c.name}</span> : <span>{kind.icon} {kind.label}</span>}
        <span className="src">{item.source}</span>
        <span style={{ whiteSpace: "nowrap" }}>{relativeTime(item.published_at)}</span>
      </p>
    );
  };

  const [first, ...rest] = items;
  return (
    <section aria-labelledby="home-news" data-news-home-card {...newsRootProps(prefs.display)} className="nw nwh">
      <div className="nwh-head">
        <h2 id="home-news" className="nw-head nwh-title">ニュース</h2>
        <Link to="/news" className="nwh-open">ニュースを開く</Link>
      </div>
      {lead && (
        <Link to="/news" className="nwh-digest" data-testid="home-digest" aria-label={`今朝のまとめ: ${lead}`}>
          <span className="nw-badge">今朝のまとめ</span>
          <span className="nwh-digest-text">{lead}</span>
        </Link>
      )}
      {isLoading ? <Skeleton className="h-24" /> : items.length === 0 ? (
        <p className="text-xs nw-muted" style={{ margin: 0 }}>まだ記事がありません。ニュースのページで取得できます。</p>
      ) : design === "text" ? (
        <ol className="nwx-list" data-testid="home-rows">
          {items.map((item, n) => (
            <li key={item.id} className="nwx-row" style={style(item)}>
              <span className="nwx-rank">{n + 1}.</span>
              <div className="nwx-body">
                <p className="nwx-title">{title(item)} <span className="nwx-host">{hostOf(item.url) && `(${hostOf(item.url)})`}</span></p>
                <p className="nwx-meta">
                  {channelOf(item) && <span className="nwx-ch">{channelOf(item)!.name}</span>}
                  <span>{item.source}</span><span>{relativeTime(item.published_at)}</span>
                </p>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className="nwh-body">
          <article className="nwh-lead" data-testid="home-lead" style={style(first)}>
            <Media item={first} color={channelOf(first)?.color} className="nwd-lead-media" />
            <div className="nwd-lead-copy">
              {meta(first)}
              <h3 className="nwh-lead-title">{title(first)}</h3>
            </div>
          </article>
          {rest.length > 0 && (
            <ul className="nwh-rows" data-testid="home-rows">
              {rest.map((item) => (
                <li key={item.id} style={style(item)}>
                  <span className="nwh-dot" aria-hidden />
                  <span className="nwh-row-title">{title(item)}</span>
                  <span className="nwh-time">{relativeTime(item.published_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
