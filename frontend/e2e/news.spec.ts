import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** ニュースの専門ページ（/news）。API は page.route で差し替える（外へ通信せず、本物の記事にも依存しない）。
 *  守ること: おすすめのカードが出る・種類のチップで絞れる・👍 で状態が変わる・アーカイブの日付から記事へ行ける・
 *  検索できる・ホームのカードから開ける・メニューに News が出る・320px でも崩れない（ライト・ダークの撮影を test-results/ に）。
 *  根拠: 2026-11-23 利用者「ニュース機能もつけて…私専門のニュースチャンネルを開設して。グラフィカルな感じで」。 */

type Item = {
  id: number; kind: string; source: string; source_key: string; title: string; url: string; summary: string;
  image_url: string | null; published_at: string; topics: string[]; state: Record<string, boolean>;
};
const noState = () => ({ open: false, like: false, dislike: false, hide: false, star: false });
const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString().replace(/\.\d+Z$/, "Z");
const ITEMS: Item[] = [
  { id: 1, kind: "release", source: "GitHub ggml-org/llama.cpp", source_key: "github:ggml-org/llama.cpp", title: "llama.cpp b9000", url: "https://example.com/release/1", summary: "Vulkan の高速化", image_url: null, published_at: at(1), topics: ["llama.cpp", "Vulkan"], state: noState() },
  { id: 2, kind: "news", source: "GIGAZINE", source_key: "rss:g", title: "Qwen の新しいモデルが公開された", url: "https://example.com/news/2", summary: "GGUF の配布も始まった", image_url: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='9'><rect width='16' height='9' fill='%23468'/></svg>", published_at: at(3), topics: ["Qwen", "GGUF"], state: noState() },
  { id: 3, kind: "model", source: "Hugging Face", source_key: "hf", title: "unsloth/Some-GGUF", url: "https://example.com/model/3", summary: "話題の GGUF", image_url: null, published_at: at(5), topics: ["GGUF"], state: noState() },
  { id: 4, kind: "forum", source: "forum.level1techs.com", source_key: "discourse:forum.level1techs.com", title: "R9700 のファン制御", url: "https://example.com/forum/4", summary: "", image_url: null, published_at: at(8), topics: ["R9700"], state: noState() },
  { id: 5, kind: "event", source: "ControlDeck", source_key: "event", title: "更新が終わりました", url: "/settings/system", summary: "v1.2 に更新", image_url: null, published_at: at(9), topics: ["更新・承認待ち"], state: noState() },
];

async function mockNews(page: Page, opts: { empty?: boolean } = {}) {
  const items = opts.empty ? [] : ITEMS.map((i) => ({ ...i, state: { ...i.state } }));
  const opened: number[] = [];
  await page.route(/\/api\/v1\/news\/items\?.*/, (route) => {
    const url = new URL(route.request().url());
    const kinds = (url.searchParams.get("kind") ?? "").split(",").filter(Boolean);
    const q = url.searchParams.get("q") ?? "";
    const date = url.searchParams.get("date") ?? "";
    let rows = items.filter((i) => (!kinds.length || kinds.includes(i.kind)) && (!q || i.title.includes(q) || i.summary.includes(q)));
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
  await page.route(/\/api\/v1\/news\/trends/, (route) => route.fulfill({ json: {
    days: Array.from({ length: 14 }, (_, i) => `2026-11-${String(10 + i).padStart(2, "0")}`),
    topics: [{ topic: "Qwen", counts: [0, 1, 0, 2, 3, 1, 0, 4, 2, 1, 5, 3, 2, 6], total: 30 }, { topic: "GGUF", counts: [1, 1, 1, 0, 0, 2, 2, 1, 3, 2, 2, 1, 4, 1], total: 21 }],
    sources: [{ source: "GIGAZINE", count: 12 }, { source: "GitHub ggml-org/llama.cpp", count: 6 }],
  } }));
  await page.route(/\/api\/v1\/news\/profile$/, (route) => route.fulfill({ json: {
    topics: [{ topic: "Qwen", weight: 4.2 }, { topic: "GGUF", weight: 2.1 }, { topic: "Vulkan", weight: 0.6 }],
    sources: [{ source_key: "rss:g", source: "GIGAZINE", weight: 2 }], avoided_sources: [], muted: [], chat_terms: 2 } }));
  await page.route(/\/api\/v1\/news\/sources$/, (route) => route.fulfill({ json: {
    config: { enabled: { release: true, news: true, model: true, forum: true, reddit: true, event: true }, github: ["ggml-org/llama.cpp"], codeberg: [],
      rss: [{ name: "GIGAZINE", url: "https://gigazine.net/news/rss_2.0/" }], hackernews: true, huggingface: true, discourse: [], reddit: ["LocalLLaMA"] },
    kinds: { release: "更新", news: "ニュース", model: "モデル", forum: "フォーラム", reddit: "Reddit", event: "出来事" }, reddit_key: false, limit: 30,
    sources: [{ source_key: "rss:bad", label: "Phoronix", kind: "news", ok: false, skipped: "", error: "HTTP 500", count: 0, at: at(0) },
      { source_key: "reddit:LocalLLaMA", label: "r/LocalLLaMA", kind: "reddit", ok: true, skipped: "Reddit のキーが未設定のため取得しません", error: "", count: 0, at: at(0) }],
    last: { finished: at(0.2), new: 3 }, running: false } }));
  return { opened };
}

for (const width of [1280, 320]) {
  test(`ニュース: おすすめ・絞り込み・👍・アーカイブ・検索（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 700 : 900 });
    const { opened } = await mockNews(page);
    await establishSession(page, context);
    await page.goto("/news");

    // おすすめ: 先頭の大きなカードと、続くカード。取れなかったソースの注記も出る
    await expect(page.locator("[data-news-card]")).toHaveCount(ITEMS.length);
    await expect(page.getByRole("link", { name: "llama.cpp b9000" })).toHaveAttribute("rel", "noopener noreferrer");
    await expect(page.getByRole("link", { name: "llama.cpp b9000" })).toHaveAttribute("target", "_blank");
    await expect(page.getByText("取れなかったソースが 1 件あります")).toBeVisible();
    await expect(page.getByRole("heading", { name: "あなたの興味" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "話題の動き" })).toBeVisible();
    // 320px でも横にあふれない
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    // 見出しを開くと open を記録する
    await page.getByRole("link", { name: "Qwen の新しいモデルが公開された" }).evaluate((a) => a.addEventListener("click", (e) => e.preventDefault()));
    await page.getByRole("link", { name: "Qwen の新しいモデルが公開された" }).click();
    await expect.poll(() => opened).toEqual([2]);

    // 種類のチップで絞る
    await page.getByRole("group", { name: "種類" }).getByRole("button", { name: "更新", exact: true }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(1);
    await page.getByRole("group", { name: "種類" }).getByRole("button", { name: "すべて" }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(ITEMS.length);

    // 👍 で状態が変わる
    const card = page.locator("[data-news-card]").filter({ hasText: "R9700 のファン制御" });
    const like = card.getByRole("button", { name: /もっと/ });
    await expect(like).toHaveAttribute("aria-pressed", "false");
    await like.click();
    await expect(like).toHaveAttribute("aria-pressed", "true");
    await card.getByRole("button", { name: /保存/ }).click();
    await expect(card.getByRole("button", { name: /保存/ })).toHaveAttribute("aria-pressed", "true");

    // 隠すと一覧から消え、元に戻せる
    await card.getByRole("button", { name: "隠す" }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(ITEMS.length - 1);

    // 検索（アーカイブ全体）
    await page.getByRole("searchbox", { name: "アーカイブ全体を検索" }).fill("Qwen");
    await expect(page.locator("[data-news-card]")).toHaveCount(1);
    await expect(page.getByText("「Qwen」の検索結果")).toBeVisible();
    await page.getByRole("searchbox", { name: "アーカイブ全体を検索" }).fill("");

    // アーカイブ: 日付の一覧 → 押すとその日の記事
    await page.getByRole("button", { name: "アーカイブ", exact: true }).click();
    await page.getByRole("button", { name: /今日|月.*日（.）/ }).first().click();
    await expect(page.locator("[data-news-card]").first()).toBeVisible();
    await page.getByRole("button", { name: "← 日付の一覧へ" }).click();
    await page.getByRole("button", { name: "☆ 保存した記事を見る" }).click();
    await expect(page.locator("[data-news-card]")).toHaveCount(1);

    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await page.evaluate((s) => { localStorage.setItem("cd-theme", s); document.documentElement.classList.toggle("dark", s === "dark"); }, scheme);
      await page.screenshot({ path: `test-results/news-${width}-${scheme}.png`, fullPage: true });
    }
  });
}

test("ニュース: 記事が無いときの案内とホームのカード・メニュー", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockNews(page);
  await establishSession(page, context);
  await page.goto("/");
  const home = page.getByRole("region", { name: "ニュース" });
  await expect(home.getByRole("link", { name: "llama.cpp b9000" })).toBeVisible();
  // メニュー（PC のサイドバー）に News が出て、押すとニュースのページへ行ける
  await page.locator("[data-shell-sidebar]").getByRole("link", { name: "News", exact: true }).click();
  await expect(page).toHaveURL(/\/news$/);
  await page.goto("/");
  await home.getByRole("link", { name: "ニュースを開く" }).click();
  await expect(page).toHaveURL(/\/news$/);
  // 設定 → 表示のメニューの表示に News が自動で並ぶ
  await page.goto("/settings/appearance");
  await expect(page.getByRole("checkbox", { name: "News" })).toBeChecked();
  await expect(page.getByRole("heading", { name: "ニュースのソース" })).toBeVisible();
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
