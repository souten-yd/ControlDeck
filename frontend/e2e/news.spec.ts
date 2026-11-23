import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** ニュースの専門ページ（/news）: 放送局のデザイン・チャンネル・設定。API は page.route で差し替える（外へ通信せず、本物の記事にも依存しない）。
 *  画像はテストの中で作る SVG の data URI（外部の画像の URL は使わない）。
 *  守ること: ON AIR・チャンネルの切り替え・テロップ・NOW/TODAY/THIS WEEK・チャンネルの区画・数字が出る／チャンネルを押すと ON AIR が変わる／
 *  設定でデザインを変えると見た目が変わる／チャンネルを足せる／携帯（390・320px）で 1 列・横にあふれない／
 *  新着・アーカイブ・検索・👍・隠すは 1 段目のまま使える。撮影は test-results/ に残す。
 *  根拠: 2026-11-23 利用者「OZY のようなページのデザインを参考に。設定で検索テーマや表示のデザインなど、ニュースを便利に使うための設定を詳細にできるように」。 */

type Item = {
  id: number; kind: string; source: string; source_key: string; title: string; url: string; summary: string;
  image_url: string | null; published_at: string; topics: string[]; state: Record<string, boolean>; channel?: string | null;
};
const noState = () => ({ open: false, like: false, dislike: false, hide: false, star: false });
const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString().replace(/\.\d+Z$/, "Z");
/** 外部へ出ない画像: 16:9 の SVG を data URI にする。 */
const art = (c1: string, c2: string, label: string) => "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs><rect width="640" height="360" fill="url(#g)"/><circle cx="500" cy="90" r="120" fill="rgba(255,255,255,.12)"/><text x="32" y="320" font-size="48" font-family="sans-serif" font-weight="800" fill="rgba(255,255,255,.85)">${label}</text></svg>`);

const mk = (id: number, kind: string, source: string, title: string, summary: string, hours: number, topics: string[], image: string | null = null): Item => ({
  id, kind, source, source_key: `${kind}:${source}`, title, url: `https://example.com/n/${id}`, summary, image_url: image, published_at: at(hours), topics, state: noState(),
});
const ITEMS: Item[] = [
  mk(1, "news", "ITmedia NEWS", "Qwen の新しいモデルが公開、GGUF 版の配布も同時に始まる", "ローカルで動かせるサイズの量子化版が最初から用意され、llama.cpp での動作確認も済んでいる。", 0.6, ["Qwen", "GGUF"], art("#f5c518", "#7c3aed", "Qwen")),
  mk(2, "release", "GitHub ggml-org/llama.cpp", "llama.cpp b9042", "ROCm の Flash Attention を高速化。Vulkan バックエンドの不具合を修正。", 1.5, ["llama.cpp", "ROCm"], art("#10b981", "#0f766e", "llama.cpp")),
  mk(3, "news", "Hacker News", "AMD Radeon AI PRO R9700 review: 32 GB for local inference", "412 点 · 187 コメント", 2, ["Radeon", "R9700"], art("#38bdf8", "#1d4ed8", "Radeon")),
  mk(4, "news", "Phoronix", "ROCm 7.2 released with RDNA4 improvements", "Performance work for gfx1201 and new HIP features.", 3, ["ROCm", "RDNA"], null),
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
type Channel = { id: string; name: string; color: string; include: string[]; exclude: string[]; kinds: string[]; lang: string; limit: number; search: boolean };
const CHANNELS: Channel[] = [
  { id: "local-llm", name: "ローカル LLM", color: "#f5c518", include: ["llama.cpp", "Strata", "Qwen", "Inkling", "GGUF"], exclude: [], kinds: [], lang: "both", limit: 6, search: true },
  { id: "amd-gpu", name: "AMD・GPU", color: "#38bdf8", include: ["ROCm", "Radeon", "RDNA", "R9700"], exclude: [], kinds: [], lang: "both", limit: 6, search: true },
  { id: "patent", name: "特許", color: "#f472b6", include: ["特許", "知財", "patent"], exclude: [], kinds: [], lang: "ja", limit: 6, search: true },
  { id: "dev-tools", name: "開発ツール", color: "#34d399", include: ["OpenCode", "Codex", "Claude Code"], exclude: [], kinds: [], lang: "both", limit: 6, search: true },
  { id: "updates", name: "使っているソフトの更新", color: "#a78bfa", include: [], exclude: [], kinds: ["release"], lang: "both", limit: 6, search: false },
];
const inChannel = (c: Channel, i: Item) => (!c.kinds.length || c.kinds.includes(i.kind)) && (c.include.length ? c.include.some((w) => (i.title + i.summary).toLowerCase().includes(w.toLowerCase())) : c.kinds.length > 0)
  && !c.exclude.some((w) => (i.title + i.summary).includes(w));

type Prefs = { display: Record<string, unknown>; taste: Record<string, unknown> };
const DEFAULT_PREFS = (): Prefs => ({
  display: { design: "broadcast", accent: "", font_size: "m", density: "normal", images: true, columns: "auto", ticker: true, onair: true, numbers: true, reduce_motion: false },
  taste: { strength: 0.5, use_chat: true, half_life_days: 30, mute_terms: [], mute_sources: [], dim_read: true },
});

function buildHome(items: Item[], channels: Channel[]) {
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

async function mockNews(page: Page, opts: { empty?: boolean; egress?: boolean; design?: string } = {}) {
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
    return route.fulfill({ json: { items: rows, next: null } });
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
    quiet: { enabled: false, start: "01:00", end: "05:00" }, llm: { digest_model: "cloud:openrouter/meta/llama-3.3-70b:free", gist_enabled: false, gist_model: "auto" },
    rag: { enabled: true, fulltext: false, on_prune: "keep" },
  };
  const savedSettings: unknown[] = [];
  const egress = { allowed: opts.egress ?? false };
  const digestOf = (id: number, period: string, day: string, body: string, extra: object = {}) => ({
    id, period, label: period === "daily" ? "今朝のまとめ" : "今週のまとめ", day, body, model: "cloud:openrouter/meta/llama-3.3-70b:free", created_at: at(1),
    error: "", fallback: false, item_ids: [1, 2], sources: [{ id: 1, n: 1, title: ITEMS[0].title, source: "ITmedia NEWS", url: ITEMS[0].url }, { id: 2, n: 2, title: ITEMS[1].title, source: "GitHub", url: ITEMS[1].url }], ...extra });
  const daily = digestOf(1, "daily", new Date().toLocaleDateString("en-CA"),   // 今日の分（ホームのカードの「今朝のまとめ」は今日の分だけ出す）
     "## ローカル LLM\n- Qwen の新モデルが公開され、GGUF 版も同時に配布された。[1]\n## AMD・GPU\n- llama.cpp が ROCm の Flash Attention を高速化した。[2]");
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

/** 画面の中で縦に動く領域（アプリの殻）の全体が写るよう、撮影の間だけ viewport を伸ばす（fullPage は殻の内側までは写さない）。 */
async function shot(page: Page, path: string) {
  const size = page.viewportSize()!;
  const height = await page.evaluate(() => {
    let best = document.documentElement.scrollHeight;
    for (const el of Array.from(document.querySelectorAll("*"))) {
      const overflow = getComputedStyle(el).overflowY;
      if ((overflow === "auto" || overflow === "scroll") && el.scrollHeight > el.clientHeight) best = Math.max(best, el.scrollHeight + (window.innerHeight - el.clientHeight));
    }
    return best;
  });
  await page.setViewportSize({ width: size.width, height: Math.min(Math.max(height, size.height), 9000) });
  // 途中の操作で殻の内側が下へ動いたままだと、先頭が写らない（2026-11-23: 携帯の撮影で ON AIR より上が欠けた）
  await page.evaluate(() => { window.scrollTo(0, 0); for (const el of Array.from(document.querySelectorAll("*"))) if (el.scrollTop) el.scrollTop = 0; });
  await page.screenshot({ path, fullPage: true });
  await page.setViewportSize(size);
}

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
const bg = (page: Page) => page.getByTestId("news-root").evaluate((el) => getComputedStyle(el).backgroundColor);

for (const [width, height] of [[1280, 900], [390, 844], [320, 700]] as const) {
  test(`ニュース（放送局）: ON AIR・テロップ・区画・数字・チャンネルの切り替え（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await mockNews(page);
    await establishSession(page, context);
    await page.goto("/news");
    const root = page.getByTestId("news-root");
    await expect(root).toHaveAttribute("data-design", "broadcast");
    // 1. ON AIR: モニターと切り替え（ALL と CH 01〜05）
    const onair = page.getByRole("region", { name: "ON AIR" });
    await expect(onair.getByTestId("onair-title")).toContainText("Qwen の新しいモデル");
    const switcher = page.getByRole("group", { name: "チャンネルの切り替え" });
    await expect(switcher.getByRole("button")).toHaveCount(6);
    await expect(switcher.getByRole("button", { name: "CH 05" })).toBeVisible();
    // 携帯ではモニターが先頭（見出しの文より上）
    if (width < 900) {
      const monitor = await onair.locator(".nw-monitor").boundingBox();
      const copy = await onair.locator(".nw-onair-copy").boundingBox();
      expect(monitor!.y).toBeLessThan(copy!.y);
    }
    // チャンネルを押すと ON AIR が変わる
    await switcher.getByRole("button", { name: "CH 02" }).click();
    await expect(onair.getByTestId("onair-title")).toContainText("llama.cpp b9042");   // CH 02 の 1 位（ROCm を含む更新）
    await expect(onair.getByTestId("onair-channel")).toContainText("CH 02 · AMD・GPU");
    await switcher.getByRole("button", { name: "CH 03" }).click();
    await expect(onair.getByTestId("onair-title")).toContainText("特許");
    await switcher.getByRole("button", { name: "ALL" }).click();
    await expect(onair.getByTestId("onair-title")).toContainText("Qwen の新しいモデル");
    // 2. テロップ（横に流れる）と、止める
    const ticker = page.getByRole("region", { name: "テロップ" });
    await expect(ticker.getByTestId("ticker-track")).toBeVisible();
    expect(await ticker.getByTestId("ticker-track").evaluate((el) => getComputedStyle(el).animationName)).toContain("nw-marquee");
    await ticker.getByRole("button", { name: "テロップを止める" }).click();
    await expect(root).toHaveAttribute("data-paused", "true");
    expect(await ticker.getByTestId("ticker-track").evaluate((el) => getComputedStyle(el).animationPlayState)).toBe("paused");
    await ticker.getByRole("button", { name: "テロップを流す" }).click();
    // 3. NOW / TODAY / THIS WEEK
    for (const id of ["now", "today", "week"]) await expect(page.getByTestId(`bucket-${id}`)).toBeVisible();
    await expect(page.getByTestId("bucket-now").getByText("NOW", { exact: true }).first()).toBeVisible();
    // 4. チャンネルごとの区画（CH 0N · 名前）
    for (const [n, name] of [[1, "ローカル LLM"], [2, "AMD・GPU"], [3, "特許"], [4, "開発ツール"], [5, "使っているソフトの更新"]] as const) {
      await expect(page.getByTestId(`section-${["local-llm", "amd-gpu", "patent", "dev-tools", "updates"][n - 1]}`).locator(".nw-ch-tag")).toHaveText(`CH 0${n} · ${name}`);
    }
    const cards = page.getByTestId("section-amd-gpu").locator("[data-news-card]");
    expect(await cards.count()).toBeGreaterThanOrEqual(3);
    await expect(cards.first().getByRole("link", { name: "読む →" })).toBeVisible();
    // 5・6. 興味・話題の動き・数字
    await expect(page.getByRole("heading", { name: "あなたの興味" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "話題の動き" })).toBeVisible();
    await expect(page.getByTestId("numbers")).toContainText("14");
    await expect(page.getByTestId("numbers")).toContainText("ローカル LLM");
    // 列: PC は 3 列、携帯は 1 列
    const columns = await page.getByTestId("section-local-llm").locator(".nw-grid").evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
    expect(columns).toBe(width >= 1000 ? 3 : width >= 640 ? 2 : 1);
    expect(await noOverflow(page)).toBe(true);
    if (width !== 320) await shot(page, `test-results/news-broadcast-${width}.png`);
  });
}

test("ニュース: 設定でデザイン・表示を変えると見た目が変わり、チャンネルを足せる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  const { saved } = await mockNews(page);
  await establishSession(page, context);
  await page.goto("/settings/news");
  await expect(page.getByRole("heading", { name: "チャンネル（検索テーマ）" })).toBeVisible();
  await expect(page.locator("[data-channel-editor]")).toHaveCount(5);
  await expect(page.getByRole("heading", { name: "ニュースのソース" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "表示のデザイン" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "好み" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "アーカイブ" })).toBeVisible();
  await shot(page, "test-results/news-settings-1280.png");

  // チャンネルを足して保存する（語は Enter で足す）
  await page.getByRole("button", { name: "＋ チャンネルを追加" }).click();
  await expect(page.locator("[data-channel-editor]")).toHaveCount(6);
  await page.getByLabel("CH 6 の名前").fill("自作キーボード");
  await page.getByLabel("自作キーボード の含める語を追加").fill("自作キーボード");
  await page.getByLabel("自作キーボード の含める語を追加").press("Enter");
  await page.getByLabel("自作キーボード の外す語を追加").fill("広告");
  await page.getByLabel("自作キーボード の外す語を追加").press("Enter");
  await page.getByRole("button", { name: "チャンネルを保存" }).click();
  await expect.poll(() => saved.channels.length).toBe(1);
  const sent = (saved.channels[0] as { channels: Channel[] }).channels;
  expect(sent).toHaveLength(6);
  expect(sent[5]).toMatchObject({ name: "自作キーボード", include: ["自作キーボード"], exclude: ["広告"] });
  // 並べ替え: 先頭を下へ
  await page.getByRole("button", { name: "ローカル LLM を下へ" }).click();
  await expect(page.locator("[data-channel-editor]").first().getByLabel("CH 1 の名前")).toHaveValue("AMD・GPU");

  // デザインを経済紙に: 設定は保存され、ニュースの見た目（背景色）が変わる
  await page.getByRole("radio", { name: /経済紙/ }).click();
  await expect.poll(() => saved.prefs.length).toBeGreaterThan(0);
  expect((saved.prefs.at(-1)!.display as { design: string }).design).toBe("finance");
  await page.goto("/news");
  await expect(page.getByTestId("news-root")).toHaveAttribute("data-design", "finance");
  expect(await bg(page)).toBe("rgb(255, 241, 229)");
  await expect(page.getByTestId("news-root").locator(".nw-head").first()).toBeVisible();
  const serif = await page.getByTestId("news-root").locator(".nw-sec-title").first().evaluate((el) => getComputedStyle(el).fontFamily);
  expect(serif).toMatch(/serif|Mincho/i);
  await shot(page, "test-results/news-finance-1280.png");

  // 表示の項目: テロップ・数字を消す、画像を消す
  await page.goto("/settings/news");
  // 保存はすぐ画面に反映される（キャッシュの更新は少し遅れるので、click の後に状態を待つ）
  await page.getByRole("checkbox", { name: "テロップを出す" }).click();
  await expect(page.getByRole("checkbox", { name: "テロップを出す" })).not.toBeChecked();
  await page.getByRole("checkbox", { name: "数字の区画を出す" }).click();
  await expect(page.getByRole("checkbox", { name: "数字の区画を出す" })).not.toBeChecked();
  await expect.poll(() => (saved.prefs.at(-1)!.display as { ticker: boolean; numbers: boolean }).numbers).toBe(false);
  await page.goto("/news");
  await expect(page.getByRole("region", { name: "テロップ" })).toHaveCount(0);
  await expect(page.getByTestId("numbers")).toHaveCount(0);
});

const DESIGN_CASES = [
  { id: "broadcast", label: "放送局", bg: "rgb(8, 10, 15)" },
  { id: "magazine", label: "マガジン", bg: "rgb(255, 255, 255)" },
  { id: "newspaper", label: "新聞", bg: "rgb(255, 255, 255)" },
  { id: "finance", label: "経済紙", bg: "rgb(255, 241, 229)" },
  { id: "text", label: "テキスト一覧", bg: "rgb(246, 246, 239)" },
] as const;

test("ニュース: 5 つのデザインを順に選ぶ（並び・特徴の要素・保存・撮影）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockNews(page);
  await establishSession(page, context);
  await page.goto("/settings/news");
  // 見本の縮小図つきで 5 つ選べる
  await expect(page.getByRole("radio")).toHaveCount(5);
  await expect(page.locator("[data-design-option]")).toHaveCount(5);
  await page.getByRole("radiogroup", { name: "デザイン" }).scrollIntoViewIfNeeded();
  await shot(page, "test-results/news-theme-picker-1280.png");
  for (const d of DESIGN_CASES) {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/settings/news");
    await page.getByRole("radio", { name: new RegExp(d.label) }).click();
    await expect(page.getByRole("radio", { name: new RegExp(d.label) })).toBeChecked();
    await page.waitForTimeout(400);      // 保存が済んでから開き直す
    await page.goto("/news");
    const root = page.getByTestId("news-root");
    await expect(root).toHaveAttribute("data-design", d.id);
    expect(await bg(page)).toBe(d.bg);
    // デザインごとの特徴の要素
    if (d.id === "broadcast") await expect(page.getByRole("region", { name: "ON AIR" })).toBeVisible();
    if (d.id === "magazine") {
      await expect(page.getByTestId("mag-lead")).toBeVisible();
      const latest = page.getByTestId("mag-latest");
      await expect(latest.getByRole("heading", { name: "LATEST" })).toBeVisible();
      expect(await latest.locator("li").count()).toBeGreaterThanOrEqual(5);
      await expect(latest.locator(".nwd-thumb").first()).toBeVisible();      // 縦の一覧の小さな画像
      const [lead, list] = await Promise.all([page.getByTestId("mag-lead").boundingBox(), latest.boundingBox()]);
      expect(list!.x).toBeGreaterThan(lead!.x + lead!.width - 4);              // 左に大きな 1 本・右に一覧
    }
    if (d.id === "newspaper") {
      await expect(page.getByTestId("np-band")).toBeVisible();
      await expect(page.getByTestId("np-band")).toContainText("前日比");
      const flash = page.getByTestId("np-flash");
      await expect(flash.getByRole("heading", { name: "速報" })).toBeVisible();
      await expect(flash.locator("time").first()).toBeVisible();
      await expect(page.getByTestId("np-lead")).toBeVisible();
    }
    if (d.id === "finance") {
      await expect(page.getByTestId("fin-lead")).toBeVisible();
      const heading = await page.getByTestId("fin-lead").locator(".nw-head").first().evaluate((el) => getComputedStyle(el).fontFamily);
      expect(heading).toMatch(/Georgia|serif|Mincho/i);
    }
    if (d.id === "text") {
      const rows = page.getByTestId("text-list").locator("li");
      expect(await rows.count()).toBeGreaterThanOrEqual(8);
      await expect(rows.first().locator(".nwx-rank")).toHaveText("1.");
      await expect(page.getByTestId("text-list")).toContainText("412 点");        // 点数・コメント数
      await expect(page.getByTestId("text-list")).toContainText("(example.com)");   // 出どころのドメイン
      await expect(page.locator(".nwd-lead-media, .nwd-thumb")).toHaveCount(0);   // 画像なし
    }
    expect(await noOverflow(page)).toBe(true);
    await shot(page, `test-results/news-theme-${d.id}-1280.png`);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await noOverflow(page)).toBe(true);
    await shot(page, `test-results/news-theme-${d.id}-390.png`);
    await page.setViewportSize({ width: 320, height: 700 });
    expect(await noOverflow(page)).toBe(true);
  }
});

test("ニュース: ダークでも 5 つのデザインが読める（背景と文字が違う色）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 390, height: 844 });
  await mockNews(page);
  await establishSession(page, context);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => document.documentElement.classList.add("dark"));
  for (const d of DESIGN_CASES.slice(1)) {
    await page.goto("/settings/news");
    await page.getByRole("radio", { name: new RegExp(d.label) }).click();
    await expect(page.getByRole("radio", { name: new RegExp(d.label) })).toBeChecked();
    await page.goto("/news");
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    const root = page.getByTestId("news-root");
    await expect(root).toHaveAttribute("data-design", d.id);
    const [back, fore] = await root.evaluate((el) => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]);
    expect(back).not.toBe(d.bg);
    expect(back).not.toBe(fore);
    expect(await noOverflow(page)).toBe(true);
    await shot(page, `test-results/news-theme-${d.id}-dark-390.png`);
  }
});

test("ニュース: 保存済みの「紙面」「コンパクト」は経済紙・テキスト一覧として扱う", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [old, now, label] of [["paper", "finance", "経済紙"], ["compact", "text", "テキスト一覧"]] as const) {
    await mockNews(page, { design: old });      // 後から足した route が先に効く
    if (old === "paper") await establishSession(page, context);
    await page.goto("/news");
    await expect(page.getByTestId("news-root")).toHaveAttribute("data-design", now);
    await page.goto("/settings/news");
    await expect(page.getByRole("radio", { name: new RegExp(label) })).toBeChecked();
  }
});

test("ニュース: 動きを減らす設定でテロップが止まる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockNews(page);
  await establishSession(page, context);
  await page.goto("/settings/news");
  await page.getByRole("checkbox", { name: "動きを減らす" }).click();
  await expect(page.getByRole("checkbox", { name: "動きを減らす" })).toBeChecked();
  await page.waitForTimeout(300);
  await page.goto("/news");
  await expect(page.getByTestId("news-root")).toHaveAttribute("data-reduce", "true");
  expect(await page.getByTestId("ticker-track").evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(await page.getByTestId("ticker-track").evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
});

for (const width of [1280, 320]) {
  test(`ニュース: 新着・チャンネル・絞り込み・👍・隠す・アーカイブ・検索（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 700 : 900 });
    const { opened } = await mockNews(page);
    await establishSession(page, context);
    await page.goto("/news");
    await expect(page.getByText("取れなかったソースが 1 件あります")).toBeVisible();

    // 見出しを開くと open を記録する
    const link = page.getByTestId("section-local-llm").getByRole("link", { name: /Qwen の新しいモデル/ }).first();
    await link.evaluate((a) => a.addEventListener("click", (e) => e.preventDefault()));
    await link.click();
    await expect.poll(() => opened).toContain(1);

    // 新着: チャンネルと種類で絞る
    await page.getByRole("tab", { name: "新着" }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(ITEMS.length);
    await page.getByRole("group", { name: "チャンネル" }).getByRole("button", { name: /CH 03/ }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(3);
    await page.getByRole("group", { name: "チャンネル" }).getByRole("button", { name: "全チャンネル" }).click();
    await page.getByRole("group", { name: "種類" }).getByRole("button", { name: "更新", exact: true }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(4);
    await page.getByRole("group", { name: "種類" }).getByRole("button", { name: "すべて" }).click();

    // 👍・保存・隠す
    const card = page.locator("[data-news-card]").filter({ hasText: "R9700 のファン制御" });
    const like = card.getByRole("button", { name: /もっと/ });
    await expect(like).toHaveAttribute("aria-pressed", "false");
    await like.click();
    await expect(like).toHaveAttribute("aria-pressed", "true");
    await card.getByRole("button", { name: /保存/ }).click();
    await expect(card.getByRole("button", { name: /保存/ })).toHaveAttribute("aria-pressed", "true");
    const before = await page.locator("[data-news-card]").count();
    await card.getByRole("button", { name: "隠す" }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(before - 1);

    // 検索（アーカイブ全体）
    await page.getByRole("searchbox", { name: "アーカイブ全体を検索" }).fill("特許庁");
    await expect(page.locator("[data-news-card]")).toHaveCount(1);
    await expect(page.getByText("「特許庁」の検索結果")).toBeVisible();
    await page.getByRole("searchbox", { name: "アーカイブ全体を検索" }).fill("");

    // アーカイブ: 日付の一覧 → その日の記事 → 保存した記事
    await page.getByRole("tab", { name: "アーカイブ" }).click();
    await page.getByRole("button", { name: /月.*日（.）/ }).first().click();
    await expect(page.locator("[data-news-card]").first()).toBeVisible();
    await page.getByRole("button", { name: "← 日付の一覧へ" }).click();
    await page.getByRole("button", { name: "☆ 保存した記事を見る" }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(1);
    expect(await noOverflow(page)).toBe(true);
  });
}

const HOME_DESIGNS = ["broadcast", "magazine", "newspaper", "finance", "text"] as const;
for (const design of HOME_DESIGNS) {
  test(`ニュース: ホームのカード（${design}）: まとめ・画像のカード・見出し・見た目`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width: 1280, height: 900 });
    await mockNews(page, { design });
    await establishSession(page, context);
    await page.goto("/");
    const home = page.getByRole("region", { name: "ニュース" });
    await expect(home).toHaveAttribute("data-design", design);
    await expect(home.getByRole("link", { name: /Qwen の新しいモデル/ })).toBeVisible();
    // 先頭に今朝のまとめ（本文の最初の文。出典の [1] は消す）。押すと /news
    const digest = home.getByTestId("home-digest");
    await expect(digest).toContainText("Qwen の新モデルが公開され、GGUF 版も同時に配布された。");
    await expect(digest).not.toContainText("[1]");
    // 表示のデザインの色（根の背景）がニュースのページと同じ変数から来ている
    const bgColor = await home.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bgColor).not.toBe("rgba(0, 0, 0, 0)");
    if (design === "text") {
      // 画像なし・番号つき
      await expect(home.locator("img")).toHaveCount(0);
      await expect(home.locator(".nwx-rank").first()).toHaveText("1.");
      await expect(home.locator(".nwx-row")).toHaveCount(5);
    } else {
      await expect(home.getByTestId("home-lead").locator("img")).toBeVisible();   // おすすめの 1 位は画像つき
      await expect(home.getByTestId("home-rows").locator("li")).toHaveCount(4);
      await expect(home.getByTestId("home-rows").locator(".nwh-dot").first()).toBeVisible();
      await expect(home.getByTestId("home-lead")).toContainText(/CH 0\d/);             // チャンネルの札
    }
    expect(await noOverflow(page)).toBe(true);
    await shot(page, `test-results/news-home-card-${design}-1280.png`);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await noOverflow(page)).toBe(true);
    await shot(page, `test-results/news-home-card-${design}-390.png`);
    await page.setViewportSize({ width: 320, height: 700 });
    expect(await noOverflow(page)).toBe(true);
  });
}

