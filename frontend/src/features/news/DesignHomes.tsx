import { TranslatedMark } from "./TranslatedMark";
import type { CSSProperties, ReactNode } from "react";
import { KIND_STYLE, relativeTime, type NewsHome, type NewsItem, type NewsPrefs } from "../../api/news";
import { ArchiveSection, ChannelSections, external, HomeAside, NumbersSection, Trio, visible } from "./BroadcastHome";
import { Media } from "./NewsCard";
import { ArticleLink } from "./ArticleLink";
import { chNo } from "./newsTheme";

/** 放送局以外のデザインの「ホーム」。中身（まとめ・ON AIR の 1 本・チャンネル・数字・アーカイブ）は放送局と同じデータで、
 *  ここは並べ方だけを変える部品の集まり。区画・数字・アーカイブは BroadcastHome の共通部品を使う（データの取り方を重ねて作らない）。
 *  参考にした型（ロゴ・文言・画像は写さない）: マガジン=The Verge、新聞=日本経済新聞、経済紙=Financial Times の色、テキスト一覧=Hacker News。 */
export interface DesignHomeProps {
  home: NewsHome; prefs: NewsPrefs; onTopic: (t: string) => void; onOpen: (id: number) => void; onChannel: (id: string) => void; onArchive: () => void;
}

/** 記事への a の属性（外部は別タブ・開いたら既読の記録）。 */
function Title({ item, onOpen }: { item: NewsItem; onOpen: (id: number) => void }) {
  return <ArticleLink item={item} onOpen={() => onOpen(item.id)}>{item.title}</ArticleLink>;
}
const clock = (iso: string) => new Date(iso).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", hour12: false });
const domain = (url: string | null) => { try { return url && external(url) ? new URL(url).hostname.replace(/^www\./, "") : ""; } catch { return ""; } };
const numberAfter = (text: string, unit: string) => text.match(new RegExp(`([\\d,]+)\\s*${unit}`))?.[1] ?? "";

/** 表の先頭（ON AIR と同じ 1 本）と、いまの記事の重複のない一覧。 */
function useStories(home: NewsHome, showLead: boolean) {
  const lead = showLead && home.onair && !home.onair.state.hide ? home.onair : null;
  const seen = new Set<number>(lead ? [lead.id] : []);
  const rest: NewsItem[] = [];
  for (const item of [...visible(home.now), ...visible(home.today), ...visible(home.week), ...home.sections.flatMap((s) => visible(s.items))]) {
    if (!seen.has(item.id)) { seen.add(item.id); rest.push(item); }
  }
  return { lead, rest };
}

function ChannelNav({ home, onChannel, className, divider }: { home: NewsHome; onChannel: (id: string) => void; className: string; divider?: string }) {
  return (
    <nav className={className} aria-label="チャンネル">
      {home.channels.map((c, i) => (
        <span key={c.id} className="nwd-nav-item" style={{ "--ch": c.color } as CSSProperties}>
          {divider && i > 0 && <i aria-hidden>{divider}</i>}
          <button type="button" onClick={() => onChannel(c.id)}>{c.name}</button>
        </span>
      ))}
    </nav>
  );
}

/** 先頭の 1 本（画像・題・リード・出どころ）。 */
function Lead({ item, home, onOpen, onTopic, testId }: { item: NewsItem; home: NewsHome; onOpen: (id: number) => void; onTopic: (t: string) => void; testId: string }) {
  const channel = home.channels.find((c) => c.id === item.channel);
  const style = KIND_STYLE[item.kind] ?? KIND_STYLE.news;
  return (
    <article className="nwd-lead" data-testid={testId} style={channel ? ({ "--ch": channel.color } as CSSProperties) : undefined}>
      <Media item={item} color={channel?.color} className="nwd-lead-media">{null}</Media>
      <div className="nwd-lead-copy">
        <p className="card-meta">{channel && <span className="nwd-tag">{channel.name}</span>}<span>{style.icon} {style.label}</span><span className="src">{item.source}</span><span>· {relativeTime(item.published_at)}</span></p>
        <h2 className="nw-head nwd-lead-title"><Title item={item} onOpen={onOpen} /></h2>
        {item.summary && <p className="nwd-lead-sum">{item.summary}</p>}
        <TranslatedMark item={item} />
        {item.topics.length > 0 && <ul className="card-chips" aria-label="話題">{item.topics.slice(0, 4).map((t) => <li key={t}><button type="button" onClick={() => onTopic(t)}>{t}</button></li>)}</ul>}
      </div>
    </article>
  );
}

