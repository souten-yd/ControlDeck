import { Sparkline } from "../../components/ui";
import { useMuteTopic, type NewsProfile, type NewsTrends } from "../../api/news";

/** 重みを文字の大きさ（4 段）にする。 */
function sizeStyle(weight: number, top: number): { fontSize: string; fontWeight: number } {
  const ratio = top > 0 ? weight / top : 0;
  return ratio > 0.75 ? { fontSize: "1.1rem", fontWeight: 700 } : ratio > 0.5 ? { fontSize: "0.95rem", fontWeight: 600 } : ratio > 0.25 ? { fontSize: "0.82rem", fontWeight: 500 } : { fontSize: "0.72rem", fontWeight: 400 };
}

/** あなたの興味: 開いた・👍・保存した記事とチャットの題から。語ごとに外せる（外した語は点に使わない）。 */
export function InterestPanel({ data, onTopic }: { data: NewsProfile; onTopic: (topic: string) => void }) {
  const mute = useMuteTopic();
  const top = Math.max(...data.topics.map((t) => t.weight), 0);
  return (
    <div className="nw-panel" role="region" aria-labelledby="news-interest">
      <h3 id="news-interest" className="nw-head">あなたの興味</h3>
      <p className="nw-muted" style={{ fontSize: ".72rem", margin: 0 }}>開いた・👍・保存した記事と、チャットの題（秘密の会話は除く）から。外した語はおすすめに使いません。</p>
      {data.topics.length === 0 ? <p className="nw-muted" style={{ fontSize: ".8rem" }}>記事を開いたり 👍 を押すと、ここに興味が出ます。</p> : (
        <ul className="nw-tags">
          {data.topics.map((t) => (
            <li key={t.topic} className="nw-tag">
              <button type="button" onClick={() => onTopic(t.topic)} title={`重み ${t.weight}`} style={sizeStyle(t.weight, top)}>{t.topic}</button>
              <button type="button" className="x" aria-label={`「${t.topic}」を興味から外す`} disabled={mute.isPending} onClick={() => mute.mutate({ topic: t.topic, muted: true })}>×</button>
            </li>
          ))}
        </ul>
      )}
      {data.sources.length > 0 && <p className="nw-muted" style={{ fontSize: ".72rem" }}>よく読むソース: {data.sources.slice(0, 4).map((s) => s.source).join("・")}</p>}
      {data.avoided_sources.length > 0 && <p className="nw-muted" style={{ fontSize: ".72rem" }}>下げているソース: {data.avoided_sources.map((s) => s.source).join("・")}</p>}
      {data.muted.length > 0 && (
        <p className="nw-muted" style={{ fontSize: ".72rem" }}>外した語:{" "}
          {data.muted.map((word) => (
            <button key={word} type="button" onClick={() => mute.mutate({ topic: word, muted: false })} aria-label={`「${word}」を戻す`}
              style={{ background: "none", border: "1px dashed currentColor", borderRadius: 999, padding: "0 8px", marginRight: 4, textDecoration: "line-through" }}>{word}</button>
          ))}
        </p>
      )}
    </div>
  );
}

/** 話題の動き: 上位 8 語の 14 日の件数（折れ線）。 */
export function TrendsPanel({ data, onTopic }: { data: NewsTrends; onTopic: (topic: string) => void }) {
  return (
    <div className="nw-panel" role="region" aria-labelledby="news-trends">
      <h3 id="news-trends" className="nw-head">話題の動き</h3>
      <p className="nw-muted" style={{ fontSize: ".72rem", margin: 0 }}>この 14 日で記事に多く出た語</p>
      {data.topics.length === 0 ? <p className="nw-muted" style={{ fontSize: ".8rem" }}>記事が集まると、話題の動きが出ます。</p> : (
        <ul style={{ padding: 0, margin: "8px 0 0" }}>
          {data.topics.map((t) => (
            <li key={t.topic} className="nw-trend">
              <button type="button" onClick={() => onTopic(t.topic)}>{t.topic}</button>
              <span className="spark"><Sparkline values={t.counts} max={Math.max(...t.counts, 1)} fill heightClass="h-5" /></span>
              <span className="n">{t.total}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