test("ニュース: ホームのカード・見出しは新しいタブで open を記録・まとめが今日の分でなければ出さない・メニュー・設定の入口", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockNews(page);
  await page.route(/\/api\/v1\/news\/digests$/, (route) => route.fulfill({ json: { latest: { daily: { id: 9, period: "daily", label: "今朝のまとめ", day: "2020-01-01", body: "- 古いまとめ。", model: "m", created_at: at(1), error: "", fallback: false, item_ids: [], sources: [] }, weekly: null }, digests: [] } }));
  await establishSession(page, context);
  await page.goto("/");
  const home = page.getByRole("region", { name: "ニュース" });
  const link = home.getByRole("link", { name: /Qwen の新しいモデル/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(home.getByTestId("home-digest")).toHaveCount(0);
  await page.locator("[data-shell-sidebar]").getByRole("link", { name: "News", exact: true }).click();
  await expect(page).toHaveURL(/\/news$/);
  // ニュースのページの上から設定を開ける
  await page.getByRole("link", { name: "⚙ 設定" }).click();
  await expect(page).toHaveURL(/\/settings\/news$/);
  // 設定 → 表示からニュースのソースは移った
  await page.goto("/settings/appearance");
  await expect(page.getByRole("checkbox", { name: "News" })).toBeChecked();
  await expect(page.getByRole("heading", { name: "ニュースのソース" })).toHaveCount(0);
});

test("ニュース: 記事が無いとき「今すぐ取得する」が出る", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 320, height: 700 });
  await mockNews(page, { empty: true });
  await page.route(/\/api\/v1\/news\/refresh$/, (route) => route.fulfill({ json: { started: true, reason: "", new: 0, failed: 0 } }));
  await establishSession(page, context);
  await page.goto("/news");
  await expect(page.getByText("まだ記事がありません。")).toBeVisible();
  await expect(page.getByRole("button", { name: "今すぐ取得する" })).toBeVisible();
});

