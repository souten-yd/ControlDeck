/** ニュースの専門ページ API の型と取得。backend/app/news/router.py と対になる。 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export type NewsKind = "release" | "news" | "model" | "forum" | "reddit" | "event";
export type NewsAction = "open" | "like" | "dislike" | "hide" | "star";
export type NewsState = Record<NewsAction, boolean>;
export type NewsView = "recommended" | "latest" | "starred";

export interface NewsItem {
  id: number; kind: NewsKind; source: string; source_key: string; title: string; url: string | null;
  summary: string; image_url: string | null; image_ai?: boolean; published_at: string; topics: string[]; state: NewsState; score?: number;
  /** チャンネルの id（どのチャンネルに属するか。無ければ null） */
  channel?: string | null;
  /** 意味で探す（RAG）の結果: 当たった断片。archived はニュースの DB から消えて RAG にだけ残る記事（id=0・操作はできない） */
  snippet?: string; archived?: boolean;
  /** 英語の抜粋を日本語に訳したもの（summary がその訳になり、元の抜粋は summary_original） */
  translated?: boolean; summary_original?: string;
  /** リーダーで本文を取り出せなかった記事（一覧・カードは元の記事への直リンクにする） */
  reader_failed?: boolean;
}
export interface NewsPage { items: NewsItem[]; next: string | null }
export interface NewsQuery { view: NewsView; kinds: string; q: string; date: string; topic: string; source: string; channel?: string }

/** チャンネル＝検索テーマ（backend/app/news/channels.py）。 */
export interface NewsChannel {
  id: string; name: string; color: string; include: string[]; exclude: string[]; kinds: string[];
  lang: "ja" | "en" | "both"; limit: number; search: boolean;
}
export interface ChannelsConfig { channels: NewsChannel[]; archive_keep: number; interval_minutes: number }
export interface ChannelsResponse extends ChannelsConfig {
  kinds: Record<string, string>; reddit_key: boolean; limits: { channels: number; terms: number; palette: string[] };
}

/** 表示のデザインと好み（利用者ごと）。 */
export type NewsDesign = "broadcast" | "magazine" | "newspaper" | "finance" | "text";
/** 旧い名前の移し替え: 保存済みの「紙面」は経済紙、「コンパクト」はテキスト一覧として扱う。 */
export const LEGACY_DESIGNS: Record<string, NewsDesign> = { paper: "finance", compact: "text" };
export interface NewsDisplay {
  design: NewsDesign; accent: string; font_size: "s" | "m" | "l"; density: "relaxed" | "normal" | "tight"; images: boolean;
  columns: "auto" | "1" | "2" | "3"; ticker: boolean; onair: boolean; numbers: boolean; reduce_motion: boolean;
  /** ホームのニュースのカードに出す記事の数（1〜10） */
  home_items: number;
}
export interface NewsTaste {
  strength: number; use_chat: boolean; half_life_days: number; mute_terms: string[]; mute_sources: string[]; dim_read: boolean;
}
export interface NewsPrefs { display: NewsDisplay; taste: NewsTaste }
export const DEFAULT_PREFS: NewsPrefs = {
  display: { design: "broadcast", accent: "", font_size: "m", density: "normal", images: true, columns: "auto", ticker: true, onair: true, numbers: true, reduce_motion: false, home_items: 5 },
  taste: { strength: 0.5, use_chat: true, half_life_days: 30, mute_terms: [], mute_sources: [], dim_read: true },
};

