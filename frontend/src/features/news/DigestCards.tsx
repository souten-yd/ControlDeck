import { Fragment, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useDigest, useDigests, type Digest } from "../../api/news";
import { digestLines } from "./NewsHomeCard";

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
const TECH = "主な技術・製品";

/** まとめから話題の絞り込みへ移るための手がかり。出典の記事の話題（topics）と、見出しがチャンネル名のときのチャンネル。 */
export interface DigestLinks {
  /** 出典の記事の話題の一覧（まとめの語と一致したものだけを押せる印にする） */
  topics: string[];
  onTopic: (topic: string) => void;
  /** 見出し（## 話題名）がチャンネル名なら、そのチャンネルで絞り込む */
  channelOf?: (heading: string) => string | null;
  onChannel?: (id: string) => void;
}

const escapeRe = (w: string) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** 「主な技術・製品」の文の中の、出典の記事の話題と一致する語を押せる印（ボタン）にする。大文字小文字は区別しない。 */
function withTopics(nodes: ReactNode[], text: string, links: DigestLinks | undefined, digest: Digest): ReactNode[] {
  const words = [...new Set(links?.topics ?? [])].filter((w) => w.length >= 2).sort((a, b) => b.length - a.length);
  if (!links || !words.length) return nodes;
  const re = new RegExp(`(${words.map(escapeRe).join("|")})`, "i");
  return text.split(re).flatMap((part, i): ReactNode[] => {
    const hit = words.find((w) => w.toLowerCase() === part.toLowerCase());
    if (hit && i % 2 === 1) return [<button key={`t${i}`} type="button" className="nw-digest-topic-btn" data-testid="digest-topic-link" onClick={() => links.onTopic(hit)}>{part}</button>];
    return withCitations(part, digest.sources).map((n, j) => <Fragment key={`${i}-${j}`}>{n}</Fragment>);
  });
}

/** まとめの本文（「## 話題」と「- 傾向: …」「- 主な技術・製品: …」「- 注目点: …」、最後の「今日の 3 行」）と、出典の一覧。
 *  「3 行」は読む順が先になるよう、画面では一番上に出す。見出しの一覧（LLM なし）の「- 題（出どころ） [n]」もそのまま読める。
 *  links があれば、見出し（チャンネル名）と「主な技術・製品」の語を押して絞り込みへ移れる。 */