// ---- 2 段目: まとめ・更新と LLM の設定・意味で探す ----

for (const [width, height] of [[1280, 900], [320, 700]] as const) {
  test(`ニュース: 今朝のまとめ・今週のまとめのカードと本文・出典（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await mockNews(page);
    await establishSession(page, context);
    await page.goto("/news");
    const daily = page.getByTestId("digest-daily");
    const weekly = page.getByTestId("digest-weekly");
    // 放送（ON AIR）より上にある
    const top = await daily.boundingBox();
    const onair = await page.getByRole("region", { name: "ON AIR" }).boundingBox();
    expect(top!.y).toBeLessThan(onair!.y);
    await expect(daily).toContainText("今朝のまとめ");
    await expect(weekly).toContainText("今週のまとめ");
    await expect(weekly.getByTestId("digest-fallback")).toContainText("見出しの一覧（LLM なし）");   // LLM なしの印
    await daily.locator("summary").first().click();
    await expect(daily.getByTestId("digest-body")).toContainText("Qwen の新モデルが公開され");
    // 出典の [1] は記事へのリンク
    const cite = daily.getByTestId("digest-body").getByRole("link", { name: "[1]" });
    await expect(cite).toHaveAttribute("href", ITEMS[0].url);
    await expect(cite).toHaveAttribute("rel", /noopener/);
    await weekly.locator("summary").first().click();
    expect(await noOverflow(page)).toBe(true);
    await shot(page, `test-results/news-digest-${width}.png`);
    // アーカイブにまとめの一覧（作れなかった回は理由）
    await page.getByRole("tab", { name: "アーカイブ" }).click();
    await expect(page.getByRole("heading", { name: "まとめ" })).toBeVisible();
    await page.getByRole("button", { name: /2026-11-22/ }).click();
    await expect(page.getByText("外部への送信が許可されていません")).toBeVisible();
    expect(await noOverflow(page)).toBe(true);
  });
}

for (const [width, height, egress] of [[1280, 900, false], [320, 700, true]] as const) {
  test(`ニュース設定: 更新のタイミング・LLM（無料の印・許可が無い案内）・RAG（${width}px・許可${egress ? "あり" : "なし"}）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    const { savedSettings } = await mockNews(page, { egress });
    await establishSession(page, context);
    await page.goto("/settings/news");
    await expect(page.getByRole("heading", { name: "更新のタイミング" })).toBeVisible();
    await expect(page.getByLabel("日刊の時刻")).toHaveValue("06:30");
    await expect(page.getByLabel("週刊の曜日")).toHaveValue("0");
    await expect(page.getByLabel("週刊の時刻")).toHaveValue("07:00");
    await expect(page.getByRole("button", { name: "今すぐ日刊のまとめを作る" })).toBeVisible();
    // LLM: 無料が先で「無料」「有料」の印
    const select = page.getByLabel("まとめのモデル");
    const options = await select.locator("option").allTextContents();
    expect(options.findIndex((t) => t.startsWith("無料 ·"))).toBeLessThan(options.findIndex((t) => t.startsWith("有料 ·")));
    await expect(page.getByRole("heading", { name: "検索（RAG）への蓄積" })).toBeVisible();
    if (!egress) {
      await expect(page.getByTestId("egress-missing")).toContainText("外部への送信が許可されていません");
      await expect(page.getByRole("link", { name: "Models を開く" })).toBeVisible();
    } else {
      await expect(page.getByTestId("egress-missing")).toHaveCount(0);
    }
    // 有料も選べる（警告つき。2026-11-23 利用者「有料モデルをニュースに使っても良い」）
    await select.selectOption("cloud:openrouter/anthropic/claude-x");
    await expect(page.getByText("有料のモデルです")).toBeVisible();
    await select.selectOption("cloud:openrouter/meta/llama-3.3-70b:free");
    // 更新の設定を変えて保存
    await page.getByLabel("ホームのフィードの更新の間隔").fill("5");
    await page.getByLabel("日刊の時刻").fill("05:45");
    await page.getByLabel("週刊の曜日").selectOption("4");
    await page.getByRole("checkbox", { name: /取得しない時間帯を決める/ }).check();
    await page.getByLabel("古い記事を消すときの RAG").selectOption("delete");
    await page.getByRole("button", { name: "保存", exact: true }).first().click();
    await expect.poll(() => savedSettings.length).toBe(1);
    expect(savedSettings[0]).toMatchObject({ feed_refresh_minutes: 5, digest_daily: { time: "05:45" }, digest_weekly: { weekday: 4 }, quiet: { enabled: true }, rag: { on_prune: "delete" } });
    expect(await noOverflow(page)).toBe(true);
    await shot(page, `test-results/news-settings-llm-${width}.png`);
  });
}

test("ニュース: 検索を「意味で探す（RAG）」に切り替えられる（消えた古い記事は操作なしで出る）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 390, height: 844 });
  await mockNews(page);
  await establishSession(page, context);
  await page.goto("/news");
  await page.getByRole("button", { name: "意味で探す" }).click();
  await page.getByLabel("アーカイブ全体を検索").fill("Qwen");
  await expect(page.getByRole("heading", { name: "「Qwen」の意味での検索結果" })).toBeVisible();
  const cards = page.locator("[data-news-card]");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(1)).toContainText("アーカイブ（RAG にだけ残っている記事）");
  await expect(cards.nth(1).getByRole("group", { name: "操作" })).toHaveCount(0);
  await expect(cards.nth(0).getByRole("group", { name: "操作" })).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
  await shot(page, "test-results/news-semantic-390.png");
  await page.getByRole("button", { name: "全文", exact: true }).click();
  await expect(page.getByRole("heading", { name: "「Qwen」の検索結果" })).toBeVisible();
});
