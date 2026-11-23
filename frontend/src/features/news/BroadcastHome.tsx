import { TranslatedMark } from "./TranslatedMark";
import { useState, type CSSProperties } from "react";
import { relativeTime, type NewsHome, type NewsItem, type NewsPrefs } from "../../api/news";
import { InterestPanel, TrendsPanel } from "./NewsAside";
import { Media, NewsCard, type CardChannel } from "./NewsCard";
import { chNo } from "./newsTheme";

export const external = (url: string) => /^https?:\/\//.test(url);
export const visible = (items: NewsItem[]) => items.filter((i) => !i.state.hide);

/** 1. ON AIR: モニターの枠に 1 本と、下にチャンネルの切り替え（CH 01〜）。押すとそのチャンネルの 1 位に変わる。
 *  携帯ではモニターが先頭（CSS の order）。 */
function OnAir({ home, onTopic, onOpen }: { home: NewsHome; onTopic: (t: string) => void; onOpen: (id: number) => void }) {
  const [choice, setChoice] = useState("all");
  const section = home.sections.find((s) => s.id === choice);
  const item = choice === "all" ? (home.onair && !home.onair.state.hide ? home.onair : null) : visible(section?.items ?? [])[0] ?? null;
  const channel = home.channels.find((c) => c.id === (choice === "all" ? item?.channel : choice));
  const color = channel?.color;
  const link = item?.url ? { href: item.url, ...(external(item.url) ? { target: "_blank", rel: "noopener noreferrer" } : {}), onClick: () => onOpen(item.id) } : null;
  return (
    <section className="nw-onair" aria-label="ON AIR" style={color ? ({ "--ch": color } as CSSProperties) : undefined}>
      <div className="nw-onair-copy">
        <p className="nw-live"><i aria-hidden /> ON AIR{channel && <span className="nw-kicker" data-testid="onair-channel"> · {chNo(channel.number)} · {channel.name}</span>}</p>
        {item ? (
          <>
            <h2 className="nw-head nw-onair-title" data-testid="onair-title">{link ? <a {...link}>{item.title}</a> : item.title}</h2>
            {item.summary && <p className="nw-muted" style={{ margin: 0, lineHeight: 1.6 }}>{item.summary}</p>}
            <TranslatedMark item={item} />
            <p className="card-meta"><span>{item.source}</span><span>· {relativeTime(item.published_at)}</span></p>
            {item.topics.length > 0 && <ul className="card-chips" aria-label="話題">{item.topics.slice(0, 4).map((t) => <li key={t}><button type="button" onClick={() => onTopic(t)}>{t}</button></li>)}</ul>}
            {link && <a className="nw-btn on" style={{ alignSelf: "flex-start" }} {...link} aria-label="読む →">読む →</a>}
          </>
        ) : <p className="nw-muted">このチャンネルにはまだ記事がありません。</p>}
      </div>
      <div className="nw-onair-monitor">
        <div className="nw-monitor">
          <div className="nw-screen" role="img" aria-label={item ? `モニター: ${item.title}` : "モニター"}>
            {item ? <Media item={item} color={color} className="nw-screen-inner">{null}</Media> : <div className="nw-fallback">📺</div>}
            <div className="nw-screen-badge"><span className="nw-badge">ON AIR</span>{channel && <span className="nw-badge line" style={{ background: "rgba(0,0,0,.55)", color: "#fff" }}>{chNo(channel.number)}</span>}</div>
          </div>
        </div>
        <div className="nw-switch" role="group" aria-label="チャンネルの切り替え">
          <button type="button" aria-pressed={choice === "all"} onClick={() => setChoice("all")}>ALL</button>
          {home.channels.map((c) => (
            <button key={c.id} type="button" aria-pressed={choice === c.id} title={c.name} onClick={() => setChoice(c.id)} style={{ "--ch": c.color } as CSSProperties}>{chNo(c.number)}</button>
          ))}
        </div>
      </div>
    </section>
  );
}