/** GET /news/home: 放送局の画面が 1 回で読み込む内容。 */
export interface HomeSection { id: string; name: string; color: string; number: number; total: number; week: number; items: NewsItem[] }
export interface NewsHome {
  channels: { id: string; name: string; color: string; number: number }[];
  onair: NewsItem | null;
  ticker: { id: number; title: string; source: string; url: string | null; published_at: string }[];
  now: NewsItem[]; today: NewsItem[]; week: NewsItem[];
  sections: HomeSection[];
  interest: NewsProfile; trends: NewsTrends;
  numbers: { today: number; read_today: number; read_30d: number; top_channel: { id: string; name: string; color: string; reads: number } | null; top_topic: string | null; top_topic_count: number };
  generated_at: string;
}
export interface ArchiveDays { days: { date: string; count: number }[]; total: number; starred: number }
export interface NewsTrends {
  days: string[]; topics: { topic: string; counts: number[]; total: number }[]; sources: { source: string; count: number }[];
}
export interface NewsProfile {
  topics: { topic: string; weight: number }[];
  sources: { source_key: string; source: string; weight: number }[];
  avoided_sources: { source_key: string; source: string; weight: number }[];
  muted: string[]; chat_terms: number;
}
export interface NewsConfig {
  enabled: Record<string, boolean>; github: string[]; codeberg: string[]; rss: { name: string; url: string }[];
  /** RSS が無いサイト: 記事の一覧のページを巡回する（robots.txt を守る・1 時間に 1 回・30 件まで） */
  pages: { name: string; url: string }[];
  hackernews: boolean; huggingface: boolean; discourse: string[]; reddit: string[];
}
/** 設定 → ニュース → ソース →「プリセット」（backend/app/news/presets.py）。 */
export interface NewsPreset {
  id: string; group: string; kind: "rss" | "github" | "codeberg" | "discourse" | "reddit"; name: string; value: string;
  lang: "ja" | "en"; note: string; key: boolean; verified: string;
}
export interface NewsPresets { groups: { id: string; label: string; hint: string; count: number }[]; items: NewsPreset[]; verified: string }
export interface SourceStatus {
  source_key: string; label: string; kind: string; ok: boolean; skipped: string; error: string; count: number; at: string;
  /** 最後に取れた時刻と、最後の失敗の理由（今は取れていても残る） */
  ok_at?: string; last_error?: string;
}
/** 通信確認・サイトの発見（backend/app/news/probe.py）。 */
export interface CheckResult {
  ok: boolean; count: number; titles: string[]; ms: number; status: number | null; latest: string;
  error: { code: string; message: string; status: number | null; location: string } | null;
}
export interface DiscoverFeed { url: string; count: number; latest: string; titles: string[] }
export interface DiscoverResult {
  url: string; title: string; mode: "feed" | "feeds" | "page" | "none"; feeds: DiscoverFeed[];
  page: { count: number; titles: string[]; robots: boolean } | null; error: { code: string; message: string } | null;
}
export interface NewsSources {
  config: NewsConfig; kinds: Record<string, string>; reddit_key: boolean; limit: number;
  sources: SourceStatus[]; last: { finished: string | null; new: number }; running: boolean;
}
/** 更新・LLM・RAG の設定（backend/app/news/settings.py）。 */
export interface NewsRuntimeSettings {
  feed_refresh_minutes: number;
  digest_daily: { enabled: boolean; time: string };
  digest_weekly: { enabled: boolean; weekday: number; time: string };
  quiet: { enabled: boolean; start: string; end: string };
  llm: { digest_model: string; gist_enabled: boolean; gist_model: string; digest_focus: string; prep_enabled: boolean; prep_model: string; prep_max_per_run: number };
  rag: { enabled: boolean; fulltext: boolean; on_prune: "keep" | "delete" };
  imagegen: { enabled: boolean; tier: ImagegenTierId; threads: number; max_per_run: number };
}
export type ImagegenTierId = "small" | "medium" | "large";
/** 画像生成のモデルの精度（小・中・大）。1 枚の目安の時間・取得の大きさ・RAM の目安と、導入済みか。 */
export interface ImagegenTier { id: ImagegenTierId; label: string; installed: boolean; megabytes: number; time_seconds: number; ram_gb: number; note: string; max_per_run: number }
/** CPU の LLM（下ごしらえ）の状態。 */
export interface CpuLlmInfo { id: string; label: string; installed: boolean; runtime_ready: boolean; megabytes: number; ram_gb: number; seconds_per_item: number }
export interface NewsSettingsResponse { settings: NewsRuntimeSettings; weekdays: string[]; interval_minutes: number; prep_calls_today?: number; imagegen_available?: boolean; imagegen_tiers?: ImagegenTier[]; cpu_llm?: CpuLlmInfo }
export interface LlmOptions {
  local: { id: string; name: string }[];
  cloud: { id: string; name: string; provider: string; free: boolean; paid: boolean }[];
  egress: boolean; caller_label: string;
}
/** 日刊・週刊のまとめ。body が空で error があるのは「作らなかった」回（外部への送信が許可されていない等）。fallback は LLM なしの見出しの一覧。 */
export interface Digest {
  id: number; period: "daily" | "weekly"; label: string; day: string; body: string; model: string; created_at: string;
  error: string; fallback: boolean; item_ids: number[];
  sources?: { id: number; n: number; title: string; source: string; url: string | null; topics?: string[] }[];
}
export interface DigestsResponse { latest: { daily: Digest | null; weekly: Digest | null }; digests: Digest[] }
export interface RefreshResult { started: boolean; reason: string; new?: number; failed?: number }

