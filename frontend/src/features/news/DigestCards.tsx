import { Fragment, useState, type ReactNode } from "react";
import { useDigest, useDigests, type Digest } from "../../api/news";

const external = (url: string) => /^https?:\/\//.test(url);

/** [n] を出典の記事へのリンクにする。本文は文字として出す（HTML は解釈しない）。 */
function withCitations(text: string, sources: Digest["sources"]): ReactNode[] {
  return text.split(/(\[\d{1,3}\])/).map((part, i) => {
    const m = /^\[(\d{1,3})\]$/.exec(part);
    const src = m ? sources?.find((s) => s.n === Number(m[1])) : undefined;
    if (!src?.url) return <Fragment key={i}>{part}</Fragment>;
    return <a key={i} href={src.url} title={src.title} {...(external(src.url) ? { target: "_blank", rel: "noopener noreferrer" } : {})} className="nw-cite">{part}</a>;
  });
}

const TREND = /^(傾向|週の傾向|主な技術・製品|注目点)\s*[:：]\s*(.*)$/;
const isThree = (heading: string) => /3\s*行/.test(heading);

/** まとめの本文（「## 話題」と「- 傾向: …」「- 主な技術・製品: …」「- 注目点: …」、最後の「今日の 3 行」）と、出典の一覧。
 *  「3 行」は読む順が先になるよう、画面では一番上に出す。見出しの一覧（LLM なし）の「- 題（出どころ） [n]」もそのまま読める。 */
export function DigestBody({ digest }: { digest: Digest }) {
  const sections: { heading: string; rows: ReactNode[]; para: ReactNode[] }[] = [];
  let current = { heading: "", rows: [] as ReactNode[], para: [] as ReactNode[] };
  const push = () => { if (current.heading || current.rows.length || current.para.length) sections.push(current); };
  digest.body.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line) return;
    if (line.startsWith("#")) { push(); current = { heading: line.replace(/^#+\s*/, ""), rows: [], para: [] }; }
    else if (/^[-・*]\s/.test(line)) {
      const text = line.replace(/^[-・*]\s*/, "");
      const m = TREND.exec(text);
      current.rows.push(m
        ? <li key={i} className="nw-digest-kv"><b className="nw-digest-k">{m[1]}</b><span>{withCitations(m[2], digest.sources)}</span></li>
        : <li key={i}>{withCitations(text, digest.sources)}</li>);
    } else current.para.push(<p key={i} style={{ margin: "4px 0" }}>{withCitations(line, digest.sources)}</p>);
  });
  push();
  const ordered = [...sections.filter((s) => isThree(s.heading)), ...sections.filter((s) => !isThree(s.heading))];
  return (
    <div data-testid="digest-body">
      {ordered.map((s, i) => (
        <section key={i} className={isThree(s.heading) ? "nw-digest-three" : "nw-digest-topic"} data-testid={isThree(s.heading) ? "digest-three" : "digest-topic"}>
          {s.heading && <h4 className="nw-digest-ch">{s.heading}</h4>}
          {s.para}
          {s.rows.length > 0 && <ul className="nw-digest-list">{s.rows}</ul>}
        </section>
      ))}
      {digest.sources && digest.sources.length > 0 && (
        <details className="nw-digest-src">
          <summary>出典（{digest.sources.length} 件）</summary>
          <ol>{digest.sources.map((s) => (
            <li key={s.n} value={s.n}>{s.url ? <a href={s.url} {...(external(s.url) ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{s.title}</a> : s.title}<span className="nw-muted"> · {s.source}</span></li>
          ))}</ol>
        </details>
      )}
    </div>
  );
}

function Badges({ digest }: { digest: Digest }) {
  return (
    <>
      {digest.fallback && <span className="nw-badge line" data-testid="digest-fallback" title={digest.error || "記事がありません"}>見出しの一覧（LLM なし）</span>}
      {!digest.fallback && digest.model && digest.body && <span className="nw-muted" style={{ fontSize: ".7rem" }}>{digest.model.replace(/^cloud:/, "")}</span>}
    </>
  );
}

const dayLabel = (d: Digest) => (d.period === "weekly" ? `${d.day} の週` : d.day);

/** 1 つのまとめ（開くと本文）。作らなかった回は理由を出す。 */
function DigestCard({ digest, period }: { digest: Digest | null; period: "daily" | "weekly" }) {
  const title = period === "daily" ? "今朝のまとめ" : "今週のまとめ";
  if (!digest) return <article className="nw-digest" data-testid={`digest-${period}`}><p className="nw-kicker" style={{ margin: 0 }}>{title}</p><p className="nw-muted" style={{ margin: "6px 0 0", fontSize: ".8rem" }}>まだ作られていません。</p></article>;
  const failed = !digest.body;
  return (
    <details className="nw-digest" data-testid={`digest-${period}`} open={false}>
      <summary>
        <span className="nw-kicker">{digest.label}</span>
        <span className="nw-muted" style={{ fontSize: ".75rem" }}>{dayLabel(digest)}</span>
        <Badges digest={digest} />
        {failed && <span className="nw-badge line" data-testid="digest-failed">作れませんでした</span>}
      </summary>
      {failed ? <p role="alert" className="nw-note" style={{ marginTop: 8 }}>{digest.error}</p> : <DigestBody digest={digest} />}
    </details>
  );
}

/** /news の上（放送の前）の「今朝のまとめ」「今週のまとめ」。 */
export function DigestCards() {
  const { data, isError } = useDigests();
  if (isError || !data) return null;
  return (
    <section className="nw-digests" aria-label="まとめ">
      <DigestCard digest={data.latest.daily} period="daily" />
      <DigestCard digest={data.latest.weekly} period="weekly" />
    </section>
  );
}

function ArchivedDigest({ digest }: { digest: Digest }) {
  const [open, setOpen] = useState(false);
  const detail = useDigest(open ? digest.id : null);
  return (
    <li>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="nw-digest-row">
        <span>{digest.label} · {dayLabel(digest)}</span>
        {digest.fallback && <span className="nw-badge line">見出しの一覧</span>}
        {!digest.body && <span className="nw-badge line">作れませんでした</span>}
      </button>
      {open && (detail.data ? (digest.body ? <DigestBody digest={detail.data} /> : <p className="nw-note">{digest.error}</p>) : <div className="nw-skel" />)}
    </li>
  );
}

/** アーカイブのまとめの一覧。 */
export function DigestArchive() {
  const { data } = useDigests();
  if (!data || data.digests.length === 0) return null;
  return (
    <section aria-labelledby="digest-archive" style={{ marginTop: 0, marginBottom: 12 }}>
      <h2 id="digest-archive" className="nw-sec-title" style={{ fontSize: "1.1rem" }}>まとめ</h2>
      <ul className="nw-digest-archive">{data.digests.map((d) => <ArchivedDigest key={d.id} digest={d} />)}</ul>
    </section>
  );
}