/** 2. テロップ: 新しい見出しが横に流れる。止められる（動きを減らす設定・OS の設定では止まる）。 */
function Ticker({ home, paused, onPause }: { home: NewsHome; paused: boolean; onPause: () => void }) {
  if (home.ticker.length === 0) return null;
  return (
    <section className="nw-ticker" aria-label="テロップ">
      <span className="nw-ticker-label">NOW SHOWING</span>
      <div className="nw-ticker-lane"><div className="nw-ticker-track" data-testid="ticker-track">
        {/* 切れ目なく流すため同じ並びを 2 回出す（2 回目は読み上げない） */}
        {[0, 1].map((copy) => (
          <span key={copy} className={copy ? "nw-dup" : "nw-ticker-set"} aria-hidden={copy ? true : undefined}>
            {home.ticker.map((t) => (
              <span key={t.id} className="nw-ticker-item"><span className="src">{t.source}</span>{t.url
                ? <a href={t.url} tabIndex={copy ? -1 : undefined} {...(external(t.url) ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{t.title}</a> : t.title}</span>
            ))}
          </span>
        ))}
      </div></div>
      <button type="button" className="nw-ticker-pause" aria-label={paused ? "テロップを流す" : "テロップを止める"} aria-pressed={paused} onClick={onPause}>{paused ? "▶" : "❚❚"}</button>
    </section>
  );
}

export function Trio({ home, prefs, onTopic, channelOf }: { home: NewsHome; prefs: NewsPrefs; onTopic: (t: string) => void; channelOf: (item: NewsItem) => CardChannel | null }) {
  const cols = [["now", "NOW", "いま", home.now], ["today", "TODAY", "今日", home.today], ["week", "THIS WEEK", "今週", home.week]] as const;
  return (
    <section aria-label="NOW・TODAY・THIS WEEK" className="nw-trio">
      {cols.map(([id, label, jp, items]) => (
        <div key={id} className="nw-trio-col" data-testid={`bucket-${id}`}>
          <h3 className="nw-head"><span className="nw-kicker" style={{ display: "block" }}>{jp}</span>{label}</h3>
          {visible(items).length === 0 ? <p className="nw-empty">まだありません</p> : visible(items).map((item) => (
            <NewsCard key={item.id} item={item} label={label} channel={channelOf(item)} hideRemoves dimRead={prefs.taste.dim_read} onTopic={onTopic} />
          ))}
        </div>
      ))}
    </section>
  );
}

/** 興味と話題の動き（全デザイン共通）。 */
export function HomeAside({ home, onTopic }: { home: NewsHome; onTopic: (t: string) => void }) {
  return (
    <section aria-label="あなたの興味と話題の動き" className="nw-two">
      <InterestPanel data={home.interest} onTopic={onTopic} />
      <TrendsPanel data={home.trends} onTopic={onTopic} />
    </section>
  );
}

/** 数字で見る（全デザイン共通）。 */
export function NumbersSection({ home }: { home: NewsHome }) {
  const n = home.numbers;
  return (
    <section aria-labelledby="nw-numbers">
      <div className="nw-sec-head"><div><span className="nw-ch-tag">BY THE NUMBERS</span><h2 id="nw-numbers" className="nw-head nw-sec-title">数字で見る</h2></div></div>
      <div className="nw-numbers-grid" data-testid="numbers">
        <div className="nw-num"><div className="nw-big">{n.today}</div><div className="nw-muted">今日の記事数</div></div>
        <div className="nw-num"><div className="nw-big">{n.read_today}</div><div className="nw-muted">今日読んだ数</div></div>
        <div className="nw-num" style={n.top_channel ? ({ "--ch": n.top_channel.color } as CSSProperties) : undefined}>
          <div className="nw-big text">{n.top_channel ? n.top_channel.name : "—"}</div><div className="nw-muted">よく読むチャンネル{n.top_channel ? `（${n.top_channel.reads} 件）` : ""}</div></div>
        <div className="nw-num"><div className="nw-big text">{n.top_topic ?? "—"}</div><div className="nw-muted">話題の上位{n.top_topic ? `（${n.top_topic_count} 件）` : ""}</div></div>
      </div>
    </section>
  );
}

/** アーカイブへの入口（全デザイン共通）。 */
export function ArchiveSection({ onArchive }: { onArchive: () => void }) {
  return (
    <section aria-labelledby="nw-archive">
      <div className="nw-sec-head"><div><span className="nw-ch-tag">ARCHIVE</span><h2 id="nw-archive" className="nw-head nw-sec-title">アーカイブ</h2></div>
        <button type="button" className="nw-more" onClick={onArchive}>日付・保存した記事・検索へ →</button></div>
    </section>
  );
}

/** チャンネルごとの区画（全デザイン共通。並べ方は CSS がデザインごとに変える）。 */
export function ChannelSections({ home, prefs, onTopic, onChannel }: { home: NewsHome; prefs: NewsPrefs; onTopic: (t: string) => void; onChannel: (id: string) => void }) {
  const byId = new Map(home.channels.map((c) => [c.id, c]));
  return (
    <>
      {home.sections.map((section) => {
        const items = visible(section.items);
        return (
          <section key={section.id} aria-labelledby={`ch-${section.id}`} data-testid={`section-${section.id}`} style={{ "--ch": section.color } as CSSProperties}>
            <div className="nw-sec-head">
              <div><span className="nw-ch-tag">{chNo(section.number)} · {section.name}</span>
                <h2 id={`ch-${section.id}`} className="nw-head nw-sec-title">{section.name}<span className="nw-muted" style={{ fontSize: ".9rem", marginLeft: 10, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>今週 {section.week} 件</span></h2></div>
              <button type="button" className="nw-more" onClick={() => onChannel(section.id)} aria-label={`${section.name} をすべて見る`}>すべて見る →</button>
            </div>
            {items.length === 0 ? <p className="nw-empty">語に合う記事がまだありません。設定 → ニュース で語を足せます。</p> : (
              <div className="nw-grid">{items.map((item) => <NewsCard key={item.id} item={item} channel={byId.get(section.id)} hideRemoves dimRead={prefs.taste.dim_read} onTopic={onTopic} />)}</div>
            )}
          </section>
        );
      })}
    </>
  );
}

/** 3〜6. チャンネルごとの区画・興味と話題の動き・数字で見る。 */
export function BroadcastHome({ home, prefs, paused, onPause, onTopic, onOpen, onChannel, onArchive }: {
  home: NewsHome; prefs: NewsPrefs; paused: boolean; onPause: () => void; onTopic: (t: string) => void; onOpen: (id: number) => void;
  onChannel: (id: string) => void; onArchive: () => void;
}) {
  const d = prefs.display;
  const byId = new Map(home.channels.map((c) => [c.id, c]));
  const channelOf = (item: NewsItem) => (item.channel ? byId.get(item.channel) ?? null : null);
  return (
    <>
      {d.onair && <OnAir home={home} onTopic={onTopic} onOpen={onOpen} />}
      {d.ticker && <Ticker home={home} paused={paused} onPause={onPause} />}
      <Trio home={home} prefs={prefs} onTopic={onTopic} channelOf={channelOf} />
      <ChannelSections home={home} prefs={prefs} onTopic={onTopic} onChannel={onChannel} />
      <HomeAside home={home} onTopic={onTopic} />
      {d.numbers && <NumbersSection home={home} />}
      <ArchiveSection onArchive={onArchive} />
    </>
  );
}