/** 行（小さな画像つきの縦の一覧）。 */
function Rows({ items, home, onOpen, label, testId, withTime, thumbs = true }: { items: NewsItem[]; home: NewsHome; onOpen: (id: number) => void; label: string; testId: string; withTime?: boolean; thumbs?: boolean }) {
  return (
    <aside className="nwd-rows" aria-label={label} data-testid={testId}>
      <h3 className="nw-head nwd-rows-head">{label}</h3>
      <ol>
        {items.map((item) => {
          const channel = home.channels.find((c) => c.id === item.channel);
          return (
            <li key={item.id} style={channel ? ({ "--ch": channel.color } as CSSProperties) : undefined}>
              {withTime && <time className="nwd-time" dateTime={item.published_at}>{clock(item.published_at)}</time>}
              {thumbs && <Media item={item} color={channel?.color} className="nwd-thumb">{null}</Media>}
              <div className="nwd-row-copy">
                <p className="nwd-row-title"><Title item={item} onOpen={onOpen} /></p>
                <p className="card-meta"><span className="src">{item.source}</span><span>· {relativeTime(item.published_at)}</span></p>
              </div>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

function Tail({ home, prefs, onTopic, onChannel, onArchive, numbers = true }: DesignHomeProps & { numbers?: boolean }) {
  return (
    <>
      <ChannelSections home={home} prefs={prefs} onTopic={onTopic} onChannel={onChannel} />
      <HomeAside home={home} onTopic={onTopic} />
      {numbers && prefs.display.numbers && <NumbersSection home={home} />}
      <ArchiveSection onArchive={onArchive} />
    </>
  );
}

/** マガジン: 左に大きな画像の 1 本、右に LATEST の縦の一覧。上に「/」で区切ったチャンネルのナビ。 */
export function MagazineHome(props: DesignHomeProps) {
  const { home, prefs, onOpen, onTopic, onChannel } = props;
  const { lead, rest } = useStories(home, prefs.display.onair);
  return (
    <>
      <ChannelNav home={home} onChannel={onChannel} className="nwd-nav nwm-nav" divider="/" />
      <div className="nwm-top">
        {lead && <Lead item={lead} home={home} onOpen={onOpen} onTopic={onTopic} testId="mag-lead" />}
        <Rows items={rest.slice(0, 8)} home={home} onOpen={onOpen} label="LATEST" testId="mag-latest" />
      </div>
      <Tail {...props} />
    </>
  );
}

/** 新聞: 紺の帯のナビ、数字の帯（話題の語の件数と前日比）、左に画像と見出しとリード・右に時刻つきの速報。 */
export function NewspaperHome(props: DesignHomeProps) {
  const { home, prefs, onOpen, onTopic, onChannel } = props;
  const { lead } = useStories(home, prefs.display.onair);
  const flash = home.ticker.slice(0, 10);
  const stats = home.trends.topics.slice(0, 4).map((t) => ({ topic: t.topic, today: t.counts[t.counts.length - 1] ?? 0, diff: (t.counts[t.counts.length - 1] ?? 0) - (t.counts[t.counts.length - 2] ?? 0) }));
  return (
    <>
      <ChannelNav home={home} onChannel={onChannel} className="nwd-nav nwn-nav" />
      {prefs.display.numbers && (
        <section className="nwn-band" aria-label="数字の帯" data-testid="np-band">
          <div className="nwn-stat"><span className="nwn-label">今日の記事</span><b className="nwn-val">{home.numbers.today}</b></div>
          {stats.map((s) => (
            <button key={s.topic} type="button" className="nwn-stat" onClick={() => onTopic(s.topic)}>
              <span className="nwn-label">{s.topic}</span><b className="nwn-val">{s.today}<small> 件</small></b>
              <span className={`nwn-diff ${s.diff > 0 ? "up" : s.diff < 0 ? "down" : ""}`}>前日比 {s.diff > 0 ? "▲" : s.diff < 0 ? "▼" : "±"}{Math.abs(s.diff)}</span>
            </button>
          ))}
        </section>
      )}
      <div className="nwn-top">
        {lead && <Lead item={lead} home={home} onOpen={onOpen} onTopic={onTopic} testId="np-lead" />}
        <aside className="nwd-rows nwn-flash" aria-label="速報" data-testid="np-flash">
          <h3 className="nw-head nwd-rows-head">速報</h3>
          <ol>
            {flash.map((t) => (
              <li key={t.id}>
                <time className="nwd-time" dateTime={t.published_at}>{clock(t.published_at)}</time>
                <p className="nwd-row-title">{t.url ? <a href={t.url} {...(external(t.url) ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{t.title}</a> : t.title}</p>
              </li>
            ))}
          </ol>
        </aside>
      </div>
      <Tail {...props} numbers={false} />
    </>
  );
}

/** 経済紙: サーモンの地に細い罫線。見出し中心の 1 本と、3 つの時間帯の列。画像は小さめ。 */
export function FinanceHome(props: DesignHomeProps) {
  const { home, prefs, onOpen, onTopic, onChannel } = props;
  const { lead } = useStories(home, prefs.display.onair);
  const byId = new Map(home.channels.map((c) => [c.id, c]));
  return (
    <>
      <ChannelNav home={home} onChannel={onChannel} className="nwd-nav nwf-nav" />
      {lead && <Lead item={lead} home={home} onOpen={onOpen} onTopic={onTopic} testId="fin-lead" />}
      <Trio home={home} prefs={prefs} onTopic={onTopic} channelOf={(item) => (item.channel ? byId.get(item.channel) ?? null : null)} />
      <Tail {...props} />
    </>
  );
}

/** テキスト一覧: 番号つきの密な見出し・点数／コメント数／出どころのドメイン。画像なし。 */
export function TextHome(props: DesignHomeProps) {
  const { home, prefs, onOpen, onChannel } = props;
  const { lead, rest } = useStories(home, prefs.display.onair);
  const stories: NewsItem[] = [...(lead ? [lead] : []), ...rest].slice(0, 30);
  let list: ReactNode;
  if (stories.length === 0) list = <p className="nw-empty">まだ記事がありません。</p>;
  else list = (
    <ol className="nwx-list" data-testid="text-list">
      {stories.map((item, i) => {
        const points = numberAfter(item.summary, "点"), comments = numberAfter(item.summary, "コメント");
        const host = domain(item.url);
        const channel = home.channels.find((c) => c.id === item.channel);
        return (
          <li key={item.id} className="nwx-row" style={channel ? ({ "--ch": channel.color } as CSSProperties) : undefined}>
            <span className="nwx-rank" aria-hidden>{i + 1}.</span>
            <div className="nwx-body">
              <p className="nwx-title"><Title item={item} onOpen={onOpen} />{host && <span className="nwx-host"> ({host})</span>}</p>
              <p className="nwx-meta">
                {points && <span>{points} 点</span>}{comments && <span>{comments} コメント</span>}
                <span>{item.source}</span><span>{relativeTime(item.published_at)}</span>
                {channel && <span className="nwx-ch">{chNo(channel.number)} {channel.name}</span>}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
  return (
    <>
      <ChannelNav home={home} onChannel={onChannel} className="nwd-nav nwx-nav" divider="|" />
      {list}
      <HomeAside home={home} onTopic={props.onTopic} />
      {prefs.display.numbers && <NumbersSection home={home} />}
      <ArchiveSection onArchive={props.onArchive} />
    </>
  );
}

export function DesignHome({ design, ...props }: DesignHomeProps & { design: "magazine" | "newspaper" | "finance" | "text" }) {
  return design === "magazine" ? <MagazineHome {...props} /> : design === "newspaper" ? <NewspaperHome {...props} /> : design === "finance" ? <FinanceHome {...props} /> : <TextHome {...props} />;
}
