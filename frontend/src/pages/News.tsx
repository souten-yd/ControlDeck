import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
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
  const failed = (sources.data?.sources ?? []).filter((s) => !s.ok);
  const notes = (sources.data?.sources ?? []).filter((s) => s.skipped);

  const pickTopic = (t: string) => { setTopic(t); setTab((cur) => (cur === "home" ? "latest" : cur)); };
  const pickChannel = (id: string) => { setChannel(id); setTab("latest"); setDate(""); setStarred(false); };
  const runRefresh = () => refresh.mutate(undefined, {
    onSuccess: (r) => toast(!r.started ? (r.reason === "running" ? "取得中です。しばらくお待ちください" : "直前に取得したばかりです")
      : `取り直しました（新着 ${r.new ?? 0} 件${r.failed ? `・取れなかったソース ${r.failed} 件` : ""}）`, r.started ? "success" : "info"),
    onError: () => toast("取り直せませんでした", "error"),
  });
  const title = searching ? `「${q.trim()}」の${useRag ? "意味での検索結果" : "検索結果"}` : topic ? `話題「${topic}」` : date ? `${date} の記事` : starred ? "保存した記事" : "";
  const root = newsRootProps(prefs.display, paused);
  const design = (LEGACY_DESIGNS[prefs.display.design] ?? prefs.display.design) as NewsDesign;
  const filtered = Boolean(searching || topic || date || starred || kinds || channel);

  return (
    <div className="mx-auto max-w-6xl p-2 md:p-6">
      <div {...root} data-testid="news-root" data-news-root>
        <header className="nw-top">
          <div style={{ minWidth: 0 }}>
            <p className="nw-kicker" style={{ margin: 0 }}>あなたの放送局</p>
            <h1 className="nw-head" style={{ fontSize: "clamp(1.6rem, 5vw, 2.4rem)" }}>News</h1>
          </div>
          <div className="nw-top-actions">
            <button type="button" className="nw-btn" onClick={runRefresh} disabled={refresh.isPending}>{refresh.isPending ? "取得中…" : "取り直す"}</button>
            <Link to="/settings/news" className="nw-btn">⚙ 設定</Link>
          </div>
        </header>
        <div className="nw-top">
          <div className="nw-tabs" role="tablist" aria-label="表示">
            {TABS.map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} aria-pressed={tab === id} className="nw-btn"
                onClick={() => { setTab(id); setDate(""); setStarred(false); setTopic(""); if (id === "home") { setChannel(""); } }}>{label}</button>
            ))}
          </div>
          <div className="nw-sem-mode">
            <input type="search" className="nw-search" aria-label="アーカイブ全体を検索" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={semantic ? "意味で探す（RAG）" : "アーカイブ全体を検索"} />
            <div role="group" aria-label="検索のしかた" className="nw-tabs">
              <button type="button" className="nw-btn" aria-pressed={!semantic} onClick={() => setSemantic(false)}>全文</button>
              <button type="button" className="nw-btn" aria-pressed={semantic} onClick={() => setSemantic(true)} title="意味の近さで探します（取り込んだ記事。ニュースのデータベースから消えた古い記事も出ます）">意味で探す</button>
            </div>
          </div>
        </div>
        {(failed.length > 0 || notes.length > 0) && (
          <details className="nw-note">
            <summary style={{ cursor: "pointer" }}>{failed.length > 0 ? `取れなかったソースが ${failed.length} 件あります` : "取得していないソースがあります"}</summary>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {failed.map((s) => <li key={s.source_key}>{s.label}: {s.error}</li>)}
              {notes.map((s) => <li key={s.source_key}>{s.label}: {s.skipped}</li>)}
            </ul>
          </details>
        )}
        {showHome && <DigestCards />}
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
        )}
        {sources.data?.last.finished && <p className="nw-muted" style={{ fontSize: ".7rem", marginTop: 20 }}>最後に取得: {relativeTime(sources.data.last.finished)}</p>}
      </div>
    </div>
  );
}
