import { Fragment, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { DEFAULT_PREFS, relativeTime, useNewsPrefs, useReader, useReaderAction, type ReaderBlock, type ReaderPage } from "../api/news";
import { newsRootProps } from "../features/news/newsTheme";
import "../styles/news.css";

/** ニュースリーダー（/news/read/<記事の id>）。上から 要約 → 元の記事のリンク → 本文（英語なら日本語の訳）。
 *  取り出した本文をすぐ出し、要約と訳ができた段落から順に差し替える（2026-11-24 利用者の要望）。 */
const PHASES: Record<string, string> = {
  fetching: "本文を取り出しています…", queued: "CPU の LLM の順番を待っています（起動に少しかかります）…",
  summarizing: "要約を作っています…", translating: "訳しています…",
};

/** **太字** と `コード` だけを描く（外から来た文は平文。リンクや HTML は作らない）。 */
function inline(text: string): ReactNode {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

function Block({ block, showSrc }: { block: ReaderBlock; showSrc: boolean }) {
  const translatable = block.ja !== undefined;
  const pending = translatable && block.ja === null && !block.fail;
  const text = translatable && block.ja ? block.ja : block.src;
  const cls = pending ? "nr-pending" : undefined;
  const state = pending ? "pending" : block.ja ? "done" : block.fail ? "fail" : "plain";
  const body = (value: string): ReactNode => {
    if (block.t === "h") return null;
    if (block.t === "ul") return <ul>{value.split("\n").map((line, i) => <li key={i}>{inline(line)}</li>)}</ul>;
    if (block.t === "q") return <blockquote>{inline(value)}</blockquote>;
    return <p>{inline(value)}</p>;
  };
  if (block.t === "img") return <figure className="nr-fig"><img src={block.src} alt={block.alt ?? ""} loading="lazy" referrerPolicy="no-referrer" /></figure>;
  if (block.t === "code") return <pre className="nr-code"><code>{block.src}</code></pre>;
  if (block.t === "table") return <pre className="nr-code">{block.src}</pre>;
  const Heading = `h${Math.min(Math.max((block.level ?? 2) + 1, 2), 5)}` as "h2";
  return (
    <div className={`nr-block ${cls ?? ""}`} data-state={state} data-testid="reader-block">
      {block.t === "h" ? <Heading className="nr-h">{inline(text)}</Heading> : body(text)}
      {showSrc && translatable && block.ja && <div className="nr-src">{block.t === "h" ? block.src : body(block.src)}</div>}
      {block.fail && <p className="nr-note">この段落は訳せませんでした（原文のまま）。</p>}
    </div>
  );
}

function Summary({ page, retry }: { page: ReaderPage; retry: () => void }) {
  const lines = page.summary ? page.summary.split(/\n+/).filter(Boolean) : [];
  return (
    <section className="nr-summary" aria-labelledby="nr-sum-h" data-testid="reader-summary">
      <h2 id="nr-sum-h" className="nr-label">要約</h2>
      {lines.length > 0 ? lines.map((line, i) => <p key={i}>{line}</p>)
        : page.status === "failed" ? <p className="nr-muted">本文を取り出せなかったので、要約はありません。</p>
        : page.llm_error ? <p className="nr-muted">要約を作れませんでした: {page.llm_error} <button type="button" className="nw-btn" onClick={retry}>もう一度</button></p>
        : <p className="nr-muted" aria-live="polite">{(page.working && PHASES[page.phase]) || PHASES.summarizing}</p>}
    </section>
  );
}

export default function NewsReaderPage() {
  const id = Number(useParams().id);
  const prefs = (useNewsPrefs().data ?? DEFAULT_PREFS).display;
  const query = useReader(id);
  const more = useReaderAction(id, "more");
  const retry = useReaderAction(id, "retry");
  const [showSrc, setShowSrc] = useState(false);
  const page = query.data;
  const root = newsRootProps(prefs);
  const back = <Link to="/news" className="nw-btn" data-testid="reader-back">← ニュース</Link>;
  if (query.isError || !Number.isFinite(id)) {
    return <div {...root}><div className="nr"><p>{back}</p><p role="alert">記事を読み込めませんでした。</p></div></div>;
  }
  if (!page) return <div {...root}><div className="nr"><p>{back}</p><p className="nr-muted">読み込んでいます…</p></div></div>;
  const item = page.item;
  const translating = page.lang !== "ja" && page.progress.total > 0;
  const finished = page.progress.done >= page.progress.total;
  const generated = item.image_url?.startsWith("/api/v1/news/images/");
  return (
    <div {...root}>
      <article className="nr" data-testid="reader" data-status={page.status}>
        <div className="nr-bar">{back}</div>
        <p className="nr-meta"><span>{item.source}</span><span>· {relativeTime(item.published_at)}</span></p>
        <h1 className="nr-title" data-testid="reader-title">{item.title}</h1>
        {item.image_url && <figure className="nr-hero"><img src={item.image_url} alt="" referrerPolicy="no-referrer" />{generated && <span className="nw-ai-mark">AI 生成のイメージ</span>}</figure>}

        <Summary page={page} retry={() => retry.mutate()} />

        <p className="nr-origin" data-testid="reader-origin">
          <span className="nr-label">元の記事</span>
          <a href={page.source_url} target="_blank" rel="noopener noreferrer" className="nr-url">{page.domain}<span className="nr-fullurl">{page.source_url}</span></a>
          <a href={page.source_url} target="_blank" rel="noopener noreferrer" className="nw-btn on" data-testid="reader-open-original">元の記事を開く ↗</a>
        </p>

        {page.status === "fetching" && <p className="nr-muted" aria-live="polite" data-testid="reader-fetching">{PHASES.fetching}</p>}
        {page.status === "failed" && (
          <div className="nr-failed" role="status" data-testid="reader-failed">
            {page.excerpt && <p>{page.excerpt}</p>}
            <p className="nr-muted">本文を取り出せませんでした。{page.error && `（${page.error}）`}元の記事を開いてください。</p>
            <button type="button" className="nw-btn" disabled={retry.isPending} onClick={() => retry.mutate()}>もう一度取り出す</button>
          </div>
        )}
        {page.status === "ready" && (
          <>
            <div className="nr-tools">
              {translating && <span className="nr-progress" role="status" data-testid="reader-progress">訳 {page.progress.done}/{page.progress.total}{page.working && !finished ? "…" : ""}</span>}
              {page.lang === "ja" && <span className="nr-muted">日本語の記事なので、そのまま表示しています。</span>}
              {translating && <label className="nr-toggle"><input type="checkbox" checked={showSrc} onChange={(e) => setShowSrc(e.target.checked)} /> 原文も表示</label>}
              {page.working && page.phase === "queued" && <span className="nr-muted">{PHASES.queued}</span>}
            </div>
            {page.llm_error && translating && !finished && (
              <p className="nr-muted" role="status">訳が止まりました: {page.llm_error} <button type="button" className="nw-btn" onClick={() => retry.mutate()}>続きから訳す</button></p>
            )}
            <div className="nr-body" data-testid="reader-body">
              {page.blocks.map((block, i) => <Block key={i} block={block} showSrc={showSrc} />)}
            </div>
            {page.remaining > 0 && finished && !page.working && (
              <p><button type="button" className="nw-btn on" data-testid="reader-more" disabled={more.isPending} onClick={() => more.mutate()}>続きを訳す（残り {page.remaining} 段落）</button></p>
            )}
          </>
        )}
      </article>
    </div>
  );
}
