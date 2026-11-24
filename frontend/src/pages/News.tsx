import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useSwipe } from "../components/useSwipe";
import { relativeTime, DEFAULT_PREFS, LEGACY_DESIGNS, useNewsAction, useNewsChannels, useNewsHome, useNewsItems, useNewsPrefs, useNewsSources, useRefreshNews, useSemanticSearch, type NewsDesign, type NewsItem, type NewsView } from "../api/news";
import { useToasts } from "../stores";
import { DesignHome } from "../features/news/DesignHomes";
import { BroadcastHome } from "../features/news/BroadcastHome";
import { NewsCard } from "../features/news/NewsCard";
import { ArchiveDays } from "../features/news/NewsArchive";
import { DigestArchive, DigestCards } from "../features/news/DigestCards";
import { chNo, newsRootProps } from "../features/news/newsTheme";
import "../styles/news.css";

type Tab = "home" | "latest" | "archive";
const TABS = [["home", "放送"], ["latest", "新着"], ["archive", "アーカイブ"]] as const;
// フォーラムの chip は Reddit も含む（どちらも「人の話し合い」）
const CHIPS = [["", "すべて"], ["release", "更新"], ["news", "ニュース"], ["model", "モデル"], ["forum,reddit", "フォーラム"], ["event", "出来事"]] as const;