const key = ["news"] as const;

export function useNewsItems(query: NewsQuery, enabled = true, refetchMinutes = 0) {
  return useInfiniteQuery({
    queryKey: [...key, "items", query],
    enabled,
    refetchInterval: refetchMinutes > 0 ? refetchMinutes * 60_000 : false,
    initialPageParam: "",
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({ view: query.view, limit: "24" });
      if (query.kinds) p.set("kind", query.kinds);
      if (query.q.trim()) p.set("q", query.q.trim());
      if (query.date) p.set("date", query.date);
      if (query.topic) p.set("topic", query.topic);
      if (query.source) p.set("source", query.source);
      if (query.channel) p.set("channel", query.channel);
      if (pageParam) p.set("before", pageParam);
      return api<NewsPage>(`/news/items?${p}`);
    },
    getNextPageParam: (last) => last.next ?? undefined,
    staleTime: 30_000,
  });
}

export const useNewsArchive = (enabled = true) =>
  useQuery({ queryKey: [...key, "archive"], queryFn: () => api<ArchiveDays>("/news/archive"), enabled, staleTime: 60_000 });
export const useNewsTrends = () =>
  useQuery({ queryKey: [...key, "trends"], queryFn: () => api<NewsTrends>("/news/trends?days=14"), staleTime: 60_000 });
export const useNewsProfile = () =>
  useQuery({ queryKey: [...key, "profile"], queryFn: () => api<NewsProfile>("/news/profile"), staleTime: 30_000 });
export const useNewsHome = () =>
  useQuery({ queryKey: [...key, "home"], queryFn: () => api<NewsHome>("/news/home"), staleTime: 30_000 });
export const useNewsChannels = () =>
  useQuery({ queryKey: [...key, "channels"], queryFn: () => api<ChannelsResponse>("/news/channels"), staleTime: 15_000 });
export const useNewsPrefs = () =>
  useQuery({ queryKey: [...key, "prefs"], queryFn: () => api<NewsPrefs>("/news/prefs"), staleTime: 60_000 });
export const useNewsSources = () =>
  useQuery({ queryKey: [...key, "sources"], queryFn: () => api<NewsSources>("/news/sources"), staleTime: 15_000 });

export const useNewsPresets = (enabled = true) =>
  useQuery({ queryKey: [...key, "presets"], queryFn: () => api<NewsPresets>("/news/presets"), staleTime: 3_600_000, enabled });

export const useNewsSettings = () =>
  useQuery({ queryKey: [...key, "settings"], queryFn: () => api<NewsSettingsResponse>("/news/settings"), staleTime: 15_000 });
export const useLlmOptions = () =>
  useQuery({ queryKey: [...key, "llm-options"], queryFn: () => api<LlmOptions>("/news/llm-options"), staleTime: 15_000 });