export function DigestBody({ digest, links }: { digest: Digest; links?: DigestLinks }) {
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
        ? <li key={i} className="nw-digest-kv"><b className="nw-digest-k">{m[1]}</b><span>{m[1] === TECH ? withTopics(withCitations(m[2], digest.sources), m[2], links, digest) : withCitations(m[2], digest.sources)}</span></li>
        : <li key={i}>{withCitations(text, digest.sources)}</li>);
    } else current.para.push(<p key={i} style={{ margin: "4px 0" }}>{withCitations(line, digest.sources)}</p>);
  });
  push();
  const ordered = [...sections.filter((s) => isThree(s.heading)), ...sections.filter((s) => !isThree(s.heading))];
  const headingLink = (heading: string): (() => void) | null => {
    if (!links || isThree(heading)) return null;
    const ch = links.channelOf?.(heading);
    if (ch && links.onChannel) return () => links.onChannel!(ch);
    const t = links.topics.find((w) => w.toLowerCase() === heading.toLowerCase());
    return t ? () => links.onTopic(t) : null;
  };
  return (
    <div data-testid="digest-body">
      {ordered.map((s, i) => {
        const go = s.heading ? headingLink(s.heading) : null;
        return (
          <section key={i} className={isThree(s.heading) ? "nw-digest-three" : "nw-digest-topic"} data-testid={isThree(s.heading) ? "digest-three" : "digest-topic"}>
            {s.heading && <h4 className="nw-digest-ch">{go
              ? <button type="button" className="nw-digest-topic-btn nw-digest-head-btn" data-testid="digest-heading-link" onClick={go} title="この話題の新着を見る">{s.heading}<span aria-hidden> ›</span></button>
              : s.heading}</h4>}
            {s.para}
            {s.rows.length > 0 && <ul className="nw-digest-list">{s.rows}</ul>}
          </section>
        );
      })}
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
const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches
  || !!document.querySelector('.nw[data-reduce="true"]');

type Period = "daily" | "weekly";

/** 1 つのバブル。閉じているときは小さな札（名前・日付・一言）、開くと横幅いっぱいに広がって本文が出る。
 *  開いた中身をタップすると小さく戻る（中のリンク・ボタン・出典の開閉は戻さない）。 */
function Bubble({ digest, period, open, links, onToggle }: { digest: Digest | null; period: Period; open: boolean; links: DigestLinks; onToggle: () => void }) {
  const title = period === "daily" ? "今朝のまとめ" : "今週のまとめ";
  if (!digest) {
    return (
      <article className="nw-bubble" data-testid={`digest-${period}`} data-open="false">
        <p className="nw-kicker" style={{ margin: 0 }}>{title}</p>
        <p className="nw-muted nw-bubble-hint">まだ作られていません。</p>
      </article>
    );
  }
  const failed = !digest.body;
  const hint = failed ? "" : digestLines(digest.body)[0] ?? "";
  const close = (e: MouseEvent) => {
    if ((e.target as Element).closest("a,button,summary,details,input,textarea")) return;      // 中のリンク・話題・出典の開閉では戻さない
    if (window.getSelection()?.toString()) return;                                           // 文字を選んでいるときも戻さない
    onToggle();
  };
  return (
    <article className="nw-bubble" data-testid={`digest-${period}`} data-open={open ? "true" : "false"} data-bubble>
      <button type="button" className="nw-bubble-head" aria-expanded={open} aria-label={`${digest.label}${open ? "を小さくする" : "を開く"}`} onClick={onToggle}>
        <span className="nw-kicker">{digest.label}</span>
        <span className="nw-muted nw-bubble-day">{dayLabel(digest)}</span>
        <span className="nw-bubble-chev" aria-hidden>{open ? "－" : "＋"}</span>
        {!open && hint && <span className="nw-bubble-hint" data-testid="digest-hint">{hint.replace(/\s*\[\d{1,3}\]/g, "")}</span>}
        {!open && failed && <span className="nw-badge line" data-testid="digest-failed">作れませんでした</span>}
        {digest.fallback && !open && <span className="nw-badge line" data-testid="digest-fallback" title={digest.error || "記事がありません"}>見出しの一覧（LLM なし）</span>}
      </button>
      {open && (
        <div className="nw-bubble-body" data-testid="digest-open" onClick={close}>
          <div className="nw-bubble-badges"><Badges digest={digest} />{failed && <span className="nw-badge line" data-testid="digest-failed">作れませんでした</span>}</div>
          {failed ? <p role="alert" className="nw-note" style={{ marginTop: 8 }}>{digest.error}</p> : <DigestBody digest={digest} links={links} />}
          <p className="nw-muted nw-bubble-tip">中をタップすると小さくなります</p>
        </div>
      )}
    </article>
  );
}

/** /news の上（放送の前）の「今朝のまとめ」「今週のまとめ」。どの幅でも 1 行に 2 つの小さなバブルを並べ、
 *  タップした 1 つが横幅いっぱいに広がって中身が開く（もう 1 つは小さいまま下に残る）。
 *  広がる・縮む動きは FLIP（位置と幅のなめらかな移動・本文の高さ）。動きを減らす設定では短くする。 */
export function DigestCards({ onTopic, channels = [], onChannel }: { onTopic: (topic: string) => void; channels?: { id: string; name: string }[]; onChannel?: (id: string) => void }) {
  const { data, isError } = useDigests();
  const [open, setOpen] = useState<Period | null>(null);
  const root = useRef<HTMLElement>(null);
  const rects = useRef(new Map<string, DOMRect>());

  // FLIP: 開閉の前後でバブルの位置・幅が変わった分を、前の位置から現在へなめらかに動かす
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const quick = reducedMotion();
    el.querySelectorAll<HTMLElement>("[data-bubble]").forEach((b) => {
      const id = b.dataset.testid ?? "";
      const prev = rects.current.get(id), now = b.getBoundingClientRect();
      rects.current.set(id, now);
      if (!prev || typeof b.animate !== "function") return;
      const dx = prev.left - now.left, dy = prev.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(prev.width - now.width) < 1) return;
      b.animate([
        { transform: `translate3d(${dx}px,${dy}px,0)`, width: `${prev.width}px` },
        { transform: "translate3d(0,0,0)", width: `${now.width}px` },
      ], { duration: quick ? 80 : 260, easing: "cubic-bezier(.2,.8,.2,1)" });
    });
    el.querySelectorAll<HTMLElement>(".nw-bubble-body").forEach((body) => {
      if (typeof body.animate !== "function") return;
      body.animate([{ opacity: 0, transform: "translate3d(0,-6px,0)" }, { opacity: 1, transform: "translate3d(0,0,0)" }], { duration: quick ? 80 : 240, easing: "ease-out" });
    });
  }, [open]);

  if (isError || !data) return null;
  const topics = [...new Set([data.latest.daily, data.latest.weekly].flatMap((d) => (d?.sources ?? []).flatMap((s) => s.topics ?? [])))];
  const links: DigestLinks = {
    topics, onTopic,
    channelOf: (heading) => channels.find((c) => c.name === heading)?.id ?? null,
    onChannel,
  };
  const toggle = (p: Period) => () => {
    // 動かす前の位置を覚える（FLIP の「前」）
    root.current?.querySelectorAll<HTMLElement>("[data-bubble]").forEach((b) => rects.current.set(b.dataset.testid ?? "", b.getBoundingClientRect()));
    setOpen((cur) => (cur === p ? null : p));
  };
  return (
    <section ref={root} className="nw-digests nw-bubbles" aria-label="まとめ" data-testid="digests" data-open={open ?? "none"}>
      <Bubble digest={data.latest.daily} period="daily" open={open === "daily"} links={links} onToggle={toggle("daily")} />
      <Bubble digest={data.latest.weekly} period="weekly" open={open === "weekly"} links={links} onToggle={toggle("weekly")} />
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
