import type { Page } from "@playwright/test";

/** ニュースのページの E2E 共通のモック（news.spec.ts から移した）。API は page.route で差し替え、外へ通信しない。 */
export type Item = {
  id: number; kind: string; source: string; source_key: string; title: string; url: string; summary: string;
  image_url: string | null; published_at: string; topics: string[]; state: Record<string, boolean>; channel?: string | null; translated?: boolean; summary_original?: string;
};
export const noState = () => ({ open: false, like: false, dislike: false, hide: false, star: false });
export const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString().replace(/\.\d+Z$/, "Z");
/** 外部へ出ない画像: 16:9 の SVG を data URI にする。 */
export const art = (c1: string, c2: string, label: string) => "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs><rect width="640" height="360" fill="url(#g)"/><circle cx="500" cy="90" r="120" fill="rgba(255,255,255,.12)"/><text x="32" y="320" font-size="48" font-family="sans-serif" font-weight="800" fill="rgba(255,255,255,.85)">${label}</text></svg>`);

export const mk = (id: number, kind: string, source: string, title: string, summary: string, hours: number, topics: string[], image: string | null = null): Item => ({
  id, kind, source, source_key: `${kind}:${source}`, title, url: `https://example.com/n/${id}`, summary, image_url: image, published_at: at(hours), topics, state: noState(),
});
export const ITEMS: Item[] = [
  mk(1, "news", "ITmedia NEWS", "Qwen の新しいモデルが公開、GGUF 版の配布も同時に始まる", "ローカルで動かせるサイズの量子化版が最初から用意され、llama.cpp での動作確認も済んでいる。", 0.6, ["Qwen", "GGUF"], art("#f5c518", "#7c3aed", "Qwen")),
  mk(2, "release", "GitHub ggml-org/llama.cpp", "llama.cpp b9042", "ROCm の Flash Attention を高速化。Vulkan バックエンドの不具合を修正。", 1.5, ["llama.cpp", "ROCm"], art("#10b981", "#0f766e", "llama.cpp")),
  mk(3, "news", "Hacker News", "AMD Radeon AI PRO R9700 review: 32 GB for local inference", "412 点 · 187 コメント", 2, ["Radeon", "R9700"], art("#38bdf8", "#1d4ed8", "Radeon")),
  { ...mk(4, "news", "Phoronix", "ROCm 7.2 released with RDNA4 improvements", "gfx1201 と新しい HIP 機能の性能改善が入った。", 3, ["ROCm", "RDNA"], null),
    translated: true, summary_original: "Performance work for gfx1201 and new HIP features across the stack." } as Item,
  mk(5, "news", "日本経済新聞", "生成 AI を使った特許調査、知財部門の半数が導入へ", "出願前の先行技術調査で AI を使う企業が増えている。", 4, ["特許", "知財"], art("#f472b6", "#9d174d", "特許")),
  mk(6, "forum", "forum.level1techs.com", "R9700 のファン制御を PID にしたら静かになった", "3 日間の運用で 78℃ に収まった、という報告。", 5, ["R9700"], null),
  mk(7, "release", "GitHub sst/opencode", "opencode v2.14.0", "サブエージェントの MCP 呼び出しを安定化。", 6, ["OpenCode"], art("#34d399", "#047857", "OpenCode")),
  mk(8, "news", "GIGAZINE", "Claude Code の新機能で複数のエージェントを並列に動かせるように", "Anthropic が開発者向けの更新を公開した。", 7, ["Claude Code"], art("#fb923c", "#9a3412", "Claude Code")),
  mk(9, "model", "Hugging Face", "unsloth/Qwen3-Coder-GGUF", "話題の GGUF · ダウンロード 48,210 · いいね 612", 9, ["Qwen", "GGUF"], null),
  mk(10, "news", "知財図鑑", "特許庁、AI 関連発明の審査ハンドブックを改訂", "進歩性の判断例に生成 AI を使った発明が加わった。", 11, ["特許", "AI"], null),
  mk(11, "forum", "discuss.huggingface.co", "Strata で Inkling 2 Mini を動かす手順", "256k のコンテキストで 70 トークン/秒前後。", 14, ["Strata", "Inkling"], art("#a78bfa", "#5b21b6", "Strata")),
  mk(12, "reddit", "r/LocalLLaMA", "Llama.cpp の新しい量子化で 30B が 24GB に収まった", "412 点 · 96 コメント", 20, ["llama.cpp"], null),
  mk(13, "release", "GitHub Niko1221/Strata", "Strata v0.9.3", "UD-IQ4_XS の読み込みを高速化。", 26, ["Strata"], null),
  mk(14, "news", "Publickey", "OpenAI の Codex、ローカル LLM への接続に正式対応", "OpenAI 互換のエンドポイントを指定できる。", 30, ["Codex"], art("#60a5fa", "#1e3a8a", "Codex")),
  mk(15, "news", "ITmedia NEWS", "AMD、Radeon RX 9000 シリーズ向けの新ドライバーを公開", "RDNA 4 のゲーム性能が最大 8% 向上。", 40, ["Radeon", "RDNA"], null),
  mk(16, "news", "Impress Watch", "知的財産高等裁判所、AI 生成物の特許性に関する判決", "発明者の要件をめぐる議論が続く。", 52, ["特許", "知財"], null),
  mk(17, "release", "GitHub souten-yd/ControlDeck", "ControlDeck v1.2.0", "ニュースのチャンネルと放送局のデザイン。", 70, ["ControlDeck"], null),
  mk(18, "news", "GIGAZINE", "Gemma の新モデルが Apple Silicon で高速に動く", "MLX 版の公開で推論が 2 倍速に。", 100, ["Gemma"], null),
  mk(19, "forum", "forum.level1techs.com", "Threadripper と R9700 2 枚でのテンソル並列の結果", "PCIe の帯域がボトルネックになる条件。", 130, ["R9700", "ROCm"], null),
  mk(20, "model", "Hugging Face", "bartowski/GLM-4.6-GGUF", "話題の GGUF · ダウンロード 9,100 · いいね 233", 150, ["GGUF"], null),
  { ...mk(21, "event", "ControlDeck", "更新が終わりました", "v1.2 に更新", 9, ["更新・承認待ち"]), url: "/settings/system" },
];
export type Channel = { id: string; name: string; color: string; include: string[]; exclude: string[]; kinds: string[]; lang: string; limit: number; search: boolean };
export const CHANNELS: Channel[] = [
  { id: "local-llm", name: "ローカル LLM", color: "#f5c518", include: ["llama.cpp", "Strata", "Qwen", "Inkling", "GGUF"], exclude: [], kinds: [], lang: "both", limit: 6, search: true },
  { id: "amd-gpu", name: "AMD・GPU", color: "#38bdf8", include: ["ROCm", "Radeon", "RDNA", "R9700"], exclude: [], kinds: [], lang: "both", limit: 6, search: true },
  { id: "patent", name: "特許", color: "#f472b6", include: ["特許", "知財", "patent"], exclude: [], kinds: [], lang: "ja", limit: 6, search: true },
  { id: "dev-tools", name: "開発ツール", color: "#34d399", include: ["OpenCode", "Codex", "Claude Code"], exclude: [], kinds: [], lang: "both", limit: 6, search: true },
  { id: "updates", name: "使っているソフトの更新", color: "#a78bfa", include: [], exclude: [], kinds: ["release"], lang: "both", limit: 6, search: false },
];
export const inChannel = (c: Channel, i: Item) => (!c.kinds.length || c.kinds.includes(i.kind)) && (c.include.length ? c.include.some((w) => (i.title + i.summary).toLowerCase().includes(w.toLowerCase())) : c.kinds.length > 0)
  && !c.exclude.some((w) => (i.title + i.summary).includes(w));

export type Prefs = { display: Record<string, unknown>; taste: Record<string, unknown> };
export const DEFAULT_PREFS = (): Prefs => ({
  display: { design: "broadcast", accent: "", font_size: "m", density: "normal", images: true, columns: "auto", ticker: true, onair: true, numbers: true, reduce_motion: false },
  taste: { strength: 0.5, use_chat: true, half_life_days: 30, mute_terms: [], mute_sources: [], dim_read: true },
});

export function buildHome(items: Item[], channels: Channel[]) {
  const live = items.filter((i) => i.kind !== "event" && !i.state.hide);
  const numbered = channels.map((c, i) => ({ id: c.id, name: c.name, color: c.color, number: i + 1 }));
  const tag = (i: Item) => ({ ...i, channel: channels.find((c) => inChannel(c, i))?.id ?? null });
  const used = new Set<number>([live[0].id]);
  const bucket = (hours: number) => { const rows = live.filter((i) => !used.has(i.id) && Date.now() - Date.parse(i.published_at) < hours * 3_600_000).slice(0, 3); rows.forEach((r) => used.add(r.id)); return rows.map(tag); };
  const now = bucket(6), today = bucket(24), week = bucket(168);
  return {
    channels: numbered, onair: tag(live[0]),
    ticker: live.slice(0, 14).map((i) => ({ id: i.id, title: i.title, source: i.source, url: i.url, published_at: i.published_at })),
    now, today, week,
    sections: channels.map((c, idx) => { const rows = live.filter((i) => inChannel(c, i)); return { id: c.id, name: c.name, color: c.color, number: idx + 1, total: rows.length, week: rows.length, items: rows.slice(0, c.limit).map((r) => ({ ...r, channel: c.id })) }; }),
    interest: { topics: [{ topic: "Qwen", weight: 4.2 }, { topic: "GGUF", weight: 3.1 }, { topic: "R9700", weight: 2.4 }, { topic: "ROCm", weight: 1.6 }, { topic: "特許", weight: 1.1 }, { topic: "Strata", weight: 0.7 }],
      sources: [{ source_key: "rss:g", source: "GIGAZINE", weight: 2 }], avoided_sources: [], muted: [], chat_terms: 3 },
    trends: { days: Array.from({ length: 14 }, (_, i) => `2026-11-${String(10 + i).padStart(2, "0")}`),
      topics: [{ topic: "Qwen", counts: [0, 1, 0, 2, 3, 1, 0, 4, 2, 1, 5, 3, 2, 6], total: 30 }, { topic: "GGUF", counts: [1, 1, 1, 0, 0, 2, 2, 1, 3, 2, 2, 1, 4, 1], total: 21 }, { topic: "ROCm", counts: [0, 0, 1, 1, 0, 2, 1, 0, 3, 1, 0, 2, 2, 3], total: 16 }],
      sources: [{ source: "GIGAZINE", count: 12 }] },
    numbers: { today: 14, read_today: 3, read_30d: 41, top_channel: { id: channels[0].id, name: channels[0].name, color: channels[0].color, reads: 18 }, top_topic: "Qwen", top_topic_count: 30 },
    generated_at: new Date().toISOString(),
  };
}

export async function mockNews(page: Page, opts: { empty?: boolean; egress?: boolean; design?: string } = {}) {
  const items = opts.empty ? [] : ITEMS.map((i) => ({ ...i, state: { ...i.state } }));
  const channels: Channel[] = CHANNELS.map((c) => ({ ...c }));
  const prefs = DEFAULT_PREFS();
  if (opts.design) prefs.display.design = opts.design;      // 保存済みの値（旧い名前を含む）を再現する
  const opened: number[] = [];
  const saved: { channels: unknown[]; prefs: Prefs[] } = { channels: [], prefs: [] };
  await page.route(/\/api\/v1\/news\/home$/, (route) => route.fulfill({ json: opts.empty ? { ...buildHome(ITEMS, channels), onair: null, ticker: [], now: [], today: [], week: [], sections: channels.map((c, i) => ({ id: c.id, name: c.name, color: c.color, number: i + 1, total: 0, week: 0, items: [] })) } : buildHome(items, channels) }));
  await page.route(/\/api\/v1\/news\/items\?.*/, (route) => {
    const url = new URL(route.request().url());
    const kinds = (url.searchParams.get("kind") ?? "").split(",").filter(Boolean);
    const q = url.searchParams.get("q") ?? "";
    const date = url.searchParams.get("date") ?? "";
    const chId = url.searchParams.get("channel") ?? "";
    const ch = channels.find((c) => c.id === chId);
    let rows = items.filter((i) => (!kinds.length || kinds.includes(i.kind)) && (!q || i.title.includes(q) || i.summary.includes(q)) && (!ch || inChannel(ch, i)));
    if (url.searchParams.get("view") === "starred") rows = rows.filter((i) => i.state.star);
    if (date) rows = rows.filter((i) => i.published_at.startsWith(date));
    // 本番の /news/items と同じく、各記事に所属チャンネルの id を付ける（ホームのカードの色の点・札）
    return route.fulfill({ json: { items: rows.map((i) => ({ ...i, channel: channels.find((c) => inChannel(c, i))?.id ?? null })), next: null } });
  });
  await page.route(/\/api\/v1\/news\/items\/(\d+)\/action$/, (route) => {
    const id = Number(route.request().url().match(/items\/(\d+)\//)![1]);
    const body = route.request().postDataJSON() as { action: string; on: boolean };
    const item = items.find((i) => i.id === id)!;
    if (body.action === "open") opened.push(id);
    item.state = { ...item.state, [body.action]: body.on };
    return route.fulfill({ json: { state: item.state } });
  });
  const today = new Date().toISOString().slice(0, 10);
  await page.route(/\/api\/v1\/news\/archive/, (route) => route.fulfill({ json: { days: opts.empty ? [] : [{ date: today, count: items.length }, { date: "2026-10-30", count: 2 }], total: items.length, starred: 0 } }));
  await page.route(/\/api\/v1\/news\/trends/, (route) => route.fulfill({ json: buildHome(ITEMS, channels).trends }));
  await page.route(/\/api\/v1\/news\/profile$/, (route) => route.fulfill({ json: buildHome(ITEMS, channels).interest }));
  await page.route(/\/api\/v1\/news\/prefs$/, (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as Prefs;
      prefs.display = body.display; prefs.taste = body.taste; saved.prefs.push(body);
    }
    return route.fulfill({ json: prefs });
  });
  await page.route(/\/api\/v1\/news\/channels$/, (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { channels: Channel[] };
      saved.channels.push(body);
      channels.splice(0, channels.length, ...body.channels.map((c, i) => ({ ...c, id: c.id || `ch${i + 1}` })));
    }
    return route.fulfill({ json: { channels, archive_keep: 50000, interval_minutes: 30, kinds: { release: "更新", news: "ニュース", model: "モデル", forum: "フォーラム", reddit: "Reddit", event: "出来事" }, reddit_key: false,
      limits: { channels: 12, terms: 20, palette: ["#f5c518", "#38bdf8", "#f472b6", "#34d399", "#a78bfa", "#fb923c", "#f87171", "#2dd4bf", "#c084fc", "#facc15", "#60a5fa", "#4ade80"] } } });
  });
  await page.route(/\/api\/v1\/news\/sources$/, (route) => route.fulfill({ json: {
    config: { enabled: { release: true, news: true, model: true, forum: true, reddit: true, event: true }, github: ["ggml-org/llama.cpp"], codeberg: [],
      rss: [{ name: "GIGAZINE", url: "https://gigazine.net/news/rss_2.0/" }], hackernews: true, huggingface: true, discourse: [], reddit: ["LocalLLaMA"] },
    kinds: { release: "更新", news: "ニュース", model: "モデル", forum: "フォーラム", reddit: "Reddit", event: "出来事" }, reddit_key: false, limit: 30,
    sources: [{ source_key: "rss:bad", label: "Phoronix", kind: "news", ok: false, skipped: "", error: "HTTP 500", count: 0, at: at(0) },
      { source_key: "reddit:LocalLLaMA", label: "r/LocalLLaMA", kind: "reddit", ok: true, skipped: "Reddit のキーが未設定のため取得しません", error: "", count: 0, at: at(0) }],
    last: { finished: at(0.2), new: 3 }, running: false } }));
  // 2 段目: まとめ・設定（更新・LLM・RAG）・意味で探す
  const settings = {
    feed_refresh_minutes: 15, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: true, weekday: 0, time: "07:00" },
    quiet: { enabled: false, start: "01:00", end: "05:00" }, llm: { digest_model: "cloud:openrouter/meta/llama-3.3-70b:free", gist_enabled: false, gist_model: "auto", digest_focus: "", prep_enabled: true, prep_max_per_run: 100 },
    rag: { enabled: true, fulltext: false, on_prune: "keep" },
    imagegen: { enabled: false, tier: "small", threads: 8, max_per_run: 30 },
  };
  const savedSettings: unknown[] = [];
  const egress = { allowed: opts.egress ?? false };
  const digestOf = (id: number, period: string, day: string, body: string, extra: object = {}) => ({
    id, period, label: period === "daily" ? "今朝のまとめ" : "今週のまとめ", day, body, model: "cloud:openrouter/meta/llama-3.3-70b:free", created_at: at(1),
    error: "", fallback: false, item_ids: [1, 2], sources: [{ id: 1, n: 1, title: ITEMS[0].title, source: "ITmedia NEWS", url: ITEMS[0].url }, { id: 2, n: 2, title: ITEMS[1].title, source: "GitHub", url: ITEMS[1].url }], ...extra });
  const daily = digestOf(1, "daily", new Date().toLocaleDateString("en-CA"),   // 今日の分（ホームのカードの「今朝のまとめ」は今日の分だけ出す）
     "## ローカル LLM\n- 傾向: Qwen の新モデルが公開され、GGUF 版も同時に配布された。[1]\n- 主な技術・製品: Qwen、GGUF、llama.cpp。[1][2]\n- 注目点: ROCm の Flash Attention が高速化した。[2]\n## 今日の 3 行\n- Qwen の新モデルが出た。[1]\n- llama.cpp が速くなった。[2]\n- ローカル LLM の更新が続く。[2]");
  const weekly = digestOf(2, "weekly", "2026-11-23", "## ローカル LLM\n- 見出しだけの一覧です。 [1]", { fallback: true, error: "接続できません", model: "auto" });
  await page.route(/\/api\/v1\/news\/digests$/, (route) => route.fulfill({ json: { latest: { daily, weekly }, digests: [daily, weekly, digestOf(3, "daily", "2026-11-22", "", { error: "外部への送信が許可されていません" })] } }));
  await page.route(/\/api\/v1\/news\/digests\/(\d+)$/, (route) => route.fulfill({ json: route.request().url().endsWith("/2") ? weekly : route.request().url().endsWith("/1") ? daily : digestOf(3, "daily", "2026-11-22", "", { error: "外部への送信が許可されていません" }) }));
  await page.route(/\/api\/v1\/news\/settings$/, (route) => {
    if (route.request().method() === "PUT") { Object.assign(settings, route.request().postDataJSON()); savedSettings.push(route.request().postDataJSON()); }
    return route.fulfill({ json: { settings, weekdays: ["月", "火", "水", "木", "金", "土", "日"], interval_minutes: 30 } });
  });
  await page.route(/\/api\/v1\/news\/llm-options$/, (route) => route.fulfill({ json: {
    local: [{ id: "auto", name: "auto（Models で選んだ既定）" }, { id: "qwen-local", name: "Qwen ローカル" }],
    cloud: [{ id: "cloud:openrouter/meta/llama-3.3-70b:free", name: "OpenRouter / meta/llama-3.3-70b:free", provider: "openrouter", free: true, paid: false },
      { id: "cloud:openrouter/anthropic/claude-x", name: "OpenRouter / anthropic/claude-x", provider: "openrouter", free: false, paid: true }],
    egress: egress.allowed, caller_label: "ニュース（公開情報だけ）" } }));
  await page.route(/\/api\/v1\/news\/semantic\?.*/, (route) => route.fulfill({ json: { semantic: true, items: [
    { ...ITEMS[0], snippet: "Qwen の量子化版が公開された" },
    { ...mk(0, "news", "Phoronix", "古い記事: Qwen 2 の登場", "", 900, ["Qwen"]), id: 0, archived: true, snippet: "RAG にだけ残る断片" }] } }));
  return { opened, saved, channels, savedSettings };
}