export const useDigests = () =>
  useQuery({ queryKey: [...key, "digests"], queryFn: () => api<DigestsResponse>("/news/digests"), staleTime: 30_000 });
export const useDigest = (id: number | null) =>
  useQuery({ queryKey: [...key, "digest", id], queryFn: () => api<Digest>(`/news/digests/${id}`), enabled: id !== null, staleTime: 60_000 });
/** 意味で探す（RAG）。全文検索の /news/items と並べて使う。 */
export const useSemanticSearch = (q: string, enabled: boolean) =>
  useQuery({ queryKey: [...key, "semantic", q], enabled: enabled && q.trim() !== "", staleTime: 30_000,
    queryFn: () => api<{ items: NewsItem[]; semantic: boolean }>(`/news/semantic?${new URLSearchParams({ q: q.trim(), limit: "24" })}`) });

export function useSaveNewsSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (settings: NewsRuntimeSettings) => api<{ settings: NewsRuntimeSettings }>("/news/settings", { method: "PUT", json: settings }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...key, "settings"] }),
  });
}

export function useRunDigest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (period: "daily" | "weekly") => api<Digest>("/news/digests/run", { method: "POST", json: { period } }),
    onSettled: () => { qc.invalidateQueries({ queryKey: [...key, "digests"] }); qc.invalidateQueries({ queryKey: [...key, "digest"] }); },
  });
}

/** 1 件の操作を送る。返ってきた状態を、開いているどの一覧にも反映する（隠した記事を一覧から外すのは画面側）。 */
export function useNewsAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, on = true }: { id: number; action: NewsAction; on?: boolean }) =>
      api<{ state: NewsState }>(`/news/items/${id}/action`, { method: "POST", json: { action, on } }),
    onSuccess: ({ state }, { id }) => {
      qc.setQueriesData<{ pages: NewsPage[]; pageParams: unknown[] }>({ queryKey: [...key, "items"] }, (old) => old && {
        ...old,
        pages: old.pages.map((page) => ({ ...page, items: page.items.map((item) => (item.id === id ? { ...item, state } : item)) })),
      });
      qc.setQueryData<NewsHome>([...key, "home"], (old) => old && patchHome(old, id, state));
      qc.invalidateQueries({ queryKey: [...key, "profile"] });
      qc.invalidateQueries({ queryKey: [...key, "archive"] });
    },
  });
}

/** 画面の 1 件の状態だけ差し替える（区画を取り直さない）。 */
function patchHome(home: NewsHome, id: number, state: NewsState): NewsHome {
  const fix = (items: NewsItem[]) => items.map((item) => (item.id === id ? { ...item, state } : item));
  return { ...home, onair: home.onair && home.onair.id === id ? { ...home.onair, state } : home.onair, now: fix(home.now), today: fix(home.today), week: fix(home.week),
    sections: home.sections.map((section) => ({ ...section, items: fix(section.items) })) };
}

export function useRefreshNews() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<RefreshResult>("/news/refresh", { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
}

export function useMuteTopic() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ topic, muted }: { topic: string; muted: boolean }) =>
      api<NewsProfile>("/news/profile/mute", { method: "PUT", json: { topic, muted } }),
    onSuccess: (profile) => {
      // 「あなたの興味」の画面はホームの 1 回の読み込み（home.interest）から描く。profile だけ差し替えても画面は変わらない
      // （2026-11-24 利用者「× をしても消えない」の原因）ので、ホームの興味も同じ結果へ差し替える。
      qc.setQueryData([...key, "profile"], profile);
      qc.setQueryData<NewsHome>([...key, "home"], (old) => old && { ...old, interest: profile });
      qc.invalidateQueries({ queryKey: [...key, "home"] });
      qc.invalidateQueries({ queryKey: [...key, "items"] });
    },
  });
}

export function useSaveChannels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: ChannelsConfig) => api<ChannelsConfig>("/news/channels", { method: "PUT", json: config }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: [...key, "channels"] }); qc.invalidateQueries({ queryKey: [...key, "home"] }); qc.invalidateQueries({ queryKey: [...key, "items"] }); },
  });
}