export default function NewsPage() {
  const toast = useToasts((s) => s.show);
  const prefsQuery = useNewsPrefs();
  const prefs = prefsQuery.data ?? DEFAULT_PREFS;
  const [tab, setTab] = useState<Tab>("home");
  const [kinds, setKinds] = useState("");
  const [channel, setChannel] = useState("");
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const [topic, setTopic] = useState("");
  const [starred, setStarred] = useState(false);
  const [paused, setPaused] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);   // 狭い幅では検索窓はふだん虫めがねだけ。押すと広がる
  const [semantic, setSemantic] = useState(false);      // 検索を「意味で探す（RAG）」にする
  useEffect(() => {
    const t = setTimeout(() => setQ(draft), 300);
    return () => clearTimeout(t);
  }, [draft]);

  const searching = q.trim() !== "";
  const showHome = tab === "home" && !searching && !topic;
  const archiveList = tab === "archive" && !date && !starred && !searching;   // 日付の一覧を見せている間は記事を取らない
  const view: NewsView = starred && tab === "archive" ? "starred" : "latest";
  const home = useNewsHome();
  const channels = useNewsChannels();
  const useRag = semantic && searching;
  const items = useNewsItems({ view, kinds, q, date: tab === "archive" ? date : "", topic, source: "", channel }, !showHome && !archiveList && !useRag);
  const rag = useSemanticSearch(q, useRag);
  const sources = useNewsSources();
  const refresh = useRefreshNews();
  const act = useNewsAction();
  const hideRemoves = tab !== "archive" && !searching;
  const list = useMemo(
    () => (useRag ? rag.data?.items ?? [] : (items.data?.pages.flatMap((p) => p.items) ?? []).filter((item) => !(hideRemoves && item.state.hide))),
    [items.data, hideRemoves, useRag, rag.data],
  );
  const listLoading = useRag ? rag.isLoading : items.isLoading;
  const listError = useRag ? rag.isError : items.isError;
  const chList = (home.data?.channels ?? channels.data?.channels.map((c, i) => ({ ...c, number: i + 1 })) ?? []);
  const chById = new Map(chList.map((c) => [c.id, c]));
  const notes = (sources.data?.sources ?? []).filter((s) => s.skipped);

  const pickTopic = (t: string) => { setTopic(t); setTab((cur) => (cur === "home" ? "latest" : cur)); };
  const pickChannel = (id: string) => { setChannel(id); setTab("latest"); setDate(""); setStarred(false); };
  const runRefresh = () => refresh.mutate(undefined, {
    onSuccess: (r) => toast(!r.started ? (r.reason === "running" ? "取得中です。しばらくお待ちください" : "直前に取得したばかりです")
      : `取り直しました（新着 ${r.new ?? 0} 件）`, r.started ? "success" : "info"),
    onError: () => toast("取り直せませんでした", "error"),
  });
  const title = searching ? `「${q.trim()}」の${useRag ? "意味での検索結果" : "検索結果"}` : topic ? `話題「${topic}」` : date ? `${date} の記事` : starred ? "保存した記事" : "";
  const root = newsRootProps(prefs.display, paused);
  const design = (LEGACY_DESIGNS[prefs.display.design] ?? prefs.display.design) as NewsDesign;
  // 新着・アーカイブの記事の一覧: 左右に指でずらすと前後のチャンネル（全チャンネル → CH 01 → …）
  const chOrder = ["", ...chList.map((c) => c.id)];
  const listSwipe = useSwipe<HTMLDivElement>({ index: Math.max(0, chOrder.indexOf(channel)), count: chOrder.length, onIndex: (n) => setChannel(chOrder[n]), mode: "swap", disabled: showHome || archiveList || useRag });
  const filtered = Boolean(searching || topic || date || starred || kinds || channel);

  return (
    <div className="mx-auto max-w-6xl p-2 md:p-6">
      <div {...root} data-testid="news-root" data-news-root>
        <header className="nw-bar" data-search={searchOpen || draft ? "open" : "closed"} data-testid="news-bar">
          <div className="nw-bar-title">
            <h1 className="nw-head">News</h1>
            <p className="nw-kicker">あなたの放送局</p>
          </div>
          <div className="nw-bar-tools">
            <button type="button" className="nw-icon" aria-label="取り直す" title="取り直す" onClick={runRefresh} disabled={refresh.isPending}>
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden className={refresh.isPending ? "nw-spin" : undefined}><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></svg>
            </button>
            <button type="button" className="nw-icon nw-bar-searchbtn" aria-label="検索を開く" title="検索" aria-expanded={searchOpen} onClick={() => setSearchOpen(true)}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          </button>
            <Link to="/settings/news" className="nw-icon" aria-label="設定" title="設定">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
            </Link>
          </div>
          <div className="nw-utabs" role="tablist" aria-label="表示">
            {TABS.map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} aria-pressed={tab === id}
                onClick={() => { setTab(id); setDate(""); setStarred(false); setTopic(""); if (id === "home") { setChannel(""); } }}>{label}</button>
            ))}
          </div>
          <div className="nw-bar-search">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden className="nw-bar-mag"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            <input type="search" className="nw-search" aria-label="アーカイブ全体を検索" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={semantic ? "意味で探す" : "アーカイブ全体を検索"} />
            <div role="group" aria-label="検索のしかた" className="nw-seg">
              <button type="button" aria-pressed={!semantic} onClick={() => setSemantic(false)}>全文</button>
              <button type="button" aria-pressed={semantic} aria-label="意味で探す" onClick={() => setSemantic(true)} title="意味の近さで探します（取り込んだ記事。ニュースのデータベースから消えた古い記事も出ます）">意味</button>
            </div>
            <button type="button" className="nw-bar-close" aria-label="検索を閉じる" onClick={() => { setSearchOpen(false); setDraft(""); }}>×</button>
          </div>
        </header>
        {notes.length > 0 && (
          <details className="nw-note">
            <summary style={{ cursor: "pointer" }}>取得していないソースがあります</summary>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {notes.map((s) => <li key={s.source_key}>{s.label}: {s.skipped}</li>)}
            </ul>
          </details>
        )}
        {showHome && <DigestCards onTopic={pickTopic} channels={chList} onChannel={pickChannel} />}
        {showHome ? (
          home.isLoading ? <div className="nw-skel" aria-busy="true" aria-label="読み込み中" style={{ minHeight: 320 }} /> : home.isError || !home.data ? (
            <p role="alert" className="nw-note">放送を読み込めませんでした。</p>
          ) : home.data.sections.every((s) => s.items.length === 0) && !home.data.onair ? (
            <div className="nw-empty">
              <p>まだ記事がありません。</p>
              <button type="button" className="nw-btn on" onClick={runRefresh} disabled={refresh.isPending}>今すぐ取得する</button>
            </div>
          ) : design !== "broadcast" ? (
            <DesignHome design={design} home={home.data} prefs={prefs} onTopic={pickTopic} onOpen={(id) => act.mutate({ id, action: "open" })} onChannel={pickChannel} onArchive={() => setTab("archive")} />
          ) : (
            <BroadcastHome home={home.data} prefs={prefs} paused={paused || prefs.display.reduce_motion} onPause={() => setPaused((p) => !p)} onTopic={pickTopic}
              onOpen={(id) => act.mutate({ id, action: "open" })} onChannel={pickChannel} onArchive={() => setTab("archive")} />
          )
        ) : (
          <div>
            {!archiveList && (
              <>
                <div role="group" aria-label="チャンネル" className="nw-tabs" style={{ marginBottom: 8 }}>
                  <button type="button" className="nw-btn" aria-pressed={channel === ""} onClick={() => setChannel("")}>全チャンネル</button>
                  {chList.map((c) => <button key={c.id} type="button" className="nw-btn" aria-pressed={channel === c.id} onClick={() => setChannel(c.id)}>{chNo(c.number)} {c.name}</button>)}
                </div>
                <div role="group" aria-label="種類" className="nw-tabs" style={{ marginBottom: 12 }}>
                  {CHIPS.map(([id, label]) => <button key={id} type="button" className="nw-btn" aria-pressed={kinds === id} onClick={() => setKinds(id)}>{label}</button>)}
                </div>
              </>
            )}
            <div ref={listSwipe.ref} {...listSwipe.handlers} className="nw-swipe" data-testid="list-swipe">
            {(title || (tab === "archive" && !archiveList)) && (
              <div className="nw-top" style={{ justifyContent: "flex-start" }}>
                {tab === "archive" && !searching && (date || starred) && <button type="button" className="nw-btn" onClick={() => { setDate(""); setStarred(false); }}>← 日付の一覧へ</button>}
                {title && <h2 className="nw-head" style={{ fontSize: "1.3rem" }}>{title}</h2>}
                {topic && <button type="button" className="nw-btn" onClick={() => setTopic("")}>話題の絞り込みを外す ×</button>}
              </div>
            )}
            {archiveList && (
              <>
                <button type="button" className="nw-btn" style={{ width: "100%", marginBottom: 8 }} onClick={() => setStarred(true)}>☆ 保存した記事を見る</button>
                <DigestArchive />
                <ArchiveDays onPick={setDate} />
              </>
            )}
            {!archiveList && (listLoading ? <div className="nw-skel" aria-busy="true" aria-label="読み込み中" /> : listError ? (
              <p role="alert" className="nw-note">記事を読み込めませんでした。</p>
            ) : list.length === 0 ? (
              <div className="nw-empty">
                <p>{filtered ? "該当する記事がありません。" : "まだ記事がありません。"}</p>
                {!filtered && <button type="button" className="nw-btn on" onClick={runRefresh} disabled={refresh.isPending}>今すぐ取得する</button>}
              </div>
            ) : (
              <>
                <div className="nw-grid">
                  {list.map((item: NewsItem) => <NewsCard key={item.id} item={item} channel={item.channel ? chById.get(item.channel) : channel ? chById.get(channel) : null}
                    hideRemoves={hideRemoves} dimRead={prefs.taste.dim_read} onTopic={pickTopic} />)}
                </div>
                {!useRag && items.hasNextPage && <button type="button" className="nw-btn" style={{ width: "100%", marginTop: 12 }} onClick={() => items.fetchNextPage()} disabled={items.isFetchingNextPage}>{items.isFetchingNextPage ? "読み込み中…" : "もっと見る"}</button>}
              </>
            ))}
            </div>
          </div>
        )}
        {sources.data?.last.finished && <p className="nw-muted" style={{ fontSize: ".7rem", marginTop: 20 }}>最後に取得: {relativeTime(sources.data.last.finished)}</p>}
      </div>
    </div>
  );
}
