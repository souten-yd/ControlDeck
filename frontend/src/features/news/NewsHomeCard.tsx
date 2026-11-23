import { useMemo, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import {
  DEFAULT_PREFS, KIND_STYLE, LEGACY_DESIGNS, relativeTime, useDigests, useNewsAction, useNewsChannels, useNewsItems, useNewsPrefs, useNewsSettings,
  type Digest, type NewsItem,
} from "../../api/news";
import { Marquee } from "../../components/Marquee";
import { Skeleton } from "../../components/ui";
import { Media } from "./NewsCard";
import { chNo, newsRootProps } from "./newsTheme";
import "../../styles/news.css";


/** 今日の日付（YYYY-MM-DD・利用者の端末の時刻）。日刊のまとめの day と比べる。 */
const dayString = (offset = 0) => { const d = new Date(); d.setDate(d.getDate() + offset); return d.toLocaleDateString("en-CA"); };
const today = () => dayString(0);

const clean = (line: string) => line.replace(/^[-・*]\s*/, "").replace(/\s*\[\d{1,3}\]/g, "").trim();
const TOP3 = /^#{1,4}\s*今日の\s*3\s*行/;

/** まとめの本文の概要の行。「## 今日の 3 行」があればその箇条書き（最大 4 行）、無ければ（古い形）最初の見出しでない 3 行。 */
export function digestLines(body: string): string[] {
  const rows = body.split("\n").map((l) => l.trim());
  const at = rows.findIndex((l) => TOP3.test(l));
  let picked: string[];
  if (at >= 0) {
    const rest = rows.slice(at + 1);
    const end = rest.findIndex((l) => l.startsWith("#"));
    picked = (end < 0 ? rest : rest.slice(0, end)).filter(Boolean);
  } else {
    picked = rows.filter((l) => l && !l.startsWith("#")).slice(0, 3);
  }
  return picked.map(clean).filter(Boolean).slice(0, 4);
}

export interface DigestSummary { label: string; lines: string[]; day: string }

/** 今日の日刊のまとめ、無ければ昨日の日刊のまとめの概要。どちらも作れていない（本文が空・見出しの一覧だけ）なら null。 */
export function digestSummary(digests: (Digest | null | undefined)[], day = today(), yesterday = dayString(-1)): DigestSummary | null {
  const daily = digests.filter((d): d is Digest => !!d && d.period === "daily" && !!d.body && !d.fallback);
  for (const [want, label] of [[day, "今朝のまとめ"], [yesterday, "昨日のまとめ"]] as const) {
    const found = daily.find((d) => d.day === want);
    const lines = found ? digestLines(found.body) : [];
    if (found && lines.length) return { label, lines, day: want };
  }
  return null;
}

/** 今日の日刊のまとめの本文の最初の 1〜2 文（平文）。今日の分でない・作られなかった・見出しの一覧（LLM なし）なら null。 */
export function digestLead(digest: Digest | null | undefined, day = today()): string | null {
  if (!digest || digest.period !== "daily" || digest.day !== day || !digest.body || digest.fallback) return null;
  const line = digestLines(digest.body)[0];
  if (!line) return null;
  const sentences = line.match(/[^。！？]+[。！？]?/g) ?? [line];
  return sentences.slice(0, 2).join("").trim() || null;
}

const hostOf = (url: string | null) => { try { return url ? new URL(url).hostname.replace(/^www\./, "") : ""; } catch { return ""; } };
const linkProps = (url: string) => (/^https?:/.test(url) ? { target: "_blank", rel: "noopener noreferrer" } : {});

/** ホームの小さなニュースのカード: 今朝のまとめ・おすすめの 1 位（画像つき）・残りの見出し。見た目はニュースのページの表示のデザインに合わせる。 */
export function NewsHomeCard() {
  const feedMinutes = useNewsSettings().data?.settings.feed_refresh_minutes ?? 0;      // 設定「ホームのフィードの更新の間隔」
  const { data, isLoading, isError } = useNewsItems({ view: "recommended", kinds: "", q: "", date: "", topic: "", source: "" }, true, feedMinutes);
  const prefs = useNewsPrefs().data ?? DEFAULT_PREFS;
  const channels = useNewsChannels().data?.channels ?? [];
  const digests = useDigests().data;
  const act = useNewsAction();
  const shown = Math.min(10, Math.max(1, prefs.display.home_items ?? 5));       // 設定 → ニュース → 表示「ホームに出す記事の数」
  const all = (data?.pages[0]?.items ?? []).filter((i) => !i.state.hide);
  const items = all.slice(0, shown);
  const design = LEGACY_DESIGNS[prefs.display.design] ?? prefs.display.design;
  const summary = useMemo(() => digestSummary([digests?.latest.daily, ...(digests?.digests ?? [])]), [digests]);
  // テロップに流す項目の配列（共通の Marquee 部品へつなぐ形）: まとめの各行、無ければ新しい見出しの上位数件
  // 依存は読み直しで毎回新しくなる配列ではなく「文字」（同じ文字なら同じ配列を返し、Marquee の項目を作り直さない）
  const telopText = summary ? summary.lines.join("\n") : all.slice(0, 5).map((i) => i.title).join("\n");
  const telop = useMemo(() => (telopText ? telopText.split("\n") : []), [telopText]);
  const telopItems = useMemo(() => telop.map((text, n) => ({ key: `${n}:${text}`, node: text })), [telop]);
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
      {telop.length > 0 && (
        <div className="nw-ticker nwh-telop" data-testid="home-telop" role="group" aria-label="テロップ">
          <span className="nw-ticker-label">NEWS</span>
          {/* 流す項目は配列。複数なら 1 件ずつ流して縦にめくり、1 件ならそれを流し続ける（共通の Marquee）。ニュースの「動きを減らす」は CSS（data-reduce）で流れを止めて省略表示にする */}
          <Marquee className="min-w-0 flex-1 text-[.85rem]" items={telopItems} />
        </div>
      )}
      {summary && (
        <Link to="/news" className="nwh-digest" data-testid="home-digest" aria-label={`${summary.label}: ${summary.lines.join(" ")}`}>
          <span className="nw-badge">{summary.label}</span>
          <span className="nwh-digest-text">{summary.lines.join(" ・ ")}</span>
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