/** 表示・好みの保存。保存前に画面へ反映する（見た目はすぐ変わる）。 */
export function useSavePrefs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (prefs: NewsPrefs) => api<NewsPrefs>("/news/prefs", { method: "PUT", json: prefs }),
    onMutate: (prefs) => { qc.setQueryData([...key, "prefs"], prefs); },
    onSuccess: (saved, prefs) => {
      qc.setQueryData([...key, "prefs"], saved);
      if (prefs.taste !== undefined) { qc.invalidateQueries({ queryKey: [...key, "home"] }); qc.invalidateQueries({ queryKey: [...key, "items"] }); }
    },
    onError: () => qc.invalidateQueries({ queryKey: [...key, "prefs"] }),
  });
}

export function useSaveNewsSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: NewsConfig) => api<{ config: NewsConfig }>("/news/sources", { method: "PUT", json: config }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...key, "sources"] }),
  });
}

export const checkNewsSource = (body: { kind: string; value: string; name?: string }) =>
  api<CheckResult>("/news/sources/check", { method: "POST", json: body });
export const discoverNewsSite = (url: string) => api<DiscoverResult>("/news/sources/discover", { method: "POST", json: { url } });

/** 「3 分前」「2 日前」。1 週間を過ぎたら日付。 */
export function relativeTime(iso: string, now = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.max(0, Math.round((now - then) / 60_000));
  if (minutes < 1) return "たった今";
  if (minutes < 60) return `${minutes}分前`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}時間前`;
  const days = Math.floor(minutes / 1440);
  return days < 7 ? `${days}日前` : new Date(iso).toLocaleDateString("ja-JP", { year: "numeric", month: "numeric", day: "numeric" });
}

export const KIND_STYLE: Record<NewsKind, { label: string; icon: string; band: string }> = {
  release: { label: "更新", icon: "📦", band: "from-emerald-500/80 to-teal-600/80" },
  news: { label: "ニュース", icon: "📰", band: "from-sky-500/80 to-blue-600/80" },
  model: { label: "モデル", icon: "🤖", band: "from-violet-500/80 to-purple-600/80" },
  forum: { label: "フォーラム", icon: "💬", band: "from-amber-500/80 to-orange-600/80" },
  reddit: { label: "Reddit", icon: "🗣️", band: "from-orange-500/80 to-red-500/80" },
  event: { label: "出来事", icon: "🔔", band: "from-zinc-500/80 to-slate-600/80" },
};


/** ニュースリーダー（backend/app/news/reader.py）。取り出し・要約・訳の途中の状態をそのまま返す。 */
export interface ReaderBlock { t: "h" | "p" | "ul" | "q" | "code" | "img" | "table"; src: string; level?: number; alt?: string; ja?: string | null; fail?: boolean }
export interface ReaderPage {
  item: { id: number; title: string; source: string; kind: NewsKind; published_at: string; url: string; image_url: string | null };
  status: "fetching" | "ready" | "failed"; working: boolean; phase: string; excerpt: string;
  blocks: ReaderBlock[]; summary: string; progress: { done: number; total: number }; remaining: number;
  domain: string; source_url: string; lang: string; error: string; llm_error: string; model: string;
}
export function useReader(id: number) {
  return useQuery({
    queryKey: [...key, "reader", id], queryFn: () => api<ReaderPage>(`/news/items/${id}/reader`),
    // 取り出し中・要約と訳の途中は 1.5 秒ごとに読み直し、できた所から差し替える
    refetchInterval: (q) => (q.state.data && !q.state.data.working && q.state.data.status !== "fetching" ? false : 1500),
  });
}
export function useReaderAction(id: number, path: "more" | "retry") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<ReaderPage>(`/news/items/${id}/reader/${path}`, { method: "POST" }),
    onSuccess: (page) => qc.setQueryData([...key, "reader", id], page),
  });
}
