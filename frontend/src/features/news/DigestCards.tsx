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

/** まとめの本文（「## 見出し」と「- 文」だけの Markdown）と、出典の一覧。 */
export function DigestBody({ digest }: { digest: Digest }) {
  const blocks: ReactNode[] = [];
  let bullets: ReactNode[] = [];
  const flush = () => { if (bullets.length) { blocks.push(<ul key={`u${blocks.length}`} className="nw-digest-list">{bullets}</ul>); bullets = []; } };
  digest.body.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line) return;
    if (line.startsWith("#")) { flush(); blocks.push(<h4 key={i} className="nw-digest-ch">{line.replace(/^#+\s*/, "")}</h4>); }
    else if (/^[-・*]\s/.test(line)) bullets.push(<li key={i}>{withCitations(line.replace(/^[-・*]\s*/, ""), digest.sources)}</li>);
    else { flush(); blocks.push(<p key={i} style={{ margin: "4px 0" }}>{withCitations(line, digest.sources)}</p>); }
  });
  flush();
  return (
    <div data-testid="digest-body">
      {blocks}
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
